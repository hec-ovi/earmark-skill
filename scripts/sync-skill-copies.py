#!/usr/bin/env python3
"""Mirror the canonical skill package to every install surface.

Canonical source: skills/earmark/{SKILL.md,references/}

Copies:
  - repo root                          (Grok /skills add, clone-as-skill)
  - plugins/earmark/skills/earmark       (Claude Code plugin)
  - plugins/earmark-codex/skills/earmark (Codex plugin)

Edit only skills/earmark/, then run this script. `--check` reports drift and exits 1
without writing; tests/test_skill_package.py fails on drift too.
"""

from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "skills" / "earmark"
TARGETS = [
    ROOT,
    ROOT / "plugins" / "earmark" / "skills" / "earmark",
    ROOT / "plugins" / "earmark-codex" / "skills" / "earmark",
]


def source_files() -> list[Path]:
    """Paths relative to SRC: SKILL.md plus every file under references/."""
    refs = sorted(p.relative_to(SRC) for p in (SRC / "references").rglob("*") if p.is_file())
    return [Path("SKILL.md"), *refs]


def drift(target: Path) -> list[str]:
    """Differences between SRC and one target, as relative paths with a reason."""
    problems = []
    wanted = source_files()
    for rel in wanted:
        dst = target / rel
        if not dst.is_file():
            problems.append(f"missing {rel}")
        elif not filecmp.cmp(SRC / rel, dst, shallow=False):
            problems.append(f"differs {rel}")
    dest_refs = target / "references"
    if dest_refs.is_dir():
        extra = {p.relative_to(target) for p in dest_refs.rglob("*") if p.is_file()} - set(wanted)
        problems += [f"extra {rel}" for rel in sorted(extra)]
    return problems


def sync(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SRC / "SKILL.md", target / "SKILL.md")
    dest_refs = target / "references"
    if dest_refs.exists():
        shutil.rmtree(dest_refs)
    shutil.copytree(SRC / "references", dest_refs)


def main(argv: list[str]) -> int:
    if not (SRC / "SKILL.md").is_file() or not (SRC / "references").is_dir():
        print(f"canonical skill missing under {SRC.relative_to(ROOT)}", file=sys.stderr)
        return 1
    check = "--check" in argv
    status = 0
    for target in TARGETS:
        name = target.relative_to(ROOT).as_posix() if target != ROOT else "."
        if check:
            problems = drift(target)
            for problem in problems:
                print(f"drift {name}: {problem}", file=sys.stderr)
            status = status or (1 if problems else 0)
        else:
            sync(target)
            print(f"synced -> {name}")
    if check and status == 0:
        print("all skill copies match skills/earmark")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
