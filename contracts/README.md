# Contracts

JSON Schemas (draft 2020-12) for everything earmark prints with `--json` and serves over HTTP.

| File | Describes |
|---|---|
| `envelope.schema.json` | the wrapper `{contract_version, ok, data, error, meta}` and the closed error code set |
| `board.schema.json` | the `data` shapes under `$defs`: `IssueSummary`, `Issue`, `Comment`, `Attachment`, `Topic`, `Agent`, `Profile`, `Event`, `Briefing`, plus the list pages (`IssueList`, `CommentPage`, `InboxPage`, `Activity`, `Team`) and error details (`NextEmpty`, `HolderConflict`, `VersionConflict`) |

## Versions

Each schema carries `x-contract-version`, and every envelope reports `contract_version`
(`earmark.CONTRACT_VERSION`). They move together, by semantic versioning:

- **MINOR**: additive changes. A new field, a new optional input, a new enum value in an
  output-only list. Consumers ignore fields they do not know.
- **MAJOR**: anything that can break a consumer. Removing or renaming a field, changing its
  type or meaning, narrowing accepted input.

`tests/test_core_contract.py` validates live board output against `board.schema.json`, so a
shape change fails the suite until the schema (and its version) is updated with it.
