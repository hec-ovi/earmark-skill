"""Shared test helpers: temp boards, joined agents and a controllable clock.

    from tests.support import BoardCase, make_board, join_agent, FakeClock

Boards live only in temporary directories; nothing touches the repository or home directory.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from earmark import timeutil  # noqa: E402
from earmark.board import Board  # noqa: E402

START = datetime(2026, 11, 3, 9, 0, 0, tzinfo=timezone.utc)


def make_board(tmpdir: str, prefix: str = "GAME", *, owner: str = "owner",
               require_review: bool = True) -> Board:
    """Initialize a board in tmpdir; the returned Board acts as the owner."""
    return Board.init(tmpdir, prefix=prefix, owner=owner, require_review=require_review)


def open_as(board: Board | str, handle: str | None) -> Board:
    """A second Board (own connection) on the same file, acting as handle."""
    path = board.path if isinstance(board, Board) else board
    return Board.open(db=path, actor=handle)


def join_agent(board: Board | str, handle: str, profile: str = "worker", *,
               cli: str | None = "test") -> Board:
    """Join handle with profile on its own connection and return that Board."""
    agent = open_as(board, None)
    agent.join(profile, handle=handle, cli=cli)
    return agent


class FakeClock:
    """Install with timeutil.set_clock(clock); move it with advance(minutes=...)."""

    def __init__(self, start: datetime = START):
        self.current = start

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **delta) -> datetime:
        self.current += timedelta(**delta)
        return self.current

    def set(self, moment: datetime) -> None:
        self.current = moment


class BoardCase(unittest.TestCase):
    """A fresh board per test as self.board (owner 'owner', prefix GAME) and a frozen clock
    as self.clock. Boards opened with self.agent()/self.open_as() are closed automatically."""

    prefix = "GAME"
    require_review = True
    freeze_clock = True

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="earmark-test-")
        self.clock = FakeClock()
        if self.freeze_clock:
            timeutil.set_clock(self.clock)
        self._boards: list[Board] = []
        self.board = self._track(make_board(self.tmp, self.prefix,
                                            require_review=self.require_review))

    def tearDown(self) -> None:
        for board in self._boards:
            board.close()
        timeutil.reset_clock()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _track(self, board: Board) -> Board:
        self._boards.append(board)
        return board

    def agent(self, handle: str, profile: str = "worker", **kw) -> Board:
        return self._track(join_agent(self.board, handle, profile, **kw))

    def open_as(self, handle: str | None) -> Board:
        return self._track(open_as(self.board, handle))

    def issue(self, title: str = "Task", **kw) -> dict:
        return self.board.create_issue(title, **kw)
