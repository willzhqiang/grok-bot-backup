"""Phase-1 platform memory: copy peer/self RecallMemory drops into a backup.

Drops live under MEMORY_DROP (default WORKSPACE/exports/memory-drop).
Missing drops are recorded as not exported and never fail the backup.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
from datetime import datetime, timezone

from .common import AGENT_DATA, lock_down, safe_name, valid_agent_id, write

MEMORY_DROP_DEFAULT = pathlib.Path(os.environ.get("WORKSPACE", "/workspace")) / "exports" / "memory-drop"

# (id, display name, required). Loaded from a site config file, never hardcoded.
# Set GROK_BACKUP_CONFIG=/path/to/config.json (default: repo/config.json).
# See config.example.json.
def _load_expected_agents() -> list[tuple[str, str, bool]]:
    cfg = pathlib.Path(os.environ.get(
        "GROK_BACKUP_CONFIG",
        os.environ.get("NUWA_BACKUP_CONFIG",
                       str(pathlib.Path(__file__).resolve().parent.parent / "config.json"))))
    if not cfg.is_file():
        return []
    data = json.loads(cfg.read_text(encoding="utf-8"))
    return [(a["id"], a.get("name", a["id"]), bool(a.get("required", True)))
            for a in data.get("expected_agents", [])]


EXPECTED_AGENTS: list[tuple[str, str, bool]] = _load_expected_agents()

TIER_ORDER = ("profile", "log", "note")
TIER_TITLE = {
    "profile": "Persistent (profile)",
    "log": "Assistant-owned (log)",
    "note": "Assistant-owned (note)",
}


def drop_root() -> pathlib.Path:
    return pathlib.Path(os.environ.get("MEMORY_DROP", str(MEMORY_DROP_DEFAULT)))


def load_jsonl(path: pathlib.Path) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            rows.append({
                "date": "",
                "tier": "note",
                "scope": "",
                "fact": f"(Line {i} contains invalid JSON; parsing skipped, original preserved in JSONL)",
                "source": "unparsed",
            })
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _tier_key(row: dict) -> str:
    tier = str(row.get("tier") or "").strip()
    scope = str(row.get("scope") or "").strip()
    if tier in TIER_TITLE:
        return tier
    if scope == "conversation" or tier == "conversation":
        return "conversation"
    return tier or "unknown"


def render_markdown(rows: list[dict], title: str, intro: str) -> str:
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        buckets.setdefault(_tier_key(row), []).append(row)
    order = list(TIER_ORDER) + ["conversation"]
    order += [k for k in buckets if k not in order]
    parts = [f"# {title}", "", intro.rstrip(), ""]
    if not rows:
        parts.append("(no facts)")
        parts.append("")
        return "\n".join(parts)
    titles = {
        **TIER_TITLE,
        "conversation": "Conversation scope (conversation)",
        "unknown": "Unspecified tier",
    }
    for key in order:
        group = buckets.get(key) or []
        if not group:
            continue
        parts.append(f"## {titles.get(key, key)}")
        parts.append("")
        for row in group:
            date = str(row.get("date") or "(no date)")
            scope = str(row.get("scope") or "(no scope)")
            source = str(row.get("source") or "self")
            fact = str(row.get("fact") or "").rstrip()
            parts.append(f"### {date} · {scope} · {source}")
            parts.append("")
            parts.append(fact if fact else "(blank)")
            parts.append("")
    return "\n".join(parts)


def _status_file(directory: pathlib.Path) -> pathlib.Path:
    """Optional explicit attestation for an intentionally empty memory export."""
    return directory / "memory-status.json"


def _agent_drop_files(agent_dir: pathlib.Path) -> list[pathlib.Path]:
    """JSONL memory files plus routines.json. Status files stay separate."""
    if not agent_dir.is_dir():
        return []
    preferred = ["memory-profile.jsonl", "memory-agent.jsonl"]
    found = []
    for name in preferred:
        path = agent_dir / name
        if path.is_file():
            found.append(path)
    for path in sorted(agent_dir.glob("*.jsonl")):
        if path not in found:
            found.append(path)
    routines = agent_dir / "routines.json"
    if routines.is_file() and routines not in found:
        found.append(routines)
    return found


def _memory_jsonl_files(files: list[pathlib.Path]) -> list[pathlib.Path]:
    return [path for path in files if path.suffix == ".jsonl"]


def _routine_count(path: pathlib.Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("routines", "items", "schedules", "jobs"):
            if isinstance(data.get(key), list):
                return len(data[key])
        if any(key in data for key in ("name", "cron", "schedule", "prompt")):
            return 1
    return 0


def _display_folder(agent_id: str, id_to_name: dict[str, str] | None) -> tuple[str, str]:
    """Return (top folder '01-assistants'|'02-groups', safe display folder name)."""
    name = None
    if id_to_name and agent_id in id_to_name:
        name = id_to_name[agent_id]
    if not name:
        for aid, label, _req in EXPECTED_AGENTS:
            if aid == agent_id:
                name = label
                break
    if not name:
        name = agent_id
    kind = "01-assistants"
    g = AGENT_DATA / "agents" / agent_id / "group.json"
    if g.exists():
        kind = "02-groups"
    return kind, safe_name(name)


def _copy_jsonl(src: pathlib.Path, dst: pathlib.Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _drop_record(agent_id: str, label: str, required: bool, directory: pathlib.Path) -> dict:
    files = _agent_drop_files(directory)
    counts = {path.name: len(load_jsonl(path)) for path in _memory_jsonl_files(files)}
    routines = directory / "routines.json"
    if routines.is_file():
        counts["routines.json"] = _routine_count(routines)
    return {
        "id": agent_id,
        "name": label,
        "required": required,
        "dropped": bool(files) or _status_file(directory).is_file(),
        "explicit_empty": _status_file(directory).is_file() and not _memory_jsonl_files(files),
        "files": counts,
    }


def _discovered_drop_dirs(root: pathlib.Path) -> list[pathlib.Path]:
    agents_root = root / "agents"
    if not agents_root.is_dir():
        return []
    found = []
    for directory in sorted(agents_root.iterdir()):
        if directory.is_dir() and (_agent_drop_files(directory) or _status_file(directory).is_file()):
            found.append(directory)
    return found


def scan(root: pathlib.Path | None = None) -> dict:
    """Status only: counts, no fact text.

    With no expected roster, every discovered agent id is a per-assistant row.
    Additional drops are only directories outside that roster.
    """
    root = root or drop_root()
    shared = root / "shared-user" / "memory-user.jsonl"
    shared_n = len(load_jsonl(shared)) if shared.is_file() else (0 if _status_file(root / "shared-user").is_file() else None)
    discovered = _discovered_drop_dirs(root)
    agents = []
    extra = []
    if EXPECTED_AGENTS:
        for aid, label, required in EXPECTED_AGENTS:
            agents.append(_drop_record(aid, label, required, root / "agents" / aid))
        known = {agent[0] for agent in EXPECTED_AGENTS}
        extra = [directory.name for directory in discovered if directory.name not in known]
    else:
        for directory in discovered:
            if valid_agent_id(directory.name):
                agents.append(_drop_record(directory.name, directory.name, False, directory))
            else:
                extra.append(directory.name)
    return {
        "drop_root": str(root),
        "drop_exists": root.is_dir(),
        "shared_user": None if shared_n is None else {"path": str(shared), "count": shared_n},
        "agents": agents,
        "extra_ids": extra,
    }


def _memory_summary(status: dict) -> tuple[int, int, list[str]]:
    agents = status["agents"]
    missing = [agent["name"] for agent in agents if not agent["dropped"]]
    return sum(1 for agent in agents if agent["dropped"]), len(agents), missing


def _last_drop_utc(directory: pathlib.Path) -> str | None:
    if not directory.is_dir():
        return None
    times = [path.stat().st_mtime for path in directory.glob("*.jsonl") if path.is_file()]
    if not times:
        return None
    return datetime.fromtimestamp(max(times), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pending_payload(status: dict, root: pathlib.Path) -> dict:
    exported, expected, _missing = _memory_summary(status)
    missing = []
    for agent in status["agents"]:
        if agent["dropped"]:
            continue
        missing.append({
            "id": agent["id"],
            "name": agent["name"],
            "required": agent["required"],
            "last_drop_utc": _last_drop_utc(root / "agents" / agent["id"]),
        })
    return {"exported": exported, "expected": expected, "missing": missing}


def _index_markdown(status: dict) -> str:
    exported, expected, missing = _memory_summary(status)
    missing_text = ", ".join(missing) if missing else "none"
    lines = [
        f"Memory export: {exported}/{expected}, missing: {missing_text}",
        "",
        "# Platform memory index (phase 1)",
        "",
        f"Source: `{status['drop_root']}` (RecallMemory drop).",
        "Assistants without memory drops are marked 'not exported'; the backup continues.",
        "Local memory/ files are preserved separately; check their timestamps for freshness.",
        "",
        "## Shared memory (scope=user)",
        "",
    ]
    if status["shared_user"] is None:
        lines.append("- not exported")
    else:
        lines.append(f"- Exported or explicitly declared empty: {status['shared_user']['count']} records → 00-shared/memory/")
    lines += ["", "## Per assistant", ""]
    for a in status["agents"]:
        tag = "" if a["required"] else " (optional)"
        if not a["dropped"]:
            lines.append(f"- {a['name']}{tag}: not exported")
        else:
            bits = ", ".join(f"{k} {n} records" for k, n in a["files"].items())
            if a["explicit_empty"]:
                bits += (", " if bits else "") + "explicit empty-memory declaration (verification required)"
            lines.append(f"- {a['name']}{tag}: exported ({bits})")
    if status["extra_ids"]:
        lines += ["", "## Additional drops not in the configured roster", ""]
        for i in status["extra_ids"]:
            lines.append(f"- `{i}`")
    lines.append("")
    return "\n".join(lines)


def apply_tree(dest: pathlib.Path, id_to_name: dict[str, str] | None = None) -> dict:
    """Write human-readable memory into dest/00-shared and dest/01-assistants|02-groups.

    dest is the virtual-office root, or grok's platform-memory root (same layout).
    """
    root = drop_root()
    status = scan(root)
    if id_to_name:
        for agent in status["agents"]:
            if agent["id"] in id_to_name:
                agent["name"] = id_to_name[agent["id"]]
    pending = pending_payload(status, root)
    status["pending"] = pending
    write(dest / "memory-pending.json", json.dumps(pending, ensure_ascii=False, indent=2))
    write(dest / "memory-index.md", _index_markdown(status))

    shared_src = root / "shared-user" / "memory-user.jsonl"
    shared_dir = dest / "00-shared" / "memory"
    shared_status = _status_file(root / "shared-user")
    if shared_status.is_file():
        _copy_jsonl(shared_status, shared_dir / "memory-status.json")
    if shared_src.is_file():
        rows = load_jsonl(shared_src)
        _copy_jsonl(shared_src, shared_dir / "memory-user.jsonl")
        write(shared_dir / "shared-memory.md", render_markdown(
            rows,
            "Shared memory",
            "scope=user, shared across assistants. Each record includes date, scope, source, and full text; searchable with grep.",
        ))
        write(shared_dir / "README.md", """# Shared memory

| File | Description |
|---|---|
| `shared-memory.md` | Human-readable, grouped by tier (persistent profile, assistant-owned log/note) |
| `memory-user.jsonl` | Original drop data, one JSONL record per line |

Frozen local copies remain under ../shared-agent-memory/ (Virtual Office export) or shared/user-memory/ (raw Grok export).
""")
    elif shared_status.is_file():
        write(shared_dir / "README.md",
              "# Shared memory\n\nNo memory facts were supplied. See memory-status.json for "
              "the explicit empty-memory declaration; verify its checked_at_utc timestamp.\n")
    else:
        write(shared_dir / "README.md", "# Shared memory\n\nnot exported\n")

    seen: set[str] = set()
    for a in status["agents"]:
        seen.add(a["id"])
        _write_agent(dest, a["id"], a["name"], root / "agents" / a["id"], id_to_name, dropped=a["dropped"])
    for aid in status["extra_ids"]:
        _write_agent(dest, aid, aid, root / "agents" / aid, id_to_name, dropped=True)
    return status


def _write_agent(dest, agent_id, label, drop_dir, id_to_name, dropped: bool) -> None:
    kind, folder = _display_folder(agent_id, id_to_name)
    # prefer an existing folder under either tree (export already created it)
    existing = None
    for top in ("01-assistants", "02-groups"):
        cand = dest / top / folder
        if cand.is_dir():
            existing = cand
            break
    base = existing if existing is not None else (dest / kind / folder)
    mem = base / "memory"
    if not dropped:
        # only note inside the pack index; don't invent empty per-agent files
        # unless that agent's folder already exists (so a reader opening memory/ sees the gap)
        if existing is not None:
            note = mem / "platform-memory.md"
            if not note.exists():
                write(note, f"# Platform memory\n\nnot exported\n\n({label}, `{agent_id}`)\n")
        return
    files = _memory_jsonl_files(_agent_drop_files(drop_dir))
    status = _status_file(drop_dir)
    if status.is_file():
        _copy_jsonl(status, mem / "memory-status.json")
    rows: list[dict] = []
    for src in files:
        _copy_jsonl(src, mem / src.name)
        rows.extend(load_jsonl(src))
    write(mem / "platform-memory.md", render_markdown(
        rows,
        f"Platform memory · {label}",
        "Exported by this assistant using RecallMemory. Persistent = profile tier; assistant-owned = log/note tiers."
        " Each record includes a date and full content; JSONL preserves the original entries.",
    ))


def apply_grok(dest: pathlib.Path) -> dict:
    """Human-readable tree at dest/platform-memory/, plus jsonl beside each agent id."""
    status = apply_tree(dest / "platform-memory", id_to_name=None)
    root = drop_root()
    shared_src = root / "shared-user" / "memory-user.jsonl"
    if shared_src.is_file() or _status_file(root / "shared-user").is_file():
        target = dest / "shared" / "platform-memory"
        target.mkdir(parents=True, exist_ok=True)
        if shared_src.is_file():
            _copy_jsonl(shared_src, target / "memory-user.jsonl")
        if _status_file(root / "shared-user").is_file():
            _copy_jsonl(_status_file(root / "shared-user"), target / "memory-status.json")
    agents_root = root / "agents"
    if agents_root.is_dir():
        for d in agents_root.iterdir():
            if not d.is_dir():
                continue
            files = _agent_drop_files(d)
            status = _status_file(d)
            if not files and not status.is_file():
                continue
            out = dest / "agents" / d.name / "platform-memory"
            out.mkdir(parents=True, exist_ok=True)
            for src in _memory_jsonl_files(files):
                _copy_jsonl(src, out / src.name)
            routines = d / "routines.json"
            if routines.is_file():
                _copy_jsonl(routines, out / "routines.json")
            if status.is_file():
                _copy_jsonl(status, out / "memory-status.json")
    return status


def main() -> None:
    ap = argparse.ArgumentParser(description="Copy memory-drop into an export tree")
    ap.add_argument("--into-grok", type=pathlib.Path, help="grok-bot-export directory")
    ap.add_argument("--into-virtual", type=pathlib.Path, help="virtual-office directory")
    ap.add_argument("--status", action="store_true", help="print counts only")
    args = ap.parse_args()
    if args.status or not (args.into_grok or args.into_virtual):
        st = scan()
        print(json.dumps({
            "drop_exists": st["drop_exists"],
            "shared_user": None if st["shared_user"] is None else st["shared_user"]["count"],
            "agents": [
                {"id": a["id"], "name": a["name"], "dropped": a["dropped"], "files": a["files"]}
                for a in st["agents"]
            ],
            "extra_ids": st["extra_ids"],
        }, ensure_ascii=False, indent=2))
        return
    if args.into_virtual:
        apply_tree(args.into_virtual)
        lock_down(args.into_virtual)
        print(f"VIRTUAL_MEMORY={args.into_virtual}")
    if args.into_grok:
        apply_grok(args.into_grok)
        lock_down(args.into_grok)
        print(f"GROK_MEMORY={args.into_grok}")


if __name__ == "__main__":
    main()
