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
echo "🌐 Open http://localhost:8000 — or http://doge.local (mDNS) from any device on your network"
echo "   - Pick a pool (zpool needs no registration; wallet = login)"
echo "   - Choose CPU or GPU mode and click START MINING"
echo ""

echo "Live activity (stratum wire, hashing, shares, chain requests) streams below."
echo "Verbose lines are tagged [v]. The browser live feed mirrors this with a VERBOSE toggle."
echo ""

# -u = unbuffered stdout so verbose data appears live in this terminal
PYTHONUNBUFFERED=1 python -u -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
