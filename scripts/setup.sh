#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
python3.11 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cd "$ROOT/frontend"
if [ -f package.json ]; then npm install; fi
echo "Setup done. Next: cp ../.env.example ../backend/.env (fill secrets), make migrate seed dev"
