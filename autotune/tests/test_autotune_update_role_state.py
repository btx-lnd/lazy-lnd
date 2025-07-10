import pytest
from datetime import datetime, timedelta
from autotune import update_role_state

def test_first_set_role_initializes_history():
    section = {}
    result = update_role_state(section, "sink")
    assert result["role"] == "sink"
    assert isinstance(result["role_flips"], list)
    assert result["days_since_flip"] == 0
    assert "last_updated" in result
    assert result["role_flips"][-1]["role"] == "sink"

def test_role_flip_increments_history_and_resets_days():
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    section = {
        "role": "tap",
        "role_flips": [{"timestamp": yesterday, "role": "tap"}],
        "days_since_flip": 3,
        "last_updated": yesterday,
    }
    result = update_role_state(section, "sink")
    assert result["role"] == "sink"
    assert result["role_flips"][-1]["role"] == "sink"
    assert result["days_since_flip"] == 0
    assert result["last_updated"] == datetime.utcnow().strftime("%Y-%m-%d")

def test_no_role_flip_increments_days_since_flip():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    section = {
        "role": "sink",
        "role_flips": [{"timestamp": today, "role": "sink"}],
        "days_since_flip": 2,
        "last_updated": today,
    }
    # Simulate a new day by changing 'last_updated'
    old = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    section["last_updated"] = old
    result = update_role_state(section, "sink")
    assert result["days_since_flip"] == 3
    assert result["role"] == "sink"

def test_no_role_flip_same_day_does_not_increment():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    section = {
        "role": "balanced",
        "role_flips": [{"timestamp": today, "role": "balanced"}],
        "days_since_flip": 5,
        "last_updated": today,
    }
    result = update_role_state(section, "balanced")
    assert result["days_since_flip"] == 5
    assert result["role"] == "balanced"

def test_role_flips_preserved():
    section = {}
    for new_role in ["tap", "sink", "balanced"]:
        section = update_role_state(section, new_role)
    assert [r["role"] for r in section["role_flips"]] == ["tap", "sink", "balanced"]

def test_no_role_flip_does_not_duplicate_flip():
    today = datetime.utcnow().strftime("%Y-%m-%d")
    section = {
        "role": "sink",
        "role_flips": [{"timestamp": today, "role": "sink"}],
        "days_since_flip": 7,
        "last_updated": today,
    }
    flips_before = list(section["role_flips"])
    result = update_role_state(section, "sink")
    # No new role flip entry should be added
    assert result["role_flips"] == flips_before

def test_init_without_role_flips_key():
    section = {"role": "sink"}
    result = update_role_state(section, "tap")
    assert "role_flips" in result
    assert result["role_flips"][-1]["role"] == "tap"
    assert result["days_since_flip"] == 0
