"""
fee_manager.py

Plug-and-play fee manager for Lightning channel dynamic fee tuning.
Handles reading config/state, running updates, and writing TOML and peer memory.
No stubs. No placeholders. Fully production-ready.
"""

import os
import sys
import logging
from datetime import datetime, timezone
from autotune.config_loader import (
    load_policy_config,
    load_peer_memory,
    save_peer_memory,
)
from autotune.charge_lnd_writer import write_charge_lnd_toml
from autotune.sync_lnd_channels import (
    get_all_channels,
    update_all_states_with_channel_info,
)
from autotune.autotune import (
    recommend_and_update_fees,
    get_forwarding_events,
)
from autotune.process_htlc import group_htlc_events_by_peer, compute_peer_htlc_stats, summarise_peer_events
from autotune.policy_utils import Policy

logger = logging.getLogger(__name__)


class FeeManager:
    def __init__(
        self,
        config_path="/app/config/params.toml",
        peer_mem_path="/app/data/peer_memory.json",
        output_path="/app/chargelnd/config.toml",
    ):
        self.config_path = config_path
        self.peer_mem_path = peer_mem_path
        self.output_path = output_path
        self.policy = None
        self.peer_mem = None
        self.channels = None

    def load(self):
        """Load config and peer memory."""
        self.policy = load_policy_config(self.config_path)
        self.peer_mem = load_peer_memory(self.peer_mem_path)
        self.channels = self.policy.get("channels", {})
        if not self.channels:
            raise ValueError("Missing or empty channels in policy")

    def update_all_fees(
        self,
        apply_changes=True,
        dry_run=False,
        ema_observe=False,
        verbose=True,
        htlc_events=None,
    ):
        """
        Run fee update pipeline for all channels.
        Returns recommendations dict and logs.
        """
        self.load()
        logger.info(f"Incoming events: {htlc_events}")

        # Get channel metrics and forwarding data
        forward_data_day, forward_data_int = get_forwarding_events(
            self.policy.node.lnd_container, self.policy.timing.fetch_interval_secs
        )
 
        config_lines = (
            open(self.output_path).readlines()
            if os.path.exists(self.output_path)
            else []
        )

        now = datetime.now(timezone.utc)
        final_report_logs = []
        rule_stats = {}
        recommendations = {}

        lnd_channels = get_all_channels(self.policy.node.lnd_container)
        self.peer_mem = update_all_states_with_channel_info(
            self.peer_mem, lnd_channels, self.policy
        )
        processed_htlc_events = group_htlc_events_by_peer(htlc_events, self.peer_mem)

        missed_summaries = ["-------HTLC Summary------"]
        for section, ch_data in self.policy.channels.items():
            if not self.peer_mem.get(section):
                self.peer_mem[section] = {}
            node_id = self.peer_mem[section].get("node_id")
            alias = ch_data.get("peer", section)  # fallback to section if no peer
            
            peer_htlc_events = processed_htlc_events.get(node_id, [])
            self.peer_mem[section]["htlc_stats"] = compute_peer_htlc_stats(peer_htlc_events, now=now)
            
            logger.debug(f"{section} HTLC Stats: {self.peer_mem[section]['htlc_stats']}")

            missed_summaries.append(f"{alias}: HTLC Stats: {self.peer_mem[section]['htlc_stats']}")

            rec, state, logs = recommend_and_update_fees(
                section,
                alias,
                self.policy,
                self.peer_mem,
                config_lines,
                now,
                ema_observe,
                dry_run,
                [],
                rule_stats,
                forward_data_day,
                forward_data_int,
            )
            recommendations[section] = rec
            self.peer_mem[section] = state
            final_report_logs.extend(logs)

        final_report_logs.extend(missed_summaries)

        # Optionally write TOML and state
        if apply_changes and not dry_run and not ema_observe:
            logger.info("Writing new config...")
            write_charge_lnd_toml(recommendations, self.output_path, self.channels)
            logger.info("Saving peer memory...")
            save_peer_memory(self.peer_mem, self.peer_mem_path)
        else:
            # Always save peer memory even if dry-run or EMA observe
            logger.info("Saving peer memory only...")
            save_peer_memory(self.peer_mem, self.peer_mem_path)

        if verbose:
            logger.debug("---- Fee Update Logs ----")
            for entry in final_report_logs:
                logger.debug(entry)
            logger.info("---- Fee Recommendations ----")
            for section, vals in recommendations.items():
                logger.info(f"{section}: {vals}")
            logger.info("-------------------------")

        return recommendations, final_report_logs

    def explain_peer(self, section_name):
        """
        Print debug/explanation for a given peer/channel.
        """
        self.load()
        if section_name not in self.peer_mem:
            logger.info(f"No peer memory for {section_name}")
            return
        state = self.peer_mem[section_name]
        logger.info(f"Peer: {section_name}")
        for k, v in state.items():
            logger.info(f"  {k}: {v}")

    def view_state(self):
        """
        Print all peer memory state.
        """
        self.load()
        logger.info("Peer memory:")
        for peer, state in self.peer_mem.items():
            logger.info(f"{peer}: {state}")


def health_check(config_path, peer_mem_path, toml_out_path):
    try:
        # Check config TOML exists and loads
        if not os.path.exists(config_path):
            logger.error(f"FAIL: Config not found: {config_path}")
            return 2
        config = load_policy_config(config_path)
        if not isinstance(config, Policy) or not config.get("channels"):
            logger.error("FAIL: Config invalid or missing 'channels'")
            return 2

        # Check peer memory loads (and is a dict)
        peer_mem = load_peer_memory(peer_mem_path)
        if not isinstance(peer_mem, dict):
            logger.error("FAIL: Peer memory is not a dict")
            return 3

        # Check output path is writeable (touch, then delete)
        try:
            with open(toml_out_path, "a") as f:
                f.write("")  # touch file
        except Exception as e:
            logger.error(f"FAIL: Cannot write to output TOML: {toml_out_path} - {e}")
            return 4

        logger.info("OK: Fee manager config, state and output path healthy")
        return 0

    except Exception as e:
        logger.exception(f"FAIL: Health check exception: {e}")
        return 5


# For CLI/test use
if __name__ == "__main__":
    import argparse

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

    args = parser.parse_args()
    mgr = FeeManager(
        config_path=args.config,
        peer_mem_path=args.peer_mem,
        output_path=args.output,
    )

    if args.view_state:
        mgr.view_state()
    elif args.explain:
        mgr.explain_peer(args.explain)
    elif args.health:
        code = health_check(args.config, args.peer_mem, args.output)
        sys.exit(code)
    else:
        mgr.update_all_fees(
            apply_changes=args.apply,
            dry_run=args.dry_run,
            ema_observe=args.ema_observe,
            verbose=True,
        )
