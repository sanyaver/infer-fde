#!/usr/bin/env bash
# One-time local setup: venv + deps + browser binaries.
# Run this once before using save_geico_session.py or save_allstate_session.py.
set -euo pipefail

BACKEND="$(cd "$(dirname "$0")/backend" && pwd)"
cd "$BACKEND"

# ── Virtual environment ────────────────────────────────────────────────────────
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

# ── Python deps ────────────────────────────────────────────────────────────────
echo "Installing Python dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# ── Browser binaries ───────────────────────────────────────────────────────────
echo "Installing Chromium (patchright)..."
python -m patchright install chromium

echo "Installing Chrome (playwright — needed for Allstate)..."
python -m playwright install chrome --with-deps

echo ""
echo "Setup complete."
echo ""
echo "Next steps:"
echo "  Geico session (run once per server):"
echo "    cd backend && source .venv/bin/activate && python save_geico_session.py"
echo ""
echo "  Allstate session (run once per server):"
echo "    cd backend && source .venv/bin/activate && python save_allstate_session.py"
echo ""
echo "  Start the full stack:"
echo "    docker-compose up --build"
