from unittest.mock import patch

import autotune.sync_lnd_channels as sync

MOCK_CHANNELS = [
    {
        "channel_point": "123:1",
        "scid": "1",
        "chan_id": "111",
        "capacity": "10000",
        "local_balance": "6000",
        "remote_balance": "4000",
        "remote_pubkey": "peerA",
    },
    {
        "channel_point": "124:1",
        "scid": "2",
        "chan_id": "112",
        "capacity": "20000",
        "local_balance": "7000",
        "remote_balance": "13000",
        "remote_pubkey": "peerB",
    },
]


def test_select_channel_fields_returns_expected_fields():
    chan = MOCK_CHANNELS[0]
    result = sync.select_channel_fields(chan)
    assert result == {
        "channel_point": "123:1",
        "scid": "1",
        "chan_id": "111",
        "capacity": "10000",
        "local_balance": "6000",
        "remote_balance": "4000",
    }


def test_get_peer_channels_filters_by_node_id():
    chans = MOCK_CHANNELS
    # Only peerA
    res = sync.get_peer_channels(chans, "peerA")
    assert len(res) == 1
    assert res[0]["channel_point"] == "123:1"
    # Only peerB
    res = sync.get_peer_channels(chans, "peerB")
    assert len(res) == 1
    assert res[0]["channel_point"] == "124:1"
    # Not present
    res = sync.get_peer_channels(chans, "peerX")
    assert res == []


@patch("autotune.sync_lnd_channels.subprocess.check_output")
def test_get_all_channels_returns_channels_on_success(mock_check_output):
    output = '{"channels": [{"channel_point": "x"}]}'
    mock_check_output.return_value = output
    chans = sync.get_all_channels(lnd_container_name="test")
    assert chans == [{"channel_point": "x"}]
    mock_check_output.assert_called_once()


@patch("autotune.sync_lnd_channels.subprocess.check_output")
def test_get_all_channels_returns_empty_list_on_error(mock_check_output):
    mock_check_output.side_effect = Exception("fail")
    chans = sync.get_all_channels(lnd_container_name="test")
    assert chans == []


def test_merge_channels_merges_updates_and_tombstones():
    existing = [
        {"channel_point": "c1", "local_balance": "500", "remote_balance": "500"},
        {"channel_point": "c2", "local_balance": "0", "remote_balance": "0"},
    ]
    current = [
        {"channel_point": "c1", "local_balance": "900", "remote_balance": "100"},
        {"channel_point": "c3", "local_balance": "300", "remote_balance": "700"},
    ]
    merged = sync.merge_channels(existing, current)
    points = {c["channel_point"]: c for c in merged}
    # c1 updated, c2 tombstoned, c3 added
    assert points["c1"]["local_balance"] == "900"
    assert points["c1"]["active"] is True
    assert points["c2"]["active"] is False
    assert points["c2"]["local_balance"] == "0"
    assert points["c3"]["active"] is True


def test_aggregate_peer_stats_correct():
    channels = [
        {"capacity": "100", "local_balance": "30", "remote_balance": "70"},
        {"capacity": "50", "local_balance": "25", "remote_balance": "25"},
    ]
    stats = sync.aggregate_peer_stats(channels)
    assert stats == {
        "peer_total_capacity": 150,
        "peer_total_local": 55,
        "peer_total_remote": 95,
        "peer_outbound_percent": 55 / 150,
    }


def test_update_all_states_with_channel_info_merges_and_updates(monkeypatch):
    # Setup
    all_states = {
        "section1": {
            "node_id": "peerA",
            "channels": [
                {"channel_point": "123:1", "local_balance": "0", "remote_balance": "0"}
            ],
        }
    }
    all_channels = MOCK_CHANNELS

    # Policy mock: channels dict with node_ids
    class Dummy(dict):
        def __getitem__(self, key):
            return dict.__getitem__(self, key)

    policy = Dummy()
    policy["channels"] = {"section1": {"node_id": "peerA"}}
    updated = sync.update_all_states_with_channel_info(all_states, all_channels, policy)
    # Should merge and update balances/stats
    st = updated["section1"]
    assert "peer_total_capacity" in st
    assert "channels" in st
    # Outbound percent matches expected
    assert st["peer_outbound_percent"] == 0.6


def test_update_all_states_with_channel_info_skips_no_node_id():
    all_states = {"section": {}}
    all_channels = MOCK_CHANNELS
    policy = {"channels": {}}
    updated = sync.update_all_states_with_channel_info(all_states, all_channels, policy)
    assert updated == all_states  # unchanged
