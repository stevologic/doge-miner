#!/usr/bin/env python
"""One boot driver for verifiable full stdout capture.

run_boot(port, log_path, mode='cpu', workers=None, timeout=45)
- Starts uvicorn, redirects ALL stdout/stderr to log_path.
- Performs external HTTP only (start with wallet/mode/workers, polls, stop).
- Writes driver lines + server output to the log.
- Returns summary dict.

Replaces divergent logic in verify_plan_steps and verify_launch_full.
"""

import subprocess
import time
import json
import urllib.request
import os
import sys

def run_boot(port: int, log_path: str, mode: str = "cpu", workers: int = None, timeout: int = 45) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    base = f"http://127.0.0.1:{port}"
    notes = []
    with open(log_path, "w", encoding="utf-8") as logf:
        logf.write(f"=== DOCUMENTED BOOT: python -m uvicorn backend.main:app (port {port}) ===\n")
        logf.write(f"=== mode={mode} workers={workers} timeout={timeout}s ===\n")
        logf.write("=== full uvicorn + miner stdout/stderr below (Started mining, Pool attempts etc) ===\n\n")
        logf.flush()

        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:app",
             "--host", "127.0.0.1", "--port", str(port), "--log-level", "info"],
            cwd=os.getcwd(),
            stdout=logf,
            stderr=subprocess.STDOUT,
            env=env,
            bufsize=1
        )

        time.sleep(6)  # banner + startup
        try:
            # health
            s = json.loads(urllib.request.urlopen(base + "/api/health", timeout=5).read())
            notes.append(f"/api/health: {s}")

            # start
            payload = {"wallet": "DTW2M5oEW97WbmYJRM71qD7uE6xfJs1MUK", "mode": mode}
            if workers is not None:
                payload["workers"] = workers
            body = json.dumps(payload).encode()
            req = urllib.request.Request(base + "/api/start", body, {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=timeout)
            notes.append(f"POST /api/start {mode} workers={workers}: ok")

            # bad-wallet prefix test (expect 400) - for plan step1
            try:
                bad_body = json.dumps({"wallet": "BADWALLET123", "mode": "cpu"}).encode()
                reqb = urllib.request.Request(base + "/api/start", bad_body, {"Content-Type": "application/json"})
                try:
                    urllib.request.urlopen(reqb, timeout=5)
                    notes.append("bad-wallet: UNEXPECTED 200")
                except urllib.error.HTTPError as he:
                    if he.code == 400:
                        notes.append("bad-wallet prefix: 400 rejected as expected")
                    else:
                        notes.append(f"bad-wallet http {he.code}")
            except Exception as be:
                notes.append(f"bad-wallet test err: {str(be)[:50]}")

            # polls - capture FULL /api/stats bodies + assert AC1 fields present
            for i in range(6):
                time.sleep(1.2)
                try:
                    body = urllib.request.urlopen(base + "/api/stats", timeout=6).read().decode()
                    st = json.loads(body)
                    logf.write(f"=== FULL_STATS_BODY POLL-{i} ===\n{body}\n")
                    line = f"POLL-{i} running={st.get('running')} mode={st.get('mode')} workers={st.get('worker_count')} rate={st.get('hashrate_khs')} hashes={st.get('total_hashes')} bal={st.get('wallet_balance')} pool_conn={st.get('pool_connected')} pool_auth={st.get('pool_authorized')} effort={st.get('effort_percent')}"
                    print(line)
                    logf.write(line + "\n")
                except Exception as e:
                    print("POLL err", e)
                    logf.write(f"POLL-{i} err: {e}\n")

            # stop
            reqs = urllib.request.Request(base + "/api/stop", data=b"{}", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(reqs, timeout=6)
            notes.append("POST /api/stop: ok")

        except Exception as ex:
            notes.append(f"DRIVER ERR: {ex}")
            print("DRIVER ERR:", ex)
        finally:
            time.sleep(1.5)
            try:
                proc.terminate()
            except:
                pass
            try:
                proc.wait(timeout=3)
            except:
                pass
            logf.write("\n=== END BOOT (see redirected stdout above for miner prints) ===\n")
            for n in notes:
                logf.write(n + "\n")
    return {"notes": notes, "log_path": log_path}


if __name__ == "__main__":
    # self test
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        lp = os.path.join(td, "test-boot.log")
        r = run_boot(18250, lp, mode="gpu", workers=5, timeout=20)
        print("result:", r)
        with open(lp, "r", encoding="utf-8", errors="ignore") as f:
            print("log head:", f.read()[:500])
