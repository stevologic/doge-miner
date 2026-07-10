from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import uvicorn
import os
from . import miner as _miner_mod
from . import chain
from .pools import POOLS, get_pool
from .wallet_validation import is_valid_doge_wallet
the_miner = _miner_mod.miner

# Chain provider requests show up in the live feed as verbose entries
chain.set_logger(lambda m: the_miner._log(m, verbose=True))

# Docker / env override for default pool (so docker-compose can provide POOL_HOST/POOL_PORT
# without requiring UI config on first start). Request body still wins on /api/start.
if os.environ.get('POOL_HOST'):
    the_miner.pool_host = os.environ.get('POOL_HOST')
if os.environ.get('POOL_PORT'):
    try:
        the_miner.pool_port = int(os.environ.get('POOL_PORT'))
    except (ValueError, TypeError):
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "frontend"))
FRONTEND_INDEX = os.path.join(FRONTEND_DIR, "index.html")

app = FastAPI(title="DogeMiner Full Stack", version="2.0")

# Serve the rich frontend (works for docker /app , start scripts, direct run)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

class StartRequest(BaseModel):
    wallet: str
    mode: str = "cpu"            # "cpu" or "gpu"
    workers: int | None = None   # optional worker count (default: cpu_count-1)
    pool_id: str | None = None   # preset id from /api/pools ("custom" or None = use host/port)
    pool_host: str | None = None # stratum host (used when no/custom preset)
    pool_port: int | None = None # stratum port
    pool_user: str | None = None # stratum username override (registered pools: account.worker)
    pool_pass: str | None = None # stratum password (e.g. "c=DOGE" or worker password)

class StatsResponse(BaseModel):
    running: bool
    mode: str
    worker_count: int
    wallet: str
    hashrate_khs: float
    total_hashes: int
    shares_accepted: int
    shares_rejected: int
    shares_submitted: int = 0
    uptime_seconds: int
    description: str
    pool_link: str
    worker_link: str = ""
    wallet_balance: float = 0.0
    cpu_percent: float = 0.0
    mem_percent: float = 0.0
    gpu_percent: float = 0.0
    pool_connected: bool = False
    pool_authorized: bool = False
    pool_id: str = "zpool"
    pool_host: str = "scrypt.mine.zpool.ca"
    pool_port: int = 3433
    pool_user: str = ""
    pool_difficulty: float = 1.0
    pool_target: str = ""
    pool_error: str = ""
    has_job: bool = False
    last_share_hash: str = ""
    best_share_diff: float = 0.0
    gpu_available: bool = False
    gpu_backend: str = "none"
    gpu_devices: list = []
    gpu_util_source: str = "none"
    # Real Scrypt Effort datapoints (populated from miner when present)
    effort_percent: float = 0.0
    current_nonce: str = "0x00000000"
    luck: float = 100.0
    streak: int = 0
    efficiency: float = 100.0
    effort_text: str = "SEARCHING"
    # Backend stdout messages (from miner prints) streamed to live feed
    backend_logs: list = []

@app.get("/")
async def serve_frontend():
    return FileResponse(FRONTEND_INDEX)

@app.post("/api/start")
async def start_mining(req: StartRequest):
    if not is_valid_doge_wallet(req.wallet):
        raise HTTPException(status_code=400, detail="Invalid Dogecoin wallet address (checksum verified)")
    if req.mode not in ["cpu", "gpu"]:
        raise HTTPException(status_code=400, detail="Mode must be 'cpu' or 'gpu'")
    if req.workers is not None and req.workers <= 0:
        raise HTTPException(status_code=400, detail="workers must be positive if provided")

    # Pool selection: preset id wins for host/port/password defaults; explicit host/port
    # overrides (custom pool); otherwise keep current miner config.
    preset = get_pool(req.pool_id) if req.pool_id else None
    if preset is not None:
        the_miner.pool_id = preset["id"]
        the_miner.pool_host = preset["host"]
        the_miner.pool_port = preset["port"]
        if req.pool_pass is None or not str(req.pool_pass).strip():
            the_miner.pool_pass = preset["password_default"]
    elif req.pool_id:
        the_miner.pool_id = "custom"
    if req.pool_host is not None and req.pool_host.strip():
        the_miner.pool_host = req.pool_host.strip()
        if preset is None:
            the_miner.pool_id = "custom"
    if req.pool_port is not None and req.pool_port > 0:
        the_miner.pool_port = req.pool_port

    # Fresh backend log buffer for this run so UI live feed shows only relevant stdout
    the_miner._recent_logs = []
    the_miner._log_id = 0
    the_miner.start(req.wallet, req.mode, workers=req.workers,
                    pool_user=req.pool_user, pool_pass=req.pool_pass)
    return {"status": "started", "mode": req.mode,
            "pool": f"{the_miner.pool_host}:{the_miner.pool_port}",
            "pool_user": the_miner.pool_user}

@app.post("/api/stop")
async def stop_mining():
    the_miner.stop()
    return {"status": "stopped"}

@app.get("/api/stats", response_model=StatsResponse)
async def get_stats():
    stats = the_miner.get_stats()

    # Pool stats link for the active preset (wallet-login pools link directly to the user);
    # worker_link is the pool's per-worker detail page/API when it has a public one.
    preset = get_pool(stats.get("pool_id", ""))
    user = stats.get("pool_user") or stats.get("wallet") or ""
    worker_link = ""
    if preset:
        pool_link = preset["stats_url"].replace("{user}", user)
        if preset.get("worker_stats_url") and user:
            worker_link = preset["worker_stats_url"].replace("{user}", user)
    else:
        pool_link = f"https://{stats.get('pool_host','')}"

    return {**stats, "pool_link": pool_link, "worker_link": worker_link}

@app.get("/api/pools")
async def list_pools():
    return {"pools": POOLS, "default": "zpool"}

@app.get("/api/mode")
async def get_mode():
    return {"current_mode": the_miner.mode, "description": the_miner.get_stats()["description"]}

# ---- Key-free blockchain telemetry (cached server-side; no tokens, no user rate limits) ----

@app.get("/api/chain/summary")
async def chain_summary():
    s = chain.get_summary()
    if s is None:
        raise HTTPException(status_code=502, detail="All chain data providers unavailable")
    return s

@app.get("/api/chain/blocks")
async def chain_blocks(limit: int = 10):
    b = chain.get_blocks(limit)
    if b is None:
        raise HTTPException(status_code=502, detail="All chain data providers unavailable")
    return {"blocks": b}

@app.get("/api/chain/address/{addr}")
async def chain_address(addr: str):
    if not is_valid_doge_wallet(addr):
        raise HTTPException(status_code=400, detail="Invalid Dogecoin address")
    a = chain.get_address(addr)
    if a is None:
        raise HTTPException(status_code=502, detail="All chain data providers unavailable")
    return a

@app.get("/api/chain/tx/{txid}")
async def chain_tx(txid: str):
    t = chain.get_tx(txid)
    if t is None:
        raise HTTPException(status_code=404, detail="Transaction not found or providers unavailable")
    return t

# Health
@app.get("/api/health")
async def health():
    return {"status": "ok", "backend": "simple-fastapi"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
