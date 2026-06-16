#!/usr/bin/env bash
# Activity Monitor – Unix launcher (dev / testing)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== Activity Monitor ==="

# Virtual environment
if [ ! -d ".venv" ]; then
    echo "[SETUP] Creating virtual environment…"
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "[SETUP] Installing dependencies…"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo "[START] Launching…"
python main.py
