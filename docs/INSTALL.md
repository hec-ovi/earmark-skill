# Install and harness setup

earmark has two parts, and an agent needs both:

- the **CLI**: distribution `earmark-skill`, command `earmark`, Python 3.10+ standard library
  only, no dependencies;
- the **skill**: `SKILL.md` plus `references/`, which tells an agent when to use the board and
  how to work it.

There is no MCP server. Agents call `earmark` from their own shell, and the skill resolves it
on PATH first, then the `earmark` launcher beside `SKILL.md` when the whole repo was installed
as the skill.

## Route summary

| You want | Do this |
|---|---|
| CLI on PATH | `uv tool install git+https://github.com/hec-ovi/earmark-skill` |
| CLI once, no install | `uvx --from git+https://github.com/hec-ovi/earmark-skill earmark info` |
| Skill in any Agent Skills CLI | `npx skills add hec-ovi/earmark-skill` |
| Skill in Grok | `/skills add hec-ovi/earmark-skill` |
| Claude Code plugin | `/plugin marketplace add hec-ovi/earmark-skill` then `/plugin install earmark@earmark-skill` |
| Codex plugin | `codex plugin marketplace add hec-ovi/earmark-skill` |
| CLI and skill from a clone | `git clone https://github.com/hec-ovi/earmark-skill && cd earmark-skill && ./install.sh` |
| Skill for one project only | `./install.sh --project /path/to/project` |

Then, once per project: `earmark init` in the project root creates `.earmark/earmark.db`
(with a `.gitignore` of `*`) and seeds the `worker`, `reviewer` and `pm` profiles.

## Where each CLI looks for skills

| Agent CLI | User level | Project level |
|---|---|---|
| Claude Code | `~/.claude/skills/earmark` | `.claude/skills/earmark` |
| Codex | `~/.agents/skills/earmark` | `.agents/skills/earmark` |
| Grok (`grok`) | `~/.agents/skills/earmark` | `.agents/skills/earmark` |
| OpenCode | `~/.agents/skills/earmark` | `.agents/skills/earmark` |
| Antigravity (`agy`) | `~/.gemini/config/skills/earmark` | `.agents/skills/earmark` |

Codex's older `~/.codex/skills` location is deprecated; use `~/.agents/skills`. Three
user-level links cover all five CLIs: `~/.agents/skills/earmark`, `~/.claude/skills/earmark`
and `~/.gemini/config/skills/earmark`. That is what `install.sh` creates.

## Skill copies in this repo

Installers look in different places, so the repo carries identical copies of one package:

| Path | Used by |
|---|---|
| `skills/earmark/` | canonical source; `npx skills add`, `install.sh` links |
| `SKILL.md` + `references/` at the root | Grok `/skills add`, cloning the repo as a skill |
| `plugins/earmark/skills/earmark/` | Claude Code plugin |
| `plugins/earmark-codex/skills/earmark/` | Codex plugin |

Edit only `skills/earmark/`, then run `python3 scripts/sync-skill-copies.py`.
`python3 scripts/sync-skill-copies.py --check` and `tests/test_skill_package.py` fail when a
copy drifts.

## CLI

```bash
uv tool install git+https://github.com/hec-ovi/earmark-skill
earmark doctor
```

Pin a ref with `@v0.1.0` or `@<sha>` on the git URL. Without uv,
`pipx install git+https://github.com/hec-ovi/earmark-skill` installs the same package. From a
clone, `./earmark <command>` runs the checkout directly with no install.

## `npx skills add`

The [`skills`](https://www.npmjs.com/package/skills) CLI installs `skills/earmark/` into every
agent it detects.

```bash
npx skills add hec-ovi/earmark-skill                 # detected agents, project scope
npx skills add hec-ovi/earmark-skill -g              # user scope
npx skills add hec-ovi/earmark-skill -a claude-code -a codex
npx skills add hec-ovi/earmark-skill --copy -y       # copy instead of symlink
```

It installs the skill only; install the CLI with `uv tool install` as well.

## Grok

```text
/skills add hec-ovi/earmark-skill
```

Grok clones the repo and installs the root `SKILL.md`. Grok also reads `~/.agents/skills` and
`.agents/skills`, so `npx skills add` and `install.sh` cover it too.

Grok loads project-level skills (`.agents/skills`, `.grok/skills`) only in trusted folders. Trust
the project once with `grok --trust` from its root, or install the skill at user level
(`~/.agents/skills/earmark`), which does not depend on folder trust. `grok inspect --json` lists
the skills Grok sees and whether the folder is trusted.

## Claude Code

```text
/plugin marketplace add hec-ovi/earmark-skill
/plugin install earmark@earmark-skill
/reload-plugins
```

`.claude-plugin/marketplace.json` points at `./plugins/earmark`, which holds
`.claude-plugin/plugin.json` and `skills/earmark/`. The skill declares
`allowed-tools: Bash(earmark:*)`, so `earmark` commands run without a permission prompt while it
is active. Manual install: link or copy `skills/earmark` to `~/.claude/skills/earmark`.

## Codex

```bash
codex plugin marketplace add hec-ovi/earmark-skill
```

Metadata lives in `.agents/plugins/marketplace.json` and
`plugins/earmark-codex/.codex-plugin/plugin.json`. The plugin adds `/earmark [join <profile> |
next | review]`. The board sits inside the project, so the default `workspace-write` sandbox
allows every write. An agent in a git worktree outside the main checkout writes to the main
checkout's board; add that checkout to `sandbox_workspace_write.writable_roots`.

## Antigravity and OpenCode

Both read `.agents/skills` in the project. At user level Antigravity reads
`~/.gemini/config/skills` and OpenCode reads `~/.agents/skills`. `npx skills add` or
`install.sh` places the skill in both.

## `install.sh`

From a clone:

| Command | Effect |
|---|---|
| `./install.sh` | CLI, then links `~/.agents/skills/earmark`, `~/.claude/skills/earmark`, `~/.gemini/config/skills/earmark` to `skills/earmark` |
| `./install.sh --project DIR` | CLI, then links `DIR/.agents/skills/earmark` and `DIR/.claude/skills/earmark` |
| `./install.sh --dry-run` | prints every action, changes nothing |
| `./install.sh --no-cli` | links only (`EARMARK_INSTALL_SKIP_CLI=1` does the same) |
| `./install.sh --uninstall` | removes the links that point at this checkout and the CLI; add `--project DIR` for a project install |

The CLI step uses `uv tool install --force --reinstall <checkout>` when uv is on PATH, else
`pipx install --force <checkout>`, else links `~/.local/bin/earmark` to the checkout launcher.
Re-running is safe: correct links are left alone, links to an older checkout are repointed, and
an existing directory at a link path is never replaced (the script reports it and exits 1).

## First session

1. `earmark info`: the board is found (run `earmark init` once per project if not).
2. `earmark join worker --cli claude-code` (or `codex`, `grok`, `agy`, `opencode`): note the handle.
3. `earmark next --as <handle>`: the agent's first issue.

Or tell the agent "join the board as worker and pick up the next issue"; the skill does the rest.
