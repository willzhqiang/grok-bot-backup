"""Copy a prepared handover into raw and Virtual Office exports.

Place the self-report in ``$AGENT_DATA/handover`` before running an export.
The raw archive receives ``handover/``. The Virtual Office archive receives
``00-shared/handover/``. A missing directory is written as an explicit
not-supplied marker. An empty handover is not described as complete.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .common import AGENT_DATA, write
from .sensitive import copy_tree, write_json


NOT_SUPPLIED = "not supplied"
SUPPLIED = "supplied"


def handover_source() -> Path | None:
    path = AGENT_DATA / "handover"
    if path.is_dir() and not path.is_symlink():
        return path
    return None


def _has_files(root: Path) -> bool:
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        if path.is_file():
            return True
    return False


def status_markdown(supplied: bool, report: dict | None = None) -> str:
    if not supplied:
        return """# Handover

- Status: not supplied
- This export does not include a handover self-report.
- Place the self-report in `AGENT_DATA/handover` and export again.
- A missing handover is not a complete handover.
- This file is an explicit gap, not a claim that the backup is complete or secret-free.
"""
    inspected = int((report or {}).get("inspected_files") or 0)
    omitted = len((report or {}).get("omitted_unscannable") or [])
    redacted = len((report or {}).get("redacted") or [])
    return f"""# Handover

- Status: supplied
- Inspected files: {inspected}
- Redacted files: {redacted}
- Omitted unscannable files: {omitted}
- Dates and prose that are not high-confidence credential assignments are kept.
- Unscannable files were left out of this archive.
- This is not a claim that the handover or the export is secret-free.
"""


def coverage_section(supplied: bool) -> str:
    if not supplied:
        state = "not supplied"
        detail = (
            "A missing handover is not a complete handover. "
            "Place a self-report in AGENT_DATA/handover before export. "
            "This is not full platform availability."
        )
    else:
        state = "supplied"
        detail = (
            "Files were inspected. High-confidence credential values were redacted. "
            "Unscannable files were omitted. This is not a secret-free claim."
        )
    return f"\n## Handover\n\n- Status: {state}\n- {detail}\n"


def install_handover(dest: Path, *, label: str) -> dict:
    """Write dest as a sanitized handover directory, or an explicit gap."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    source = handover_source()
    supplied = source is not None and _has_files(source)
    report: dict
    if not supplied:
        report = {
            "label": label,
            "source": str(source) if source else "",
            "destination": str(dest),
            "copied_files": 0,
            "inspected_files": 0,
            "excluded": [],
            "redacted": [],
            "omitted_unscannable": [],
            "skipped": [],
            "handover_status": NOT_SUPPLIED,
        }
    else:
        report = copy_tree(source, dest, label=label)
        report["handover_status"] = SUPPLIED
    write(dest / "HANDOVER_STATUS.md", status_markdown(supplied, report))
    return report


def append_coverage(coverage_md: Path, supplied: bool) -> None:
    path = Path(coverage_md)
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    if "\n## Handover\n" in text or text.startswith("## Handover\n"):
        return
    path.write_text(text.rstrip() + "\n" + coverage_section(supplied), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install a sanitized handover directory")
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--label", default="handover")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--coverage", type=Path)
    args = parser.parse_args()
    report = install_handover(args.dest, label=args.label)
    if args.coverage:
        append_coverage(args.coverage, report.get("handover_status") == SUPPLIED)
    if args.report:
        write_json(args.report, report)
    print(f"HANDOVER_STATUS={report.get('handover_status')}")
    print(f"HANDOVER_INSPECTED={report.get('inspected_files') or 0}")


if __name__ == "__main__":
    main()
