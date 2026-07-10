import unittest
import time

from backend import chain
from backend.pools import POOLS, get_pool


def make_stub(responses, calls):
    """Stub http_get_json: `responses` maps a URL substring -> payload or Exception.
    Records every URL in `calls`."""
    def stub(url, timeout=8.0):
        calls.append(url)
        for frag, payload in responses.items():
            if frag in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise RuntimeError(f"unstubbed url: {url}")
    return stub


BLOCKCHAIR_STATS = {"data": {
    "best_block_height": 6283789, "best_block_hash": "d75a...", "difficulty": 42709402.8,
    "blocks_24h": 1365, "transactions_24h": 27385, "mempool_transactions": 80,
    "circulation": 1.55e19, "market_price_usd": 0.074,
}}
COINGECKO = {"dogecoin": {"usd": 0.0743, "btc": 1.15e-06, "usd_24h_change": 2.2}}
BLOCKCYPHER_CHAIN = {"height": 6283789, "hash": "d75a...", "unconfirmed_count": 5}


class TestChainModule(unittest.TestCase):
    def setUp(self):
        # isolate cache between tests
        chain._cache.clear()
        chain._stale.clear()
        self._orig = chain.http_get_json

    def tearDown(self):
        chain.http_get_json = self._orig

    def test_summary_primary_provider_with_price(self):
        calls = []
        chain.http_get_json = make_stub({
            "blockchair.com/dogecoin/stats": BLOCKCHAIR_STATS,
            "coingecko.com": COINGECKO,
        }, calls)
        s = chain.get_summary()
        self.assertEqual(s["height"], 6283789)
        self.assertAlmostEqual(s["difficulty"], 42709402.8)
        self.assertGreater(s["network_hashrate_hs"], 1e15)  # ~3 PH/s for doge
        self.assertAlmostEqual(s["price_usd"], 0.0743)
        self.assertEqual(s["source"], "blockchair")

    def test_summary_cached_no_repeat_calls(self):
        calls = []
        chain.http_get_json = make_stub({
            "blockchair.com/dogecoin/stats": BLOCKCHAIR_STATS,
            "coingecko.com": COINGECKO,
        }, calls)
        chain.get_summary()
        n = len(calls)
        chain.get_summary()
        chain.get_summary()
        self.assertEqual(len(calls), n, "cached summary must not hit providers again")

    def test_summary_failover_to_blockcypher(self):
        calls = []
        chain.http_get_json = make_stub({
            "blockchair.com/dogecoin/stats": RuntimeError("provider down"),
            "blockcypher.com/v1/doge/main": BLOCKCYPHER_CHAIN,
            "coingecko.com": COINGECKO,
        }, calls)
        s = chain.get_summary()
        self.assertEqual(s["height"], 6283789)
        self.assertEqual(s["source"], "blockcypher")

    def test_summary_serves_stale_on_total_failure(self):
        calls = []
        chain.http_get_json = make_stub({
            "blockchair.com/dogecoin/stats": BLOCKCHAIR_STATS,
            "coingecko.com": COINGECKO,
        }, calls)
        s1 = chain.get_summary()
        self.assertIsNotNone(s1)
        # expire the cache, then break all providers
        chain._cache.clear()
        chain.http_get_json = make_stub({}, calls)  # everything raises
        s2 = chain.get_summary()
        self.assertIsNotNone(s2, "must serve last good value when all providers fail")
        self.assertTrue(s2.get("stale"))
        self.assertEqual(s2["height"], s1["height"])

    def test_address_lookup_and_balance(self):
        addr = "DTW2M5oEW97WbmYJRM71qD7uE6xfJs1MUK"
        calls = []
        chain.http_get_json = make_stub({
            f"dashboards/address/{addr}": {"data": {addr: {
                "address": {"balance": 12345678900, "received": 22345678900,
                            "spent": 10000000000, "transaction_count": 42},
                "transactions": ["ab" * 32, "cd" * 32],
            }}},
        }, calls)
        a = chain.get_address(addr)
        self.assertEqual(a["address"], addr)
        self.assertAlmostEqual(a["balance_doge"], 123.456789)
        self.assertEqual(a["tx_count"], 42)
        self.assertEqual(len(a["recent_txids"]), 2)
        # balance helper used by the miner
        self.assertAlmostEqual(chain.fetch_address_balance(addr), 123.456789)

    def test_address_failover_to_blockcypher(self):
        addr = "DTW2M5oEW97WbmYJRM71qD7uE6xfJs1MUK"
        calls = []
        chain.http_get_json = make_stub({
            "dashboards/address": RuntimeError("down"),
            f"addrs/{addr}": {"balance": 500000000, "total_received": 500000000,
                              "total_sent": 0, "n_tx": 3, "txrefs": []},
        }, calls)
        a = chain.get_address(addr)
        self.assertEqual(a["source"], "blockcypher")
        self.assertAlmostEqual(a["balance_doge"], 5.0)

    def test_blocks_list(self):
        calls = []
        chain.http_get_json = make_stub({
            "dogecoin/blocks": {"data": [
                {"id": 100, "hash": "aa", "time": "2026-07-10 00:00:00",
                 "transaction_count": 5, "size": 1000, "difficulty": 1.5},
                {"id": 99, "hash": "bb", "time": "2026-07-09 23:59:00",
                 "transaction_count": 2, "size": 500, "difficulty": 1.4},
            ]},
        }, calls)
        b = chain.get_blocks(2)
        self.assertEqual(len(b), 2)
        self.assertEqual(b[0]["height"], 100)

    def test_tx_lookup_rejects_bad_txid(self):
        self.assertIsNone(chain.get_tx("nothex"))
        self.assertIsNone(chain.get_tx(""))


class TestPools(unittest.TestCase):
    def test_presets_have_required_fields(self):
        required = {"id", "name", "host", "port", "registration_required",
                    "username_hint", "password_default", "payout", "web", "stats_url"}
        self.assertGreaterEqual(len(POOLS), 4)
        for p in POOLS:
            self.assertTrue(required.issubset(p.keys()), f"pool {p.get('id')} missing fields")
            self.assertIsInstance(p["port"], int)

    def test_no_registration_option_exists(self):
        noreg = [p for p in POOLS if not p["registration_required"]]
        self.assertGreaterEqual(len(noreg), 1, "must offer at least one no-registration pool")
        self.assertEqual(noreg[0]["id"], "zpool")

    def test_registered_options_exist(self):
        reg = [p for p in POOLS if p["registration_required"]]
        self.assertGreaterEqual(len(reg), 2, "must offer registered pool choices")

    def test_get_pool(self):
        self.assertIsNotNone(get_pool("zpool"))
        self.assertIsNone(get_pool("nope"))
        self.assertIsNone(get_pool(None))

    def test_zpool_has_public_worker_stats(self):
        z = get_pool("zpool")
        self.assertIn("walletEx", z.get("worker_stats_url", ""))
        self.assertIn("{user}", z.get("worker_stats_url", ""))


class TestApiEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            raise unittest.SkipTest("fastapi TestClient (httpx) not installed")
        from backend.main import app
        cls.client = TestClient(app)

    def setUp(self):
        chain._cache.clear()
        chain._stale.clear()
        self._orig = chain.http_get_json
        chain.http_get_json = make_stub({
            "blockchair.com/dogecoin/stats": BLOCKCHAIR_STATS,
            "coingecko.com": COINGECKO,
        }, [])

    def tearDown(self):
        chain.http_get_json = self._orig

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

    def test_pools_endpoint(self):
        r = self.client.get("/api/pools")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["default"], "zpool")
        ids = [p["id"] for p in data["pools"]]
        self.assertIn("zpool", ids)
        self.assertIn("aikapool", ids)

    def test_chain_summary_endpoint(self):
        r = self.client.get("/api/chain/summary")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["height"], 6283789)

    def test_chain_address_rejects_invalid(self):
        r = self.client.get("/api/chain/address/NOTANADDRESS")
        self.assertEqual(r.status_code, 400)

    def test_start_rejects_bad_wallet_checksum(self):
        r = self.client.post("/api/start", json={"wallet": "D" + "a" * 30, "mode": "cpu"})
        self.assertEqual(r.status_code, 400)

    def test_start_stop_with_preset(self):
        r = self.client.post("/api/start", json={
            "wallet": "DTW2M5oEW97WbmYJRM71qD7uE6xfJs1MUK",
            "mode": "cpu", "workers": 1, "pool_id": "aikapool",
            "pool_user": "acct.rig1", "pool_pass": "pw",
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["pool"], "stratum.aikapool.com:7915")
        self.assertEqual(body["pool_user"], "acct.rig1")
        s = self.client.get("/api/stats").json()
        self.assertTrue(s["running"])
        self.assertEqual(s["pool_id"], "aikapool")
        self.assertIn("shares_submitted", s)
        self.assertIn("pool_target", s)
        # aikapool has no public per-worker page -> no worker link
        self.assertEqual(s["worker_link"], "")
        r = self.client.post("/api/stop")
        self.assertEqual(r.status_code, 200)

    def test_worker_link_for_zpool(self):
        wallet = "DTW2M5oEW97WbmYJRM71qD7uE6xfJs1MUK"
        r = self.client.post("/api/start", json={
            "wallet": wallet, "mode": "cpu", "workers": 1, "pool_id": "zpool",
        })
        self.assertEqual(r.status_code, 200)
        s = self.client.get("/api/stats").json()
        self.assertIn("walletEx", s["worker_link"])
        self.assertIn(wallet, s["worker_link"])
        self.assertIn(wallet, s["pool_link"])
        self.client.post("/api/stop")


if __name__ == "__main__":
    unittest.main(verbosity=2)
