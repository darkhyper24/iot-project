"""Load per-room MQTT and CoAP PSK credentials from generated JSON files."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from simulator.models.room import Room

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_mqtt_credentials_map(config: dict) -> dict[str, tuple[str, str]]:
    """room_id -> (username, password). Empty if file missing or invalid."""
    path = config.get("mqtt", {}).get("credentials_file") or ""
    if not path:
        path = "config/secrets/mqtt_nodes.json"
    p = Path(path)
    if not p.is_absolute():
        p = _repo_root() / p
    if not p.is_file():
        logger.warning("MQTT credentials file not found at %s — using mqtt.username/password from YAML", p)
        return {}

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Failed to read MQTT credentials %s: %s", p, e)
        return {}

    out: dict[str, tuple[str, str]] = {}
    for node in data.get("nodes", []):
        rid = node.get("room_id")
        user = node.get("username")
        pwd = node.get("password")
        if rid and isinstance(user, str) and isinstance(pwd, str):
            out[rid] = (user, pwd)
    logger.info("Loaded %d MQTT node credentials from %s", len(out), p)
    return out


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


def mqtt_user_pass_for_room(config: dict, room: Room, cred_map: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    if room.id in cred_map:
        return cred_map[room.id]
    shared_user = (config.get("mqtt", {}).get("username") or "").strip()
    shared_pwd = (config.get("mqtt", {}).get("password") or "").strip()
    if shared_user:
        return shared_user, shared_pwd
    return None


def coap_identity_psk_for_room(room: Room, psk_map: dict[str, tuple[bytes, bytes]]) -> tuple[bytes, bytes] | None:
    return psk_map.get(room.id)
