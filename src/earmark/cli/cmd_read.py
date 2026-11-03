"""Read commands: topics, ls, show, comments, inbox."""

from __future__ import annotations

from earmark.errors import InvalidRequest

from earmark.cli import render as R
from earmark.cli.common import Context, Result, _bad_field, _comment_id, _labels, _split, canon_key


def cmd_topics(ctx: Context, a) -> Result:
    nodes = ctx.board().topics(root=a.path, depth=a.depth, include_archived=a.all)
    return Result(nodes, lambda: R.render_topics(nodes, style=ctx.style))


def _ls_kwargs(a) -> dict:
    return dict(topic=a.topic, exact=a.exact, status=a.status or None, priority=_split(a.priority),
                min_priority=a.min_priority, assignee=a.assignee, reviewer=a.reviewer,
                reporter=a.reporter, type=_split(a.type), label=_labels(a.label), q=a.q,
                due_before=a.due_before, overdue=a.overdue, updated_since=a.updated_since,
                created_since=a.created_since, ready=a.ready, blocked=a.blocked, stale=a.stale,
                parent=a.parent, sort=a.sort, desc=a.desc, limit=a.limit, offset=a.offset)


def cmd_ls(ctx: Context, a) -> Result:
    try:
        fields = R.ls_fields(a.fields)
    except ValueError as e:
        raise _bad_field("ls", str(e), list(R.LS_COLUMNS) + ["flags"]) from None
    if a.limit < 0 or a.offset < 0:
        raise InvalidRequest("ls: --limit and --offset must be 0 or more")
    b = ctx.board()
    kw = _ls_kwargs(a)
    if a.count:
        page = b.list_issues(**{**kw, "limit": 1, "offset": 0})
        total = page.get("total", 0)
        return Result({"total": total}, str(total))
    page = b.list_issues(**kw)
    noun = R.status_word(a.status)
    return Result(page, lambda: R.render_ls(page, fields=fields, noun=noun, style=ctx.style,
                                            width=ctx.width))


def cmd_show(ctx: Context, a) -> Result:
    fields = None
    if a.fields:
        try:
            fields = R.show_fields(a.fields)
        except ValueError as e:
            raise _bad_field("show", str(e), R.SHOW_FIELDS) from None
    n = a.comments or 0
    if n < 0:
        raise InvalidRequest("show: -c N must be 0 or more")
    issue = ctx.board().get_issue(a.key, comments=n)
    if fields:
        data = {f: R.field_value(issue, f) for f in fields}
        return Result(data, lambda: R.render_fields(issue, fields))
    return Result(issue, lambda: R.render_show(issue, style=ctx.style))


def cmd_comments(ctx: Context, a) -> Result:
    since = _comment_id(a.since, "--since")
    if a.last is not None and a.last < 1:
        raise InvalidRequest("comments: --last N must be 1 or more")
    b = ctx.board()
    page = b.list_comments(a.key, since=since, new=a.new, last=a.last, limit=a.limit)
    key = canon_key(ctx.prefix, a.key)
    return Result(page, lambda: R.render_comments(page, key=key, since=since, new=a.new,
                                                  style=ctx.style))


def cmd_inbox(ctx: Context, a) -> Result:
    page = ctx.board().inbox(peek=a.peek, limit=a.limit)
    return Result(page, lambda: R.render_inbox(page, peek=a.peek, style=ctx.style))
