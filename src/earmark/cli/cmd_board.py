"""Board commands: init, info, wait, ui, export, backup, doctor, config."""

from __future__ import annotations

import os
import re
from typing import Any

from earmark.errors import Empty, InternalError, InvalidRequest, NoBoard

from earmark.cli import render as R
from earmark.cli.app import dump_json
from earmark.cli.common import Context, Result


def init_dir(db: str | None) -> str:
    """Project directory for `init`: cwd, --db DIR, or --db DIR/.earmark/earmark.db."""
    if not db:
        return os.getcwd()
    path = os.path.abspath(os.path.expanduser(db))
    if os.path.isdir(path) or not path.endswith(".db"):
        return path
    parent = os.path.dirname(path)
    if os.path.basename(path) == "earmark.db" and os.path.basename(parent) == ".earmark":
        return os.path.dirname(parent)
    raise InvalidRequest(f"init creates DIR/.earmark/earmark.db; '{db}' is not that path",
                         hint="earmark init --db DIR, or run earmark init inside the project")


def cmd_init(ctx: Context, a) -> Result:
    from earmark.board import Board
    directory = init_dir(ctx.db_flag())
    kw: dict[str, Any] = {"require_review": not a.no_review}
    if a.prefix:
        kw["prefix"] = a.prefix
    if a.owner:
        kw["owner"] = a.owner
    b = Board.init(directory, **kw)
    ctx._board = b
    ctx.prefix = getattr(b, "prefix", None)
    data = {"path": b.path, "prefix": b.prefix, "owner": b.owner, "require_review": not a.no_review}

    def text() -> str:
        return "\n".join([
            f"board: {b.path}",
            R.SEP.join([f"prefix: {b.prefix}", f"owner: {b.owner}",
                        "review required: " + ("yes" if not a.no_review else "no")]),
            f"next: earmark topic add PATH{R.SEP}earmark join worker --cli NAME",
        ])

    return Result(data, text)


def cmd_info(ctx: Context, a) -> Result:
    res = ctx.board().info()
    return Result(res, lambda: R.render_info(res))


def parse_duration(raw: str) -> float:
    """Seconds from '90', '90s', '9m', '1h', '1h30m'."""
    s = str(raw).strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return float(s)
    parts = re.findall(r"(\d+)([smhd])", s)
    if not parts or "".join(n + u for n, u in parts) != s:
        raise InvalidRequest(f"wait: bad --timeout '{raw}'", hint="e.g. 90s, 9m, 1h")
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return float(sum(int(n) * mult[u] for n, u in parts))


def cmd_wait(ctx: Context, a) -> Result:
    import time
    timeout = parse_duration(a.timeout)
    interval = max(0.05, a.interval)
    b = ctx.board()
    deadline = time.monotonic() + timeout
    version = b.data_version()
    hit = b.wait_check(a.mode)
    while not hit:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Empty(f"wait: nothing new in {a.timeout}{R.SEP}for: {a.mode}",
                        details={"mode": a.mode, "timeout_seconds": timeout})
        time.sleep(min(interval, remaining))
        current = b.data_version()
        if current != version:
            version = current
            hit = b.wait_check(a.mode)
    return Result(hit, lambda: R.render_wait(hit))


def cmd_ui(ctx: Context, a) -> Result:
    path = ctx.board().path
    ctx.close()
    from earmark.server import serve
    try:
        serve(path, host="127.0.0.1", port=a.port, open_browser=a.open)
    except KeyboardInterrupt:
        pass
    return Result(None, None)


def cmd_export(ctx: Context, a) -> Result:
    data = ctx.board().export()
    return Result(data, lambda: dump_json(data, pretty=True))


def cmd_backup(ctx: Context, a) -> Result:
    res = ctx.board().backup(a.path)
    return Result(res, lambda: R.render_backup(res, a.path))


def _basic_checks(detail: str) -> list[dict]:
    import sqlite3
    import sys
    return [
        {"name": "python", "ok": sys.version_info >= (3, 10), "detail": sys.version.split()[0]},
        {"name": "sqlite", "ok": True, "detail": sqlite3.sqlite_version},
        {"name": "board", "ok": False, "detail": detail},
    ]


def cmd_doctor(ctx: Context, a) -> Result:
    try:
        checks = ctx.board().doctor()
    except NoBoard as e:
        checks = _basic_checks(e.message + (f" ({e.hint})" if e.hint else ""))
    bad = [c for c in checks if not c.get("ok")]
    if bad and ctx.json:
        raise InternalError(f"doctor: {len(bad)} of {len(checks)} checks failed: "
                            + ", ".join(str(c.get("name")) for c in bad), details=checks)
    return Result(checks, lambda: R.render_doctor(checks), code=1 if bad else 0)


def cmd_config(ctx: Context, a) -> Result:
    res = ctx.board().config(a.key, a.value)
    return Result(res, lambda: R.render_kv(res) if isinstance(res, dict) else R.dash(res))
