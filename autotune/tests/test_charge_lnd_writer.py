import pytest
from autotune.charge_lnd_writer import write_charge_lnd_toml


def test_write_charge_lnd_toml_basic(tmp_path):
    recommendations = {
        "chanA": {
            "min_range_ppm": 10,
            "max_range_ppm": 20,
            "inbound_range_ppm": 5,
            "max_htlc_msat": 12345,
        },
        "chanB": {
            "min_range_ppm": 15,
            "max_range_ppm": 30,
            "inbound_range_ppm": 0,
            "max_htlc_msat": 54321,
        },
    }
    # New channels dict includes node.id for only chanA
    channels = {
        "chanA": {"node_id": "abcdef123"},
        "chanB": {"peer": "other"},  # no node.id: should error
    }
    out_path = tmp_path / "test_charge-lnd.toml"

    # Should raise for missing node.id in chanB
    with pytest.raises(KeyError):
        write_charge_lnd_toml(recommendations, out_path, channels)

    # Remove chanB to verify success
    write_charge_lnd_toml({"chanA": recommendations["chanA"]}, out_path, channels)
    text = out_path.read_text()
    assert "[chanA]" in text
    assert "min_range_ppm = 10" in text
    assert "max_range_ppm = 20" in text
    assert "inbound_range_ppm = 5" in text
    assert "max_htlc_msat = 12345" in text
    assert "node.id = abcdef123" in text


def test_write_charge_lnd_toml_handles_strings(tmp_path):
    recommendations = {
        "chanX": {
            "min_range_ppm": 1,
            "foo": "barbaz",
        }
    }
    channels = {"chanX": {"node_id": "id-x"}}
    out_path = tmp_path / "out.toml"
    write_charge_lnd_toml(recommendations, out_path, channels)
    text = out_path.read_text()
    assert "foo = barbaz" in text or 'foo = "barbaz"' in text
    assert "node.id = id-x" in text


def test_write_charge_lnd_toml_creates_file(tmp_path):
    out_path = tmp_path / "foo.toml"
    recommendations = {"chanY": {"min_range_ppm": 3}}
    channels = {"chanY": {"node_id": "test-id"}}
    write_charge_lnd_toml(recommendations, out_path, channels)
    assert out_path.exists()
    assert "[chanY]" in out_path.read_text()
    assert "node.id = test-id" in out_path.read_text()
