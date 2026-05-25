#!/usr/bin/env python3
import argparse
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path


DEFAULT_CONFIG = "notifier_config.json"

# TSV columns shared with the NinjaTrader indicator. The notifier uses these names
# when parsing queue rows and when writing manual test alerts.
QUEUE_HEADER = [
    "id",
    "created_at",
    "connection_type",
    "strategy",
    "instrument",
    "account",
    "connection",
    "order_status",
    "previous_order_status",
    "price_status",
    "previous_price_status",
    "position",
    "position_quantity",
    "tracked_order",
    "native_error",
]


# Config and state helpers. State is written atomically so a crash or forced stop
# does not leave a half-written JSON file behind.
def load_json(path, default):
    file_path = Path(path)

    if not file_path.exists():
        return default

    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path, data):
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = file_path.with_suffix(file_path.suffix + ".tmp")

    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)

    temp_path.replace(file_path)


def resolve_value(value):
    if isinstance(value, str) and value.startswith("env:"):
        import os

        return os.environ.get(value[4:], "")

    return value


def resolve_config(value):
    if isinstance(value, dict):
        return {key: resolve_config(item) for key, item in value.items()}

    if isinstance(value, list):
        return [resolve_config(item) for item in value]

    return resolve_value(value)


# NinjaTrader writes TSV rows, with tabs/newlines escaped inside fields. These
# helpers keep the queue format readable while still allowing error messages to
# contain line breaks or other special characters.
def unescape_field(value):
    result = []
    index = 0

    while index < len(value):
        char = value[index]

        if char == "\\" and index + 1 < len(value):
            next_char = value[index + 1]

            if next_char == "t":
                result.append("\t")
            elif next_char == "r":
                result.append("\r")
            elif next_char == "n":
                result.append("\n")
            elif next_char == "\\":
                result.append("\\")
            else:
                result.append(next_char)

            index += 2
            continue

        result.append(char)
        index += 1

    return "".join(result)


def escape_field(value):
    return str(value or "").replace("\\", "\\\\").replace("\t", "\\t").replace("\r", "\\r").replace("\n", "\\n")


# Manual test mode: append a synthetic PRICE or ORDER loss event to the same queue
# file NT8 uses. If an older queue exists without a header, repair it before adding
# the new row so future reads are unambiguous.
def append_test_alert(queue_file, connection_type):
    path = Path(queue_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text("\t".join(QUEUE_HEADER) + "\n", encoding="utf-8")
    else:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

        if lines and lines[0].split("\t") != QUEUE_HEADER:
            path.write_text("\t".join(QUEUE_HEADER) + "\n" + "\n".join(lines) + "\n", encoding="utf-8")

    now = datetime.now()
    row = {
        "id": f"debug-{now:%Y%m%d%H%M%S%f}-{connection_type}",
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "connection_type": connection_type,
        "strategy": "Manual debug alert",
        "instrument": "Debug instrument",
        "account": "Debug account",
        "connection": "Manual test",
        "order_status": "Connected",
        "previous_order_status": "Connected",
        "price_status": "ConnectionLost" if connection_type == "PRICE" else "Connected",
        "previous_price_status": "Connected",
        "position": "Unknown",
        "position_quantity": "0",
        "tracked_order": "Manual queue test, no real NT8 order",
        "native_error": "Created by nt8_connection_notifier.py --write-test-alert",
    }

    if connection_type == "ORDER":
        row["order_status"] = "ConnectionLost"

    with path.open("a", encoding="utf-8") as handle:
        handle.write("\t".join(escape_field(row[column]) for column in QUEUE_HEADER) + "\n")

    print(f"Wrote {connection_type} test alert to {path}")


# Read all queued alerts. Newer queues include a header row, but the reader also
# accepts older/headerless files by applying QUEUE_HEADER to every row.
def read_alerts(queue_file):
    path = Path(queue_file)

    if not path.exists():
        return []

    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    if not lines:
        return []

    first_row = lines[0].split("\t")

    if first_row == QUEUE_HEADER:
        header = first_row
        data_lines = lines[1:]
    else:
        header = QUEUE_HEADER
        data_lines = lines

    alerts = []

    for line in data_lines:
        if not line.strip():
            continue

        parts = [unescape_field(part) for part in line.split("\t")]

        if len(parts) != len(header):
            print(f"Skipping malformed alert line: {line}")
            continue

        alerts.append(dict(zip(header, parts)))

    return alerts


# Human-readable alert body posted to Healthchecks. Healthchecks stores this body
# with the ping, which makes the alert detail visible in the check history.
def build_message(alert):
    return (
        f"NT8 {alert.get('connection_type', 'UNKNOWN')} connection lost\n"
        f"Time: {alert.get('created_at', '')}\n"
        f"Source: {alert.get('strategy', '')}\n"
        f"Instrument: {alert.get('instrument', '')}\n"
        f"Account: {alert.get('account', '')}\n"
        f"Connection: {alert.get('connection', '')}\n"
        f"Order status: {alert.get('order_status', '')} "
        f"(previous: {alert.get('previous_order_status', '')})\n"
        f"Price status: {alert.get('price_status', '')} "
        f"(previous: {alert.get('previous_price_status', '')})\n"
        f"Position: {alert.get('position', '')}, quantity: {alert.get('position_quantity', '')}\n"
        f"Tracked order: {alert.get('tracked_order', '')}\n"
        f"Native error: {alert.get('native_error', '')}"
    )


# Connection-loss alerts go to alert_ping_url. By default, they are sent to the
# Healthchecks /fail endpoint so the dedicated alert check turns red immediately.
def send_healthchecks_connection_alerts(healthchecks_config, alert):
    ping_url = healthchecks_config.get("alert_ping_url", "").rstrip("/")

    if not ping_url:
        raise ValueError("Healthchecks channel is enabled but ping_url is missing")

    if healthchecks_config.get("send_failure", True) and not ping_url.endswith("/fail"):
        ping_url = ping_url + "/fail"

    body = build_message(alert).encode("utf-8")
    timeout = int(healthchecks_config.get("timeout_seconds", 20))
    request = urllib.request.Request(ping_url, data=body, method="POST")

    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_body = response.read().decode("utf-8", errors="replace")

    if response.status not in (200, 201):
        raise RuntimeError(f"Healthchecks returned HTTP {response.status}: {response_body}")


# Heartbeats go to ping_url and prove the Python notifier itself is still running.
# This is separate from connection-loss events, which use alert_ping_url above.
def send_healthchecks_pc_heartbeat(healthchecks_config):
    ping_url = healthchecks_config.get("ping_url", "").rstrip("/")

    if not ping_url:
        raise ValueError("Healthchecks heartbeat is enabled but ping_url is missing")

    timeout = int(healthchecks_config.get("timeout_seconds", 20))
    request = urllib.request.Request(ping_url, data=b"nt8 notifier heartbeat", method="POST")

    with urllib.request.urlopen(request, timeout=timeout) as response:
        response_body = response.read().decode("utf-8", errors="replace")

    if response.status not in (200, 201):
        raise RuntimeError(f"Healthchecks heartbeat returned HTTP {response.status}: {response_body}")


# Normalize state loaded from disk. sent.healthchecks prevents duplicate delivery
# for old queue rows, while last_healthchecks_heartbeat rate-limits heartbeat pings.
def ensure_state_shape(state):
    state.setdefault("sent", {})
    state["sent"].setdefault("healthchecks", [])
    state.setdefault("last_healthchecks_heartbeat", 0)
    return state


# Send every unsent alert currently in the queue. Failed sends are left out of the
# sent list, so the next loop/run retries them after network or service recovery.
def process_alerts(config, state):
    queue_file = config["queue_file"]
    alerts = read_alerts(queue_file)
    sent = state["sent"]["healthchecks"]
    healthchecks_config = config.get("healthchecks", {})

    if not healthchecks_config.get("enabled", False):
        print("Healthchecks is disabled.")
        return

    for alert in alerts:
        alert_id = alert.get("id")

        if not alert_id:
            continue

        if alert_id in sent:
            continue

        try:
            send_healthchecks_connection_alerts(healthchecks_config, alert)
        except Exception as exc:
            print(f"Healthchecks failed for alert {alert_id}: {exc}")
            continue

        sent.append(alert_id)
        print(f"Healthchecks sent for alert {alert_id}")


# Optional heartbeat check. This can alert if the PC, internet connection, or this
# Python process stops, even when NT8 has not written a connection-loss event.
def process_heartbeat(config, state):
    healthchecks_config = config.get("healthchecks", {})

    if not healthchecks_config.get("enabled", False):
        return

    if not healthchecks_config.get("send_heartbeat", False):
        return

    interval = int(healthchecks_config.get("heartbeat_seconds", 60))
    now = time.time()

    if now < float(state.get("last_healthchecks_heartbeat", 0)) + interval:
        return

    try:
        send_healthchecks_pc_heartbeat(healthchecks_config)
    except Exception as exc:
        print(f"Healthchecks heartbeat failed: {exc}")
        return

    state["last_healthchecks_heartbeat"] = now
    print(f"Healthchecks heartbeat sent at {datetime.fromtimestamp(now).strftime('%Y-%m-%d %H:%M:%S')}")


# CLI entry point. In normal mode the script polls forever; --once is useful for
# tests and scheduled runs, and --write-test-alert creates a synthetic queue event.
def main():
    parser = argparse.ArgumentParser(description="Send NT8 connection alerts from a file queue.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Path to notifier_config.json")
    parser.add_argument("--once", action="store_true", help="Process the queue once and exit")
    parser.add_argument(
        "--write-test-alert",
        choices=("PRICE", "ORDER"),
        help="Append a manual test alert to the configured queue file and exit",
    )
    args = parser.parse_args()

    config = resolve_config(load_json(args.config, {}))

    if "queue_file" not in config:
        raise SystemExit("Config must define queue_file")

    if args.write_test_alert:
        append_test_alert(config["queue_file"], args.write_test_alert)
        return

    state_file = config.get("state_file", str(Path(args.config).with_name("notifier_state.json")))
    poll_seconds = int(config.get("poll_seconds", 10))
    state = ensure_state_shape(load_json(state_file, {}))

    while True:
        process_alerts(config, state)
        process_heartbeat(config, state)
        save_json(state_file, state)

        if args.once:
            break

        time.sleep(poll_seconds)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Exiting on user request")
