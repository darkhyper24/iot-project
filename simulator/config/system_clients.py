"""Phase 3 system client credentials (fanout / reporter / ota / ota_publisher).

Reads ``config/secrets/system_clients.json`` written by
``scripts/generate_campus_secrets.py``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_PATH = "config/secrets/system_clients.json"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_system_clients(config: dict) -> dict[str, dict[str, str]]:
    """Return {role: {"username": ..., "password": ...}} for the four system roles."""
    path = config.get("phase3", {}).get("system_clients_file", _DEFAULT_PATH)
    p = Path(path)
    if not p.is_absolute():
        p = _repo_root() / p
    if not p.is_file():
        logger.warning("System clients file %s not found — Phase 3 fanout/reporter/ota disabled.", p)
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Failed to load system clients %s: %s", p, e)
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}
