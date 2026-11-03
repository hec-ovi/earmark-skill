"""Command grammar (skills/earmark/references/commands.md).

Only the subparser of the invoked command is built, so startup stays flat as the command set
grows; the top-level help is a fixed text grouped by area. Global flags (--as, --db, --json)
are accepted before or after the subcommand. Parse errors raise InvalidRequest (exit 1)
instead of argparse's exit 2, which means not_found here.
"""

from __future__ import annotations

import argparse
import sys
TYPE_CHECKING = False
if TYPE_CHECKING:  # annotations only; typing stays off the startup path
    from typing import Callable

from earmark.errors import InvalidRequest

GROUPS = ("Read", "Work", "Write", "Files", "Topics", "Team", "Board")

# name -> (group, one-line description)
COMMANDS: dict[str, tuple[str, str]] = {
    "topics": ("Read", "topic tree with open counts"),
    "ls": ("Read", "list issues, one line each"),
    "show": ("Read", "one issue: fields, description, comment count, last comment id"),
    "comments": ("Read", "comments on an issue, oldest first"),
    "inbox": ("Read", "new comments for you across issues; advances your cursor"),
    "next": ("Work", "pick and claim the best issue in your scope (exit 4 when none)"),
    "claim": ("Work", "claim one issue, or renew your lease on it"),
    "release": ("Work", "give an issue back: unassigned todo"),
    "review": ("Work", "hand off to review; NOTE is posted as the handoff comment"),
    "approve": ("Work", "reviewer: in_review -> done with an approval comment"),
    "reject": ("Work", "reviewer: in_review -> todo for the same assignee"),
    "done": ("Work", "close directly (owner, pm, or boards without required review)"),
    "move": ("Work", "any allowed status transition"),
    "reopen": ("Work", "done or canceled -> todo"),
    "cancel": ("Work", "-> canceled"),
    "new": ("Write", "create an issue; prints the new key"),
    "edit": ("Write", "change only the given fields"),
    "comment": ("Write", "add a comment, or --edit one of yours; prints #id"),
    "link": ("Write", "add a dependency: blocks, duplicates or relates"),
    "unlink": ("Write", "remove a dependency"),
    "assign": ("Write", "set or clear the assignee (owner or pm)"),
    "rank": ("Write", "manual order: --before, --after, --top or --bottom"),
    "delete": ("Write", "delete an issue for good (owner; needs --yes; prefer cancel)"),
    "attach": ("Files", "attach files to an issue or one of its comments; prints @id per file"),
    "attachments": ("Files", "an issue's files: id, kind, size, name, author, comment, path"),
    "attachment": ("Files", "one attachment's fields and path, or copy it with --out"),
    "detach": ("Files", "remove an attachment (its author, the owner or a pm)"),
    "topic": ("Topics", "add, edit, mv or rm a topic"),
    "join": ("Team", "register as a profile and print your briefing"),
    "leave": ("Team", "go offline and return your work to the queue"),
    "whoami": ("Team", "your handle, profile, role, scope and issues"),
    "team": ("Team", "every agent: role, profile, cli, presence, current issues"),
    "profiles": ("Team", "list profiles"),
    "profile": ("Team", "show, add, edit or rm a profile"),
    "triage": ("Team", "pm view: what needs triage, assignment or attention"),
    "init": ("Board", "create .earmark/earmark.db here and seed profiles"),
    "info": ("Board", "board path, prefix, counts by status, agents online, sizes"),
    "wait": ("Board", "block read-only until work, a review or inbox arrives"),
    "ui": ("Board", "web UI on 127.0.0.1"),
    "export": ("Board", "JSON dump of the board to stdout"),
    "backup": ("Board", "consistent copy: DIR/earmark.db and DIR/files/"),
    "doctor": ("Board", "Python, SQLite, WAL, schema, board location and attachment file checks"),
    "config": ("Board", "read or set require_review, lease_minutes, max_attachment_mb, prefix"),
}
SUBCOMMANDS = {"topic": ("add", "edit", "mv", "rm"), "profile": ("show", "add", "edit", "rm")}

TOP_HELP = """\
earmark: local issue board for teams of coding agents

usage: earmark [--as HANDLE] [--db PATH] [--json] <command> [args]

  Read    topics  ls  show  comments  inbox
  Work    next  claim  release  review  approve  reject  done  move  reopen  cancel
  Write   new  edit  comment  link  unlink  assign  rank  delete
  Files   attach  attachments  attachment  detach
  Topics  topic add | edit | mv | rm
  Team    join  leave  whoami  team  profiles  profile show | add | edit | rm  triage
  Board   init  info  wait  ui  export  backup  doctor  config

examples:
  earmark join worker --cli claude-code       register, print your handle and briefing
  earmark next --as worker-1                  claim the best ready issue in your scope
  earmark ls -t ui-ux --ready                 open issues under a topic, no open blockers
  earmark comments GAME-42 --new --as worker-1
  earmark review GAME-42 -m "what changed, how to verify" --as worker-1

global: --as HANDLE (or EARMARK_AS) · --db PATH (or EARMARK_DB) · --json (envelope)
keys: GAME-42, game-42 or 42 · dates: 2026-11-07, today, tomorrow, +3d, +2w
exit: 0 ok · 1 invalid · 2 not found · 3 conflict · 4 empty · 5 busy
run `earmark <command> -h` for its flags
"""

STATUSES = ("backlog", "todo", "in_progress", "in_review", "blocked", "done", "canceled")
SORTS = ("priority", "rank", "updated", "created", "due", "key")
LINK_KINDS = ("blocks", "duplicates", "relates")
WAIT_MODES = ("work", "review", "inbox", "any")


class ParseExit(Exception):
    """argparse asked to exit (help printed): carries the exit status."""

    def __init__(self, status: int = 0):
        super().__init__(status)
        self.status = status


class _NoTheme:
    """Stands in for 3.14's color theme: every style is the empty string."""

    def __getattr__(self, name: str) -> str:
        return ""


def _identity(text: str) -> str:
    return text


class _Formatter(argparse.HelpFormatter):
    """Fixed width (no terminal probe) and plain help. On 3.14 argparse builds a formatter while
    adding arguments and its color setup imports _colorize, which pulls in dataclasses and
    inspect (~15 ms); skipping it keeps `earmark ls` startup flat."""

    def __init__(self, prog: str, *args, **kwargs) -> None:
        kwargs.setdefault("max_help_position", 30)
        kwargs["width"] = 100
        super().__init__(prog, *args, **kwargs)

    def _set_color(self, color) -> None:  # 3.14+
        self._theme = _NoTheme()
        self._decolor = _identity


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        kwargs.setdefault("formatter_class", _Formatter)
        if sys.version_info >= (3, 14):
            kwargs.setdefault("suggest_on_error", True)
        super().__init__(*args, **kwargs)

    def error(self, message: str):  # type: ignore[override]
        raise InvalidRequest(f"{self.prog}: {message}", hint=f"{self.prog} -h")

    def exit(self, status: int = 0, message: str | None = None):  # type: ignore[override]
        if message:
            sys.stderr.write(message)
        raise ParseExit(status)


# ---- global flags ----------------------------------------------------------------------

def _globals(p: argparse.ArgumentParser, suppress_help: bool = False) -> None:
    S = argparse.SUPPRESS
    p.add_argument("--as", dest="actor", metavar="HANDLE", default=S,
                   help=S if suppress_help else "act as HANDLE (or EARMARK_AS)")
    p.add_argument("--db", metavar="PATH", default=S,
                   help=S if suppress_help else "board file or directory (or EARMARK_DB)")
    p.add_argument("--json", action="store_true", default=S,
                   help=S if suppress_help else "print the JSON envelope")


def scan(argv: list[str]) -> tuple[str | None, str | None]:
    """(command, subcommand) from argv without parsing: global flags and their values skipped."""
    i, n = 0, len(argv)
    cmd = None
    while i < n:
        tok = argv[i]
        if tok == "--":
            i += 1
            if i < n and cmd is None:
                return argv[i], None
            break
        if tok in ("--as", "--db"):
            i += 2
            continue
        if tok.startswith("-") and tok != "-":
            i += 1
            continue
        if cmd is None:
            cmd = tok
            if cmd not in SUBCOMMANDS:
                return cmd, None
        else:
            return cmd, tok
        i += 1
    return cmd, None


def wants_json(argv: list[str]) -> bool:
    for tok in argv:
        if tok == "--":
            return False
        if tok == "--json":
            return True
    return False


def normalize(argv: list[str]) -> list[str]:
    """`-l -LABEL` (remove a label) would read as an option: join it as `--label=-LABEL`."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            out.extend(argv[i:])
            break
        if tok in ("-l", "--label") and i + 1 < len(argv):
            nxt = argv[i + 1]
            if nxt.startswith("-") and len(nxt) > 1 and nxt != "--":
                out.append("--label=" + nxt)
                i += 2
                continue
        out.append(tok)
        i += 1
    return out


# ---- shared argument groups ------------------------------------------------------------

def _key(p, name: str = "key", help: str = "issue key: GAME-42, game-42 or 42") -> None:
    p.add_argument(name, metavar=name.upper(), help=help)


def _note(p, required: bool = False, help: str = "comment posted with the move ('-' reads stdin)") -> None:
    p.add_argument("-m", "--note", metavar="NOTE", required=required, help=help)


def _body(p) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument("-b", "--body", metavar="BODY", help="description text")
    g.add_argument("--body-file", metavar="F", help="read the description from F ('-' for stdin)")
    return g


# ---- per-command builders --------------------------------------------------------------

def b_topics(p):
    p.add_argument("path", nargs="?", metavar="PATH", help="subtree root")
    p.add_argument("--depth", type=int, metavar="N", help="levels to show (1: only the top)")
    p.add_argument("--all", action="store_true", help="include archived topics")


def b_ls(p):
    p.add_argument("-t", "--topic", action="append", default=[], metavar="PATH",
                   help="topic and its subtree; repeatable")
    p.add_argument("--exact", action="store_true", help="topic node only, not its subtree")
    p.add_argument("-s", "--status", metavar="LIST",
                   help="statuses, or open (default), active, closed, all")
    p.add_argument("-p", "--priority", metavar="LIST", help="e.g. urgent,high")
    p.add_argument("--min-priority", metavar="P", help="P or more urgent")
    p.add_argument("-a", "--assignee", metavar="H", help="handle, me or none")
    p.add_argument("--reviewer", metavar="H", help="handle, me or none")
    p.add_argument("--reporter", metavar="H", help="handle or me")
    p.add_argument("--type", metavar="LIST", help="bug, feature, task, chore")
    p.add_argument("-l", "--label", action="append", default=[], metavar="L",
                   help="label; repeatable, all must match")
    p.add_argument("--due-before", metavar="DATE", help="due before DATE")
    p.add_argument("--overdue", action="store_true", help="past due and not closed")
    p.add_argument("--updated-since", metavar="WHEN", help="30m, 2h, 3d, 1w or a date")
    p.add_argument("--created-since", metavar="WHEN", help="30m, 2h, 3d, 1w or a date")
    p.add_argument("--ready", action="store_true", help="no open blockers")
    p.add_argument("--blocked", action="store_true", help="has open blockers")
    p.add_argument("--stale", action="store_true", help="expired lease")
    p.add_argument("-q", metavar="TEXT", help="substring of title or description")
    p.add_argument("--parent", metavar="KEY", help="sub-issues of KEY")
    p.add_argument("--sort", choices=SORTS, default="priority", metavar="KEY",
                   help="priority (default: priority, rank), rank, updated, created, due, key")
    p.add_argument("--desc", action="store_true", help="reverse the sort")
    p.add_argument("--limit", type=int, default=50, metavar="N", help="default 50")
    p.add_argument("--offset", type=int, default=0, metavar="N", help="from the footer's next:")
    p.add_argument("--fields", metavar="LIST",
                   help="columns, e.g. key,status,title,flags (also reviewer, labels, due, updated...)")
    p.add_argument("--count", action="store_true", help="print the count only")


def b_show(p):
    _key(p)
    p.add_argument("-f", "--fields", metavar="FIELDS", help="print only these `field: value` lines")
    p.add_argument("-c", "--comments", nargs="?", type=int, const=5, metavar="N",
                   help="append the last N comments (default 5)")


def b_comments(p):
    _key(p)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--since", metavar="ID", help="everything after comment ID")
    g.add_argument("--new", action="store_true", help="after your cursor, then advance it")
    p.add_argument("--last", type=int, metavar="N", help="the latest N")
    p.add_argument("--limit", type=int, default=100, metavar="N", help="cap (default 100)")


def b_inbox(p):
    p.add_argument("--peek", action="store_true", help="do not advance your inbox cursor")
    p.add_argument("--limit", type=int, default=50, metavar="N", help="cap (default 50)")


def b_next(p):
    p.add_argument("--review", action="store_true", help="review mode (default for reviewers)")
    p.add_argument("-t", "--topic", action="append", default=[], metavar="PATH",
                   help="only inside PATH; repeatable")
    p.add_argument("--dry-run", action="store_true", help="show the pick without claiming it")


def b_claim(p):
    _key(p)


def b_release(p):
    _key(p)
    _note(p, help="why ('-' reads stdin)")


def b_review(p):
    _key(p)
    _note(p, required=True, help="handoff: what changed, files, how to verify ('-' reads stdin)")


def b_approve(p):
    _key(p)
    _note(p, help="what you checked ('-' reads stdin)")


def b_reject(p):
    _key(p)
    _note(p, required=True, help="each defect: file:line, expected, actual ('-' reads stdin)")


def b_done(p):
    _key(p)
    _note(p)


def b_move(p):
    _key(p)
    p.add_argument("status", metavar="STATUS", help=", ".join(STATUSES))
    _note(p)


def b_reopen(p):
    _key(p)
    _note(p, required=True, help="why it reopens ('-' reads stdin)")


def b_cancel(p):
    _key(p)
    _note(p, required=True, help="why ('-' reads stdin)")


def b_new(p):
    p.add_argument("title", metavar="TITLE")
    _body(p)
    p.add_argument("-t", "--topic", metavar="PATH", help="existing topic path")
    p.add_argument("-p", "--priority", metavar="PRIO", help="urgent, high, medium (default), low, none")
    p.add_argument("--type", metavar="T", help="bug, feature, task (default), chore")
    p.add_argument("-l", "--label", action="append", default=[], metavar="LABEL", help="repeatable")
    p.add_argument("--due", metavar="DATE", help="2026-11-07, today, tomorrow, +3d, +2w")
    p.add_argument("--parent", metavar="KEY", help="make it a sub-issue of KEY")
    p.add_argument("--assign", metavar="H", help="assignee handle or me")
    p.add_argument("--status", metavar="S", help="todo (default) or backlog")


def b_edit(p):
    _key(p)
    p.add_argument("--title", metavar="T", help="new title")
    g = _body(p)
    g.add_argument("--append", metavar="TEXT", help="append to the description")
    t = p.add_mutually_exclusive_group()
    t.add_argument("-t", "--topic", metavar="PATH", help="move to an existing topic")
    t.add_argument("--no-topic", action="store_true", help="clear the topic")
    p.add_argument("-p", "--priority", metavar="PRIO", help="urgent, high, medium, low, none")
    p.add_argument("--type", metavar="T", help="bug, feature, task, chore")
    d = p.add_mutually_exclusive_group()
    d.add_argument("--due", metavar="DATE", help="2026-11-07, today, tomorrow, +3d, +2w")
    d.add_argument("--no-due", action="store_true", help="clear the due date")
    p.add_argument("-l", "--label", action="append", default=[], metavar="+L|-L",
                   help="add (+L or L) or remove (-L) a label; repeatable")
    r = p.add_mutually_exclusive_group()
    r.add_argument("--parent", metavar="KEY", help="make it a sub-issue of KEY")
    r.add_argument("--no-parent", action="store_true", help="clear the parent")
    p.add_argument("--if-version", type=int, metavar="N", help="fail with exit 3 if the issue moved on")


def b_comment(p):
    p.add_argument("key", nargs="?", metavar="KEY", help="issue key (omit with --edit)")
    p.add_argument("text", nargs="?", metavar="TEXT", help="comment body; '-' reads stdin")
    p.add_argument("--body-file", metavar="F", help="read the body from F ('-' for stdin)")
    p.add_argument("--kind", choices=("note", "progress", "question"), default=None, metavar="K",
                   help="note (default), progress, question")
    p.add_argument("--reply-to", metavar="ID", help="comment id this answers")
    p.add_argument("--edit", metavar="ID", help="replace the body of your comment ID")
    p.add_argument("--attach", action="append", default=[], metavar="FILE",
                   help="attach FILE to the new comment; repeatable")


def b_link(p):
    _key(p)
    p.add_argument("kind", choices=LINK_KINDS, metavar="KIND", help="blocks, duplicates or relates")
    p.add_argument("target", metavar="KEY2", help="the other issue")


b_unlink = b_link


def b_assign(p):
    _key(p)
    p.add_argument("handle", metavar="HANDLE", help="handle, me, or none to clear")


def b_rank(p):
    _key(p)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--before", metavar="KEY2", help="just above KEY2")
    g.add_argument("--after", metavar="KEY2", help="just below KEY2")
    g.add_argument("--top", action="store_true", help="first in rank order")
    g.add_argument("--bottom", action="store_true", help="last in rank order")


def b_delete(p):
    _key(p)
    p.add_argument("--yes", action="store_true", help="required: deletion cannot be undone")


def b_attach(p):
    _key(p)
    p.add_argument("files", nargs="+", metavar="FILE", help="files to attach")
    p.add_argument("--comment", metavar="ID", help="attach to comment ID of this issue")


def b_attachments(p):
    _key(p)


def _attachment_id(p):
    p.add_argument("id", metavar="ID", help="attachment id: @12 or 12")


def b_attachment(p):
    _attachment_id(p)
    p.add_argument("--out", metavar="DEST", help="copy the file to DEST (a new file, or a directory)")


b_detach = _attachment_id


def b_topic(p):
    sub = p.add_subparsers(dest="sub", metavar="add|edit|mv|rm", parser_class=Parser)
    sub.required = True

    def topic_fields(q):
        q.add_argument("--name", metavar="N", help="display name")
        q.add_argument("--icon", metavar="I", help="icon name")
        q.add_argument("--description", metavar="D", help="one line on what lives here")

    a = _sub(sub, "add", "create a topic (missing ancestors too)")
    a.add_argument("path", metavar="PATH", help="e.g. ui-ux/video-calls")
    topic_fields(a)
    e = _sub(sub, "edit", "change a topic's name, icon or description")
    e.add_argument("path", metavar="PATH", help="topic path")
    topic_fields(e)
    m = _sub(sub, "mv", "move or rename a topic and its subtree")
    m.add_argument("path", metavar="PATH", help="topic path")
    m.add_argument("new_path", metavar="NEW_PATH", help="where it goes, with its subtree")
    r = _sub(sub, "rm", "remove an empty topic, or archive it")
    r.add_argument("path", metavar="PATH", help="topic path")
    r.add_argument("--archive", action="store_true", help="archive instead (keeps its issues)")


def b_join(p):
    p.add_argument("profile", metavar="PROFILE", help="worker, reviewer, pm or a custom profile")
    p.add_argument("--cli", metavar="NAME", help="claude-code, codex, grok, agy, opencode")


def b_leave(p):
    p.add_argument("--keep", action="store_true", help="keep your claims and assignments")


def b_none(p):
    pass


b_whoami = b_team = b_profiles = b_triage = b_info = b_export = b_none


def b_doctor(p):
    p.add_argument("--fix", action="store_true", help="remove attachment files no row references")


def b_profile(p):
    sub = p.add_subparsers(dest="sub", metavar="show|add|edit|rm", parser_class=Parser)
    sub.required = True

    def prompt_args(q):
        g = q.add_mutually_exclusive_group()
        g.add_argument("--prompt", metavar="T", help="briefing text printed by join")
        g.add_argument("--prompt-file", metavar="F", help="read the prompt from F ('-' for stdin)")
        q.add_argument("-t", "--topic", action="append", default=[], metavar="PATH",
                       help="scope subtree; repeatable (none = whole board)")
        q.add_argument("--icon", metavar="I", help="icon name")

    s = _sub(sub, "show", "full prompt and scope")
    s.add_argument("slug", metavar="SLUG")
    a = _sub(sub, "add", "create a profile (owner or pm)")
    a.add_argument("slug", metavar="SLUG")
    a.add_argument("--role", required=True, choices=("worker", "reviewer", "pm"), metavar="R",
                   help="worker, reviewer or pm")
    a.add_argument("--name", required=True, metavar="N", help="display name")
    prompt_args(a)
    e = _sub(sub, "edit", "change a profile (owner or pm)")
    e.add_argument("slug", metavar="SLUG")
    e.add_argument("--role", choices=("worker", "reviewer", "pm"), metavar="R",
                   help="worker, reviewer or pm")
    e.add_argument("--name", metavar="N", help="display name")
    prompt_args(e)
    e.add_argument("--whole-board", action="store_true", help="clear the scope")
    e.add_argument("--if-version", type=int, metavar="N", help="fail with exit 3 if it moved on")
    r = _sub(sub, "rm", "remove a profile (owner or pm)")
    r.add_argument("slug", metavar="SLUG")


def b_init(p):
    p.add_argument("--prefix", metavar="GAME", help="issue key prefix")
    p.add_argument("--owner", metavar="HANDLE", help="board owner (a person)")
    p.add_argument("--no-review", action="store_true", help="allow done without review")


def b_wait(p):
    p.add_argument("--for", dest="mode", choices=WAIT_MODES, default="any", metavar="MODE",
                   help="work, review, inbox or any (default)")
    p.add_argument("--timeout", default="9m", metavar="T", help="e.g. 90s, 9m (default), 1h")
    p.add_argument("--interval", type=float, default=2.0, help=argparse.SUPPRESS)


def b_ui(p):
    p.add_argument("--port", type=int, default=7477, metavar="N", help="default 7477")
    p.add_argument("--open", action="store_true", help="open the browser")


def b_backup(p):
    p.add_argument("path", metavar="DIR", help="destination directory (created when missing)")


def b_config(p):
    p.add_argument("key", nargs="?", metavar="KEY",
                   help="require_review, lease_minutes, max_attachment_mb, prefix")
    p.add_argument("value", nargs="?", metavar="VALUE")


GLOBAL_EPILOG = "global: --as HANDLE (or EARMARK_AS) · --db PATH (or EARMARK_DB) · --json"


def _sub(sub, name: str, desc: str) -> Parser:
    q = sub.add_parser(name, help=desc, description=desc, formatter_class=_Formatter,
                       epilog=GLOBAL_EPILOG)
    _globals(q, suppress_help=True)
    return q


# ---- entry -----------------------------------------------------------------------------

def print_top_help(file=None) -> None:
    (file or sys.stdout).write(TOP_HELP)


def suggest(word: str) -> str | None:
    import difflib
    close = difflib.get_close_matches(word, list(COMMANDS), n=3, cutoff=0.5)
    return ("did you mean: " + ", ".join(f"earmark {c}" for c in close)) if close else None


def parse(argv: list[str]) -> tuple[str, argparse.Namespace]:
    """Return (command name for meta, namespace). Raises InvalidRequest or ParseExit."""
    cmd, _sub_name = scan(argv)
    if cmd is None:
        asked = any(a in ("-h", "--help", "--version") for a in argv)
        if wants_json(argv) and not asked:
            raise InvalidRequest("no command given", hint="earmark -h")
        if "--version" in argv:
            from earmark import __version__
            sys.stdout.write(f"earmark {__version__}\n")
            raise ParseExit(0)
        print_top_help()
        raise ParseExit(0)
    if cmd not in COMMANDS:
        raise InvalidRequest(f"unknown command '{cmd}'", hint=suggest(cmd) or "earmark -h")
    root = Parser(prog="earmark", add_help=False)
    root.add_argument("-h", "--help", action="store_true", default=False)
    root.add_argument("--version", action="store_true", default=False)
    _globals(root)
    subs = root.add_subparsers(dest="command", parser_class=Parser)
    p = subs.add_parser(cmd, prog=f"earmark {cmd}", description=COMMANDS[cmd][1],
                        formatter_class=_Formatter,
                        epilog=None if cmd in SUBCOMMANDS else GLOBAL_EPILOG)
    _globals(p, suppress_help=True)
    builder: Callable = globals()["b_" + cmd]
    builder(p)
    args = normalize(argv)
    # `earmark -h ls` asks for the top-level help, `earmark ls -h` for the command's
    idx = args.index(cmd) if cmd in args else len(args)
    if any(a in ("-h", "--help") for a in args[:idx]):
        print_top_help()
        raise ParseExit(0)
    ns = root.parse_args(args)
    name = cmd
    if cmd in SUBCOMMANDS:
        name = f"{cmd} {ns.sub}"
    return name, ns
