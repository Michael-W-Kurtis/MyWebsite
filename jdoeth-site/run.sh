#!/usr/bin/env bash
# Start the site for local development, from anywhere.
#
# This exists because `uvicorn app.main:app` only works when the current
# directory is the one containing app/ — which is web/, not the repo root.
# Running it from the repo root gives "ModuleNotFoundError: No module named
# 'app'", which does not obviously point at the working directory.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/web"

PORT="${PORT:-8000}"
# 0.0.0.0, not 127.0.0.1: this box is reached by hostname from other machines on
# the LAN, and binding to loopback only would make it unreachable.
HOST="${HOST:-0.0.0.0}"
export DATA_DIR="${DATA_DIR:-$HERE/web/data}"

if [ -d "$HERE/web/.venv" ]; then
  # shellcheck disable=SC1091
  source "$HERE/web/.venv/bin/activate"
fi

python -c "import fastapi, uvicorn, PIL, pixelsort" 2>/dev/null || {
  echo "Dependencies missing. Run:"
  echo "  python3 -m venv web/.venv && source web/.venv/bin/activate"
  echo "  pip install -r web/requirements.txt"
  exit 1
}

# Ruby is only needed by the PNGlitch wrapper. Warn rather than exit: the rest
# of the site is unaffected.
if ! command -v ruby >/dev/null 2>&1; then
  echo "NOTE: ruby not found - the PNGlitch wrapper will not run."
  echo "      sudo apt install ruby-full && sudo gem install pnglitch"
elif ! ruby -e "require 'pnglitch'" >/dev/null 2>&1; then
  echo "NOTE: ruby found, but the pnglitch gem is missing."
  echo "      sudo gem install pnglitch"
fi

echo "Serving from $(pwd)"
echo "  data     $DATA_DIR"
echo "  games    $HERE/web/content/games"
echo "  http://${HOST}:${PORT}"
# Any extra arguments are passed straight through to uvicorn, so
#   ./run.sh --host 127.0.0.1 --port 9000
# still works without editing this file.
exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT" --reload "$@"
