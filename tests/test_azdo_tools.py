from pathlib import Path

import pytest

from src.models.skill import AVAILABLE_TOOLS
from src.skill_loader import load_skill
from src.tools.azdo_tools import AzDOClient, build_tools, numbered_unified_diff
from src.tools.comment_tracking import CommentTracker

EXAMPLES = sorted((Path(__file__).resolve().parent.parent / "examples").glob("*.md"))


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_example_skills_load(path):
    load_skill(path)


def test_every_available_tool_is_built():
    tools = build_tools(client=None, comments=None, enabled_tools=sorted(AVAILABLE_TOOLS))
    assert {t.name for t in tools} == AVAILABLE_TOOLS


def test_numbered_diff_uses_new_file_line_numbers():
    before = "a\nb\nc\nd\n"
    after = "a\nB\nc\nnew\nd\n"
    diff = numbered_unified_diff(before, after, "/f.py", "/f.py")
    lines = diff.splitlines()
    assert lines[:2] == ["--- a/f.py", "+++ b/f.py"]
    assert "     1  a" in lines
    assert "       -b" in lines
    assert "     2 +B" in lines
    assert "     4 +new" in lines
    assert "     5  d" in lines


def test_numbered_diff_without_trailing_newline_is_well_formed():
    diff = numbered_unified_diff("a\nb\nc", "a\nb\nX", "/f", "/f")
    assert diff.splitlines()[-2:] == ["       -c", "     3 +X"]
    assert numbered_unified_diff("same", "same", "/f", "/f") == ""


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self._payload = payload
        self.content = content

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(response=self)


class FakeSession:
    """Routes AzDO REST calls to canned responses and records POST bodies."""

    def __init__(self, files_by_version):
        self.files = files_by_version
        self.posted = []
        self.change_pages = [
            {"changeEntries": [
                {"item": {"path": "/src/a.py"}, "changeType": "edit", "changeTrackingId": 7},
                {"item": {"path": "/src", "isFolder": True}, "changeType": "edit"},
            ], "nextSkip": 2},
            {"changeEntries": [
                {"item": {"path": "/src/new name.py"}, "originalPath": "/src/old.py",
                 "changeType": "rename, edit", "changeTrackingId": 8},
            ], "nextSkip": 0},
        ]

    def get(self, url, params=None, **kwargs):
        params = params or {}
        if "/items" in url:
            text = self.files.get((params["versionDescriptor.version"], params["path"]))
            return FakeResponse(404) if text is None else FakeResponse(content=text.encode("utf-8"))
        if url.split("?")[0].endswith("/iterations"):
            return FakeResponse(payload={"value": [
                {"id": 1, "sourceRefCommit": {"commitId": "old"}},
                {"id": 2, "sourceRefCommit": {"commitId": "src2"},
                 "commonRefCommit": {"commitId": "base"}, "targetRefCommit": {"commitId": "tip"}},
            ]})
        if "/changes" in url:
            return FakeResponse(payload=self.change_pages[0 if params.get("$skip", 0) == 0 else 1])
        if "/threads" in url:
            return FakeResponse(payload={"value": []})
        return FakeResponse(payload={"sourceRefName": "refs/heads/feature",
                                     "targetRefName": "refs/heads/main"})

    def post(self, url, data=None, **kwargs):
        self.posted.append((url, data))
        return FakeResponse(payload={"id": len(self.posted), "comments": [{"id": 1}]})


def make_client(files):
    client = AzDOClient.__new__(AzDOClient)
    client._base, client._project, client._repo, client._pr_id = "https://x", "p", "r", 1
    client._session = FakeSession(files)
    client._reset_cache()
    return client


def test_changes_are_paginated_and_skip_folders():
    client = make_client({})
    assert client.list_changed_files() == ["/src/a.py", "/src/new name.py"]


def test_diff_is_against_merge_base_of_latest_iteration():
    client = make_client({
        ("base", "/src/a.py"): "x = 1\n",
        ("tip", "/src/a.py"): "x = 1\nunrelated = 'merged into main later'\n",
        ("src2", "/src/a.py"): "x = 2\n",
        ("base", "/src/old.py"): "print('hi')\n",
        ("src2", "/src/new name.py"): "print('hi')\nprint('è')\n",
    })
    diff = client.get_pr_diff()
    assert "unrelated" not in diff
    assert "     1 +x = 2" in diff
    # Renamed file is diffed against its original path; UTF-8 is decoded correctly.
    assert "--- a/src/old.py" in diff and "+++ b/src/new name.py" in diff
    assert "     2 +print('è')" in diff
    assert client.get_file_content("src/a.py", line_numbers=True) == "     1  x = 2"


def test_inline_thread_is_tied_to_latest_iteration():
    import json

    client = make_client({("src2", "/src/a.py"): "x = 2\n"})
    tracker = CommentTracker(client, "code-review")
    assert "posted" in tracker.post_inline("src/a.py", 1, "Comment")
    body = json.loads(client._session.posted[-1][1])
    assert body["threadContext"]["filePath"] == "/src/a.py"
    assert body["pullRequestThreadContext"] == {
        "changeTrackingId": 7,
        "iterationContext": {"firstComparingIteration": 2, "secondComparingIteration": 2},
    }
    assert "<!-- skillflow:" in body["comments"][0]["content"]
