# DogeMiner Full-Stack

A full-stack, **real** Dogecoin pool miner with a rich dashboard: Python/FastAPI backend,
single-page frontend, CPU **and** OpenCL GPU mining, selectable pools (with or without
registration), and a key-free live blockchain explorer.

> Educational project. Everything on screen is real (no simulated numbers), but CPU/GPU
> scrypt hashrates are tiny compared to ASICs — expect real accepted shares, not riches.

## What's real (verified by tests + live runs)

- **Scrypt PoW** (`N=1024, r=1, p=1`) — validated against the **Dogecoin genesis block**
  and block 1 (header serialization, stratum prevhash word-order, little-endian target
  comparison, pool diff-1 target `0xffff·2²²⁴`).
- **Stratum pool mining** — subscribe/authorize/notify/submit against live pools;
  accepted/rejected counters come *only* from pool responses. Live-verified with real
  accepted shares on zpool.
- **GPU mining** — a full scrypt OpenCL kernel (NVIDIA/AMD/Intel; Windows/macOS/Linux).
  The kernel must pass a self-test against `hashlib.scrypt` before it is used, and every
  GPU share candidate is re-verified on the CPU before submission. No usable GPU →
  automatic CPU fallback, honestly labeled (`gpu_backend: "cpu-fallback"`).
- **CPU mining** — `hashlib.scrypt` releases the GIL, so worker threads scale across
  cores (default: cores−1 workers).
- **Telemetry** — rolling hashrate, total hashes, shares submitted/accepted/rejected,
  pool difficulty + share target, last PoW hash, best share difficulty, luck (actual vs
  statistically expected shares), efficiency, streak, CPU/MEM (psutil), GPU utilization
  (nvidia-smi), on-chain wallet balance, live DOGE price.
- **Blockchain explorer, no API keys** — height, difficulty, network hashrate, price,
  mempool, recent blocks, address & transaction lookups. Multi-provider failover
  (Blockbook → Blockchair → BlockCypher, CoinGecko for price) with backend caching, so
  users never hit provider rate limits themselves; stale data is served (and labeled)
  if every provider is down.

## Quick start

**Windows**: double-click `start.bat` (or `powershell -ExecutionPolicy Bypass -File .\start.ps1`)

**macOS / Linux**:
```bash
./start.sh
```

**Docker**:
```bash
docker compose up --build
```

Then open **http://localhost:8000**, enter your DOGE address (checksum-verified),
pick a pool, choose CPU or GPU, and START MINING.

## Pools

| Pool | Registration | Login | Payout |
|------|--------------|-------|--------|
| **zpool.ca** (default) | none | your DOGE wallet address | DOGE direct to wallet |
| AikaPool | free account | `username.worker` | DOGE |
| litecoinpool.org | free account | `username.worker` | LTC (merged-mines DOGE) |
| F2Pool | free account | `account.worker` | LTC + DOGE (merged) |
| Custom | — | anything | your pool's rules |

All preset hosts/ports were verified live (DNS + stratum handshake). Zergpool was
removed — it shut down permanently in September 2025.

Tips: zpool honors password options like `c=DOGE` (payout coin) and `d=N` (static
difficulty). Low static difficulty (e.g. `c=DOGE,d=64`) gets CPU/GPU miners frequent
accepted shares.

## GPU notes

- Needs `pyopencl` + `numpy` (installed automatically by the start scripts; skipped
  gracefully if unavailable) and an OpenCL runtime from your GPU driver.
- Multiple GPUs: the discrete GPU is preferred automatically; override with the
  `DOGE_GPU_DEVICE=<index>` environment variable. Batch size: `DOGE_GPU_BATCH`.
- GPU utilization telemetry: nvidia-smi when available; on AMD/Intel the miner reports
  the real OpenCL kernel duty cycle instead (source shown in the UI).
- Docker GPU passthrough requires the NVIDIA Container Toolkit and an OpenCL ICD in the
  image; otherwise the container mines on CPU.

## Frontend styling (no CDNs)

The UI ships a locally compiled Tailwind v4 stylesheet (`frontend/tailwind.css`) —
no runtime CDN compile, no external CSS dependency, works offline (the retro logo
font falls back to system-ui offline). To rebuild after markup changes:

```bash
npm i tailwindcss @tailwindcss/cli
# input.css: @import "tailwindcss"; @source "<path>/frontend/index.html";
npx @tailwindcss/cli -i input.css -o frontend/tailwind.css --minify
```

## API

- `POST /api/start` `{wallet, mode: cpu|gpu, workers?, pool_id?, pool_host?, pool_port?, pool_user?, pool_pass?}`
- `POST /api/stop`
- `GET /api/stats` — full mining + system telemetry (poll it; it never blocks)
- `GET /api/pools` — pool presets with registration info
- `GET /api/chain/summary` | `/api/chain/blocks?limit=N` | `/api/chain/address/{addr}` | `/api/chain/tx/{txid}`
- `GET /api/health`

Env overrides: `POOL_HOST` / `POOL_PORT` (initial pool without UI config).

## Tests

```bash
python -m unittest backend.tests.test_miner backend.tests.test_chain_pools
```

52 tests, including known-chain vectors (Dogecoin genesis + block 1), the full
worker→submit→accept path over an injected stratum socket, chain-provider failover and
caching, wallet Base58Check validation (backend/frontend parity), and — on machines with
an OpenCL GPU — kernel-vs-CPU self-test and a full GPU mining integration test.

## Honesty / transparency

- Share counters are driven exclusively by real pool responses.
- "WALLET BALANCE" is the live on-chain balance for your address.
- You will never receive DOGE *from this app* — payouts come from your pool once your
  contributed shares reach its minimum payout (check the pool stats link in the header).
