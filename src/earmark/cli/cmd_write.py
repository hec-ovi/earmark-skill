"""Write commands: new, edit, comment, link, unlink, assign, rank, delete."""

from __future__ import annotations

from typing import Any

from earmark.errors import InvalidRequest

from earmark.cli import render as R
from earmark.cli.common import Context, Result, _comment_id, _key, _labels, canon_key


def cmd_new(ctx: Context, a) -> Result:
    body = ctx.read_text(a.body, a.body_file, "body")
    labels = [lb.lstrip("+") for lb in _labels(a.label)]
    given = dict(body=body, topic=a.topic, priority=a.priority, type=a.type, status=a.status,
                 labels=labels or None, due=a.due, parent=a.parent, assignee=a.assign)
    kw = {k: v for k, v in given.items() if v is not None}
    issue = ctx.board().create_issue(a.title, **kw)
    return Result(issue, lambda: _key(issue))


def cmd_edit(ctx: Context, a) -> Result:
    fields: dict[str, Any] = {}
    if a.title is not None:
        fields["title"] = a.title
    body = ctx.read_text(a.body, a.body_file, "body")
    if body is not None:
        fields["body"] = body
    if a.topic is not None:
        fields["topic"] = a.topic
    if a.no_topic:
        fields["topic"] = None
    if a.priority is not None:
        fields["priority"] = a.priority
    if a.type is not None:
        fields["type"] = a.type
    if a.due is not None:
        fields["due"] = a.due
    if a.no_due:
        fields["due"] = None
    if a.parent is not None:
        fields["parent"] = a.parent
    if a.no_parent:
        fields["parent"] = None
    add, remove = [], []
    for lb in _labels(a.label):
        if lb.startswith("-"):
            remove.append(lb[1:])
        else:
            add.append(lb.lstrip("+"))
    append = ctx.read_text(a.append, None, "append")
    if not fields and not add and not remove and append is None:
        raise InvalidRequest("edit: nothing to change",
                             hint=f"earmark edit {a.key} --title T | -b BODY | -t PATH | -p PRIO | -l +LABEL ...")
    issue = ctx.board().update_issue(a.key, if_version=a.if_version, append=append,
                                     add_labels=add, remove_labels=remove, **fields)
    changed = list(fields)
    if append is not None and "body" not in changed:
        changed.append("body")
    if add or remove:
        changed.append("labels")
    return Result(issue, lambda: f"{_key(issue)} updated: {', '.join(changed)}{R.SEP}version "
                                 f"{R.dash(issue.get('version'))}")


def cmd_comment(ctx: Context, a) -> Result:
    if a.edit is not None:
        cid = _comment_id(a.edit, "--edit")
        if a.key is not None and a.text is not None:
            raise InvalidRequest("comment --edit takes ID and TEXT, no KEY",
                                 hint=f'earmark comment --edit {cid} "new text"')
        inline = a.text if a.text is not None else a.key
        if inline is not None and a.body_file is not None:
            raise InvalidRequest("comment: give TEXT or --body-file, not both")
        body = ctx.read_text(inline, a.body_file, "comment")
        if not body:
            raise InvalidRequest("comment --edit: empty body", hint=f'earmark comment --edit {cid} "new text"')
        c = ctx.board().edit_comment(cid, body)
        return Result(c, lambda: f"#{c.get('id', cid)} edited")
    if a.key is None:
        raise InvalidRequest("comment: KEY is required", hint='earmark comment KEY "text"')
    if a.text is not None and a.body_file is not None:
        raise InvalidRequest("comment: give TEXT or --body-file, not both")
    body = ctx.read_text(a.text, a.body_file, "comment")
    if not body:
        raise InvalidRequest("comment: empty body",
                             hint=f'earmark comment {a.key} "text"  (or - to read stdin)')
    reply_to = _comment_id(a.reply_to, "--reply-to")
    kw: dict[str, Any] = {}
    if a.kind:
        kw["kind"] = a.kind
    if reply_to is not None:
        kw["reply_to"] = reply_to
    c = ctx.board().add_comment(a.key, body, **kw)
    return Result(c, lambda: f"#{c.get('id')}")


def _link(ctx: Context, a, verb: str) -> Result:
    b = ctx.board()
    issue = (b.link if verb == "linked" else b.unlink)(a.key, a.kind, a.target)
    target = canon_key(ctx.prefix, a.target)
    return Result(issue, lambda: f"{verb}: {_key(issue, canon_key(ctx.prefix, a.key))} {a.kind} {target}")


def cmd_link(ctx: Context, a) -> Result:
    return _link(ctx, a, "linked")


def cmd_unlink(ctx: Context, a) -> Result:
    return _link(ctx, a, "unlinked")


def cmd_assign(ctx: Context, a) -> Result:
    handle = None if a.handle.strip().lower() in ("none", "-") else a.handle.strip()
    issue = ctx.board().assign(a.key, handle)
    return Result(issue, lambda: f"{_key(issue)} assignee: {R.dash(issue.get('assignee'))}")


def cmd_rank(ctx: Context, a) -> Result:
    issue = ctx.board().rank(a.key, before=a.before, after=a.after, top=a.top, bottom=a.bottom)
    if a.top or a.bottom:
        where = "top" if a.top else "bottom"
    else:
        where = ("before " if a.before else "after ") + canon_key(ctx.prefix, a.before or a.after)
    return Result(issue, lambda: f"{_key(issue)} ranked: {where}")


def cmd_delete(ctx: Context, a) -> Result:
    if not a.yes:
        raise InvalidRequest(f"delete {a.key}: needs --yes, deletion cannot be undone",
                             hint=f'earmark cancel {a.key} -m "why" keeps the history; '
                                  f"or earmark delete {a.key} --yes")
    res = ctx.board().delete_issue(a.key)
    key = (res or {}).get("deleted") if isinstance(res, dict) else None
    return Result(res, lambda: f"deleted {key or canon_key(ctx.prefix, a.key)}")
