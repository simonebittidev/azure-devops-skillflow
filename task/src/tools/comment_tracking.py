"""Bookkeeping for the comment threads SkillFlow posts on a Pull Request.

The pipeline runs on every PR update, so without bookkeeping each run re-posts
the same findings and old comments pile up even after the code they referred to
has changed. Every thread SkillFlow creates therefore carries a hidden marker
(an HTML comment, invisible in the rendered markdown) that records:

  - the skill that posted it, so each skill only manages its own threads;
  - the kind of thread ("inline" review comment or the skill's "summary");
  - for inline threads, an anchor: a hash of the commented line and its
    neighbours at the time the comment was posted.

On later runs the anchor is searched for in the current version of the file:
  - found     -> the code is unchanged; the thread's current line is known even
                 if lines moved, and new comments near it are duplicates;
  - not found -> the code was modified, so the thread is outdated and is
                 closed automatically (if the problem is still there, the
                 agent reports it again on the new code).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

# A new inline comment within this many lines of an existing thread of the
# same skill (whose code is unchanged) is considered a duplicate.
DUPLICATE_LINE_TOLERANCE = 2

# Lines above and below the commented line included in the anchor hash.
ANCHOR_CONTEXT_LINES = 1

# Comments longer than this are truncated in get_pr_comments output.
MAX_COMMENT_CHARS = 1500

THREAD_STATUS_CODES = {
    "active": 1,
    "fixed": 2,
    "wontFix": 3,
    "closed": 4,
    "byDesign": 5,
    "pending": 6,
}
_STATUS_NAMES = {code: name for name, code in THREAD_STATUS_CODES.items()}
OPEN_STATUSES = {"active", "pending"}

OUTDATED_REPLY = (
    "🤖 **SkillFlow**: the code this comment referred to has changed since it was posted, "
    "so this thread was closed automatically. If the problem is still present, "
    "it will be reported again on the updated code."
)

_MARKER_RE = re.compile(r"\s*<!--\s*skillflow:(\{.*?\})\s*-->\s*", re.DOTALL)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def normalize_path(path: str) -> str:
    """Ensure a file path starts with '/' as required by the Azure DevOps API."""
    return path if path.startswith("/") else f"/{path}"


def normalize_status(status: Any) -> str:
    """Return the thread status name; the REST API may return a name or a number."""
    if isinstance(status, int):
        return _STATUS_NAMES.get(status, "unknown")
    return str(status) if status else "unknown"


def build_marker(skill: str, kind: str, anchor: str | None = None) -> str:
    data: dict[str, str] = {"skill": skill, "kind": kind}
    if anchor:
        data["anchor"] = anchor
    # "--" is not allowed inside an HTML comment.
    payload = json.dumps(data, ensure_ascii=True).replace("--", "-\\u002d")
    return f"<!-- skillflow:{payload} -->"


def parse_marker(content: str) -> dict | None:
    match = _MARKER_RE.search(content or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and data.get("skill") else None


def strip_marker(content: str) -> str:
    return _MARKER_RE.sub("", content or "").strip()


def _window_hash(lines: list[str], line: int) -> str:
    start = max(0, line - 1 - ANCHOR_CONTEXT_LINES)
    end = min(len(lines), line + ANCHOR_CONTEXT_LINES)
    window = "\n".join(text.strip() for text in lines[start:end])
    return hashlib.sha1(window.encode("utf-8")).hexdigest()[:16]


def compute_anchor(lines: list[str], line: int) -> str:
    """Hash of `line` (1-based) and its neighbours, ignoring indentation."""
    return _window_hash(lines, line)


def build_anchor_index(lines: list[str]) -> dict[str, list[int]]:
    """Map every anchor hash in the file to the (1-based) lines producing it."""
    index: dict[str, list[int]] = {}
    for line in range(1, len(lines) + 1):
        index.setdefault(_window_hash(lines, line), []).append(line)
    return index


def locate_anchor(index: dict[str, list[int]], anchor: str, hint: int) -> int | None:
    """Return the line matching `anchor` closest to `hint`, or None if the code changed."""
    candidates = index.get(anchor)
    if not candidates:
        return None
    return min(candidates, key=lambda line: abs(line - hint))


# ---------------------------------------------------------------------------
# Thread model
# ---------------------------------------------------------------------------

@dataclass
class TrackedThread:
    id: int
    status: str
    file_path: str | None
    line: int | None
    first_comment_id: int
    comments: list[dict] = field(default_factory=list)
    marker: dict | None = None

    @property
    def skill(self) -> str | None:
        return self.marker.get("skill") if self.marker else None

    @property
    def kind(self) -> str | None:
        return self.marker.get("kind") if self.marker else None

    @classmethod
    def from_api(cls, thread: dict) -> "TrackedThread | None":
        """Build from a REST thread; None for deleted, empty or system threads."""
        if thread.get("isDeleted"):
            return None
        comments = [c for c in thread.get("comments", []) if not c.get("isDeleted")]
        if not comments or _is_system(comments[0]):
            return None

        ctx = thread.get("threadContext") or {}
        start = ctx.get("rightFileStart") or ctx.get("leftFileStart") or {}
        first = comments[0]
        return cls(
            id=thread["id"],
            status=normalize_status(thread.get("status")),
            file_path=normalize_path(ctx["filePath"]) if ctx.get("filePath") else None,
            line=start.get("line"),
            first_comment_id=first.get("id", 1),
            comments=[
                {
                    "author": (c.get("author") or {}).get("displayName", ""),
                    "content": c.get("content", "") or "",
                }
                for c in comments
                if not _is_system(c)
            ],
            marker=parse_marker(first.get("content", "")),
        )


# ---------------------------------------------------------------------------
# Per-skill tracker
# ---------------------------------------------------------------------------

class CommentTracker:
    """Posts, deduplicates and cleans up the comment threads of one skill.

    `client` is an AzDOClient (or a test double) providing list_threads,
    get_source_text, create_thread, create_inline_thread, update_comment,
    reply_to_thread and set_thread_status.
    """

    def __init__(self, client: Any, skill: str) -> None:
        self._client = client
        self._skill = skill
        self._threads: list[TrackedThread] | None = None
        self._indexes: dict[str, tuple[str, dict[str, list[int]]]] = {}

    # -- reading ------------------------------------------------------------

    def threads(self) -> list[TrackedThread]:
        if self._threads is None:
            parsed = (TrackedThread.from_api(t) for t in self._client.list_threads())
            self._threads = [t for t in parsed if t is not None]
        return self._threads

    def _own(self, kind: str | None = None) -> list[TrackedThread]:
        return [
            t for t in self.threads()
            if t.skill == self._skill and (kind is None or t.kind == kind)
        ]

    def _anchor_index(self, path: str) -> dict[str, list[int]] | None:
        text = self._client.get_source_text(path)
        if text is None:
            return None
        cached = self._indexes.get(path)
        if cached is None or cached[0] is not text:
            cached = (text, build_anchor_index(text.splitlines()))
            self._indexes[path] = cached
        return cached[1]

    def current_line(self, thread: TrackedThread) -> int | None:
        """Line where an own inline thread's code is now, or None if that code changed."""
        anchor = thread.marker.get("anchor") if thread.marker else None
        if not anchor or not thread.file_path:
            return thread.line
        index = self._anchor_index(thread.file_path)
        if index is None:
            return None
        return locate_anchor(index, anchor, thread.line or 1)

    def describe(self) -> list[dict]:
        """Simplified view of all PR threads for the agent."""
        result = []
        for t in self.threads():
            own = t.skill == self._skill
            entry: dict[str, Any] = {
                "id": t.id,
                "status": t.status,
                "filePath": t.file_path,
                "line": t.line,
                "postedBy": f"SkillFlow skill '{t.skill}'" if t.skill else
                            (t.comments[0]["author"] if t.comments else ""),
                "isFromThisSkill": own,
            }
            if own and t.kind == "inline":
                current = self.current_line(t)
                entry["line"] = current
                entry["codeChangedSinceComment"] = current is None
            entry["comments"] = [
                {"author": c["author"], "content": _truncate(strip_marker(c["content"]))}
                for c in t.comments
            ]
            result.append(entry)
        return result

    # -- cleanup ------------------------------------------------------------

    def close_outdated(self) -> list[int]:
        """Close this skill's open inline threads whose code has changed since posting."""
        closed = []
        for t in self._own("inline"):
            if t.status not in OPEN_STATUSES or self.current_line(t) is not None:
                continue
            self._client.reply_to_thread(t.id, OUTDATED_REPLY)
            self._client.set_thread_status(t.id, "closed")
            t.status = "closed"
            closed.append(t.id)
        return closed

    def resolve(self, thread_id: int, reason: str) -> str:
        thread = next((t for t in self._own() if t.id == thread_id), None)
        if thread is None:
            return (
                f"Error: thread #{thread_id} was not posted by this skill. "
                "Only threads with isFromThisSkill=true can be resolved."
            )
        if thread.status not in OPEN_STATUSES:
            return f"Thread #{thread_id} is already {thread.status}; nothing to do."
        self._client.reply_to_thread(thread_id, f"✅ **SkillFlow**: resolved — {reason}")
        self._client.set_thread_status(thread_id, "fixed")
        thread.status = "fixed"
        return f"Thread #{thread_id} marked as resolved."

    # -- posting ------------------------------------------------------------

    def find_duplicate(self, file_path: str, line: int) -> tuple[TrackedThread, int] | None:
        """An own inline thread (any status) on unchanged code near `line`, if any."""
        for t in self._own("inline"):
            if t.file_path != file_path:
                continue
            current = self.current_line(t)
            if current is not None and abs(current - line) <= DUPLICATE_LINE_TOLERANCE:
                return t, current
        return None

    def post_inline(self, file_path: str, line: int, content: str) -> str:
        path = normalize_path(file_path)
        text = self._client.get_source_text(path)
        if text is None:
            return (
                f"Error: {path} does not exist in the PR's latest version, so it cannot be "
                "commented on. Use a path from list_changed_files."
            )
        lines = text.splitlines()
        if not 1 <= line <= len(lines):
            return (
                f"Error: line {line} is out of range for {path} ({len(lines)} lines). "
                "Use the new-file line numbers shown by get_pr_diff / get_file_diff."
            )

        duplicate = self.find_duplicate(path, line)
        if duplicate:
            thread, current = duplicate
            first = strip_marker(thread.comments[0]["content"]) if thread.comments else ""
            title = (first.splitlines() or [""])[0]
            return (
                f"Skipped: this skill already has a comment thread (#{thread.id}, status "
                f"'{thread.status}') on {path}:{current} and that code has not changed: "
                f"\"{_truncate(title, 200)}\". Not posted again to avoid duplicates."
            )

        anchor = compute_anchor(lines, line)
        body = f"{content}\n\n{build_marker(self._skill, 'inline', anchor)}"
        result = self._client.create_inline_thread(path, line, body)
        self.threads().append(TrackedThread(
            id=result.get("id", 0),
            status="active",
            file_path=path,
            line=line,
            first_comment_id=((result.get("comments") or [{}])[0]).get("id", 1),
            comments=[{"author": "SkillFlow", "content": body}],
            marker=parse_marker(body),
        ))
        return f"Comment posted on {path}:{line} (thread #{result.get('id')})."

    def post_summary(self, content: str) -> str:
        body = f"{content}\n\n{build_marker(self._skill, 'summary')}"
        existing = max(self._own("summary"), key=lambda t: t.id, default=None)
        if existing is not None:
            try:
                self._client.update_comment(existing.id, existing.first_comment_id, body)
            except Exception:
                existing.marker = None  # stop targeting it; post a new summary below
            else:
                existing.comments[0]["content"] = body
                return f"Summary comment updated (thread #{existing.id})."

        result = self._client.create_thread(body)
        self.threads().append(TrackedThread(
            id=result.get("id", 0),
            status="active",
            file_path=None,
            line=None,
            first_comment_id=((result.get("comments") or [{}])[0]).get("id", 1),
            comments=[{"author": "SkillFlow", "content": body}],
            marker=parse_marker(body),
        ))
        return f"Summary comment posted (thread #{result.get('id')})."


def _is_system(comment: dict) -> bool:
    """Comments Azure DevOps generates itself (votes, pushes, policy updates)."""
    return comment.get("commentType") in ("system", 3)


def _truncate(text: str, limit: int = MAX_COMMENT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + "…"
