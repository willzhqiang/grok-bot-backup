"""Copy one source tree with the shared credential and symlink policy."""
from __future__ import annotations

import argparse
from pathlib import Path

from .sensitive import LIMITATIONS, combine_reports, copy_tree, load_copy_reports, load_policy, write_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy a tree with credential and symlink exclusions")
    parser.add_argument("src", nargs="?", type=Path)
    parser.add_argument("dst", nargs="?", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--label", default=None)
    parser.add_argument("--skip-dir-prefix", action="append", default=[])
    parser.add_argument("--combine", type=Path, help="Directory of *-copy.json reports to merge")
    parser.add_argument("--output", type=Path, help="Where to write the combined exclusion report")
    args = parser.parse_args()

    if args.combine is not None:
        if args.output is None:
            raise SystemExit("--output is required with --combine")
        payload = combine_reports(load_copy_reports(args.combine))
        write_json(args.output, payload)
        print(f"EXCLUSION_REPORT={args.output}")
        print(f"EXCLUDED_FILES={payload['excluded_count']}")
        return

    if args.src is None or args.dst is None or args.report is None:
        raise SystemExit("usage: python -m grok_bot_backup.tree_copy SRC DST --report FILE")

    prefixes = tuple(args.skip_dir_prefix)

    def extra(entry: Path, rel: str) -> str | None:
        if prefixes and (entry.is_dir() or entry.is_symlink()) and entry.name.startswith(prefixes):
            return "sand-subagent transcripts (default)"
        return None

    if not args.src.is_dir():
        payload = {
            "label": args.label or str(args.src),
            "source": str(args.src),
            "destination": str(args.dst),
            "copied_files": 0,
            "excluded": [],
            "skipped": [],
            "policy": load_policy().as_dict(),
            "limitations": list(LIMITATIONS),
        }
        write_json(args.report, payload)
        print("COPIED_FILES=0")
        print("EXCLUDED_FILES=0")
        return

    report = copy_tree(args.src, args.dst, extra_reason=extra if prefixes else None, label=args.label)
    write_json(args.report, report)
    print(f"COPIED_FILES={report['copied_files']}")
    print(f"EXCLUDED_FILES={len(report['excluded'])}")


if __name__ == "__main__":
    main()
