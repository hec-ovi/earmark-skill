"""Run one command: parse, resolve identity, call the board, print text or the JSON envelope.

Output contract (docs/ARCHITECTURE.md): text on stdout, plain unless stdout is a terminal and
NO_COLOR is unset; `--json` prints the envelope to stdout for success and for errors; text-mode
errors go to stderr as `error:` / `hint:`; `empty` is an answer, so its line goes to stdout.
Exit codes come from earmark.errors.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable

from earmark.errors import EarmarkError, InternalError

from earmark.cli.render import PLAIN, Style


class Result:
    """What a command hands back: JSON data plus a lazily rendered text form."""

    __slots__ = ("data", "text", "code", "stream")

    def __init__(self, data: Any, text: str | Callable[[], str] | None = None, *, code: int = 0,
                 stream: str = "stdout") -> None:
        self.data = data
        self.text = text
        self.code = code
        self.stream = stream


class Context:
    """Per-invocation state shared by command handlers."""

    def __init__(self, ns: Any, command: str) -> None:
        self.ns = ns
        self.command = command
        self.stdout = sys.stdout
        self.stdin = sys.stdin
        self.env = os.environ
        self.json = bool(getattr(ns, "json", False))
        self.tty = _isatty(self.stdout)
        color = self.tty and not self.env.get("NO_COLOR") and self.env.get("TERM") != "dumb"
        self.style = Style(True, "…") if color else PLAIN
        self.width = _term_width() if self.tty else None
        self._board = None
        self.actor: str | None = None
        self.prefix: str | None = None

    # ---- identity and board ------------------------------------------------------------
    def actor_flag(self) -> str | None:
        """--as, then EARMARK_AS (empty means unset)."""
        return getattr(self.ns, "actor", None) or self.env.get("EARMARK_AS") or None

    def db_flag(self) -> str | None:
        return getattr(self.ns, "db", None) or self.env.get("EARMARK_DB") or None

    def board(self, *, identity: bool = True):
        """Open the board once. identity=False skips actor resolution and touch (join)."""
        if self._board is not None:
            return self._board
        from earmark.board import Board
        actor = self.actor_flag() if identity else None
        b = Board.open(self.db_flag(), actor)
        self._board = b
        self.prefix = getattr(b, "prefix", None)
        if identity:
            if actor is None and _isatty(self.stdin) and self.tty:
                actor = getattr(b, "owner", None)
                if actor:
                    b.set_actor(actor)
            self.actor = actor
            if actor:
                b.touch()
        return b

    def close(self) -> None:
        if self._board is not None:
            try:
                self._board.close()
            except Exception:
                pass
            self._board = None

    # ---- input -------------------------------------------------------------------------
    def read_stdin(self, what: str) -> str:
        if _isatty(self.stdin):
            from earmark.errors import InvalidRequest
            raise InvalidRequest(f"{what}: '-' reads stdin, but stdin is a terminal",
                                 hint="pipe the text in, or pass it as an argument")
        return self.stdin.read()

    def read_text(self, value: str | None, file: str | None, what: str) -> str | None:
        """Inline text, `-` (stdin) or --*-file F (F may be '-'). Trailing whitespace trimmed."""
        if file is not None:
            text = self.read_stdin(what) if file == "-" else _read_file(file, what)
        elif value == "-":
            text = self.read_stdin(what)
        else:
            return value
        return text.rstrip()


def _read_file(path: str, what: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        from earmark.errors import InvalidRequest
        raise InvalidRequest(f"{what}: cannot read {path}: {e.strerror or e}") from None


def _isatty(stream) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def _term_width() -> int:
    import shutil
    return shutil.get_terminal_size((100, 24)).columns


# ---- envelope ---------------------------------------------------------------------------

def envelope(command: str, *, data: Any = None, error: dict | None = None,
             board: str | None = None, actor: str | None = None) -> dict:
    from earmark import CONTRACT_VERSION
    return {
        "contract_version": CONTRACT_VERSION,
        "ok": error is None,
        "data": data if error is None else None,
        "error": error,
        "meta": {"command": command, "board": board, "actor": actor},
    }


def dump_json(obj: Any, pretty: bool) -> str:
    import json
    if pretty:
        return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)


# ---- run --------------------------------------------------------------------------------

def _fix_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if enc != "utf8" and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def _write(stream, text: str) -> None:
    if not text:
        return
    stream.write(text if text.endswith("\n") else text + "\n")


def handler_for(command: str) -> Callable:
    """cmd_<name> from the family module (cmd_read, cmd_work, ...), imported on demand."""
    from earmark.cli.parser import COMMANDS
    group = COMMANDS[command.split()[0]][0].lower()
    module = __import__(f"earmark.cli.cmd_{group}", fromlist=["cmd"])
    return getattr(module, "cmd_" + command.replace(" ", "_"))


def run(argv: list[str] | None = None) -> int:
    from earmark.cli import parser as P

    argv = list(sys.argv[1:] if argv is None else argv)
    _fix_encoding()
    json_mode = P.wants_json(argv)
    ctx: Context | None = None
    command = "earmark"
    try:
        try:
            command, ns = P.parse(argv)
        except P.ParseExit as e:
            return e.status
        ctx = Context(ns, command)
        result = handler_for(command)(ctx, ns)
        if ctx.json:
            _write(sys.stdout, dump_json(envelope(command, data=result.data, board=ctx.prefix,
                                                  actor=ctx.actor), ctx.tty))
        else:
            text = result.text() if callable(result.text) else result.text
            if text:
                _write(sys.stdout if result.stream == "stdout" else sys.stderr, text)
        return result.code
    except EarmarkError as e:
        return _fail(e, command, ctx, json_mode)
    except NotImplementedError as e:
        err = InternalError(f"not implemented yet: {e}" if str(e) else "not implemented yet",
                            hint="the board service is still being built")
        return _fail(err, command, ctx, json_mode)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError):
            pass
        return 0
    except Exception as e:  # internal bug: still answer with the contract
        if os.environ.get("EARMARK_DEBUG"):
            raise
        err = InternalError(f"{type(e).__name__}: {e}",
                            hint="rerun with EARMARK_DEBUG=1 for the traceback")
        return _fail(err, command, ctx, json_mode)
    finally:
        if ctx is not None:
            ctx.close()


def _fail(e: EarmarkError, command: str, ctx: Context | None, json_mode: bool) -> int:
    if ctx is not None:
        json_mode = ctx.json
    board = ctx.prefix if ctx else None
    actor = ctx.actor if ctx else None
    if json_mode:
        _write(sys.stdout, dump_json(envelope(command, error=e.to_dict(), board=board, actor=actor),
                                     bool(ctx and ctx.tty)))
        return e.exit_code
    if e.code == "empty":
        from earmark.cli.common import empty_text
        _write(sys.stdout, empty_text(command, e, ctx))
        return e.exit_code
    color = _isatty(sys.stderr) and not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"
    style = Style(True) if color else PLAIN
    lines = [f"{style.paint('error:', '1;31')} {e.message}"]
    if e.hint:
        lines.append(f"{style.dim('hint:')} {e.hint}")
    _write(sys.stderr, "\n".join(lines))
    return e.exit_code
