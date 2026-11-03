"""Work commands: next, claim, release, need, review, approve, reject, done, move, reopen, cancel."""

from __future__ import annotations

from earmark.errors import EarmarkError

from earmark.cli import render as R
from earmark.cli.common import Context, Result, _key, _labels, empty_text


def _note(ctx: Context, a) -> str | None:
    return ctx.read_text(a.note, None, "note")


def _moved(issue: dict, verb: str, ctx: Context | None = None, a=None) -> Result:
    """The one-line result of a move; with --next, then `next` (see then_next)."""
    if a is not None and getattr(a, "then_next", False):
        return then_next(ctx, issue, verb)
    return Result(issue, lambda: R.status_line(issue, verb))


def then_next(ctx: Context, issue: dict, verb: str) -> Result:
    """`review|approve|reject|done|release KEY --next`: the action (committed), then `next`.
    Text: the action line, then next's output. Exit code and --json follow `next`: data
    {action, next} on a claim; nothing left is exit 4 with the action in error.details."""
    line = R.status_line(issue, verb)
    try:
        nxt, sel = claim_next(ctx)
    except EarmarkError as exc:
        err = exc
        err.details = dict(err.details, action=issue) if isinstance(err.details, dict) else {"action": issue}
        if err.code == "empty":
            return Result(None, lambda: line + "\n" + empty_text("next", err, ctx), error=err)
        return Result(None, line, error=err)
    return Result({"action": issue, "next": nxt}, lambda: line + "\n\n" + next_text(ctx, nxt, sel))


def claim_next(ctx: Context, *, review: bool | None = None, topics=(), dry_run: bool = False):
    """Pick and claim, then read the comments the actor has not seen (advancing the cursor), so
    starting an issue is one call. Returns (issue with `comments`, selection); Empty passes up."""
    b = ctx.board()
    issue = b.next(review=review, topics=list(topics or ()), dry_run=dry_run)
    if dry_run:  # nothing claimed, no cursor moved
        return issue, None
    page = b.list_comments(_key(issue), new=True) or {}
    items = list(page.get("items") or [])
    issue["comments"] = items
    return issue, {"items": items, "mode": "new", "last_id": page.get("last_id"),
                   "has_more": bool(page.get("has_more"))}


def next_text(ctx: Context, issue: dict, sel: dict | None, dry_run: bool = False) -> str:
    out = R.render_show(issue, style=ctx.style, comments=sel)
    if dry_run:
        out += f"\n\ndry run: not claimed{R.SEP}earmark claim {_key(issue)}"
    return out


def cmd_next(ctx: Context, a) -> Result:
    issue, sel = claim_next(ctx, review=True if a.review else None, topics=a.topic, dry_run=a.dry_run)
    return Result(issue, lambda: next_text(ctx, issue, sel, a.dry_run))


def cmd_need(ctx: Context, a) -> Result:
    body = ctx.read_text(a.body, a.body_file, "body")
    given = dict(topic=a.topic, body=body, priority=a.priority, type=a.type,
                 labels=_labels(a.label) or None, assignee=a.assign, attach=list(a.attach) or None)
    res = ctx.board().need(a.key, a.title, **{k: v for k, v in given.items() if v is not None})
    return Result(res, lambda: R.render_need(res, actor=ctx.actor))


def cmd_claim(ctx: Context, a) -> Result:
    return _moved(ctx.board().claim(a.key), "claimed")


def cmd_release(ctx: Context, a) -> Result:
    note = _note(ctx, a)
    return _moved(ctx.board().release(a.key, note=note), "released", ctx, a)


def cmd_review(ctx: Context, a) -> Result:
    note = _note(ctx, a)
    return _moved(ctx.board().submit_review(a.key, note=note), "sent to review", ctx, a)


def cmd_approve(ctx: Context, a) -> Result:
    note = _note(ctx, a)
    return _moved(ctx.board().approve(a.key, note=note), "approved", ctx, a)


def cmd_reject(ctx: Context, a) -> Result:
    note = _note(ctx, a)
    return _moved(ctx.board().reject(a.key, note=note), "rejected", ctx, a)


def cmd_done(ctx: Context, a) -> Result:
    note = _note(ctx, a)
    return _moved(ctx.board().done(a.key, note=note), "done", ctx, a)


def cmd_move(ctx: Context, a) -> Result:
    note = _note(ctx, a)
    return _moved(ctx.board().move(a.key, a.status.strip().lower(), note=note), "moved")


def cmd_reopen(ctx: Context, a) -> Result:
    note = _note(ctx, a)
    return _moved(ctx.board().reopen(a.key, note=note), "reopened")


def cmd_cancel(ctx: Context, a) -> Result:
    note = _note(ctx, a)
    return _moved(ctx.board().cancel(a.key, note=note), "canceled")
