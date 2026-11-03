"""install.sh against a throwaway HOME: dry run, real run, idempotence, project mode,
conflict handling, launcher fallback and uninstall. Never touches the real HOME."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.sh"
SKILL = str(ROOT / "skills" / "earmark")
USER_LINKS = (".agents/skills/earmark", ".claude/skills/earmark", ".gemini/config/skills/earmark")
PROJECT_LINKS = (".agents/skills/earmark", ".claude/skills/earmark")
BASH = shutil.which("bash")


def tree(base: Path) -> dict[str, str]:
    """Every non-directory entry under base: relative path -> link target (or 'file')."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(base):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                out[str(path.relative_to(base))] = os.readlink(path)
            elif path.is_file():
                out[str(path.relative_to(base))] = "file"
    return out


@unittest.skipUnless(BASH and os.name == "posix", "install.sh needs a POSIX shell and symlinks")
class InstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="earmark-install-"))
        self.home = self.tmp / "home dir"          # a space, to catch word splitting
        self.home.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_install(self, *args: str, skip_cli: bool = True, path: str | None = None):
        env = {"HOME": str(self.home), "PATH": path or os.environ.get("PATH", "/usr/bin:/bin")}
        if skip_cli:
            env["EARMARK_INSTALL_SKIP_CLI"] = "1"
        return subprocess.run([BASH, str(INSTALL), *args], capture_output=True, text=True,
                              env=env, timeout=60)

    def assertOk(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_dry_run_changes_nothing(self):
        proc = self.run_install("--dry-run")
        self.assertOk(proc)
        self.assertEqual(tree(self.home), {})
        self.assertEqual(list(self.home.iterdir()), [])
        for rel in USER_LINKS:
            self.assertIn(f"would link  {self.home / rel} -> {SKILL}", proc.stdout)

    def test_install_is_exact_and_idempotent(self):
        self.assertOk(self.run_install())
        expected = {rel: SKILL for rel in USER_LINKS}
        self.assertEqual(tree(self.home), expected)
        again = self.run_install()
        self.assertOk(again)
        self.assertEqual(again.stdout.count("already linked"), len(USER_LINKS))
        self.assertEqual(tree(self.home), expected)

    def test_uninstall_removes_only_its_links(self):
        self.assertOk(self.run_install())
        other = self.home / ".claude" / "skills" / "other"
        other.symlink_to(self.tmp)
        proc = self.run_install("--uninstall")
        self.assertOk(proc)
        self.assertEqual(tree(self.home), {".claude/skills/other": str(self.tmp)})
        self.assertOk(self.run_install("--uninstall"))

    def test_project_mode(self):
        project = self.tmp / "project"
        project.mkdir()
        self.assertOk(self.run_install("--project", str(project)))
        self.assertEqual(tree(project), {rel: SKILL for rel in PROJECT_LINKS})
        self.assertEqual(tree(self.home), {})
        self.assertOk(self.run_install("--project", str(project), "--uninstall"))
        self.assertEqual(tree(project), {})

    def test_existing_directory_is_kept(self):
        real = self.home / ".claude" / "skills" / "earmark"
        real.mkdir(parents=True)
        (real / "SKILL.md").write_text("user copy\n")
        proc = self.run_install()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("is not a symlink", proc.stderr)
        self.assertEqual((real / "SKILL.md").read_text(), "user copy\n")
        self.assertEqual(os.readlink(self.home / USER_LINKS[0]), SKILL)

    def test_stale_link_is_repointed(self):
        stale = self.home / USER_LINKS[0]
        stale.parent.mkdir(parents=True)
        stale.symlink_to(self.tmp / "old-checkout")
        self.assertOk(self.run_install())
        self.assertEqual(os.readlink(stale), SKILL)

    def test_launcher_fallback_without_uv_or_pipx(self):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for tool in ("dirname", "readlink", "mkdir", "ln", "rm", "python3", "grep"):
            found = shutil.which(tool)
            if found:
                (bin_dir / tool).symlink_to(found)
        dry = self.run_install("--dry-run", skip_cli=False, path=str(bin_dir))
        self.assertOk(dry)
        self.assertIn(f"would link  {self.home}/.local/bin/earmark -> {ROOT / 'earmark'}", dry.stdout)
        self.assertEqual(tree(self.home), {})
        proc = self.run_install(skip_cli=False, path=str(bin_dir))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        expected = {rel: SKILL for rel in USER_LINKS}
        expected[".local/bin/earmark"] = str(ROOT / "earmark")
        self.assertEqual(tree(self.home), expected)
        self.assertOk(self.run_install("--uninstall", skip_cli=False, path=str(bin_dir)))
        self.assertEqual(tree(self.home), {})

    def test_bad_option(self):
        proc = self.run_install("--bogus")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("unknown option", proc.stderr)
        self.assertEqual(tree(self.home), {})


if __name__ == "__main__":
    unittest.main()
