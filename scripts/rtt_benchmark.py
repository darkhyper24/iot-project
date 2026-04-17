#!/usr/bin/env python3
"""Measure command round-trip: publish .../cmd → first .../response (same command_id).

Uses the rtt-bench HiveMQ user (see scripts/gen_credentials.py). TLS to broker :8883.

Example:
  MQTT_PASSWORD_SECRET=... python scripts/rtt_benchmark.py --n 50 --mixed
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
import statistics
import time
import uuid
from pathlib import Path

import yaml
from gmqtt import Client as MQTTClient

ROOT = Path(__file__).resolve().parents[1]


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config" / "config.yaml").read_text())


def build_ssl() -> ssl.SSLContext:
    security = load_config().get("security", {})
    ca = os.environ.get("SECURITY_MQTT_CA_PATH", str(ROOT / "config" / "certs" / "ca.crt"))
    ctx = ssl.create_default_context(cafile=ca if os.path.exists(ca) else None)
    if security.get("mqtt_insecure_skip_verify", True):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def run_benchmark(args: argparse.Namespace) -> None:
    cfg = load_config()
    prefix = cfg["mqtt"]["topic_prefix"]
    building = cfg["building"]["id"]
    host = os.environ.get("MQTT_BROKER_HOST", cfg["mqtt"]["broker_host"])
    port = int(os.environ.get("MQTT_BROKER_PORT", str(cfg["mqtt"]["broker_port"])))

    # Must match scripts/gen_credentials.py (bench user uses --bench-password, not HMAC).
    user = "rtt-bench"
    password = os.environ.get("RTT_BENCH_PASSWORD", "rtt-bench-secret")

    # Default MQTT room r512 (floor 5 room 12) and CoAP r502 (floor 5 room 2)
    mqtt_path = f"{prefix}/{building}/f{args.floor:02d}/r{args.floor * 100 + 12:03d}"
    coap_path = f"{prefix}/{building}/f{args.floor:02d}/r{args.floor * 100 + 2:03d}"

    lat_ms: list[float] = []
    pending: dict[str, float] = {}

    client = MQTTClient(f"rtt-bench-{uuid.uuid4().hex[:8]}")

    def on_message(c, topic, payload, qos, props):
        del c, qos, props
        try:
            body = json.loads(payload.decode() if isinstance(payload, bytes) else payload)
        except json.JSONDecodeError:
            return
        cid = body.get("command_id")
        if not cid or cid not in pending:
            return
        t0 = pending.pop(cid)
        dt = (time.perf_counter() - t0) * 1000
        lat_ms.append(dt)

    client.on_message = on_message
    client.set_auth_credentials(user, password)

    ssl_ctx = build_ssl()
    await client.connect(host, port, ssl=ssl_ctx, keepalive=30)
    client.subscribe(f"{prefix}/{building}/+/+/response", qos=1)

    for i in range(args.n):
        use_coap = args.mixed and (i % 2 == 1)
        path = coap_path if use_coap else mqtt_path
        topic_cmd = f"{path}/cmd"
        topic_resp = f"{path}/response"
        cid = f"rtt-{uuid.uuid4().hex}"
        pending[cid] = time.perf_counter()
        cmd = {
            "command_id": cid,
            "action": "set_hvac",
            "value": "ECO" if i % 2 == 0 else "ON",
        }
        client.publish(topic_cmd, json.dumps(cmd), qos=2)
        # wait for this command's response
        deadline = time.perf_counter() + args.timeout
        while cid in pending and time.perf_counter() < deadline:
            await asyncio.sleep(0.02)
        if cid in pending:
            pending.pop(cid, None)
            print(f"timeout waiting for {cid} ({'coap' if use_coap else 'mqtt'} {path})")

    await client.disconnect()

    if not lat_ms:
        print("No RTT samples collected.")
        return

    lat_ms.sort()

    def pct(p: float) -> float:
        if not lat_ms:
            return float("nan")
        i = max(0, min(len(lat_ms) - 1, int(round((p / 100.0) * (len(lat_ms) - 1)))))
        return lat_ms[i]

    print(
        f"samples={len(lat_ms)} p50={statistics.median(lat_ms):.1f}ms "
        f"p95={pct(95):.1f}ms p99={pct(99):.1f}ms max={lat_ms[-1]:.1f}ms"
    )

    out = ROOT / "perf_logs" / "rtt_samples.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        f.write("latency_ms\n")
        for x in lat_ms:
            f.write(f"{x:.3f}\n")
    print(f"wrote {out}")


def main() -> None:
    p = argparse.ArgumentParser(description="RTT benchmark for campus command path")
    p.add_argument("--n", type=int, default=100)
    p.add_argument("--floor", type=int, default=5)
    p.add_argument("--mixed", action="store_true", help="alternate CoAP and MQTT target rooms")
    p.add_argument("--timeout", type=float, default=5.0)
    args = p.parse_args()
    asyncio.run(run_benchmark(args))


if __name__ == "__main__":
    main()
