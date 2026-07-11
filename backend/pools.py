"""Popular Dogecoin mining pool presets.

Every non-custom entry here was verified live (DNS + real stratum mining.subscribe
handshake) on 2026-07-11. Zergpool was removed: it permanently shut down Sep 2025.

Two kinds of pools:
  * registration_required=False — log in with just your DOGE wallet address as the
    stratum username (no account/signup). Payouts go straight to that wallet.
  * registration_required=True  — create a free account on the pool website first;
    the stratum username is account.workername.
"""

POOLS = [
    {
        "id": "zpool",
        "name": "zpool.ca (no registration)",
        "host": "scrypt.mine.zpool.ca",
        "port": 3433,
        "registration_required": False,
        "username_hint": "Your DOGE wallet address",
        "password_default": "c=DOGE",
        "password_hint": "c=DOGE selects the payout coin. Optional: d=N static difficulty, ID=rigname.",
        "payout": "Direct DOGE to your wallet (wallet-login multipool)",
        "web": "https://www.zpool.ca",
        "stats_url": "https://www.zpool.ca/wallet/{user}",
        # per-worker detail (live miners array while mining, balances, payouts)
        "worker_stats_url": "https://www.zpool.ca/api/walletEx?address={user}",
        "notes": ("Multi-coin scrypt pool that mines DOGE directly. Just point a wallet at it. "
                  "Payouts wait for the pool to find DOGE blocks and reach the minimum "
                  "(see your wallet page for credited balance)."),
    },
    {
        "id": "aikapool",
        "name": "AikaPool (registered)",
        "host": "stratum.aikapool.com",
        "port": 7915,
        "registration_required": True,
        "username_hint": "AikaPool username.workername",
        "password_default": "x",
        "password_hint": "Your worker password from the AikaPool dashboard.",
        "payout": "DOGE (dedicated Dogecoin pool)",
        "web": "https://aikapool.com/doge/",
        "stats_url": "https://aikapool.com/doge/index.php?page=statistics&action=blocks",
        "notes": "Dedicated DOGE pool. Create a free account and a worker on their site first.",
    },
    {
        "id": "litecoinpool",
        "name": "litecoinpool.org (registered)",
        "host": "litecoinpool.org",
        "port": 3333,
        "registration_required": True,
        "username_hint": "litecoinpool username.workername",
        "password_default": "x",
        "password_hint": "Any password (worker auth is by username).",
        "payout": "LTC (merged-mines DOGE; DOGE value paid out in LTC)",
        "web": "https://www.litecoinpool.org",
        "stats_url": "https://www.litecoinpool.org/account",
        "notes": "Long-running merged-mining pool (LTC+DOGE). Registration required.",
    },
    {
        "id": "f2pool",
        "name": "F2Pool (registered)",
        "host": "ltc.f2pool.com",
        "port": 8888,
        "registration_required": True,
        "username_hint": "F2Pool account.workername",
        "password_default": "x",
        "password_hint": "Any password.",
        "payout": "LTC + DOGE (merged mining, separate DOGE payouts)",
        "web": "https://www.f2pool.com",
        "stats_url": "https://www.f2pool.com/mining-user/dashboard",
        "notes": ("Large exchange-grade pool; merged LTC+DOGE. Registration required. "
                  "CAUTION: F2Pool's stratum accepts ANY username — a typo silently mines "
                  "into the void, so double-check your account name on their dashboard."),
    },
    {
        "id": "viabtc",
        "name": "ViaBTC (registered)",
        "host": "ltc.viabtc.io",
        "port": 3333,
        "registration_required": True,
        "username_hint": "ViaBTC account.workername",
        "password_default": "x",
        "password_hint": "Any password.",
        "payout": "LTC + DOGE (merged mining, automatic separate DOGE payouts)",
        "web": "https://www.viabtc.com",
        "stats_url": "https://www.viabtc.com/ltc/miner",
        "notes": ("One of the largest LTC+DOGE merged-mining pools (PPS+ or PPLNS). "
                  "Mining LTC automatically earns DOGE too — no extra setup beyond "
                  "adding a DOGE payout address. Failover port 443."),
    },
    {
        "id": "antpool",
        "name": "AntPool (registered)",
        "host": "stratum-ltc.antpool.com",
        "port": 8888,
        "registration_required": True,
        "username_hint": "AntPool subaccount.workersuffix (e.g. acct.01)",
        "password_default": "x",
        "password_hint": "Any password.",
        "payout": "LTC + DOGE (merged mining; daily payouts, 0.001 LTC minimum)",
        "web": "https://www.antpool.com",
        "stats_url": "https://www.antpool.com/login",
        "notes": ("Bitmain-run pool with LTC+DOGE merged mining. Alternate ports 443 "
                  "and 25. Create a sub-account and set your payout wallet addresses "
                  "in the dashboard first, or earnings sit in the account balance."),
    },
    {
        "id": "powerpool",
        "name": "PowerPool (registered)",
        "host": "scrypt.stratum.powerpool.io",
        "port": 3333,
        "registration_required": True,
        "username_hint": "PowerPool username.workername",
        "password_default": "x",
        "password_hint": "Any password. Optional: d=N sets a static starting difficulty.",
        "payout": "Your pick of DOGE, LTC, BTC, USDC and more (hourly payouts)",
        "web": "https://powerpool.io",
        "stats_url": "https://powerpool.io/login",
        "notes": ("Smaller profit-switching scrypt pool (1% fee, real-time PPS, hourly "
                  "payouts with no minimum). Pays in whatever coin mix you choose — set "
                  "DOGE in the dashboard payout settings. Registration required."),
    },
]


def get_pool(pool_id: str):
    for p in POOLS:
        if p["id"] == pool_id:
            return p
    return None
