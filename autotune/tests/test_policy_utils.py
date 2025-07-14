import pytest
from autotune.policy_utils import Policy, enforce_policy


def test_policy_attr_and_dict_access():
    p = Policy({"a": 1, "b": {"c": 2, "d": {"e": 3}}})
    # Dict access
    assert p["a"] == 1
    assert p["b"]["c"] == 2
    # Attr access
    assert p.a == 1
    assert p.b.c == 2
    # Nested Policy auto-wrap
    assert isinstance(p.b, Policy)
    assert isinstance(p.b.d, Policy)
    assert p.b.d.e == 3


def test_policy_get_and_to_dict():
    p = Policy({"x": 42, "nest": {"y": "z"}})
    assert p.get("x") == 42
    assert p.get("nest").y == "z"
    assert p.get("missing", 99) == 99
    as_dict = p.to_dict()
    assert as_dict == {"x": 42, "nest": {"y": "z"}}


def test_policy_items_keys_values_and_iter_len_repr():
    data = {"foo": 1, "bar": 2}
    p = Policy(data)
    assert set(p.items()) == set(data.items())
    assert set(p.keys()) == set(data.keys())
    assert set(p.values()) == set(data.values())
    assert list(iter(p)) == list(data)
    assert len(p) == 2
    assert "foo" in repr(p)


def test_policy_missing_key_raises():
    p = Policy({})
    with pytest.raises(AttributeError):
        _ = p.missing
    # __getitem__ should raise KeyError
    with pytest.raises(KeyError):
        _ = p["missing"]


def test_enforce_policy_min_max_inbound(monkeypatch):
    # Mocked policy with min/max/inbound in channels[section]
    pol = Policy(
        {
            "channels": {
                "sec1": {
                    "min_range_ppm": 111,
                    "max_range_ppm": 222,
                    "inbound_fee_ppm": 333,
                }
            }
        }
    )
    new_fees = {"min_fee_ppm": 0, "max_fee_ppm": 500, "inbound_fee_ppm": 0}
    state = {"fee": 999, "min_fee": 0, "max_fee": 0, "inbound_fee": 0}
    out_fees, out_state = enforce_policy("sec1", dict(new_fees), dict(state), pol)
    # All clamped to policy
    assert out_fees["max_fee_ppm"] == 222
    assert out_fees["inbound_fee_ppm"] == 0
    assert out_state["fee"] == 222
    assert out_state["inbound_fee"] == 0


def test_enforce_policy_missing_channel_block():
    # Should not raise, defaults to 3000
    pol = Policy({"channels": {}})
    new_fees = {"min_fee_ppm": 0, "max_fee_ppm": 0, "inbound_fee_ppm": 0}
    state = {}
    out_fees, out_state = enforce_policy("notfound", dict(new_fees), dict(state), pol)
    assert out_fees["max_fee_ppm"] == 3000
