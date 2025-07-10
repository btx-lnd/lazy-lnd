import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# Constants
# Constants
WINDOWS = 7 * 24 * 2  # 336 windows of 30mins  each for 7 days
INTERVAL_SECS = 24 * 60 * 60  # 1 day
FREQUENCY = 30 * 60
NOW = int(datetime.now(timezone.utc).timestamp())
STATE_FILE = "state.json"

CHANNELS = {
    "coingate": "coingate",
    "clearmoney": "clearmoney",
    "acinq": "acinq",
    "kraken": "kraken",
    "nodemcnodyface": "nodemcnodyface",
    "node_boat": "node on the boat",
    "centralwank": "centralwank",
    "crazy": "crazy",
    "edelweiss": "edelweiss",
    "jayhawkpleb": "jayhawkpleb",
    "1sats.com": "1sats",
    "boltz_cln": "boltz",
    "authenticity": "authenticity",
}

ALPHA_1D = 0.4
ALPHA_5D = 0.15
ALPHA_7D = 0.1


def get_events(start, end):
    cmd = [
        "docker",
        "exec",
        "lnd",
        "lncli",
        "fwdinghistory",
        f"--start_time={start}",
        f"--end_time={end}",
        "--max_events=50000",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(result.stdout).get("forwarding_events", [])
    except:
        return []


def apply_ema(current, value, alpha):
    return current + alpha * (value - current)


def build_ema():
    state = defaultdict(
        lambda: {
            "ema_1d": 0,
            "ema_5d": 0,
            "ema_7d": 0,
            "revenue_ema_1d": 0,
            "revenue_ema_5d": 0,
            "revenue_ema_7d": 0,
        }
    )

    for i in reversed(range(WINDOWS)):
        start = NOW - (i + 1) * FREQUENCY
        end = start + INTERVAL_SECS
        print(
            f"Fetching from {start} to {end} ({datetime.fromtimestamp(start, timezone.utc)} -> {datetime.fromtimestamp(end, timezone.utc)})"
        )
        events = get_events(start, end)

        # Aggregate volume + fees per alias
        stats = defaultdict(lambda: {"vol": 0, "fee": 0})
        for e in events:
            alias = e.get("peer_alias_out", "").lower()
            for section, frag in CHANNELS.items():
                if frag.lower() in alias:
                    stats[section]["vol"] += int(e.get("amt_out", 0))
                    stats[section]["fee"] += int(e.get("fee", 0))

        # Apply smoothing
        for section, data in stats.items():
            s = state[section]
            v, f = data["vol"], data["fee"]
            s["ema_1d"] = apply_ema(s["ema_1d"], v, ALPHA_1D)
            s["ema_5d"] = apply_ema(s["ema_5d"], v, ALPHA_5D)
            s["ema_7d"] = apply_ema(s["ema_7d"], v, ALPHA_7D)
            s["revenue_ema_1d"] = apply_ema(s["revenue_ema_1d"], f, ALPHA_1D)
            s["revenue_ema_5d"] = apply_ema(s["revenue_ema_5d"], f, ALPHA_5D)
            s["revenue_ema_7d"] = apply_ema(s["revenue_ema_7d"], f, ALPHA_7D)

        time.sleep(0)  # avoid hitting LND too fast

    # Finalize state output
    final_state = {}
    for section, s in state.items():
        final_state[section] = {
            "ema_1d": int(s["ema_1d"]),
            "ema_5d": int(s["ema_5d"]),
            "ema_7d": int(s["ema_7d"]),
            "revenue_ema_1d": int(s["revenue_ema_1d"]),
            "revenue_ema_5d": int(s["revenue_ema_5d"]),
            "revenue_ema_7d": int(s["revenue_ema_7d"]),
            "fee": 25,
            "cooldown_until": datetime.now(timezone.utc).isoformat(),
            "zero_ema_count": 0,
            "htlc_stuck_count": 0,
        }

    with open(STATE_FILE, "w") as f:
        json.dump(final_state, f, indent=2)

    print(f"\n✅ EMA state bootstrapped from past 7 days: {STATE_FILE}")


if __name__ == "__main__":
    build_ema()
