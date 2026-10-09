from src.tools.comment_tracking import (
    OUTDATED_REPLY,
    CommentTracker,
    build_marker,
    parse_marker,
    strip_marker,
)


class FakeAzDO:
    """In-memory stand-in for AzDOClient's thread and file methods."""

    def __init__(self, files: dict[str, str]):
        self.files = dict(files)
        self.threads: list[dict] = []
        self._next_id = 1

    # files
    def get_source_text(self, path):
        return self.files.get(path)

    # threads
    def list_threads(self):
        return [dict(t, comments=[dict(c) for c in t["comments"]]) for t in self.threads]

    def _add(self, content, ctx=None):
        thread = {
            "id": self._next_id,
            "status": "active",
            "threadContext": ctx,
            "comments": [{"id": 1, "content": content, "commentType": "text",
                          "author": {"displayName": "Build Service"}}],
        }
        self._next_id += 1
        self.threads.append(thread)
        return thread

    def create_thread(self, content):
        return self._add(content)

    def create_inline_thread(self, path, line, content):
        return self._add(content, {"filePath": path, "rightFileStart": {"line": line, "offset": 1}})

    def _get(self, thread_id):
        return next(t for t in self.threads if t["id"] == thread_id)

    def update_comment(self, thread_id, comment_id, content):
        self._get(thread_id)["comments"][comment_id - 1]["content"] = content

    def reply_to_thread(self, thread_id, content):
        comments = self._get(thread_id)["comments"]
        comments.append({"id": len(comments) + 1, "content": content, "commentType": "text",
                         "author": {"displayName": "Build Service"}})

    def set_thread_status(self, thread_id, status):
        self._get(thread_id)["status"] = status

    # helpers
    def add_human_thread(self, content, path=None, line=None, status="active"):
        ctx = {"filePath": path, "rightFileStart": {"line": line}} if path else None
        thread = self._add(content, ctx)
        thread["status"] = status
        thread["comments"][0]["author"] = {"displayName": "Alice"}
        return thread


CODE = "\n".join(f"line {i}" for i in range(1, 31)) + "\n"


def run(client, skill="code-review"):
    """A fresh tracker, like a new pipeline run."""
    return CommentTracker(client, skill)


def test_marker_round_trip_is_hidden_and_parseable():
    marker = build_marker("my--skill", "inline", "abc")
    assert "--" not in marker[4:-3]  # nothing that would end the HTML comment early
    content = f"Body\n\n{marker}"
    assert parse_marker(content) == {"skill": "my--skill", "kind": "inline", "anchor": "abc"}
    assert strip_marker(content) == "Body"
    assert parse_marker("no marker here") is None


def test_rerun_on_unchanged_code_does_not_repost():
    client = FakeAzDO({"/a.py": CODE})
    assert "posted" in run(client).post_inline("a.py", 10, "**[HIGH]** Bug")

    second = run(client)
    for line in (9, 10, 11, 12):
        assert second.post_inline("/a.py", line, "**[HIGH]** Same bug, reworded").startswith("Skipped")
    assert len(client.threads) == 1

    # Far enough away, it's a different finding.
    assert "posted" in second.post_inline("/a.py", 20, "Other issue")
    assert len(client.threads) == 2


def test_dedup_follows_code_that_moved():
    client = FakeAzDO({"/a.py": CODE})
    run(client).post_inline("/a.py", 10, "Bug")

    # Five lines inserted at the top: the commented code is now at line 15.
    client.files["/a.py"] = "new\n" * 5 + CODE
    tracker = run(client)
    assert tracker.close_outdated() == []
    assert tracker.post_inline("/a.py", 15, "Bug again").startswith("Skipped")
    assert "posted" in tracker.post_inline("/a.py", 10, "Something else")
    described = {t["id"]: t for t in tracker.describe()}
    assert described[1]["line"] == 15
    assert described[1]["codeChangedSinceComment"] is False


def test_changed_code_closes_thread_and_allows_new_comment():
    client = FakeAzDO({"/a.py": CODE})
    run(client).post_inline("/a.py", 10, "Bug")

    client.files["/a.py"] = CODE.replace("line 10\n", "line 10 fixed\n")
    tracker = run(client)
    assert tracker.close_outdated() == [1]
    assert client.threads[0]["status"] == "closed"
    assert client.threads[0]["comments"][-1]["content"] == OUTDATED_REPLY

    # The issue may still be there on the new code: reporting it is allowed.
    assert "posted" in tracker.post_inline("/a.py", 10, "Bug still there")
    # And it isn't closed again on the next run.
    assert run(client).close_outdated() == []


def test_deleted_file_closes_thread():
    client = FakeAzDO({"/a.py": CODE})
    run(client).post_inline("/a.py", 10, "Bug")
    del client.files["/a.py"]
    assert run(client).close_outdated() == [1]


def test_dismissed_thread_on_unchanged_code_is_not_reposted():
    client = FakeAzDO({"/a.py": CODE})
    run(client).post_inline("/a.py", 10, "Bug")
    client.threads[0]["status"] = "wontFix"

    tracker = run(client)
    assert tracker.close_outdated() == []
    assert client.threads[0]["status"] == "wontFix"
    assert tracker.post_inline("/a.py", 10, "Bug").startswith("Skipped")


def test_skills_and_humans_are_isolated():
    client = FakeAzDO({"/a.py": CODE})
    client.add_human_thread("Human remark", "/a.py", 10)
    run(client, "security").post_inline("/a.py", 10, "Security issue")

    review = run(client, "code-review")
    assert "posted" in review.post_inline("/a.py", 10, "Review issue")

    # code-review never touches threads it doesn't own.
    client.files["/a.py"] = "rewritten\n"
    assert run(client, "code-review").close_outdated() == [3]
    assert [t["status"] for t in client.threads] == ["active", "active", "closed"]
    assert "not posted by this skill" in run(client, "code-review").resolve(1, "done")
    assert "not posted by this skill" in run(client, "code-review").resolve(2, "done")


def test_summary_is_updated_in_place():
    client = FakeAzDO({})
    assert "posted" in run(client).post_summary("Summary v1")
    assert "updated" in run(client).post_summary("Summary v2")
    tracker = run(client)
    tracker.post_summary("Summary v3")
    tracker.post_summary("Summary v3, final")
    assert len(client.threads) == 1
    assert strip_marker(client.threads[0]["comments"][0]["content"]) == "Summary v3, final"

    # Another skill keeps its own summary.
    run(client, "security").post_summary("Security summary")
    assert len(client.threads) == 2


def test_resolve_own_thread():
    client = FakeAzDO({"/a.py": CODE})
    run(client).post_inline("/a.py", 10, "Bug")
    tracker = run(client)
    assert "resolved" in tracker.resolve(1, "null check added")
    assert client.threads[0]["status"] == "fixed"
    assert "already fixed" in tracker.resolve(1, "again")


def test_invalid_locations_are_rejected():
    client = FakeAzDO({"/a.py": CODE})
    tracker = run(client)
    assert tracker.post_inline("/missing.py", 1, "x").startswith("Error")
    assert tracker.post_inline("/a.py", 0, "x").startswith("Error")
    assert tracker.post_inline("/a.py", 31, "x").startswith("Error")
    assert client.threads == []


def test_describe_hides_markers_system_and_deleted_threads():
    client = FakeAzDO({"/a.py": CODE})
    run(client).post_inline("/a.py", 10, "Bug")
    client.reply_to_thread(1, "Won't fix: intended")
    client.threads.append({"id": 50, "status": "active", "comments": [
        {"id": 1, "content": "Alice voted 10", "commentType": "system"}]})
    client.threads.append({"id": 51, "isDeleted": True, "comments": [
        {"id": 1, "content": "gone", "commentType": "text"}]})

    described = run(client).describe()
    assert [t["id"] for t in described] == [1]
    thread = described[0]
    assert thread["isFromThisSkill"] is True
    assert [c["content"] for c in thread["comments"]] == ["Bug", "Won't fix: intended"]


def test_api_paths_without_leading_slash_and_numeric_system_comments():
    client = FakeAzDO({"/a.py": CODE})
    run(client).post_inline("/a.py", 10, "Bug")
    client.threads[0]["threadContext"]["filePath"] = "a.py"
    client.threads.append({"id": 60, "status": 1, "comments": [
        {"id": 1, "content": "Policy update", "commentType": 3}]})

    tracker = run(client)
    assert tracker.post_inline("a.py", 10, "Bug").startswith("Skipped")
    assert [t["id"] for t in tracker.describe()] == [1]
