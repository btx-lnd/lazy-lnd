import io
import json
import tomli
import pytest

from autotune import config_loader


@pytest.fixture
def example_peer_mem():
    return {"chan1": {"fee": 100}, "chan2": {"fee": 50}}


@pytest.fixture
def tmp_peer_mem_file(tmp_path, example_peer_mem):
    path = tmp_path / "peer_mem.json"
    with open(path, "w") as f:
        json.dump(example_peer_mem, f)
    return path


def test_load_peer_memory_exists(tmp_peer_mem_file, example_peer_mem):
    mem = config_loader.load_peer_memory(tmp_peer_mem_file)
    assert mem == example_peer_mem


def test_load_peer_memory_missing(tmp_path):
    path = tmp_path / "missing.json"
    result = config_loader.load_peer_memory(path)
    assert result == {}


def test_save_peer_memory(tmp_path, example_peer_mem):
    path = tmp_path / "save_mem.json"
    config_loader.save_peer_memory(example_peer_mem, path)
    with open(path) as f:
        data = json.load(f)
    assert data == example_peer_mem


def test_load_policy_config_reads_toml(tmp_path):
    toml_content = """
[foo]
bar = "baz"
num = 123
"""
    path = tmp_path / "params.toml"
    with open(path, "wb") as f:
        f.write(toml_content.encode())
    result = config_loader.load_policy_config(path)
    assert result["foo"]["bar"] == "baz"
    assert result["foo"]["num"] == 123
