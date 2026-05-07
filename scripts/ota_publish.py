#!/usr/bin/env python3
"""Phase 3 — OTA publisher CLI.

Builds a signed config payload and publishes it to one of:
  campus/b01/ota/config              (broadcast, all 200 rooms)
  campus/b01/f##/ota/config          (one floor)
  campus/b01/f##/r###/ota/config     (one room)

Examples:
  python scripts/ota_publish.py --scope broadcast --version 2 --alpha 0.02 --beta 0.25
  python scripts/ota_publish.py --scope floor --floor 5 --version 2 --alpha 0.02
  python scripts/ota_publish.py --scope room  --floor 5 --room 520 --version 3 --beta 0.3

Use --tamper to send a payload with a deliberately wrong sha256 (verifies tamper alert).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt  # type: ignore[import-untyped]

REPO_ROOT = Path(__file__).resolve().parents[1]


def _canonical_unsigned(payload: dict) -> bytes:
    unsigned = {k: v for k, v in payload.items() if k != "sha256"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sign(payload: dict) -> str:
    return hashlib.sha256(_canonical_unsigned(payload)).hexdigest()


def _load_publisher_creds() -> tuple[str, str]:
    p = REPO_ROOT / "config/secrets/system_clients.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    pub = data["ota_publisher"]
    return pub["username"], pub["password"]


def _build_topic(scope: str, floor: int | None, room: int | None) -> str:
    if scope == "broadcast":
        return "campus/b01/ota/config"
    if scope == "floor":
        if floor is None:
            raise SystemExit("--floor required for --scope floor")
        return f"campus/b01/f{floor:02d}/ota/config"
    if scope == "room":
        if floor is None or room is None:
            raise SystemExit("--floor and --room required for --scope room")
        return f"campus/b01/f{floor:02d}/r{room:03d}/ota/config"
    raise SystemExit(f"Unknown scope {scope!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scope", choices=["broadcast", "floor", "room"], required=True)
    ap.add_argument("--floor", type=int)
    ap.add_argument("--room", type=int, help="Global room number, e.g. 520")
    ap.add_argument("--version", required=True, help="String version, e.g. 2 or 1.2.3")
    ap.add_argument("--alpha", type=float)
    ap.add_argument("--beta", type=float)
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=8883)
    ap.add_argument("--cafile", default=str(REPO_ROOT / "config/certs/ca.crt"))
    ap.add_argument("--no-tls", action="store_true")
    ap.add_argument("--tamper", action="store_true",
                    help="Publish with an intentionally invalid sha256 to trigger tamper alert")
    args = ap.parse_args()

    params: dict[str, float] = {}
    if args.alpha is not None:
        params["alpha"] = args.alpha
    if args.beta is not None:
        params["beta"] = args.beta
    if not params:
        print("warning: no params (--alpha/--beta) provided; payload only bumps version", file=sys.stderr)

    payload = {
        "version": str(args.version),
        "params": params,
        "ts": int(time.time()),
    }
    payload["sha256"] = _sign(payload)
    if args.tamper:
        payload["sha256"] = "0" * 64

    topic = _build_topic(args.scope, args.floor, args.room)
    user, pwd = _load_publisher_creds()

    client = mqtt.Client(client_id="ota-publisher-cli")
    client.username_pw_set(user, pwd)
    if not args.no_tls:
        client.tls_set(ca_certs=args.cafile)
        client.tls_insecure_set(True)
    client.connect(args.host, args.port, keepalive=15)
    info = client.publish(topic, json.dumps(payload), qos=1, retain=False)
    info.wait_for_publish(timeout=10)
    client.disconnect()

    print(f"Published to {topic}: {json.dumps(payload)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
