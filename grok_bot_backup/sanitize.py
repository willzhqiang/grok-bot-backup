"""Content-aware redaction for text and structured files.

High-confidence credential assignments and exact secret keys are replaced with
`<redacted>`. Ordinary wording, including max_tokens and technical discussion,
is left in place. Values that were removed are not returned and are not logged.

Files that cannot be decoded and scanned are omitted. That is fail-closed:
binary databases, opaque attachments, oversize files, and unrecognized
encodings are not copied into the archive.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil


SCAN_MAX_BYTES_DEFAULT = 1_048_576
REDACTED = "<redacted>"

_STRUCTURED_KEY = re.compile(
    r"^(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|secrets|password|passwd|"
    r"token|tokens|authorization|credential|credentials|private[_-]?key|client[_-]?secret)$",
    re.IGNORECASE,
)
# Text assignments are narrower than JSON keys. A variable named token is not enough.
_TEXT_KEY = re.compile(
    r"^(?:api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|client[_-]?secret|"
    r"password|passwd|authorization|secret|aws[_-]?secret[_-]?access[_-]?key)$",
    re.IGNORECASE,
)
_BENIGN_KEY = re.compile(
    r"^(?:max[_-]?tokens?|n[_-]?tokens?|token[_-]?(?:count|limit|parser|izers?)|session[_-]?timeout|timeout)$",
    re.IGNORECASE,
)
_PEM = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_BEARER = re.compile(
    r"(?i)(authorization\s*[:=]\s*)?(\bbearer\s+)([A-Za-z0-9._~+/=-]{8,})"
)
_INLINE_ASSIGN = re.compile(
    r"""(?i)(?<![A-Za-z0-9_])(?P<key>api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|client[_-]?secret|password|passwd|aws[_-]?secret[_-]?access[_-]?key)(?P<sep>\s*[:=]\s*)(?P<val>"(?:\\.|[^"])*"|'(?:\\.|[^'])*'|[^\s,;]+)"""
)
_ENV_ASSIGN = re.compile(
    r"(?im)^(?P<prefix>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)(?P<sep>\s*=\s*)(?P<val>.+?)\s*$"
)
_JSONISH = re.compile(
    r"""(?P<pre>(?<![A-Za-z0-9_])["'])(?P<key>[A-Za-z0-9_.-]+)["']\s*:\s*(?P<val>"(?:\\.|[^"])*"|'(?:\\.|[^'])*')"""
)

_DB_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm",
}
_BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".mp4", ".mp3", ".wav", ".bin",
    ".exe", ".so", ".dylib", ".woff", ".woff2", ".ico", ".parquet", ".feather", ".bmp",
    ".avi", ".mov", ".ogg", ".webm", ".zip", ".gz", ".7z", ".rar",
}


def scan_limit() -> int:
    raw = os.environ.get("SANITIZE_MAX_BYTES", str(SCAN_MAX_BYTES_DEFAULT))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("SANITIZE_MAX_BYTES must be a positive integer") from exc
    if value <= 0:
        raise ValueError("SANITIZE_MAX_BYTES must be a positive integer")
    return value


def _folded_key(key: str) -> str:
    return key.replace("-", "_").casefold()


def is_structured_secret_key(key: str) -> bool:
    folded = _folded_key(key)
    if _BENIGN_KEY.fullmatch(key) or _BENIGN_KEY.fullmatch(folded):
        return False
    return bool(_STRUCTURED_KEY.fullmatch(key) or _STRUCTURED_KEY.fullmatch(folded))


def is_text_secret_key(key: str) -> bool:
    folded = _folded_key(key)
    if _BENIGN_KEY.fullmatch(key) or _BENIGN_KEY.fullmatch(folded):
        return False
    if folded.endswith("_token") and folded.startswith("max"):
        return False
    if _TEXT_KEY.fullmatch(key) or _TEXT_KEY.fullmatch(folded):
        return True
    parts = set(folded.split("_"))
    if "password" in parts or "passwd" in parts:
        return True
    if "secret" in parts and "max" not in parts:
        return True
    if {"api", "key"} <= parts or folded.endswith("api_key"):
        return True
    if "access" in parts and "token" in parts:
        return True
    if "refresh" in parts and "token" in parts:
        return True
    if "private" in parts and "key" in parts:
        return True
    if "client" in parts and "secret" in parts:
        return True
    return folded == "authorization"


def _literal_assignment(value: str) -> bool:
    stripped = value.strip().strip(",").strip()
    if not stripped or stripped in {"<", ">", "{", "}", "[", "]"}:
        return False
    lowered = stripped.casefold()
    if any(token in lowered for token in ("(", "os.environ", "process.env", "getenv", "environ[")):
        return False
    return True


def redact_text(text: str) -> tuple[str, int]:
    """Replace high-confidence secrets. The removed text is not returned."""
    if not text:
        return text, 0
    count = 0

    def _pem(match: re.Match) -> str:
        nonlocal count
        count += 1
        return "<redacted-private-key>"

    text = _PEM.sub(_pem, text)

    def _bearer(match: re.Match) -> str:
        nonlocal count
        count += 1
        prefix = match.group(1) or ""
        return f"{prefix}{match.group(2)}{REDACTED}"

    text = _BEARER.sub(_bearer, text)

    def _inline(match: re.Match) -> str:
        nonlocal count
        if not _literal_assignment(match.group("val")):
            return match.group(0)
        count += 1
        return f"{match.group('key')}{match.group('sep')}{REDACTED}"

    text = _INLINE_ASSIGN.sub(_inline, text)

    def _env_line(match: re.Match) -> str:
        nonlocal count
        key = match.group("key")
        if not is_text_secret_key(key) or not _literal_assignment(match.group("val")):
            return match.group(0)
        count += 1
        return f"{match.group('prefix')}{key}{match.group('sep')}{REDACTED}"

    text = _ENV_ASSIGN.sub(_env_line, text)

    def _jsonish(match: re.Match) -> str:
        nonlocal count
        key = match.group("key")
        if not (is_text_secret_key(key) or is_structured_secret_key(key)):
            return match.group(0)
        if not _literal_assignment(match.group("val")):
            return match.group(0)
        count += 1
        quote = match.group("pre")
        return f"{quote}{key}{quote}: {REDACTED}"

    text = _JSONISH.sub(_jsonish, text)
    return text, count


def redact_structured(value):
    """Return (copy, stats). Stats list key names and counts, never values."""
    stats = {"structured_keys": [], "text_redactions": 0}

    def walk(node, key_name: str | None):
        if key_name is not None and is_structured_secret_key(key_name):
            stats["structured_keys"].append(key_name)
            return REDACTED
        if isinstance(node, dict):
            copied = {}
            for key, item in node.items():
                name = key if isinstance(key, str) else None
                copied[key] = walk(item, name)
            return copied
        if isinstance(node, list):
            return [walk(item, None) for item in node]
        if isinstance(node, str):
            redacted, found = redact_text(node)
            stats["text_redactions"] += found
            return redacted
        return node

    return walk(value, None), stats


def _changed(stats: dict) -> bool:
    return bool(stats["structured_keys"] or stats["text_redactions"])


def _suffixes(name: str) -> str:
    folded = name.casefold()
    for suffix in (".db-wal", ".db-shm", ".sqlite-wal", ".sqlite-shm", ".tar.gz"):
        if folded.endswith(suffix):
            return suffix
    return Path(folded).suffix


def unscannable_reason(path: Path) -> str | None:
    """Reason to omit a regular file, or None when it can be scanned."""
    if path.is_symlink():
        return "symlink not scanned"
    try:
        info = path.stat()
    except OSError:
        return "unreadable file"
    if info.st_size > scan_limit():
        return "oversize"
    suffix = _suffixes(path.name)
    if suffix in _DB_SUFFIXES or path.name.casefold().endswith(".db"):
        return "unscannable database"
    if suffix in _BINARY_SUFFIXES:
        return "unscannable binary"
    try:
        preview = path.read_bytes()[:8192]
    except OSError:
        return "unreadable file"
    if preview.startswith(b"SQLite format 3\x00") or b"\x00" in preview:
        return "unscannable binary"
    try:
        path.read_bytes()[:scan_limit()].decode("utf-8")
    except UnicodeDecodeError:
        return "unrecognized encoding"
    except OSError:
        return "unreadable file"
    return None


def _omit(reason: str) -> tuple[None, dict]:
    return None, {
        "structured_keys": [],
        "text_redactions": 0,
        "action": "omitted",
        "reason": reason,
    }


def sanitize_content(name: str, data: bytes) -> tuple[bytes | None, dict]:
    """Return export bytes, or None when the payload must be omitted.

    The stats dict never contains removed secret values.
    """
    if len(data) > scan_limit():
        return _omit("oversize")
    suffix = _suffixes(name)
    if suffix in _DB_SUFFIXES or name.casefold().endswith(".db"):
        return _omit("unscannable database")
    if suffix in _BINARY_SUFFIXES or data.startswith(b"SQLite format 3\x00") or b"\x00" in data[:8192]:
        return _omit("unscannable binary")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return _omit("unrecognized encoding")

    unchanged = {"structured_keys": [], "text_redactions": 0, "action": "copied"}
    memory = name.casefold().endswith(".jsonl")
    if memory or name.casefold().endswith(".json"):
        encoded, stats, parsed = _sanitize_json_document(text, memory=memory)
        if not parsed:
            redacted, found = redact_text(text)
            if found == 0:
                return data, unchanged
            return redacted.encode("utf-8"), {
                "structured_keys": [],
                "text_redactions": found,
                "action": "redacted",
            }
        payload = encoded.encode("utf-8")
        action = "redacted" if _changed(stats) else "copied"
        if action == "copied" and payload == data:
            return data, unchanged
        return payload, {
            "structured_keys": stats["structured_keys"],
            "text_redactions": stats["text_redactions"],
            "action": action,
        }

    redacted, found = redact_text(text)
    if found == 0:
        return data, unchanged
    return redacted.encode("utf-8"), {
        "structured_keys": [],
        "text_redactions": found,
        "action": "redacted",
    }


def _sanitize_json_document(text: str, *, memory: bool) -> tuple[str, dict, bool]:
    """Return (encoded text or None, stats, parsed_ok)."""
    stats = {"structured_keys": [], "text_redactions": 0}
    if memory:
        lines = []
        parsed_any = False
        for line in text.splitlines():
            if not line.strip():
                lines.append(line)
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                redacted, found = redact_text(line)
                stats["text_redactions"] += found
                lines.append(redacted)
                continue
            parsed_any = True
            if isinstance(row, dict) and "fact" in row:
                clean, row_stats = _sanitize_memory_record(row)
            else:
                clean, row_stats = redact_structured(row)
            stats["structured_keys"].extend(row_stats["structured_keys"])
            stats["text_redactions"] += row_stats["text_redactions"]
            lines.append(json.dumps(clean, ensure_ascii=False))
        body = "\n".join(lines)
        if text.endswith("\n") or not text:
            body += "\n"
        return body, stats, True
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return "", stats, False
    clean, stats = redact_structured(parsed)
    if not _changed(stats):
        return text, stats, True
    encoded = json.dumps(clean, ensure_ascii=False, indent=2) + "\n"
    return encoded, stats, True


def _sanitize_memory_record(row: dict) -> tuple[dict, dict]:
    """Redact fact text. Keep date, tier, scope, source, and timestamp fields."""
    clean, stats = redact_structured(row)
    if not isinstance(clean, dict):
        return row, stats
    clean["sanitization"] = "redacted" if _changed(stats) else "inspected"
    return clean, stats


def export_bytes(path: Path) -> bytes | None:
    """Bytes that should appear in an archive for this source file."""
    if unscannable_reason(path):
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    payload, _stats = sanitize_content(path.name, data)
    return payload


def transfer_regular_file(src: Path, dst: Path) -> dict:
    """Write a scanned copy or report an omission. Never includes secret values."""
    reason = unscannable_reason(src)
    if reason:
        return {
            "action": "omitted",
            "reason": reason,
            "structured_keys": [],
            "text_redactions": 0,
        }
    try:
        data = src.read_bytes()
    except OSError:
        return {
            "action": "omitted",
            "reason": "unreadable file",
            "structured_keys": [],
            "text_redactions": 0,
        }
    payload, stats = sanitize_content(src.name, data)
    if payload is None:
        return {
            "action": "omitted",
            "reason": stats.get("reason") or "unscannable",
            "structured_keys": [],
            "text_redactions": 0,
        }
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(payload)
    try:
        shutil.copystat(src, dst, follow_symlinks=False)
    except OSError:
        pass
    action = stats.get("action") or "copied"
    if action not in {"copied", "redacted"}:
        action = "redacted" if _changed(stats) else "copied"
    return {
        "action": action,
        "reason": None,
        "structured_keys": list(stats.get("structured_keys") or []),
        "text_redactions": int(stats.get("text_redactions") or 0),
    }
