"""Attachment commands: attach, attachments, attachment, detach."""

from __future__ import annotations

from earmark.cli import render as R
from earmark.cli.common import Context, Result, _comment_id, canon_key


def cmd_attach(ctx: Context, a) -> Result:
    comment = _comment_id(a.comment, "--comment")
    kw = {"comment": comment} if comment is not None else {}
    items = ctx.board().attach(a.key, list(a.files), **kw)
    return Result(items, lambda: "\n".join(f"@{i.get('id')}" for i in items))


def cmd_attachments(ctx: Context, a) -> Result:
    items = ctx.board().list_attachments(a.key)
    key = canon_key(ctx.prefix, a.key)
    return Result(items, lambda: R.render_attachments(items, key=key, style=ctx.style))


def cmd_attachment(ctx: Context, a) -> Result:
    if a.out is not None:
        res = ctx.board().save_attachment(a.id, a.out)
        return Result(res, lambda: R.render_saved(res))
    item = ctx.board().get_attachment(a.id)
    return Result(item, lambda: R.render_attachment(item, style=ctx.style))


def cmd_detach(ctx: Context, a) -> Result:
    res = ctx.board().detach(a.id)
    return Result(res, lambda: f"detached @{res.get('deleted')} {res.get('name')} from {res.get('issue')}")
