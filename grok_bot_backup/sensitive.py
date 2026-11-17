"""Conservative exclusions for credential files, auth databases, and opaque archives.

Matching uses exact file names, known extensions, and exact directory names.
It does not search for the substrings "token", "secret", "session", or
"password" inside ordinary filenames or file contents. This module never
returns file contents and never logs them.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import shutil
import stat
from dataclasses import dataclass


AVAILABILITY_NOTE = (
    "missing=0 is not full platform availability. "
    "Unavailable cells were not found by a disk probe and do not prove the platform has no such data."
)

LIMITATIONS = (
    "Text and JSON are scanned for high-confidence credential assignments and exact secret keys. "
    "This is not a secret-free guarantee. Ordinary prose can still contain a secret that does not "
    "match those patterns. Removed values are not written to logs or reports.",
    "max_tokens and technical discussion of tokens are kept. A JSON key that is exactly token, "
    "api_key, access_token, authorization, password, or private_key is redacted.",
    "Binary databases, opaque attachments, oversize files, and unrecognized encodings are omitted "
    "instead of copied. They are not inspected. store.db and the raw search-index snapshot are "
    "in this group. INCLUDE_AUTH_DATABASES=1 does not put those generic databases back.",
    "Opaque nested archives are not opened. INCLUDE_OPAQUE_ARCHIVES=1 copies them whole and "
    "does not filter files inside them. Previous grok-bot-export and virtual-office archives "
    "stay excluded either way.",
    "Symlinks that leave the source tree or point at an excluded path are omitted and are never "
    "followed. In-tree links to ordinary files are stored as links.",
    "INCLUDE_CREDENTIAL_FILES=1 copies env files, private keys, cookie and session files, "
    "token-store filenames, and credential directories. The tools still do not print file contents.",
    AVAILABILITY_NOTE,
)

_AUTH_DB_NAMES = frozenset({
    "cookies.sqlite",
    "cookies.sqlite3",
    "cookies.db",
    "login data",
    "logins.db",
    "signons.sqlite",
    "key4.db",
    "key3.db",
    "cert9.db",
    "auth.db",
    "auth.sqlite",
    "auth.sqlite3",
    "authentication.db",
    "credentials.db",
    "credentials.sqlite",
    "credentials.sqlite3",
    "sessions.db",
    "sessions.sqlite",
    "sessions.sqlite3",
    "session.db",
    "web data",
})

_CREDENTIAL_FILES = frozenset({
    ".netrc",
    "_netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    ".credentials",
    "credentials",
    "credentials.json",
    "credentials.yml",
    "credentials.yaml",
    "credentials.xml",
    "credentials.txt",
    "credential",
    "credential.json",
    "credential.txt",
    "secrets",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "secrets.txt",
    ".secrets",
    "auth.json",
    "auth.yaml",
    "auth.yml",
    "service-account.json",
    "service_account.json",
    "application_default_credentials.json",
    "gcloud-credentials.json",
    "logins.json",
})

_TOKEN_FILES = frozenset({
    "token.json",
    "tokens.json",
    "access_token",
    "access_token.json",
    "refresh_token",
    "refresh_token.json",
    "access-token",
    "access-token.json",
    "refresh-token",
    "refresh-token.json",
})

_COOKIE_FILES = frozenset({
    "cookies",
    "cookies.txt",
    "cookies.json",
})

_SESSION_FILES = frozenset({
    ".session",
    "sessionstore",
    "sessionstore.json",
    "sessionstore.jsonl",
    "sessions.json",
    "session.json",
})

_CREDENTIAL_DIRS = frozenset({
    ".ssh",
    ".gnupg",
    ".gpg",
    ".aws",
    ".kube",
    ".secrets",
    "secrets",
    "cookies",
    "sessions",
    ".sessions",
    "session-store",
    "token-store",
})

_PRIVATE_KEY_EXTENSIONS = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".jks", ".ppk", ".p8", ".keystore",
})

_PRIVATE_KEY_NAMES = frozenset({
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "id_xmss",
})

_ARCHIVE_SUFFIXES = frozenset({
    ".zip", ".tar", ".tgz", ".tbz", ".tbz2", ".txz", ".tzst", ".7z", ".rar",
    ".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst",
})

_AUTH_DB_EXTENSIONS = frozenset({".kdbx", ".kdb"})
_SIDECARS = ("-wal", "-shm", "-journal")
_CREDENTIAL_KINDS = frozenset({
    "env file",
    "private key",
    "credential file",
    "credential location",
    "cookie store",
    "session store",
    "token store",
})


@dataclass(frozen=True)
class Policy:
    include_credential_files: bool = False
    include_auth_databases: bool = False
    include_opaque_archives: bool = False

    def as_dict(self) -> dict:
        return {
            "include_credential_files": self.include_credential_files,
            "include_auth_databases": self.include_auth_databases,
            "include_opaque_archives": self.include_opaque_archives,
        }


def _flag(name: str) -> bool:
    value = os.environ.get(name, "0")
    if value not in ("0", "1"):
        raise ValueError(f"{name} must be 0 or 1")
    return value == "1"


def load_policy() -> Policy:
    return Policy(
        include_credential_files=_flag("INCLUDE_CREDENTIAL_FILES"),
        include_auth_databases=_flag("INCLUDE_AUTH_DATABASES"),
        include_opaque_archives=_flag("INCLUDE_OPAQUE_ARCHIVES"),
    )


def _folded(name: str) -> str:
    return name.casefold()


def _is_env_name(name: str) -> bool:
    folded = _folded(name)
    return folded == ".env" or folded.startswith(".env.") or folded.endswith(".env")


def _compound_suffix(name: str) -> str:
    folded = _folded(name)
    for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst"):
        if folded.endswith(suffix):
            return suffix
    return PurePosixPath(folded).suffix


def _strip_sidecar(name: str) -> str:
    folded = _folded(name)
    for suffix in _SIDECARS:
        if folded.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _is_auth_database_name(name: str) -> bool:
    base = _strip_sidecar(name)
    folded = _folded(base)
    if _compound_suffix(base) in _AUTH_DB_EXTENSIONS or PurePosixPath(folded).suffix in _AUTH_DB_EXTENSIONS:
        return True
    return folded in _AUTH_DB_NAMES


def file_kind(name: str) -> str | None:
    """Classify one filename. None means the name is ordinary."""
    if _is_auth_database_name(name):
        return "authentication database"
    if _compound_suffix(name) in _ARCHIVE_SUFFIXES:
        return "opaque archive"
    if _is_env_name(name):
        return "env file"
    folded = _folded(name)
    if folded in _PRIVATE_KEY_NAMES or _compound_suffix(name) in _PRIVATE_KEY_EXTENSIONS:
        return "private key"
    if folded in _CREDENTIAL_FILES:
        return "credential file"
    if folded in _COOKIE_FILES:
        return "cookie store"
    if folded in _SESSION_FILES:
        return "session store"
    if folded in _TOKEN_FILES:
        return "token store"
    return None


def directory_kind(name: str) -> str | None:
    if _is_env_name(name):
        return "env file"
    if _folded(name) in _CREDENTIAL_DIRS:
        return "credential location"
    return None


def _kept_by_policy(kind: str, policy: Policy) -> bool:
    if kind == "opaque archive":
        return policy.include_opaque_archives
    if kind == "authentication database":
        return policy.include_auth_databases
    if kind in _CREDENTIAL_KINDS:
        return policy.include_credential_files
    return False


def exclusion_reason(parts: tuple[str, ...], *, is_dir: bool, policy: Policy | None = None) -> str | None:
    """Return a stable reason, or None when the relative path may be copied."""
    if not parts:
        return None
    policy = policy or load_policy()
    directory_parts = parts if is_dir else parts[:-1]
    for component in directory_parts:
        kind = directory_kind(component)
        if kind and not _kept_by_policy(kind, policy):
            return kind
    if is_dir:
        return None
    kind = file_kind(parts[-1])
    if kind and not _kept_by_policy(kind, policy):
        return kind
    return None


def configured_patterns() -> list[str]:
    return [item for item in os.environ.get("BACKUP_EXCLUDE_PATHS", "").split(os.pathsep) if item]


def configured_reason(rel: str, source_root: Path) -> str | None:
    """Match BACKUP_EXCLUDE_PATHS against this source root at any depth.

    A relative pattern matches a path component sequence, not a substring.
    `secrets` matches `secrets` and `a/secrets/file`, and does not match
    `secrets-notes.md`. Absolute patterns match that filesystem location.
    """
    rel = rel.strip("/")
    hay = f"/{rel}/" if rel else "/"
    root = source_root.resolve()
    for value in configured_patterns():
        raw = Path(value).expanduser()
        if raw.is_absolute():
            try:
                target = raw.resolve()
            except OSError:
                target = Path(os.path.abspath(raw))
            if target == root or target in root.parents:
                raise ValueError(f"Refusing to exclude the entire source tree: {target}")
            candidate = Path(os.path.normpath(str(root / rel))) if rel else root
            candidate_text = os.path.normpath(str(candidate))
            target_text = os.path.normpath(str(target))
            if candidate_text == target_text or candidate_text.startswith(target_text + os.sep):
                return "configured exclusion"
            continue
        pattern = os.path.normpath(value).replace("\\", "/").strip("/")
        if pattern in ("", ".") or pattern.startswith("..") or "/../" in f"/{pattern}/":
            raise ValueError(f"Refusing exclusion pattern that escapes the source tree: {value}")
        needle = f"/{pattern}/"
        if needle in hay:
            return "configured exclusion"
    return None


def lexical_target(link: Path) -> Path:
    raw = os.readlink(link)
    if "\x00" in raw:
        raise OSError("symlink target contains NUL")
    joined = Path(raw) if os.path.isabs(raw) else (link.parent / raw)
    return Path(os.path.abspath(os.path.normpath(str(joined))))


def symlink_block_reason(link: Path, source_root: Path, policy: Policy | None = None) -> str | None:
    """None when the link stays inside the source and does not aim at an excluded path."""
    policy = policy or load_policy()
    try:
        lexical = lexical_target(link)
    except OSError:
        return "symlink escapes source root"
    root = source_root.resolve()
    try:
        relative = lexical.relative_to(root)
    except ValueError:
        return "symlink escapes source root"
    rel = relative.as_posix()
    if rel == ".." or rel.startswith("../"):
        return "symlink escapes source root"
    try:
        if lexical.exists():
            lexical.resolve().relative_to(root)
    except (OSError, ValueError):
        return "symlink escapes source root"
    parts = relative.parts
    if parts:
        is_dir = lexical.is_dir() and not lexical.is_symlink()
        if exclusion_reason(parts, is_dir=is_dir, policy=policy):
            return "symlink targets excluded path"
        if configured_reason(rel, root):
            return "symlink targets excluded path"
    return None


def redact_exact_secret_keys(value):
    """Copy JSON-like data, replacing values of exact credential keys.

    The original value is not returned and is not included in errors.
    """
    from .sanitize import redact_structured

    redacted, _stats = redact_structured(value)
    return redacted


def read_regular_text(path: Path, limit: int = 1_000_000) -> str | None:
    """Read a regular file without following a symlink. Never raises for a bad file."""
    if path.is_symlink():
        return None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = -1
    try:
        fd = os.open(path, flags)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            data = handle.read(limit + 1)
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) > limit:
        return None
    return data.decode("utf-8", errors="replace")


def _copy_nofollow(src: Path, dst: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(src, flags)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise OSError("not a regular file")
        dst.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(fd, "rb") as src_f:
            fd = -1
            with dst.open("wb") as out:
                shutil.copyfileobj(src_f, out)
        shutil.copystat(src, dst, follow_symlinks=False)
    finally:
        if fd >= 0:
            os.close(fd)


def _is_regular_nofollow(path: Path) -> bool:
    if path.is_symlink():
        return False
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def copy_tree(
    src: Path,
    dst: Path,
    *,
    source_root: Path | None = None,
    extra_reason=None,
    apply_configured: bool = True,
    policy: Policy | None = None,
    label: str | None = None,
) -> dict:
    """Copy src into dst without following links that leave the tree.

    extra_reason(entry, relative_posix) may return a structural skip reason
    such as a previous backup or a bulky directory. It runs before credential
    rules so previous export archives keep their existing reason.
    """
    src = Path(src)
    if not src.is_dir() or src.is_symlink():
        raise NotADirectoryError(f"Source directory does not exist: {src}")
    src_root = src.resolve()
    root = Path(source_root).resolve() if source_root is not None else src_root
    dst = Path(dst)
    policy = policy or load_policy()

    def _refuse_root(reason: str) -> dict:
        return {
            "label": label or str(src_root),
            "source": str(src_root),
            "destination": str(dst),
            "copied_files": 0,
            "excluded": [{"path": src.name, "reason": reason}],
            "skipped": [f"{src.name} ({reason})"],
            "policy": policy.as_dict(),
            "limitations": list(LIMITATIONS),
        }

    own_kind = directory_kind(src.name)
    if own_kind and not _kept_by_policy(own_kind, policy):
        return _refuse_root(own_kind)
    if apply_configured:
        configured = configured_reason(src.name, src.parent)
        if configured:
            return _refuse_root(configured)
    dst.mkdir(parents=True, exist_ok=True)
    try:
        dst_resolved = dst.resolve()
    except OSError:
        dst_resolved = dst
    excluded: list[dict] = []
    skipped: list[str] = []
    redacted: list[dict] = []
    omitted_unscannable: list[dict] = []
    copied = 0
    inspected = 0

    def record(rel: str, reason: str) -> None:
        excluded.append({"path": rel, "reason": reason})
        skipped.append(f"{rel} ({reason})")

    def reason_for(entry: Path, rel: str) -> str | None:
        if not entry.is_symlink():
            try:
                resolved = entry.resolve()
            except OSError:
                resolved = None
            if resolved is not None and (resolved == dst_resolved or dst_resolved in resolved.parents):
                return "copy destination"
        if entry.is_symlink():
            blocked = symlink_block_reason(entry, root, policy)
            if blocked:
                return blocked
        is_dir = entry.is_dir() and not entry.is_symlink()
        if extra_reason is not None:
            structural = extra_reason(entry, rel)
            if structural:
                return structural
        if apply_configured:
            configured = configured_reason(rel, root)
            if configured:
                return configured
        return exclusion_reason(tuple(PurePosixPath(rel).parts), is_dir=is_dir, policy=policy)

    def walk(directory: Path) -> None:
        nonlocal copied, inspected
        for entry in sorted(directory.iterdir(), key=lambda item: item.name):
            rel = entry.relative_to(src_root).as_posix()
            reason = reason_for(entry, rel)
            if reason:
                record(rel, reason)
                continue
            target = dst / entry.relative_to(src_root)
            if entry.is_symlink():
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.is_symlink() or target.exists():
                    target.unlink()
                target.symlink_to(os.readlink(entry))
                copied += 1
                continue
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                walk(entry)
                continue
            if _is_regular_nofollow(entry):
                from .sanitize import transfer_regular_file

                outcome = transfer_regular_file(entry, target)
                if outcome["action"] == "omitted":
                    reason = outcome.get("reason") or "unscannable"
                    record(rel, reason)
                    omitted_unscannable.append({"path": rel, "reason": reason})
                    continue
                inspected += 1
                copied += 1
                if outcome["action"] == "redacted" or outcome["structured_keys"] or outcome["text_redactions"]:
                    redacted.append({
                        "path": rel,
                        "structured_keys": outcome["structured_keys"],
                        "text_redactions": outcome["text_redactions"],
                    })
                continue
            record(rel, "unsupported file type")

    walk(src_root)
    excluded.sort(key=lambda item: item["path"])
    redacted.sort(key=lambda item: item["path"])
    omitted_unscannable.sort(key=lambda item: item["path"])
    return {
        "label": label or str(src_root),
        "source": str(src_root),
        "destination": str(dst),
        "copied_files": copied,
        "inspected_files": inspected,
        "excluded": excluded,
        "redacted": redacted,
        "omitted_unscannable": omitted_unscannable,
        "skipped": sorted(set(skipped)),
        "policy": policy.as_dict(),
        "limitations": list(LIMITATIONS),
    }


def classify_tree(root: Path, policy: Policy | None = None) -> tuple[int, int]:
    """Return (eligible_files, policy_blocked_files) without following links."""
    policy = policy or load_policy()
    if not root.exists() and not root.is_symlink():
        return (0, 0)
    if root.is_symlink() or root.is_file():
        blocked = symlink_block_reason(root, root.parent, policy) if root.is_symlink() else None
        kind = exclusion_reason((root.name,), is_dir=False, policy=policy)
        if blocked or kind:
            return (0, 1)
        return (1, 0)
    own_kind = directory_kind(root.name)
    if own_kind and not _kept_by_policy(own_kind, policy):
        return (0, 1)
    root = root.resolve()
    eligible = 0
    blocked = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        parent = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            entry = parent / name
            rel = entry.relative_to(root).as_posix()
            parts = tuple(PurePosixPath(rel).parts)
            if entry.is_symlink():
                if symlink_block_reason(entry, root, policy) or exclusion_reason(parts, is_dir=False, policy=policy):
                    blocked += 1
                else:
                    eligible += 1
                continue
            if exclusion_reason(parts, is_dir=True, policy=policy):
                blocked += 1
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            entry = parent / name
            rel = entry.relative_to(root).as_posix()
            parts = tuple(PurePosixPath(rel).parts)
            if entry.is_symlink():
                if symlink_block_reason(entry, root, policy) or exclusion_reason(parts, is_dir=False, policy=policy):
                    blocked += 1
                else:
                    eligible += 1
                continue
            from .sanitize import unscannable_reason
            if (
                exclusion_reason(parts, is_dir=False, policy=policy)
                or not _is_regular_nofollow(entry)
                or unscannable_reason(entry)
            ):
                blocked += 1
            else:
                eligible += 1
    return eligible, blocked


def filtered_presence(src: Path, dest: Path | None = None, policy: Policy | None = None) -> str:
    """Coverage cell that treats policy exclusions as partial, not missing."""
    if not src.exists() and not src.is_symlink():
        return "unavailable"
    eligible, blocked = classify_tree(src, policy)
    if eligible == 0 and blocked == 0:
        return "unavailable"
    if eligible == 0:
        return "partial"
    if dest is None:
        return "partial" if blocked else "ok"
    if not dest.exists() and not dest.is_symlink():
        return "missing"
    copied, _dest_blocked = classify_tree(dest, policy)
    if copied < eligible:
        return "missing" if copied == 0 else "partial"
    return "partial" if blocked else "ok"


def scrub_tree(root: Path, policy: Policy | None = None) -> list[dict]:
    """Delete excluded files already written under root. Returns path records only."""
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        return []
    policy = policy or load_policy()
    root = root.resolve()
    removed: list[dict] = []

    def delete(entry: Path) -> None:
        if entry.is_symlink() or entry.is_file():
            entry.unlink(missing_ok=True)
        elif entry.is_dir():
            shutil.rmtree(entry)

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        parent = Path(dirpath)
        kept: list[str] = []
        for name in dirnames:
            entry = parent / name
            rel = entry.relative_to(root).as_posix()
            parts = tuple(PurePosixPath(rel).parts)
            reason = None
            if entry.is_symlink():
                reason = symlink_block_reason(entry, root, policy) or exclusion_reason(parts, is_dir=False, policy=policy)
            elif exclusion_reason(parts, is_dir=True, policy=policy):
                reason = exclusion_reason(parts, is_dir=True, policy=policy)
            if reason:
                removed.append({"path": rel, "reason": reason})
                delete(entry)
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            entry = parent / name
            rel = entry.relative_to(root).as_posix()
            parts = tuple(PurePosixPath(rel).parts)
            reason = None
            if entry.is_symlink():
                reason = symlink_block_reason(entry, root, policy) or exclusion_reason(parts, is_dir=False, policy=policy)
            else:
                reason = exclusion_reason(parts, is_dir=False, policy=policy)
            if reason:
                removed.append({"path": rel, "reason": reason})
                delete(entry)
    removed.sort(key=lambda item: item["path"])
    return removed


def combine_reports(reports: list[dict], policy: Policy | None = None) -> dict:
    policy = policy or load_policy()
    excluded = []
    redacted = []
    omitted = []
    inspected = 0
    for report in reports:
        label = report.get("label") or report.get("source") or ""
        inspected += int(report.get("inspected_files") or 0)
        for item in report.get("excluded") or []:
            path = item.get("path")
            reason = item.get("reason")
            if not isinstance(path, str) or not isinstance(reason, str):
                continue
            excluded.append({"source": label, "path": path, "reason": reason})
        for item in report.get("redacted") or []:
            path = item.get("path")
            if not isinstance(path, str):
                continue
            redacted.append({
                "source": label,
                "path": path,
                "structured_keys": list(item.get("structured_keys") or []),
                "text_redactions": int(item.get("text_redactions") or 0),
            })
        for item in report.get("omitted_unscannable") or []:
            path = item.get("path")
            reason = item.get("reason")
            if isinstance(path, str) and isinstance(reason, str):
                omitted.append({"source": label, "path": path, "reason": reason})
    excluded.sort(key=lambda item: (item["source"], item["path"]))
    redacted.sort(key=lambda item: (item["source"], item["path"]))
    omitted.sort(key=lambda item: (item["source"], item["path"]))
    return {
        "excluded_count": len(excluded),
        "excluded": excluded,
        "inspected_files": inspected,
        "redacted_count": len(redacted),
        "redacted": redacted,
        "omitted_unscannable_count": len(omitted),
        "omitted_unscannable": omitted,
        "policy": policy.as_dict(),
        "limitations": list(LIMITATIONS),
        "coverage_note": AVAILABILITY_NOTE,
        "scan_note": (
            "inspected_files were decoded and scanned. redacted lists paths and key names only. "
            "omitted_unscannable files were not copied. This is not a secret-free claim."
        ),
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    import json
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_copy_reports(directory: Path) -> list[dict]:
    import json
    reports = []
    if not directory.is_dir():
        return reports
    names = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        if path.name == "workspace-copy.json" or path.name.endswith("-copy.json") or path.name == "source-exclusions.json":
            names.append(path)
    for path in sorted(names, key=lambda item: item.name):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and "excluded" in data:
            data.setdefault("label", path.stem)
            reports.append(data)
    return reports
