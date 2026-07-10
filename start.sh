#!/bin/bash
# DogeMiner launcher for Linux / macOS

set -e

echo "🚀 Starting DogeMiner Full Stack (backend + frontend)..."

cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "Installing dependencies..."
pip install -r backend/requirements.txt -q

# GPU extras are optional — never fail the launch over them
echo "Installing optional GPU (OpenCL) extras (skipped on failure)..."
pip install -r backend/requirements-gpu.txt -q || echo "  -> GPU extras unavailable; CPU mining still works."

echo ""
echo "✅ Backend ready."
echo "🌐 Open http://localhost:8000 in your browser"
echo "   - Pick a pool (zpool needs no registration; wallet = login)"
echo "   - Choose CPU or GPU mode and click START MINING"
echo ""

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
