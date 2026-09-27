#!/usr/bin/env bash
# Create the skill's virtual environment with pinned dependencies.
# Only `chart` and `report` need it; resolve/fetch/analyze use the standard library.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11+ is required")'
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
echo "Ready. Run: .venv/bin/python scripts/wi.py --help"
