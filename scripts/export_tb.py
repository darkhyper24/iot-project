#!/usr/bin/env python3
"""Export ThingsBoard entities to config/thingsboard/provisioning/ for version control.

Writes:
  - device_profiles.json — paginated device profiles
  - dashboards.json — tenant dashboards (full metadata)
  - rule_chains.json — tenant rule chains (full metadata)

Usage:
  TB_URL=http://localhost:9090 TB_USER=tenant@thingsboard.org TB_PASS=tenant \\
  python scripts/export_tb.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "config" / "thingsboard" / "provisioning"


def login(base: str, user: str, password: str) -> requests.Session:
    s = requests.Session()
    r = s.post(f"{base.rstrip('/')}/api/auth/login", json={"username": user, "password": password}, timeout=30)
    r.raise_for_status()
    token = r.json()["token"]
    s.headers["X-Authorization"] = f"Bearer {token}"
    return s


def export_device_profiles(session: requests.Session, base: str) -> list:
    profiles = []
    page = 0
    while True:
        r = session.get(
            f"{base}/api/deviceProfiles",
            params={"pageSize": 50, "page": page},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        profiles.extend(data.get("data", []))
        if not data.get("hasNext", False):
            break
        page += 1
    return profiles


def export_dashboards(session: requests.Session, base: str) -> list:
    out = []
    page = 0
    while True:
        r = session.get(
            f"{base}/api/tenant/dashboards",
            params={"pageSize": 50, "page": page},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        for d in data.get("data", []):
            did = d["id"]["id"]
            full = session.get(f"{base}/api/dashboard/{did}", timeout=60)
            full.raise_for_status()
            out.append(full.json())
        if not data.get("hasNext", False):
            break
        page += 1
    return out


def export_rule_chains(session: requests.Session, base: str) -> list:
    out = []
    page = 0
    while True:
        r = session.get(
            f"{base}/api/ruleChains",
            params={"pageSize": 50, "page": page},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        for d in data.get("data", []):
            cid = d["id"]["id"]
            meta = session.get(f"{base}/api/ruleChain/{cid}/metadata", timeout=60)
            if meta.status_code == 404:
                continue
            meta.raise_for_status()
            out.append(meta.json())
        if not data.get("hasNext", False):
            break
        page += 1
    return out


def main() -> int:
    base = os.environ.get("TB_URL", "http://localhost:9090").rstrip("/")
    user = os.environ.get("TB_USER", "tenant@thingsboard.org")
    password = os.environ.get("TB_PASS", "tenant")

    try:
        session = login(base, user, password)
    except Exception as e:
        print(f"login failed: {e}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    profiles = export_device_profiles(session, base)
    (OUT_DIR / "device_profiles.export.json").write_text(json.dumps(profiles, indent=2))
    print(f"wrote {OUT_DIR / 'device_profiles.export.json'} ({len(profiles)} profiles)")

    try:
        dashboards = export_dashboards(session, base)
        (OUT_DIR / "dashboards.json").write_text(json.dumps(dashboards, indent=2))
        print(f"wrote {OUT_DIR / 'dashboards.json'} ({len(dashboards)} dashboards)")
    except Exception as e:
        print(f"dashboard export: {e}", file=sys.stderr)

    try:
        chains = export_rule_chains(session, base)
        (OUT_DIR / "rule_chains.json").write_text(json.dumps(chains, indent=2))
        print(f"wrote {OUT_DIR / 'rule_chains.json'} ({len(chains)} chains)")
    except Exception as e:
        print(f"rule chain export: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
