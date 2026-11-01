#!/usr/bin/env bash
# Verify an export folder: bin/verify-export.sh <export-dir> [--archive X.zip] [--date YYYY-MM-DD] [--coverage] [--strict]
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m grok_bot_backup.verify "$@"
