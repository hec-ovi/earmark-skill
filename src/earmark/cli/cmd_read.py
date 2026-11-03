"""Read commands: topics, ls, show, comments, inbox."""

from __future__ import annotations

from earmark.errors import InvalidRequest, NotFound

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
    extra = sorted({R.LS_EXTRA[f] for f in fields if f in R.LS_EXTRA})
    if extra:  # show's other scalars: asked for only when a column needs them
        kw["extra"] = extra
    page = b.list_issues(**kw)
    noun = R.status_word(a.status)
    return Result(page, lambda: R.render_ls(page, fields=fields, noun=noun, style=ctx.style,
                                            width=ctx.width))


class ShowSpec:
    """What `show` prints: fields (None = the full view) and which comments, if any."""

    __slots__ = ("fields", "comments", "mode", "since", "last")

    def __init__(self, a) -> None:
        fields = None
        if a.fields:
            try:
                fields = R.show_fields(a.fields) or None
            except ValueError as e:
                raise _bad_field("show", str(e), R.SHOW_FIELDS) from None
        if a.comments is not None and a.comments < 0:
            raise InvalidRequest("show: -c N must be 0 or more")
        if a.last is not None and a.last < 1:
            raise InvalidRequest("show: --last N must be 1 or more")
        if a.new and a.last is not None:
            raise InvalidRequest("show: give either --new or --last, not both: --new advances your "
                                 "cursor past every unread comment, --last would hide the rest",
                                 hint="--new (everything unread) or --last N (the latest N)")
        self.since = _comment_id(a.since, "--since")
        self.mode = "new" if a.new else "since" if self.since is not None else "last"
        picked = a.new or self.since is not None or a.last is not None
        c = a.comments or 0
        # -c N, --new, --since and --last add the comments part to whatever -f selects
        if fields is not None and (picked or c) and "comments" not in fields:
            fields.append("comments")
        self.fields = fields
        self.comments = ("comments" in fields) if fields is not None else bool(picked or c)
        self.last = a.last if a.last is not None else (c or None)
        if self.mode == "new":
            self.last = None  # every unread one: the cursor moves past all it returns
        elif self.mode == "last" and self.last is None:
            self.last = 5

    def fetch(self, board, key: str) -> tuple[dict, dict | None]:
        """(issue, comment selection) for one key: one call for the last N, two otherwise."""
        if not self.comments:
            return board.get_issue(key, comments=0), None
        if self.mode == "last":
            issue = board.get_issue(key, comments=self.last)
            return issue, {"items": list(issue.get("comments") or []), "mode": "last"}
        issue = board.get_issue(key, comments=0)
        page = board.list_comments(key, since=self.since, new=self.mode == "new", last=self.last,
                                   limit=100)
        items = list(page.get("items") or [])
        issue["comments"] = items
        return issue, {"items": items, "mode": self.mode, "since": self.since,
                       "last_id": page.get("last_id"), "has_more": bool(page.get("has_more"))}

    def data(self, issue: dict, *, with_key: bool = False) -> dict:
        if self.fields is None:
            return issue
        out = {"key": issue.get("key")} if with_key and "key" not in self.fields else {}
        out.update((f, R.field_value(issue, f)) for f in self.fields)
        return out


def _missing(errors: list[NotFound]) -> NotFound:
    """One not_found for the keys of a several-key show that do not exist."""
    messages = [e.message for e in errors]
    if all(m.startswith("no issue ") for m in messages):
        message = "no issue " + ", ".join(m[len("no issue "):] for m in messages)
    else:
        message = "; ".join(messages)
    return NotFound(message, hint=errors[0].hint)


def cmd_show(ctx: Context, a) -> Result:
    spec = ShowSpec(a)
    b = ctx.board()
    seen: set[str] = set()
    keys = []
    for raw in a.keys:
        canon = canon_key(ctx.prefix, raw)
        if canon not in seen:
            seen.add(canon)
            keys.append(raw)
    found: list[tuple[dict, dict | None]] = []
    errors: list[NotFound] = []
    missing: list[str] = []
    for raw in keys:
        try:
            found.append(spec.fetch(b, raw))
        except NotFound as e:
            if len(keys) == 1:
                raise
            errors.append(e)
            missing.append(canon_key(ctx.prefix, raw))
    if len(keys) == 1:
        issue, sel = found[0]
        data = spec.data(issue)
        if spec.fields is None:
            return Result(data, lambda: R.render_show(issue, style=ctx.style, comments=sel))
        return Result(data, lambda: R.render_fields(issue, spec.fields, comments=sel, style=ctx.style))
    items = [spec.data(i, with_key=True) for i, _ in found]
    error = None
    if errors:
        error = _missing(errors)
        error.details = {"missing": missing, "found": items}
        if not found:
            raise error

    def text() -> str:
        return R.render_show_many([i for i, _ in found], spec.fields, comments=[s for _, s in found],
                                  style=ctx.style)

    return Result(items, text, error=error)


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
