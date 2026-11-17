# Grok Bot Backup

## Easiest way: import the backup Bot

**Don't want to set up the scripts yourself?** [**Import the ready-made Grok Bot backup assistant**](https://x.ai/bot/LvdHazZLidr0S3JJAdr8e).

After you import it, the Bot will guide you through the backup setup, asking **where to save your backups** (Google Drive or locally) and **how often to back up**. If you prefer to run or customize the source code yourself, follow the instructions below.

Unofficial, open-source export utilities for **Grok Bot** shared-box environments. Keep as much accessible data as possible in two forms: raw files and human-readable Markdown for handover to another AI assistant or a person.

**Status:** Successfully tested from a fresh clone on a real Grok Bot shared box (commit `667bdda`): raw and human-readable exports, complete available agent files, matching conversation counts, exclusion safeguards, and strict memory-file freshness checks all passed. This does not prove that the platform exposed every memory or guarantee compatibility with other environments. **Verify backups on your own box before relying on them.** This project is not a one-click restore tool; see [Recovery and migration](#recovery-and-migration-manual-not-one-click) for safe ways to use either export.

## Quick start

Requirements: Linux, Bash, Python 3.12+, Git, tar, and **filesystem access** to your Grok Bot shared box (`/home/box/agent-data` by default). It does not log into a web account or download your data from the cloud.

```bash
git clone https://github.com/company-notes/grok-bot-backup.git
cd grok-bot-backup
python3 -m unittest discover -s tests -v
bin/export-grok-bot-backup.sh         # Raw data + portable conversations
bin/export-virtual-office.sh         # Human-readable Markdown handover
```

Backups are created under `OUT_ROOT` (default: `/workspace/exports`). Each command prints its output paths. **Both commands produce a standard `.zip` archive** (alongside the uncompressed export folder), which can be opened with Windows File Explorer. No additional compression utility or Python install is needed to *extract* the ZIP on Windows. **Neither command encrypts its results.** Env files, private keys, cookie and session stores, token-store filenames, credential directories, authentication databases, and opaque nested archives are **omitted by default**. Ordinary notes, settings, memory, conversations, and source files are still copied and can contain secrets; filename rules do not read file contents. Restrictive Unix permissions do not replace encryption. Store the resulting archives wherever your own policy requires, and never commit real exports to a public repository.

**Disk-space planning:** there is no fixed minimum. Size depends on your source data. Allow room for the uncompressed export directory **and** its compressed archive, plus headroom for other agents using the box. If the disk is limited, consider excluding large regenerable directories or choosing an external `OUT_ROOT`.

### Optional assistant roster configuration

**No configuration file is required for a first run.** Leave `config.json` absent: the exporter discovers actual assistant IDs from the accessible platform data and verifies their available conversation and memory files.

Only create a config file if you want to explicitly list assistants whose memory drops you expect:

```bash
cp config.example.json config.json
# Edit config.json and replace the empty list with your real assistant UUIDs.
```

The shipped `config.example.json` uses `"expected_agents": []`, so copying it unchanged does **not** introduce nonexistent example assistants. For an explicitly tracked assistant, use the following format (replace the example UUID and display name):

```json
{
  "expected_agents": [
    {
      "id": "00000000-0000-0000-0000-000000000001",
      "name": "My assistant",
      "required": true
    }
  ]
}
```

Use `required: true` only for assistants you actually expect on your own box; `required: false` marks an optional assistant. The roster controls expectations for memory-drop reporting. It does not disable the credential, archive, or symlink rules below.

### Opening the ZIP on Windows

1. Copy the `grok-bot-export-<UTC>.zip` or `virtual-office-<UTC>.zip` file to a **private** location on your PC. These archives are **not password-protected**, even if they contain passwords or other secrets.
2. In Windows File Explorer, right-click the ZIP and choose **Extract All…**; select an empty, private folder. You can then open the `.md` documents with a text editor such as VS Code. The Virtual Office ZIP is organized for handoff and reading.
3. ZIP archives preserve the export folder as one top-level directory; do not extract their contents directly into a live Grok Bot filesystem. Extremely long paths and Unix-only filenames or symbolic links may require an advanced extractor and a Linux box for a fully faithful raw recovery.

ZIP uses standard compression with **ZIP64** support for files or archives over 4 GB and UTF-8 names for non-ASCII paths. Symlinks are saved **as Unix-style link entries without following their targets**, to prevent copying data outside the chosen backup scope. Windows built-in extraction does **not** re-create Unix symlinks or POSIX permissions reliably; this affects exact restoration, not the readability of ordinary documents. The original uncompressed export remains available for platform-specific recovery.

To verify a ZIP against a still-accessible Grok Bot box, pass it explicitly:

```bash
bin/verify-export.sh /workspace/exports/grok-bot-export-<UTC> --archive /workspace/exports/grok-bot-export-<UTC>.zip --strict
```

Old `.tar.gz` backups remain readable by supported archive tools and can still be passed to `verify-export.sh --archive`. The exporter no longer creates new `.tar.gz` files by default.

## What is excluded by default

Both the raw export and the Virtual Office export use the same rules on **every source root**: `AGENT_DATA` (agents, private files, `backup_pre`, projects, workflows, user memory, transcripts), `WORKSPACE`, memory-drop files that match a credential name, business-data trees copied into the readable pack, and the embedded program snapshot.

A path is excluded when a **path component or filename matches exactly** (case-insensitive), or when the extension is a known private-key or archive extension. Matching is not a substring search. Text and JSON that are kept are then scanned. Exact secret keys (`api_key`, `access_token`, `authorization`, `password`, `private_key`, and the same names with `_` or `-`) and high-confidence assignments such as `API_KEY=...`, `Authorization: Bearer ...`, and PEM private-key blocks are replaced with `<redacted>`. `max_tokens`, `sessionTimeout`, and ordinary sentences such as "Choose a strong password policy" or "the token parser" stay. Removed values are not printed.

These names and fields stay in the backup:

- `src/token_parser.py`, `tokenizer.py`, `design-tokens.css`
- `session-notes.md`, `notes/token-handling.md`
- `settings.json` fields such as `max_tokens` and `theme`
- `profile.json`, instructions, standing rules, memory JSONL, and conversations, after that scan
- ordinary `private/note.txt`

Memory JSONL keeps `date`, `tier`, `scope`, and `source`. The file modification time is preserved. Each scanned record is marked `sanitization` `inspected` or `redacted`.

These are omitted unless you opt in:

| Kind | Examples | Opt-in |
| --- | --- | --- |
| Env files | `.env`, `.env.local`, `feishu.env` | `INCLUDE_CREDENTIAL_FILES=1` |
| Private keys | `id_rsa`, `*.pem`, `*.key`, `*.p12` | `INCLUDE_CREDENTIAL_FILES=1` |
| Cookie, session, and token-store files | `cookies.txt`, `session.json`, `token.json`, `.netrc` | `INCLUDE_CREDENTIAL_FILES=1` |
| Credential directories | `secrets/`, `.ssh/`, `.aws/`, `sessions/`, `cookies/`, `token-store/` | `INCLUDE_CREDENTIAL_FILES=1` |
| Authentication databases | `cookies.sqlite`, `auth.db`, `Login Data`, `*.kdbx`, and their `-wal`/`-shm`/`-journal` sidecars | `INCLUDE_AUTH_DATABASES=1` |
| Opaque nested archives | `*.zip`, `*.tar`, `*.tar.gz`, `*.tgz`, `*.7z`, `*.rar`, including `migration/box-files.tar.gz` | `INCLUDE_OPAQUE_ARCHIVES=1` |

Opt-in copies those files **without opening them** and without removing secrets inside an archive. Previous `grok-bot-export-*` and `virtual-office-*` directories and archives stay excluded even when `INCLUDE_OPAQUE_ARCHIVES=1`.

Generic databases and other unscannable bytes are **omitted with no opt-in**. That includes `store.db`, `*.sqlite`, the raw `search-index-snapshot.db`, binary attachments, files larger than `SANITIZE_MAX_BYTES` (default 1 MiB), and files that are not valid UTF-8. They are listed as `unscannable database`, `unscannable binary`, `oversize`, or `unrecognized encoding`. The archive does not contain their bytes. Conversation text still comes from the search index as Markdown after redaction. Tool-call text can still be read from `store.db` while building that Markdown; the database file itself is not packed. Coverage for a folder that only contained those files is `partial` (present on disk, omitted from the archive), not a silent `ok`. `missing` is still the cell that fails `verify --strict`. `unavailable` still means the probe did not find the category on disk. **`missing=0` is not full platform availability**, and a clean scan is not a secret-free claim.

Symlinks that point outside the source tree, or at an excluded path, are dropped and never followed. In-tree links to ordinary files are stored as links. The ZIP writer also stores links as links and refuses archive names that are absolute or contain `..`.

`BACKUP_EXCLUDE_PATHS` and repeated `--exclude` add more skips on **every** source root. A relative pattern matches a path-component sequence at any depth: `secrets` matches `secrets/file` and `a/secrets/file`, and does not match `secrets-notes.md`. An absolute pattern matches that filesystem location. Output directories are always excluded.

```bash
# Default: credential files, auth databases, and nested archives are omitted.
bin/export-grok-bot-backup.sh
bin/export-virtual-office.sh

# Also skip a directory under the workspace and under AGENT_DATA:
bin/export-grok-bot-backup.sh --exclude custom-skip
bin/export-virtual-office.sh --exclude custom-skip

# Several relative or absolute paths (Linux path separator ':'):
BACKUP_EXCLUDE_PATHS="custom-skip:old-archives:/mnt/other-data" bin/export-grok-bot-backup.sh

# Unsafe opt-in. This puts the named files into the unencrypted archive.
INCLUDE_CREDENTIAL_FILES=1 INCLUDE_AUTH_DATABASES=1 INCLUDE_OPAQUE_ARCHIVES=1 bin/export-grok-bot-backup.sh
```

Each skip is a path plus a reason. The raw export writes `meta/exclusion-report.json` and `meta/workspace-copy.json`. The Virtual Office export writes `exclusion-report.json` and `workspace-copy.json`. The report counts `inspected_files`, `redacted` paths and key names, and `omitted_unscannable` paths. It does not contain file contents. Logs do not print secret values.

**These rules do not prove an archive is free of secrets.** A secret written as ordinary prose, inside an omitted database, or inside an opted-in archive is not removed. Do not describe the scan as a guarantee.

Both commands also **always** exclude the configured `OUT_ROOT` and `$WORKSPACE/exports` (even if `OUT_ROOT` points elsewhere), plus pre-existing `grok-bot-export-*` or `virtual-office-*` directories and their archives anywhere in the copied workspace. Large build directories (`.venv*`, `.cache`, `node_modules`, `__pycache__`, `.git`) are skipped in the workspace unless `INCLUDE_BULKY_DIRS=1`. Sand-subagent transcripts are skipped unless `INCLUDE_SUBAGENTS=1`.

```bash
INCLUDE_BULKY_DIRS=1 INCLUDE_SUBAGENTS=1 bin/export-grok-bot-backup.sh
INCLUDE_BULKY_DIRS=1 bin/export-virtual-office.sh
```

**Warning:** A finished export still cannot include information the local platform does not expose. See memory preparation below. `missing=0` is not full platform availability.

## Staging export and handover

`AGENT_DATA`, `WORKSPACE`, `MEMORY_DROP`, and `OUT_ROOT` are the source roots. Point them at a staging directory you built. The tools do not open any other platform root, and they do not need a live agent, X-Monitor, or the rest of a shared box. No extra switch is required.

```bash
STAGE=$(mktemp -d)
mkdir -p "$STAGE/agent-data/agents/<uuid>" "$STAGE/agent-data/handover" \
  "$STAGE/workspace" "$STAGE/memory-drop/agents/<uuid>" "$STAGE/out"
# Copy only the metadata, memory JSONL, and handover you intend to export.
# Add a minimal search-index.db if you want conversation Markdown.
export AGENT_DATA="$STAGE/agent-data"
export WORKSPACE="$STAGE/workspace"
export MEMORY_DROP="$STAGE/memory-drop"
export OUT_ROOT="$STAGE/out"
bin/export-grok-bot-backup.sh
bin/export-virtual-office.sh
```

Put the handover self-report in `$AGENT_DATA/handover/` **before** those commands. It is copied, after the same scan, to `handover/` in the raw archive and `00-shared/handover/` in the Virtual Office archive. Both get `HANDOVER_STATUS.md`. If the directory is missing or has no files, that status says `not supplied` and `not a complete handover`, and `coverage.md` repeats the gap. An empty handover is not described as complete.

## Local synthetic verification

The regression tests use temporary trees and synthetic strings only. They do not read a live account or a private backup.

```bash
python3 -m compileall -q grok_bot_backup tests
bash -n bin/*.sh
python3 -m unittest discover -s tests -v
python3 -m unittest tests.test_credential_filter tests.test_content_sanitize -v
```

## Prepare platform memory *before* backup

The program can copy local memory files, but it **cannot invoke each assistant's internal RecallMemory tool**. An assistant must explicitly export its own currently available memory first. Do this for **every assistant** on the shared box and for shared (`scope=user`) memory where permitted.

Set `MEMORY_DROP` to the prepared directory. Default: `$WORKSPACE/exports/memory-drop`. Use this structure:

```text
memory-drop/
├── shared-user/
│   └── memory-user.jsonl
└── agents/
    └── <agent-uuid>/
        ├── memory-profile.jsonl
        └── memory-agent.jsonl
```

The program accepts any `*.jsonl` under `agents/<agent-uuid>/`. Each line must be one JSON object, preserving the **full original memory text**, for example:

```json
{"date":"2026-10-22","tier":"profile","scope":"agent","source":"RecallMemory","fact":"Example persistent fact"}
```

Use `tier` of `profile`, `log`, or `note`, with `scope` and `source` reflecting what the assistant actually retrieved. Do not invent facts or claim completeness if recall was partial. The exporter also preserves the JSONL files byte-for-byte alongside readable Markdown.

Copy this request to **each** assistant (adjust for its tool access):

> Use your available RecallMemory interface to retrieve all memory facts you are permitted to see, covering every available tier and scope, including older records. Export the complete facts—not summaries—to valid UTF-8 JSONL, one JSON object per line, under `$MEMORY_DROP/agents/<your-agent-uuid>/`. Include `date`, `tier`, `scope`, `source`, and a nonempty `fact`. If you can export shared user memory, write it to `$MEMORY_DROP/shared-user/memory-user.jsonl`. Report tool limitations and the number of facts exported. Only if you actually searched and found no facts, create the documented `memory-status.json` confirmation with the correct ID and UTC check time. Do not modify source memories or claim a completed export without checking.

### Confirming an assistant with genuinely no memory

An **empty JSONL file is not proof that the memory export ran**. If all exported JSONL files for an assistant contain zero valid `fact` records, verification now prints **WARN**; `--strict` prints **FAIL**. Invalid JSONL or missing/blank `fact` values are also reported.

If the assistant has **actually checked every accessible memory tier and found zero facts**, write an additional `memory-status.json` beside its JSONL files. For example:

```json
{
  "status": "no_memories",
  "agent_id": "00000000-0000-0000-0000-000000000001",
  "checked_at_utc": "2026-10-23T00:00:00Z"
}
```

The `agent_id` must match that assistant's UUID. `checked_at_utc` must be the genuine, timezone-aware UTC timestamp of the completed check, within `MEMORY_MAX_AGE_DAYS` of the backup (default: two days). The exporter preserves this separate file and the verifier checks the declaration. **Only use it after explicitly confirming there are no retrievable memories; never generate a declaration just to make `--strict` pass.** If facts are available, export them instead.

Do not leave `memory-status.json` in place after facts have been exported. When a drop contains nonempty JSONL facts and a `no_memories` declaration together, `verify --strict` fails. Delete the stale declaration before exporting. The tool keeps the filename `memory-status.json` so the contradiction stays visible.

Shared user memory may use the same mechanism under `shared-user/memory-status.json`, substituting `"scope": "user"` for `agent_id`. Shared user memory is optional when not accessible, but a *present yet empty* shared memory JSONL file is not considered a successful export without the declaration.

**Group chats are not individual assistants.** A group does not require a separate RecallMemory export to pass `--strict`, and the verifier prints `SKIP MEMORY` to make that exception clear. If group memory files are actually provided, they are checked for valid facts, freshness, and empty-export declarations as well. Individual assistants participating in a group still require their own memory exports.

**Freshness matters.** The verifier uses the JSONL files' modification times as the export-time proxy; the raw files are copied with their timestamps preserved. An old `memory-drop` can make a backup look complete even though new memories are missing. Do not reset timestamps to pretend old exports are new.

## Verify the backup

### ZIP archive verification

Each finished export now includes a `.zip` archive and an uncompressed directory. The normal folder check verifies contents against the live source index; add `--archive /path/to/file.zip` to also check the ZIP's file integrity and confirm it includes the backup program snapshot. Verification remains able to inspect legacy `.tar.gz` archives.


```bash
bin/verify-export.sh /workspace/exports/grok-bot-export-<UTC>
bin/verify-export.sh /workspace/exports/virtual-office-<UTC>

# Default max memory age is 2 days; stale/missing drops show WARN.
MEMORY_MAX_AGE_DAYS=2 bin/verify-export.sh /path/to/export

# Require memory drops to be present and recent:
MEMORY_MAX_AGE_DAYS=2 bin/verify-export.sh /path/to/export --strict

# Optional: require at least one conversation on a particular day:
bin/verify-export.sh /path/to/export --date YYYY-MM-DD

# Print the coverage matrix (also written as coverage.md during export):
bin/verify-export.sh /path/to/export --coverage

# Strict: missing matrix cells fail. Unavailable cells warn and do not fail.
bin/verify-export.sh /path/to/export --coverage --strict
```

The verifier prints each available assistant's memory-file last-modified timestamp explicitly in **UTC**, and the age relative to the backup snapshot. File modification time is only a proxy for when the memory was exported, not proof that the retrieved content is complete. It lists missing drops by assistant name. A missing, stale, malformed, or unconfirmed empty drop is a **WARN** (exit 0 if other checks pass); with `--strict` it becomes **FAIL** (nonzero exit). A `no_memories` declaration next to nonempty facts is also a **FAIL** under `--strict`. Groups are exempt from mandatory assistant-memory export; a genuinely empty export needs the explicit, recent `memory-status.json` confirmation described above. This check does not prove that all memories have been retrieved—it detects absent or aging export files.

### Coverage matrix

Each Virtual Office export, and each raw export, writes `coverage.json` and `coverage.md`. Rows are assistants. Columns are `instructions`, `memory`, `routines`, `conversation`, `toolcalls`, `private`, `attachments`, `connectors`, `business-data-tree`, `ui-only-secrets`, and `standing-rules`. Each cell is `ok`, `partial`, `unavailable`, or `missing`.

With no `config.json` roster, `memory-index.md` lists agent ids discovered under the memory drop's `agents/` directory in **Per assistant**. Only directories outside that set are **Additional drops**.

| Cell | Meaning |
| --- | --- |
| `ok` | The export captured this for the assistant |
| `partial` | Something was captured, with a gap (for example only inferred routines, or facts plus a stale empty-memory declaration) |
| `unavailable` | The probes ran and the platform did not have this on disk |
| `missing` | A required piece was absent from the export |

`verify --coverage` prints the matrix and `COVERAGE missing=<n> unavailable=<n>`. **`missing=0` is not full platform availability.** Unavailable cells were probed and not found on disk; that does not prove the platform has no such data. With `--strict`, a `missing` cell fails the run. An `unavailable` cell prints **WARN** and does not, by itself, fail `--strict`. A platform-hosted system prompt is `unavailable` (see instructions below), not a permanent failure. Credential files that were omitted on purpose make a private or business-data cell `partial` when other files remain, or `partial` when the only files were excluded. That is not a `missing` memory failure.

Inspect the raw export's `meta/MANIFEST.json`, `meta/workspace-copy.json`, and `platform-memory/memory-index.md` for included data, exclusions, and RecallMemory status. The readable export has `memory-index.md`, `conversation-index.md`, and `workspace-copy.json`. Verification compares conversation messages against the live search index and checks exported notes and memory drops; check the source before assuming universal compatibility.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `AGENT_DATA` | `/home/box/agent-data` | Root of the local platform agent data |
| `WORKSPACE` | `/workspace` | Shared workspace |
| `OUT_ROOT` | `$WORKSPACE/exports` | Backup destination; automatically excluded |
| `BACKUP_EXCLUDE_PATHS` | empty | Optional colon-separated paths to omit under every source root |
| `INCLUDE_BULKY_DIRS` | `0` | Set `1` to include large dependency/cache folders in the workspace |
| `INCLUDE_SUBAGENTS` | `0` | Set `1` to include sand-subagent transcripts in the raw backup |
| `INCLUDE_CREDENTIAL_FILES` | `0` | Set `1` to copy env files, private keys, cookie/session/token files, and credential directories. Unsafe |
| `INCLUDE_AUTH_DATABASES` | `0` | Set `1` to copy authentication databases matched by filename. They are not inspected |
| `INCLUDE_OPAQUE_ARCHIVES` | `0` | Set `1` to copy nested zip/tar/7z/rar archives. Contents are not filtered |
| `SANITIZE_MAX_BYTES` | `1048576` | Largest file that is scanned. Larger files are omitted |
| `MEMORY_DROP` | `$WORKSPACE/exports/memory-drop` | Pre-exported platform memory files |
| `MEMORY_MAX_AGE_DAYS` | `2` | Max age of a memory drop before WARN / strict FAIL |
| `GROK_BACKUP_CONFIG` | `<repo>/config.json` | Optional expected agent roster |
| `GROK_BACKUP_USER_LABEL` | `User` | Conversation label for the human side |

Older `NUWA_BACKUP_CONFIG` and `NUWA_BACKUP_USER_LABEL` environment variables remain accepted for compatibility, but new setup should use the `GROK_BACKUP_*` names.

## Archive contents

### Raw export: `grok-bot-export-<UTC>/`

- `agents/`: accessible assistant and non-assistant source files after the exclusions above, including ordinary `private/` notes, settings, attachments, and databases such as `store.db`, plus `organization/`, `people/`, `procedures/`, `active-agent.json`, and `audit-outbox.json` when those are ordinary files. Non-assistant entries are **never counted as assistants**. Credential files and nested archives inside `private/backup_pre/` or `migration/` are omitted by default.
- `shared/`: local shared memory, projects, workflows, and optionally frozen subagent transcripts.
- `workspace/`: shared workspace files, subject to the same credential, archive, symlink, and configured exclusions.
- `conversations/`: monthly human-readable Markdown and raw JSONL from the platform-synchronized SQLite index. Non-assistant identifiers (such as `organization`, `people`, `procedures`) are not treated as assistants or displayed as `Unknown assistant`. The raw archive also keeps a consistent `conversations/search-index-snapshot.db` SQLite copy, including platform records that are not assistant conversations.
- `platform-memory/`: readable RecallMemory export, when supplied. `memory-pending.json` lists assistants that still have no drop.
- `coverage.md`: the same coverage matrix as the readable export, probed from the box at export time.
- `meta/`: file inventory, workspace omissions, and this program's git revision.

### Human-readable export: `virtual-office-<UTC>/`

- `00-shared/`: available projects, shared memory, skills, **workspace outputs**.
- `01-assistants/<name>/`: `instructions.md` (full prompt, or a reconstructed stand-in), `routines.md` (schedules or an unavailable marker), `connectors.md` (names and auth status only), profile, conversations, memory, and notes, private files, references, or attachments when those sources exist.
- `coverage.md`: the matrix described above. `memory-pending.json` lists assistants that still need a memory drop.
- `02-groups/<name>/`: group information and conversations.
- `99-backup-program/`: source code version and conversation statistics.
- `memory-index.md`, `conversation-index.md`, `workspace-copy.json`: export indexes.

Textual labels and directory names generated by this utility are in English. Original data and messages are **not translated**.

## Recovery and migration (manual, not one-click)

**Can I restore my Bot by copying the raw backup back? Not necessarily.** There are **two complementary backups**, and they solve different recovery problems:

| Export | Best for | What it does **not** do |
| --- | --- | --- |
| **Human-readable Virtual Office** (`virtual-office-<UTC>/`) | Handing off an assistant's role, instructions, notes, memory exports, attachments and conversations to a person or a different AI assistant | Automatically recreate a Grok Bot account, assistant IDs, permissions, tools or executable memory |
| **Raw Grok Bot export** (`grok-bot-export-<UTC>/`) | Preserving the on-box source files so an operator can recover individual files or plan a platform-specific migration | Guarantee that replacing the live platform directories/database will recreate the original running Bot |

### 1. Inspect and extract a backup **outside** the live platform

Use an archive **you trust**. Backup archives may include secrets and symbolic links; inspect their contents and destination before extracting. These commands extract to a separate, private directory, **not** to `/workspace` or `/home/box/agent-data`:

```bash
umask 077
mkdir -p "$HOME/grok-bot-recovery"
python3 -m zipfile -l /path/to/grok-bot-export-YYYYMMDDTHHMMSSZ.zip
python3 -m zipfile -e /path/to/grok-bot-export-YYYYMMDDTHHMMSSZ.zip "$HOME/grok-bot-recovery"
```

Replace the example archive path with your real backup. The archive creates a `grok-bot-export-<UTC>/` subdirectory. For the readable export, use its corresponding `virtual-office-<UTC>.zip` archive in the same manner. On Windows, use File Explorer → **Extract All…** instead. Inspect untrusted archives before extracting; Python's basic unzip example is for trusted backups and is **not a substitute for secure extraction checks**. You can also use an already-extracted export directory rather than a `.zip`.

For existing **legacy `.tar.gz`** backups (not produced by current versions), keep using `tar -tzf <archive>` to inspect and `tar -xzf <archive> -C "$HOME/grok-bot-recovery"` to extract on a trusted Unix machine.

**Never run** `tar -xzf ... -C /home/box/agent-data` or blindly copy the entire raw export over a live Grok Bot installation. The backup's directory layout is an export layout, **not** a drop-in image of the platform's original filesystem.

### 2. If moving to another AI assistant (recommended first path)

1. Start with the **Virtual Office** export. Read `README.md`, `roster.md`, and `conversation-index.md`; find the assistant under `01-assistants/<name>/`.
2. Give the new assistant the relevant `instructions.md`, `profile.md`, `memory/`, `routines.md`, `connectors.md`, and monthly conversation Markdown, plus `notes-and-profiles/`, `references/`, or `routines/` when those folders exist, and any shared knowledge under `00-shared/`. Copy attachments or workspace deliverables only as needed. Read `coverage.md` before assuming a section is complete.
3. Ask it to **summarize what it can actually understand and import**, map tasks and permissions in the new environment, and confirm missing memory or tools. Importing `memory/*.jsonl` into a new memory system requires that system's own supported import process; reading a file does not create native memory automatically.
4. Recreate integrations, credentials, schedules, access controls, and approvals in the **new** system after reviewing them. Do not hand raw password files or tokens to a new assistant without checking that they are still needed and securely handled.

This is a **handover/reconstruction**, not an automated transfer of an assistant's internal state.

### 3. If recovering files or trying to rebuild Grok Bot

1. First keep an untouched copy of the archive and make a **separate backup of the current box**. Record the Grok Bot/host versions, assistant IDs, and any differences in filesystem layout. Prefer an isolated test box or a fresh supported installation, not production.
2. Inspect `meta/MANIFEST.json`, `meta/workspace-copy.json` and `meta/backup-program/COMMIT` (if present). Check what was excluded or unavailable; optional `INCLUDE_BULKY_DIRS` and `INCLUDE_SUBAGENTS` may affect coverage.
3. Recover ordinary **workspace files** selectively from `workspace/`. Original assistant files and non-assistant platform metadata live under `agents/`; other shared source material lives under `shared/`. Map each file to the correct destination for **your exact platform version** before copying.
4. Treat databases specially: `conversations/search-index-snapshot.db` is a consistent **exported snapshot** of the search index, not a verified replacement for whatever databases the destination Grok Bot version currently uses. Other raw `*.db` files may have been copied while in use and may require application-specific consistency checks, matching IDs or migrations. **Do not overwrite live SQLite databases, their WAL/SHM files, or running agent directories.** Follow the platform's supported shutdown/restore/import procedure if one exists.
5. Treat `agents/<id>/platform-memory/*.jsonl` and `shared/platform-memory/` as **exported memory data**, not as a working RecallMemory database. The platform's memory API may require separate, authenticated re-import, and some memories might never have been accessible to the exporter.
6. Test recovered content, permissions, conversations, attachments, integrations and scheduled tasks in isolation **before** considering a cutover. If a compatible platform import/restore mechanism is unavailable, keep the raw backup for reference and use the human-readable version for manual reconstruction.

**There is no `restore` command in this repository.** We have tested **export and verification**, not automatic restoration into a new or existing Grok Bot instance. A successful `verify-export.sh --strict` means the available exported records met the checks (including memory-file freshness); it does **not** certify that all platform state exists, that every memory was retrievable, or that a full restore will work. Verification normally also compares against a *live source index*, so that check may be unavailable after the original box is lost.

### Restore safety

Backup data is **unencrypted** and may include credentials, confidential conversations, private assistant folders and attachments. Keep staged files under restricted access; review or rotate exposed credentials as appropriate, and securely remove temporary copies once the recovery is complete. Never publish a real export archive.

## Limitations

- Platform-provided `managed-skills/` and `plugin-skills/` (for example built-in skills installed by the host) are **intentionally not included**. Only user-authored `workflows/` are backed up. Built-in skills belong to the host's installed environment and may need to be reinstalled separately. This is an explicit scope limitation, not a statement that those files are inaccessible.
- The platform search index generally provides visible conversation text. Tool calls are probed in both `agent-transcripts/<id>/<id>.jsonl` and `agents/<id>/store.db` (`transcript_entries`). A hit is written to `conversations/raw/transcript.jsonl` and `conversations/transcript.md`. A miss leaves `tool calls: unavailable` in `conversations/README.md`.
- Full system prompts are platform-hosted. `instructions.md` is always `source: reconstructed`: the profile description, the first handoff copied verbatim, and standing rules when a standing-rules file or field exists. The instructions cell is `unavailable`.
- Routines come first from each memory drop's `routines.json`. Each item keeps `source` of `platform`, `box-cron`, `memory-drop`, or `inferred`. The raw file is copied to `routines/routines.json` and summarized in `routines.md`. Disk `crontab.txt`, `cron.json`, and schedule directories fill gaps. Inferred rows are guesses. If every source misses, `routines.md` says unavailable and the export prints **WARN**.
- `memory-export-notes.json` is not a memory declaration. Delete a stale `memory-status.json` when facts exist; do not rename it.
- Generic SQLite files, including `store.db` and the raw search-index snapshot, are omitted from the archive because they cannot be scanned safely. Tool-call rows can still be extracted into text and then redacted. A database copied while applications are running is not a consistent snapshot even when it is read for that text.
- High-confidence scans redact exact secret keys and assignment patterns. Ordinary prose can still contain a secret. Opting in to opaque archives copies them whole without inspection. `missing=0` is not full platform availability, and a finished scan is not a secret-free claim.
- Local memory can be stale. The memory-drop process requires agent cooperation; exported-fact completeness is not technically guaranteed. `MEMORY_MAX_AGE_DAYS` defaults to 2. `--strict` still fails when a memory drop is missing, stale, empty without a real declaration, or contradictory.
- Different Grok Bot versions may use different internal storage layouts. Paths must be checked on the target box.
- This is an **export**, not an automatic restore workflow. Recovery/rehydration into a new provider must be planned separately.

## Local installation and synthetic verification

Use a public clone and the synthetic fixtures in `tests/`. This does not read a live agent, a private backup, rclone, or an account, and it does not change schedules.

```bash
git clone https://github.com/company-notes/grok-bot-backup.git
cd grok-bot-backup
python3 -m venv .venv
. .venv/bin/activate
python3 -m compileall -q grok_bot_backup tests
bash -n bin/*.sh
python3 -m unittest discover -s tests -v
```

No third-party packages are required. Python 3.12 or newer is the version used in CI. The credential regression test builds a temporary `AGENT_DATA` and `WORKSPACE`, runs both export commands, and checks that synthetic secret strings are absent from the ZIP members, the manifest, the exclusion report, and the command output. It also checks that ordinary token-related source, session notes, private notes, settings, memory, and conversations remain, and that a missing memory drop still fails `verify --strict`.

To inspect one synthetic run yourself, the same layout the test builds is enough: point `AGENT_DATA`, `WORKSPACE`, `OUT_ROOT`, and `MEMORY_DROP` at temporary directories you created, run `bin/export-grok-bot-backup.sh` and `bin/export-virtual-office.sh`, then search the ZIP for a marker that you put only in an excluded file. Do not point those variables at a real home directory or a real backup for this check. Checking a private computer's real export is a separate later step; do not upload that archive or paste secret file contents into an issue.

```bash
python3 -m unittest tests.test_credential_filter tests.test_content_sanitize -v
python3 -m unittest discover -s tests -v
```

See `.github/workflows/ci.yml` for continuous integration, and [LICENSE](LICENSE) for the MIT license.
