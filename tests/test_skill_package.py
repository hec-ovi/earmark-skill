"""Skill package checks: spec frontmatter, size budget, copies in sync, commands that exist,
profile files that parse, plugin manifests that agree. Stdlib only; parsing is done by hand so
the test enforces the YAML subset every agent CLI can read."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "earmark"
SKILL = SKILL_DIR / "SKILL.md"
COMMANDS = SKILL_DIR / "references" / "commands.md"
PROFILES = ROOT / "src" / "earmark" / "profiles"
ROLES = ("worker", "reviewer", "pm")
VERSION = "0.1.0"

SPEC_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
BODY_MAX_LINES = 100          # target 60-90; the spec ceiling is 500
BODY_MAX_TOKENS = 2500        # chars / 4; the spec ceiling is 5000
TWO_WORD = {"topic", "profile"}
GLOBAL_FLAGS = {"--as", "--db", "--json"}


def unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """The YAML subset skills use: `key: plain`, `key: >-` folded blocks, one nested map."""
    lines = text.split("\n")
    if lines[0] != "---":
        raise ValueError("must start with a --- line")
    end = lines.index("---", 1)
    block, meta, i = lines[1:end], {}, 0
    while i < len(block):
        line = block[i]
        i += 1
        if not line.strip():
            continue
        m = re.fullmatch(r"([a-z][a-z0-9_-]*):(?: (.*))?", line)
        if not m:
            raise ValueError(f"not a top-level key: {line!r}")
        key, value = m.group(1), (m.group(2) or "").strip()
        nested = []
        while i < len(block) and (block[i].startswith("  ") or not block[i].strip()):
            nested.append(block[i])
            i += 1
        if value in (">", ">-"):
            meta[key] = " ".join(n.strip() for n in nested if n.strip())
        elif value == "":
            sub = {}
            for n in nested:
                if n.strip():
                    mm = re.fullmatch(r"  ([a-z][a-z0-9_-]*): (.+)", n)
                    if not mm:
                        raise ValueError(f"not a nested key: {n!r}")
                    sub[mm.group(1)] = unquote(mm.group(2).strip())
            meta[key] = sub
        else:
            if nested:
                raise ValueError(f"{key}: unexpected indented lines")
            if ": " in value or " #" in value:
                raise ValueError(f"{key}: plain scalar needs quoting or a >- block")
            meta[key] = unquote(value)
    return meta, "\n".join(lines[end + 1:])


def strip_quotes(text: str) -> str:
    return re.sub(r'"[^"]*"', '"X"', text)


def command_mentions(text: str, code_spans_only: bool) -> list[str]:
    """`earmark <sub> ...` invocations, cut where prose resumes."""
    if code_spans_only:
        found = [s for s in re.findall(r"`([^`]+)`", text, flags=re.S) if s.startswith("earmark ")]
    else:
        found = [text[m.start():] for m in re.finditer(r"\bearmark (?=[a-z])", text)]
    out = []
    for raw in found:
        cmd = strip_quotes(raw.replace("\n", " "))
        cmd = re.split(r": |; |, |\. | then |\)|$", cmd, maxsplit=1)[0]
        out.append(cmd.strip())
    return out


def subcommand(cmd: str) -> str:
    words = cmd.split()
    if len(words) > 2 and words[1] in TWO_WORD and re.fullmatch(r"[a-z]+", words[2]):
        return f"{words[1]} {words[2]}"
    return words[1]


def flags(cmd: str) -> set[str]:
    return {t.split("=")[0].rstrip(".,") for t in cmd.split()[1:] if re.fullmatch(r"--?[a-z][a-z-]*(=.*)?[.,]?", t)}


def has_flag(text: str, flag: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", text) is not None


def contract() -> tuple[set[str], dict[str, str]]:
    """Subcommands named in commands.md and, per first word, the contract text that covers it."""
    text = COMMANDS.read_text(encoding="utf-8")
    subs = set()
    for span in re.findall(r"`(earmark [^`]+)`", text):
        subs.add(subcommand(span))
        subs.add(span.split()[1])
    scope: dict[str, str] = {}
    for line in text.splitlines():
        for word in set(re.findall(r"`earmark ([a-z]+)", line)):
            scope[word] = scope.get(word, "") + line + "\n"
    for heading, body in re.findall(r"^### `([a-z]+)`[^\n]*\n(.*?)(?=^#)", text, flags=re.S | re.M):
        scope[heading] = scope.get(heading, "") + body
    return subs, scope


def skill_texts() -> dict[str, tuple[str, bool]]:
    """Files whose commands must exist: name -> (text, commands only inside code spans)."""
    _, body = parse_frontmatter(SKILL.read_text(encoding="utf-8"))
    texts = {"SKILL.md": (body, True)}
    for role in ROLES:
        loop = PROFILES / "loops" / f"{role}.md"
        texts[f"loops/{role}.md"] = (loop.read_text(encoding="utf-8"), False)
    codex = ROOT / "plugins" / "earmark-codex" / "commands" / "earmark.md"
    texts["codex command"] = (codex.read_text(encoding="utf-8"), True)
    return texts


def load_sync():
    spec = importlib.util.spec_from_file_location("sync_skill_copies", ROOT / "scripts" / "sync-skill-copies.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FrontmatterTest(unittest.TestCase):
    def setUp(self):
        self.meta, self.body = parse_frontmatter(SKILL.read_text(encoding="utf-8"))

    def test_only_spec_keys(self):
        self.assertLessEqual(set(self.meta), SPEC_KEYS)
        self.assertIn("name", self.meta)
        self.assertIn("description", self.meta)

    def test_name(self):
        name = self.meta["name"]
        self.assertRegex(name, r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(name), 64)
        self.assertEqual(name, SKILL_DIR.name)
        self.assertNotRegex(name, r"anthropic|claude")

    def test_description(self):
        desc = self.meta["description"]
        self.assertTrue(1 <= len(desc) <= 1024, len(desc))
        self.assertNotRegex(desc, r"[<>]", "no XML-like tags in the description")
        self.assertRegex(desc, r"^[A-Z][a-z]+s ", "third person: starts with a verb in -s")
        lower = desc.lower()
        for trigger in (".earmark", "issue", "ticket", "backlog", "board", "review queue",
                        "join as", "pick up the next issue", "what are the agents working on"):
            self.assertIn(trigger, lower)

    def test_optional_fields(self):
        self.assertEqual(self.meta.get("license"), "MIT")
        self.assertLessEqual(len(self.meta.get("compatibility", "")), 500)
        metadata = self.meta.get("metadata", {})
        self.assertIsInstance(metadata, dict)
        self.assertEqual(metadata.get("version"), VERSION)
        self.assertEqual(metadata.get("author"), "Hector Oviedo")
        self.assertEqual(self.meta.get("allowed-tools"), "Bash(earmark:*)")


class BodyTest(unittest.TestCase):
    def setUp(self):
        _, self.body = parse_frontmatter(SKILL.read_text(encoding="utf-8"))

    def test_size_budget(self):
        lines = self.body.strip("\n").splitlines()
        self.assertLessEqual(len(lines), BODY_MAX_LINES)
        self.assertLessEqual(len(self.body) // 4, BODY_MAX_TOKENS)

    def test_sections(self):
        for heading in ("## Start", "## Read in levels", "## Worker loop", "## Reviewer loop",
                        "## PM loop", "## Exit codes", "## Rules"):
            self.assertIn(heading, self.body)

    def test_standing_rules(self):
        for needle in ("--as <handle>", "uv tool install git+https://github.com/hec-ovi/earmark-skill",
                       "Never install it yourself", "--kind progress", "earmark next --review",
                       "earmark wait --timeout 9m", "earmark leave", "three lines"):
            self.assertIn(needle, self.body)

    def test_references_resolve_one_level_deep(self):
        links = re.findall(r"\]\(([^)]+)\)", self.body)
        self.assertIn("references/commands.md", links)
        for link in links:
            self.assertRegex(link, r"^references/[a-z0-9-]+\.md$")
            self.assertTrue((SKILL_DIR / link).is_file(), link)
        on_disk = {f"references/{p.name}" for p in (SKILL_DIR / "references").iterdir()}
        self.assertEqual(on_disk, set(links), "every reference is linked from SKILL.md")


class CommandsExistTest(unittest.TestCase):
    def test_subcommands_and_flags_are_in_the_contract(self):
        subs, scope = contract()
        all_contract = COMMANDS.read_text(encoding="utf-8")
        for name, (text, spans) in skill_texts().items():
            cmds = command_mentions(text, spans)
            self.assertTrue(cmds, f"{name}: no earmark commands found")
            for cmd in cmds:
                sub = subcommand(cmd)
                self.assertIn(sub, subs, f"{name}: unknown command in {cmd!r}")
                covered = scope.get(sub.split()[0], "")
                for flag in flags(cmd) - GLOBAL_FLAGS:
                    self.assertTrue(has_flag(covered, flag), f"{name}: {flag} is not a flag of {sub}: {cmd!r}")
            for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]+", strip_quotes(text)):
                self.assertTrue(has_flag(all_contract, flag), f"{name}: {flag} is not in commands.md")

    def test_real_cli_accepts_them(self):
        env = dict(os.environ, EARMARK_DB=str(ROOT / "tests" / "no-such-board.db"))
        probe = subprocess.run([sys.executable, str(ROOT / "earmark"), "--help"],
                               capture_output=True, text=True, env=env, timeout=30)
        if probe.returncode != 0:
            self.skipTest("CLI not runnable yet")
        seen = {}
        for name, (text, spans) in skill_texts().items():
            for cmd in command_mentions(text, spans):
                seen.setdefault(subcommand(cmd), set()).update(flags(cmd))
        for sub, used in sorted(seen.items()):
            proc = subprocess.run([sys.executable, str(ROOT / "earmark"), *sub.split(), "--help"],
                                  capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(proc.returncode, 0, f"earmark {sub} --help: {proc.stderr}")
            for flag in used - GLOBAL_FLAGS:
                self.assertTrue(has_flag(proc.stdout, flag), f"earmark {sub} --help lacks {flag}")


class CopiesTest(unittest.TestCase):
    def test_copies_match_canonical(self):
        sync = load_sync()
        for target in sync.TARGETS:
            self.assertEqual(sync.drift(target), [],
                             f"{target.relative_to(ROOT)} drifted: run scripts/sync-skill-copies.py")

    def test_root_discovery(self):
        self.assertTrue((ROOT / "SKILL.md").is_file())
        self.assertFalse((ROOT / "skills" / "SKILL.md").exists())


class ProfilesTest(unittest.TestCase):
    def test_profiles_parse(self):
        for role in ROLES:
            meta, prompt = parse_frontmatter((PROFILES / f"{role}.md").read_text(encoding="utf-8"))
            self.assertEqual(set(meta), {"name", "role", "icon"}, role)
            self.assertTrue(meta["name"].strip())
            self.assertEqual(meta["role"], role)
            self.assertRegex(meta["icon"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
            lines = [ln for ln in prompt.strip().splitlines() if ln.strip()]
            self.assertTrue(4 <= len(lines) <= 10, f"{role}: {len(lines)} prompt lines")
            self.assertTrue(lines[0].startswith("You are"), f"{role}: second person")

    def test_loops(self):
        for role in ROLES:
            text = (PROFILES / "loops" / f"{role}.md").read_text(encoding="utf-8")
            lines = text.strip().splitlines()
            self.assertLessEqual(len(lines), 15, role)
            self.assertIn("--as <your handle>", lines[0])
            steps = [int(m) for m in re.findall(r"^(\d+)\. ", text, flags=re.M)]
            self.assertEqual(steps, list(range(1, len(steps) + 1)), f"{role}: numbered steps")
            self.assertIn("3-line summary", text)
            self.assertIn("earmark wait", text)


class ManifestsTest(unittest.TestCase):
    def load(self, rel):
        return json.loads((ROOT / rel).read_text(encoding="utf-8"))

    def test_claude_plugin(self):
        market = self.load(".claude-plugin/marketplace.json")
        entry = market["plugins"][0]
        self.assertEqual((entry["name"], entry["source"], entry["version"]), ("earmark", "./plugins/earmark", VERSION))
        self.assertEqual(market["metadata"]["version"], VERSION)
        plugin = self.load("plugins/earmark/.claude-plugin/plugin.json")
        self.assertEqual((plugin["name"], plugin["version"], plugin["license"]), ("earmark", VERSION, "MIT"))
        self.assertTrue((ROOT / "plugins/earmark/skills/earmark/SKILL.md").is_file())
        self.assertFalse((ROOT / ".claude-plugin" / "plugin.json").exists())

    def test_codex_plugin(self):
        market = self.load(".agents/plugins/marketplace.json")
        entry = market["plugins"][0]
        self.assertEqual(entry["name"], "earmark-codex")
        self.assertEqual(entry["source"]["path"], "./plugins/earmark-codex")
        plugin = self.load("plugins/earmark-codex/.codex-plugin/plugin.json")
        self.assertEqual((plugin["name"], plugin["version"], plugin["skills"]), ("earmark-codex", VERSION, "./skills/"))
        self.assertEqual(plugin["interface"]["brandColor"], "#4F5BD5")
        self.assertEqual(plugin["interface"]["category"], "Productivity")
        self.assertTrue((ROOT / "plugins/earmark-codex/commands/earmark.md").is_file())

    def test_authors(self):
        for rel in (".claude-plugin/marketplace.json", "plugins/earmark/.claude-plugin/plugin.json",
                    "plugins/earmark-codex/.codex-plugin/plugin.json"):
            data = self.load(rel)
            author = data.get("author") or data["owner"]
            self.assertEqual(author, {"name": "Hector Oviedo", "url": "https://github.com/hec-ovi"}, rel)


class HygieneTest(unittest.TestCase):
    OWNED = ["skills/earmark", "src/earmark/profiles", "plugins", ".claude-plugin", ".agents",
             "evals", "docs/INSTALL.md", "docs/SKILL_DESIGN.md", "install.sh", "scripts/sync-skill-copies.py"]

    def files(self):
        for rel in self.OWNED:
            path = ROOT / rel
            if path.is_file():
                yield path
            elif path.is_dir():
                yield from (p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts)

    def test_no_personal_paths_or_emails(self):
        for path in self.files():
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(ROOT)
            self.assertNotRegex(text, r"/home/[a-z]|/Users/[A-Za-z]", f"{rel}: home-directory path")
            self.assertNotRegex(text, r"[\w.+-]+@[\w-]+\.[\w.]+", f"{rel}: email address")

    def test_no_em_or_en_dashes(self):
        for path in self.files():
            self.assertNotRegex(path.read_text(encoding="utf-8"), "[–—]", str(path.relative_to(ROOT)))


if __name__ == "__main__":
    unittest.main()
