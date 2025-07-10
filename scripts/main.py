import argparse
import sys
import time
import logging
import json
from autotune.fee_manager import FeeManager, health_check
from drivers.buffer_htlc import append_to_ndjson, load_recent_events, prune_ndjson_buffer

logging.basicConfig(
    stream=sys.stdout,
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_event(line):
    try:
        event = json.loads(line)
        ts = int(event.get("timestamp_ns", 0)) // 1_000_000_000 or int(time.time())
        event["_event_time"] = ts
        logger.debug(f"Event captured: {event}")
        return event
    except Exception:
        return None


def key_from_event(event):
    """HTLCs can be matched by incoming_channel_id+incoming_htlc_id."""
    return (
        event.get("incoming_channel_id"),
        event.get("incoming_htlc_id"),
    )


def match_and_buffer_events(log_file, expiry_secs, interval_secs, mgr, fee_kwargs):
    pending = {}  # (chan_id, htlc_id) -> event
    matched_batch = []
    last_batch = time.time()
    fail_types = {"FORWARD_FAIL", "FINAL"}
    fail_keys = {"forward_fail_event", "link_fail_event", "failure"}
    fail_keys_not_paired = {"link_fail_event"}

    first_run = True

    logger.info(f"Opening log: {log_file}")
    with open(log_file, "r") as f:
        f.seek(0, 2)  # Seek to end
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.2)
                continue
            event = parse_event(line)
            if not event:
                continue

            k = key_from_event(event)
            event_type = event.get("event_type", "")

            # If FORWARD, buffer as pending
            if event_type == "FORWARD" and event.get("forward_event"):
                pending[k] = event
            # If FORWARD_FAIL or FINAL, match and build batch record. UPSTREAM failure
            elif event_type in fail_types or any(key in event for key in fail_keys):
                fwd = pending.pop(k, None)
                if fwd:
                    matched = {
                        "fwd": fwd,
                        "result": event,
                        "ts": event["_event_time"],
                    }
                    append_to_ndjson(matched)
                    logger.debug(f"Match: {matched}")
                
                # SOLO matches are local failures 
                elif any(key in event for key in fail_keys_not_paired):
                    solo = {
                        "fwd": event,
                        "result": {},
                        "ts": event["_event_time"],
                    }
                    append_to_ndjson(solo)
                    logger.debug(f"Solo Match: {solo}")
            # Optionally: keep unmatched pending only for expiry_secs
            now = int(time.time())
            success_batch = []
            for pk in list(pending.keys()):
                if now - pending[pk]["_event_time"] > expiry_secs:
                    fwd_event = pending.pop(pk)
                    success = {
                        "fwd": fwd_event,
                        "result": {"event_type": "SUCCESS"},
                        "ts": fwd_event["_event_time"],
                    }
                    append_to_ndjson(success)
            
            # On each interval: process batch, then clear it
            if (now - last_batch > interval_secs) or first_run:
                recent_events = load_recent_events()
                fee_kwargs["htlc_events"] = recent_events
                prune_ndjson_buffer()
                logger.info(f"Failed HTLC events processed: {len(matched_batch)}") 
                mgr.update_all_fees(**fee_kwargs)
                last_batch = now
                first_run = False


def main():
    parser = argparse.ArgumentParser(
        description="Fee Manager: Dynamic Fee Engine for Lightning"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write charge-lnd config and peer mem"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show recommendations, do not write config",
    )
    parser.add_argument(
        "--ema-observe", action="store_true", help="Record EMA only, no config changes"
    )
    parser.add_argument(
        "--explain", type=str, help="Explain logic/state for peer/channel"
    )
    parser.add_argument(
        "--health", action="store_true", help="Run health check and exit"
    )
    parser.add_argument("--view-state", action="store_true", help="Print peer memory")
    parser.add_argument(
        "--config",
        type=str,
        default="/app/config/params.toml",
        help="Policy config TOML",
    )
    parser.add_argument(
        "--peer-mem",
        type=str,
        default="/app/data/peer_memory.json",
        help="Peer memory JSON",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="/app/chargelnd/config.toml",
        help="Output TOML for charge-lnd",
    )
    parser.add_argument(
        "--htlc-log",
        type=str,
        default="/app/log/htlc.ndjson",
        help="HTLC event log file to follow",
    )
    parser.add_argument(
        "--interval-mins", type=int, default=30, help="Fee update interval in minutes"
    )
    parser.add_argument(
        "--expiry-mins", type=int, default=15, help="HTLC event expiry window (minutes)"
    )

    args = parser.parse_args()
    mgr = FeeManager(
        config_path=args.config,
        peer_mem_path=args.peer_mem,
        output_path=args.output,
    )

    fee_kwargs = dict(
        apply_changes=args.apply,
        dry_run=args.dry_run,
        ema_observe=args.ema_observe,
        verbose=True,
    )

    # One-shot actions
    if args.view_state:
        mgr.view_state()
    elif args.explain:
        mgr.explain_peer(args.explain)
    elif args.health:
        code = health_check(args.config, args.peer_mem, args.output)
        sys.exit(code)
    elif args.dry_run or args.ema_observe:
        mgr.update_all_fees(**fee_kwargs)
    else:
        match_and_buffer_events(
            log_file=args.htlc_log,
            expiry_secs=args.expiry_mins * 60,
            interval_secs=args.interval_mins * 60,
            mgr=mgr,
            fee_kwargs=fee_kwargs,
        )


if __name__ == "__main__":
    main()
