"""earmark command line: argparse grammar and text/JSON rendering over the board service.

`main` is the console-script entry. Everything else is imported on demand so `earmark ls`
loads only the CLI, the board and db modules.
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    from earmark.cli.app import run
    return run(argv)


__all__ = ["main"]
