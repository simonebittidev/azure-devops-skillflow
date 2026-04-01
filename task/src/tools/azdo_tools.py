"""LangGraph-compatible tools for interacting with Azure DevOps Pull Requests.

Each function is decorated with @tool so LangGraph's ReAct agent can call them.
The PRContext (credentials + PR identifiers) is injected via closure when the
tool list is built — the agent never sees or handles credentials directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import requests
from langchain_core.tools import tool

from ..models.skill import PRContext

# Marker appended to PRs created by SkillFlow to prevent pipeline loops.
# main.py checks for this marker at startup and skips execution if found.
SKILLFLOW_PR_MARKER = "<!-- generated-by-skillflow -->"


# ---------------------------------------------------------------------------
# Internal AzDO REST client
# ---------------------------------------------------------------------------

class AzDOClient:
    """Thin wrapper around the Azure DevOps REST API."""

    def __init__(self, ctx: PRContext) -> None:
        self._base = ctx.organization_url.rstrip("/")
        self._project = ctx.project
        self._repo = ctx.repository_id
        self._pr_id = ctx.pull_request_id
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._session.auth = ("", ctx.access_token)

    def _url(self, path: str, api_version: str = "7.1") -> str:
        base = f"{self._base}/{self._project}/_apis/git/repositories/{self._repo}"
        sep = "&" if "?" in path else "?"
        return f"{base}/{path}{sep}api-version={api_version}"

    def get(self, path: str, **kwargs: Any) -> dict:
        url = self._url(path)
        resp = self._session.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def post(self, path: str, body: dict, **kwargs: Any) -> dict:
        url = self._url(path)
        resp = self._session.post(url, data=json.dumps(body), **kwargs)
        resp.raise_for_status()
        return resp.json()

    def get_pr_diff(self) -> str:
        """Return a unified diff of all changes in the PR."""
        import difflib

        pr_data = self.get(f"pullRequests/{self._pr_id}")
        source_branch = pr_data.get("sourceRefName", "").replace("refs/heads/", "")
        target_branch = pr_data.get("targetRefName", "").replace("refs/heads/", "")

        all_diffs: list[str] = []
        for file_path in self.list_changed_files():
            before = self._get_file_at_branch(file_path, target_branch)
            after = self._get_file_at_branch(file_path, source_branch)
            diff = list(difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a{file_path}",
                tofile=f"b{file_path}",
            ))
            all_diffs.extend(diff)

        return "".join(all_diffs) if all_diffs else "No changes found."

    def _get_file_at_branch(self, file_path: str, branch: str) -> str:
        """Return the raw text of a file at a specific branch, or '' if not found."""
        url = self._url(
            f"items?path={file_path}&versionDescriptor.versionType=branch"
            f"&versionDescriptor.version={branch}"
        )
        resp = self._session.get(url)
        if resp.status_code == 404:
            return ""
        resp.raise_for_status()
        return resp.text

    def get_file_diff(self, file_path: str) -> str:
        """Return the unified diff for a single file in the PR."""
        import difflib

        pr_data = self.get(f"pullRequests/{self._pr_id}")
        source_branch = pr_data.get("sourceRefName", "").replace("refs/heads/", "")
        target_branch = pr_data.get("targetRefName", "").replace("refs/heads/", "")

        before = self._get_file_at_branch(file_path, target_branch)
        after = self._get_file_at_branch(file_path, source_branch)
        diff = list(difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a{file_path}",
            tofile=f"b{file_path}",
        ))
        return "".join(diff) if diff else "No changes in this file."

    def get_file_content(self, file_path: str) -> str:
        """Return the content of a file at the PR's source branch."""
        pr_data = self.get(f"pullRequests/{self._pr_id}")
        source_ref = pr_data.get("sourceRefName", "")

        params = {
            "path": file_path,
            "versionDescriptor.version": source_ref.replace("refs/heads/", ""),
            "versionDescriptor.versionType": "branch",
        }
        url = self._url(f"items?path={file_path}&versionDescriptor.versionType=branch"
                        f"&versionDescriptor.version={source_ref.replace('refs/heads/', '')}")
        resp = self._session.get(url)
        if resp.status_code == 404:
            return f"File not found: {file_path}"
        resp.raise_for_status()
        return resp.text

    def list_changed_files(self) -> list[str]:
        """Return the list of file paths changed in the PR."""
        data = self.get(f"pullRequests/{self._pr_id}/iterations")
        iterations = data.get("value", [])
        if not iterations:
            return []
        latest = iterations[-1]["id"]

        changes_data = self.get(
            f"pullRequests/{self._pr_id}/iterations/{latest}/changes"
        )
        return [
            c["item"]["path"]
            for c in changes_data.get("changeEntries", [])
            if "item" in c and "path" in c["item"]
        ]

    def get_pr_description(self) -> str:
        """Return the description of the current PR."""
        pr_data = self.get(f"pullRequests/{self._pr_id}")
        return pr_data.get("description", "") or ""

    def post_pr_comment(self, comment: str) -> dict:
        """Post a general (non-inline) comment thread on the PR."""
        body = {
            "comments": [{"parentCommentId": 0, "content": comment, "commentType": 1}],
            "status": 1,
        }
        return self.post(f"pullRequests/{self._pr_id}/threads", body)

    def post_inline_comment(
        self, file_path: str, line: int, comment: str, right_file_start_line: int | None = None
    ) -> dict:
        """Post an inline comment on a specific line of a file in the PR."""
        line_number = right_file_start_line or line
        body = {
            "comments": [{"parentCommentId": 0, "content": comment, "commentType": 1}],
            "status": 1,
            "threadContext": {
                "filePath": file_path,
                "rightFileStart": {"line": line_number, "offset": 1},
                "rightFileEnd": {"line": line_number, "offset": 1},
            },
        }
        return self.post(f"pullRequests/{self._pr_id}/threads", body)

    def create_commit(self, changes: list[dict]) -> dict:
        """Push a new commit with file changes to the PR's source branch.

        Each item in `changes` must have:
          - path: str           — file path in the repo
          - content: str        — new file content (UTF-8)
          - change_type: str    — "add" | "edit" | "delete"
        """
        pr_data = self.get(f"pullRequests/{self._pr_id}")
        source_ref = pr_data.get("sourceRefName", "")
        branch = source_ref.replace("refs/heads/", "")

        refs_data = self.get(f"refs?filter=heads/{branch}")
        old_object_id = refs_data["value"][0]["objectId"]

        push_changes = []
        for c in changes:
            requested = c.get("change_type") or c.get("changeType") or c.get("type") or "edit"
            path = c["path"] if c["path"].startswith("/") else f"/{c['path']}"

            # Auto-detect: override add/edit based on actual file existence
            if requested == "delete":
                change_type = "delete"
            else:
                exists = self._file_exists_on_branch(path, branch)
                change_type = "edit" if exists else "add"
            change_entry: dict[str, Any] = {
                "changeType": change_type,
                "item": {"path": path},
            }
            if change_type != "delete":
                change_entry["newContent"] = {
                    "content": c["content"],
                    "contentType": "rawtext",
                }
            push_changes.append(change_entry)

        push_body = {
            "refUpdates": [{"name": source_ref, "oldObjectId": old_object_id}],
            "commits": [
                {
                    "comment": "LLM Skill suggestion ***NO_CI***",
                    "changes": push_changes,
                }
            ],
        }
        return self.post("pushes", push_body)

    def _file_exists_on_branch(self, path: str, branch: str) -> bool:
        """Check whether a file exists on the given branch."""
        norm = path if path.startswith("/") else f"/{path}"
        url = self._url(
            f"items?path={norm}"
            f"&versionDescriptor.versionType=branch"
            f"&versionDescriptor.version={branch}"
        )
        resp = self._session.get(url)
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return True

    def create_pr(self, title: str, description: str, changes: list[dict]) -> dict:
        """Create a new branch with the given changes and open a PR against the base branch."""
        import time

        pr_data = self.get(f"pullRequests/{self._pr_id}")
        target_ref = pr_data.get("targetRefName", "refs/heads/main")
        source_ref = pr_data.get("sourceRefName", "refs/heads/main")

        refs_data = self.get(f"refs?filter={target_ref.replace('refs/', '')}")
        old_object_id = refs_data["value"][0]["objectId"]

        new_branch = f"refs/heads/skill-suggestion-{int(time.time())}"

        # The new branch is created from the target branch, so we must check
        # file existence against the target branch to pick the right changeType.
        target_branch = target_ref.replace("refs/heads/", "")

        push_changes = []
        for c in changes:
            requested = c.get("change_type") or c.get("changeType") or c.get("type") or "edit"
            path = c["path"] if c["path"].startswith("/") else f"/{c['path']}"

            # Auto-detect: override add/edit based on actual file existence
            if requested == "delete":
                change_type = "delete"
            else:
                exists = self._file_exists_on_branch(path, target_branch)
                change_type = "edit" if exists else "add"
            change_entry: dict[str, Any] = {
                "changeType": change_type,
                "item": {"path": path},
            }
            if change_type != "delete":
                change_entry["newContent"] = {
                    "content": c["content"],
                    "contentType": "rawtext",
                }
            push_changes.append(change_entry)

        push_body = {
            "refUpdates": [{"name": new_branch, "oldObjectId": "0000000000000000000000000000000000000000"}],
            "commits": [
                {
                    "comment": f"LLM Skill suggestion: {title}",
                    "parents": [old_object_id],
                    "changes": push_changes,
                }
            ],
        }
        self.post("pushes", push_body)

        pr_body = {
            "title": title,
            "description": f"{description}\n\n{SKILLFLOW_PR_MARKER}",
            "sourceRefName": new_branch,
            "targetRefName": target_ref,
        }
        url = (
            f"{self._base}/{self._project}/_apis/git/repositories/{self._repo}"
            f"/pullrequests?api-version=7.1"
        )
        resp = self._session.post(url, data=json.dumps(pr_body))
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Tool factory — returns a list of @tool functions bound to a PRContext
# ---------------------------------------------------------------------------

def build_tools(ctx: PRContext, enabled_tools: list[str]):
    """Return LangGraph-compatible tool functions bound to the given PRContext.

    Only tools listed in `enabled_tools` are included, matching the skill's
    `tools:` frontmatter field.
    """
    client = AzDOClient(ctx)
    all_tools = []

    if "get_pr_diff" in enabled_tools:
        @tool
        def get_pr_diff() -> str:
            """Fetch the full unified diff of all changes in the current Pull Request."""
            return client.get_pr_diff()

        all_tools.append(get_pr_diff)

    if "list_changed_files" in enabled_tools:
        @tool
        def list_changed_files() -> str:
            """Return a newline-separated list of file paths changed in the current Pull Request."""
            files = client.list_changed_files()
            return "\n".join(files) if files else "No changed files found."

        all_tools.append(list_changed_files)

    if "get_file_content" in enabled_tools:
        @tool
        def get_file_content(file_path: str) -> str:
            """Return the full content of a file from the PR's source branch.

            Args:
                file_path: The path of the file relative to the repository root (e.g. src/main.py).
            """
            return client.get_file_content(file_path)

        all_tools.append(get_file_content)

    if "get_file_diff" in enabled_tools:
        @tool
        def get_file_diff(file_path: str) -> str:
            """Return the unified diff (only the changed lines) for a single file in the PR.

            Args:
                file_path: The path of the file relative to the repository root (e.g. src/main.py).
            """
            return client.get_file_diff(file_path)

        all_tools.append(get_file_diff)

    if "post_pr_comment" in enabled_tools:
        @tool
        def post_pr_comment(comment: str) -> str:
            """Post a general comment on the Pull Request (not tied to a specific file/line).

            Args:
                comment: The markdown-formatted comment text.
            """
            client.post_pr_comment(comment)
            return "Comment posted successfully."

        all_tools.append(post_pr_comment)

    if "post_inline_comment" in enabled_tools:
        @tool
        def post_inline_comment(file_path: str, line: int, comment: str) -> str:
            """Post an inline review comment on a specific line of a file in the Pull Request.

            Args:
                file_path: The path of the file (e.g. src/main.py).
                line: The 1-based line number to comment on.
                comment: The markdown-formatted comment text.
            """
            client.post_inline_comment(file_path, line, comment)
            return f"Inline comment posted on {file_path}:{line}."

        all_tools.append(post_inline_comment)

    if "create_commit" in enabled_tools:
        @tool
        def create_commit(changes_json: str) -> str:
            """Create a commit on the PR's source branch with the given file changes.

            Args:
                changes_json: A JSON array of change objects. Each object must have:
                    - path (str): file path in the repo
                    - content (str): new file content
                    - change_type (str): "add", "edit", or "delete"

            Example:
                [{"path": "src/new_file.py", "content": "print('hello')", "change_type": "add"}]
            """
            try:
                changes = json.loads(changes_json)
            except json.JSONDecodeError as e:
                return f"Error: changes_json is not valid JSON — {e}"
            client.create_commit(changes)
            return "Commit created successfully."

        all_tools.append(create_commit)

    if "create_pr" in enabled_tools:
        @tool
        def create_pr(title: str, description: str, changes_json: str) -> str:
            """Create a new branch with file changes and open a Pull Request against the base branch.

            Args:
                title: The title of the new Pull Request.
                description: The markdown description for the new Pull Request.
                changes_json: A JSON array of change objects. Each object must have:
                    - path (str): file path in the repo
                    - content (str): new file content
                    - change_type (str): "add", "edit", or "delete"

                Example:
                    [{"path": "CHANGELOG.md", "content": "# Changelog\\n...", "change_type": "edit"}]
            """
            try:
                changes = json.loads(changes_json)
            except json.JSONDecodeError as e:
                return f"Error: changes_json is not valid JSON — {e}"
            result = client.create_pr(title, description, changes)
            pr_url = result.get("url", "")
            return f"Pull Request created: {pr_url}"

        all_tools.append(create_pr)

    return all_tools
