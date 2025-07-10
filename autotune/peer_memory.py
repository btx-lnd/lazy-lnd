def update_peer_memory(section, prev_state, new_state):
    # You can extend this to preserve/restore richer per-peer metadata
    merged = prev_state.copy()
    merged.update(new_state)
    return merged
