# Changelog

## Content scan — 2026-11-17

- Redact exact secret keys in JSON settings, profiles, and config, including nested `api_key`, `access_token`, `authorization`, `password`, and `private_key`. Keep `max_tokens` and ordinary technical wording.
- Redact high-confidence text assignments, bearer headers, and PEM private-key blocks in notes, memory, conversations, and source. Do not print the removed values.
- Omit generic databases, binary attachments, oversize files, and unrecognized encodings from the archive. Record them as unscannable. Do not copy `store.db` or the raw search-index snapshot.
- Preserve memory `date`, `tier`, `scope`, `source`, and file mtime, and mark each scanned record `inspected` or `redacted`.
- Copy `$AGENT_DATA/handover` into raw `handover/` and Virtual Office `00-shared/handover/`. A missing handover is an explicit gap, not a complete export.
- `MEMORY_MAX_AGE_DAYS` remains 2. `verify --strict` still fails when a memory drop is missing.


## Credential exclusions — 2026-11-17

- Exclude env files, private keys, cookie and session stores, token-store filenames, and credential directories by exact name on every source root: agent data, workspace, shared projects, workflows, transcripts, Virtual Office conversion, and the embedded program snapshot.
- Exclude opaque nested archives and filename-matched authentication databases unless `INCLUDE_OPAQUE_ARCHIVES=1` or `INCLUDE_AUTH_DATABASES=1`. Those opt-ins copy the bytes and do not inspect them.
- Apply `BACKUP_EXCLUDE_PATHS` and `--exclude` under every source root, matching path components rather than substrings. `token_parser.py` and `session-notes.md` stay.
- Omit symlinks that leave the source tree or point at an excluded path. Do not follow them.
- Record paths and reasons in `exclusion-report.json`. Do not write file contents into logs, manifests, or that report.
- State that `missing=0` is not full platform availability. `MEMORY_MAX_AGE_DAYS` still defaults to 2, and `--strict` still fails when a memory drop is missing.


## Coverage matrix — 2026-11-01

- Add `coverage.json` and `coverage.md` for each assistant across instructions, memory, routines, conversation, tool calls, private files, attachments, and connectors. Cells are `ok`, `partial`, `unavailable`, or `missing`.
- `verify --coverage` prints the matrix. `--strict` fails on `missing` and warns on `unavailable`.
- Write `instructions.md` as `source: reconstructed` (description, verbatim first handoff, standing rules). The instructions coverage cell stays `unavailable`. The matrix also has `business-data-tree`, `ui-only-secrets`, and `standing-rules`.
- Copy each memory drop's `routines.json` into `routines/` and render `routines.md` with source `platform`, `box-cron`, `memory-drop`, or `inferred`. Disk crontab and schedule files fill gaps. A drop that contains routines is not marked unavailable.
- When no `config.json` roster exists, `memory-index.md` lists discovered agent ids under Per assistant. Only directories outside that roster go under Additional drops.
- `memory-export-notes.json` is not a supported declaration. `verify --strict` fails if that file is present, and if facts coexist with `memory-status.json`.
- Probe tool calls from `agent-transcripts/<id>/<id>.jsonl` and `agents/<id>/store.db` `transcript_entries`. Hits are saved as `conversations/raw/transcript.jsonl` and `conversations/transcript.md`. Misses say `tool calls: unavailable`.
- Fail `verify --strict` when JSONL facts and a `no_memories` `memory-status.json` are both present. The status filename is left unchanged.
- Write `memory-pending.json` and a `Memory export: N/M, missing: …` line on the memory index.


## ZIP default — 2026-10-23

- Both raw and Virtual Office exports now produce Windows-friendly `.zip` archives using Python's standard library, with ZIP64 and UTF-8 support.
- Preserve symlink entries without dereferencing them; use the original file copies for Unix-specific recovery.
- The verifier accepts and checks new ZIP archives and retains support for older `.tar.gz` backups.
- Add extraction instructions for Windows and regression tests for ZIP content.


## 2026-10-22 — Full agent-tree and previous-export safeguards

- Preserve all accessible entries under `agent-data/agents/`, including non-assistant platform folders and root-level metadata, without counting them as assistants.
- Ignore organization/people/procedures identifiers in assistant conversation lists and verification rows, while retaining the full SQLite search index snapshot in raw archives.
- Exclude legacy `grok-bot-export-*` and `virtual-office-*` directories and archives anywhere in the workspace, regardless of the configured output directory.
- Add end-to-end tests that run the actual raw export command and verify source preservation, conversation counts, and archive provenance.
- Keep built-in managed/plugin skills outside the export scope; user-defined workflows are preserved.


## Initial public release — 2026-10-22

- Export complete, accessible Grok Bot data as a raw archive and a human-readable Virtual Office package.
- Export platform-synced conversation history, available RecallMemory drops (phase 1), and backup verification reports.
- Configure site-specific assistant names/IDs in `config.json`, with paths controlled by environment variables.
- Add `INCLUDE_BULKY_DIRS` to optionally include large workspace dependencies and caches.
