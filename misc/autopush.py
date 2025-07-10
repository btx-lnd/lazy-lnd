#!/usr/bin/python3
import subprocess
import yaml
import json
import time
import fcntl
import os
import sys

LOCKFILE = "/tmp/autopush.lock"

# --- CONFIGURATION ---
NODE_A = "HamSandwich"  # The destination node (creates invoice)
NODE_B = "Saruman"  # The source node (pays invoice)

# The core alias of the channel peer. The script will find any alias containing this string.
PEER_ALIAS_FRAGMENT = ""

# The amount of liquidity to pull in this run.
MIN_AMOUNT_TO_PULL_SATS = 100000

# Safety check: only run if the source channel has at least this much to send.
MINIMUM_SOURCE_BALANCE_SATS = 25000

COMMAND_TIMEOUT_S = 30

# --- UTILITY AND EXECUTION FUNCTIONS ---


def acquire_lock():
    global lockfile
    lockfile = open(LOCKFILE, "w")
    try:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Another instance is already running. Exiting.")
        sys.exit(1)


def run_command(command, check=True):
    """Runs a shell command with a timeout, returns stdout or None on failure."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=check,
            shell=True,
            timeout=COMMAND_TIMEOUT_S,
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        print(f"   -! TIMEOUT: Command '{command}' took too long to execute.")
        return None
    except subprocess.CalledProcessError as e:
        print(f"   -! COMMAND FAILED: {command}\n   -! Stderr: {e.stderr.strip()}")
        return None


def get_outbound_liquidity(node_name, peer_alias_fragment):
    """Gets outbound liquidity for a channel using robust parsing."""
    print(
        f"-> Checking source liquidity on '{node_name}' with peer containing '{peer_alias_fragment}'..."
    )
    raw_peers = run_command(f"bos peers --node {node_name} --no-color")
    if not raw_peers:
        return 0

    for line in raw_peers.splitlines():
        clean_line = line.replace("â\x94\x82", "│")
        if "│" not in clean_line:
            continue

        parts = clean_line.split("│")
        if len(parts) >= 5:
            try:
                current_alias = parts[1].strip()
                if peer_alias_fragment in current_alias:
                    outbound_btc = float(parts[4].strip() or "0.0")
                    sats = int(outbound_btc * 100_000_000)
                    return sats
            except (ValueError, IndexError):
                continue

    print(f"   -! Could not find a peer containing alias '{peer_alias_fragment}'.")
    return 0


def parse_invoice(invoice_output):
    """Parses YAML output from `bos invoice`."""
    if not invoice_output:
        return None
    try:
        invoice_data = yaml.safe_load(invoice_output)
        if isinstance(invoice_data.get("request"), dict):
            return invoice_data.get("request", {}).get("request")
        return invoice_data.get("request")
    except (yaml.YAMLError, AttributeError):
        return None


# --- MAIN EXECUTION LOGIC ---
def main():
    print(
        f"--- Starting Targeted Pull from '{PEER_ALIAS_FRAGMENT}' at {time.ctime()} ---"
    )

    source_balance = get_outbound_liquidity(NODE_B, PEER_ALIAS_FRAGMENT)
    source_balance = source_balance - MINIMUM_SOURCE_BALANCE_SATS
    if source_balance < MIN_AMOUNT_TO_PULL_SATS:
        print(
            f"-> Source channel balance ({source_balance} sats) is below minimum required ({MIN_AMOUNT_TO_PULL_SATS} sats). Exiting."
        )
        return
    print(f"   - Source channel has sufficient balance: {source_balance} sats.")

    print(f"-> Creating {source_balance} sat invoice on {NODE_A} with hints...")
    invoice_cmd = f"bos invoice {source_balance} --node {NODE_A} --include-hints"
    invoice_output = run_command(invoice_cmd)
    invoice_request = parse_invoice(invoice_output)

    if not invoice_request:
        print("   -! Failed to create hinted invoice. Aborting.")
        return

    print(f"-> Requesting {NODE_B} to pay the invoice...")
    # Add --out flag to force payment via the correct channel on Saruman
    success = run_command(
        f"bos pay {invoice_request} --node {NODE_B} --max-fee 10 --out '{PEER_ALIAS_FRAGMENT}'"
    )

    if success:
        print("\n--- Targeted Pull Successful! ---")
    else:
        print("\n--- Targeted Pull Failed. ---")


if __name__ == "__main__":
    acquire_lock()
    try:
        import yaml
    except ImportError:
        print("Error: PyYAML not found. Please run 'pip install PyYAML'")
        exit(1)

    try:
        subprocess.run("command -v jq", shell=True, check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(
            "Error: jq is not installed. Please install it with 'sudo apt-get install jq'"
        )
        exit(1)

    main()
