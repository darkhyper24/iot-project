#!/usr/bin/env python3
"""Deprecated: use scripts/generate_campus_secrets.py for per-room MQTT + HiveMQ RBAC + CoAP PSK."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    gen = root / "scripts" / "generate_campus_secrets.py"
    print("gen_mqtt_passwords.py is deprecated; delegating to generate_campus_secrets.py", file=sys.stderr)
    return subprocess.call([sys.executable, str(gen), *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
