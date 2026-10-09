"""LangGraph-compatible tools for interacting with Azure DevOps Pull Requests.

Each function is decorated with @tool so LangGraph's ReAct agent can call them.
The PRContext (credentials + PR identifiers) is injected via closure when the
tool list is built — the agent never sees or handles credentials directly.
"""

from __future__ import annotations

import difflib
import json
import re
import time
import uuid
from typing import Any

import requests
from langchain_core.tools import tool

from ..models.skill import PRContext
from .comment_tracking import THREAD_STATUS_CODES, CommentTracker, normalize_path

# Marker appended to PRs created by SkillFlow to prevent pipeline loops.
# main.py checks for this marker at startup and skips execution if found.
SKILLFLOW_PR_MARKER = "<!-- generated-by-skillflow -->"

# Azure DevOps rejects PR descriptions longer than this.
MAX_PR_DESCRIPTION_CHARS = 4000

# get_pr_diff stops adding files past this size so the LLM context isn't exceeded.
MAX_PR_DIFF_CHARS = 150_000

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def numbered_unified_diff(before: str, after: str, old_path: str, new_path: str) -> str:
    """Unified diff where each line is prefixed with its line number in the new file.

    Removed lines get no number: they don't exist in the new file. Returns '' when
    the two versions are identical.
    """
    diff = list(difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a{old_path}",
        tofile=f"b{new_path}",
        lineterm="",
    ))
    if not diff:
        return ""

    out = diff[:2]  # ---/+++ headers
    new_line = 0
    for line in diff[2:]:
        hunk = _HUNK_RE.match(line)
        if hunk:
            new_line = int(hunk.group(1))
            out.append(line)
        elif line.startswith("-"):
            out.append(f"{'':>6} {line}")
        else:
            out.append(f"{new_line:>6} {line}")
            new_line += 1
    return "\n".join(out) + "\n"


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
        self._reset_cache()

    def _reset_cache(self) -> None:
        self._pr_data: dict | None = None
        self._iteration: dict | None = None
        self._changes: list[dict] | None = None
        self._texts: dict[tuple[str, str], str | None] = {}

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

    def patch(self, path: str, body: dict, **kwargs: Any) -> dict:
        url = self._url(path)
        resp = self._session.patch(url, data=json.dumps(body), **kwargs)
        resp.raise_for_status()
        return resp.json()

    # -- PR state -------------------------------------------------------------

    def get_pr_data(self) -> dict:
        if self._pr_data is None:
            self._pr_data = self.get(f"pullRequests/{self._pr_id}")
        return self._pr_data

    def get_latest_iteration(self) -> dict | None:
        """Return the PR's latest iteration (one per push), or None if it has none."""
        if self._iteration is None:
            data = self.get(f"pullRequests/{self._pr_id}/iterations")
            iterations = data.get("value", [])
            self._iteration = max(iterations, key=lambda it: it.get("id", 0)) if iterations else {}
        return self._iteration or None

    def _iteration_commit(self, *keys: str) -> str | None:
        iteration = self.get_latest_iteration() or {}
        for key in keys:
            commit = (iteration.get(key) or {}).get("commitId")
            if commit:
                return commit
        return None

    def _source_version(self) -> tuple[str, str]:
        """(version, versionType) of the code under review: the latest pushed commit."""
        commit = self._iteration_commit("sourceRefCommit")
        if commit:
            return commit, "commit"
        return self.get_pr_data().get("sourceRefName", "").replace("refs/heads/", ""), "branch"

    def _base_version(self) -> tuple[str, str]:
        """(version, versionType) the PR is compared against: the merge base.

        Diffing against the target branch tip would show commits merged into the
        target after the PR branched off as if this PR reverted them.
        """
        commit = self._iteration_commit("commonRefCommit", "targetRefCommit")
        if commit:
            return commit, "commit"
        return self.get_pr_data().get("targetRefName", "").replace("refs/heads/", ""), "branch"

    def get_changes(self) -> list[dict]:
        """Return the files changed in the PR (latest iteration vs. its base)."""
        if self._changes is not None:
            return self._changes

        iteration = self.get_latest_iteration()
        entries: list[dict] = []
        if iteration:
            skip = 0
            while True:
                data = self.get(
                    f"pullRequests/{self._pr_id}/iterations/{iteration['id']}/changes",
                    params={"$top": 2000, "$skip": skip},
                )
                batch = data.get("changeEntries", [])
                entries.extend(batch)
                next_skip = data.get("nextSkip") or 0
                if not batch or next_skip <= skip:
                    break
                skip = next_skip

        self._changes = []
        for entry in entries:
            item = entry.get("item") or {}
            if not item.get("path") or item.get("isFolder") or item.get("gitObjectType") in ("tree", "commit"):
                continue
            self._changes.append({
                "path": item["path"],
                "originalPath": entry.get("originalPath") or item["path"],
                "changeType": str(entry.get("changeType", "")),
                "changeTrackingId": entry.get("changeTrackingId"),
            })
        return self._changes

    def _find_change(self, file_path: str) -> dict | None:
        path = normalize_path(file_path)
        return next((c for c in self.get_changes() if c["path"] == path), None)

    # -- File contents --------------------------------------------------------

    def _get_text(self, file_path: str, version: str, version_type: str) -> str | None:
        """Return a file's text at the given version, or None if it doesn't exist there."""
        file_path = normalize_path(file_path)
        key = (file_path, f"{version_type}:{version}")
        if key not in self._texts:
            resp = self._session.get(self._url("items"), params={
                "path": file_path,
                "versionDescriptor.versionType": version_type,
                "versionDescriptor.version": version,
            })
            if resp.status_code == 404:
                self._texts[key] = None
            else:
                resp.raise_for_status()
                # Decode explicitly: requests assumes ISO-8859-1 for text/* without a charset.
                self._texts[key] = resp.content.decode("utf-8", errors="replace")
        return self._texts[key]

    def get_source_text(self, file_path: str) -> str | None:
        """Return a file's text in the PR's latest version, or None if it doesn't exist."""
        return self._get_text(file_path, *self._source_version())

    def get_file_content(self, file_path: str, line_numbers: bool = False) -> str:
        """Return the content of a file in the PR's latest version."""
        file_path = normalize_path(file_path)
        text = self.get_source_text(file_path)
        if text is None:
            return f"File not found: {file_path}"
        if not line_numbers:
            return text
        return "\n".join(f"{i:>6}  {line}" for i, line in enumerate(text.splitlines(), 1))

    def list_changed_files(self) -> list[str]:
        """Return the list of file paths changed in the PR."""
        return [c["path"] for c in self.get_changes()]

    # -- Diffs ----------------------------------------------------------------

    def _diff_change(self, path: str, original_path: str) -> str:
        before = self._get_text(original_path, *self._base_version()) or ""
        after = self.get_source_text(path) or ""
        if "\x00" in before or "\x00" in after:
            return f"Binary file {path} changed.\n"
        return numbered_unified_diff(before, after, original_path, path)

    def get_pr_diff(self) -> str:
        """Return a line-numbered unified diff of all changes in the PR."""
        parts: list[str] = []
        size = 0
        changes = self.get_changes()
        for i, change in enumerate(changes):
            diff = self._diff_change(change["path"], change["originalPath"])
            if parts and size + len(diff) > MAX_PR_DIFF_CHARS:
                remaining = "\n".join(c["path"] for c in changes[i:])
                parts.append(
                    "\n[Diff truncated to stay within the context window. "
                    f"Call get_file_diff for the remaining files:]\n{remaining}\n"
                )
                break
            parts.append(diff)
            size += len(diff)
        return "".join(parts) if parts else "No changes found."

    def get_file_diff(self, file_path: str) -> str:
        """Return the line-numbered unified diff for a single file in the PR."""
        path = normalize_path(file_path)
        change = self._find_change(path)
        diff = self._diff_change(path, change["originalPath"] if change else path)
        return diff or "No changes in this file."

    # -- Comment threads ------------------------------------------------------

    def get_pr_description(self) -> str:
        """Return the description of the current PR."""
        return self.get_pr_data().get("description", "") or ""

    def list_threads(self) -> list[dict]:
        """Return the raw comment threads of the PR."""
        return self.get(f"pullRequests/{self._pr_id}/threads").get("value", [])

    def create_thread(self, content: str) -> dict:
        """Create a general (non-inline) comment thread on the PR."""
        body = {
            "comments": [{"parentCommentId": 0, "content": content, "commentType": 1}],
            "status": THREAD_STATUS_CODES["active"],
        }
        return self.post(f"pullRequests/{self._pr_id}/threads", body)

    def create_inline_thread(self, file_path: str, line: int, content: str) -> dict:
        """Create a comment thread on a line of a file in the PR's latest version."""
        file_path = normalize_path(file_path)
        body: dict[str, Any] = {
            "comments": [{"parentCommentId": 0, "content": content, "commentType": 1}],
            "status": THREAD_STATUS_CODES["active"],
            "threadContext": {
                "filePath": file_path,
                "rightFileStart": {"line": line, "offset": 1},
                "rightFileEnd": {"line": line, "offset": 1},
            },
        }
        # Tie the thread to the file and iteration it was written against, so Azure
        # DevOps keeps it on the right code when later pushes move the line.
        # first == second iteration means "base of the PR vs. that iteration".
        iteration = self.get_latest_iteration()
        change = self._find_change(file_path)
        if iteration and change and change.get("changeTrackingId") is not None:
            body["pullRequestThreadContext"] = {
                "changeTrackingId": change["changeTrackingId"],
                "iterationContext": {
                    "firstComparingIteration": iteration["id"],
                    "secondComparingIteration": iteration["id"],
                },
            }
        try:
            return self.post(f"pullRequests/{self._pr_id}/threads", body)
        except requests.HTTPError as exc:
            if "pullRequestThreadContext" not in body or exc.response is None or exc.response.status_code != 400:
                raise
            del body["pullRequestThreadContext"]
            return self.post(f"pullRequests/{self._pr_id}/threads", body)

    def update_comment(self, thread_id: int, comment_id: int, content: str) -> dict:
        return self.patch(
            f"pullRequests/{self._pr_id}/threads/{thread_id}/comments/{comment_id}",
            {"content": content},
        )

    def reply_to_thread(self, thread_id: int, content: str) -> dict:
        return self.post(
            f"pullRequests/{self._pr_id}/threads/{thread_id}/comments",
            {"parentCommentId": 1, "content": content, "commentType": 1},
        )

    def set_thread_status(self, thread_id: int, status: str) -> dict:
        return self.patch(
            f"pullRequests/{self._pr_id}/threads/{thread_id}",
            {"status": THREAD_STATUS_CODES[status]},
        )

    # -- Commits and PRs ------------------------------------------------------

    def _ref_object_id(self, ref_name: str) -> str:
        """Return the commit a full ref name (e.g. refs/heads/main) points to."""
        # `filter` is a prefix match: "heads/dev" also returns "heads/dev-old".
        data = self.get("refs", params={"filter": ref_name.removeprefix("refs/")})
        for ref in data.get("value", []):
            if ref.get("name") == ref_name:
                return ref["objectId"]
        raise RuntimeError(f"Branch not found: {ref_name}")

    def create_commit(self, changes: list[dict]) -> dict:
        """Push a new commit with file changes to the PR's source branch.

        Each item in `changes` must have:
          - path: str           — file path in the repo
          - content: str        — new file content (UTF-8)
          - change_type: str    — "add" | "edit" | "delete"
        """
        source_ref = self.get_pr_data().get("sourceRefName", "")
        branch = source_ref.replace("refs/heads/", "")
        old_object_id = self._ref_object_id(source_ref)

        push_changes = []
        for c in changes:
            requested = c.get("change_type") or c.get("changeType") or c.get("type") or "edit"
            path = normalize_path(c["path"])

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
        result = self.post("pushes", push_body)
        # The push creates a new PR iteration: cached diffs and contents are stale.
        self._reset_cache()
        return result

    def _file_exists_on_branch(self, path: str, branch: str) -> bool:
        """Check whether a file exists on the given branch."""
        resp = self._session.get(self._url("items"), params={
            "path": normalize_path(path),
            "versionDescriptor.versionType": "branch",
            "versionDescriptor.version": branch,
        })
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        return True

    def create_pr(self, title: str, description: str, changes: list[dict], create_pr_target: str = "target_branch") -> dict:
        """Create a new branch with the given changes and open a PR against the base branch."""
        pr_data = self.get_pr_data()
        target_ref = pr_data.get("targetRefName", "refs/heads/main")
        source_ref = pr_data.get("sourceRefName", "refs/heads/main")

        base_ref = source_ref if create_pr_target == "source_branch" else target_ref
        old_object_id = self._ref_object_id(base_ref)

        new_branch = f"refs/heads/skill-suggestion-{int(time.time())}-{uuid.uuid4().hex[:6]}"

        # The new branch is created from base_ref, so we must check
        # file existence against that branch to pick the right changeType.
        base_branch = base_ref.replace("refs/heads/", "")

        push_changes = []
        for c in changes:
            requested = c.get("change_type") or c.get("changeType") or c.get("type") or "edit"
            path = normalize_path(c["path"])

            # Auto-detect: override add/edit based on actual file existence
            if requested == "delete":
                change_type = "delete"
            else:
                exists = self._file_exists_on_branch(path, base_branch)
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

        # Keep the loop-prevention marker even when the description is at the size limit.
        room = MAX_PR_DESCRIPTION_CHARS - len(SKILLFLOW_PR_MARKER) - 2
        if len(description) > room:
            description = description[: room - 1] + "…"
        pr_body = {
            "title": title,
            "description": f"{description}\n\n{SKILLFLOW_PR_MARKER}",
            "sourceRefName": new_branch,
            "targetRefName": base_ref,
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

def build_tools(
    client: AzDOClient,
    comments: CommentTracker,
    enabled_tools: list[str],
    create_pr_target: str = "target_branch",
):
    """Return LangGraph-compatible tool functions bound to the given client.

    Only tools listed in `enabled_tools` are included, matching the skill's
    `tools:` frontmatter field. `comments` tracks the threads of the running
    skill, so posting tools can skip duplicates of earlier runs.
    """
    all_tools = []

    if "get_pr_comments" in enabled_tools:
        @tool
        def get_pr_comments() -> str:
            """Return the existing comment threads on the Pull Request as a JSON array.

            Each thread contains:
            - id: thread id (use it with resolve_pr_comment)
            - status: active, pending, fixed, wontFix, closed or byDesign
            - filePath / line: location of an inline thread (null for general comments).
              For threads posted by this skill, line is where that code is now, and
              codeChangedSinceComment tells whether the commented code was modified.
            - postedBy / isFromThisSkill: who opened the thread
            - comments: the comment and all replies (author + content)

            Use it before reviewing:
            - Do not re-report problems already covered by a thread, whatever its status:
              a thread marked wontFix, byDesign or closed means the problem was dismissed.
              Read the replies to understand the authors' decisions.
            - If an active thread with isFromThisSkill=true describes a problem that is now
              fixed in the code, call resolve_pr_comment on it.
            """
            return json.dumps(comments.describe(), ensure_ascii=False)

        all_tools.append(get_pr_comments)

    if "resolve_pr_comment" in enabled_tools:
        @tool
        def resolve_pr_comment(thread_id: int, reason: str) -> str:
            """Mark one of this skill's own comment threads as resolved (fixed).

            Only use it after verifying in the current code that the problem described by
            the thread is fixed. Only threads with isFromThisSkill=true can be resolved.

            Args:
                thread_id: The thread id returned by get_pr_comments.
                reason: One sentence explaining how the problem was fixed.
            """
            return comments.resolve(thread_id, reason)

        all_tools.append(resolve_pr_comment)

    if "get_pr_diff" in enabled_tools:
        @tool
        def get_pr_diff() -> str:
            """Fetch the unified diff of all changes in the current Pull Request.

            Each line starts with its line number in the new version of the file; use that
            number when commenting. Removed lines ('-') have no number and can't be commented on.
            """
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
        def get_file_content(file_path: str, line_numbers: bool = False) -> str:
            """Return the full content of a file in the Pull Request's latest version.

            Args:
                file_path: The path of the file relative to the repository root (e.g. src/main.py).
                line_numbers: Prefix every line with its line number. Use it when you need to
                    reference lines in comments; never when the content will be written back
                    with create_commit or create_pr.
            """
            return client.get_file_content(file_path, line_numbers)

        all_tools.append(get_file_content)

    if "get_file_diff" in enabled_tools:
        @tool
        def get_file_diff(file_path: str) -> str:
            """Return the unified diff (only the changed lines) for a single file in the PR.

            Each line starts with its line number in the new version of the file; use that
            number when commenting. Removed lines ('-') have no number.

            Args:
                file_path: The path of the file relative to the repository root (e.g. src/main.py).
            """
            return client.get_file_diff(file_path)

        all_tools.append(get_file_diff)

    if "post_pr_comment" in enabled_tools:
        @tool
        def post_pr_comment(comment: str) -> str:
            """Post this skill's general comment on the Pull Request (not tied to a file/line).

            Each skill keeps a single general comment: if this skill already posted one, in
            this or a previous pipeline run, its content is replaced instead of adding a new
            comment. Put the whole summary in one call.

            Args:
                comment: The markdown-formatted comment text.
            """
            return comments.post_summary(comment)

        all_tools.append(post_pr_comment)

    if "post_inline_comment" in enabled_tools:
        @tool
        def post_inline_comment(file_path: str, line: int, comment: str) -> str:
            """Post an inline review comment on a specific line of a file in the Pull Request.

            Comments that duplicate one this skill already posted near the same line, on
            code that hasn't changed since, are skipped automatically.

            Args:
                file_path: The path of the file (e.g. src/main.py).
                line: The 1-based line number in the new version of the file.
                comment: The markdown-formatted comment text.
            """
            return comments.post_inline(file_path, line, comment)

        all_tools.append(post_inline_comment)

    if "post_suggestion_comment" in enabled_tools:
        @tool
        def post_suggestion_comment(file_path: str, line: int, suggested_code: str, comment: str = "") -> str:
            """Post an inline suggestion on a specific line of a file in the Pull Request.

            The suggestion renders in Azure DevOps with an 'Apply change' button that lets
            the PR author apply the change directly without leaving the browser.
            Use this instead of post_inline_comment when you want to propose a specific
            code fix that can be applied with one click. Duplicates of a comment this skill
            already posted near the same line, on unchanged code, are skipped automatically.

            Args:
                file_path: The path of the file (e.g. src/main.py).
                line: The 1-based line number in the new version of the file.
                suggested_code: The replacement code for that line (without leading/trailing newlines).
                comment: Optional explanation shown above the suggestion diff.
            """
            suggestion = f"```suggestion\n{suggested_code}\n```"
            content = f"{comment}\n\n{suggestion}" if comment else suggestion
            return comments.post_inline(file_path, line, content)

        all_tools.append(post_suggestion_comment)

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
            result = client.create_pr(title, description, changes, create_pr_target=create_pr_target)
            pr_url = result.get("url", "")
            return f"Pull Request created: {pr_url}"

        all_tools.append(create_pr)

    return all_tools
