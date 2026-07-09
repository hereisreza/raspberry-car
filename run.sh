#!/usr/bin/env bash
# Run the robot control server on Raspberry Pi / Linux.
# Creates a virtual environment on first run, installs dependencies, then
# starts uvicorn. No internet access is required after the first install.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

echo "Starting server on http://${HOST}:${PORT}"
exec uvicorn server:app --host "$HOST" --port "$PORT"
