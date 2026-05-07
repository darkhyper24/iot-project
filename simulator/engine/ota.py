"""Phase 3 — OTA pipeline.

Single simulator-level subscriber receives broadcast/floor/room OTA topics,
verifies SHA-256, applies physics overrides + version bump on resolved rooms.
On hash mismatch, publishes a tamper alert.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:  # avoid hard import at module load time so pure helpers stay testable
    from gmqtt import Client as MQTTClient

    from simulator.models.room import Room

logger = logging.getLogger(__name__)


def canonical_unsigned_bytes(payload: dict) -> bytes:
    """Plan B.5 canonical hash body: full payload minus the 'sha256' field."""
    unsigned = {k: v for k, v in payload.items() if k != "sha256"}
    return json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compute_signature(payload: dict) -> str:
    return hashlib.sha256(canonical_unsigned_bytes(payload)).hexdigest()


def verify_signature(payload: dict) -> bool:
    sig = payload.get("sha256")
    if not isinstance(sig, str):
        return False
    return compute_signature(payload) == sig


def _version_key(value: str) -> tuple:
    """Comparable best-effort version key for numeric dotted versions."""
    parts: list[Any] = []
    for part in str(value).split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(part)
    return tuple(parts)


def version_not_newer(candidate: str, current: str) -> bool:
    """True when candidate should be skipped because it is <= current."""
    if candidate == current:
        return True
    try:
        return _version_key(candidate) <= _version_key(current)
    except TypeError:
        # Mixed non-comparable parts: only exact equality is considered old.
        return False


class OtaSubscriber:
    """One MQTT client; resolves scope from topic; applies to in-memory rooms."""

    def __init__(self, config: dict, rooms: "Iterable[Room]"):
        from simulator import addressing
        from simulator.system_clients import load_system_clients

        self.config = config
        self.rooms = list(rooms)
        self._sys_creds = load_system_clients(config)
        self._client = None
        self._tamper_topic = f"{addressing.campus_prefix(config)}/{addressing.building_slug(config)}/security/tamper"

    async def start(self) -> None:
        from gmqtt import Client as MQTTClient

        cred = self._sys_creds.get("ota")
        if not cred:
            logger.warning("OTA subscriber disabled (no credentials).")
            return
        client = MQTTClient(client_id="sim-ota")
        client.set_auth_credentials(cred["username"], cred["password"])
        client.on_message = self._on_message
        from simulator import addressing
        from simulator.engine.twin import _connect  # reuse connect helper

        await _connect(client, self.config)
        prefix = addressing.campus_prefix(self.config)
        bldg = addressing.building_slug(self.config)
        topics = [
            f"{prefix}/{bldg}/ota/config",
            f"{prefix}/{bldg}/+/ota/config",
            f"{prefix}/{bldg}/+/+/ota/config",
        ]
        for t in topics:
            client.subscribe(t, qos=1)
        self._client = client
        logger.info("OTA subscriber listening on %s", topics)

    async def _on_message(self, client, topic, payload, qos, properties):  # noqa: ARG002
        try:
            data = json.loads(payload.decode() if isinstance(payload, bytes) else payload)
        except json.JSONDecodeError:
            logger.warning("Malformed OTA payload on %s", topic)
            return

        if not verify_signature(data):
            logger.warning("OTA hash MISMATCH topic=%s payload_keys=%s", topic, list(data.keys()))
            self._publish_tamper(topic, client, data)
            return

        targets = self._resolve_targets(topic)
        if not targets:
            logger.warning("OTA topic %s resolved zero rooms", topic)
            return

        version = str(data.get("version", ""))
        params: dict[str, Any] = data.get("params", {}) or {}
        applied = 0
        for room in targets:
            # idempotency / rollback guard: skip if version is not newer
            if version and version_not_newer(version, room.current_version):
                continue
            if "alpha" in params:
                try:
                    room.alpha = float(params["alpha"])
                except (TypeError, ValueError):
                    logger.warning("OTA alpha invalid on %s: %r", room.id, params["alpha"])
            if "beta" in params:
                try:
                    room.beta = float(params["beta"])
                except (TypeError, ValueError):
                    logger.warning("OTA beta invalid on %s: %r", room.id, params["beta"])
            if version:
                room.current_version = version
            applied += 1
        logger.info("OTA applied: scope=%s rooms=%d version=%s", topic, applied, version)

    def _resolve_targets(self, topic: str) -> list:
        # campus/b01/ota/config         — broadcast (200)
        # campus/b01/f##/ota/config     — floor (20)
        # campus/b01/f##/r###/ota/config — single room
        parts = topic.split("/")
        if parts[-1] != "config" or "ota" not in parts:
            return []
        try:
            ota_idx = parts.index("ota")
        except ValueError:
            return []
        scope = parts[2:ota_idx]
        if not scope:
            return list(self.rooms)
        if len(scope) == 1 and scope[0].startswith("f"):
            try:
                floor = int(scope[0][1:])
            except ValueError:
                return []
            return [r for r in self.rooms if r.floor_number == floor]
        if len(scope) == 2 and scope[0].startswith("f") and scope[1].startswith("r"):
            try:
                floor = int(scope[0][1:])
                room_num = int(scope[1][1:])
            except ValueError:
                return []
            return [r for r in self.rooms if r.floor_number == floor and r.room_number == room_num]
        return []

    def _publish_tamper(self, src_topic: str, client, payload: dict) -> None:
        alert = {
            "source_topic": src_topic,
            "client_id": getattr(client, "_client_id", "sim-ota"),
            "ts": int(time.time()),
            "received_payload_keys": list(payload.keys()),
            "expected_sha256": compute_signature(payload),
            "actual_sha256": payload.get("sha256"),
        }
        if self._client is not None:
            self._client.publish(self._tamper_topic, json.dumps(alert), qos=1)
        logger.warning("Tamper alert published: %s", alert)

    async def stop(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001
                logger.exception("OTA disconnect error")
