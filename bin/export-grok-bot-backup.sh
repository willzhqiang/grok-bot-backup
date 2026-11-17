#!/usr/bin/env bash
# Export Grok Bot durable data from the shared box computer.
# Includes: all agents' profiles/settings/avatars/memory/routines/attachments/reference,
#           conversation transcripts, user skills (workflows), workspace work products.
#           + full chat history of every assistant from the platform-synced search index
#             (conversations/<display-name>/YYYY-MM.md), + a copy of this program at its git commit.
# NOTE: unencrypted local backup. Credential files, auth databases, and opaque
# nested archives are excluded by filename rules. Ordinary files can still
# contain secrets. Never publish export archives or print secret file contents.
# Part of company-notes/grok-bot-backup; run directly.
set -euo pipefail
umask 077

REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}"

AGENT_DATA="${AGENT_DATA:-/home/box/agent-data}"
WORKSPACE="${WORKSPACE:-/workspace}"
OUT_ROOT="${OUT_ROOT:-${WORKSPACE}/exports}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="${OUT_ROOT}/grok-bot-export-${STAMP}"
ARCHIVE="${OUT_ROOT}/grok-bot-export-${STAMP}.zip"

# Credential files, authentication databases, and opaque archives are excluded
# unless INCLUDE_CREDENTIAL_FILES, INCLUDE_AUTH_DATABASES, or
# INCLUDE_OPAQUE_ARCHIVES is exactly 1. --include-private / --passphrase are
# still accepted for compatibility but do not disable those rules.
DEPRECATED_FLAG=0

die() { echo "ERROR: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --exclude)
      [[ $# -ge 2 ]] || die "--exclude requires a path"
      export BACKUP_EXCLUDE_PATHS="${BACKUP_EXCLUDE_PATHS:+${BACKUP_EXCLUDE_PATHS}:}$2"
      shift 2 ;;
    --include-private) DEPRECATED_FLAG=1; shift ;;
    --passphrase) DEPRECATED_FLAG=1; shift; if [[ $# -gt 0 ]]; then shift; fi ;;
    --passphrase=*) DEPRECATED_FLAG=1; shift ;;
    -h|--help)
      echo "Usage: $0"
      echo "  Backup workspace and AGENT_DATA with conservative credential exclusions."
      echo "  Env: INCLUDE_SUBAGENTS=1 also copies sand-subagent transcripts."
      echo "       INCLUDE_BULKY_DIRS=1 copies .venv*, .cache, node_modules, __pycache__, .git."
      echo "       INCLUDE_CREDENTIAL_FILES=1 copies env files, private keys, cookie/session/token files."
      echo "       INCLUDE_AUTH_DATABASES=1 copies authentication databases matched by filename."
      echo "       INCLUDE_OPAQUE_ARCHIVES=1 copies nested zip/tar/7z/rar archives without inspecting them."
      echo "       BACKUP_EXCLUDE_PATHS='secrets:other-dir' excludes those paths under every source root."
      echo "  --exclude PATH can be repeated and applies to workspace and AGENT_DATA."
      echo "  Output directories and previous export archives are always excluded."
      exit 0
      ;;
    *) die "unknown arg: $1" ;;
  esac
done

if [[ "$DEPRECATED_FLAG" -eq 1 ]]; then
  echo "NOTICE: --include-private/--passphrase are deprecated and ignored; credential filename rules still apply." >&2
fi

[[ -d "$AGENT_DATA" ]] || die "AGENT_DATA not found: $AGENT_DATA"
echo "WARNING: Unencrypted backup. Credential files, auth databases, and opaque archives are excluded by filename rules. Ordinary notes, settings, memory, and conversations can still contain secrets." >&2
echo "WORKSPACE=$WORKSPACE OUT_ROOT=$OUT_ROOT BACKUP_EXCLUDE_PATHS=${BACKUP_EXCLUDE_PATHS:-}" >&2
mkdir -p "$DEST"/{agents,shared,workspace,meta}

# Resolve symlink for clarity in manifest
AGENT_DATA_REAL="$(readlink -f "$AGENT_DATA" 2>/dev/null || echo "$AGENT_DATA")"

copy_filtered() {
  local src="$1" dst="$2" report="$3"
  shift 3
  if [[ -d "$src" ]]; then
    python3 -m grok_bot_backup.tree_copy "$src" "$dst" --report "$report" "$@"
  fi
}

# --- Preserve agent-data/agents entries, whether or not they are assistants. ---
# The agents/ root also contains organization/, people/, procedures/, top-level
# JSON files, and sometimes other platform data. Copy that tree with the shared
# credential, archive, and symlink rules, then count only genuine agent IDs.
[[ -d "$AGENT_DATA/agents" ]] || die "Agent directory missing: $AGENT_DATA/agents"
copy_filtered "$AGENT_DATA/agents" "$DEST/agents" "$DEST/meta/agents-copy.json" --label agent-data/agents

# --- Per-agent identity summary (only real UUID-named assistants) ---
shopt -s nullglob
AGENT_DIRS=("$AGENT_DATA"/agents/*/)
shopt -u nullglob

AGENT_COUNT=0
for agent_dir in "${AGENT_DIRS[@]}"; do
  [[ -d "$agent_dir" ]] || continue
  agent_id="$(basename "$agent_dir")"
  # Only actual UUID/hex agent directories; ignore organization, people, etc.
  [[ "$agent_id" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ || "$agent_id" =~ ^[0-9a-fA-F]{32}$ ]] || continue
  [[ -f "${agent_dir}profile.json" || -f "${agent_dir}settings.json" ]] || continue
  AGENT_COUNT=$((AGENT_COUNT + 1))
  out="$DEST/agents/${agent_id}"
  mkdir -p "$out"

  # The full agent tree has already been copied above. Only add a name index.

  # name for index
  if [[ -f "${agent_dir}profile.json" ]] && command -v python3 >/dev/null; then
    python3 - "$agent_dir" "$out" <<'PY'
import json,sys,pathlib
src=pathlib.Path(sys.argv[1])/"profile.json"
out=pathlib.Path(sys.argv[2])
try:
    data=json.loads(src.read_text(encoding="utf-8"))
    (out/"_export_name.txt").write_text(data.get("name") or "(unnamed)", encoding="utf-8")
except Exception as e:
    (out/"_export_name.txt").write_text(f"(unreadable: {e})", encoding="utf-8")
PY
  fi
done

# --- Shared user memory / projects (may be absent until first write) ---
for d in user-memory projects; do
  if [[ -d "$AGENT_DATA/$d" ]]; then
    copy_filtered "$AGENT_DATA/$d" "$DEST/shared/$d" "$DEST/meta/${d}-copy.json" --label "agent-data/$d"
  fi
done

# User-authored skills (workflows). Skip Cursor managed-skills / plugin-skills.
if [[ -d "$AGENT_DATA/workflows" ]]; then
  copy_filtered "$AGENT_DATA/workflows" "$DEST/shared/workflows" "$DEST/meta/workflows-copy.json" \
    --label agent-data/workflows
fi

# Conversation transcripts (jsonl) — useful portable history.
# sand-subagent directories are skipped unless INCLUDE_SUBAGENTS=1.
if [[ -d "$AGENT_DATA/agent-transcripts" ]]; then
  transcript_args=(--label agent-data/agent-transcripts)
  if [[ "${INCLUDE_SUBAGENTS:-0}" != "1" ]]; then
    transcript_args+=(--skip-dir-prefix sand-subagent-)
  fi
  copy_filtered "$AGENT_DATA/agent-transcripts" "$DEST/shared/agent-transcripts" \
    "$DEST/meta/transcripts-copy.json" "${transcript_args[@]}"
fi

# --- Workspace contents; the only unconditional exclusion is our own export output. ---
python3 -m grok_bot_backup.workspace "$WORKSPACE" "$DEST/workspace" "$OUT_ROOT" "$DEST/meta/workspace-copy.json"

# --- Full conversations of every assistant (platform-synced search index) ---
python3 -m grok_bot_backup.conversations --dest "$DEST/conversations" >/dev/null

# --- Platform memory drops (phase 1). Missing agents are not exported, never fatal. ---
python3 -m grok_bot_backup.memory_drop --into-grok "$DEST" >/dev/null

# Coverage matrix for this raw export (same probes as the readable pack).
python3 -m grok_bot_backup.coverage \
  --data-root "$AGENT_DATA" \
  --stats "$DEST/conversations/conversation-stats.json" \
  --out "$DEST"

# --- Copy of this backup program: repo source at HEAD + commit hash ---
python3 -m grok_bot_backup.program "$DEST/meta/backup-program" > "$DEST/meta/.program.out"
PROGRAM_COMMIT="$(sed -n 's/^PROGRAM_COMMIT=//p' "$DEST/meta/.program.out")"
rm -f "$DEST/meta/.program.out"

# Combine copy reports. Paths and reasons only; file contents are never printed.
python3 - "$DEST" <<'PY'
import sys
from pathlib import Path
from grok_bot_backup.sensitive import combine_reports, load_copy_reports, write_json
dest = Path(sys.argv[1])
reports = load_copy_reports(dest / "meta")
program = dest / "meta" / "backup-program"
if program.is_dir():
    reports.extend(load_copy_reports(program))
write_json(dest / "meta" / "exclusion-report.json", combine_reports(reports))
PY

# --- Manifest ---
python3 - "$DEST" "$AGENT_DATA" "$AGENT_DATA_REAL" "$STAMP" "$AGENT_COUNT" "$PROGRAM_COMMIT" <<'PY'
import json, os, sys, hashlib, pathlib
from grok_bot_backup.common import valid_agent_id
from datetime import datetime, timezone

dest = pathlib.Path(sys.argv[1])
agent_data = sys.argv[2]
agent_data_real = sys.argv[3]
stamp = sys.argv[4]
agent_count = int(sys.argv[5])
program_commit = sys.argv[6]
conv = json.loads((dest / "conversations" / "conversation-stats.json").read_text(encoding="utf-8"))
conv_total = sum(a["messages"] for a in conv["agents"])
workspace_report = json.loads((dest / "meta" / "workspace-copy.json").read_text(encoding="utf-8"))
exclusion_path = dest / "meta" / "exclusion-report.json"
exclusion_report = json.loads(exclusion_path.read_text(encoding="utf-8")) if exclusion_path.is_file() else {
    "excluded": [], "excluded_count": 0, "limitations": [], "policy": {}, "coverage_note": "",
}

def file_list(root: pathlib.Path):
    rows = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(root))
            st = p.stat()
            rows.append({"path": rel, "bytes": st.st_size})
    return rows

files = file_list(dest)
total_bytes = sum(f["bytes"] for f in files)

agents = []
agents_dir = dest / "agents"
if agents_dir.is_dir():
    for d in sorted(agents_dir.iterdir()):
        if not d.is_dir() or not valid_agent_id(d.name):
            continue
        if not ((d / "profile.json").is_file() or (d / "settings.json").is_file()):
            continue
        name = None
        np = d / "_export_name.txt"
        if np.exists():
            name = np.read_text(encoding="utf-8").strip()
        agents.append({"id": d.name, "name": name})

manifest = {
    "format": "grok-bot-box-export-v2",
    "program_commit": program_commit,
    "conversations": {"source": conv["source"], "cutoff_taipei": conv["cutoff_taipei"],
                      "total_messages": conv_total,
                      "per_agent": {a["name"]: a["messages"] for a in conv["agents"]}},
    "exported_at_utc": datetime.now(timezone.utc).isoformat(),
    "stamp": stamp,
    "agent_data_path": agent_data,
    "agent_data_realpath": agent_data_real,
    "agent_count": agent_count,
    "agents": agents,
    "included": [
        "agents/** (full local agents tree: UUID assistants AND non-assistant data)",
        "organization/, people/, procedures/, active-agent.json, audit-outbox.json when present",
        "agent SQLite files may be live copies; database consistency is not guaranteed",
        "shared/workflows (user skills)",
        "shared/user-memory, shared/projects (if present)",
        "shared/agent-transcripts (excluding sand-subagent-* unless INCLUDE_SUBAGENTS=1)",
        "workspace/ and AGENT_DATA trees (credential files, auth databases, opaque archives, escaping symlinks, explicit excludes, and default bulky directories omitted; see meta/exclusion-report.json and meta/workspace-copy.json)",
        "conversations/<name>/YYYY-MM.md full chat history from search-index.db (+ raw/, attachment-index.md, frozen supplement)",
        "meta/backup-program/ (repo source at HEAD + COMMIT)",
        "platform-memory/ human-readable RecallMemory drops when MEMORY_DROP exists",
        "agents/<id>/platform-memory/ and shared/platform-memory/ raw jsonl from those drops",
        "coverage.json and coverage.md (instructions, memory, routines, conversation, toolcalls, private, attachments, connectors)",
    ],
    "excluded": [
        "Credential files (env, private keys, cookie/session/token-store names, credential directories) unless INCLUDE_CREDENTIAL_FILES=1",
        "Authentication databases matched by filename unless INCLUDE_AUTH_DATABASES=1",
        "Opaque nested archives unless INCLUDE_OPAQUE_ARCHIVES=1; their contents are not inspected either way",
        "Symlinks that leave the source tree or point at an excluded path",
        "Filename rules do not detect secrets inside ordinary notes, settings, memory, or conversations",
        "Host-level files outside the included trees are not deliberately copied",
        "managed-skills/, plugin-skills/ (Cursor-managed)",
        "sand-subagent transcripts (default)",
        exclusion_report.get("coverage_note") or "missing=0 is not full platform availability",
    ],
    "workspace_copy": workspace_report,
    "exclusions": exclusion_report,
    "memory_drop": os.environ.get("MEMORY_DROP", os.path.join(os.environ.get("WORKSPACE", "/workspace"), "exports/memory-drop")),
    "include_subagents": os.environ.get("INCLUDE_SUBAGENTS", "0") == "1",
    "full_backup": not any(
        x.endswith("(bulky directory)") or x.endswith("(configured exclusion)")
        for x in workspace_report["skipped"]
    ) and os.environ.get("INCLUDE_SUBAGENTS", "0") == "1",
    "full_backup_note": "full_backup only reflects optional workspace bulky-directory and sand-subagent selections. It is not full platform availability. missing=0 does not mean every platform surface was exposed. Credential files, authentication databases, and opaque archives are omitted by default.",
    "notes": [
        "All assistants on one account share this computer filesystem.",
        "Per-agent identity/memory lives under agents/<id>/; skills under workflows/; scratch deliverables under /workspace.",
        "This archive is a portable backup for leaving Grok Bot lock-in; restoration is outside this tool.",
        "Credential filenames, auth databases, and opaque archives are excluded by default. See meta/exclusion-report.json. This does not prove ordinary files are free of secrets.",
        "Local memory files can be stale; verify platform-memory drop freshness.",
        "Platform memory: copied from MEMORY_DROP when provided. Missing drops are marked not exported.",
        "missing=0 is not full platform availability.",
    ],
    "file_count": len(files),
    "total_bytes": total_bytes,
    "files": files,
}

(dest / "meta" / "MANIFEST.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)

readme = f"""# Grok Bot box export ({stamp})

Exported from shared box computer.

## What's inside
- `agents/` — all available agent-tree data, including UUID assistants, organization/people/procedures folders and top-level metadata files
- `shared/workflows/` — user-authored skills
- `conversations/` — **full chat history of every assistant** (one folder per display name, monthly Markdown, Asia/Taipei); start at `conversations/README.md`
- `shared/agent-transcripts/` — locally available transcript JSONL with tool calls (may be stale)
- `shared/user-memory/`, `shared/projects/` — if present; check the source timestamps
- `platform-memory/` — human-readable platform memory from RecallMemory drops (phase 1); index at `platform-memory/memory-index.md`; gaps in `platform-memory/memory-pending.json`
- `coverage.md` — per-assistant coverage matrix. `unavailable` was probed and not found; `missing` means a required piece did not land in the export
- `agents/<id>/platform-memory/` — raw jsonl of that assistant's drop, if any
- `workspace/` — shared work products / deliverables from `/workspace`
- `meta/MANIFEST.json` — file inventory
- `meta/backup-program/` — the backup program used (repo source at commit `{program_commit}`)

## Excluded on purpose
Credential files, authentication databases, opaque nested archives, and symlinks that leave the source tree. See `meta/exclusion-report.json`. Filename rules do not detect secrets inside ordinary notes, settings, memory, or conversations. `missing=0` is not full platform availability. Host files outside selected trees and, by default, sand-subagent transcripts are also omitted. Current output roots, prior grok-bot-export-* and virtual-office-* folders/archives, explicit excludes and optional bulky directories are listed in `meta/workspace-copy.json`.

## Re-run
```bash
bin/export-grok-bot-backup.sh
# or:
INCLUDE_SUBAGENTS=1 INCLUDE_BULKY_DIRS=1 bin/export-grok-bot-backup.sh
```

Conversation messages: {conv_total} (snapshot {conv["cutoff_taipei"]} Asia/Taipei)
Platform memory (phase 1): see `platform-memory/memory-index.md`. Local `memory/` files may be outdated; inspect their timestamps.

Agents found: {agent_count}
Total files: {len(files)}
Total bytes: {total_bytes}
"""
(dest / "README.md").write_text(readme, encoding="utf-8")
print(f"Manifest written: {len(files)} files, {total_bytes} bytes, {agent_count} agents")
PY

# --- ZIP (Windows-compatible, retains symlinks as ZIP link entries) ---
mkdir -p "$OUT_ROOT"
find "$DEST" -type d -exec chmod 700 {} + ; find "$DEST" -type f -exec chmod 600 {} +
python3 -m grok_bot_backup.archive "$DEST" "$ARCHIVE"

# summary
python3 - "$DEST" "$ARCHIVE" <<'PY'
import json, pathlib, sys
dest=pathlib.Path(sys.argv[1])
archive=pathlib.Path(sys.argv[2])
m=json.loads((dest/"meta"/"MANIFEST.json").read_text(encoding="utf-8"))
print("EXPORT_DIR="+str(dest))
print("ARCHIVE="+str(archive))
print("ARCHIVE_BYTES="+str(archive.stat().st_size))
print("FILE_COUNT="+str(m["file_count"]))
print("TOTAL_BYTES="+str(m["total_bytes"]))
print("AGENT_COUNT="+str(m["agent_count"]))
print("PROGRAM_COMMIT="+str(m["program_commit"]))
print("CONVERSATION_MESSAGES="+str(m["conversations"]["total_messages"]))
for a in m["agents"]:
    print(f"AGENT\t{a['id']}\t{a.get('name')}")
PY
