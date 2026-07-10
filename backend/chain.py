"""Key-free Dogecoin blockchain telemetry with backend caching + multi-provider failover.

No API tokens or keys anywhere. Users never hit provider rate limits directly:
every response is cached server-side (TTL per data type) and each request only
reaches a public provider when the cache is stale. On provider failure the next
provider is tried; if all fail, the last known good value is served (marked stale).

Providers (all public, keyless):
  * dogecoin.atomicwallet.io (Blockbook) - chain status, address + tx lookups (primary)
  * blockchair.com  - rich stats, blocks, address + tx dashboards
  * blockcypher.com - chain tip, address balances, transactions
  * coingecko.com   - DOGE price (USD/BTC + 24h change)
"""

import json
import threading
import time
import urllib.request
import urllib.error
from typing import Any, Callable, Dict, List, Optional

USER_AGENT = "doge-miner-fullstack/2.0 (educational; github-style local app)"

# Optional one-line telemetry sink for provider requests (wired to the miner's live
# feed as verbose entries by main.py). Never allowed to break a fetch.
logger: Optional[Callable[[str], None]] = None


def set_logger(fn: Optional[Callable[[str], None]]):
    global logger
    logger = fn


def _tell(msg: str):
    if logger is not None:
        try:
            logger(msg)
        except Exception:
            pass


def _short_url(url: str) -> str:
    return url.split("//", 1)[-1][:70]


# Injectable for tests (replace with a stub to avoid network)
def _default_http_get_json(url: str, timeout: float = 8.0) -> Any:
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        _tell(f"chain: GET {_short_url(url)} ok ({(time.time()-t0)*1000:.0f} ms)")
        return data
    except Exception as e:
        _tell(f"chain: GET {_short_url(url)} failed ({e.__class__.__name__}) — trying next provider")
        raise


http_get_json: Callable[..., Any] = _default_http_get_json

_cache: Dict[str, Any] = {}          # key -> (expires_at, value)
_stale: Dict[str, Any] = {}          # key -> last good value (no expiry)
_cache_lock = threading.Lock()
_MAX_KEYS = 500


def _cached(key: str, ttl: float, producer: Callable[[], Any]) -> Any:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    try:
        value = producer()
    except Exception:
        value = None
    if value is not None:
        with _cache_lock:
            if len(_cache) > _MAX_KEYS:
                _cache.clear()
            if len(_stale) > _MAX_KEYS:
                _stale.clear()
            _cache[key] = (now + ttl, value)
            _stale[key] = value
        return value
    with _cache_lock:
        old = _stale.get(key)
    if old is not None:
        if isinstance(old, dict):
            old = dict(old)
            old["stale"] = True
        return old
    return None


def _first(*producers: Callable[[], Any]) -> Any:
    """Try providers in order; first non-None wins."""
    for p in producers:
        try:
            v = p()
            if v is not None:
                return v
        except Exception:
            continue
    return None


# ---------------- price ----------------

def _price_coingecko() -> Optional[Dict[str, Any]]:
    d = http_get_json(
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=dogecoin&vs_currencies=usd,btc&include_24hr_change=true")
    doge = d.get("dogecoin") if isinstance(d, dict) else None
    if not doge or "usd" not in doge:
        return None
    return {
        "usd": float(doge["usd"]),
        "btc": float(doge.get("btc") or 0),
        "usd_24h_change": round(float(doge.get("usd_24h_change") or 0), 3),
        "source": "coingecko",
    }


def _price_blockchair() -> Optional[Dict[str, Any]]:
    d = http_get_json("https://api.blockchair.com/dogecoin/stats")
    data = (d or {}).get("data") or {}
    usd = data.get("market_price_usd")
    if usd is None:
        return None
    return {
        "usd": float(usd),
        "btc": float(data.get("market_price_btc") or 0),
        "usd_24h_change": round(float(data.get("market_price_usd_change_24h_percentage") or 0), 3),
        "source": "blockchair",
    }


def get_price() -> Optional[Dict[str, Any]]:
    return _cached("price", 60, lambda: _first(_price_coingecko, _price_blockchair))


# ---------------- chain summary ----------------

BLOCKBOOK = "https://dogecoin.atomicwallet.io/api/v2"


def _summary_blockbook() -> Optional[Dict[str, Any]]:
    d = http_get_json(BLOCKBOOK)
    bb = (d or {}).get("blockbook") or {}
    be = (d or {}).get("backend") or {}
    if not bb.get("bestHeight"):
        return None
    difficulty = float(be.get("difficulty") or 0)
    return {
        "height": int(bb["bestHeight"]),
        "best_block_hash": be.get("bestBlockHash", ""),
        "difficulty": difficulty,
        "network_hashrate_hs": difficulty * 4294967296 / 60 if difficulty else 0,
        "blocks_24h": 0,
        "transactions_24h": 0,
        "mempool_transactions": int(bb.get("mempoolSize") or 0),
        "circulation_doge": 0,
        "node_version": be.get("subversion", ""),
        "source": "blockbook",
    }


def _summary_blockchair() -> Optional[Dict[str, Any]]:
    d = http_get_json("https://api.blockchair.com/dogecoin/stats")
    data = (d or {}).get("data") or {}
    if not data.get("best_block_height"):
        return None
    difficulty = float(data.get("difficulty") or 0)
    return {
        "height": int(data["best_block_height"]),
        "best_block_hash": data.get("best_block_hash", ""),
        "difficulty": difficulty,
        # Dogecoin targets 1-minute blocks: hashrate ~= difficulty * 2^32 / 60
        "network_hashrate_hs": difficulty * 4294967296 / 60 if difficulty else 0,
        "blocks_24h": int(data.get("blocks_24h") or 0),
        "transactions_24h": int(data.get("transactions_24h") or 0),
        "mempool_transactions": int(data.get("mempool_transactions") or 0),
        "circulation_doge": round(float(data.get("circulation") or 0) / 1e8, 0),
        "source": "blockchair",
    }


def _summary_blockcypher() -> Optional[Dict[str, Any]]:
    d = http_get_json("https://api.blockcypher.com/v1/doge/main")
    if not d or "height" not in d:
        return None
    # BlockCypher exposes no difficulty for doge chain tip; leave 0 (UI shows n/a)
    return {
        "height": int(d["height"]),
        "best_block_hash": d.get("hash", ""),
        "difficulty": 0,
        "network_hashrate_hs": 0,
        "blocks_24h": 0,
        "transactions_24h": 0,
        "mempool_transactions": int(d.get("unconfirmed_count") or 0),
        "circulation_doge": 0,
        "source": "blockcypher",
    }


def get_summary() -> Optional[Dict[str, Any]]:
    def produce():
        s = _first(_summary_blockbook, _summary_blockchair, _summary_blockcypher)
        if s is None:
            return None
        # enrich blockbook/blockcypher with blockchair's 24h counters when cheap (cached)
        if s["source"] != "blockchair":
            extra = _cached("summary:blockchair", 300,
                            lambda: _first(_summary_blockchair))
            if isinstance(extra, dict):
                for k in ("blocks_24h", "transactions_24h", "circulation_doge"):
                    if not s.get(k):
                        s[k] = extra.get(k, 0)
        price = get_price() or {}
        s = dict(s)
        s["price_usd"] = price.get("usd", 0)
        s["price_btc"] = price.get("btc", 0)
        s["price_usd_24h_change"] = price.get("usd_24h_change", 0)
        s["updated"] = int(time.time())
        return s
    return _cached("summary", 60, produce)


# ---------------- recent blocks ----------------

def _blocks_blockchair(limit: int) -> Optional[List[Dict[str, Any]]]:
    d = http_get_json(f"https://api.blockchair.com/dogecoin/blocks?limit={limit}&s=id(desc)")
    rows = (d or {}).get("data")
    if not isinstance(rows, list) or not rows:
        return None
    out = []
    for r in rows:
        out.append({
            "height": int(r.get("id") or 0),
            "hash": r.get("hash", ""),
            "time": r.get("time", ""),
            "tx_count": int(r.get("transaction_count") or 0),
            "size": int(r.get("size") or 0),
            "difficulty": float(r.get("difficulty") or 0),
        })
    return out


def _blocks_blockcypher(limit: int) -> Optional[List[Dict[str, Any]]]:
    # blockcypher has no cheap list endpoint; return just the tip as a 1-item fallback
    d = http_get_json("https://api.blockcypher.com/v1/doge/main")
    if not d or "height" not in d:
        return None
    return [{
        "height": int(d["height"]),
        "hash": d.get("hash", ""),
        "time": d.get("time", ""),
        "tx_count": 0,
        "size": 0,
        "difficulty": 0,
    }]


def _blocks_blockbook(limit: int) -> Optional[List[Dict[str, Any]]]:
    """Walk the last `limit` heights on the public blockbook (fallback when
    blockchair's list endpoint is throttled). One small request per block."""
    d = http_get_json(BLOCKBOOK)
    tip = int(((d or {}).get("blockbook") or {}).get("bestHeight") or 0)
    if not tip:
        return None
    out = []
    for h in range(tip, max(tip - limit, 0), -1):
        try:
            b = http_get_json(f"{BLOCKBOOK}/block/{h}?pageSize=1")
        except Exception:
            break
        if not b or "hash" not in b:
            break
        out.append({
            "height": int(b.get("height") or h),
            "hash": b.get("hash", ""),
            "time": b.get("time", ""),
            "tx_count": int(b.get("txCount") or 0),
            "size": int(b.get("size") or 0),
            "difficulty": float(b.get("difficulty") or 0),
        })
    return out or None


def get_blocks(limit: int = 10) -> Optional[List[Dict[str, Any]]]:
    limit = max(1, min(int(limit or 10), 30))
    return _cached(f"blocks:{limit}", 120,
                   lambda: _first(lambda: _blocks_blockchair(limit),
                                  lambda: _blocks_blockbook(min(limit, 10)),
                                  lambda: _blocks_blockcypher(limit)))


# ---------------- address lookup ----------------

def _address_blockbook(addr: str) -> Optional[Dict[str, Any]]:
    d = http_get_json(f"{BLOCKBOOK}/address/{addr}?details=txids&pageSize=10")
    if not d or d.get("error") or "address" not in d:
        return None
    return {
        "address": addr,
        "balance_doge": round(float(d.get("balance") or 0) / 1e8, 8),
        "received_doge": round(float(d.get("totalReceived") or 0) / 1e8, 8),
        "spent_doge": round(float(d.get("totalSent") or 0) / 1e8, 8),
        "tx_count": int(d.get("txs") or 0),
        "first_seen": "",
        "last_seen": "",
        "recent_txids": list(d.get("txids") or [])[:10],
        "source": "blockbook",
    }


def _address_blockchair(addr: str) -> Optional[Dict[str, Any]]:
    d = http_get_json(f"https://api.blockchair.com/dogecoin/dashboards/address/{addr}?limit=10")
    data = ((d or {}).get("data") or {}).get(addr)
    if not data:
        return None
    a = data.get("address") or {}
    if a.get("balance") is None and not a.get("transaction_count"):
        # blockchair returns zeroed struct for unknown addresses; still valid (0 balance)
        pass
    return {
        "address": addr,
        "balance_doge": round(float(a.get("balance") or 0) / 1e8, 8),
        "received_doge": round(float(a.get("received") or 0) / 1e8, 8),
        "spent_doge": round(float(a.get("spent") or 0) / 1e8, 8),
        "tx_count": int(a.get("transaction_count") or 0),
        "first_seen": a.get("first_seen_receiving", ""),
        "last_seen": a.get("last_seen_receiving", ""),
        "recent_txids": list(data.get("transactions") or [])[:10],
        "source": "blockchair",
    }


def _address_blockcypher(addr: str) -> Optional[Dict[str, Any]]:
    d = http_get_json(f"https://api.blockcypher.com/v1/doge/main/addrs/{addr}")
    if not d or d.get("error"):
        return None
    txrefs = d.get("txrefs") or []
    return {
        "address": addr,
        "balance_doge": round(float(d.get("balance") or 0) / 1e8, 8),
        "received_doge": round(float(d.get("total_received") or 0) / 1e8, 8),
        "spent_doge": round(float(d.get("total_sent") or 0) / 1e8, 8),
        "tx_count": int(d.get("n_tx") or 0),
        "first_seen": "",
        "last_seen": "",
        "recent_txids": [t.get("tx_hash", "") for t in txrefs[:10]],
        "source": "blockcypher",
    }


def get_address(addr: str) -> Optional[Dict[str, Any]]:
    addr = (addr or "").strip()
    if not addr:
        return None
    return _cached(f"addr:{addr}", 120,
                   lambda: _first(lambda: _address_blockbook(addr),
                                  lambda: _address_blockchair(addr),
                                  lambda: _address_blockcypher(addr)))


def fetch_address_balance(addr: str) -> Optional[float]:
    """Balance-only helper used by the miner's background wallet refresh."""
    info = get_address(addr)
    if info is None:
        return None
    return info.get("balance_doge")


# ---------------- transaction lookup ----------------

def _tx_blockbook(txid: str) -> Optional[Dict[str, Any]]:
    d = http_get_json(f"{BLOCKBOOK}/tx/{txid}")
    if not d or d.get("error") or "txid" not in d:
        return None
    return {
        "txid": txid,
        "block_height": int(d.get("blockHeight") or -1),
        "time": str(d.get("blockTime") or ""),
        "fee_doge": round(float(d.get("fees") or 0) / 1e8, 8),
        "input_total_doge": round(float(d.get("valueIn") or 0) / 1e8, 8),
        "output_total_doge": round(float(d.get("value") or 0) / 1e8, 8),
        "size": int(d.get("size") or 0),
        "source": "blockbook",
    }


def _tx_blockchair(txid: str) -> Optional[Dict[str, Any]]:
    d = http_get_json(f"https://api.blockchair.com/dogecoin/dashboards/transaction/{txid}")
    data = ((d or {}).get("data") or {}).get(txid)
    if not data:
        return None
    t = data.get("transaction") or {}
    return {
        "txid": txid,
        "block_height": int(t.get("block_id") or -1),
        "time": t.get("time", ""),
        "fee_doge": round(float(t.get("fee") or 0) / 1e8, 8),
        "input_total_doge": round(float(t.get("input_total") or 0) / 1e8, 8),
        "output_total_doge": round(float(t.get("output_total") or 0) / 1e8, 8),
        "size": int(t.get("size") or 0),
        "source": "blockchair",
    }


def _tx_blockcypher(txid: str) -> Optional[Dict[str, Any]]:
    d = http_get_json(f"https://api.blockcypher.com/v1/doge/main/txs/{txid}")
    if not d or d.get("error"):
        return None
    return {
        "txid": txid,
        "block_height": int(d.get("block_height", -1)),
        "time": d.get("confirmed", ""),
        "fee_doge": round(float(d.get("fees") or 0) / 1e8, 8),
        "input_total_doge": 0,
        "output_total_doge": round(float(d.get("total") or 0) / 1e8, 8),
        "size": int(d.get("size") or 0),
        "source": "blockcypher",
    }


def get_tx(txid: str) -> Optional[Dict[str, Any]]:
    txid = (txid or "").strip()
    if not txid or len(txid) != 64:
        return None
    return _cached(f"tx:{txid}", 300,
                   lambda: _first(lambda: _tx_blockbook(txid),
                                  lambda: _tx_blockchair(txid),
                                  lambda: _tx_blockcypher(txid)))
