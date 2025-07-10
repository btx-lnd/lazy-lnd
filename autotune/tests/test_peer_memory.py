import pytest
from autotune.peer_memory import update_peer_memory


def test_merges_new_into_prev():
    prev = {"fee": 100, "role": "tap", "ema": 55}
    new = {"fee": 110, "ema": 80}
    out = update_peer_memory("alias", prev, new)
    assert out == {"fee": 110, "role": "tap", "ema": 80}


def test_prev_state_is_not_modified():
    prev = {"fee": 100, "role": "tap"}
    new = {"fee": 200}
    _ = update_peer_memory("alias", prev, new)
    assert prev == {"fee": 100, "role": "tap"}  # Original is not mutated


def test_new_keys_added():
    prev = {"fee": 100}
    new = {"inbound_fee": 50}
    out = update_peer_memory("alias", prev, new)
    assert out == {"fee": 100, "inbound_fee": 50}


def test_empty_prev_state():
    prev = {}
    new = {"fee": 10}
    out = update_peer_memory("alias", prev, new)
    assert out == {"fee": 10}


def test_empty_new_state():
    prev = {"fee": 100, "role": "tap"}
    new = {}
    out = update_peer_memory("alias", prev, new)
    assert out == {"fee": 100, "role": "tap"}


def test_both_empty():
    out = update_peer_memory("alias", {}, {})
    assert out == {}
