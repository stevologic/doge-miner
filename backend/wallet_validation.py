"""Single source of truth for Dogecoin wallet validation.

Real Base58Check validation (stdlib only): format regex as a fast pre-filter,
then full base58 decode + version byte + double-SHA256 checksum verification.
Accepts mainnet P2PKH ('D', version 0x1E) and P2SH ('9'/'A', version 0x16).
"""

import hashlib
import re

_DOGE_RE = re.compile(r"^[D9A][1-9A-HJ-NP-Za-km-z]{25,34}$")

_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58_ALPHABET)}

# Dogecoin mainnet version bytes
P2PKH_VERSION = 0x1E  # addresses starting with 'D'
P2SH_VERSION = 0x16   # addresses starting with '9' or 'A'


def b58decode(s: str) -> bytes:
    """Decode a base58 string to bytes (raises ValueError on invalid chars)."""
    num = 0
    for c in s:
        if c not in _B58_INDEX:
            raise ValueError(f"invalid base58 character: {c!r}")
        num = num * 58 + _B58_INDEX[c]
    # each leading '1' encodes a leading zero byte
    n_zeros = len(s) - len(s.lstrip("1"))
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    return b"\x00" * n_zeros + body


def b58encode(b: bytes) -> str:
    """Encode bytes as base58 (used by tests to build known-valid addresses)."""
    n_zeros = len(b) - len(b.lstrip(b"\x00"))
    num = int.from_bytes(b, "big")
    out = ""
    while num:
        num, rem = divmod(num, 58)
        out = _B58_ALPHABET[rem] + out
    return "1" * n_zeros + out


def b58check_encode(version: int, payload: bytes) -> str:
    """Build a Base58Check string from a version byte + payload (e.g. hash160)."""
    raw = bytes([version]) + payload
    checksum = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[:4]
    return b58encode(raw + checksum)


def is_valid_doge_wallet(w: str) -> bool:
    """True iff w is a checksum-valid mainnet Dogecoin address (P2PKH 'D' or P2SH '9'/'A')."""
    if not w:
        return False
    w = w.strip()
    if not _DOGE_RE.match(w):
        return False
    try:
        raw = b58decode(w)
    except ValueError:
        return False
    if len(raw) != 25:
        return False
    version, payload, checksum = raw[0], raw[:-4], raw[-4:]
    if version not in (P2PKH_VERSION, P2SH_VERSION):
        return False
    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    return checksum == expected
