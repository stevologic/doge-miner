import time
import unittest
import hashlib
import struct
# Pure package import. Run tests with: python -m unittest backend.tests.test_miner -v (from project root)
from backend.miner import (
    DogeMiner, diff_to_target_int, evaluate_share, evaluate_share_ex, hash_meets_target,
    QueuedStratumSocket, compute_effort_percent, compute_current_nonce, compute_luck,
    compute_streak, compute_efficiency, compute_effort_text,
    build_header, build_header_prefix, build_coinbase, merkle_root, swap_endian_words,
    nbits_to_target, default_scrypt, share_difficulty, DIFF1_TARGET, MAX_TARGET,
)


def sha256d(b: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


# ---- Known-chain fixtures (fetched from the public Dogecoin chain, embedded as vectors) ----
# Dogecoin genesis block (height 0):
GENESIS_HASH = "1a91e3dace36e2be3bf030a65679fe821aa1d6ef92e7c9902eb318182c355691"
GENESIS_MERKLE_DISPLAY = "5b2a3f53f605d62c53e62932dac6925e3d74afa5a4b459745c36d42d0ed26a69"
GENESIS_TIME = 1386325540           # 0x52a1a624
GENESIS_BITS = "1e0ffff0"
GENESIS_NONCE = 99943               # 0x00018667
# Raw 80-byte header straight from the raw block (blockchair /raw/block):
GENESIS_HEADER_HEX = (
    "01000000" + "00" * 32 +
    "696ad20e2dd4365c7459b4a4a5af743d5e92c6da3229e6532cd605f6533f2a5b" +
    "24a6a152" + "f0ff0f1e" + "67860100"
)
# The single (coinbase) transaction of the genesis block ("Nintondo"):
GENESIS_COINBASE_HEX = (
    "01000000010000000000000000000000000000000000000000000000000000000000000000"
    "ffffffff1004ffff001d0104084e696e746f6e646fffffffff010058850c02000000434104"
    "0184710fa689ad5023690c80f3a49c8f13f8d45b8c857fbcbc8bc4a8e4d3eb4b10f4d4604f"
    "a08dce601aaf0f470216fe1b51850b4acf21b179c45070ac7b03a9ac00000000"
)

# Dogecoin block 1:
BLOCK1_HASH = "82bc68038f6034c0596b6e313729793a887fded6e92a31fbdf70863f89d9bea2"
BLOCK1_MERKLE_DISPLAY = "5f7e779f7600f54e528686e91d5891f3ae226ee907f461692519e549105f521c"
BLOCK1_TIME = 1386474927
BLOCK1_BITS = "1e0ffff0"
BLOCK1_NONCE = 1417875456
# Stratum wire-format prevhash (display hash's 4-byte chunks in reverse chunk order —
# computed independently of swap_endian_words so this test is non-circular):
BLOCK1_STRATUM_PREVHASH = "2c3556912eb3181892e7c9901aa1d6ef5679fe823bf030a6ce36e2be1a91e3da"


class TestKnownChainVectors(unittest.TestCase):
    """Prove header serialization, scrypt PoW direction and target math against the real chain."""

    def test_genesis_header_serialization_and_sha256d(self):
        merkle_internal = bytes.fromhex(GENESIS_MERKLE_DISPLAY)[::-1]
        hdr = build_header("00000001", "00" * 32, merkle_internal,
                           f"{GENESIS_TIME:08x}", GENESIS_BITS, GENESIS_NONCE)
        self.assertEqual(hdr.hex(), GENESIS_HEADER_HEX)
        self.assertEqual(sha256d(hdr)[::-1].hex(), GENESIS_HASH)

    def test_genesis_scrypt_pow_meets_nbits_target_little_endian(self):
        hdr = bytes.fromhex(GENESIS_HEADER_HEX)
        pow_hash = default_scrypt(hdr)
        pow_int = int.from_bytes(pow_hash, "little")
        self.assertTrue(hash_meets_target(pow_int, nbits_to_target(GENESIS_BITS)))
        # and it satisfies pool difficulty 1 (nbits target is 16x smaller than diff1)
        self.assertTrue(hash_meets_target(pow_int, diff_to_target_int(1.0)))
        # big-endian interpretation would NOT meet the target (guards the old bug)
        self.assertFalse(hash_meets_target(int.from_bytes(pow_hash, "big"),
                                           nbits_to_target(GENESIS_BITS)))

    def test_block1_stratum_prevhash_word_swap(self):
        """Non-circular: hardcoded stratum-format prevhash + build_header must reproduce block 1."""
        merkle_internal = bytes.fromhex(BLOCK1_MERKLE_DISPLAY)[::-1]
        hdr = build_header("00000001", BLOCK1_STRATUM_PREVHASH, merkle_internal,
                           f"{BLOCK1_TIME:08x}", BLOCK1_BITS, BLOCK1_NONCE)
        self.assertEqual(sha256d(hdr)[::-1].hex(), BLOCK1_HASH)

    def test_evaluate_share_genesis_end_to_end(self):
        """Full pool-job path with the real genesis coinbase: coinbase -> merkle -> header ->
        scrypt -> target check -> mining.submit payload."""
        job = {
            "job_id": "gen0",
            "prevhash": "00" * 32,
            "coinb1": GENESIS_COINBASE_HEX,
            "coinb2": "",
            "merkle_branch": [],
            "version": "00000001",
            "nbits": GENESIS_BITS,
            "ntime": f"{GENESIS_TIME:08x}",
            "clean": True,
        }
        submit, pow_hash = evaluate_share_ex(job, "", "", f"{GENESIS_TIME:08x}",
                                             GENESIS_NONCE, "DTestUser", 1.0, None)
        self.assertIsNotNone(pow_hash)
        # merkle root of a single-tx block is sha256d(coinbase); display order must match
        cb = build_coinbase(GENESIS_COINBASE_HEX, "", "", "")
        self.assertEqual(merkle_root(cb, [])[::-1].hex(), GENESIS_MERKLE_DISPLAY)
        # genesis PoW passes pool difficulty 1 -> submit produced with correct fields
        self.assertIsNotNone(submit)
        self.assertIn("mining.submit", submit)
        self.assertIn("gen0", submit)
        self.assertIn(f"{GENESIS_NONCE:08x}", submit)
        self.assertIn("DTestUser", submit)
        # at very high difficulty the same hash must NOT produce a share
        submit_hi = evaluate_share(job, "", "", f"{GENESIS_TIME:08x}",
                                   GENESIS_NONCE, "DTestUser", 1_000_000.0, None)
        self.assertIsNone(submit_hi)

    def test_genesis_share_difficulty_sane(self):
        hdr = bytes.fromhex(GENESIS_HEADER_HEX)
        sdiff = share_difficulty(default_scrypt(hdr))
        # genesis PoW is ~diff 100 in pool units (must be >= 16 = nbits/diff1 ratio)
        self.assertGreater(sdiff, 16.0)
        self.assertLess(sdiff, 10_000.0)

    def test_swap_endian_words_involution_and_alignment(self):
        self.assertEqual(swap_endian_words("aabbccdd"), bytes.fromhex("ddccbbaa"))
        self.assertEqual(swap_endian_words("0102030405060708"),
                         bytes.fromhex("0403020108070605"))
        with self.assertRaises(ValueError):
            swap_endian_words("aabbcc")  # not word aligned


class TestTargets(unittest.TestCase):
    def test_diff1_is_scrypt_pool_convention(self):
        self.assertEqual(diff_to_target_int(1.0), 0xffff << 224)

    def test_diff_to_target_int_table_driven(self):
        cases = [0.1, 0.0001, 1.0, 16.0, 1024.0]
        targets = {}
        for d in cases:
            t = diff_to_target_int(d)
            self.assertIsInstance(t, int)
            self.assertGreater(t, 0)
            targets[d] = t
        self.assertGreater(targets[0.0001], targets[0.1])
        self.assertGreater(targets[0.1], targets[1.0])
        self.assertGreater(targets[1.0], targets[16.0])
        self.assertGreater(targets[16.0], targets[1024.0])
        self.assertFalse(hash_meets_target((1 << 255), diff_to_target_int(16.0)))
        # sub-diff targets clamp to max instead of overflowing
        self.assertEqual(diff_to_target_int(1e-12), MAX_TARGET)
        self.assertEqual(diff_to_target_int(0), MAX_TARGET)
        self.assertEqual(diff_to_target_int(None), MAX_TARGET)

    def test_nbits_to_target(self):
        # 1e0ffff0 -> 0x0ffff0 << (8*(0x1e-3))
        self.assertEqual(nbits_to_target("1e0ffff0"), 0x0ffff0 << (8 * (0x1e - 3)))
        self.assertEqual(nbits_to_target("1d00ffff"), 0xffff << (8 * (0x1d - 3)))


class TestRealDogeMiner(unittest.TestCase):
    def test_scrypt_produces_32_byte_real_output(self):
        m = DogeMiner()
        h = m._scrypt_work(42, "job1")
        self.assertIsInstance(h, (bytes, bytearray))
        self.assertEqual(len(h), 32)

    def test_start_cpu_vs_gpu_distinct_modes(self):
        m = DogeMiner()
        m.start("DWallet123", "cpu", workers=1)
        self.assertTrue(m.get_stats()["running"])
        self.assertEqual(m.get_stats()["mode"], "cpu")
        self.assertEqual(m.get_stats()["worker_count"], 1)
        m.stop()

        m.start("DWallet123", "gpu", workers=1)
        s = m.get_stats()
        self.assertEqual(s["mode"], "gpu")
        # honest telemetry: either real opencl or clearly-labeled cpu fallback
        self.assertIn(s["gpu_backend"], ("opencl", "cpu-fallback"))
        m.stop()

    def test_user_workers_config_used(self):
        m = DogeMiner()
        m.start("DWalletCfg", "cpu", workers=5)
        self.assertEqual(m.get_stats()["worker_count"], 5)
        m.stop()

    def test_default_workers_use_cpu_count(self):
        import os
        m = DogeMiner()
        m.start("DWalletDefault", "cpu")
        expect = max(1, (os.cpu_count() or 2) - 1)
        self.assertEqual(m.get_stats()["worker_count"], expect)
        m.stop()

    def test_real_scrypt_work_increments_hashes(self):
        m = DogeMiner()
        m.start("DWalletWork", "cpu", workers=1)
        time.sleep(0.8)
        stats = m.get_stats()
        self.assertTrue(stats["running"])
        self.assertGreater(stats["total_hashes"], 0)
        self.assertGreater(stats["hashrate_khs"], 0)
        m.stop()

    def test_uptime_and_wallet_in_stats(self):
        m = DogeMiner()
        w = "DTW2M5oEW97WbmYJRM71qD7uE6xfJs1MUK"
        m.start(w, "cpu", workers=1)
        time.sleep(0.3)
        s = m.get_stats()
        self.assertEqual(s["wallet"], w)
        self.assertGreaterEqual(s["uptime_seconds"], 0)
        m.stop()

    def test_monitoring_fields_present(self):
        m = DogeMiner()
        m.start("DMon", "cpu", workers=2)
        time.sleep(0.6)
        s = m.get_stats()
        for k in ("cpu_percent", "gpu_percent", "mem_percent"):
            self.assertIn(k, s)
            self.assertIsInstance(s[k], (int, float))
        m.stop()

    def test_new_telemetry_fields_present(self):
        m = DogeMiner()
        m.start("DTelemetry", "cpu", workers=1)
        time.sleep(0.4)
        s = m.get_stats()
        for k in ("shares_submitted", "last_share_hash", "best_share_diff",
                  "pool_difficulty", "pool_target", "pool_user", "pool_error",
                  "pool_id", "has_job", "gpu_available", "gpu_backend", "gpu_devices"):
            self.assertIn(k, s)
        self.assertEqual(s["pool_user"], "DTelemetry")  # defaults to wallet
        m.stop()

    def test_pool_user_and_pass_override(self):
        m = DogeMiner()
        m.start("DWalletX", "cpu", workers=1, pool_user="account.worker1", pool_pass="secret")
        self.assertEqual(m.pool_user, "account.worker1")
        self.assertEqual(m.pool_pass, "secret")
        auth = m._format_authorize(m.pool_user, m.pool_pass)
        self.assertIn("account.worker1", auth)
        self.assertIn("secret", auth)
        m.stop()

    def test_gpu_util_helper_cross_platform(self):
        m = DogeMiner()
        for _ in range(3):
            v = m._get_gpu_util()
            self.assertIsInstance(v, float)
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 100.0)

    def test_stratum_helpers_pure(self):
        m = DogeMiner()
        sub = m._format_subscribe()
        self.assertIn("mining.subscribe", sub)
        auth = m._format_authorize("DTestWallet", "c=DOGE")
        self.assertIn("DTestWallet", auth)
        self.assertIn("c=DOGE", auth)
        submit = m._format_submit("DTest", "job1", "0000", "12345678", "00000001")
        self.assertIn("mining.submit", submit)

        d = m._parse_difficulty([16.0])
        self.assertEqual(d, 16.0)
        job = m._parse_notify(["job1", "prev", "c1", "c2", [], "1", "1d00ffff", "5e0e0e0e", True])
        self.assertEqual(job["job_id"], "job1")

        target_int = m._diff_to_target(16.0)
        self.assertIsInstance(target_int, int)
        self.assertGreater(target_int, 0)

    def test_real_work_and_share_flow(self):
        m = DogeMiner()
        m.start("DRealPool", "cpu", workers=1)
        time.sleep(0.5)
        s = m.get_stats()
        self.assertTrue(s["running"])
        self.assertGreaterEqual(s["shares_accepted"], 0)
        self.assertGreaterEqual(s["shares_rejected"], 0)
        m.stop()

    def test_evaluate_share_pure_returns_submit_on_low_diff_real_scrypt(self):
        job = {
            "job_id": "job1",
            "prevhash": "0000000000000000000000000000000000000000000000000000000000000000",
            "coinb1": "01",
            "coinb2": "02",
            "merkle_branch": [],
            "version": "00000001",
            "nbits": "1d00ffff",
            "ntime": "5e0e0e0e",
            "clean": True,
        }
        # tiny diff => target clamps to max => real scrypt hash will meet
        submit = evaluate_share(job, "abcd", "00000000", "5e0e0e0e", 12345, "DTestPoolW", 1e-9, None)
        self.assertIsNotNone(submit)
        self.assertIn("mining.submit", submit)
        self.assertIn("job1", submit)

    def test_handle_stratum_accept_increments_shares(self):
        m = DogeMiner()
        self.assertEqual(m.get_stats()["shares_accepted"], 0)
        self.assertEqual(m.get_stats()["shares_rejected"], 0)
        m._handle_stratum({"id": 4, "result": True})
        self.assertEqual(m.get_stats()["shares_accepted"], 1)
        m._handle_stratum({"id": 4, "result": False, "error": ["rejected"]})
        self.assertEqual(m.get_stats()["shares_rejected"], 1)

    def test_handle_stratum_set_extranonce(self):
        m = DogeMiner()
        m._handle_stratum({"id": None, "method": "mining.set_extranonce", "params": ["ffaa", 8]})
        self.assertEqual(m.extranonce1, "ffaa")
        self.assertEqual(m.extranonce2_size, 8)

    def test_worker_submit_and_accept_via_recv_loop(self):
        """Shipped integration: QueuedStratumSocket preloaded with pool lines.
        start() + real _recv_loop + real worker/evaluate -> submit in .sent; push accept -> shares_accepted==1."""
        m = DogeMiner()
        q = QueuedStratumSocket()
        q.push(b'{"id":1,"result":[[], "abcd", 4]}\n')
        q.push(b'{"id":2,"result":true}\n')
        q.push(b'{"id":null,"method":"mining.set_difficulty","params":[0.0000001]}\n')
        q.push(b'{"id":null,"method":"mining.notify","params":["job1","0000000000000000000000000000000000000000000000000000000000000000","01","02",[],"00000001","1d00ffff","5e0e0e0e",true]}\n')
        m.socket_factory = lambda *a, **k: q
        m.start("DQueued", "cpu", workers=1)
        deadline = time.time() + 6
        submitted = False
        while time.time() < deadline:
            sent = b"".join(q.sent) if q.sent else b""
            if b"mining.submit" in sent:
                submitted = True
                break
            time.sleep(0.05)
        self.assertTrue(submitted, "worker must have sent mining.submit via queued socket")
        # submitted-share counter is real
        self.assertGreaterEqual(m.get_stats()["shares_submitted"], 1)
        q.push(b'{"id":4,"result":true}\n')
        deadline = time.time() + 4
        while time.time() < deadline:
            if m.get_stats()["shares_accepted"] >= 1:
                break
            time.sleep(0.05)
        s = m.get_stats()
        self.assertGreaterEqual(s["shares_accepted"], 1)
        m.stop()

    def test_effort_pures_invariants(self):
        e1 = compute_effort_percent(100, 10)
        e2 = compute_effort_percent(200, 10)
        self.assertGreater(e2, e1)
        e_small_t = compute_effort_percent(500, 5)
        e_large_t = compute_effort_percent(500, 10)
        self.assertGreaterEqual(e_small_t, e_large_t)
        self.assertEqual(compute_luck(0, 100), 100.0)
        self.assertEqual(compute_luck(0, 10000), 100.0)
        # real luck: at diff 64, 4 shares from 10M hashes is ~167% (expected ~2.4)
        lucky = compute_luck(4, 10_000_000, 64.0)
        self.assertGreater(lucky, 120.0)
        self.assertLess(lucky, 250.0)
        # exactly on expectation ~= 100%: expected shares for diff 1 = h*65535/2^32
        h = int(1 * 4294967296 / 65535 * 10)  # ~10 expected shares
        on_par = compute_luck(10, h, 1.0)
        self.assertAlmostEqual(on_par, 100.0, delta=1.0)
        # unknown difficulty falls back to neutral 100
        self.assertEqual(compute_luck(1, 1000000), 100.0)

    def test_scrypt_effort_real_datapoint_pures(self):
        self.assertGreaterEqual(compute_effort_percent(0, 0), 0)
        self.assertLessEqual(compute_effort_percent(0, 0), 99.9)
        p = compute_effort_percent(50000, 10)
        self.assertGreater(p, 0)
        self.assertLessEqual(p, 99.9)
        self.assertEqual(compute_current_nonce(0), "0x00000000")
        self.assertEqual(compute_current_nonce(0x1234abcd), "0x1234abcd")
        self.assertEqual(compute_luck(0, 100), 100.0)
        l = compute_luck(5, 100000, 16.0)
        self.assertGreater(l, 0)
        self.assertEqual(compute_streak(3, 10), 3)
        self.assertEqual(compute_streak(3, 0), 0)
        self.assertEqual(compute_efficiency(5, 5), 50.0)
        self.assertEqual(compute_efficiency(0, 0), 100.0)
        self.assertEqual(compute_effort_text(0, True), "SEARCHING")
        self.assertEqual(compute_effort_text(100, True), "HASHING")
        self.assertEqual(compute_effort_text(0, False), "STOPPED")

    def test_effort_get_stats_share_variation(self):
        m = DogeMiner()
        q = QueuedStratumSocket()
        q.push(b'{"id":1,"result":[[], "abcd", 4]}\n')
        q.push(b'{"id":2,"result":true}\n')
        q.push(b'{"id":null,"method":"mining.set_difficulty","params":[0.0001]}\n')
        q.push(b'{"id":null,"method":"mining.notify","params":["job1","0000000000000000000000000000000000000000000000000000000000000000","01","02",[],"00000001","1d00ffff","5e0e0e0e",true]}\n')
        m.socket_factory = lambda *a, **k: q
        m.start("DQueuedEffort", "cpu", workers=1)
        time.sleep(0.5)
        q.push(b'{"id":4,"result":true}\n')
        time.sleep(0.3)
        s1 = m.get_stats()
        q.push(b'{"id":4,"result":true}\n')
        time.sleep(0.3)
        q.push(b'{"id":4,"result":false,"error":["rejected"]}\n')
        time.sleep(0.3)
        s3 = m.get_stats()
        m.stop()
        for k in ("effort_percent", "current_nonce", "luck", "streak", "efficiency", "effort_text"):
            self.assertIn(k, s1)
        self.assertGreaterEqual(s1["streak"], 1)
        self.assertLess(s3["efficiency"], 100.0)
        self.assertEqual(s3["streak"], 0)
        self.assertNotEqual(s1.get("current_nonce"), "0x00000000")
        self.assertGreater(s1.get("total_hashes", 0), 0)
        # real hash telemetry flows from workers
        self.assertNotEqual(s1.get("last_share_hash", ""), "")
        self.assertGreater(s1.get("best_share_diff", 0), 0)

    def test_pool_host_port_configurable_and_used(self):
        m = DogeMiner()
        # default is a no-registration wallet-login pool
        self.assertEqual(m.pool_host, 'scrypt.mine.zpool.ca')
        self.assertEqual(m.pool_port, 3433)
        m.pool_host = 'custom.pool.example'
        m.pool_port = 4444
        self.assertEqual(m.pool_host, 'custom.pool.example')
        self.assertEqual(m.pool_port, 4444)


class TestWalletValidation(unittest.TestCase):
    def test_base58check_validation(self):
        from backend.wallet_validation import is_valid_doge_wallet, b58check_encode
        # real shipped default address must be checksum-valid
        self.assertTrue(is_valid_doge_wallet("DTW2M5oEW97WbmYJRM71qD7uE6xfJs1MUK"))
        # constructed valid P2PKH (D...) and P2SH (9/A...) addresses
        p2pkh = b58check_encode(0x1E, bytes(range(20)))
        p2sh = b58check_encode(0x16, bytes(range(20, 40)))
        self.assertTrue(p2pkh.startswith("D"))
        self.assertIn(p2sh[0], "9A")
        self.assertTrue(is_valid_doge_wallet(p2pkh))
        self.assertTrue(is_valid_doge_wallet(p2sh))
        # corrupt the checksum -> invalid
        corrupted = p2pkh[:-1] + ("2" if p2pkh[-1] != "2" else "3")
        self.assertFalse(is_valid_doge_wallet(corrupted))
        # wrong version byte (bitcoin P2PKH 0x00) -> invalid even with good checksum
        btc = b58check_encode(0x00, bytes(range(20)))
        self.assertFalse(is_valid_doge_wallet(btc))
        # format garbage
        self.assertFalse(is_valid_doge_wallet(""))
        self.assertFalse(is_valid_doge_wallet("0" + "1" * 33))
        self.assertFalse(is_valid_doge_wallet("D" + "0" * 33))  # 0 not base58
        # plausible-format but bad checksum
        self.assertFalse(is_valid_doge_wallet("9" + "1" * 33))

    def test_wallet_validation_parity_with_frontend(self):
        """Frontend regex is a format pre-filter; backend adds checksum. On format-level
        cases both must agree. Executed via cscript on the exact shipped JS when available."""
        from backend.wallet_validation import is_valid_doge_wallet
        import subprocess, re, os, tempfile, shutil as _shutil
        good = "DTW2M5oEW97WbmYJRM71qD7uE6xfJs1MUK"
        self.assertTrue(is_valid_doge_wallet(good))
        self.assertFalse(is_valid_doge_wallet("BADWALLET123"))

        if not _shutil.which("cscript"):
            self.skipTest("cscript not available (non-Windows)")
        with open("frontend/index.html", encoding="utf-8") as f:
            html = f.read()
        m = re.search(r"function isValidDogeAddress\(addr\)\s*\{", html)
        self.assertIsNotNone(m, "could not find isValidDogeAddress in shipped index.html")
        start = m.start()
        depth = 0
        i = start
        started = False
        end = start
        while i < len(html):
            c = html[i]
            if c == "{":
                depth += 1
                started = True
            elif c == "}" and started:
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
            i += 1
        js_body = html[start:end]
        js = "if(!String.prototype.trim)String.prototype.trim=function(){return this.replace(/^\\s+|\\s+$/g,\"\");};\n"
        js += js_body + "\n"
        js += f"WScript.Echo( isValidDogeAddress(\"{good}\") ? \"good\" : \"bad\" );\n"
        js += "WScript.Echo( isValidDogeAddress(\"BADWALLET123\") ? \"good\" : \"bad\" );\n"
        js += "var badD0='D';for(var i=0;i<33;i++)badD0+='0';WScript.Echo( isValidDogeAddress(badD0) ? \"good\" : \"bad\" );\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False, encoding="utf-8") as tf:
            tf.write(js)
            jspath = tf.name
        try:
            out = subprocess.check_output(["cscript", "//Nologo", "//E:JScript", jspath],
                                          stderr=subprocess.STDOUT, text=True, timeout=30)
            lines = [l.strip() for l in out.strip().splitlines()]
            self.assertEqual(lines, ["good", "bad", "bad"])
        finally:
            os.unlink(jspath)


if __name__ == "__main__":
    unittest.main(verbosity=2)


def _gpu_available():
    try:
        from backend.gpu import ScryptGPU
        g = ScryptGPU()
        ok = bool(g.init())
        g.release()
        return ok
    except Exception:
        return False


@unittest.skipUnless(_gpu_available(), "no OpenCL GPU device / pyopencl not installed")
class TestGpuBackend(unittest.TestCase):
    """Real-GPU tests: skipped automatically on machines without an OpenCL GPU."""

    def test_kernel_self_test_matches_hashlib(self):
        from backend.gpu import ScryptGPU
        g = ScryptGPU()
        self.assertTrue(g.init())
        self.assertTrue(g.self_test(), "GPU scrypt kernel must match hashlib.scrypt")
        g.release()

    def test_scan_finds_cpu_verified_candidates_at_easy_target(self):
        import os as _os
        from backend.miner import diff_to_target_int
        from backend.gpu import ScryptGPU
        g = ScryptGPU()
        self.assertTrue(g.init())
        self.assertTrue(g.self_test())
        prefix = _os.urandom(76)
        target = diff_to_target_int(0.001)  # ~1 candidate per 66 hashes
        count, candidates, samples = g.scan(prefix, 0, target)
        self.assertGreater(count, 0)
        self.assertGreater(len(candidates), 0, "easy target must yield candidates")
        # every candidate is CPU-verified inside scan(); double-check one here
        n, h = candidates[0]
        self.assertLess(int.from_bytes(h, "little"), target)
        self.assertGreater(len(samples), 0)
        g.release()

    def test_miner_gpu_mode_uses_opencl_and_submits(self):
        m = DogeMiner()
        q = QueuedStratumSocket()
        q.push(b'{"id":1,"result":[[], "abcd", 4]}\n')
        q.push(b'{"id":2,"result":true}\n')
        q.push(b'{"id":null,"method":"mining.set_difficulty","params":[0.001]}\n')
        q.push(b'{"id":null,"method":"mining.notify","params":["jobG","0000000000000000000000000000000000000000000000000000000000000000","01","02",[],"00000001","1d00ffff","5e0e0e0e",true]}\n')
        m.socket_factory = lambda *a, **k: q
        m.start("DTW2M5oEW97WbmYJRM71qD7uE6xfJs1MUK", "gpu")
        deadline = time.time() + 25
        while time.time() < deadline:
            if b"mining.submit" in b"".join(q.sent):
                break
            time.sleep(0.25)
        s = m.get_stats()
        m.stop()
        self.assertEqual(s["gpu_backend"], "opencl")
        self.assertTrue(s["gpu_devices"])
        self.assertGreater(s["total_hashes"], 0)
        self.assertGreaterEqual(s["shares_submitted"], 1)
        self.assertIn(b"mining.submit", b"".join(q.sent))
