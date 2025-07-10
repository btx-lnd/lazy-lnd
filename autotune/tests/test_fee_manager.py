import unittest
from unittest.mock import patch, mock_open
import importlib
import sys

from autotune.policy_utils import Policy


def reload_fee_manager():
    if "fee_manager" in sys.modules:
        del sys.modules["fee_manager"]
    return importlib.import_module("autotune.fee_manager")


def mock_policy_with_minimal_required_keys(**overrides):
    base = {
        "channels": {"chan1": {"peer": "Alias1", "node_id": "id1"}},
        "node": {"lnd_container": "testcontainer", "name": "testnode"},
        "timing": {
            "fetch_interval_secs": 10,
            "cooldown_days": 2,
            "min_age_days": 1,
            "max_age_days": 365,
            "observe_mode_refresh_secs": 3600,
            "cooldown_hours": 6,
        },
        "thresholds": {
            "revenue": 0.2,
            "sink_ema_target": 10,
            "role_ratio": 0.5,
            "base_delta": 0.2,
            "role_flip_days": 3,
            "high_ema_delta_threshold": 500000,
            "high_rev_delta_threshold": 500,
            "role_flip_bonus": 0.1,
            "high_delta_bonus": 0.1,
            "mid_streak_min": 3,
            "mid_streak_max": 6,
            "mid_streak_bonus": 0.05,
            "high_streak_bonus": 0.1,
            "early_streak_max": 5,
            "early_streak_penalty": 0.03,
            "zero_ema_count_threshold": 24,
            "min_delta": 0.03,
            "max_delta": 0.5,
        },
        "alpha": {
            "role_flip_days": 3,
            "balanced_1d": 0.35,
            "balanced_5d": 0.14,
            "balanced_7d": 0.09,
            "weighted_1d": 0.5,
            "weighted_5d": 0.23,
            "weighted_7d": 0.14,
            "rev_1d": 0.35,
            "rev_5d": 0.14,
            "rev_7d": 0.09,
            "failed_htlc": 0.5,
            "observe": 0.04,
            "min_role_flips": 2,
            "zero_ema_trigger": 1,
            "fee_bump_streak_threshold": 3,
        },
        "htlc": {
            "ratio": 0.9,
            "reserve_deduction": 0.0101,
            "min_capacity": 0.05,
            "failed_htlc_threshold": 3,
            "max_size_override_ppm": 0.7,
            "min_htlc_msat": 1000,
        },
        "fees": {
            "min_ppm": 0,
            "max_ppm": 2500,
            "increment_ppm": 25,
            "bump_max": 25,
            "min_max_ratio": 0.5,
            "failed_htlc_bump": 25,
            "max_fee_for_low_delta": 500,
            "max_fee_for_sink": 1500,
            "default_inbound_fee": 1000,
        },
        "paths": {
            "config_file": "/tmp/config.toml",
            "state_file": "/tmp/state.json",
            "fee_log_file": "/tmp/fee_log.ndjson",
            "log_file": "/tmp/script.log",
            "lockfile": "/tmp/lockfile",
        },
        "sync": {
            "sync_nodes": [],
            "command_timeout_secs": 5,
            "prefer_aliases": True,
        },
        "logging": {
            "log_level": "INFO",
            "ndjson_enabled": True,
        },
        "rules": {"exempt_from_sink_guard": [], "inbound_fee_targets": []},
    }
    # Allow test-specific overrides (e.g., empty channels)
    for k, v in overrides.items():
        base[k] = v
    from autotune.policy_utils import Policy

    return Policy(base)


class TestFeeManager(unittest.TestCase):
    @patch(
        "autotune.fee_manager.load_policy_config",
        return_value=mock_policy_with_minimal_required_keys(),
    )
    @patch("autotune.fee_manager.load_peer_memory", return_value={})
    def test_load_ok(self, mock_load_peer_memory, mock_load_policy):
        from autotune.fee_manager import FeeManager

        mgr = FeeManager()
        mgr.load()
        self.assertIn("chan1", mgr.policy.channels)
        self.assertEqual(
            mgr.policy.channels["chan1"], {"peer": "Alias1", "node_id": "id1"}
        )

    @patch(
        "autotune.fee_manager.load_policy_config",
        return_value=mock_policy_with_minimal_required_keys(channels={}),
    )
    @patch("autotune.fee_manager.load_peer_memory", return_value={})
    def test_load_missing_channels_raises(
        self, mock_load_peer_memory, mock_load_policy
    ):
        from autotune.fee_manager import FeeManager

        mgr = FeeManager()
        with self.assertRaises(ValueError):
            mgr.load()

    @patch(
        "autotune.autotune.get_forwarding_events", return_value=("fwd_day", "fwd_int")
    )
    @patch("autotune.autotune.get_peers", return_value="raw_peers")
    @patch(
        "autotune.autotune.recommend_and_update_fees",
        return_value=(
            {"chan1": {"foo": "bar"}},
            {"some_state": 1},
            ["log: Alias1 something happened"],
        ),
    )
    @patch("autotune.fee_manager.write_charge_lnd_toml")
    @patch("autotune.fee_manager.save_peer_memory")
    @patch("builtins.open", new_callable=mock_open, read_data="")
    @patch("os.path.exists", return_value=True)
    @patch(
        "autotune.fee_manager.load_policy_config",
        return_value=mock_policy_with_minimal_required_keys(),
    )
    @patch("autotune.fee_manager.load_peer_memory", return_value={})
    def test_update_all_fees_apply(
        self,
        mock_load_peer_memory,
        mock_load_policy,
        mock_exists,
        mock_open_,
        mock_save,
        mock_write,
        mock_rec,
        mock_getpeers,
        mock_getfwds,
    ):
        from autotune.fee_manager import FeeManager

        mock_rec.return_value = (
            {"chan1": {"foo": "bar"}},
            {"some_state": 1},
            ["log: Alias1 something happened"],
        )
        mgr = FeeManager()
        recs, logs = mgr.update_all_fees(
            apply_changes=True, dry_run=False, verbose=False
        )
        mock_write.assert_called_once()
        mock_save.assert_called_once()
        self.assertIn("chan1", recs)
        # Just test logs is a list and last log is a string
        self.assertIsInstance(logs, list)
        self.assertIsInstance(logs[-1], str)

    @patch(
        "autotune.fee_manager.load_policy_config",
        return_value=mock_policy_with_minimal_required_keys(),
    )
    @patch(
        "autotune.fee_manager.load_peer_memory", return_value={"chan1": {"foo": "bar"}}
    )
    def test_explain_peer_runs(self, mock_load_peer_memory, mock_load_policy):
        from autotune.fee_manager import FeeManager

        mgr = FeeManager()
        try:
            mgr.explain_peer("chan1")
        except Exception as e:
            self.fail(f"explain_peer raised {e}")

    @patch(
        "autotune.fee_manager.load_policy_config",
        return_value=mock_policy_with_minimal_required_keys(),
    )
    @patch(
        "autotune.fee_manager.load_peer_memory", return_value={"chan1": {"foo": "bar"}}
    )
    def test_view_state_runs(self, mock_load_peer_memory, mock_load_policy):
        from autotune.fee_manager import FeeManager

        mgr = FeeManager()
        try:
            mgr.view_state()
        except Exception as e:
            self.fail(f"view_state raised {e}")

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open)
    @patch(
        "autotune.fee_manager.load_policy_config",
        return_value=mock_policy_with_minimal_required_keys(),
    )
    @patch("autotune.fee_manager.load_peer_memory", return_value="not_a_dict")
    def test_health_check_peer_mem_not_dict(
        self, mock_load_peer, mock_load_policy, mock_open_, mock_exists
    ):
        from autotune.fee_manager import health_check

        code = health_check("c", "peer", "out")
        self.assertEqual(code, 3)

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", side_effect=OSError("NOPE"))
    @patch(
        "autotune.fee_manager.load_policy_config",
        return_value=mock_policy_with_minimal_required_keys(),
    )
    @patch("autotune.fee_manager.load_peer_memory", return_value={})
    def test_health_check_cannot_write_output(
        self, mock_load_peer, mock_load_policy, mock_open_, mock_exists
    ):
        from autotune.fee_manager import health_check

        code = health_check("c", "peer", "out")
        self.assertEqual(code, 4)

    @patch("os.path.exists", return_value=True)
    @patch("builtins.open", new_callable=mock_open)
    @patch(
        "autotune.fee_manager.load_policy_config",
        return_value=mock_policy_with_minimal_required_keys(),
    )
    @patch("autotune.fee_manager.load_peer_memory", return_value={})
    def test_health_check_ok(
        self, mock_load_peer, mock_load_policy, mock_open_, mock_exists
    ):
        from autotune.fee_manager import health_check

        code = health_check("c", "peer", "out")
        self.assertEqual(code, 0)

    if __name__ == "__main__":
        unittest.main()
