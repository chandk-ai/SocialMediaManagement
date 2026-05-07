#!/usr/bin/env bash
# Run from repo root. Auto-fixes everything ruff can fix safely, plus the
# "unsafe" upgrades (timezone.utc → UTC, AsyncIterator → collections.abc, ...)
# under explicit opt-in.
set -euo pipefail

cd "$(dirname "$0")/../backend"

if ! command -v ruff >/dev/null; then
  echo "→ Installing ruff..."
  pip install ruff
fi

echo "→ ruff check --fix (safe)"
ruff check --fix . || true

echo "→ ruff check --fix --unsafe-fixes (modernize)"
ruff check --fix --unsafe-fixes . || true

echo "→ ruff format ."
ruff format .

echo
echo "Done. Review the changes with:"
echo "    git diff backend/"
