"""Pure renderers: board dicts (shapes in docs/HTTP_API.md) in, text out.

Plain ASCII-aligned text unless a Style with color is passed (stdout is a terminal and
NO_COLOR is unset). Widths are measured on plain text and color is applied after padding, so
alignment never depends on the terminal. Bodies (descriptions, comments, prompts) are never cut.
"""

from __future__ import annotations

import time
TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only; typing stays off the startup path
    from typing import Any, Callable, Iterable

SEP = " · "
TOPIC_WIDTH = 29          # ls TOPIC column cap in a terminal; piped output prints paths in full
CLOSED = ("done", "canceled")


class Style:
    """ANSI styling; every method is the identity when color is off."""

    STATUS = {"backlog": "2", "todo": "", "in_progress": "33", "in_review": "36",
              "blocked": "31", "done": "32", "canceled": "2"}
    PRIORITY = {"urgent": "1;31", "high": "31", "medium": "", "low": "2", "none": "2"}
    FLAG = {"overdue": "31", "blocked": "31", "stale": "33"}

    def __init__(self, color: bool = False, ellipsis: str = "...") -> None:
        self.color = color
        self.ellipsis = ellipsis

    def paint(self, text: str, code: str | None) -> str:
        if not self.color or not code or not text:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"

    def bold(self, text: str) -> str:
        return self.paint(text, "1")

    def dim(self, text: str) -> str:
        return self.paint(text, "2")

    def status(self, text: str) -> str:
        return self.paint(text, self.STATUS.get(text.strip()))

    def priority(self, text: str) -> str:
        return self.paint(text, self.PRIORITY.get(text.strip()))

    def cell(self, text: str) -> str:
        return self.dim(text) if text.strip() == "-" else text

    def flag(self, text: str) -> str:
        return self.paint(text, self.FLAG.get(text.split(":", 1)[0], "2"))


PLAIN = Style(False)


# ---- terminal safety -------------------------------------------------------------------

# C0 except tab and newline, DEL and C1 print as visible `\xNN`, so text from the board
# (titles, bodies, comments, names) cannot move the cursor, retitle the terminal or write the
# clipboard. JSON output is not touched.
CONTROL_ESCAPES = {c: f"\\x{c:02x}" for c in (*range(0x09), *range(0x0B, 0x20), *range(0x7F, 0xA0))}
JSON_C1 = {c: f"\\u{c:04x}" for c in range(0x7F, 0xA0)}
OWN_SGR = r"(\x1b\[(?:0|1|2|31|32|33|36|1;31)m)"   # the only sequences Style emits


def escape_controls(text: str, *, color: bool = False) -> str:
    """Final text for stdout/stderr. CRLF line ends become LF; with color on, Style's own
    SGR codes stay and every other escape is shown."""
    if "\r\n" in text:
        text = text.replace("\r\n", "\n")
    if not color or "\x1b" not in text:
        return text.translate(CONTROL_ESCAPES)
    import re
    pieces = re.split(OWN_SGR, text)
    return "".join(p if i % 2 else p.translate(CONTROL_ESCAPES) for i, p in enumerate(pieces))


def json_text(text: str) -> str:
    """JSON printed as text (export): DEL and C1, which json leaves raw, as \\u escapes."""
    return text.translate(JSON_C1)


def one_line(text: str) -> str:
    """A value printed on one line (titles, cells): line breaks shown as \\n."""
    if "\n" in text or "\r" in text:
        return text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\r")
    return text


# ---- small helpers ---------------------------------------------------------------------

def dash(value: Any) -> str:
    """Display value: '-' for empty, lists joined with ', ', line breaks shown as \\n."""
    if value is None or value == "" or value == [] or value == ():
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(dash(_key_of(v)) for v in value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return one_line(str(value))


def _key_of(v: Any) -> Any:
    if isinstance(v, dict):
        return v.get("key") or v.get("path") or v.get("handle") or v.get("slug") or v.get("id")
    return v


def cut(text: str, width: int, ellipsis: str = "") -> str:
    """Cut to width; with an ellipsis the result still fits in width."""
    if width <= 0 or len(text) <= width:
        return text
    if not ellipsis:
        return text[:width]
    return text[: max(0, width - len(ellipsis))].rstrip() + ellipsis


def today() -> str:
    """UTC date, like the board's due dates (follows the board clock when it is loaded)."""
    try:
        from earmark import timeutil
        return timeutil.today().isoformat()
    except ImportError:
        return time.strftime("%Y-%m-%d", time.gmtime())


def now_epoch() -> float:
    try:
        from earmark import timeutil
        return timeutil.now().timestamp()
    except ImportError:
        return time.time()


def plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def is_overdue(issue: dict, day: str) -> bool:
    due = issue.get("due")
    return bool(due) and due < day and issue.get("status") not in CLOSED


def lines_of(text: str | None) -> list[str]:
    return (text or "").splitlines()


def indent_body(text: str, prefix: str = "  ") -> list[str]:
    out = [(prefix + line) if line.strip() else "" for line in lines_of(text)]
    return out or [prefix.rstrip() or ""]


def table(columns: list[tuple[str, Callable[[str], str] | None]], rows: list[list[str]],
          style: Style = PLAIN, tails: list[str] | None = None,
          min_widths: dict[int, int] | None = None) -> list[str]:
    """Aligned rows, two spaces between columns, the last column unpadded, tails appended."""
    widths = []
    for i, (head, _) in enumerate(columns):
        w = max([len(head)] + [len(r[i]) for r in rows])
        if min_widths and i in min_widths:
            w = max(w, min_widths[i])
        widths.append(w)
    last = len(columns) - 1

    def render(cells: list[str], header: bool, tail: str) -> str:
        parts = []
        for i, text in enumerate(cells):
            pad = "" if i == last else " " * (widths[i] - len(text) + 2)
            if header:
                shown = style.dim(text) if style.color else text
            else:
                fn = columns[i][1]
                shown = fn(text) if (fn and style.color) else style.cell(text)
            parts.append(shown + pad)
        line = "".join(parts)
        if tail:
            line = line.rstrip() + "  " + tail if not cells[-1] else line + "  " + tail
        return line

    out = [render([h for h, _ in columns], True, "")]
    for n, r in enumerate(rows):
        out.append(render(r, False, tails[n] if tails else ""))
    return out


# ---- issues: ls --------------------------------------------------------------------------

LS_DEFAULT = ("key", "status", "priority", "type", "assignee", "topic", "title", "flags")

# field -> (header, getter)
LS_COLUMNS: dict[str, tuple[str, Callable[[dict], Any]]] = {
    "key": ("KEY", lambda i: i.get("key")),
    "id": ("ID", lambda i: i.get("id")),
    "status": ("STATUS", lambda i: i.get("status")),
    "priority": ("PRIO", lambda i: i.get("priority")),
    "effective_priority": ("EPRIO", lambda i: i.get("effective_priority")),
    "type": ("TYPE", lambda i: i.get("type")),
    "assignee": ("ASSIGNEE", lambda i: i.get("assignee")),
    "reviewer": ("REVIEWER", lambda i: i.get("reviewer")),
    "reporter": ("REPORTER", lambda i: i.get("reporter")),
    "topic": ("TOPIC", lambda i: i.get("topic")),
    "title": ("TITLE", lambda i: i.get("title")),
    "labels": ("LABELS", lambda i: ",".join(i.get("labels") or [])),
    "due": ("DUE", lambda i: i.get("due")),
    "parent": ("PARENT", lambda i: i.get("parent")),
    "children": ("SUB", lambda i: i.get("children") if not isinstance(i.get("children"), list)
                 else len(i["children"])),
    "rank": ("RANK", lambda i: i.get("rank")),
    "version": ("VERSION", lambda i: i.get("version")),
    "comment_count": ("C", lambda i: i.get("comment_count")),
    "attachment_count": ("A", lambda i: i.get("attachment_count")),
    "last_comment_id": ("LAST", lambda i: f"#{i['last_comment_id']}" if i.get("last_comment_id") else None),
    "lease_until": ("LEASE", lambda i: i.get("lease_until")),
    "waiting": ("WAITING", lambda i: i.get("waiting")),
    "blocked": ("BLOCKED", lambda i: i.get("blocked")),
    "blocks_open": ("HOLDS", lambda i: i.get("blocks_open")),
    "stale": ("STALE", lambda i: i.get("stale")),
    "created_at": ("CREATED", lambda i: i.get("created_at")),
    "updated_at": ("UPDATED", lambda i: i.get("updated_at")),
    # the rest of show's scalars; ls asks the board for them (list_issues extra=LS_EXTRA[...])
    "started_at": ("STARTED", lambda i: i.get("started_at")),
    "review_requested_at": ("REVIEW_REQUESTED", lambda i: i.get("review_requested_at")),
    "resolved_at": ("RESOLVED", lambda i: i.get("resolved_at")),
    "new_comments": ("NEW", lambda i: i.get("new_comments")),
    "blocks": ("BLOCKS", lambda i: ",".join(_links(i, "blocks"))),
    "blocked_by": ("BLOCKED_BY", lambda i: ",".join(_links(i, "blocked_by"))),
    "duplicates": ("DUPLICATES", lambda i: ",".join(_links(i, "duplicates"))),
    "duplicated_by": ("DUPLICATED_BY", lambda i: ",".join(_links(i, "duplicated_by"))),
    "relates": ("RELATES", lambda i: ",".join(_links(i, "relates"))),
}
LS_EXTRA = {"started_at": "started_at", "review_requested_at": "review_requested_at",
            "resolved_at": "resolved_at", "new_comments": "new_comments",
            **{name: "links" for name in ("blocks", "blocked_by", "duplicates", "duplicated_by",
                                          "relates")}}
LS_INTERNAL = {"waiting"}   # triage-only column
LS_ALIASES = {"prio": "priority", "comments": "comment_count", "c": "comment_count",
              "created": "created_at", "updated": "updated_at", "lease": "lease_until",
              "sub": "children", "label": "labels", "last_comment": "last_comment_id",
              "attachments": "attachment_count", "a": "attachment_count",
              "effective": "effective_priority", "eprio": "effective_priority",
              "holds": "blocks_open"}


def ls_fields(spec: str | None) -> list[str]:
    """Parse --fields; raises ValueError naming the unknown field."""
    if not spec:
        return list(LS_DEFAULT)
    out = []
    for raw in spec.split(","):
        name = raw.strip().lower().replace("-", "_")
        if not name:
            continue
        name = LS_ALIASES.get(name, name)
        if name != "flags" and (name not in LS_COLUMNS or name in LS_INTERNAL):
            raise ValueError(raw.strip())
        if name not in out:
            out.append(name)
    return out or list(LS_DEFAULT)


def issue_flags(issue: dict, day: str | None = None) -> list[str]:
    """Trailing ls flags: blocks:N c:N due:MM-DD overdue blocked stale sub:N a:N."""
    day = day or today()
    flags = []
    if issue.get("blocks_open"):
        flags.append(f"blocks:{issue['blocks_open']}")
    if issue.get("comment_count"):
        flags.append(f"c:{issue['comment_count']}")
    due = issue.get("due")
    if due:
        flags.append("due:" + (due[5:] if due[:4] == day[:4] else due))
        if is_overdue(issue, day):
            flags.append("overdue")
    if issue.get("blocked"):
        flags.append("blocked")
    if issue.get("stale"):
        flags.append("stale")
    kids = issue.get("children")
    n = len(kids) if isinstance(kids, list) else (kids or 0)
    if n:
        flags.append(f"sub:{n}")
    if issue.get("attachment_count"):
        flags.append(f"a:{issue['attachment_count']}")
    return flags


def status_word(status: Any) -> str:
    """Footer noun: '212 open issues', 'closed issues', 'issues'."""
    if status in (None, "", "open", ["open"]):
        return "open issues"
    if isinstance(status, (list, tuple)) and len(status) == 1:
        status = status[0]
    if status in ("active", "closed"):
        return f"{status} issues"
    return "issues"


def ls_footer(page: dict, noun: str = "open issues") -> str:
    items = page.get("items") or []
    total = page.get("total", len(items))
    offset = page.get("offset") or 0
    parts = [f"{len(items)} of {total} {noun}"]
    if offset:
        parts.append(f"offset {offset}")
    if offset + len(items) < total and items:
        parts.append(f"next: --offset {offset + len(items)}")
    return SEP.join(parts)


def render_ls(page: dict, *, fields: Iterable[str] | None = None, noun: str = "open issues",
              style: Style = PLAIN, day: str | None = None, width: int | None = None) -> str:
    """`earmark ls` text: header, aligned columns, '-' for empty, flags last, footer.

    `width` is the terminal's (stdout is a TTY): long topics and titles are shortened to fit.
    Without it (piped, agents) every cell prints in full, so a topic path can be passed on."""
    items = page.get("items") or []
    fields = list(fields or LS_DEFAULT)
    footer = ls_footer(page, noun)
    if not items:
        return style.dim(footer) if style.color else footer
    day = day or today()
    show_flags = "flags" in fields
    cols = [f for f in fields if f != "flags"]
    tails = [" ".join(style.flag(f) for f in issue_flags(i, day)) if show_flags else ""
             for i in items]
    tail_plain = [" ".join(issue_flags(i, day)) if show_flags else "" for i in items]
    rows = []
    for i in items:
        row = []
        for f in cols:
            text = dash(LS_COLUMNS[f][1](i))
            if f == "topic" and width:
                text = cut(text, TOPIC_WIDTH, style.ellipsis)
            row.append(text)
        rows.append(row)
    if "title" in cols and width:
        ti = cols.index("title")
        others = sum(max(len(LS_COLUMNS[c][0]), *(len(r[k]) for r in rows)) + 2
                     for k, c in enumerate(cols) if k != ti)
        longest_tail = max((len(t) for t in tail_plain), default=0)
        cap = max(24, width - others - (longest_tail + 2 if longest_tail else 0) - 1)
        for r in rows:
            r[ti] = cut(r[ti], cap, style.ellipsis)
    painters: dict[str, Callable[[str], str]] = {"status": style.status, "priority": style.priority,
                                                 "key": style.bold}
    columns = [(LS_COLUMNS[f][0], painters.get(f)) for f in cols]
    out = table(columns, rows, style, tails)
    out.append(style.dim(footer) if style.color else footer)
    return "\n".join(out)


# ---- issues: show ------------------------------------------------------------------------

def _links(issue: dict, kind: str) -> list[str]:
    links = issue.get("links") or {}
    return list(links.get(kind) or issue.get(kind) or [])


def _children_keys(issue: dict) -> list[str]:
    kids = issue.get("children")
    if isinstance(kids, list):
        return [k["key"] if isinstance(k, dict) else str(k) for k in kids]
    return []


def holder_note(issue: dict, who: str, day: str | None = None) -> str:
    """'(lease until …)' after the holder: assignee while in_progress, reviewer while in_review."""
    lease = issue.get("lease_until")
    status = issue.get("status")
    holder = {"in_progress": "assignee", "in_review": "reviewer"}.get(status or "")
    if not lease or holder != who or not issue.get(who):
        return ""
    return f" (lease until {lease}, stale)" if issue.get("stale") else f" (lease until {lease})"


def with_status(issue: dict, keys: list[str]) -> list[str]:
    """`GAME-77 (done)`: linked keys with their status when the issue carries it."""
    status = issue.get("link_status") or {}
    return [f"{k} ({status[k]})" if k in status else k for k in keys]


def effective_note(issue: dict) -> str:
    """` (effective high, blocks GAME-42)` when what it holds up is more urgent than itself."""
    eff = issue.get("effective_priority")
    if not eff or eff == issue.get("priority"):
        return ""
    status = issue.get("link_status") or {}
    holds = [k for k in _links(issue, "blocks") if status.get(k) not in CLOSED]
    return f" (effective {eff}, blocks {', '.join(holds)})" if holds else f" (effective {eff})"


def comments_summary(issue: dict) -> str:
    n = issue.get("comment_count") or 0
    text = f"comments: {n}"
    if n and issue.get("last_comment_id"):
        text += f", last #{issue['last_comment_id']}"
    new = issue.get("new_comments")
    if n and new is not None:
        text += f", {new} new for you"
    return text


KIND_PLURALS = {"image": "images", "video": "videos", "pdf": "pdfs", "file": "files"}
KIND_ORDER = ("image", "audio", "video", "pdf", "text", "file")


def attachments_summary(issue: dict) -> str:
    """`attachments: 2 (1 image, 1 audio)`; '' when the issue has none."""
    items = issue.get("attachments")
    if not isinstance(items, list):
        n = issue.get("attachment_count") or 0
        return f"attachments: {n}" if n else ""
    if not items:
        return ""
    counts = {k: 0 for k in KIND_ORDER}
    for a in items:
        kind = a.get("kind") if a.get("kind") in counts else "file"
        counts[kind] += 1
    parts = [f"{n} {KIND_PLURALS.get(k, k) if n > 1 else k}" for k, n in counts.items() if n]
    return f"attachments: {len(items)} ({', '.join(parts)})"


def render_show(issue: dict, *, style: Style = PLAIN, day: str | None = None,
                comments: dict | None = None) -> str:
    """`earmark show` (and `next`) text, exactly the commands.md layout, then the comments
    picked (see comment_part; default: the issue's `comments`, the last N)."""
    day = day or today()
    g = "   "
    due = dash(issue.get("due"))
    if is_overdue(issue, day):
        due += " (overdue)"
    head = f"{style.bold(issue.get('key', '?'))}  {one_line(str(issue.get('title') or ''))}"
    lines = [
        head,
        g.join([f"status: {style.status(dash(issue.get('status')))}",
                f"priority: {style.priority(dash(issue.get('priority')))}{effective_note(issue)}",
                f"type: {dash(issue.get('type'))}", f"version: {dash(issue.get('version'))}"]),
        f"topic: {dash(issue.get('topic'))}",
        g.join([f"assignee: {dash(issue.get('assignee'))}{holder_note(issue, 'assignee')}",
                f"reviewer: {dash(issue.get('reviewer'))}{holder_note(issue, 'reviewer')}",
                f"reporter: {dash(issue.get('reporter'))}"]),
        g.join([f"labels: {dash(issue.get('labels'))}", f"due: {due}"]),
        g.join([f"parent: {dash(issue.get('parent'))}", f"sub: {dash(_children_keys(issue))}",
                f"blocks: {dash(_links(issue, 'blocks'))}",
                f"blocked by: {dash(with_status(issue, _links(issue, 'blocked_by')))}"]),
    ]
    extra = [(label, _links(issue, kind)) for label, kind in
             (("duplicates", "duplicates"), ("duplicated by", "duplicated_by"), ("relates", "relates"))]
    if any(v for _, v in extra):
        lines.append(g.join(f"{label}: {dash(v)}" for label, v in extra))
    lines.append(g.join([f"created: {dash(issue.get('created_at'))}",
                         f"updated: {dash(issue.get('updated_at'))}"]))
    lines.append(comments_summary(issue))
    files = attachments_summary(issue)
    if files:
        lines.append(files)
    lines.append("")
    body = issue.get("body") or ""
    lines.append(body.rstrip("\n") if body.strip() else style.dim("(no description)"))
    sel = comments if comments is not None else (
        {"items": issue["comments"], "mode": "last"} if issue.get("comments") else None)
    if sel and sel.get("items"):  # the full view leaves out an empty selection
        lines.append("")
        lines.extend(comment_part(issue, sel, style=style))
    return "\n".join(lines)


# `show -f`: scalar fields print as `field: value`; parts print as their own lines
SHOW_SCALARS = ("key", "id", "title", "status", "priority", "effective_priority", "type", "topic",
                "assignee", "reviewer", "reporter", "labels", "due", "parent", "blocked",
                "blocks_open", "lease_until", "stale", "rank", "version", "created_at", "updated_at",
                "started_at", "review_requested_at", "resolved_at", "comment_count",
                "last_comment_id", "new_comments", "attachment_count",
                "blocks", "blocked_by", "duplicates", "duplicated_by", "relates")
SHOW_PARTS = ("body", "comments", "attachments", "links", "children")
SHOW_FIELDS = SHOW_SCALARS + SHOW_PARTS
SHOW_ALIASES = {"sub": "children", "prio": "priority", "created": "created_at",
                "updated": "updated_at", "lease": "lease_until", "description": "body",
                "blocked-by": "blocked_by", "label": "labels", "files": "attachments",
                "effective": "effective_priority", "eprio": "effective_priority",
                "holds": "blocks_open", "last_comment": "last_comment_id"}
LINK_KINDS = (("blocks", "blocks"), ("blocked_by", "blocked by"), ("duplicates", "duplicates"),
              ("duplicated_by", "duplicated by"), ("relates", "relates"))


def show_fields(spec: str) -> list[str]:
    """Parse -f in the order given; raises ValueError naming the unknown field."""
    out = []
    for raw in spec.split(","):
        name = raw.strip().lower()
        if not name:
            continue
        name = SHOW_ALIASES.get(name, name).replace("-", "_")
        if name not in SHOW_FIELDS:
            raise ValueError(raw.strip())
        if name not in out:
            out.append(name)
    return out


def field_value(issue: dict, name: str) -> Any:
    """The JSON value of one `-f` field or part."""
    if name in ("blocks", "blocked_by", "duplicates", "duplicated_by", "relates"):
        return _links(issue, name)
    if name == "links":
        return {kind: _links(issue, kind) for kind, _ in LINK_KINDS}
    if name == "children":
        kids = issue.get("children")
        return kids if isinstance(kids, list) else []
    if name in ("attachments", "comments"):
        return list(issue.get(name) or [])
    return issue.get(name)


def comment_part(issue: dict, sel: dict | None, *, style: Style = PLAIN) -> list[str]:
    """Comments picked by show/next: a heading that says which, the comments, and the call
    for the rest when the page was cut. sel: {items, mode: new|since|last, since, last_id,
    has_more}."""
    sel = sel or {"items": list(issue.get("comments") or []), "mode": "last"}
    items, mode = sel.get("items") or [], sel.get("mode") or "last"
    if not items:
        if mode == "new":
            after = sel.get("last_id")
            return [f"comments: nothing new after #{after}" if after else "comments: nothing new"]
        if mode == "since":
            return [f"comments: nothing after #{sel.get('since')}"]
        return ["comments: none"]
    if mode == "new":
        head = f"{plural(len(items), 'new comment')}:"
    elif mode == "since":
        head = f"{plural(len(items), 'comment')} after #{sel.get('since')}:"
    else:
        total = max(issue.get("comment_count") or 0, len(items))
        head = f"last {len(items)} of {plural(total, 'comment')}:"
    lines = [style.dim(head) if style.color else head]
    for c in items:
        lines.extend(comment_lines(c, style=style))
    if sel.get("has_more"):
        key = issue.get("key", "KEY")
        lines.append(f"more: earmark comments {key} --new" if mode == "new"
                     else f"more: earmark comments {key} --since {items[-1].get('id')}")
    return lines


def part_lines(issue: dict, name: str, *, comments: dict | None = None,
               style: Style = PLAIN) -> list[str]:
    """One `show -f` part: body, comments, attachments (the table), links, children."""
    if name == "body":
        body = lines_of(issue.get("body"))
        if not body:
            return ["body: -"]
        return [f"body: {body[0]}".rstrip()] + [("  " + ln) if ln.strip() else "" for ln in body[1:]]
    if name == "comments":
        return comment_part(issue, comments, style=style)
    if name == "attachments":
        items = issue.get("attachments") or []
        return render_attachments(items, style=style).split("\n")
    if name == "links":
        lines = [f"{label}: {', '.join(with_status(issue, keys))}"
                 for kind, label in LINK_KINDS if (keys := _links(issue, kind))]
        return lines or ["links: none"]
    if name == "children":
        kids = [k for k in field_value(issue, "children") if isinstance(k, dict)]
        if not kids:
            return ["children: none"]
        return summary_rows(kids, style=style, fields=("key", "status", "priority", "assignee", "title"))
    return [f"{name}: {dash(field_value(issue, name))}"]


def render_fields(issue: dict, fields: Iterable[str], *, comments: dict | None = None,
                  style: Style = PLAIN) -> str:
    """`show KEY -f a,b`: in the order asked, `field: value` per scalar field and each part's
    lines (bodies continue indented)."""
    out: list[str] = []
    for name in fields:
        if name in SHOW_PARTS:
            out.extend(part_lines(issue, name, comments=comments, style=style))
        else:
            out.append(f"{name}: {dash(field_value(issue, name))}")
    return "\n".join(out)


def show_line(issue: dict, scalars: Iterable[str], *, key_width: int = 0,
              style: Style = PLAIN) -> str:
    """`GAME-42  status: in_progress  assignee: claude-ui`: one issue of a several-key show."""
    key = str(issue.get("key") or "?")
    cells = [f"{name}: {dash(field_value(issue, name))}" for name in scalars if name != "key"]
    if not cells:
        return style.bold(key)
    return style.bold(key) + " " * (max(key_width, len(key)) - len(key) + 2) + "  ".join(cells)


def render_show_many(issues: list[dict], fields: list[str] | None, *,
                     comments: list[dict | None] | None = None, style: Style = PLAIN,
                     day: str | None = None) -> str:
    """Several keys. Scalar fields only: one line per issue. With parts (or the full view):
    one block per issue, the key line with its scalar fields first, blocks separated by a
    blank line."""
    comments = comments or [None] * len(issues)
    if fields is None:
        return "\n\n".join(render_show(i, style=style, day=day, comments=c)
                            for i, c in zip(issues, comments))
    scalars = [f for f in fields if f not in SHOW_PARTS]
    parts = [f for f in fields if f in SHOW_PARTS]
    width = max(len(str(i.get("key") or "?")) for i in issues)
    if not parts:
        return "\n".join(show_line(i, scalars, key_width=width, style=style) for i in issues)
    blocks = []
    for issue, sel in zip(issues, comments):
        lines = [show_line(issue, scalars, style=style)]
        for name in parts:
            lines.extend(part_lines(issue, name, comments=sel, style=style))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# ---- comments and inbox ------------------------------------------------------------------

def short_time(ts: Any) -> str:
    """Comment header time: '2026-11-03T14:05:09Z' -> '11-03 14:05' (UTC); '-' when unknown."""
    if not ts:
        return "-"
    s = str(ts)
    if len(s) >= 16 and s[10] == "T" and s.endswith("Z"):
        return f"{s[5:10]} {s[11:16]}"
    try:
        from datetime import datetime, timezone
        moment = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if moment.tzinfo is not None:
            moment = moment.astimezone(timezone.utc)
        return moment.strftime("%m-%d %H:%M")
    except ValueError:
        return s


def comment_lines(c: dict, *, with_key: bool = False, style: Style = PLAIN) -> list[str]:
    head = [f"#{c.get('id')}", str(c.get("author") or "-"), str(c.get("kind") or "note"),
            short_time(c.get("created_at"))]
    if with_key:
        head.insert(0, str(c.get("issue") or "-"))
    if c.get("reply_to"):
        head.append(f"reply-to:#{c['reply_to']}")
    if c.get("edited_at"):
        head.append("edited")
    files = c.get("attachments") or []
    if files:
        head.append("files:" + ",".join(f"@{a.get('id')}" for a in files))
    first = "  ".join(head)
    if style.color:
        first = style.bold(first)
    return [first] + indent_body(c.get("body") or "")


def render_comments(page: dict, *, key: str | None = None, since: int | None = None,
                    new: bool = False, style: Style = PLAIN) -> str:
    items = page.get("items") or []
    if not items:
        after = page.get("last_id") or since
        if new or since is not None:
            word = "nothing new" if new else "nothing"
            return f"comments: {word} after #{after}" if after else f"comments: {word}"
        return f"comments: none on {key}" if key else "comments: none"
    out: list[str] = []
    for c in items:
        out.extend(comment_lines(c, style=style))
    last = page.get("last_id") or items[-1].get("id")
    foot = [f"last: #{last}", plural(len(items), "comment")]
    if page.get("has_more"):
        foot.append("more: --new" if new else f"more: --since {last}")
    out.append(SEP.join(foot))
    return "\n".join(out)


def render_inbox(page: dict, *, peek: bool = False, style: Style = PLAIN) -> str:
    items = page.get("items") or []
    if not items:
        cursor = page.get("cursor") or page.get("last_id")
        return f"inbox: nothing new after #{cursor}" if cursor else "inbox: nothing new"
    out: list[str] = []
    for c in items:
        out.extend(comment_lines(c, with_key=True, style=style))
    last = page.get("last_id") or items[-1].get("id")
    foot = [f"last: #{last}", plural(len(items), "comment")]
    if page.get("has_more"):
        # --peek leaves the cursor, so the same call would repeat this page
        foot.append(f"more: --peek --limit {2 * len(items)}" if peek else "more: earmark inbox")
    out.append(SEP.join(foot))
    return "\n".join(out)


# ---- topics ------------------------------------------------------------------------------

def render_topics(nodes: list[dict], *, style: Style = PLAIN) -> str:
    """Tree with open counts; first column min 28 wide, counts right-aligned."""
    rows: list[tuple[str, str, str, bool]] = []

    def walk(node: dict, depth: int) -> None:
        label = node.get("path") if depth == 0 else node.get("slug")
        counts = node.get("counts") or {}
        rows.append(("  " * depth + str(label or node.get("slug") or "?"),
                     str(node.get("name") or node.get("slug") or ""),
                     str(counts.get("open", 0)), bool(node.get("archived"))))
        for child in node.get("children") or []:
            walk(child, depth + 1)

    for n in nodes:
        walk(n, 0)
    if not rows:
        return "topics: none · add one: earmark topic add PATH"
    w1 = max(28, max(len(r[0]) for r in rows)) + 2
    w2 = max(len(r[1]) for r in rows) + 2
    w3 = max(len(r[2]) for r in rows)
    out = []
    for path, name, count, archived in rows:
        line = f"{path:<{w1}}{name:<{w2}}{count:>{w3}} open"
        if archived:
            line += "  archived"
        out.append(style.dim(line) if (archived and style.color) else line)
    return "\n".join(out)


# ---- team, profiles ----------------------------------------------------------------------

def parse_ts(ts: str) -> float | None:
    """'YYYY-MM-DDTHH:MM:SSZ' -> epoch seconds (UTC), None when unparsable."""
    import calendar
    try:
        return float(calendar.timegm((int(ts[0:4]), int(ts[5:7]), int(ts[8:10]),
                                      int(ts[11:13]), int(ts[14:16]), int(ts[17:19]), 0, 0, 0)))
    except (TypeError, ValueError, IndexError):
        return None


def ago(ts: str | None, now: float | None = None) -> str:
    """Compact age: 45s, 2m, 1h, 3d, 5w; '-' when unknown."""
    t = parse_ts(ts) if ts else None
    if t is None:
        return "-"
    s = max(0, int((now_epoch() if now is None else now) - t))
    for size, unit in ((604800, "w"), (86400, "d"), (3600, "h"), (60, "m")):
        if s >= size:
            return f"{s // size}{unit}"
    return f"{s}s"


def working_of(agent: dict) -> str:
    items = list(agent.get("working") or []) + list(agent.get("reviewing") or [])
    return ", ".join(f"{i.get('key')} {i.get('status')}" for i in items) or "-"


def render_team(team: dict, *, owner: str | None = None, now: float | None = None,
                style: Style = PLAIN) -> str:
    agents = team.get("agents") if isinstance(team, dict) else team
    agents = agents or []
    if not agents:
        return "team: nobody yet · earmark join PROFILE"
    rows = []
    for a in agents:
        role = a.get("role") or ("owner" if a.get("handle") == owner else a.get("kind"))
        rows.append([dash(a.get("handle")), dash(role), dash(a.get("profile")), dash(a.get("cli")),
                     ago(a.get("last_seen"), now), dash(a.get("presence")), working_of(a)])
    presence = {"active": "32", "idle": "33", "offline": "2"}
    cols = [("HANDLE", style.bold), ("ROLE", None), ("PROFILE", None), ("CLI", None),
            ("SEEN", None), ("STATE", lambda t: style.paint(t, presence.get(t.strip()))),
            ("WORKING", None)]
    return "\n".join(table(cols, rows, style, min_widths={0: 10}))


def render_profiles(profiles: list[dict], *, style: Style = PLAIN) -> str:
    if not profiles:
        return "profiles: none · earmark profile add SLUG --role R --name N"
    rows = [[dash(p.get("slug")), dash(p.get("role")), dash(p.get("name")),
             dash(p.get("topics")) if p.get("topics") else "whole board", dash(p.get("agents"))]
            for p in profiles]
    cols = [("SLUG", style.bold), ("ROLE", None), ("NAME", None), ("SCOPE", None), ("AGENTS", None)]
    return "\n".join(table(cols, rows, style))


def render_profile(p: dict, *, style: Style = PLAIN) -> str:
    lines = [f"{style.bold(str(p.get('slug')))}  {p.get('name') or ''}".rstrip(),
             "   ".join([f"role: {dash(p.get('role'))}", f"version: {dash(p.get('version'))}",
                         f"agents: {dash(p.get('agents'))}"]),
             f"scope: {dash(p.get('topics')) if p.get('topics') else 'whole board'}",
             "   ".join([f"created: {dash(p.get('created_at'))}",
                         f"updated: {dash(p.get('updated_at'))}"]),
             ""]
    prompt = p.get("prompt") or ""
    lines.append(prompt.rstrip("\n") if prompt.strip() else "(no prompt)")
    return "\n".join(lines)


def summary_rows(items: list[dict], *, style: Style = PLAIN, day: str | None = None,
                 fields: Iterable[str] = ("key", "status", "priority", "topic", "title", "flags")) -> list[str]:
    """Compact issue table without footer (briefings, whoami, triage)."""
    text = render_ls({"items": items, "total": len(items)}, fields=fields, style=style, day=day)
    return text.split("\n")[:-1]


def render_briefing(b: dict, *, style: Style = PLAIN, day: str | None = None,
                    resumed: bool = False) -> str:
    """`earmark join`: identity, how to pass it, prompt, scope, assigned issues, inbox, loop.
    resumed: an agent that had left came back under its handle (`you are H (resumed)`)."""
    agent = b.get("agent") or {}
    prof = b.get("profile") or {}
    board = b.get("board") or {}
    handle = agent.get("handle") or "?"
    role = prof.get("role") or agent.get("role") or "-"
    again = " (resumed)" if resumed else ""
    lines = [f"you are {style.bold(handle)}{again}{SEP}profile {prof.get('slug') or agent.get('profile') or '-'} "
             f"({role}){SEP}board {board.get('prefix') or '-'}",
             f"pass --as {handle} on every earmark command",
             f"scope: {dash(b.get('scope')) if b.get('scope') else 'whole board'}"]
    settings = []
    if "require_review" in board:
        rr = board.get("require_review")
        settings.append("review required: " + ("yes" if rr in (True, 1, "1") else "no"))
    if board.get("lease_minutes"):
        settings.append(f"lease: {board['lease_minutes']}m")
    if settings:
        lines.append(SEP.join(settings))
    prompt = (prof.get("prompt") or "").strip("\n")
    if prompt.strip():
        lines += ["", prompt]
    assigned = b.get("assigned") or []
    lines.append("")
    if assigned:
        lines.append(f"assigned to you ({len(assigned)}):")
        lines += summary_rows(assigned, style=style, day=day)
    else:
        lines.append("assigned to you: none")
    lines.append(f"inbox: {b.get('inbox_new') or 0} new")
    loop = (b.get("loop") or "").strip("\n")
    if loop.strip():
        lines += ["", loop]
    return "\n".join(lines)


# ---- generic -----------------------------------------------------------------------------

def _scalar(v: Any) -> str:
    if isinstance(v, dict):
        if "key" in v and ("status" in v or "title" in v):
            return f"{v['key']} {v.get('status', '')}".strip()
        return " ".join(f"{k}={dash(_key_of(x)) if not isinstance(x, dict) else _scalar(x)}"
                        for k, x in v.items()) or "-"
    if isinstance(v, (list, tuple)):
        return ", ".join(_scalar(x) for x in v) or "-"
    return dash(v)


def render_kv(d: dict, *, skip: Iterable[str] = ()) -> str:
    """Fallback `key: value` lines for dicts without a dedicated layout."""
    skip = set(skip)
    return "\n".join(f"{k}: {_scalar(v)}" for k, v in d.items() if k not in skip)


def render_doctor(checks: list[dict]) -> str:
    w = max([len(str(c.get("name"))) for c in checks] + [4])
    out = []
    for c in checks:
        mark = "ok  " if c.get("ok") else "FAIL"
        out.append(f"{mark}  {str(c.get('name')):<{w}}  {dash(c.get('detail'))}".rstrip())
    bad = sum(1 for c in checks if not c.get("ok"))
    out.append(f"doctor: {len(checks) - bad} of {len(checks)} checks ok")
    return "\n".join(out)


def human_size(n: Any) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return dash(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ---- write confirmations and board views --------------------------------------------------

def status_line(issue: dict, verb: str) -> str:
    """One line after a work command: `GAME-42 claimed · in_progress · assignee x · lease until T`."""
    status = issue.get("status")
    parts = [f"{issue.get('key', '?')} {verb}"]
    if status and status != verb:
        parts.append(status)
    if status in ("todo", "in_progress", "in_review", "blocked") and issue.get("assignee"):
        parts.append(f"assignee {issue['assignee']}")
    if status == "in_review" and issue.get("reviewer"):
        parts.append(f"reviewer {issue['reviewer']}")
    if status in ("in_progress", "in_review") and issue.get("lease_until"):
        parts.append(f"lease until {issue['lease_until']}")
    return SEP.join(parts)


def _keys(items: Any) -> str:
    if not items:
        return "-"
    if isinstance(items, (list, tuple)):
        return ", ".join(str(_key_of(i)) for i in items)
    return str(items)


def render_leave(res: Any, *, actor: str | None = None) -> str:
    if not isinstance(res, dict):
        return f"{actor or 'you'} offline"
    agent = res.get("agent") if isinstance(res.get("agent"), dict) else {}
    handle = agent.get("handle") or res.get("handle") or actor or "you"
    parts = [f"{handle} offline"]
    for label, k in (("returned", "returned"), ("released", "released"),
                     ("unassigned", "unassigned"), ("reviews returned", "reviews"),
                     ("kept", "kept")):
        if res.get(k):
            parts.append(f"{label}: {_keys(res[k])}")
    return SEP.join(parts)


def render_whoami(res: dict, *, style: Style = PLAIN, day: str | None = None) -> str:
    agent = res.get("agent") if isinstance(res.get("agent"), dict) else res
    prof = res.get("profile") if isinstance(res.get("profile"), dict) else {}
    handle = agent.get("handle") or "?"
    profile = prof.get("slug") or agent.get("profile")
    role = prof.get("role") or agent.get("role") or ("owner" if res.get("is_owner") else agent.get("kind"))
    scope = res.get("scope") if "scope" in res else prof.get("topics")
    lines = [SEP.join([f"handle: {style.bold(str(handle))}", f"profile: {dash(profile)}",
                       f"role: {dash(role)}", f"state: {dash(agent.get('presence'))}"]),
             f"scope: {dash(scope) if scope else 'whole board'}"]
    if res.get("inbox_new") is not None:
        lines.append(f"inbox: {res['inbox_new']} new")
    issues = []
    for k in ("working", "reviewing", "assigned"):
        for i in (res.get(k) or agent.get(k) or []):
            if isinstance(i, dict) and i.get("key") not in {x.get("key") for x in issues}:
                issues.append(i)
    if issues:
        lines.append(f"your issues ({len(issues)}):")
        lines += summary_rows(issues, style=style, day=day)
    else:
        lines.append("your issues: none")
    return "\n".join(lines)


# ---- check ----------------------------------------------------------------------------------

CHECK_LEASE_WARN_MIN = 15


def lease_left(minutes: Any) -> str:
    """Lease left: 1h40m, 2h, 12m; 'expired' at 0 or less."""
    m = int(minutes)
    if m <= 0:
        return "expired"
    h, m = divmod(m, 60)
    return f"{h}h{m}m" if h and m else (f"{h}h" if h else f"{m}m")


def _holding(h: dict) -> str:
    left = h.get("lease_left_min")
    key = str(h.get("key") or "?")
    if left is None:
        return key
    if left < CHECK_LEASE_WARN_MIN:
        return f"{key} (lease {lease_left(left)}, comment to renew)"
    return f"{key} (lease {lease_left(left)})"


def _check_since(since: Any, day: str) -> str:
    """'14:05:09Z' for today (UTC), '11-02 14:05:09Z' for an earlier day."""
    s = str(since)
    if len(s) >= 19 and s[10] == "T":
        return s[11:] if s[:10] == day else f"{s[5:10]} {s[11:]}"
    return s


def _arrow(c: dict) -> str:
    return f"{dash(c.get('old'))}→{dash(c.get('new'))}"


def _unblocked(c: dict) -> str:
    old = c.get("old")
    by = c.get("by_issue")
    if isinstance(old, str) and " " in old:  # the event's own 'GAME-31 done'
        return f"unblocked ({old})"
    if by:
        return f"unblocked ({by} {c.get('resolution') or 'done'})"
    return "unblocked"


CHECK_LINKS_SHOWN = 2


def check_links(links: list[dict]) -> str:
    """`+blocks GAME-5, -relates GAME-2`: each link added (+) or removed (-), as seen from the
    issue; past the first two, `…`."""
    words = [f"{'-' if ln.get('change') == 'removed' else '+'}"
             f"{str(ln.get('link') or 'link').replace('_', ' ')} {dash(ln.get('key'))}"
             for ln in links[:CHECK_LINKS_SHOWN]]
    return ", ".join(words + (["…"] if len(links) > CHECK_LINKS_SHOWN else []))


def check_change(c: dict) -> str:
    """One change of a checked issue, as few words as say what happened."""
    kind = c.get("kind")
    by = c.get("by")
    if kind == "assigned":
        return f"new for you (assigned by {by})" if by else "new for you"
    if kind == "created":
        return f"new issue ({by})" if by else "new issue"
    if kind == "handoff":
        return f"handoff ({by})" if by else "handoff"
    if kind == "stale":
        return f"stale claim ({by})" if by else "stale claim"
    if kind == "status":
        return f"status {_arrow(c)}"
    if kind in ("rejected", "approved"):
        return f"{kind} ({by})" if by else str(kind)
    if kind == "unblocked":
        return _unblocked(c)
    if kind == "review_cap":
        return "review rounds exhausted"
    if kind == "takeover":
        return f"taken over ({by})" if by else "taken over"
    if kind in ("field", "edit"):
        field = c.get("field") or "field"
        if field == "links" and c.get("links"):
            return check_links(c["links"])
        if field == "assignee" and "new" in c:
            return f"assigned to {c['new']}" if c["new"] else "unassigned"
        return f"{field} {_arrow(c)}" if field in ("priority", "status") else f"{field} changed"
    return str(kind or "changed").replace("_", " ")


def check_parts(item: dict) -> list[str]:
    parts: list[str] = []
    changes = item.get("changes") or []
    # what makes it yours (or new) leads the line, with how urgent it is
    leads = ("assigned", "created")
    rated = False
    for c in sorted(changes, key=lambda c: leads.index(c["kind"]) if c.get("kind") in leads else 2):
        parts.append(check_change(c))
        if c.get("kind") in leads and not rated:
            rated = True
            if item.get("priority"):
                parts.append(str(item["priority"]))
            if item.get("blocks_open"):
                parts.append(f"blocks:{item['blocks_open']}")
    comments, questions = item.get("comments") or 0, item.get("questions") or 0
    mentions = item.get("mentions") or 0
    calls = []  # a question addressed to you, then the other comments that @mention you
    if questions:
        calls.append("question for you" if questions == 1 else f"{questions} questions for you")
    if mentions:
        calls.append("mentions you" if mentions == 1 else f"{mentions} mention you")
    if comments:
        parts.append(", ".join([plural(comments, "new comment"), *calls]))
    elif calls:
        parts.append(", ".join(calls))
    if item.get("files"):
        parts.append(plural(item["files"], "new file"))
    return parts or ["changed"]


def check_counts(res: dict) -> list[str]:
    """The header's counts, by role: what `next --review` could claim for a reviewer
    (`reviews waiting: N`), else what `next` could claim (`ready for you: N`) and, when some of
    the actor's issues wait on open blockers, `M waiting on blockers`."""
    if res.get("role") == "reviewer" and res.get("reviews_waiting") is not None:
        return [f"reviews waiting: {res['reviews_waiting']}"]
    ready = res.get("ready_for_you", res.get("todo_for_you"))
    if ready is None:
        return []
    parts = [f"ready for you: {ready}"]
    if res.get("waiting_on_blockers"):
        parts.append(f"{res['waiting_on_blockers']} waiting on blockers")
    return parts


def render_check(res: dict, *, day: str | None = None) -> str:
    """`earmark check`: one header line (since when, what you hold, what `next` could claim),
    then one line per changed issue (`KEY  change · change`), then `+N more` when the board cut
    the list."""
    if not isinstance(res, dict):
        return dash(res)
    day = day or today()
    changed = res.get("changed") or []
    since = res.get("since")
    when = _check_since(since, day) if since else None
    if changed:
        head = [f"check: since {when}" if when else "check: changes"]
    else:
        head = [f"check: nothing new since {when}" if when else "check: nothing new"]
    holding = res.get("holding") or []
    if holding:
        head.append("holding " + ", ".join(_holding(h) for h in holding))
    head += check_counts(res)
    out = [SEP.join(head)]
    width = max((len(str(i.get("key") or "?")) for i in changed), default=0)
    for item in changed:
        key = str(item.get("key") or "?")
        out.append(f"{key:<{width}}  " + SEP.join(check_parts(item)))
    if res.get("more"):
        out.append(f"+{res['more']} more")
    return "\n".join(out)


TRIAGE_SECTIONS = (("backlog", "untriaged backlog"), ("unassigned", "unassigned todo"),
                   ("offline", "assigned to offline agents"),
                   ("stale", "stale claims"), ("review_cap", "exhausted reviews"),
                   ("review_queue", "review queue"),
                   ("blocked", "blocked"), ("overdue", "overdue"))
# the call that lists a whole section when triage cuts it
TRIAGE_MORE = {"backlog": "earmark ls -s backlog", "unassigned": "earmark ls -s todo -a none",
               "stale": "earmark ls --stale", "review_queue": "earmark ls -s in_review",
               "blocked": "earmark ls --blocked", "overdue": "earmark ls --overdue",
               "idle_agents": "earmark team"}
TRIAGE_WHEN_ANY = ("offline", "review_cap")  # in the counts line only when there are some
# columns every row of a section shares by definition
TRIAGE_FIXED = {"backlog": ("status",), "unassigned": ("status", "assignee"),
                "review_queue": ("status",), "review_cap": ("status",)}
TRIAGE_KEEP = ("key", "title", "flags")


def _varying(name: str, items: list[dict], fields: list[str]) -> list[str]:
    """Drop the columns that say the same thing on every row of a section."""
    out = []
    for f in fields:
        if f in TRIAGE_KEEP:
            out.append(f)
            continue
        if f in TRIAGE_FIXED.get(name, ()):
            continue
        values = {dash(LS_COLUMNS[f][1](i)) for i in items}
        if len(items) > 1 and len(values) == 1:
            continue
        out.append(f)
    return out


def _more(name: str, total: int, shown: int) -> list[str]:
    if total <= shown:
        return []
    call = TRIAGE_MORE.get(name) or f"earmark triage --limit {total}"  # no ls filter says "offline"
    return [f"+{total - shown} more: {call}"]


def render_triage(res: dict, *, style: Style = PLAIN, day: str | None = None,
                  now: float | None = None) -> str:
    """PM view: one counts line, then an aligned table per non-empty section (issues with the
    ls columns minus those constant in the section, review queue adds WAITING, stale claims add
    LEASE; idle agents like `team`). A section cut by the board's limit ends with
    `+N more: <the earmark ls call that lists it>`."""
    if not isinstance(res, dict):
        return dash(res)
    counts = res.get("counts") or {}
    head = [f"{label} {counts.get(name, len(res.get(name) or []))}"
            for name, label in TRIAGE_SECTIONS
            if name not in TRIAGE_WHEN_ANY or counts.get(name, len(res.get(name) or []))]
    head.append(f"idle agents {counts.get('idle_agents', len(res.get('idle_agents') or []))}")
    out = ["triage: " + SEP.join(head)]
    since = res.get("review_since") or {}
    base = [f for f in LS_DEFAULT]
    for name, label in TRIAGE_SECTIONS:
        items = res.get(name) or []
        if not items:
            continue
        total = counts.get(name, len(items))
        out += ["", f"{label} ({total}):"]
        fields = list(base)
        if name == "review_queue":
            items = [dict(i, waiting=ago(since.get(i.get("key")), now)) for i in items]
            fields.insert(fields.index("assignee") + 1, "waiting")
        elif name == "stale":
            fields.insert(fields.index("assignee") + 1, "lease_until")
        out += summary_rows(items, style=style, day=day, fields=_varying(name, items, fields))
        out += _more(name, total, len(items))
    idle = res.get("idle_agents") or []
    if idle:
        total = counts.get("idle_agents", len(idle))
        out += ["", f"idle agents ({total}):"]
        rows = [[dash(a.get("handle")), dash(a.get("role") or a.get("kind")), dash(a.get("profile")),
                 dash(a.get("cli")), ago(a.get("last_seen"), now), dash(a.get("presence"))] for a in idle]
        cols = [("HANDLE", style.bold), ("ROLE", None), ("PROFILE", None), ("CLI", None),
                ("SEEN", None), ("STATE", None)]
        out += table(cols, rows, style)
        out += _more("idle_agents", total, len(idle))
    if all(not res.get(n) for n, _ in TRIAGE_SECTIONS) and not idle:
        out.append("nothing to triage")
    return "\n".join(out)


def render_info(res: dict) -> str:
    """`earmark info`: where the board is, what it holds, who is on it."""
    if not isinstance(res, dict):
        return dash(res)
    lines = [f"board: {dash(res.get('path'))}"]
    head = [f"prefix: {dash(res.get('prefix'))}", f"owner: {dash(res.get('owner'))}"]
    if res.get("schema_version") is not None:
        head.append(f"schema: {res['schema_version']}")
    if res.get("created_at"):
        head.append(f"created: {res['created_at']}")
    lines.append(SEP.join(head))
    settings = res.get("settings")
    if isinstance(settings, dict) and settings:
        lines.append("settings: " + SEP.join(f"{k} {dash(v)}" for k, v in settings.items()))
    counts = res.get("counts")
    if isinstance(counts, dict):
        by = counts.get("by_status") or {}
        parts = [f"issues: {counts.get('total', sum(by.values()) if by else 0)} total, "
                 f"{counts.get('open', 0)} open"]
        parts += [f"{s} {n}" for s, n in by.items() if n]
        lines.append(SEP.join(parts))
    team = []
    agents = res.get("agents")
    if isinstance(agents, dict):
        team.append(f"agents: {agents.get('online', 0)} online of {agents.get('total', 0)}")
    elif isinstance(agents, list):
        team.append(f"agents online: {len(agents)}" + (f" ({_keys(agents)})" if agents else ""))
    elif agents is not None:
        team.append(f"agents online: {agents}")
    if isinstance(res.get("topics"), int):
        team.append(f"topics: {res['topics']}")
    if team:
        lines.append(SEP.join(team))
    files = res.get("files") or res.get("sizes")
    if isinstance(files, dict) and files:
        lines.append("files: " + SEP.join(f"{k} {human_size(v)}" for k, v in files.items()))
    versions = [f"{label} {res[k]}" for k, label in (("version", "earmark"), ("contract_version", "contract"),
                                                    ("sqlite_version", "sqlite")) if res.get(k)]
    if versions:
        lines.append("versions: " + SEP.join(versions))
    return "\n".join(lines)


WAIT_NEXT = {"work": "earmark next", "review": "earmark next --review"}


def render_wait(hit: Any) -> str:
    """What arrived, and the command that takes it (`wait: changes · 2 for you: earmark check`)."""
    if not isinstance(hit, dict):
        return f"wait: {dash(hit)}"
    if hit.get("reason") == "board":  # nothing open on the whole board: time to stop
        return f"wait: board{SEP}no open issues"
    reason = hit.get("reason") or "something arrived"
    parts = [f"wait: {reason}"]
    issue = hit.get("issue") if isinstance(hit.get("issue"), dict) else None
    key = (issue or {}).get("key") or (hit.get("key") if isinstance(hit.get("key"), str) else None)
    title = (issue or {}).get("title") or hit.get("title")
    if key:
        parts.append(f"{key} {title}" if title else key)
    if isinstance(hit.get("count"), int):
        parts.append(f"{hit['count']} for you: earmark check" if reason == "changes"
                     else f"count: {hit['count']}")
    for k, v in hit.items():
        if k in ("reason", "issue", "key", "title", "hint", "count") or isinstance(v, (dict, list)):
            continue
        parts.append(f"{k.replace('_', ' ')}: {dash(v)}")
    nxt = hit.get("hint") or WAIT_NEXT.get(reason)
    if nxt:
        parts.append(f"next: {nxt}")
    return SEP.join(parts)


def render_backup(res: Any, dest: str) -> str:
    if not isinstance(res, dict):
        return f"backup: {dest}"
    parts = [f"backup: {res.get('path') or dest}"]
    size = res.get("bytes") if "bytes" in res else res.get("size")
    if size is not None:
        parts.append(human_size(size))
    if "files" in res:
        parts.append(f"files: {res['files']} ({file_size(res.get('files_bytes') or 0)})")
    if res.get("missing"):
        parts.append(f"missing: {len(res['missing'])} (see earmark doctor)")
    return SEP.join(parts)


# ---- attachments --------------------------------------------------------------------------

def file_size(n: Any) -> str:
    """Attachment sizes: 512 B, 7.5 KB, 184 KB, 2.1 MB (one decimal under 10)."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return dash(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            if unit == "B":
                return f"{n:.0f} B"
            return f"{n:.1f} {unit}" if n < 10 else f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.0f} GB"


def render_attachments(items: list[dict], *, key: str | None = None, style: Style = PLAIN) -> str:
    """`earmark attachments KEY`: id, kind, size, name, author, comment, absolute path."""
    if not items:
        return f"attachments: none on {key}" if key else "attachments: none"
    rows = [[f"@{a.get('id')}", dash(a.get("kind")), file_size(a.get("size")), dash(a.get("name")),
             dash(a.get("author")), f"#{a['comment_id']}" if a.get("comment_id") else "-",
             dash(a.get("path"))] for a in items]
    cols = [("ID", style.bold), ("KIND", None), ("SIZE", None), ("NAME", None), ("AUTHOR", None),
            ("COMMENT", None), ("PATH", None)]
    return "\n".join(table(cols, rows, style))


def render_attachment(a: dict, *, style: Style = PLAIN) -> str:
    """`earmark attachment ID`: its fields, then the path to open."""
    g = "   "
    comment = f"#{a['comment_id']}" if a.get("comment_id") else "-"
    return "\n".join([
        f"{style.bold('@' + str(a.get('id')))}  {a.get('name') or ''}".rstrip(),
        g.join([f"issue: {dash(a.get('issue'))}", f"comment: {comment}",
                f"author: {dash(a.get('author'))}", f"created: {dash(a.get('created_at'))}"]),
        g.join([f"kind: {dash(a.get('kind'))}", f"mime: {dash(a.get('mime'))}",
                f"size: {file_size(a.get('size'))} ({dash(a.get('size'))} bytes)"]),
        f"sha256: {dash(a.get('sha256'))}",
        f"path: {dash(a.get('path'))}",
    ])


def render_need(res: dict, *, actor: str | None = None) -> str:
    """`need`: what was filed and where, then what happens to the issue that waits."""
    new, waiting = res.get("created") or {}, res.get("blocked") or {}
    first = [f"{new.get('key')} created in {dash(new.get('topic'))}"]
    if new.get("assignee"):
        first.append(f"assigned to {new['assignee']}")
    first.append(f"blocks {waiting.get('key')}")
    if waiting.get("status") == "blocked" and waiting.get("assignee") and waiting.get("assignee") == actor:
        second = (f"{waiting.get('key')} blocked, still yours{SEP}returns to your next when "
                  f"{new.get('key')} is done")
    else:
        second = f"{waiting.get('key')} {dash(waiting.get('status'))}{SEP}waits on {new.get('key')}"
    return "\n".join([SEP.join(first), second])


def render_saved(res: dict) -> str:
    a = res.get("attachment") or {}
    return SEP.join([f"@{a.get('id')} copied to {res.get('path')}", file_size(res.get("bytes"))])
