"""Helpers shared by the command handlers. Handlers shape inputs and outputs only: the board
parses keys, dates, priorities and handles, enforces rules and identity, and raises
earmark.errors."""

from __future__ import annotations

TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only; typing stays off the startup path
    from typing import Any

from earmark.errors import EarmarkError, InvalidRequest

from earmark.cli import render as R
from earmark.cli.app import Context, Result  # noqa: F401  (re-exported for handlers)


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _labels(values: list[str]) -> list[str]:
    out = []
    for v in values:
        out.extend(_split(v))
    return out


def _comment_id(raw: str | None, what: str = "comment id") -> int | None:
    if raw is None:
        return None
    s = str(raw).strip().lstrip("#")
    if not s.isdigit():
        raise InvalidRequest(f"{what} must be a number like 1190 or #1190, got '{raw}'")
    return int(s)


def canon_key(prefix: str | None, raw: str) -> str:
    """Display form of a key the user typed: '42', '#42', 'game-42' -> 'GAME-42'."""
    s = str(raw).strip().lstrip("#")
    if s.isdigit() and prefix:
        return f"{prefix}-{int(s)}"
    return s.upper()


def _key(issue: Any, fallback: str = "?") -> str:
    if isinstance(issue, dict):
        return str(issue.get("key") or fallback)
    return fallback


def _bad_field(cmd: str, field: str, valid) -> InvalidRequest:
    return InvalidRequest(f"{cmd}: unknown field '{field}'", hint="fields: " + ",".join(valid))


def empty_text(command: str, e: EarmarkError, ctx: Context | None) -> str:
    """Exit-4 answer line for stdout. `next` gets its counts line from commands.md."""
    d = e.details if isinstance(e.details, dict) else {}
    if command == "next" and any(k in d for k in ("review_queue", "inbox", "stale")):
        if "mode" in d:
            review = d.get("mode") == "review"
        else:
            review = bool(ctx and getattr(ctx.ns, "review", False))
        changes = f"changes for you: {d.get('changes', 0)}"  # the check feed, as the hint counts
        if review:
            return R.SEP.join(["next: nothing to review for you (your own handoffs excluded)",
                               changes])
        return R.SEP.join(["next: nothing ready for you",
                           f"reviews for you: {d.get('review_queue', 0)}", changes,
                           f"stale: {d.get('stale', 0)}"])
    return e.message
