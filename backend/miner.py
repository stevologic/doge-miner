import hashlib
import os
import threading
import time
import random
import socket
import json
import binascii
import struct
import queue
from typing import Optional, Dict, Any, Callable
import decimal
from decimal import Decimal

try:
    import psutil
except ImportError:
    psutil = None

import shutil
import subprocess


# --- Pure share-evaluation module (module-level, side-effect free, unit-testable) ---
# Conventions match the reference scrypt miners (pooler/cpuminer, cgminer, nightminer):
#  * pool ("scrypt") difficulty 1 corresponds to target 0x0000ffff << 224
#  * the scrypt PoW hash is interpreted as a little-endian 256-bit integer
#  * stratum prevhash is delivered as eight uint32 words, each hex-encoded big-endian:
#    the header wants each 4-byte word byte-swapped (NOT a full 32-byte reverse)
#  * the merkle root from the sha256d chain is used as-is (internal byte order)
# These were all wrong before this rewrite, which made real pool shares invalid.

DIFF1_TARGET = 0xffff << 224  # scrypt pool difficulty-1 target
MAX_TARGET = (1 << 256) - 1


def diff_to_target_int(diff: float) -> int:
    """Integer share target for a pool difficulty (Decimal with enough precision for
    fractional diffs; the default 28-digit context would truncate the 72-digit target)."""
    if diff is None or diff <= 0:
        return MAX_TARGET
    with decimal.localcontext() as ctx:
        ctx.prec = 100
        t = int(Decimal(DIFF1_TARGET) / Decimal(str(diff)))
    return min(t, MAX_TARGET)


def nbits_to_target(nbits_hex: str) -> int:
    """Decode compact 'bits' (e.g. 1e0ffff0) into the full 256-bit block target."""
    n = int(nbits_hex, 16)
    exp = n >> 24
    mant = n & 0x00FFFFFF
    if exp <= 3:
        return mant >> (8 * (3 - exp))
    return mant << (8 * (exp - 3))


def target_to_hex(target: int) -> str:
    return f"{min(max(target, 0), MAX_TARGET):064x}"


def hash_meets_target(hash_int: int, target: int) -> bool:
    return hash_int < target


def scrypt_hash_int(hash_bytes: bytes) -> int:
    """Interpret a 32-byte scrypt PoW output as the little-endian integer used for target checks."""
    return int.from_bytes(hash_bytes, "little")


def share_difficulty(hash_bytes: bytes) -> float:
    """Actual pool-difficulty achieved by a hash (for best-share telemetry)."""
    h = int.from_bytes(hash_bytes, "little")
    if h <= 0:
        return float("inf")
    return DIFF1_TARGET / h


def swap_endian_words(hex_words: str) -> bytes:
    """Byte-swap each 4-byte word of a hex string (stratum prevhash wire format -> header bytes)."""
    msg = binascii.unhexlify(hex_words)
    if len(msg) % 4 != 0:
        raise ValueError("hex string must be 4-byte word aligned")
    return b"".join(msg[4 * i:4 * i + 4][::-1] for i in range(len(msg) // 4))


def build_coinbase(coinb1: str, coinb2: str, extranonce1: str, extranonce2: str) -> bytes:
    c1 = binascii.unhexlify(coinb1)
    c2 = binascii.unhexlify(coinb2)
    en1 = binascii.unhexlify(extranonce1) if extranonce1 else b""
    en2 = binascii.unhexlify(extranonce2) if extranonce2 else b""
    return c1 + en1 + en2 + c2


def merkle_root(coinbase: bytes, branches: list) -> bytes:
    root = hashlib.sha256(hashlib.sha256(coinbase).digest()).digest()
    for branch in branches:
        b = binascii.unhexlify(branch)
        root = hashlib.sha256(hashlib.sha256(root + b).digest()).digest()
    return root


def build_header(version_hex: str, prevhash: str, merkle_root_b: bytes, ntime: str, nbits: str, nonce: int) -> bytes:
    version = struct.pack("<I", int(version_hex, 16))
    prev = swap_endian_words(prevhash)  # per-word swap of the stratum wire format
    merkle = merkle_root_b  # sha256d chain output is already in header (internal) byte order
    ntime_b = binascii.unhexlify(ntime)[::-1]
    nbits_b = binascii.unhexlify(nbits)[::-1]
    nonce_b = struct.pack("<I", nonce)
    return version + prev + merkle + ntime_b + nbits_b + nonce_b


def build_header_prefix(job: Dict[str, Any], extranonce1: str, extranonce2: str, ntime: str) -> bytes:
    """76-byte header prefix (everything except the nonce). Workers compute this once per
    job+extranonce and only append the packed nonce per hash — the coinbase/merkle work
    does not depend on the nonce, so rebuilding it every hash was pure waste."""
    coinbase = build_coinbase(
        job.get("coinb1", ""),
        job.get("coinb2", ""),
        extranonce1 or "",
        extranonce2 or "",
    )
    merkle = merkle_root(coinbase, job.get("merkle_branch", []))
    ntime_use = ntime or job.get("ntime") or hex(int(time.time()))[2:].zfill(8)
    version = struct.pack("<I", int(job.get("version", "00000001"), 16))
    prev = swap_endian_words(job.get("prevhash", "00" * 32))
    ntime_b = binascii.unhexlify(ntime_use)[::-1]
    nbits_b = binascii.unhexlify(job.get("nbits", "1d00ffff"))[::-1]
    return version + prev + merkle + ntime_b + nbits_b


def default_scrypt(data: bytes) -> bytes:
    """Real scrypt with Dogecoin/Litecoin PoW parameters (N=1024, r=1, p=1)."""
    return hashlib.scrypt(data, salt=data, n=1024, r=1, p=1, maxmem=0, dklen=32)


def evaluate_share(
    job: Dict[str, Any],
    extranonce1: str,
    extranonce2: str,
    ntime: str,
    nonce: int,
    user: str,
    difficulty: float,
    scrypt_fn=None,
) -> Optional[str]:
    """Pure: assemble coinbase/merkle/header from pool job, real scrypt, check vs target.
    Returns formatted mining.submit string on success (meets), else None. No side effects, no I/O.
    `user` is the stratum username the pool authorized (wallet for no-registration pools,
    account.worker for registered pools)."""
    submit, _ = evaluate_share_ex(job, extranonce1, extranonce2, ntime, nonce, user, difficulty, scrypt_fn)
    return submit


def evaluate_share_ex(
    job: Dict[str, Any],
    extranonce1: str,
    extranonce2: str,
    ntime: str,
    nonce: int,
    user: str,
    difficulty: float,
    scrypt_fn=None,
):
    """Like evaluate_share but also returns the raw 32-byte hash (or None on error)."""
    if not job or not user:
        return None, None
    if scrypt_fn is None:
        scrypt_fn = default_scrypt
    try:
        ntime_use = ntime or job.get("ntime") or hex(int(time.time()))[2:].zfill(8)
        prefix = build_header_prefix(job, extranonce1, extranonce2, ntime_use)
        header = prefix + struct.pack("<I", nonce)
        hash_result = scrypt_fn(header)
        hash_int = int.from_bytes(hash_result, "little")
        target = diff_to_target_int(difficulty)
        if hash_meets_target(hash_int, target):
            job_id = job.get("job_id", "")
            nonce_hex = f"{nonce:08x}"
            submit = json.dumps({
                "id": 4,
                "method": "mining.submit",
                "params": [user, job_id, extranonce2, ntime_use, nonce_hex]
            }) + "\n"
            return submit, hash_result
        return None, hash_result
    except (binascii.Error, struct.error, ValueError, TypeError, KeyError):
        return None, None


# --- Pure real-datapoint derivations for Scrypt Effort div (module-level, side-effect free) ---
def compute_effort_percent(total_hashes: int, uptime_seconds: int) -> float:
    """Real progress % for effort bar derived from actual hash work and time (monotonic increasing with work)."""
    if total_hashes <= 0 or uptime_seconds <= 0:
        return 0.0
    base = 100 * (1 - 1 / (1 + total_hashes / 500.0))
    rate = total_hashes / uptime_seconds
    bonus = min(10.0, rate / 20.0)
    return min(99.9, base + bonus)


def compute_current_nonce(last_nonce: int) -> str:
    """Representative real nonce from worker search state, formatted for display."""
    return f"0x{last_nonce:08x}"


def compute_luck(shares_accepted: int, total_hashes: int, difficulty: float = 0.0) -> float:
    """Real luck % = actual shares / statistically expected shares at the pool difficulty.
    Expected shares = total_hashes * 65535 / (difficulty * 2^32) (scrypt pool-diff units).
    100% = exactly on expectation; >100% = lucky. Returns 100 with no shares yet."""
    if shares_accepted <= 0:
        return 100.0
    if difficulty and difficulty > 0 and total_hashes > 0:
        expected = total_hashes * 65535.0 / (difficulty * 4294967296.0)
        if expected > 0:
            return min(999.9, max(0.1, round(shares_accepted / expected * 100.0, 1)))
    # difficulty unknown: fall back to neutral
    return 100.0


def compute_streak(current_streak: int, shares_accepted: int) -> int:
    """Real streak of consecutive accepts from _handle_stratum responses."""
    return max(0, current_streak if shares_accepted > 0 else 0)


def compute_efficiency(shares_accepted: int, shares_rejected: int) -> float:
    """Real efficiency % = accepted / (accepted+rejected) from actual pool responses."""
    total = shares_accepted + shares_rejected
    if total <= 0:
        return 100.0
    return round((shares_accepted / total) * 100, 1)


def compute_effort_text(total_hashes: int, running: bool) -> str:
    """Real mining state text derived from whether hashing is occurring."""
    if not running:
        return "STOPPED"
    return "HASHING" if total_hashes > 0 else "SEARCHING"


def duty_percent(busy_delta: float, wall_delta: float) -> float:
    """GPU utilization as kernel duty cycle: fraction of wall time spent executing
    OpenCL batches. Real, vendor-neutral fallback where nvidia-smi doesn't exist."""
    if wall_delta <= 0 or busy_delta < 0:
        return 0.0
    return max(0.0, min(100.0, busy_delta / wall_delta * 100.0))


class QueuedStratumSocket:
    """Queue-backed socket stand-in for testing the composed recv_loop + worker submit + accept path.
    Used via DogeMiner.socket_factory. No unittest.mock. Thread-safe inbound queue.
    .sent records every sendall(data) as bytes (for polling 'mining.submit').
    Test code preloads full response lines (ending \n) via .push(bytes), and can push accept later.
    """
    def __init__(self):
        self._in_q = queue.Queue()
        self._sent = []
        self._timeout = None
        self._closed = False

    @property
    def sent(self):
        """List of all bytes passed to sendall (in order)."""
        return self._sent

    def settimeout(self, val):
        self._timeout = val

    def sendall(self, data):
        if isinstance(data, (str,)):
            data = data.encode("utf-8")
        self._sent.append(data)

    def recv(self, bufsize):
        try:
            if self._timeout is not None and self._timeout > 0:
                chunk = self._in_q.get(timeout=self._timeout)
            else:
                chunk = self._in_q.get()
            if chunk is None:
                return b""
            return chunk
        except queue.Empty:
            return b""
        except (OSError, ValueError, TypeError, EOFError):
            return b""

    def close(self):
        self._closed = True
        try:
            self._in_q.put_nowait(None)
        except (queue.Full, OSError, ValueError):
            pass

    def push(self, data):
        """Test-only: inject bytes (full \n-terminated stratum line) into inbound queue."""
        if isinstance(data, (str,)):
            data = data.encode("utf-8")
        self._in_q.put(data)


class DogeMiner:
    def __init__(self):
        self.running = False
        self.mode = "cpu"  # "cpu" or "gpu"
        self.wallet = ""
        self.total_hashes = 0
        self.shares_accepted = 0
        self.shares_rejected = 0
        self.shares_submitted = 0
        self.current_hashrate = 0.0
        # Real state for Scrypt Effort div datapoints (representative nonce + share streak)
        self.last_nonce = 0
        self.current_streak = 0
        self.last_share_hash = ""   # display-order hex of most recent computed scrypt hash
        self.best_share_diff = 0.0  # highest pool-difficulty any hash this session achieved
        self.threads = []
        self.lock = threading.Lock()
        self.start_time = None
        self.worker_count = 1
        self._stop_event = threading.Event()
        # Injectable for testing the composed Stratum path without real TCP.
        self.socket_factory = socket.socket

        # Monitoring
        self.cpu_percent = 0.0
        self.mem_percent = 0.0
        self.gpu_percent = 0.0
        self._monitor_thread = None
        self._rate_samples = []  # (t, total_hashes) for rolling 60s hashrate

        # Pool (Stratum) state - default pool miner.
        # Default is a no-registration pool: username is your DOGE wallet address.
        self.pool_id = "zpool"
        self.pool_host = "scrypt.mine.zpool.ca"
        self.pool_port = 3433
        self.pool_user = ""   # stratum username (wallet for no-reg pools, account.worker for registered)
        self.pool_pass = "c=DOGE"
        self.pool_error = ""  # last connect/protocol error for UI telemetry
        self.sock = None
        self.recv_thread = None
        self.current_job: Dict[str, Any] = None
        self.extranonce1 = ""
        self.extranonce2_size = 4
        self.difficulty = 1.0
        self.authorized = False
        self.connected = False
        self.job_lock = threading.Lock()
        self.send_lock = threading.Lock()
        self._connect_lock = threading.Lock()  # protect concurrent _connect_pool calls (start + watchdog)
        # authorization watchdog: warn when a pool silently ignores a bad username
        # (litecoinpool, for example, never answers a failed mining.authorize)
        self.auth_grace_seconds = 15.0
        self._connected_at = 0.0
        self._auth_warned = False

        # GPU backend state (real OpenCL when available; honest fallback otherwise)
        self.gpu = None                # ScryptGPU instance when active
        self.gpu_available = False
        self.gpu_backend = "none"      # "opencl" | "cpu-fallback" | "none"
        self.gpu_device_names = []
        # utilization source: nvidia-smi when present, else OpenCL kernel duty cycle
        self.gpu_util_source = "none"  # "nvidia-smi" | "opencl-duty" | "none"
        self._gpu_busy_prev = 0.0
        self._gpu_busy_prev_t = 0.0

        # Backend log buffer (captures prints/stdout messages) so they appear in UI live feed
        self._log_id = 0
        self._recent_logs: list = []
        self._last_progress_log = 0.0
        self._last_gpu_log = 0.0

        # Real wallet balance (fetched from blockchain, cached, refreshed in background —
        # never from inside get_stats' lock, which used to stall the stats endpoint)
        self.wallet_balance = 0.0
        self._last_balance_fetch = 0
        self._balance_cache_ttl = 60  # seconds
        self._balance_inflight = False
        self.balance_fetcher: Optional[Callable[[str], Optional[float]]] = None

        # Track connect/reconnect threads for clean join on stop/reconfig
        self._reconnect_thread = None
        self._pool_connect_thread = None

    def _log(self, message: str, verbose: bool = False):
        """Append to buffer (for /api/stats live feed) and print to stdout (docker/terminal).
        verbose entries are wire/telemetry chatter (stratum traffic, progress ticks, provider
        requests); the UI shows them only when its VERBOSE toggle is on."""
        self._log_id += 1
        entry = {"id": self._log_id, "msg": message, "v": verbose}
        self._recent_logs.append(entry)
        if len(self._recent_logs) > 200:
            self._recent_logs.pop(0)
        # stdout may be a legacy Windows codepage (cp1252) that can't encode e.g. the
        # arrow characters used in wire logs; logging must never raise into callers
        # (a UnicodeEncodeError here used to kill worker threads mid-submit).
        try:
            print(message)
        except UnicodeEncodeError:
            print(message.encode("ascii", "replace").decode("ascii"))
        except Exception:
            pass

    def _scrypt_work(self, nonce: int, job_data: str = "dogecoin") -> bytes:
        """Real Scrypt using stdlib (used for fallback/no-job case)"""
        data = (job_data + str(nonce)).encode('utf-8')
        return hashlib.scrypt(data, salt=b'doge', n=1024, r=1, p=1, maxmem=0, dklen=32)

    # --- Pure Stratum helpers (side-effect free, unit testable) ---
    def _format_subscribe(self) -> str:
        return json.dumps({"id": 1, "method": "mining.subscribe", "params": ["doge-miner-fullstack/2.0"]}) + "\n"

    def _format_authorize(self, user: str, password: str = "x") -> str:
        return json.dumps({"id": 2, "method": "mining.authorize", "params": [user, password]}) + "\n"

    def _format_submit(self, user: str, job_id: str, extranonce2: str, ntime: str, nonce: str) -> str:
        return json.dumps({
            "id": 4,
            "method": "mining.submit",
            "params": [user, job_id, extranonce2, ntime, nonce]
        }) + "\n"

    def _parse_difficulty(self, params: list) -> float:
        return float(params[0]) if params else 1.0

    def _parse_notify(self, params: list) -> Dict[str, Any]:
        if len(params) < 9:
            return {}
        return {
            "job_id": params[0],
            "prevhash": params[1],
            "coinb1": params[2],
            "coinb2": params[3],
            "merkle_branch": params[4],
            "version": params[5],
            "nbits": params[6],
            "ntime": params[7],
            "clean": params[8]
        }

    def _diff_to_target(self, diff: float) -> int:
        return diff_to_target_int(diff)

    def _build_coinbase(self, job: Dict, extranonce2: str) -> bytes:
        return build_coinbase(job.get("coinb1", ""), job.get("coinb2", ""), self.extranonce1, extranonce2)

    def _merkle_root(self, coinbase: bytes, branches: list) -> bytes:
        return merkle_root(coinbase, branches)

    def _build_header(self, job: Dict, merkle_root_b: bytes, ntime: str, nonce: int) -> bytes:
        return build_header(
            job.get("version", "00000001"),
            job.get("prevhash", ""),
            merkle_root_b,
            ntime,
            job.get("nbits", "1d00ffff"),
            nonce,
        )

    def _scrypt_header(self, header: bytes) -> bytes:
        # Real scrypt on the 80-byte header for pool jobs (real shares)
        return default_scrypt(header)

    def _get_gpu_util(self) -> float:
        """Cross-platform (Win/Mac/Linux) GPU util via nvidia-smi if present; stdlib only.
        Returns 0.0 on any failure, absence of tool, or non-NVIDIA hardware."""
        if shutil.which("nvidia-smi") is None:
            return 0.0
        try:
            out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            ).decode("utf-8", errors="ignore").strip()
            if out:
                val_str = out.split("\n")[0].strip()
                val = float(val_str)
                return max(0.0, min(100.0, val))
        except Exception:
            pass
        return 0.0

    def _maybe_refresh_balance(self):
        """Kick off a background balance refresh if the cache is stale.
        Never blocks the caller: get_stats reads the cached value only."""
        if not self.wallet:
            return
        now = time.time()
        if now - self._last_balance_fetch < self._balance_cache_ttl or self._balance_inflight:
            return
        self._balance_inflight = True

        def _bg():
            try:
                fetcher = self.balance_fetcher
                if fetcher is None:
                    from . import chain
                    fetcher = chain.fetch_address_balance
                bal = fetcher(self.wallet)
                if bal is not None:
                    self.wallet_balance = float(bal)
                    self._log(f"wallet balance refreshed from chain: {self.wallet_balance} DOGE", verbose=True)
                self._last_balance_fetch = time.time()
            except Exception:
                self._last_balance_fetch = time.time()  # don't hammer on persistent failure
            finally:
                self._balance_inflight = False

        threading.Thread(target=_bg, daemon=True).start()

    def _monitor_loads(self):
        """Dedicated low-rate monitor thread: samples CPU/GPU/mem accurately and
        maintains the rolling hashrate window + background balance refresh."""
        while not self._stop_event.is_set():
            if not self.running:
                time.sleep(0.2)
                continue
            try:
                if psutil is not None:
                    cp = psutil.cpu_percent(interval=0.5)
                    mp = psutil.virtual_memory().percent
                    gp = self._get_gpu_util()
                    src = "nvidia-smi" if gp > 0 else "none"
                    # vendor-neutral fallback: kernel duty cycle from the OpenCL backend
                    # (AMD/Intel have no nvidia-smi; this is still a real measurement)
                    gpu_obj = self.gpu
                    if gpu_obj is not None:
                        now_b = time.time()
                        busy = gpu_obj.busy_seconds
                        if self._gpu_busy_prev_t > 0 and gp <= 0:
                            gp = round(duty_percent(busy - self._gpu_busy_prev,
                                                    now_b - self._gpu_busy_prev_t), 1)
                            src = "opencl-duty"
                        self._gpu_busy_prev = busy
                        self._gpu_busy_prev_t = now_b
                    with self.lock:
                        self.cpu_percent = cp
                        self.mem_percent = mp
                        self.gpu_percent = gp
                        self.gpu_util_source = src
                else:
                    time.sleep(0.5)
                now = time.time()
                with self.lock:
                    self._rate_samples.append((now, self.total_hashes))
                    while self._rate_samples and now - self._rate_samples[0][0] > 65:
                        self._rate_samples.pop(0)
                # periodic heartbeat: proves hashing is really happening (always visible —
                # this IS the miner's work, not chatter)
                if now - self._last_progress_log >= 5.0 and self.total_hashes > 0 and self.running:
                    self._last_progress_log = now
                    rate = 0.0
                    if len(self._rate_samples) >= 2:
                        t0s, h0 = self._rate_samples[0]
                        t1s, h1 = self._rate_samples[-1]
                        if t1s > t0s:
                            rate = (h1 - h0) / (t1s - t0s)
                    self._log(
                        f"working: {self.total_hashes:,} hashes @ {rate:,.0f} H/s | "
                        f"nonce 0x{self.last_nonce:08x} | pool diff {self.difficulty:g} | "
                        f"best share {self.best_share_diff:.4f}")
                self._maybe_refresh_balance()
            except (AttributeError, OSError, TypeError, ValueError):
                pass
            time.sleep(0.1)

    def _next_extranonce2(self, worker_id: int, counter: int) -> str:
        en2_size = getattr(self, 'extranonce2_size', 4) or 4
        val = (worker_id + counter * 1024) % (1 << (8 * en2_size))
        return f"{val:0{en2_size * 2}x}"

    def _worker(self, worker_id: int):
        """CPU worker thread: uses current pool job if available, submits real shares.
        Header prefix (coinbase/merkle) is cached per job+extranonce so each hash only
        packs the nonce and runs scrypt."""
        nonce = random.randint(0, 2**32 - 1)
        local_hashes = 0
        flush_time = time.time()
        en2_counter = 0
        cached_key = None
        prefix = None
        target = MAX_TARGET
        extranonce2 = self._next_extranonce2(worker_id, en2_counter)
        best_local = 0.0
        last_hash_hex = ""
        # per-worker sweep window for verbose "actual work" lines
        window_hashes = 0
        window_best = 0.0
        window_start_nonce = nonce

        while not self._stop_event.is_set():
            if not self.running:
                time.sleep(0.1)
                continue

            job = None
            with self.job_lock:
                if self.current_job:
                    job = self.current_job
                difficulty = self.difficulty
                extranonce1 = self.extranonce1

            if job:
                key = (id(job), extranonce1, extranonce2, difficulty)
                if key != cached_key:
                    try:
                        ntime = job.get("ntime") or hex(int(time.time()))[2:].zfill(8)
                        prefix = build_header_prefix(job, extranonce1, extranonce2, ntime)
                        target = diff_to_target_int(difficulty)
                        cached_key = key
                    except (binascii.Error, struct.error, ValueError, TypeError):
                        prefix = None
                        cached_key = key
                if prefix is not None:
                    header = prefix + struct.pack("<I", nonce)
                    hash_result = default_scrypt(header)
                    local_hashes += 1
                    hash_int = int.from_bytes(hash_result, "little")
                    last_hash_hex = hash_result[::-1].hex()
                    sdiff = DIFF1_TARGET / hash_int if hash_int else float("inf")
                    if sdiff > best_local:
                        best_local = sdiff
                    window_hashes += 1
                    if sdiff > window_best:
                        window_best = sdiff
                    if window_hashes >= 1000:
                        self._log(
                            f"worker {worker_id:02d}: swept 0x{window_start_nonce:08x}→0x{nonce:08x} "
                            f"(+{window_hashes:,} scrypt hashes), window best diff {window_best:.4f}",
                            verbose=True)
                        window_hashes = 0
                        window_best = 0.0
                        window_start_nonce = nonce
                    if hash_meets_target(hash_int, target):
                        ntime = job.get("ntime") or hex(int(time.time()))[2:].zfill(8)
                        submit = self._format_submit(
                            self.pool_user or self.wallet, job.get("job_id", ""),
                            extranonce2, ntime, f"{nonce:08x}"
                        )
                        with self.send_lock:
                            if self.sock:
                                try:
                                    self.sock.sendall(submit.encode())
                                    with self.lock:
                                        self.shares_submitted += 1
                                    self._log(f"→ mining.submit job={job.get('job_id','')} nonce={nonce:08x} share_diff={sdiff:.3f}", verbose=True)
                                except (OSError, socket.error, AttributeError):
                                    self.connected = False  # trigger watchdog reconnect
                        # accepted/rejected ONLY from pool _handle_stratum responses
                else:
                    time.sleep(0.05)
            else:
                # fallback for no job (pool not connected yet): still do real scrypt work
                self._scrypt_work(nonce)
                local_hashes += 1

            nonce = (nonce + 1) & 0xffffffff
            if nonce == 0:
                en2_counter += 1
                extranonce2 = self._next_extranonce2(worker_id, en2_counter)
                cached_key = None

            # accumulate hashes + telemetry
            if local_hashes >= 8 or (time.time() - flush_time > 0.25):
                with self.lock:
                    self.total_hashes += local_hashes
                    self.last_nonce = nonce
                    if last_hash_hex:
                        self.last_share_hash = last_hash_hex
                    if best_local > self.best_share_diff:
                        self.best_share_diff = best_local
                local_hashes = 0
                flush_time = time.time()

            # modest throttle so the uvicorn main thread + API stay responsive
            if self.mode == "cpu":
                time.sleep(0.002)
            else:
                time.sleep(0.0005)

    def _gpu_worker(self):
        """GPU driver thread: scans large nonce ranges per batch on the OpenCL device.
        Only started when the GPU backend initialized AND passed its CPU-verified self-test."""
        nonce = random.randint(0, 2**32 - 1)
        en2_counter = 0
        cached_key = None
        prefix = None
        target = MAX_TARGET
        extranonce2 = self._next_extranonce2(0, en2_counter)

        while not self._stop_event.is_set() and self.running:
            job = None
            with self.job_lock:
                if self.current_job:
                    job = self.current_job
                difficulty = self.difficulty
                extranonce1 = self.extranonce1

            if not job or self.gpu is None:
                time.sleep(0.2)
                continue

            key = (id(job), extranonce1, extranonce2, difficulty)
            if key != cached_key:
                try:
                    ntime = job.get("ntime") or hex(int(time.time()))[2:].zfill(8)
                    prefix = build_header_prefix(job, extranonce1, extranonce2, ntime)
                    target = diff_to_target_int(difficulty)
                    cached_key = key
                except (binascii.Error, struct.error, ValueError, TypeError):
                    prefix = None
                    cached_key = key
            if prefix is None:
                time.sleep(0.2)
                continue

            try:
                # candidates come back CPU-verified (hashlib.scrypt) — see gpu.ScryptGPU.scan
                count, candidates, samples = self.gpu.scan(prefix, nonce, target)
            except Exception as e:
                self._log(f"GPU scan error, falling back to CPU: {e}")
                self.gpu = None
                self.gpu_backend = "cpu-fallback"
                # spin up CPU threads to replace the GPU driver
                for i in range(self.worker_count):
                    t = threading.Thread(target=self._worker, args=(i,), daemon=True)
                    t.start()
                    self.threads.append(t)
                return

            now = time.time()
            if now - self._last_gpu_log >= 1.0:
                self._last_gpu_log = now
                secs = max(self.gpu.last_scan_seconds, 1e-6)
                self._log(
                    f"GPU batch: {count} nonces in {self.gpu.last_scan_seconds*1000:.0f} ms "
                    f"(~{count/secs/1000:.0f} KH/s on-device), {len(samples)} sample(s), "
                    f"{len(candidates)} share candidate(s)",
                    verbose=True)

            best_local = 0.0
            last_hex = ""
            for _, hash_result in samples:
                hash_int = int.from_bytes(hash_result, "little")
                if hash_int == 0:
                    continue
                sdiff = DIFF1_TARGET / hash_int
                if sdiff > best_local:
                    best_local = sdiff
                    last_hex = hash_result[::-1].hex()
            for n, hash_result in candidates:
                ntime = job.get("ntime") or hex(int(time.time()))[2:].zfill(8)
                submit = self._format_submit(
                    self.pool_user or self.wallet, job.get("job_id", ""),
                    extranonce2, ntime, f"{n:08x}"
                )
                with self.send_lock:
                    if self.sock:
                        try:
                            self.sock.sendall(submit.encode())
                            with self.lock:
                                self.shares_submitted += 1
                            self._log(
                                f"→ mining.submit job={job.get('job_id','')} nonce={n:08x} "
                                f"share_diff={share_difficulty(hash_result):.3f} (CPU-verified)",
                                verbose=True)
                        except (OSError, socket.error, AttributeError):
                            self.connected = False

            with self.lock:
                self.total_hashes += count
                self.last_nonce = (nonce + count) & 0xffffffff
                if last_hex:
                    self.last_share_hash = last_hex
                if best_local > self.best_share_diff:
                    self.best_share_diff = best_local

            new_nonce = nonce + count
            if new_nonce > 0xffffffff:
                en2_counter += 1
                extranonce2 = self._next_extranonce2(0, en2_counter)
                cached_key = None
            nonce = new_nonce & 0xffffffff

    def start(self, wallet: str, mode: str = "cpu", workers: int = None,
              pool_user: str = None, pool_pass: str = None):
        if self.running:
            self.stop()

        self.wallet = wallet
        self.mode = mode
        self.pool_user = (pool_user or "").strip() or wallet
        if pool_pass is not None and str(pool_pass).strip():
            self.pool_pass = str(pool_pass).strip()
        self.running = True
        self._stop_event.clear()
        self.total_hashes = 0
        self.shares_accepted = 0
        self.shares_rejected = 0
        self.shares_submitted = 0
        self.current_hashrate = 0.0
        self.last_nonce = 0
        self.current_streak = 0
        self.last_share_hash = ""
        self.best_share_diff = 0.0
        self.pool_error = ""
        self._rate_samples = []
        self.start_time = time.time()

        if workers is not None and workers > 0:
            self.worker_count = workers
        else:
            # Real default: use the machine's cores (leave one for the API/UI thread)
            self.worker_count = max(1, (os.cpu_count() or 2) - 1)

        # GPU mode: try the real OpenCL backend first (self-tested against hashlib.scrypt).
        self.gpu = None
        self.gpu_available = False
        self.gpu_backend = "none"
        self.gpu_device_names = []
        self.gpu_util_source = "none"
        self._gpu_busy_prev = 0.0
        self._gpu_busy_prev_t = 0.0
        self.threads = []
        gpu_started = False
        if mode == "gpu":
            try:
                from .gpu import ScryptGPU
                gpu = ScryptGPU()
                names = gpu.init()
                if names and gpu.self_test():
                    self.gpu = gpu
                    self.gpu_available = True
                    self.gpu_backend = "opencl"
                    self.gpu_device_names = names
                    self.worker_count = 1  # one GPU driver thread (batch parallelism is on-device)
                    t = threading.Thread(target=self._gpu_worker, daemon=True)
                    t.start()
                    self.threads.append(t)
                    gpu_started = True
                    self._log(f"GPU mining on OpenCL device(s): {', '.join(names)} (kernel self-test passed)")
                else:
                    self._log("GPU backend unavailable or failed self-test; using CPU threads (labeled honestly)")
            except Exception as e:
                self._log(f"GPU backend not available ({e.__class__.__name__}: {e}); using CPU threads")
            if not gpu_started:
                self.gpu_backend = "cpu-fallback"

        if not gpu_started:
            for i in range(self.worker_count):
                t = threading.Thread(target=self._worker, args=(i,), daemon=True)
                t.start()
                self.threads.append(t)

        # Start dedicated monitor thread for accurate cross-platform CPU/GPU loads
        self._monitor_thread = threading.Thread(target=self._monitor_loads, daemon=True)
        self._monitor_thread.start()

        # Initial sample so first stats poll has real values
        if psutil is not None:
            try:
                cp = psutil.cpu_percent(interval=0.1)
                mp = psutil.virtual_memory().percent
                gp = self._get_gpu_util()
                with self.lock:
                    self.cpu_percent = cp
                    self.mem_percent = mp
                    self.gpu_percent = gp
            except (AttributeError, OSError, TypeError):
                pass

        self._log(f"Started {mode.upper()} mining with {self.worker_count} workers for wallet {wallet} "
                  f"(pool {self.pool_host}:{self.pool_port} as {self.pool_user})")

        # Pool connection in background threads (never block the API handler).
        self._pool_connect_thread = threading.Thread(target=self._connect_pool, daemon=True)
        self._pool_connect_thread.start()
        self._reconnect_thread = threading.Thread(target=self._reconnect_loop, daemon=True)
        self._reconnect_thread.start()

    def stop(self):
        self.running = False
        self._stop_event.set()
        for t in self.threads:
            if t.is_alive():
                t.join(timeout=1)
        self.threads = []
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2.0)
        self._monitor_thread = None
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            self._reconnect_thread.join(timeout=2.0)
        self._reconnect_thread = None
        if self._pool_connect_thread and self._pool_connect_thread.is_alive():
            self._pool_connect_thread.join(timeout=2.0)
        self._pool_connect_thread = None
        if self.gpu is not None:
            try:
                self.gpu.release()
            except Exception:
                pass
            self.gpu = None
        self.current_hashrate = 0.0
        self.start_time = None
        self.last_nonce = 0
        self.current_streak = 0
        self._rate_samples = []
        # Reset load values so get_stats post-stop reflects current system (not last mining sample)
        self.cpu_percent = 0.0
        self.mem_percent = 0.0
        self.gpu_percent = 0.0
        self._disconnect_pool()
        self._log("Miner stopped")

    # --- Real Stratum pool methods (default, no sim) ---
    def _connect_pool(self):
        if self.connected:
            return
        with self._connect_lock:
            if self.connected:
                return
            for attempt in range(5):
                try:
                    factory = self.socket_factory if callable(self.socket_factory) else socket.socket
                    sock = factory(socket.AF_INET, socket.SOCK_STREAM)
                    self.sock = sock
                    is_queued = isinstance(self.sock, QueuedStratumSocket)
                    if not is_queued:
                        self.sock.settimeout(30)
                        self.sock.connect((self.pool_host, self.pool_port))
                    else:
                        self.sock.settimeout(30)
                    self.connected = True
                    self.authorized = False
                    self.current_job = None
                    self.pool_error = ""
                    self._connected_at = time.time()
                    self._auth_warned = False

                    # subscribe
                    sub = self._format_subscribe()
                    with self.send_lock:
                        self.sock.sendall(sub.encode())
                    self._log(f"→ mining.subscribe {self.pool_host}:{self.pool_port}", verbose=True)
                    # authorize (wallet for no-reg pools, account.worker for registered)
                    auth = self._format_authorize(self.pool_user or self.wallet, self.pool_pass)
                    with self.send_lock:
                        self.sock.sendall(auth.encode())
                    self._log(f"→ mining.authorize {self.pool_user or self.wallet} (pass: {self.pool_pass})", verbose=True)

                    # start recv loop (real one, even for queued)
                    self.recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
                    self.recv_thread.start()

                    self._log(f"Connected to pool {self.pool_host}:{self.pool_port} as {self.pool_user or self.wallet}")
                    return
                except (OSError, socket.error, socket.timeout, ConnectionError, ConnectionRefusedError) as e:
                    self.connected = False
                    self.pool_error = f"{e.__class__.__name__}: {e}"
                    self._log(f"Pool connect attempt {attempt+1} failed: {e}")
                    if self.sock:
                        try:
                            self.sock.close()
                        except (OSError, AttributeError):
                            pass
                    self.sock = None
                    if not self.running or self._stop_event.is_set():
                        return
                    time.sleep(1)
            if not self.connected and self.running:
                self._log("Pool connect failed after retries; background watchdog keeps retrying")

    def _disconnect_pool(self):
        self.connected = False
        self.authorized = False
        if self.sock:
            try:
                self.sock.close()
            except (OSError, AttributeError, socket.error):
                pass
            self.sock = None

    def _recv_loop(self):
        buffer = b""
        while self.connected and self.running and self.sock:
            try:
                data = self.sock.recv(4096)
                if not data:
                    break
                buffer += data
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode())
                        self._handle_stratum(msg)
                    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError):
                        pass  # ignore bad stratum lines, do not swallow share path
            except socket.timeout:
                continue
            except (OSError, socket.error, ConnectionError):
                break
        self.connected = False
        self.authorized = False
        # let the persistent reconnect watchdog recover; do not nest connect calls here

    def _reconnect_loop(self):
        """Persistent watchdog: while miner running, periodically ensure we are connected,
        and flag pools that silently never confirm authorization (bad username/worker)."""
        while self.running and not self._stop_event.is_set():
            if not self.connected:
                self._connect_pool()
            if (self.connected and not self.authorized and not self._auth_warned
                    and self._connected_at
                    and time.time() - self._connected_at > self.auth_grace_seconds):
                self._auth_warned = True
                self.pool_error = ("pool has not confirmed authorization — "
                                   "check pool username/worker and password")
                self._log("WARNING: pool has not confirmed authorization — shares may be "
                          "discarded. Check your pool username/worker (registered pools need "
                          "an account).")
            for _ in range(4):
                if not self.running or self._stop_event.is_set():
                    return
                time.sleep(1)

    def _handle_stratum(self, msg: dict):
        if "method" in msg:
            method = msg["method"]
            params = msg.get("params", [])
            if method == "mining.set_difficulty":
                with self.job_lock:
                    self.difficulty = self._parse_difficulty(params)
                self._log(f"← mining.set_difficulty {self.difficulty:g}", verbose=True)
            elif method == "mining.notify":
                job = self._parse_notify(params)
                with self.job_lock:
                    if job:
                        self.current_job = job
                if job:
                    clean = " (clean — restart nonce search)" if job.get("clean") else ""
                    self._log(f"← mining.notify job={job.get('job_id')} nbits={job.get('nbits')}{clean}", verbose=True)
            elif method == "mining.set_extranonce":
                # some multipools (zpool/zergpool family) rotate extranonce mid-session
                try:
                    with self.job_lock:
                        if params:
                            self.extranonce1 = params[0] if isinstance(params[0], str) else self.extranonce1
                            if len(params) > 1:
                                self.extranonce2_size = int(params[1])
                except (TypeError, ValueError, IndexError):
                    pass
                self._log(f"← mining.set_extranonce {self.extranonce1}/{self.extranonce2_size}", verbose=True)
            elif method == "client.get_version":
                # some pools probe clients and may drop those that ignore the request
                if msg.get("id") is not None:
                    reply = json.dumps({"id": msg["id"], "result": "doge-miner-fullstack/2.0",
                                        "error": None}) + "\n"
                    with self.send_lock:
                        if self.sock:
                            try:
                                self.sock.sendall(reply.encode())
                            except (OSError, socket.error, AttributeError):
                                pass
                self._log("← client.get_version (answered)", verbose=True)
            elif method == "client.show_message":
                if params:
                    self._log(f"pool message: {params[0]}")
            elif method == "client.reconnect":
                self.connected = False  # watchdog reconnects
        elif "result" in msg or "error" in msg:
            # responses to subscribe/authorize/submit
            if msg.get("id") == 2:
                if msg.get("result") is True:
                    self.authorized = True
                    self.pool_error = ""
                    self._log("Pool authorized")
                elif msg.get("result") is False or msg.get("error"):
                    self.pool_error = f"authorize rejected: {msg.get('error') or 'bad username/worker'}"
                    self._log(f"Pool authorize rejected: {msg.get('error') or 'bad username/worker'}")
            if msg.get("id") == 4:  # submit response
                if msg.get("result") is True:
                    with self.lock:
                        self.shares_accepted += 1
                        self.current_streak += 1
                    self._log("Share accepted by pool")
                else:
                    with self.lock:
                        self.shares_rejected += 1
                        self.current_streak = 0
                    self._log(f"Share rejected: {msg.get('error')}")
            if msg.get("id") == 1 and msg.get("result"):
                # subscribe result often [ [..], extranonce1, size ]
                try:
                    res = msg["result"]
                    if len(res) > 1:
                        self.extranonce1 = res[1] if isinstance(res[1], str) else ""
                        if len(res) > 2:
                            self.extranonce2_size = int(res[2])
                    self._log(f"← subscribed: extranonce1={self.extranonce1} en2size={self.extranonce2_size}", verbose=True)
                except (TypeError, ValueError, IndexError, KeyError):
                    pass

    def get_stats(self):
        with self.lock:
            now = time.time()
            elapsed = (now - self.start_time) if self.running and self.start_time else 0
            uptime = int(elapsed)
            # Rolling ~60s hashrate from monitor samples; falls back to session average early on
            hashrate = 0.0
            if self.running and self.start_time and elapsed > 0.01 and self.total_hashes > 0:
                if len(self._rate_samples) >= 2:
                    t0, h0 = self._rate_samples[0]
                    t1, h1 = self._rate_samples[-1]
                    if t1 > t0:
                        hashrate = ((h1 - h0) / (t1 - t0)) / 1000.0
                if hashrate <= 0:
                    hashrate = (self.total_hashes / elapsed) / 1000.0
                if self.current_hashrate > 0:
                    hashrate = self.current_hashrate * 0.35 + hashrate * 0.65
            self.current_hashrate = hashrate

            cpu_p = self.cpu_percent
            mem_p = self.mem_percent
            gpu_p = self.gpu_percent
            if not self.running:
                if psutil is not None:
                    try:
                        cpu_p = psutil.cpu_percent(interval=0.1)
                        mem_p = psutil.virtual_memory().percent
                        gpu_p = self._get_gpu_util()
                    except (AttributeError, OSError, TypeError, ValueError):
                        pass

            self.cpu_percent = round(cpu_p, 1)
            self.mem_percent = round(mem_p, 1)
            self.gpu_percent = round(gpu_p, 1)

            effort_percent = compute_effort_percent(self.total_hashes, uptime)
            current_nonce = compute_current_nonce(self.last_nonce)
            luck = compute_luck(self.shares_accepted, self.total_hashes, self.difficulty)
            streak = compute_streak(self.current_streak, self.shares_accepted)
            efficiency = compute_efficiency(self.shares_accepted, self.shares_rejected)
            effort_text = compute_effort_text(self.total_hashes, self.running)

            desc = f"{self.mode.upper()} ({self.worker_count} workers)"
            if self.mode == "gpu":
                if self.gpu_backend == "opencl" and self.gpu_device_names:
                    desc = f"GPU OpenCL: {self.gpu_device_names[0]}"
                elif self.gpu_backend == "cpu-fallback":
                    desc = f"GPU mode (no usable GPU — CPU fallback, {self.worker_count} threads)"

            stats = {
                "running": self.running,
                "mode": self.mode,
                "worker_count": self.worker_count,
                "wallet": self.wallet,
                "hashrate_khs": round(self.current_hashrate, 3),
                "total_hashes": self.total_hashes,
                # shares_* come exclusively from real pool submit responses (see _handle_stratum)
                "shares_accepted": self.shares_accepted,
                "shares_rejected": self.shares_rejected,
                "shares_submitted": self.shares_submitted,
                "uptime_seconds": uptime,
                "description": desc,
                "wallet_balance": self.wallet_balance,
                "cpu_percent": self.cpu_percent,
                "mem_percent": self.mem_percent,
                "gpu_percent": self.gpu_percent,
                "pool_connected": self.connected,
                "pool_authorized": self.authorized,
                "pool_id": self.pool_id,
                "pool_host": self.pool_host,
                "pool_port": self.pool_port,
                "pool_user": self.pool_user,
                "pool_difficulty": self.difficulty,
                "pool_target": target_to_hex(diff_to_target_int(self.difficulty)),
                "pool_error": self.pool_error,
                "has_job": self.current_job is not None,
                "last_share_hash": self.last_share_hash,
                "best_share_diff": round(self.best_share_diff, 6),
                "gpu_available": self.gpu_available,
                "gpu_backend": self.gpu_backend,
                "gpu_devices": list(self.gpu_device_names),
                "gpu_util_source": self.gpu_util_source,
                # Real datapoints for SCRYPT EFFORT div (no illustrative values)
                "effort_percent": effort_percent,
                "current_nonce": current_nonce,
                "luck": luck,
                "streak": streak,
                "efficiency": efficiency,
                "effort_text": effort_text,
                # Recent stdout messages from backend (captured via _log) for the UI live feed
                "backend_logs": list(self._recent_logs)
            }
        # Balance refresh outside the lock: non-blocking background kick
        self._maybe_refresh_balance()
        return stats

# Global instance (shipped; used by main.py and tests)
miner = DogeMiner()
