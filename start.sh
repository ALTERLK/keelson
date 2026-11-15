#!/usr/bin/env sh
# One-command start for the Keelson web console (macOS / Linux).
#
#   ./start.sh             live if .env has an API key, otherwise replay
#   ./start.sh --replay    recorded traces only; no model is called
#   ./start.sh --port 9000
set -e
cd "$(dirname "$0")"

ok() { "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; }

if ! { [ -x .venv/bin/python ] && ok .venv/bin/python; }; then
  PY=""
  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && ok "$candidate"; then PY="$candidate"; break; fi
  done
  if [ -z "$PY" ]; then
    echo "Python 3.11 or newer was not found. Install it and run this again." >&2
    exit 1
  fi
  rm -rf .venv
  echo "Creating .venv with $PY"
  "$PY" -m venv .venv
fi

# Reinstall only when pyproject.toml is newer than the last successful install.
STAMP=.venv/.keelson-installed
if [ ! -f "$STAMP" ] || [ pyproject.toml -nt "$STAMP" ]; then
  echo "Installing dependencies (first run takes about a minute)..."
  .venv/bin/python -m pip install --quiet --disable-pip-version-check -e ".[web]"
  touch "$STAMP"
fi

exec .venv/bin/python -m agents.main web "$@"
