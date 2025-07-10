import os
import json
import time

NDJSON_BUFFER_PATH = "/app/data/htlc_event_buffer.ndjson"
MAX_WINDOW_SECS = 86400  # 24 hours

def append_to_ndjson(event, path=NDJSON_BUFFER_PATH):
    # Append events as lines
    with open(path, "a") as f:
        f.write(json.dumps(event) + "\n")

def load_recent_events(path=NDJSON_BUFFER_PATH, max_age=MAX_WINDOW_SECS):
    now = int(time.time())
    events = []
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        for line in f:
            try:
                event = json.loads(line)
                if event.get("ts", 0) >= now - max_age:
                    events.append(event)
            except Exception:
                continue
    return events

def prune_ndjson_buffer(path=NDJSON_BUFFER_PATH, max_age=MAX_WINDOW_SECS):
    now = int(time.time())
    tmp_path = path + ".tmp"
    count = 0
    if not os.path.exists(path):
        with open(path, "w") as f:
            pass  # create empty file
        return 0 
    with open(path, "r") as inp, open(tmp_path, "w") as out:
        for line in inp:
            try:
                event = json.loads(line)
                if event.get("ts", 0) >= now - max_age:
                    out.write(json.dumps(event) + "\n")
                    count += 1
            except Exception:
                continue
    os.replace(tmp_path, path)
    return count
 