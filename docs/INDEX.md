# Index

Map "what to change" to the one place to open. Behavior is defined in `ARCHITECTURE.md`; the CLI
surface in `skills/earmark/references/commands.md`; the wire format in `HTTP_API.md`.

| Want | Open |
|---|---|
| Behavior rules: storage, identity, transitions, leases, `next`, needs, cursors, `check` | `docs/ARCHITECTURE.md` |
| Schema | `src/earmark/schema.sql` |
| Board discovery, pragmas, write transactions, migrations | `src/earmark/db.py` |
| Service facade (the only layer that runs SQL) | `src/earmark/board.py` |
| Issue reads, filters, edits, rank, links | `src/earmark/issues.py`, `src/earmark/views.py` |
| Status moves, claims, leases, `next`, review flow, needs, auto-unblock | `src/earmark/workflow.py` |
| Comments, mentions, cursors, inbox | `src/earmark/comments.py` |
| Topics tree | `src/earmark/topics.py` |
| Profiles, join and leave, team, triage, `check` | `src/earmark/team.py` |
| Attachments beside the board | `src/earmark/attachments.py` |
| Info, config, doctor, export, backup | `src/earmark/admin.py` |
| Enums and input parsing · time and durations · error codes | `src/earmark/model.py` · `timeutil.py` · `errors.py` |
| Default profiles and role loops printed by `join` | `src/earmark/profiles/` |
| CLI grammar · text rendering · commands by family | `src/earmark/cli/parser.py` · `render.py` · `cmd_*.py` |
| HTTP routes · security checks · live stream · static files | `src/earmark/server/api.py` · `app.py` · `stream.py` · `static.py` |
| Web UI (no build step) | `src/earmark/ui/` (`js/`, `css/`, `vendor/`, `icons.svg`) |
| JSON Schemas for `--json` and the HTTP API | `contracts/` |
| Skill (canonical) and its references | `skills/earmark/` |
| Skill copies: repo root, Claude plugin, Codex plugin | `SKILL.md` + `references/`, `plugins/earmark/`, `plugins/earmark-codex/` (synced by `scripts/sync-skill-copies.py`) |
| Marketplaces | `.claude-plugin/marketplace.json`, `.agents/plugins/marketplace.json` |
| Install across CLIs | `docs/INSTALL.md`, `install.sh` |
| Skill design and token budget | `docs/SKILL_DESIGN.md` |
| Evaluation scenarios for real agent CLIs | `evals/` |
| Demo board · soak harness · icon sprite | `scripts/seed-demo.py` · `scripts/soak.py` · `scripts/build-icons.py` |
| Measured performance | `docs/PERFORMANCE.md` |
| Tests | `tests/` (`test_core_*`, `test_cli_*`, `test_server_*`, `test_skill_*`, `test_ui_*`) |
