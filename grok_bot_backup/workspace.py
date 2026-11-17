"""Copy workspace files without ever copying backup output into itself.

Credential files, authentication databases, and opaque archives are excluded by
the shared policy in grok_bot_backup.sensitive. Operators can exclude more paths
with BACKUP_EXCLUDE_PATHS, separated by os.pathsep (: on Linux). The same
relative pattern is applied to every source root, not only this workspace.
The active OUT_ROOT and WORKSPACE/exports are always excluded.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .sensitive import copy_tree


def bulky(name: str) -> bool:
    return name.startswith(".venv") or name in {".cache", "node_modules", "__pycache__", ".git"}


def generated_backup_artifact(path: Path) -> str | None:
    """Omit old exports wherever they live, even when OUT_ROOT changes.

    Only directories and supported archive files count: ordinary documents
    with a similar prefix are not treated as previously generated backups.
    """
    if not path.name.startswith(("grok-bot-export-", "virtual-office-")):
        return None
    if path.is_dir():
        return "previous backup directory"
    if path.is_file() and path.name.endswith((".tar.gz", ".tgz", ".tar", ".zip")):
        return "previous backup archive"
    return None


def enabled() -> bool:
    value = os.environ.get("INCLUDE_BULKY_DIRS", "0")
    if value not in ("0", "1"):
        raise ValueError("INCLUDE_BULKY_DIRS must be 0 or 1")
    return value == "1"


def exclusions(workspace: Path, out_root: Path) -> dict[Path, str]:
    """Resolve configured exclusions without following links during copying."""
    workspace = workspace.resolve()
    targets = {
        (workspace / "exports").resolve(): "default backup directory",
        out_root.resolve(): "backup output",
    }
    for value in filter(None, os.environ.get("BACKUP_EXCLUDE_PATHS", "").split(os.pathsep)):
        raw = Path(value).expanduser()
        target = (raw if raw.is_absolute() else workspace / raw).resolve()
        if target == workspace or target in workspace.parents:
            raise ValueError(f"Refusing to exclude the entire workspace: {target}")
        targets[target] = "configured exclusion"
    return targets


def copy_workspace(src: Path, dst: Path, out_root: Path) -> dict:
    """Copy selected files safely and report each deliberately skipped path.

    Refuses unsafe output locations before making any copy. Paths are compared
    by resolved absolute location, preventing recursion through nested folders
    or symbolic links. In-tree symlinks are preserved. Links that leave the
    workspace, or that point at an excluded path, are omitted and not followed.
    """
    include = enabled()
    src = src.resolve()
    output = out_root.resolve()
    dst = dst.resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"WORKSPACE not found: {src}")
    if output == src or output in src.parents:
        raise ValueError(f"OUT_ROOT must not contain WORKSPACE: {output}")
    if dst == src or (src in dst.parents and not (dst == output or output in dst.parents)):
        raise ValueError(f"Destination inside WORKSPACE but outside OUT_ROOT: {dst}")

    excluded = exclusions(src, output)

    def extra(entry: Path, rel: str) -> str | None:
        if not entry.is_symlink():
            try:
                target = entry.resolve()
            except OSError:
                target = entry
            reason = next((why for root, why in excluded.items()
                           if target == root or root in target.parents), None)
            if reason:
                return reason
        reason = generated_backup_artifact(entry)
        if reason:
            return reason
        if not include and entry.is_dir() and bulky(entry.name):
            return "bulky directory"
        return None

    report = copy_tree(src, dst, extra_reason=extra, label="workspace")
    report["include_bulky_dirs"] = include
    report["excluded_roots"] = [str(path) for path in sorted(excluded)]
    return report


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 5:
        raise SystemExit("usage: python -m grok_bot_backup.workspace <src> <dst> <out_root> <report.json>")
    result = copy_workspace(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
    report = Path(sys.argv[4])
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"WORKSPACE_BULKY={'included' if result['include_bulky_dirs'] else 'excluded'}; SKIPPED={len(result['skipped'])}")
