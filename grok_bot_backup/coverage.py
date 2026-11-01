"""Coverage matrix and on-disk probes for a readable agent export.

Cells are ok, partial, unavailable, or missing.
missing fails verify --strict. unavailable warns and does not fail --strict.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sqlite3

from .common import safe_name, valid_agent_id, write

DIMENSIONS = (
    "instructions",
    "memory",
    "routines",
    "conversation",
    "toolcalls",
    "private",
    "attachments",
    "connectors",
    "business-data-tree",
    "ui-only-secrets",
    "standing-rules",
)
CELLS = ("ok", "partial", "unavailable", "missing")

_SECRET_KEY = re.compile(
    r"(token|secret|password|api[_-]?key|authorization|credential|private[_-]?key)",
    re.I,
)
_CRON_FIELD = re.compile(r"^[@\d*/,\-]+$")
_ROUTINE_PROBES = (
    "automations/",
    "routines/",
    "schedules/",
    "cron.json",
    "automations.json",
    "platform schedule store (agent-data schedules/, automations/, routines/ keyed by agent id)",
    "store.db tables schedules, automations, routines, cron",
)
_RECONSTRUCTED_NOTE = (
    "The full system prompt is platform-hosted and is not part of this export. "
    "This file is a reconstruction: the profile description, the first handoff copied verbatim, "
    "and standing rules when a standing-rules file or field is present. "
    "Re-enter the system prompt in the new platform."
)
_STANDARD_AGENT_DIRS = {
    "memory", "notes", "attachments", "reference", "private", "automations",
    "routines", "schedules", "avatar", "raw",
}


def _quote(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(name)
    return '"' + name + '"'


def _read_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None


def _textify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _nonempty(value) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (dict, list)) and value:
        return json.dumps(value, ensure_ascii=False, indent=2)
    return None


def _file_count(root: pathlib.Path) -> int:
    if not root.exists():
        return 0
    if root.is_file():
        return 1
    return sum(1 for path in root.rglob("*") if path.is_file())


def presence_cell(src: pathlib.Path, dest: pathlib.Path | None = None) -> str:
    """ok when files exist and were copied; unavailable when the source is empty."""
    src_count = _file_count(src)
    if src_count == 0:
        return "unavailable"
    if dest is None:
        return "ok"
    dest_count = _file_count(dest)
    if dest_count >= src_count:
        return "ok"
    if dest_count == 0:
        return "missing"
    return "partial"


def _connect(path: pathlib.Path) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def read_kv(db_path: pathlib.Path) -> list[tuple[str, str]]:
    """Return key/value rows from a kv-like table. Empty when the db has none."""
    if not db_path.is_file():
        return []
    conn = _connect(db_path)
    if conn is None:
        return []
    pairs: list[tuple[str, str]] = []
    try:
        tables = {row[0] for row in conn.execute(
            "select name from sqlite_master where type='table'")}
        for table in ("kv", "kvs", "key_value", "key_values", "agent_kv"):
            if table not in tables:
                continue
            columns = [row[1] for row in conn.execute(f"pragma table_info({_quote(table)})")]
            key_col = next((c for c in ("key", "k", "name") if c in columns), None)
            val_col = next((c for c in ("value", "v", "val", "data", "json") if c in columns), None)
            if not key_col or not val_col:
                continue
            query = f"select {_quote(key_col)}, {_quote(val_col)} from {_quote(table)}"
            for key, value in conn.execute(query):
                if key is not None:
                    pairs.append((str(key), _textify(value)))
    except (sqlite3.Error, ValueError):
        return []
    finally:
        conn.close()
    return pairs


def _coerce_block(block):
    if not isinstance(block, dict):
        return block
    kind = block.get("type")
    if kind == "tool_use" and "input" not in block and "arguments" in block:
        copied = dict(block)
        copied["input"] = block["arguments"]
        return copied
    if kind in ("text", "tool_use", "tool_result"):
        return block
    if "name" in block and ("input" in block or "arguments" in block):
        return {
            "type": "tool_use",
            "name": block.get("name"),
            "input": block.get("input", block.get("arguments")),
        }
    if "name" in block and ("result" in block or "output" in block):
        return {
            "type": "tool_result",
            "name": block.get("name"),
            "result": block.get("result", block.get("output")),
        }
    return block


def _content_row(blocks, role, ident) -> dict:
    row = {"role": role or "assistant", "message": {"content": [_coerce_block(b) for b in blocks]}}
    if ident is not None:
        row["id"] = ident
    return row


def _text_row(text: str, role, ident) -> dict:
    row = {"role": role or "assistant", "message": {"content": [{"type": "text", "text": text}]}}
    if ident is not None:
        row["id"] = ident
    return row


def _normalize_transcript_entry(entry, role, ident) -> dict | None:
    if entry is None:
        return None
    if isinstance(entry, bytes):
        entry = entry.decode("utf-8", errors="replace")
    parsed = entry if isinstance(entry, (dict, list)) else None
    if isinstance(entry, str):
        text = entry.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return _text_row(text, role, ident)
    if isinstance(parsed, dict) and isinstance(parsed.get("message"), dict):
        row = dict(parsed)
        message = dict(parsed["message"])
        content = message.get("content")
        if isinstance(content, list):
            message["content"] = [_coerce_block(block) for block in content]
        elif isinstance(content, str):
            message["content"] = [{"type": "text", "text": content}]
        row["message"] = message
        if role and not row.get("role"):
            row["role"] = role
        if ident is not None and "id" not in row:
            row["id"] = ident
        return row
    if isinstance(parsed, dict) and isinstance(parsed.get("content"), list):
        return _content_row(parsed["content"], parsed.get("role") or role, ident)
    if isinstance(parsed, dict) and parsed.get("type") in ("text", "tool_use", "tool_result"):
        return _content_row([parsed], role, ident)
    if isinstance(parsed, list):
        return _content_row(parsed, role, ident)
    if isinstance(parsed, dict):
        for key in ("text", "content", "result"):
            if isinstance(parsed.get(key), str) and parsed[key].strip():
                return _text_row(parsed[key], role or parsed.get("role"), ident)
        return _text_row(json.dumps(parsed, ensure_ascii=False), role, ident)
    if isinstance(parsed, str) and parsed.strip():
        return _text_row(parsed, role, ident)
    return None


def load_store_transcript(db_path: pathlib.Path | None) -> list[dict]:
    """Normalize agents/<id>/store.db transcript_entries into frozen-jsonl rows."""
    if db_path is None or not db_path.is_file():
        return []
    conn = _connect(db_path)
    if conn is None:
        return []
    try:
        tables = {row[0] for row in conn.execute(
            "select name from sqlite_master where type='table'")}
        if "transcript_entries" not in tables:
            return []
        columns = [row[1] for row in conn.execute("pragma table_info(transcript_entries)")]
        order = [name for name in ("seq", "id") if name in columns]
        sql = "select * from transcript_entries"
        if order:
            sql += " order by " + ", ".join(_quote(name) for name in order)
        fetched = conn.execute(sql).fetchall()
    except (sqlite3.Error, ValueError):
        return []
    finally:
        conn.close()
    index = {name: pos for pos, name in enumerate(columns)}
    entry_name = next((name for name in ("entry", "json", "data", "payload", "content") if name in index), None)
    if entry_name is None:
        return []
    rows = []
    for row in fetched:
        item = _normalize_transcript_entry(
            row[index[entry_name]],
            row[index["role"]] if "role" in index else None,
            row[index["id"]] if "id" in index else None,
        )
        if item is not None:
            rows.append(item)
    return rows


def has_tool_blocks(rows: list[dict]) -> bool:
    for row in rows:
        content = ((row.get("message") or {}).get("content") or [])
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("tool_use", "tool_result"):
                return True
    return False


def _string_field(mapping: dict, names: tuple[str, ...]) -> str | None:
    for name in names:
        value = mapping.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _find_named_text(agent_dir: pathlib.Path, profile: dict, pairs, filenames: tuple[str, ...],
                     fields: tuple[str, ...]) -> str | None:
    text = _string_field(profile, fields)
    if text:
        return text
    needles = tuple(item.lower().replace("-", "").replace("_", "") for item in fields)
    for key, value in pairs:
        if _SECRET_KEY.search(key):
            continue
        compact = key.lower().replace("-", "").replace("_", "")
        if compact in needles or any(needle and (compact.endswith(needle) or compact.startswith(needle)) for needle in needles):
            stripped = value.strip()
            if stripped:
                return stripped
    wanted = {name.lower() for name in filenames}
    for root in (agent_dir, agent_dir / "notes", agent_dir / "memory", agent_dir / "reference"):
        if not root.is_dir():
            continue
        for path in sorted(root.iterdir()):
            if path.is_file() and path.name.lower() in wanted:
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    return text
    return None


def probe_instructions(agent_dir: pathlib.Path, profile: dict, handoff: str | None = None) -> dict:
    """Always reconstruct. The full system prompt is not chased."""
    pairs = read_kv(agent_dir / "store.db")
    file_handoff = _find_named_text(
        agent_dir, profile, pairs,
        ("handoff.md", "first-handoff.md"),
        ("handoff", "firstHandoff", "first_handoff"),
    )
    # An explicit handoff file or field wins. Otherwise use the verbatim text
    # the caller found (typically the first user message).
    handoff_text = file_handoff or (handoff.strip() if isinstance(handoff, str) and handoff.strip() else None)
    rules = _find_named_text(
        agent_dir, profile, (),
        ("standing-rules.md", "rules.md"),
        ("standingRules", "standing_rules"),
    )
    description = profile.get("description") if isinstance(profile.get("description"), str) else ""
    body = "\n".join([
        "## Description",
        "",
        description.strip() or "(not present)",
        "",
        "## First handoff",
        "",
        handoff_text or "(not present)",
        "",
        "## Standing rules",
        "",
        rules or "(not present)",
        "",
    ])
    probed = _instruction(
        "reconstructed", False, "unavailable", body,
        ["profile.description", "first handoff verbatim", "standing rules"],
        _RECONSTRUCTED_NOTE,
    )
    probed["standing_rules"] = rules or ""
    probed["handoff"] = handoff_text or ""
    return probed


def _instruction(source: str, complete: bool, cell: str, text: str, probes: list[str], note: str) -> dict:
    return {
        "source": source,
        "complete": complete,
        "coverage": cell,
        "text": text,
        "probes": probes,
        "note": note,
    }


def render_instructions(name: str, probed: dict) -> str:
    lines = [
        f"# Instructions · {name}",
        "",
        f"- Source: `{probed['source']}`",
        f"- Complete: {'true' if probed['complete'] else 'false'}",
        f"- Coverage: {probed['coverage']}",
        "",
    ]
    if probed.get("note"):
        lines.extend([probed["note"], ""])
    lines.extend([probed.get("text") or "(empty)", ""])
    return "\n".join(lines)


def _skipped_routine_path(path: pathlib.Path) -> bool:
    return "automation_completion_inbox" in path.parts


def _origin(path: pathlib.Path, data_root: pathlib.Path) -> str:
    try:
        return str(path.relative_to(data_root))
    except ValueError:
        return path.name


def _is_cron_fields(fields: list[str]) -> bool:
    return len(fields) >= 5 and all(_CRON_FIELD.fullmatch(field) for field in fields[:5])


def _routine(name, cron, timezone, prompt, enabled, last_run, source, origin, note="") -> dict:
    item = {
        "name": name,
        "cron": cron,
        "timezone": timezone,
        "prompt": prompt,
        "enabled": enabled,
        "last_run": last_run,
        "source": source,
        "origin": origin,
    }
    if note:
        item["note"] = note
    return item


def _inferred(name: str, origin: str, note: str) -> dict:
    return _routine(name, None, None, None, None, None, "inferred", origin, note)


def _cron_value(obj: dict) -> str | None:
    for key in ("cron", "cronExpression", "cron_expression", "expression", "schedule"):
        if key not in obj:
            continue
        value = obj[key]
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            inner = _cron_value(value)
            if inner:
                return inner
    return None


def _enabled(obj: dict):
    if "enabled" not in obj:
        return None
    value = obj["enabled"]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "1", "enabled", "on"):
            return True
        if low in ("false", "no", "0", "disabled", "off"):
            return False
    return None


def _last_run(obj: dict):
    for key in ("last_run", "lastRun", "last_run_at", "lastRunAt"):
        value = obj.get(key)
        if isinstance(value, (str, int, float)) and value != "":
            return value
    return None


def _prompt_value(obj: dict) -> str | None:
    for key in ("prompt", "instructions", "message", "task"):
        text = _nonempty(obj.get(key))
        if text:
            return text
    return None


def _routine_name(obj: dict, fallback: str) -> str:
    for key in ("name", "title", "id"):
        text = _nonempty(obj.get(key))
        if text:
            return text
    return fallback


def _declared_source(obj: dict, default_source: str, cron: str | None) -> str:
    declared = obj.get("source")
    if isinstance(declared, str):
        norm = declared.strip().lower().replace("_", "-")
        if norm in ("platform", "box-cron", "inferred", "memory-drop"):
            return norm
    if cron:
        return default_source
    return "inferred"


def _routine_from_mapping(obj: dict, default_source: str, origin: str, fallback: str) -> dict:
    cron = _cron_value(obj)
    source = _declared_source(obj, default_source, cron)
    note = ""
    if source == "inferred":
        note = "No schedule fields were found. This entry is inferred and is not a confirmed routine."
    timezone = _nonempty(
        obj.get("timezone") or obj.get("timeZone") or obj.get("tz")
        or obj.get("CRON_TZ") or obj.get("cron_tz")
    )
    return _routine(
        _routine_name(obj, fallback),
        cron,
        timezone,
        _prompt_value(obj),
        _enabled(obj),
        _last_run(obj),
        source,
        origin,
        note,
    )


def _parse_crontab(text: str, origin: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 6 and _is_cron_fields(parts):
            rows.append(_routine(
                "crontab", " ".join(parts[:5]), None, " ".join(parts[5:]),
                None, None, "box-cron", origin,
            ))
        else:
            rows.append(_inferred("unparsed cron line", origin, "Line is not a 5-field cron entry."))
    return rows


def _mentions_agent(data) -> bool:
    if isinstance(data, dict):
        if any(valid_agent_id(str(key)) for key in data) or _agent_of(data):
            return True
        for value in data.values():
            if isinstance(value, list) and any(isinstance(item, dict) and _agent_of(item) for item in value):
                return True
            if isinstance(value, dict) and _agent_of(value):
                return True
    if isinstance(data, list):
        return any(isinstance(item, dict) and _agent_of(item) for item in data)
    return False


def _agent_of(obj: dict) -> str | None:
    for key in ("agent_id", "agentId", "assistant_id", "assistantId"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _select_entries(data, agent_id: str, agent_scoped: bool):
    if agent_scoped:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("routines", "automations", "schedules", "jobs", "items"):
                if isinstance(data.get(key), list):
                    return data[key]
            return [data]
        if isinstance(data, str):
            return [data]
        return []
    if isinstance(data, dict) and agent_id in data:
        inner = data[agent_id]
        if isinstance(inner, list):
            return inner
        if isinstance(inner, dict):
            return [inner]
        if isinstance(inner, str):
            return [inner]
    entries = []
    if isinstance(data, dict):
        for key in ("routines", "automations", "schedules", "jobs", "items"):
            if isinstance(data.get(key), list):
                entries.extend(data[key])
        if _agent_of(data) == agent_id:
            return [data]
    elif isinstance(data, list):
        entries = data
    matched = [item for item in entries if isinstance(item, dict) and _agent_of(item) == agent_id]
    return matched or None


def _binary(path: pathlib.Path) -> bool:
    try:
        return b"\0" in path.read_bytes()[:1024]
    except OSError:
        return True


def _routines_from_file(path: pathlib.Path, default_source: str, agent_id: str,
                        data_root: pathlib.Path, agent_scoped: bool) -> list[dict]:
    if not path.is_file() or _skipped_routine_path(path) or _binary(path):
        return []
    origin = _origin(path, data_root)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if data is None:
        if default_source == "box-cron":
            parsed = _parse_crontab(text, origin)
            if parsed:
                return parsed
        if text.strip():
            return [_inferred(path.name, origin, "File is not JSON and has no cron fields.")]
        return []
    selected = _select_entries(data, agent_id, agent_scoped)
    if selected is None:
        return []
    rows = []
    for item in selected:
        if isinstance(item, str):
            if default_source == "box-cron":
                rows.extend(_parse_crontab(item, origin))
            else:
                rows.append(_inferred(path.name, origin, "Entry was a string without schedule fields."))
            continue
        if isinstance(item, dict):
            rows.append(_routine_from_mapping(item, default_source, origin, path.stem))
        else:
            rows.append(_inferred(path.name, origin, "Entry was not an object."))
    return rows


def _files_under(root: pathlib.Path) -> list[pathlib.Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file() and not _skipped_routine_path(path))


def _routines_from_db(db_path: pathlib.Path, origin_prefix: str) -> list[dict]:
    if not db_path.is_file():
        return []
    conn = _connect(db_path)
    if conn is None:
        return []
    tables = ("schedules", "schedule", "automations", "automation", "routines", "routine", "cron", "cron_jobs")
    found = []
    try:
        present = {row[0] for row in conn.execute(
            "select name from sqlite_master where type='table'")}
        for table in tables:
            if table not in present or "completion_inbox" in table:
                continue
            columns = [row[1] for row in conn.execute(f"pragma table_info({_quote(table)})")]
            for raw in conn.execute(f"select * from {_quote(table)}"):
                obj = {}
                for pos, name in enumerate(columns):
                    value = raw[pos]
                    parsed = None
                    if isinstance(value, str):
                        try:
                            parsed = json.loads(value)
                        except json.JSONDecodeError:
                            parsed = None
                    obj[name] = parsed if isinstance(parsed, (dict, list)) else value
                payload = None
                for key in ("data", "json", "payload", "entry", "definition"):
                    if isinstance(obj.get(key), dict):
                        payload = obj[key]
                        break
                mapping = payload or {k: v for k, v in obj.items() if not isinstance(v, (dict, list))}
                if isinstance(mapping, dict):
                    found.append(_routine_from_mapping(
                        mapping, "platform", f"{origin_prefix}:{table}", table,
                    ))
    except (sqlite3.Error, ValueError):
        return found
    finally:
        conn.close()
    return found


def probe_routines(agent_dir: pathlib.Path) -> list[dict]:
    """Union of on-box schedule locations. Inferred rows are never treated as fact."""
    data_root = agent_dir.parent.parent
    agent_id = agent_dir.name
    found: list[dict] = []
    scoped = (
        (agent_dir / "automations", "platform", True),
        (agent_dir / "routines", "platform", True),
        (agent_dir / "schedules", "platform", True),
        (data_root / "automations" / agent_id, "platform", True),
        (data_root / "routines" / agent_id, "platform", True),
        (data_root / "schedules" / agent_id, "platform", True),
    )
    for root, source, agent_scoped in scoped:
        for path in _files_under(root):
            found.extend(_routines_from_file(path, source, agent_id, data_root, agent_scoped))
        if root.suffix and root.is_file():
            found.extend(_routines_from_file(root, source, agent_id, data_root, agent_scoped))
    singles = (
        (agent_dir / "cron.json", "box-cron", True),
        (agent_dir / "automations.json", "platform", True),
        (data_root / "schedules" / f"{agent_id}.json", "platform", True),
        (data_root / "automations" / f"{agent_id}.json", "platform", True),
        (data_root / "routines" / f"{agent_id}.json", "platform", True),
        (data_root / "schedules.json", "platform", False),
        (data_root / "automations.json", "platform", False),
        (data_root / "cron.json", "box-cron", False),
    )
    for path, source, agent_scoped in singles:
        found.extend(_routines_from_file(path, source, agent_id, data_root, agent_scoped))
    found.extend(_routines_from_db(agent_dir / "store.db", "store.db"))
    if agent_dir.is_dir():
        for path in sorted(agent_dir.rglob("crontab.txt")):
            if path.is_file() and not _skipped_routine_path(path):
                found.extend(_routines_from_file(path, "box-cron", agent_id, data_root, True))
    deduped = []
    seen = set()
    for item in found:
        key = (item.get("origin"), item.get("name"), item.get("cron"), item.get("source"), item.get("prompt"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def routines_cell(routines: list[dict]) -> str:
    if not routines:
        return "unavailable"
    if any(item.get("source") in ("platform", "box-cron", "memory-drop") for item in routines):
        return "ok"
    return "partial"


def routine_note(routines: list[dict]) -> str:
    if not routines:
        return "unavailable"
    counts = {name: 0 for name in ("platform", "box-cron", "memory-drop", "inferred")}
    for item in routines:
        source = item.get("source") if item.get("source") in counts else "inferred"
        counts[source] += 1
    return ", ".join(f"{count} {name}" for name, count in counts.items() if count)


def _drop_routine_entries(data) -> list:
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("routines", "items", "schedules", "jobs"):
        if isinstance(data.get(key), list):
            return data[key]
    nested = []
    for key, value in data.items():
        if isinstance(value, dict) and (_cron_value(value) or value.get("prompt") or value.get("schedule")):
            item = dict(value)
            item.setdefault("name", key)
            nested.append(item)
    if nested:
        return nested
    if _cron_value(data) or data.get("name") or data.get("prompt"):
        return [data]
    return []


def load_drop_routines(path: pathlib.Path | None) -> list[dict]:
    """Parse a memory-drop routines.json. Item source is kept when it is set."""
    if path is None or not path.is_file():
        return []
    data = _read_json(path)
    if data is None:
        return [_inferred(path.name, path.name, "routines.json is not valid JSON.")]
    rows = []
    for item in _drop_routine_entries(data):
        if isinstance(item, dict):
            rows.append(_routine_from_mapping(item, "memory-drop", "memory-drop/routines.json", path.stem))
        else:
            rows.append(_inferred(path.name, "memory-drop/routines.json", "Entry was not an object."))
    return rows


def merge_routines(primary: list[dict], extra: list[dict]) -> list[dict]:
    """Keep drop routines, then disk routines whose cron is not already present."""
    merged = list(primary)
    seen = {item.get("cron") for item in primary if item.get("cron")}
    for item in extra:
        cron = item.get("cron")
        if cron and cron in seen:
            continue
        merged.append(item)
        if cron:
            seen.add(cron)
    return merged


_ENV_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")


def env_key_names(agent_dir: pathlib.Path) -> list[str]:
    """Key names from on-disk env files. Values are never returned."""
    names = []
    if not agent_dir.is_dir():
        return names
    for path in sorted(agent_dir.rglob("*.env")):
        if not path.is_file() or _skipped_routine_path(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = path.name
        try:
            rel = str(path.relative_to(agent_dir))
        except ValueError:
            pass
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = _ENV_KEY.match(stripped)
            if match and not _SECRET_KEY.search(match.group(1)):
                names.append(f"{rel}: {match.group(1)}")
            elif match:
                names.append(f"{rel}: {match.group(1)}")
    return names


def render_ui_secrets(name: str, key_names: list[str]) -> str:
    lines = [
        f"# UI-only secrets · {name}",
        "",
        "- Coverage: unavailable",
        "",
        "Webhook URLs, API keys, and authorization headers that live only in the platform UI",
        "are not on disk. Re-enter them in the new platform. Values are not written here.",
        "",
    ]
    if key_names:
        lines.append("On-disk env files list key names only:")
        lines.append("")
        lines.extend(f"- `{item}`" for item in key_names)
    else:
        lines.append("No on-disk env files were found.")
    lines.append("")
    return "\n".join(lines)


def business_dirs(agent_dir: pathlib.Path) -> list[pathlib.Path]:
    if not agent_dir.is_dir():
        return []
    found = []
    for child in sorted(agent_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name in _STANDARD_AGENT_DIRS or child.name == "automation_completion_inbox":
            continue
        if any(path.is_file() for path in child.rglob("*")):
            found.append(child)
    return found


def _slug(name: str, used: set[str]) -> str:
    base = safe_name(name).lower().replace(" ", "-")
    base = re.sub(r"[^a-z0-9._-]+", "-", base).strip("-") or "routine"
    slug = base
    number = 2
    while slug in used:
        slug = f"{base}-{number}"
        number += 1
    used.add(slug)
    return slug


def _md_cell(value) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_routines(name: str, routines: list[dict]) -> str:
    if not routines:
        probed = "\n".join(f"- `{item}`" for item in _ROUTINE_PROBES)
        return (
            f"# Routines · {name}\n\n"
            "- Status: unavailable\n"
            "- Coverage: unavailable\n\n"
            "No schedule definitions were found. "
            "Unavailable means these probes found no routine. "
            "It does not mean a confirmed empty schedule.\n\n"
            f"Probed:\n\n{probed}\n\n"
            "`automation_completion_inbox` is completion mail, not a schedule definition, and was ignored.\n"
        )
    lines = [f"# Routines · {name}", ""]
    if any(item.get("source") == "inferred" for item in routines):
        lines.extend([
            "Inferred entries are guesses from files that had no schedule fields. "
            "Confirm them before using them as schedules.",
            "",
        ])
    lines.extend([
        "| Name | Cron | Timezone | Enabled | Last run | Source | File |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ])
    for item in routines:
        lines.append("| " + " | ".join([
            _md_cell(item.get("name")),
            _md_cell(item.get("cron")),
            _md_cell(item.get("timezone")),
            _md_cell(item.get("enabled")),
            _md_cell(item.get("last_run")),
            _md_cell(item.get("source")),
            f"`routines/{item.get('slug')}.json`",
        ]) + " |")
    lines.append("")
    for item in routines:
        lines.extend([
            f"## {item.get('name')}",
            "",
            f"- Source: `{item.get('source')}`",
            f"- Origin: `{item.get('origin')}`",
        ])
        if item.get("note"):
            lines.append(f"- Note: {item['note']}")
        lines.extend(["", "### Prompt", "", item.get("prompt") or "(none)", ""])
    return "\n".join(lines)


def _connector_name(obj: dict, fallback: str) -> str:
    for key in ("name", "title", "id"):
        text = _nonempty(obj.get(key))
        if text and not _SECRET_KEY.search(text):
            return text
    return fallback


def _auth_status(obj) -> str:
    if not isinstance(obj, dict):
        return "unknown"
    for key in ("auth", "authStatus", "auth_status", "status", "authorized", "connected"):
        if key not in obj:
            continue
        value = obj[key]
        if isinstance(value, bool):
            return "authorized" if value else "unauthorized"
        if isinstance(value, str) and value.strip():
            low = value.strip().lower()
            if _SECRET_KEY.search(low) or len(low) > 40:
                return "unknown"
            if low in ("ok", "authorized", "connected", "valid", "ready"):
                return "authorized"
            if low in ("unauthorized", "disconnected", "missing", "invalid", "expired", "none"):
                return "unauthorized"
            return low
    return "unknown"


def _connector_items(data, origin: str) -> list[dict]:
    found = []
    if isinstance(data, list):
        for index, item in enumerate(data, 1):
            if isinstance(item, str) and item.strip() and not _SECRET_KEY.search(item):
                found.append({"name": item.strip(), "auth": "unknown", "origin": origin})
            elif isinstance(item, dict):
                found.append({
                    "name": _connector_name(item, f"connector-{index}"),
                    "auth": _auth_status(item),
                    "origin": origin,
                })
    elif isinstance(data, dict):
        wrapped = False
        for key in ("connectors", "mcpServers", "mcp", "integrations"):
            if key in data:
                wrapped = True
                found.extend(_connector_items(data[key], f"{origin}:{key}"))
        if wrapped:
            return found
        if any(key in data for key in ("name", "title", "id", "auth", "status", "authorized")):
            found.append({
                "name": _connector_name(data, "connector"),
                "auth": _auth_status(data),
                "origin": origin,
            })
        else:
            for key, value in data.items():
                if _SECRET_KEY.search(str(key)):
                    continue
                if isinstance(value, dict):
                    found.append({"name": str(key), "auth": _auth_status(value), "origin": origin})
                elif isinstance(value, str) and value.strip() and not _SECRET_KEY.search(key):
                    found.append({"name": str(key), "auth": "unknown", "origin": origin})
    return found


def probe_connectors(agent_dir: pathlib.Path, settings: dict) -> list[dict]:
    found: list[dict] = []
    if isinstance(settings, dict):
        for key in ("connectors", "mcpServers", "mcp", "integrations"):
            if key in settings:
                found.extend(_connector_items(settings[key], f"settings.json:{key}"))
    data_root = agent_dir.parent.parent
    for path, scoped in (
        (agent_dir / "connectors.json", True),
        (agent_dir / "mcp.json", True),
        (data_root / "connectors.json", False),
        (data_root / "mcp.json", False),
    ):
        if not path.is_file():
            continue
        data = _read_json(path)
        if data is None:
            continue
        origin = _origin(path, data_root)
        if scoped or not _mentions_agent(data):
            found.extend(_connector_items(data, origin))
            continue
        selected = _select_entries(data, agent_dir.name, False)
        if selected:
            found.extend(_connector_items(selected, origin))
    deduped = []
    seen = set()
    for item in found:
        key = (item["name"], item["auth"], item["origin"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def connectors_cell(items: list[dict]) -> str:
    if not items:
        return "unavailable"
    if any(item.get("auth") == "unknown" for item in items):
        return "partial"
    return "ok"


def render_connectors(name: str, items: list[dict], cell: str) -> str:
    if not items:
        return (
            f"# Connectors · {name}\n\n"
            "- Status: unavailable\n"
            f"- Coverage: {cell}\n\n"
            "No connector names were found. "
            "Probed settings.json keys connectors, mcpServers, mcp, and integrations, "
            "plus connectors.json and mcp.json. Tokens are not written to this file.\n"
        )
    lines = [
        f"# Connectors · {name}",
        "",
        f"- Coverage: {cell}",
        "",
        "Names and auth status only. Tokens are not included.",
        "",
        "| Name | Auth | Origin |",
        "| --- | --- | --- |",
    ]
    for item in items:
        lines.append(f"| {_md_cell(item['name'])} | {_md_cell(item['auth'])} | {_md_cell(item['origin'])} |")
    lines.append("")
    return "\n".join(lines)


def write_agent_views(dest: pathlib.Path, name: str, agent_dir: pathlib.Path,
                      profile: dict, settings: dict, handoff: str | None = None,
                      routines_file: pathlib.Path | None = None) -> dict:
    """Write instructions, routines, connectors, and the extra coverage notes."""
    instructions = probe_instructions(agent_dir, profile, handoff)
    write(dest / "instructions.md", render_instructions(name, instructions))
    write(dest / "raw" / "instructions.json", json.dumps({
        "source": instructions["source"],
        "complete": instructions["complete"],
        "coverage": instructions["coverage"],
        "probes": instructions["probes"],
        "note": instructions["note"],
        "text": instructions["text"],
    }, ensure_ascii=False, indent=2))
    dropped = load_drop_routines(routines_file)
    routines = merge_routines(dropped, probe_routines(agent_dir))
    if routines_file is not None and routines_file.is_file():
        target = dest / "routines" / "routines.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(routines_file, target)
    used: set[str] = set()
    for item in routines:
        item["slug"] = _slug(str(item.get("name") or "routine"), used)
        payload = {key: item.get(key) for key in (
            "name", "cron", "timezone", "prompt", "enabled", "last_run", "source", "origin", "note",
        )}
        write(dest / "routines" / f"{item['slug']}.json", json.dumps(payload, ensure_ascii=False, indent=2))
    write(dest / "routines.md", render_routines(name, routines))
    connectors = probe_connectors(agent_dir, settings)
    connector_cell = connectors_cell(connectors)
    write(dest / "connectors.md", render_connectors(name, connectors, connector_cell))
    key_names = env_key_names(agent_dir)
    write(dest / "ui-only-secrets.md", render_ui_secrets(name, key_names))
    trees = business_dirs(agent_dir)
    for tree in trees:
        shutil.copytree(tree, dest / tree.name, symlinks=True, dirs_exist_ok=True)
    rules_cell = "ok" if instructions.get("standing_rules") else "unavailable"
    business_cell = "ok" if trees else "unavailable"
    return {
        "cells": {
            "instructions": "unavailable",
            "routines": routines_cell(routines),
            "connectors": connector_cell,
            "standing-rules": rules_cell,
            "business-data-tree": business_cell,
            "ui-only-secrets": "unavailable",
        },
        "notes": {
            "instructions": "reconstructed",
            "routines": routine_note(routines),
            "connectors": "unavailable" if not connectors else f"{len(connectors)} named",
            "standing-rules": "present" if rules_cell == "ok" else "not on disk",
            "business-data-tree": "unavailable" if not trees else ", ".join(tree.name for tree in trees),
            "ui-only-secrets": "platform UI secrets are not on disk",
        },
    }


def _count_facts(memory_dir: pathlib.Path | None) -> tuple[int, int]:
    if memory_dir is None or not memory_dir.is_dir():
        return 0, 0
    files = list(memory_dir.glob("*.jsonl"))
    facts = 0
    for path in files:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict) and str(record.get("fact") or "").strip():
                facts += 1
    return facts, len(files)


def memory_cell(agent_id: str, memory_dir: pathlib.Path | None, mem_status: dict | None) -> str:
    facts, files = _count_facts(memory_dir)
    has_status = bool(memory_dir and (memory_dir / "memory-status.json").is_file())
    if facts and has_status:
        return "partial"
    if facts or has_status:
        return "ok"
    if files:
        return "partial"
    for agent in (mem_status or {}).get("agents") or []:
        if agent.get("id") == agent_id and agent.get("required") and not agent.get("dropped"):
            return "missing"
    return "unavailable"


def _conversation_cell(stat: dict, dest_root: pathlib.Path) -> str:
    conv = dest_root / stat["conv_dir"] if stat.get("conv_dir") else None
    if stat.get("messages"):
        if conv and any(conv.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].md")):
            return "ok"
        return "missing"
    return "unavailable"


def _pack_row(agent_id: str, name: str, cells: dict, notes: dict) -> dict:
    packed = {}
    for dimension in DIMENSIONS:
        cell = cells.get(dimension) or "missing"
        if cell not in CELLS:
            cell = "missing"
        packed[dimension] = cell
    return {"id": agent_id, "name": name, "cells": packed, "notes": notes}


def assemble_rows(records: list[dict], conv_stats: list[dict], mem_status: dict | None,
                  dest_root: pathlib.Path) -> list[dict]:
    by_id = {record["id"]: record for record in records}
    seen = set()
    rows = []
    for stat in conv_stats:
        if stat.get("kind") != "agent":
            continue
        agent_id = stat["agent_id"]
        seen.add(agent_id)
        record = by_id.get(agent_id)
        cells = dict(record["cells"]) if record else {dimension: "unavailable" for dimension in DIMENSIONS}
        notes = dict(record.get("notes") or {}) if record else {}
        cells["conversation"] = _conversation_cell(stat, dest_root)
        cells["toolcalls"] = stat.get("toolcalls") or "unavailable"
        notes.setdefault("toolcalls", stat.get("toolcalls_source") or (
            "tool calls: unavailable" if cells["toolcalls"] == "unavailable" else cells["toolcalls"]
        ))
        if record and record.get("memory_dir"):
            memory_dir = pathlib.Path(record["memory_dir"])
        elif record and record.get("assistant_dir"):
            memory_dir = pathlib.Path(record["assistant_dir"]) / "memory"
        else:
            memory_dir = None
        cells["memory"] = memory_cell(agent_id, memory_dir, mem_status)
        notes["memory"] = cells["memory"]
        if not stat.get("messages"):
            notes["conversation"] = "no messages in the platform index"
        name = (record or {}).get("name") or stat.get("name") or agent_id
        rows.append(_pack_row(agent_id, name, cells, notes))
    for record in records:
        if record["id"] in seen:
            continue
        cells = dict(record["cells"])
        cells["conversation"] = "unavailable"
        cells["toolcalls"] = "unavailable"
        memory_dir = pathlib.Path(record["assistant_dir"]) / "memory" if record.get("assistant_dir") else None
        cells["memory"] = memory_cell(record["id"], memory_dir, mem_status)
        rows.append(_pack_row(record["id"], record["name"], cells, record.get("notes") or {}))
    rows.sort(key=lambda row: (row["name"].lower(), row["id"]))
    return rows


def render_matrix(payload: dict) -> str:
    header = "| Agent | " + " | ".join(DIMENSIONS) + " |"
    rule = "| --- | " + " | ".join("---" for _ in DIMENSIONS) + " |"
    lines = [
        "# Coverage matrix",
        "",
        "Cells: `ok`, `partial`, `unavailable`, `missing`.",
        "",
        "`missing` fails `verify --strict`. `unavailable` warns and does not fail `--strict`.",
        "",
        header,
        rule,
    ]
    notes = []
    for agent in payload.get("agents") or []:
        cells = agent.get("cells") or {}
        values = [cells.get(dimension, "missing") for dimension in DIMENSIONS]
        lines.append("| " + " | ".join([_md_cell(agent.get("name"))] + values) + " |")
        for dimension in DIMENSIONS:
            note = (agent.get("notes") or {}).get(dimension)
            if note and cells.get(dimension) != "ok":
                notes.append(f"- **{_md_cell(agent.get('name'))}** {dimension}: {_md_cell(note)}")
    if notes:
        lines.extend(["", "## Notes", ""] + notes)
    lines.append("")
    return "\n".join(lines)


def write_reports(dest: pathlib.Path, rows: list[dict]) -> dict:
    payload = {"version": 1, "dimensions": list(DIMENSIONS), "agents": rows}
    write(dest / "coverage.json", json.dumps(payload, ensure_ascii=False, indent=2))
    write(dest / "coverage.md", render_matrix(payload))
    return payload


def report_coverage(root: pathlib.Path, *, strict: bool, show: bool) -> bool:
    """Print the matrix when asked. Return False when a coverage check fails."""
    path = root / "coverage.json"
    if not path.is_file():
        if not show:
            return True
        print(("FAIL " if strict else "WARN ") + "coverage.json not found")
        return not strict
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(("FAIL " if strict else "WARN ") + f"coverage.json unreadable ({exc})")
        return not strict
    if show:
        print(render_matrix(payload).rstrip())
    ok = True
    missing = unavailable = 0
    for agent in payload.get("agents") or []:
        name = agent.get("name") or agent.get("id")
        cells = agent.get("cells") or {}
        for dimension in DIMENSIONS:
            cell = cells.get(dimension)
            if cell not in CELLS:
                cell = "missing"
            if cell == "missing":
                missing += 1
                line = f"coverage {name} {dimension}: missing"
                if strict:
                    ok = False
                    print("FAIL " + line)
                else:
                    print("WARN " + line)
            elif cell == "unavailable":
                unavailable += 1
                print(f"WARN coverage {name} {dimension}: unavailable")
    print(f"COVERAGE missing={missing} unavailable={unavailable}")
    return ok


def _records_from_box(data_root: pathlib.Path, out: pathlib.Path) -> list[dict]:
    from .memory_drop import drop_root
    records = []
    agents = data_root / "agents"
    if not agents.is_dir():
        return records
    for agent_dir in sorted(agents.iterdir()):
        if not agent_dir.is_dir() or not (agent_dir / "profile.json").is_file():
            continue
        if (agent_dir / "group.json").is_file():
            continue
        profile = _read_json(agent_dir / "profile.json") or {}
        settings = _read_json(agent_dir / "settings.json") or {}
        if not isinstance(profile, dict):
            profile = {}
        if not isinstance(settings, dict):
            settings = {}
        instructions = probe_instructions(agent_dir, profile)
        routines_file = drop_root() / "agents" / agent_dir.name / "routines.json"
        routines = merge_routines(load_drop_routines(routines_file), probe_routines(agent_dir))
        connectors = probe_connectors(agent_dir, settings)
        name = profile.get("name") or agent_dir.name
        records.append({
            "id": agent_dir.name,
            "name": name,
            "memory_dir": out / "agents" / agent_dir.name / "platform-memory",
            "cells": {
                "instructions": "unavailable",
                "routines": routines_cell(routines),
                "connectors": connectors_cell(connectors),
                "private": presence_cell(agent_dir / "private"),
                "attachments": presence_cell(agent_dir / "attachments"),
                "standing-rules": "ok" if instructions.get("standing_rules") else "unavailable",
                "business-data-tree": "ok" if business_dirs(agent_dir) else "unavailable",
                "ui-only-secrets": "unavailable",
            },
            "notes": {
                "instructions": "reconstructed",
                "routines": routine_note(routines),
                "connectors": "unavailable" if not connectors else f"{len(connectors)} named",
                "standing-rules": "present" if instructions.get("standing_rules") else "not on disk",
                "business-data-tree": ", ".join(path.name for path in business_dirs(agent_dir)) or "unavailable",
                "ui-only-secrets": "platform UI secrets are not on disk",
            },
        })
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Write coverage.json and coverage.md for a raw export")
    parser.add_argument("--data-root", type=pathlib.Path, required=True)
    parser.add_argument("--stats", type=pathlib.Path, required=True)
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args()
    from .memory_drop import scan
    document = json.loads(args.stats.read_text(encoding="utf-8"))
    records = _records_from_box(args.data_root, args.out)
    rows = assemble_rows(records, document.get("agents") or [], scan(), args.out)
    write_reports(args.out, rows)
    print(f"COVERAGE={args.out / 'coverage.md'}")


if __name__ == "__main__":
    main()
