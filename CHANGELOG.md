# Changelog

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
