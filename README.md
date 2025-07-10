
# HamSandwich Lightning Fee Manager 🚦

Welcome to **HamSandwich's Lightning Autotuner**!  
This fee/routing engine dynamically optimizes Lightning Network channel fees using adaptive rules, peer stats, HTLC event telemetry, and (future) ML.  

**Caveat** : This was built mostly on an iPhone, and sometimes an iPad, using copeous amounts of ChatGPT, while navigating bedtimes and beer. Who knows what easter eggs it hides!!

---

## 🚀 Features

| Feature                       | Status    | Notes                                   |
|-------------------------------|-----------|-----------------------------------------|
| Adaptive outbound fee rules   | 🟡   | Responsive to volume, revenue, role, can runaway sometimes     |
| Positive & negative inbound   | 🟢    | Clamp, decay, sink/tap roles, auto logic|
| HTLC fail event analysis      | 🟡   | Events captured, not yet used        |
| NDJSON event logging          | 🟡 | Inconsistent structure           |
| Per-peer, per-channel config  | 🟢    | Custom min/max/role/range               |
| Multi-day EMA blending        | 🟢    | (1/5/7d), adaptive alpha                |
| Sink/tap role detection       | 🟢    | Automatic with role-flip handling       |
| Automated fee writes          | 🟡 | To charge-lnd today, planned direct to LND soon     |
| ML-model                      | 🔴   | Planned    |
| Web UI / dashboard            | 🔴    | Planned                                 |


---
## 🧾 Tech debt

- Lots of giant functions to break up. Thats ChatGPT for you!
- Unecessary use of BoS to obtain channel balances (as I didnt know about lncli before)
- Depends on charge-lnd to run separately.
- Uses docker socket to interact DIRECTLY with lncli running on your lnd container (shudder). Seriously who would do that...

---

## 🛠 Quickstart

1. **Edit `autotune/params.toml`**  
   This is your master config: channel mapping, limits, rules, smoothing, file paths.  
   See the [Params Config Guide](#params-config-guide) below.

2. **Run as Docker containers**  
   - One container runs the HTLC event capture (`htlc.py`).
   - One container runs the main fee manager (`main.py`) on your chosen schedule (default 30min).

   Example:
   
   ```bash
   docker build . -t autotune:v0.6
   docker compose -f docker-compose.dryrun.yml up -d
   ```

To just observe and gather EMA stats, update the docker compose to pass the flag `--ema-observe`.

3. **See logs and output**  
   - Fee config: `/app/chargelnd/config.toml`
   - State: `/app/data/peer_memory.json`
   - Logs: `/app/log/fee_adjust.log`, `/app/log/fee_changes.ndjson`

4. **charge-lnd**
   - Point charge-lnd at the config.toml 

---

## 📝 Params Config Guide

Each `[section]` is documented inline in the file. Here’s a starter template:

```toml
[paths]
# Files and logs
# - config_file: Path for the generated charge-lnd config (recommended fees output)
# - state_file: Persistent fee tuning state (EMAs, streaks, cooldowns)
# - log_file: Human-readable script run log (for audits)
# - fee_log_file: NDJSON (structured) fee/rule events
# - peer_mem: Per-channel extra state/memory
config_file = "/app/chargelnd/config.toml"
state_file = "/app/log/fee_adjust_state.json"
log_file = "/app/log/fee_adjust.log"
fee_log_file = "/app/log/fee_changes.ndjson"
peer_mem = "/app/data/peer_memory.json"

[ema]
# EMA smoothing factors for traffic/revenue
# - alpha_balanced_1d/5d/7d: Increase for faster response, decrease for steadier/smoother
# - alpha_weighted_*: Used after role flip, higher is more aggressive
alpha_balanced_1d = 0.25
alpha_balanced_5d = 0.08
alpha_balanced_7d = 0.07
alpha_weighted_1d = 0.4
alpha_weighted_5d = 0.05
alpha_weighted_7d = 0.03

[fees]
# Fee bumping logic
# - increment_ppm: Smallest allowed fee step. Higher = chunkier/jumpier. Lower = smoother.
# - min_ppm/max_ppm: Hard global min/max for all channels
# - bump_max: Max single fee jump. Raise to allow bigger moves.
# - min_max_ratio: Minimum allowed ratio for min_fee to max_fee
# - failed_htlc_bump: Extra bump if HTLC fails
increment_ppm = 25
min_ppm = 1
max_ppm = 2500
bump_max = 250
min_max_ratio = 0.5
failed_htlc_bump = 25

[htlc]
# HTLC and reserve margins
# - ratio: Higher means only higher liquidity channels are "active"
# - reserve_deduction: Higher = more conservative (keeps more back)
# - min_capacity: Ignore channels below this percent as inactive
# - failed_htlc_threshold: HTLC fails to trigger bump (lower = more sensitive)
ratio = 0.9
reserve_deduction = 0.0101
min_capacity = 0.05
failed_htlc_threshold = 3

[timing]
# Time and backoff controls
# - cooldown_hours: Min hours between fee changes (raise to reduce churn)
# - fee_backoff_hours: Wait after a failed fee bump
# - failed_bump_flag_hours: How long to mark as failed/cooldown
# - fetch_interval_secs: How often to run autotune (lower = more frequent)
cooldown_hours = 4
fee_backoff_hours = 24
failed_bump_flag_hours = 6
fetch_interval_secs = 1800
    
[node]
# Node/container info
# - lnd_container: Docker container name for docker exec
# - name: Lightning node alias
lnd_container = "lnd"
name = "HamSandwich"

[thresholds]
# Traffic and role thresholds
# - base_delta: % change in EMA/traffic to trigger rules (lower = more sensitive)
# - revenue: Minimum revenue change to act
# - role_ratio: Inbound/outbound ratio for role classification
# - role_flip_bonus: More negative = aggressive after flip
# - role_flip_days: How long after flip to apply bonus
# - mid_streak_min/max, early_streak_max: Fee bump streak thresholds for adaptation
# - zero_ema_count_threshold: After this many zero-EMAs, decay/penalize more
# - high_delta_bonus: Lowers threshold on big volume surges
# - min_delta/max_delta: Bounds for sensitivity
# - sink_ema_target: Target EMA for sink detection
# - htlc_forward_failures_raise: Number of upstream failed HTLC events before bumping fees
# - htlc_forward_failures_hold: Number of upstream failed HTLC events to hold fees
base_delta = 0.20
revenue = 0.20
role_ratio = 1.5
role_flip_bonus = 0.03
role_flip_days = 3
mid_streak_min = 6
mid_streak_max = 12
early_streak_max = 5
zero_ema_count_threshold = 24
high_delta_bonus = 0.02
mid_streak_bonus = 0.02
high_streak_bonus = 0.04
early_streak_penalty = 0.03
zero_ema_penalty = 0.02
min_delta = 0.03
max_delta = 0.20
high_ema_delta_threshold = 500000
high_rev_delta_threshold = 500
sink_ema_target = 250000
htlc_forward_failures_raise = 900
htlc_forward_failures_hold = 900

[alpha]
# EMA responsiveness tuning
# - balanced_*: Normal operation, higher = more sensitive, lower = sluggish
# - weighted_*: Used after role flip
# - fee_bump_*: If many bumps, slow down adaptation
# - zero_ema_*: If stuck, increase adaptation
balanced_1d = 0.15
balanced_5d = 0.10
balanced_7d = 0.07
weighted_1d = 0.4
weighted_5d = 0.05
weighted_7d = 0.03
role_flip_days = 3
min_role_flips = 2
zero_ema_trigger = 3
zero_ema_1d_boost = 0.2
zero_ema_5d_boost = 0.1
zero_ema_7d_boost = 0.05
zero_ema_max_1d = 0.6
zero_ema_max_5d = 0.2
zero_ema_max_7d = 0.1
fee_bump_streak_threshold = 3
fee_bump_decay_1d = 0.05
fee_bump_decay_5d = 0.02
fee_bump_decay_7d = 0.01
fee_bump_min_1d = 0.05
fee_bump_min_5d = 0.03
fee_bump_min_7d = 0.01

[rules]
# Peer-specific fee/inbound guard lists
# - exempt_from_sink_guard: Aliases never hit by sink-guard
# - inbound_fee_targets: Aliases allowed for inbound fee logic
exempt_from_sink_guard = []
inbound_fee_targets = []

[inbound_fees]
# Inbound fee range for adaptive rules
# - min_fee_ppm: Smallest inbound fee rule can set
# - max_fee_ppm: Largest inbound fee allowed
min_fee_ppm = 25
max_fee_ppm = 3500


# Per-channel fee range overrides (never go outside these)
# - peer: Alias for logs, display, matching
# - node_id: Peer node pubkey
# 
# Below are optional per channel
# - min_range_ppm: Hard lower bound
# - max_range_ppm: Hard upper bound
# - inbound_fee_ppm: Minimum positive inbound fee

[channels.acinq]
peer = "acinq"
node_id = "03864ef025fde8fb587d989186ce6a4a186895ee44a926bfc370e2c366597a3f8f"
min_range_ppm = 750
...

```

For every peer/channel, create a `[channels.X]` block.  
Optionally tweak `min_range_ppm`, `max_range_ppm` and `inbound_fee_ppm` for desired fee bounds. Or just let the script gradually/eventually figure it out


---

## ⚡ How it works

- Captures real-time HTLC events and state, not just fwding history
- Calculates optimal fees based on activity, streaks, stuck/failures, role, and bound, using weighted rules.
- Log everything as NDJSON for easy ML or dashboard integration
- Modular rules engine (easy to add more heuristics)
- When <10% capacity, Freeze outbound fees and set negative inbound == the min_fee_ppm

---

## 🧠 ML-Readiness

- All metrics, events, and changes are logged for future supervised/unsupervised learning
- Next steps: export features (time series, lagged signals, streaks, fail ratios, liquidity gradients) and plug into an ML pipeline, Also, learn ML so I can do this!

---

## 👤 About

Written and tuned by **🥪HamSandwich🥪**.  
For questions, tweaks, or tips...

- Telegram at https://t.me/HamSandwichLND 

---

## ❤️ Contributions

Issues, PRs, and new rules/strategies welcome.  
See `autotune/rule_engine.py` for how to add a rule.

Want to show your appreciation?

- Zap me at zap@ln.stx.ie
- Open a channel with my node! Find it at https://amboss.space/c/btx


