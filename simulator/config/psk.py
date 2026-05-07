"""Load per-room CoAP PSK credentials from generated JSON files."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulator.domain.room import Room

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_coap_psk_map(config: dict) -> dict[str, tuple[bytes, bytes]]:
    """room_id -> (identity bytes, psk bytes)."""
    path = config.get("phase2", {}).get("coap", {}).get("psk_file") or ""
    if not path:
        path = "config/secrets/coap_psk.json"
    p = Path(path)
    if not p.is_absolute():
        p = _repo_root() / p
    if not p.is_file():
        logger.warning("CoAP PSK file not found at %s", p)
        return {}

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Failed to read CoAP PSK %s: %s", p, e)
        return {}

    out: dict[str, tuple[bytes, bytes]] = {}
    for node in data.get("nodes", []):
        rid = node.get("room_id")
        ident = node.get("identity")
        key_hex = node.get("key_hex")
        if rid and ident and key_hex:
            try:
                out[rid] = (str(ident).encode("utf-8"), bytes.fromhex(str(key_hex)))
            except ValueError:
                continue
    logger.info("Loaded %d CoAP PSK entries from %s", len(out), p)
    return out


def coap_identity_psk_for_room(
    room: "Room",
    psk_map: dict[str, tuple[bytes, bytes]],
) -> tuple[bytes, bytes] | None:
    return psk_map.get(room.id)
