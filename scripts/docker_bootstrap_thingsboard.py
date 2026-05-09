#!/usr/bin/env python3
"""Wait for ThingsBoard, then seed project entities and gateway flows."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]


def wait_for_login(url: str, username: str, password: str) -> None:
    login_url = f"{url.rstrip('/')}/api/auth/login"
    payload = {"username": username, "password": password}
    last_error = "not attempted"

    for attempt in range(1, 121):
        try:
            response = requests.post(login_url, json=payload, timeout=10)
            if response.ok:
                print(f"ThingsBoard API is ready after {attempt} attempt(s).")
                return
            last_error = f"HTTP {response.status_code}: {response.text[:160]}"
        except requests.RequestException as exc:
            last_error = str(exc)

        print(f"Waiting for ThingsBoard API ({attempt}/120): {last_error}")
        time.sleep(5)

    raise RuntimeError(f"ThingsBoard API did not become ready: {last_error}")


def run_script(script: str, url: str, username: str, password: str) -> None:
    cmd = [
        sys.executable,
        script,
        "--url",
        url,
        "--username",
        username,
        "--password",
        password,
    ]
    print(f"Running {' '.join(cmd)}")
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    url = os.environ.get("TB_BOOTSTRAP_URL", "http://thingsboard:8080")
    username = os.environ.get("TB_BOOTSTRAP_USERNAME", "tenant@thingsboard.org")
    password = os.environ.get("TB_BOOTSTRAP_PASSWORD", "tenant")

    wait_for_login(url, username, password)
    run_script("scripts/seed_thingsboard.py", url, username, password)
    run_script("scripts/generate_nodered_flows.py", url, username, password)
    print("ThingsBoard entities and Node-RED gateway flows are ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
