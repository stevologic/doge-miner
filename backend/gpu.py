"""Real GPU scrypt mining via OpenCL (NVIDIA / AMD / Intel on Windows, macOS, Linux).

Implements the full Dogecoin/Litecoin PoW function scrypt(N=1024, r=1, p=1, dkLen=32)
as an OpenCL kernel: PBKDF2-HMAC-SHA256 -> ROMix(Salsa20/8) -> PBKDF2-HMAC-SHA256.

Honesty guarantees:
  * The kernel must pass a self-test against Python's hashlib.scrypt on random
    headers before it is ever used (DogeMiner refuses the backend otherwise).
  * Every share candidate the GPU finds is re-verified on the CPU with
    hashlib.scrypt before being submitted to a pool.

pyopencl is an optional dependency: without it (or without any OpenCL GPU device)
DogeMiner falls back to CPU threads and reports gpu_backend="cpu-fallback".
"""

import hashlib
import os
import struct
import time
from typing import List, Optional, Tuple

MAX_CANDIDATES = 256
MAX_SAMPLES = 512

KERNEL_SOURCE = r"""
/* ---- SHA-256 ---- */
__constant uint SHA_K[64] = {
    0x428a2f98u,0x71374491u,0xb5c0fbcfu,0xe9b5dba5u,0x3956c25bu,0x59f111f1u,0x923f82a4u,0xab1c5ed5u,
    0xd807aa98u,0x12835b01u,0x243185beu,0x550c7dc3u,0x72be5d74u,0x80deb1feu,0x9bdc06a7u,0xc19bf174u,
    0xe49b69c1u,0xefbe4786u,0x0fc19dc6u,0x240ca1ccu,0x2de92c6fu,0x4a7484aau,0x5cb0a9dcu,0x76f988dau,
    0x983e5152u,0xa831c66du,0xb00327c8u,0xbf597fc7u,0xc6e00bf3u,0xd5a79147u,0x06ca6351u,0x14292967u,
    0x27b70a85u,0x2e1b2138u,0x4d2c6dfcu,0x53380d13u,0x650a7354u,0x766a0abbu,0x81c2c92eu,0x92722c85u,
    0xa2bfe8a1u,0xa81a664bu,0xc24b8b70u,0xc76c51a3u,0xd192e819u,0xd6990624u,0xf40e3585u,0x106aa070u,
    0x19a4c116u,0x1e376c08u,0x2748774cu,0x34b0bcb5u,0x391c0cb3u,0x4ed8aa4au,0x5b9cca4fu,0x682e6ff3u,
    0x748f82eeu,0x78a5636fu,0x84c87814u,0x8cc70208u,0x90befffau,0xa4506cebu,0xbef9a3f7u,0xc67178f2u
};

#define ROTR(x,n) rotate((uint)(x),(uint)(32-(n)))
#define CH(x,y,z)  (((x)&(y)) ^ (~(x)&(z)))
#define MAJ(x,y,z) (((x)&(y)) ^ ((x)&(z)) ^ ((y)&(z)))
#define EP0(x) (ROTR(x,2)^ROTR(x,13)^ROTR(x,22))
#define EP1(x) (ROTR(x,6)^ROTR(x,11)^ROTR(x,25))
#define SIG0(x) (ROTR(x,7)^ROTR(x,18)^((x)>>3))
#define SIG1(x) (ROTR(x,17)^ROTR(x,19)^((x)>>10))

static void sha256_transform(uint *state, const uchar *block) {
    uint w[64];
    for (int i = 0; i < 16; i++)
        w[i] = ((uint)block[i*4]<<24)|((uint)block[i*4+1]<<16)|((uint)block[i*4+2]<<8)|((uint)block[i*4+3]);
    for (int i = 16; i < 64; i++)
        w[i] = SIG1(w[i-2]) + w[i-7] + SIG0(w[i-15]) + w[i-16];
    uint a=state[0],b=state[1],c=state[2],d=state[3],e=state[4],f=state[5],g=state[6],h=state[7];
    for (int i = 0; i < 64; i++) {
        uint t1 = h + EP1(e) + CH(e,f,g) + SHA_K[i] + w[i];
        uint t2 = EP0(a) + MAJ(a,b,c);
        h=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
    }
    state[0]+=a; state[1]+=b; state[2]+=c; state[3]+=d;
    state[4]+=e; state[5]+=f; state[6]+=g; state[7]+=h;
}

/* full sha256 of msg[0..len-1], len <= 240 */
static void sha256(const uchar *msg, uint len, uchar *out32) {
    uint state[8] = {0x6a09e667u,0xbb67ae85u,0x3c6ef372u,0xa54ff53au,
                     0x510e527fu,0x9b05688cu,0x1f83d9abu,0x5be0cd19u};
    uchar block[64];
    uint i = 0;
    while (len - i >= 64) { sha256_transform(state, msg + i); i += 64; }
    uint rem = len - i;
    for (uint k = 0; k < rem; k++) block[k] = msg[i+k];
    block[rem] = 0x80;
    for (uint k = rem+1; k < 64; k++) block[k] = 0;
    if (rem >= 56) {
        sha256_transform(state, block);
        for (uint k = 0; k < 64; k++) block[k] = 0;
    }
    ulong bits = (ulong)len * 8;
    for (int k = 0; k < 8; k++) block[63-k] = (uchar)(bits >> (8*k));
    sha256_transform(state, block);
    for (int k = 0; k < 8; k++) {
        out32[k*4]   = (uchar)(state[k] >> 24);
        out32[k*4+1] = (uchar)(state[k] >> 16);
        out32[k*4+2] = (uchar)(state[k] >> 8);
        out32[k*4+3] = (uchar)(state[k]);
    }
}

/* HMAC-SHA256 with precomputed ipad/opad 64-byte key blocks */
static void hmac_sha256(const uchar *ipad, const uchar *opad,
                        const uchar *msg, uint msglen, uchar *out32) {
    uchar buf[240];
    for (int k = 0; k < 64; k++) buf[k] = ipad[k];
    for (uint k = 0; k < msglen; k++) buf[64+k] = msg[k];
    uchar inner[32];
    sha256(buf, 64 + msglen, inner);
    for (int k = 0; k < 64; k++) buf[k] = opad[k];
    for (int k = 0; k < 32; k++) buf[64+k] = inner[k];
    sha256(buf, 96, out32);
}

/* ---- Salsa20/8 core ---- */
#define R(a,b) rotate((uint)(a),(uint)(b))
static void salsa8(uint *B) {
    uint x[16];
    for (int i = 0; i < 16; i++) x[i] = B[i];
    for (int i = 0; i < 8; i += 2) {
        x[ 4] ^= R(x[ 0]+x[12], 7);  x[ 8] ^= R(x[ 4]+x[ 0], 9);
        x[12] ^= R(x[ 8]+x[ 4],13);  x[ 0] ^= R(x[12]+x[ 8],18);
        x[ 9] ^= R(x[ 5]+x[ 1], 7);  x[13] ^= R(x[ 9]+x[ 5], 9);
        x[ 1] ^= R(x[13]+x[ 9],13);  x[ 5] ^= R(x[ 1]+x[13],18);
        x[14] ^= R(x[10]+x[ 6], 7);  x[ 2] ^= R(x[14]+x[10], 9);
        x[ 6] ^= R(x[ 2]+x[14],13);  x[10] ^= R(x[ 6]+x[ 2],18);
        x[ 3] ^= R(x[15]+x[11], 7);  x[ 7] ^= R(x[ 3]+x[15], 9);
        x[11] ^= R(x[ 7]+x[ 3],13);  x[15] ^= R(x[11]+x[ 7],18);
        x[ 1] ^= R(x[ 0]+x[ 3], 7);  x[ 2] ^= R(x[ 1]+x[ 0], 9);
        x[ 3] ^= R(x[ 2]+x[ 1],13);  x[ 0] ^= R(x[ 3]+x[ 2],18);
        x[ 6] ^= R(x[ 5]+x[ 4], 7);  x[ 7] ^= R(x[ 6]+x[ 5], 9);
        x[ 4] ^= R(x[ 7]+x[ 6],13);  x[ 5] ^= R(x[ 4]+x[ 7],18);
        x[11] ^= R(x[10]+x[ 9], 7);  x[ 8] ^= R(x[11]+x[10], 9);
        x[ 9] ^= R(x[ 8]+x[11],13);  x[10] ^= R(x[ 9]+x[ 8],18);
        x[12] ^= R(x[15]+x[14], 7);  x[13] ^= R(x[12]+x[15], 9);
        x[14] ^= R(x[13]+x[12],13);  x[15] ^= R(x[14]+x[13],18);
    }
    for (int i = 0; i < 16; i++) B[i] += x[i];
}

/* BlockMix for r=1 on X[32]: (B0,B1) -> (Salsa(B1^B0), Salsa(Y0^B1)) */
static void blockmix(uint *X) {
    uint Y0[16], Y1[16];
    for (int k = 0; k < 16; k++) Y0[k] = X[16+k] ^ X[k];
    salsa8(Y0);
    for (int k = 0; k < 16; k++) Y1[k] = Y0[k] ^ X[16+k];
    salsa8(Y1);
    for (int k = 0; k < 16; k++) { X[k] = Y0[k]; X[16+k] = Y1[k]; }
}

/* hash (32 bytes) interpreted as little-endian 256-bit integer: is it < target? */
static int hash_lt_target(const uchar *h, __global const uchar *t) {
    for (int i = 31; i >= 0; i--) {
        if (h[i] < t[i]) return 1;
        if (h[i] > t[i]) return 0;
    }
    return 0;
}

__kernel void scrypt_scan(
    __global const uchar *prefix76,
    const uint start_nonce,
    __global const uchar *target,       /* 32B, share target (LE number) */
    __global const uchar *tele_target,  /* 32B, easier telemetry target */
    __global uint *V,                   /* scratch: 32*1024 uints per work item */
    __global uint *counters,            /* [0]=candidates, [1]=samples */
    __global uint *cand_nonces,         /* MAX_CANDIDATES */
    __global uint *samp_nonces,         /* MAX_SAMPLES */
    __global uchar *samp_hashes         /* MAX_SAMPLES * 32 */
) {
    uint gid = get_global_id(0);
    uint nonce = start_nonce + gid;

    /* 80-byte header = prefix76 + nonce (LE) */
    uchar header[80];
    for (int i = 0; i < 76; i++) header[i] = prefix76[i];
    header[76] = (uchar)(nonce);
    header[77] = (uchar)(nonce >> 8);
    header[78] = (uchar)(nonce >> 16);
    header[79] = (uchar)(nonce >> 24);

    /* HMAC key = SHA256(header) since 80 > blocksize 64 */
    uchar key[32];
    sha256(header, 80, key);
    uchar ipad[64], opad[64];
    for (int i = 0; i < 32; i++) { ipad[i] = key[i] ^ 0x36; opad[i] = key[i] ^ 0x5c; }
    for (int i = 32; i < 64; i++) { ipad[i] = 0x36; opad[i] = 0x5c; }

    /* PBKDF2(P=header, S=header, c=1, dkLen=128) -> B */
    uchar B[128];
    uchar msg[84];
    for (int i = 0; i < 80; i++) msg[i] = header[i];
    for (uint blk = 1; blk <= 4; blk++) {
        msg[80] = (uchar)(blk >> 24); msg[81] = (uchar)(blk >> 16);
        msg[82] = (uchar)(blk >> 8);  msg[83] = (uchar)(blk);
        hmac_sha256(ipad, opad, msg, 84, B + (blk-1)*32);
    }

    /* bytes -> 32 LE uints */
    uint X[32];
    for (int i = 0; i < 32; i++)
        X[i] = ((uint)B[i*4]) | ((uint)B[i*4+1]<<8) | ((uint)B[i*4+2]<<16) | ((uint)B[i*4+3]<<24);

    /* ROMix, N=1024 */
    __global uint *v = V + (ulong)gid * 32u * 1024u;
    for (uint i = 0; i < 1024; i++) {
        __global uint *vi = v + i*32u;
        for (int k = 0; k < 32; k++) vi[k] = X[k];
        blockmix(X);
    }
    for (uint i = 0; i < 1024; i++) {
        uint j = X[16] & 1023u;
        __global uint *vj = v + j*32u;
        for (int k = 0; k < 32; k++) X[k] ^= vj[k];
        blockmix(X);
    }

    /* LE uints -> bytes */
    uchar Xb[128];
    for (int i = 0; i < 32; i++) {
        Xb[i*4]   = (uchar)(X[i]);
        Xb[i*4+1] = (uchar)(X[i] >> 8);
        Xb[i*4+2] = (uchar)(X[i] >> 16);
        Xb[i*4+3] = (uchar)(X[i] >> 24);
    }

    /* PBKDF2(P=header, S=Xb, c=1, dkLen=32) -> final hash */
    uchar msg2[132];
    for (int i = 0; i < 128; i++) msg2[i] = Xb[i];
    msg2[128] = 0; msg2[129] = 0; msg2[130] = 0; msg2[131] = 1;
    uchar hash[32];
    hmac_sha256(ipad, opad, msg2, 132, hash);

    if (hash_lt_target(hash, target)) {
        uint slot = atomic_inc(&counters[0]);
        if (slot < %(MAX_CANDIDATES)d) cand_nonces[slot] = nonce;
    }
    if (hash_lt_target(hash, tele_target)) {
        uint slot = atomic_inc(&counters[1]);
        if (slot < %(MAX_SAMPLES)d) {
            samp_nonces[slot] = nonce;
            for (int k = 0; k < 32; k++) samp_hashes[slot*32 + k] = hash[k];
        }
    }
}
""" % {"MAX_CANDIDATES": MAX_CANDIDATES, "MAX_SAMPLES": MAX_SAMPLES}


def _cpu_scrypt(header: bytes) -> bytes:
    return hashlib.scrypt(header, salt=header, n=1024, r=1, p=1, maxmem=0, dklen=32)


def _target_to_le_bytes(target: int) -> bytes:
    return int(min(max(target, 0), (1 << 256) - 1)).to_bytes(32, "little")


class ScryptGPU:
    """OpenCL scrypt scanner. Call init() then self_test() before scan()."""

    def __init__(self, batch_size: Optional[int] = None):
        self.cl = None
        self.ctx = None
        self.queue = None
        self.program = None
        self.kernel = None
        self.device = None
        self.batch_size = batch_size or int(os.environ.get("DOGE_GPU_BATCH", "0") or 0)
        self._bufs = None
        self.last_scan_seconds = 0.0

    def init(self) -> List[str]:
        """Pick an OpenCL GPU device, build the kernel. Returns device names ([] on failure)."""
        try:
            import pyopencl as cl
        except ImportError:
            return []
        self.cl = cl
        try:
            devices = []
            for plat in cl.get_platforms():
                for dev in plat.get_devices(device_type=cl.device_type.GPU):
                    devices.append(dev)
            if not devices:
                return []
            # prefer discrete GPUs (no host-unified memory) over integrated graphics;
            # compute-unit counts are not comparable across vendors
            pick = int(os.environ.get("DOGE_GPU_DEVICE", "-1"))
            if 0 <= pick < len(devices):
                self.device = devices[pick]
            else:
                def score(d):
                    try:
                        discrete = 0 if d.host_unified_memory else 1
                    except Exception:
                        discrete = 0
                    return (discrete, d.global_mem_size)
                self.device = max(devices, key=score)
            self.ctx = cl.Context([self.device])
            self.queue = cl.CommandQueue(self.ctx)
            self.program = cl.Program(self.ctx, KERNEL_SOURCE).build()
            self.kernel = cl.Kernel(self.program, "scrypt_scan")
            if not self.batch_size:
                # 128 KB scratch per work item; keep V under ~1/6 of device memory, cap 8192
                mem_limit = int(self.device.global_mem_size // 6 // (128 * 1024))
                self.batch_size = max(256, min(8192, mem_limit))
            self._alloc(self.batch_size)
            return [self.device.name.strip()]
        except Exception:
            self.release()
            return []

    def _alloc(self, batch: int):
        cl = self.cl
        mf = cl.mem_flags
        self._bufs = {
            "prefix": cl.Buffer(self.ctx, mf.READ_ONLY, 76),
            "target": cl.Buffer(self.ctx, mf.READ_ONLY, 32),
            "tele_target": cl.Buffer(self.ctx, mf.READ_ONLY, 32),
            "V": cl.Buffer(self.ctx, mf.READ_WRITE, batch * 128 * 1024),
            "counters": cl.Buffer(self.ctx, mf.READ_WRITE, 8),
            "cand_nonces": cl.Buffer(self.ctx, mf.READ_WRITE, MAX_CANDIDATES * 4),
            "samp_nonces": cl.Buffer(self.ctx, mf.READ_WRITE, MAX_SAMPLES * 4),
            "samp_hashes": cl.Buffer(self.ctx, mf.READ_WRITE, MAX_SAMPLES * 32),
        }

    def scan_raw(self, prefix76: bytes, start_nonce: int, count: int,
                 target: int, tele_target: int):
        """Run one kernel batch. Returns (count, candidate_nonces, samples[(nonce, hash)])."""
        import numpy as np
        cl = self.cl
        count = min(count, self.batch_size)
        b = self._bufs
        t0 = time.time()
        cl.enqueue_copy(self.queue, b["prefix"], prefix76[:76])
        cl.enqueue_copy(self.queue, b["target"], _target_to_le_bytes(target))
        cl.enqueue_copy(self.queue, b["tele_target"], _target_to_le_bytes(tele_target))
        cl.enqueue_copy(self.queue, b["counters"], np.zeros(2, dtype=np.uint32))
        self.kernel.set_args(
            b["prefix"], np.uint32(start_nonce & 0xFFFFFFFF),
            b["target"], b["tele_target"], b["V"], b["counters"],
            b["cand_nonces"], b["samp_nonces"], b["samp_hashes"],
        )
        cl.enqueue_nd_range_kernel(self.queue, self.kernel, (count,), None)
        counters = np.empty(2, dtype=np.uint32)
        cl.enqueue_copy(self.queue, counters, b["counters"])
        n_cand = int(min(counters[0], MAX_CANDIDATES))
        n_samp = int(min(counters[1], MAX_SAMPLES))
        cand = []
        samples = []
        if n_cand:
            arr = np.empty(MAX_CANDIDATES, dtype=np.uint32)
            cl.enqueue_copy(self.queue, arr, b["cand_nonces"])
            cand = [int(x) for x in arr[:n_cand]]
        if n_samp:
            arr = np.empty(MAX_SAMPLES, dtype=np.uint32)
            hs = np.empty(MAX_SAMPLES * 32, dtype=np.uint8)
            cl.enqueue_copy(self.queue, arr, b["samp_nonces"])
            cl.enqueue_copy(self.queue, hs, b["samp_hashes"])
            samples = [(int(arr[i]), bytes(hs[i*32:(i+1)*32])) for i in range(n_samp)]
        self.queue.finish()
        self.last_scan_seconds = time.time() - t0
        return count, cand, samples

    def scan(self, prefix76: bytes, start_nonce: int, target: int):
        """High-level scan of one batch.
        Returns (hashes_done, verified_candidates[(nonce, hash)], samples[(nonce, hash)]).
        Every candidate is re-verified on the CPU with hashlib.scrypt — a buggy kernel
        can never submit a bad share."""
        tele_target = min(target << 14, (1 << 256) - 1)
        count, cand_nonces, samples = self.scan_raw(
            prefix76, start_nonce, self.batch_size, target, tele_target)
        verified = []
        for n in cand_nonces:
            header = prefix76 + struct.pack("<I", n & 0xFFFFFFFF)
            h = _cpu_scrypt(header)
            if int.from_bytes(h, "little") < target:
                verified.append((n, h))
        return count, verified, samples

    def self_test(self, vectors: int = 8) -> bool:
        """Kernel output must match hashlib.scrypt on random headers. Uses the telemetry
        path with an always-pass target so full hashes come back for comparison."""
        if self.program is None:
            return False
        try:
            import numpy as np  # noqa: F401  (required by scan_raw)
        except ImportError:
            return False
        try:
            rnd = os.urandom(76)
            n0 = 0xDEAD0000
            count, _, samples = self.scan_raw(rnd, n0, vectors,
                                              target=1,  # nothing passes the real target
                                              tele_target=(1 << 256) - 1)  # everything sampled
            if len(samples) != vectors:
                return False
            got = {n: h for n, h in samples}
            for i in range(vectors):
                nonce = (n0 + i) & 0xFFFFFFFF
                expect = _cpu_scrypt(rnd + struct.pack("<I", nonce))
                if got.get(nonce) != expect:
                    return False
            return True
        except Exception:
            return False

    def release(self):
        self._bufs = None
        self.kernel = None
        self.program = None
        self.queue = None
        self.ctx = None
        self.device = None
