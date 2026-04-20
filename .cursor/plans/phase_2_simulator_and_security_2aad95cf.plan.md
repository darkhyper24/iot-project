---
name: Phase 2 Simulator Python Scope
overview: "Complete all Python/simulator code for SWAPD453 Phase 2 — hybrid engine (100 gmqtt + 100 CoAP/aiocoap), PDF topics/cmd/URIs, QoS2/DUP/Sentinel CON, TLS (MQTT) and DTLS (CoAP), credential loading — plus Dockerfile/requirements and simulator-side Docker/env. Out of scope for this plan: ThingsBoard UI/provisioning and Node-RED flows (handled separately by you); non-Python HiveMQ broker XML/ACL/certs are listed as required repo artifacts the simulator depends on."
todos:
  - id: config-addressing
    content: "config.yaml + config.py: phase2 topics campus/b01/f##/r###, cmd, transport split, TLS/CoAP/DTLS env keys"
    status: completed
  - id: module-addressing
    content: "New simulator/addressing.py (or Room refactor): mqtt_path, coap_path, is_mqtt_room()"
    status: pending
  - id: room-commandhandler
    content: "room.py + commands.py: cmd topic resolution, optional cmd_id; keep apply_command"
    status: pending
  - id: mqtt-clients-main
    content: "main.py: create/connect/disconnect 100 MQTT clients, LWT, TLS ssl params, staggered connect"
    status: pending
  - id: world-engine-split
    content: "world_engine.py: per-transport room loop, client map, fleet/heartbeat topics, shutdown all clients"
    status: pending
  - id: mqtt-security-module
    content: "Optional simulator/mqtt_tls.py: build ssl.SSLContext from ca/cert/key paths"
    status: pending
  - id: coap-server-module
    content: "New simulator/coap_server.py: aiocoap Site, telemetry Observe, CON PUT hvac, Sentinel CON, notify on change"
    status: pending
  - id: coap-dtls
    content: DTLS for CoAP (PSK or certs); Dockerfile system deps if needed
    status: pending
  - id: reliability-dup
    content: "CommandHandler or wrapper: QoS2 subscribe, DUP/idempotency logging"
    status: pending
  - id: faults-sentinel
    content: "Optional sentinel trigger (faults.py or room): drives CON alert resource"
    status: pending
  - id: requirements-dockerfile
    content: requirements.txt (aiocoap, etc.) + Dockerfile apt/openssl if DTLS
    status: pending
  - id: compose-simulator-env
    content: "docker-compose.yaml: simulator env for TLS paths, 8883, CoAP/DTLS secrets volume"
    status: pending
  - id: docs-simulator
    content: "DOCKER_COMMANDS.md / MANUAL_TESTING.md: cmd, TLS mosquitto, CoAP test hints"
    status: pending
  - id: hivemq-artifacts
    content: "Non-Python: config/hivemq TLS listener + ACL XML + config/certs (or script); needed for Python TLS clients to work"
    status: pending
isProject: false
---

# Phase 2 — All Python simulator + security (ThingsBoard & Node-RED excluded)

**Your focus in this plan:** every **Python** change under [`iot-project`](file:///Users/youssefhelal/Youssef/iot-project), plus **Dockerfile**, **requirements.txt**, **simulator `environment`/`volumes` in docker-compose**, and **config YAML** the app reads.

**Explicitly out of scope here (you work on these next):** ThingsBoard provisioning, MQTT integration UI, dashboards, rule chains, and **Node-RED** flow JSON / thinning / gateway logic. The plan still defines **topic/URI contracts** so those tools can align.

---

## 1. File-level checklist (Python & app config)

| Action | Path |
|--------|------|
| Edit | [`main.py`](file:///Users/youssefhelal/Youssef/iot-project/main.py) — thin entry; may delegate to new `simulator/bootstrap.py` if `main.py` grows |
| Edit | [`simulator/main.py`](file:///Users/youssefhelal/Youssef/iot-project/simulator/main.py) — create **100** `MQTTClient`s, connect TLS, register handlers, start **CoAP** server task, `asyncio.gather` engine + coap |
| Add | `simulator/addressing.py` — `mqtt_topic_base(room)`, `mqtt_telemetry_topic`, `mqtt_cmd_topic`, `coap_path_telemetry`, `coap_path_actuators`, `transport_for_room` |
| Edit | [`simulator/models/room.py`](file:///Users/youssefhelal/Youssef/iot-project/simulator/models/room.py) — Phase 2 slugs (`b01`, `f##`, `r###`); deprecate or alias old `bldg_01/floor_XX/room_XXX`; property `uses_mqtt` / `uses_coap` from config |
| Edit | [`simulator/engine/commands.py`](file:///Users/youssefhelal/Youssef/iot-project/simulator/engine/commands.py) — resolve **`cmd`** segments; optional **`cmd_id`** dedup; **QoS/DUP** logging hooks |
| Edit | [`simulator/engine/world_engine.py`](file:///Users/youssefhelal/Youssef/iot-project/simulator/engine/world_engine.py) — `dict[str, MQTTClient]`; branch `_room_loop` by transport; `setup_mqtt` per client; shutdown disconnect all |
| Add | `simulator/coap_server.py` (or `simulator/coap/`) — aiocoap **Site**, resources for telemetry Observe, **CON** PUT `actuators/hvac`, **Sentinel** CON, `asyncio` integration with `WorldEngine` / `Room` refs |
| Add (opt.) | `simulator/mqtt_tls.py` — `ssl_context_from_config(config)` using CA/client cert paths |
| Edit | [`simulator/config.py`](file:///Users/youssefhelal/Youssef/iot-project/simulator/config.py) — env: `MQTT_TLS_CAFILE`, `MQTT_TLS_CERTFILE`, `MQTT_TLS_KEYFILE`, `MQTT_USE_TLS`, `COAP_PSK`, `DTLS_*`, broker port |
| Edit | [`simulator/faults.py`](file:///Users/youssefhelal/Youssef/iot-project/simulator/faults.py) — optional hook or new fault type to fire **Sentinel** once |
| No logic change target | [`simulator/physics.py`](file:///Users/youssefhelal/Youssef/iot-project/simulator/physics.py) — keep as-is |
| Edit | [`simulator/persistence/database.py`](file:///Users/youssefhelal/Youssef/iot-project/simulator/persistence/database.py) — only if schema/room_id format changes (likely **unchanged**) |
| Edit | [`config/config.yaml`](file:///Users/youssefhelal/Youssef/iot-project/config/config.yaml) — `phase2:` block: transport rule, TLS toggle, CoAP bind host/port |
| Edit | [`requirements.txt`](file:///Users/youssefhelal/Youssef/iot-project/requirements.txt) — `aiocoap`; pin versions |
| Edit | [`Dockerfile`](file:///Users/youssefhelal/Youssef/iot-project/Dockerfile) — OS packages if OpenSSL/DTLS build needs them |
| Edit | [`docker-compose.yaml`](file:///Users/youssefhelal/Youssef/iot-project/docker-compose.yaml) — **simulator** `environment` for TLS paths, `8883`, mount `./config/certs` |
| Edit | [`DOCKER_COMMANDS.md`](file:///Users/youssefhelal/Youssef/iot-project/DOCKER_COMMANDS.md), [`MANUAL_TESTING.md`](file:///Users/youssefhelal/Youssef/iot-project/MANUAL_TESTING.md) — `cmd`, TLS `mosquitto_sub` examples |

**Non-Python artifacts (required for security story; not Node-RED/TB):**

| Artifact | Purpose |
|----------|---------|
| `config/hivemq/` — listener + TLS + **ACL** | Broker accepts TLS on **8883**; ACL restricts `campus/b01/f{K}/#` per user |
| `config/certs/` — CA, server, optional **100 client** certs or use username/password from YAML | gmqtt `ssl_context` / HiveMQ trust |
| Optional `scripts/gen_mqtt_passwords.py` or shell | Generate 100 usernames/passwords **matching** ACL users (Python-friendly) |

---

## 2. Behaviour summary (PDF-aligned)

- **100 MQTT**: one `gmqtt.Client` per MQTT-designated room; unique `client_id`; **LWT**; subscribe `.../cmd` with **QoS 2** where supported; publish telemetry/heartbeat on PDF topic shape.
- **100 CoAP**: one **aiocoap** process, **Observe** on `/f##/r###/telemetry`; **CON** **PUT** `/f##/r###/actuators/hvac`; **CON** **Sentinel** alert path; **DTLS** when enabled.
- **200 asyncio tasks**: one loop per **room** (unchanged count); each loop drives physics then **either** MQTT publish **or** CoAP notification — not both.
- **Reliability**: `cmd_id` + idempotent apply; log **DUP**; document in code comment or `docs/` snippet for report later.

---

## 3. Handoff contract (for your Node-RED / ThingsBoard work later)

You do **not** implement these in Python here, but the simulator must expose stable:

- **MQTT topics:** `campus/b01/f{ff}/r{rrr}/telemetry`, `.../heartbeat` (if kept), `.../cmd`.
- **CoAP:** `coap://simulator:5683/f##/r###/telemetry` (plain UDP in container); **DTLS** port/host TBD in config when enabled.
- **Credentials:** per-floor or per-device users must match **HiveMQ ACL** and what **gmqtt** uses in Python.

---

## 4. Implementation order (Python-first)

1. **Addressing + config + Room + CommandHandler** (`cmd`, transport split) — can still use **one** MQTT client temporarily to validate topics on HiveMQ **1883**.
2. **Split 100 MQTT clients + WorldEngine** + LWT + per-client subscribe.
3. **CoAP server module** + wire to CoAP rooms (plain UDP first).
4. **Reliability**: QoS2 path, `cmd_id`, DUP logging; Sentinel CON resource + trigger.
5. **`mqtt_tls.py` + config** → connect to **8883**; update compose env.
6. **CoAP DTLS** + Dockerfile deps.
7. **HiveMQ XML/ACL/certs** in repo (non-Python) so end-to-end auth works from Python clients.
8. **Docs** for manual testing with TLS.

---

## 5. Risks

- **gmqtt**: verify **TLS** and **QoS 2 subscribe** API on your version.
- **aiocoap DTLS**: may need **`tinydtls`** / system libs — spike before locking Dockerfile.
- **100 connections**: keep [`ulimits`](file:///Users/youssefhelal/Youssef/iot-project/docker-compose.yaml) and monitor file descriptors.

---

## 6. Deferred (your parallel track — not this codebase)

- ThingsBoard: devices, integration, assets, dashboards.
- Node-RED: 10 flows, Observe→MQTT, southbound, thinning, offline branch.
- Course **Performance PDF** (Pulse, RTT tables): evidence after stack is complete.
