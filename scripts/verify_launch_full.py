#!/usr/bin/env python
"""Verification driver: documented entry ONLY.
Boots `python -m uvicorn backend.main:app` and redirects its FULL stdout/stderr
directly into SCRATCH/launch-run-N.log (no separate server.log, no pure summaries).
Performs external HTTP POST/GET only (urllib).
Saves everything to the canonical SCRATCH.
"""
import subprocess, time, json, urllib.request, os, sys

SCRATCH = r"C:\Users\steph\AppData\Local\Temp\grok-goal-ee1345bd7c97\implementer"  # current goal scratch (fixed)
os.makedirs(SCRATCH, exist_ok=True)

def run_one(name, port, mode="cpu", workers=None):
    runlog = os.path.join(SCRATCH, f"{name}.log")
    # Use the single driver for full stdout/stderr capture (prints like Started GPU mining).
    # Fix relative import for direct script run: add scripts dir to path
    import sys
    import os as _os
    _scripts_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    import boot_capture
    boot_capture.run_boot(port, runlog, mode=mode, workers=workers, timeout=45)
    print(f"Wrote FULL boot output to {runlog}")
    with open(runlog, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    for marker in ["Uvicorn running", "Started CPU mining", "Started GPU mining", "POLL-", "bad-wallet"]:
        if marker in content:
            print("  FOUND:", marker)

if __name__ == "__main__":
    run_one("launch-run-1", 18200, mode="cpu", workers=4)
    time.sleep(2)
    run_one("launch-run-2", 18201, mode="gpu", workers=5)
    print("=== launch-run logs now contain full uvicorn stdout/stderr + external driver evidence ===")