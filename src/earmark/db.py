"""Board discovery, connections, pragmas, migrations and write transactions.

Every write goes through `write(conn)`: one BEGIN IMMEDIATE … COMMIT. Reads run in
autocommit mode (or a deferred snapshot) and never take the write lock.
"""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Callable, Iterator

from earmark.errors import Busy, InternalError, InvalidRequest, NoBoard

DIR_NAME = ".earmark"
DB_NAME = "earmark.db"
SCHEMA_VERSION = 1
MIN_SQLITE = (3, 35, 0)
BUSY_TIMEOUT_MS = 10000
WRITE_RETRIES = 3
GIT_TIMEOUT_S = 2.0

PRAGMAS = (
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA journal_size_limit=8388608",
)


# ---- discovery --------------------------------------------------------------------------

def find_board(db: str | None = None, *, cwd: str | None = None,
               env: dict | None = None) -> str:
    """Return the board file path. First hit wins: flag, EARMARK_DB, walk up, git main checkout."""
    env = os.environ if env is None else env
    start = os.path.abspath(cwd or os.getcwd())
    if db:
        return _explicit(db, start, "--db")
    if env.get("EARMARK_DB"):
        return _explicit(env["EARMARK_DB"], start, "EARMARK_DB")
    found = _walk_up(start)
    if found:
        return found
    main = git_main_checkout(start)
    if main:
        candidate = os.path.join(main, DIR_NAME, DB_NAME)
        if os.path.isfile(candidate):
            return candidate
    raise NoBoard(f"no earmark board found from {start}", hint="earmark init")


def _explicit(value: str, start: str, source: str) -> str:
    path = os.path.abspath(os.path.join(start, os.path.expanduser(value)))
    if os.path.isdir(path):
        for candidate in (os.path.join(path, DIR_NAME, DB_NAME), os.path.join(path, DB_NAME)):
            if os.path.isfile(candidate):
                return candidate
        raise NoBoard(f"{source} points to {path}, which holds no {DIR_NAME}/{DB_NAME}",
                      hint=f"earmark init  (in {path})")
    if os.path.isfile(path):
        return path
    raise NoBoard(f"{source} points to {path}, which does not exist", hint="earmark init")


def _walk_up(start: str) -> str | None:
    current = start
    while True:
        candidate = os.path.join(current, DIR_NAME, DB_NAME)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def git_main_checkout(cwd: str) -> str | None:
    """Directory of the main checkout for the repository containing cwd (worktree-aware)."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=cwd, capture_output=True, text=True, timeout=GIT_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    common = out.stdout.strip()
    if out.returncode != 0 or not common:
        return None
    common = os.path.normpath(common)
    return os.path.dirname(common) if os.path.basename(common) == ".git" else common


# ---- connections ------------------------------------------------------------------------

def check_sqlite() -> None:
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise InternalError(
            f"earmark needs SQLite {'.'.join(map(str, MIN_SQLITE))} or newer; this Python links "
            f"SQLite {sqlite3.sqlite_version}",
            hint="use a newer Python build (python.org, uv or your package manager)",
        )


def connect(path: str, *, busy_timeout_ms: int = BUSY_TIMEOUT_MS) -> sqlite3.Connection:
    """Autocommit connection with the board pragmas. Not for concurrent use across threads."""
    check_sqlite()
    try:
        conn = sqlite3.connect(path, isolation_level=None, timeout=busy_timeout_ms / 1000,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        for pragma in PRAGMAS:
            conn.execute(pragma)
    except sqlite3.DatabaseError as exc:
        raise NoBoard(f"cannot open board {path}: {exc}") from None
    return conn


def _is_locked(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "locked" in text or "busy" in text


def _sleep_jitter(attempt: int) -> None:
    import random

    time.sleep(random.uniform(0.02, 0.1) * (2 ** attempt))


@contextmanager
def write(conn: sqlite3.Connection, *, retries: int = WRITE_RETRIES,
          sleep: Callable[[int], None] = _sleep_jitter) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE … COMMIT; ROLLBACK on error. Raises Busy when the lock stays held."""
    for attempt in range(retries + 1):
        try:
            conn.execute("BEGIN IMMEDIATE")
            break
        except sqlite3.OperationalError as exc:
            if not _is_locked(exc):
                raise
            if attempt == retries:
                raise Busy("the board is busy (another writer holds the lock); try again",
                           hint="retry the same command") from None
            sleep(attempt)
    try:
        yield conn
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    try:
        conn.execute("COMMIT")
    except sqlite3.OperationalError as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        if _is_locked(exc):
            raise Busy("the board is busy; the write was rolled back", hint="retry the same command") from None
        raise


@contextmanager
def snapshot(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A consistent read view across several SELECTs (deferred BEGIN, takes no write lock)."""
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN")
    try:
        yield conn
    finally:
        if conn.in_transaction:
            conn.execute("COMMIT")


# ---- schema and migrations --------------------------------------------------------------

def schema_sql() -> str:
    """schema.sql from the installed package or the checkout."""
    try:
        from importlib.resources import files

        return files("earmark").joinpath("schema.sql").read_text(encoding="utf-8")
    except (ImportError, FileNotFoundError, OSError, TypeError):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "schema.sql"), encoding="utf-8") as fh:
            return fh.read()


def split_sql(script: str) -> list[str]:
    """Split a script into complete statements (executescript would COMMIT on Python 3.10)."""
    statements, buf = [], ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                statements.append(buf.strip())
            buf = ""
    if buf.strip() and not all(l.strip().startswith("--") or not l.strip() for l in buf.splitlines()):
        raise InternalError("schema.sql ends with an incomplete statement")
    return statements


def _migration_v1(conn: sqlite3.Connection) -> None:
    for statement in split_sql(schema_sql()):
        conn.execute(statement)


# version -> function applied inside the migration write transaction
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {1: _migration_v1}


def schema_version(conn: sqlite3.Connection) -> int:
    has_meta = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'meta'").fetchone()
    if not has_meta:
        return 0
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return int(row[0]) if row else 0


def migrate(conn: sqlite3.Connection, *, target: int = SCHEMA_VERSION) -> int:
    """Apply missing migrations in one write transaction. Returns the resulting version."""
    current = schema_version(conn)
    if current > target:
        raise InvalidRequest(f"this board uses schema {current}; this earmark understands up to "
                             f"{target}", hint="upgrade earmark")
    if current == target:
        return current
    with write(conn):
        current = schema_version(conn)
        for version in range(current + 1, target + 1):
            MIGRATIONS[version](conn)
            conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(version),))
    return target


def create(path: str) -> sqlite3.Connection:
    """Create a new board file in WAL mode with the current schema."""
    conn = connect(path)
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if str(mode).lower() != "wal":
        conn.close()
        raise InternalError(f"could not enable WAL on {path} (got {mode}); is it on a network share?",
                            hint="put the board on a local disk")
    migrate(conn)
    return conn


def open_board(path: str) -> sqlite3.Connection:
    """Connect to an existing board and bring its schema up to date."""
    conn = connect(path)
    try:
        version = schema_version(conn)
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise NoBoard(f"{path} is not an earmark board: {exc}") from None
    if version == 0:
        conn.close()
        raise NoBoard(f"{path} is not an earmark board (no meta table)", hint="earmark init")
    if version < SCHEMA_VERSION:
        migrate(conn)
    elif version > SCHEMA_VERSION:
        conn.close()
        raise InvalidRequest(f"this board uses schema {version}; this earmark understands up to "
                             f"{SCHEMA_VERSION}", hint="upgrade earmark")
    return conn


def chunks(seq: list, size: int = 500) -> Iterator[list]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]
