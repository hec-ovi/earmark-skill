"""Work commands: next, claim, release, need, review, approve, reject, done, move, reopen, cancel."""

from __future__ import annotations

from earmark.cli import render as R
from earmark.cli.common import Context, Result, _key, _labels


def _note(ctx: Context, a) -> str | None:
    return ctx.read_text(a.note, None, "note")


def _moved(issue: dict, verb: str) -> Result:
    return Result(issue, lambda: R.status_line(issue, verb))


def cmd_next(ctx: Context, a) -> Result:
    issue = ctx.board().next(review=True if a.review else None, topics=a.topic, dry_run=a.dry_run)

    def text() -> str:
        out = R.render_show(issue, style=ctx.style)
        if a.dry_run:
            out += f"\n\ndry run: not claimed{R.SEP}earmark claim {_key(issue)}"
        return out

    return Result(issue, text)


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
    return _moved(ctx.board().release(a.key, note=note), "released")


def cmd_review(ctx: Context, a) -> Result:
    note = _note(ctx, a)
    return _moved(ctx.board().submit_review(a.key, note=note), "sent to review")


def cmd_approve(ctx: Context, a) -> Result:
    note = _note(ctx, a)
    return _moved(ctx.board().approve(a.key, note=note), "approved")


def cmd_reject(ctx: Context, a) -> Result:
    note = _note(ctx, a)
    return _moved(ctx.board().reject(a.key, note=note), "rejected")


def cmd_done(ctx: Context, a) -> Result:
    note = _note(ctx, a)
    return _moved(ctx.board().done(a.key, note=note), "done")


def cmd_move(ctx: Context, a) -> Result:
    note = _note(ctx, a)
    return _moved(ctx.board().move(a.key, a.status.strip().lower(), note=note), "moved")


def cmd_reopen(ctx: Context, a) -> Result:
    note = _note(ctx, a)
    return _moved(ctx.board().reopen(a.key, note=note), "reopened")


def cmd_cancel(ctx: Context, a) -> Result:
    note = _note(ctx, a)
    return _moved(ctx.board().cancel(a.key, note=note), "canceled")
