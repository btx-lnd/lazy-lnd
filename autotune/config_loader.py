import tomli
import json
import os
import shutil

from autotune.policy_utils import Policy

BACKUP_COUNT = 1000 # Keep N old backups

def validate_peer_memory(peer_mem):
    # Minimal: ensure dict and required keys
    if not isinstance(peer_mem, dict):
        raise ValueError("Peer memory is not a dict.")
    # Add more validation if needed, e.g. keys, types
    return True


def save_peer_memory(peer_mem, path):
    validate_peer_memory(peer_mem)
    tmp_path = f"{path}.tmp"

    # Write to temp file first
    with open(tmp_path, "w") as f:
        json.dump(peer_mem, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    # Validate file can be read back
    with open(tmp_path, "r") as f:
        loaded = json.load(f)
        validate_peer_memory(loaded)

    # Rolling backups
    for i in range(BACKUP_COUNT, 0, -1):
        old = f"{path}.{i}"
        older = f"{path}.{i-1}" if i > 1 else path
        if os.path.exists(older):
            shutil.copy2(older, old)

    # Move tmp to final atomically
    os.replace(tmp_path, path)


def load_peer_memory(path):
    for i in range(0, BACKUP_COUNT + 1):
        check = path if i == 0 else f"{path}.{i}"
        try:
            with open(check, "r") as f:
                peer_mem = json.load(f)
                validate_peer_memory(peer_mem)
                return peer_mem
        except Exception:
            continue
    # None found
    return {}

def load_policy_config(path):
    with open(path, "rb") as f:
        return Policy(tomli.load(f))
