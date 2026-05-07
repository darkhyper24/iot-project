# Docker Command Reference

This file is a quick command catalog for manually interacting with the simulator.

Run all commands from the **repository root** (where `docker-compose.yaml` lives).

## Secure stack (first-time)

1. `cp .env.example .env` and adjust if needed.
2. `./scripts/gen_broker_keystore.sh` — creates `config/certs/broker.jks`, `ca.crt`, etc.
3. `python scripts/generate_campus_secrets.py` — creates `config/secrets/mqtt_nodes.json`, `config/secrets/coap_psk.json`, and `config/hivemq/extensions/hivemq-file-rbac-extension/conf/credentials.xml` (gitignored).
4. `docker compose up --build`

Default simulator settings use **MQTT TLS 8883** (`MQTT_USE_TLS=true`), per-room user/password from `mqtt_nodes.json`, and **CoAP DTLS** on **5684** (host UDP **5694**).

### Local dev without TLS / without keystore

- Copy `config/hivemq/conf/config.plain-only.xml` over `config/hivemq/conf/config.xml`.
- Set `MQTT_USE_TLS=false`, `MQTT_BROKER_PORT=1883` in `.env`.
- You still need per-node MQTT credentials if the File RBAC extension is enabled (or use shared `MQTT_USERNAME` / `MQTT_PASSWORD` only if the file-rbac `credentials.xml` is not mounted).

## Start / Stop

### Stack (HiveMQ, ThingsBoard, gateways)

Start everything (build simulator image):

```bash
docker compose up --build
```

Start in background:

```bash
docker compose up --build -d
```

**ThingsBoard first-time database init** (run once after `postgres-tb` is healthy; required before the UI will work):

```bash
docker compose run --rm -e INSTALL_TB=true -e LOAD_DEMO=false thingsboard
```

Then start or restart ThingsBoard if needed:

```bash
docker compose up -d thingsboard
```

**Useful host ports**

| Service | Host port | Notes |
|--------|-----------|--------|
| HiveMQ MQTT | 1883 | Plain listener (optional; remove or bind locally for TLS-only mode) |
| HiveMQ MQTT TLS | 8883 | Default simulator path (`MQTT_USE_TLS=true`) |
| ThingsBoard UI | 9090 | http://localhost:9090 |
| ThingsBoard Edge RPC | 7070 | |
| Simulator Postgres | 5433 | |
| Simulator CoAP DTLS (UDP) | 5694 | Maps to container **5684** when `phase2.coap.dtls_enabled` is true |
| Node-RED floor 1–3 | 1880–1882 | |
| Node-RED floor 4–10 | 1890–1896 | |

### Stop

Stop everything (default compose file):

```bash
docker compose down
```

Stop and delete volumes:

```bash
docker compose down -v
```

Check running services:

```bash
docker compose ps
```

## Logs

Follow simulator logs:

```bash
docker compose logs -f simulator
```

Show recent simulator logs:

```bash
docker compose logs --tail=100 simulator
```

Show fleet health warnings only:

```bash
docker compose logs -f simulator | grep fleet_health_warning
```

## Topic Structure (Phase 2)

Building slug matches `config/config.yaml` `building.id` (default `b01`). MQTT is used for **rooms 1–10 on each floor**; rooms **11–20** use CoAP only (no MQTT under these paths).

- Single room telemetry:
  `campus/b01/f01/r101/telemetry`
- Single room heartbeat:
  `campus/b01/f01/r101/heartbeat`
- Last-will (offline):
  `campus/b01/f01/r101/lwt`
- Fleet monitoring heartbeat:
  `campus/b01/fleet_monitoring/heartbeat`
- Single room command (QoS 2 subscribe):
  `campus/b01/f01/r101/cmd`
- Single floor command (all MQTT rooms on that floor):
  `campus/b01/f01/cmd`
- Whole-building command (all MQTT rooms):
  `campus/b01/cmd`

**MQTT TLS (default):** run `scripts/gen_broker_keystore.sh`, keep `config/hivemq/conf/config.xml` (PLAIN + TLS listeners). Set `.env` from `.env.example` (`MQTT_USE_TLS=true`, `MQTT_BROKER_PORT=8883`, `MQTT_TLS_CHECK_HOSTNAME=false`). Subscribe with `mosquitto_sub -h localhost -p 8883 --cafile config/certs/ca.crt -u <user> -P <password> -t ...` where `user`/`password` come from `config/secrets/mqtt_nodes.json` for that room.

**CoAP DTLS (default):** host UDP **5694** → container **5684**; use a CoAP client with DTLS PSK matching `config/secrets/coap_psk.json` for the room identity (e.g. `c111` for `b01-f01-r111`). Plain UDP CoAP on 5683 is used only when `phase2.coap.dtls_enabled` is false in `config/config.yaml`.

Legacy topic shapes (`campus/bldg_01/floor_01/room_101/...`, `.../command`) are **not** emitted by the Phase 2 simulator.

## How To Customize Topics

### Building

Current building:

```text
bldg_01
```

If your building id changes, replace only this segment:

```text
campus/bldg_01/...
```

### Floor

Format:

```text
floor_01
floor_02
...
floor_10
```

Examples:

- Floor 1: `floor_01`
- Floor 5: `floor_05`
- Floor 10: `floor_10`

### Room

Format:

```text
room_101
room_218
room_520
room_1008
```

Examples:

- Floor 1 room 1 -> `room_101`
- Floor 2 room 18 -> `room_218`
- Floor 5 room 20 -> `room_520`
- Floor 10 room 8 -> `room_1008`

## MQTT Subscribe Commands

These examples call **mosquitto_sub** / **mosquitto_pub** on your machine against HiveMQ at **localhost:1883** (published by Compose). Install clients first, e.g. on macOS: `brew install mosquitto`.

**Path translation:** templates below use the older `bldg_01/floor_XX/room_XXX` naming. The Phase 2 simulator publishes **`campus/b01/f##/r###/...`** instead (see **Topic Structure (Phase 2)**). Replace segments accordingly, e.g. `floor_01/room_101` → `f01/r101`, `bldg_01` → `b01`, and use the `cmd` suffix instead of `command`.


### Single Room Telemetry

Template:

```bash
mosquitto_sub -h localhost -p 1883 -t 'campus/bldg_01/floor_01/room_101/telemetry'
```

Example for room 520:

```bash
mosquitto_sub -h localhost -p 1883 -t 'campus/bldg_01/floor_05/room_520/telemetry'
```

### Single Room Heartbeat

Template:

```bash
mosquitto_sub -h localhost -p 1883 -t 'campus/bldg_01/floor_01/room_101/heartbeat'
```

### Fleet Monitoring Heartbeat

```bash
mosquitto_sub -h localhost -p 1883 -t 'campus/bldg_01/fleet_monitoring/heartbeat'
```

### Whole Floor Telemetry

Template:

```bash
mosquitto_sub -h localhost -p 1883 -t 'campus/bldg_01/floor_01/+/telemetry'
```

Example for floor 5:

```bash
mosquitto_sub -h localhost -p 1883 -t 'campus/bldg_01/floor_05/+/telemetry'
```

### Whole Building Telemetry

```bash
mosquitto_sub -h localhost -p 1883 -t 'campus/bldg_01/+/+/telemetry'
```

### All Fleet Telemetry

```bash
mosquitto_sub -h localhost -p 1883 -t 'campus/+/+/+/telemetry'
```

### Only Occupied Rooms

```bash
timeout 10 mosquitto_sub -h localhost -p 1883 -t 'campus/+/+/+/telemetry' | grep '\"occupancy\": true'
```

### Only Faulty Telemetry

```bash
timeout 15 mosquitto_sub -h localhost -p 1883 -t 'campus/+/+/+/telemetry' | grep -v '"fault": "none"'
```

## MQTT Publish Commands

### Single Room Command

Template:

```bash
mosquitto_pub -h localhost -p 1883 -t 'campus/bldg_01/floor_01/room_101/command' -m '{\"hvac_mode\":\"ON\",\"target_temp\":26,\"lighting_dimmer\":80}'
```

Example for room 218:

```bash
mosquitto_pub -h localhost -p 1883 -t 'campus/bldg_01/floor_02/room_218/command' -m '{\"hvac_mode\":\"ECO\",\"target_temp\":24,\"lighting_dimmer\":60}'
```

### Whole Floor Command

Template:

```bash
mosquitto_pub -h localhost -p 1883 -t 'campus/bldg_01/floor_02/command' -m '{\"hvac_mode\":\"ECO\",\"target_temp\":24}'
```

Example for floor 5:

```bash
mosquitto_pub -h localhost -p 1883 -t 'campus/bldg_01/floor_05/command' -m '{\"hvac_mode\":\"OFF\",\"target_temp\":22}'
```

### Whole Building Command

Template:

```bash
mosquitto_pub -h localhost -p 1883 -t 'campus/bldg_01/command' -m '{\"hvac_mode\":\"OFF\",\"target_temp\":22}'
```

Example:

```bash
mosquitto_pub -h localhost -p 1883 -t 'campus/bldg_01/command' -m '{\"hvac_mode\":\"ON\",\"target_temp\":26,\"lighting_dimmer\":75}'
```

## Database Commands

### Count All Rooms

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT COUNT(*) AS room_count FROM room_states;"
```

### Show One Room

Template:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT room_id, hvac_mode, target_temp, last_temp, last_humidity, last_update FROM room_states WHERE room_id = 'b01-f01-r101';"
```

Example for room 520:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT room_id, hvac_mode, target_temp, last_temp, last_humidity, last_update FROM room_states WHERE room_id = 'b01-f05-r520';"
```

### Show All Rooms on One Floor

Template:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT room_id, hvac_mode, target_temp FROM room_states WHERE room_id LIKE 'b01-f02-%' ORDER BY room_id;"
```

### Count Rooms on One Floor

Template:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT COUNT(*) AS floor_count FROM room_states WHERE room_id LIKE 'b01-f05-%';"
```

### Count Rooms Matching a Commanded State

Example for floor-wide update:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT COUNT(*) AS updated_rooms FROM room_states WHERE room_id LIKE 'b01-f02-%' AND hvac_mode = 'ECO' AND target_temp = 24;"
```

Example for fleet-wide update:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT COUNT(*) AS updated_rooms FROM room_states WHERE hvac_mode = 'OFF' AND target_temp = 22;"
```

## Restart / Persistence Checks

Restart simulator only:

```bash
docker compose restart simulator
```

Check one room after restart:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT room_id, hvac_mode, target_temp, last_temp, last_humidity, last_update FROM room_states WHERE room_id = 'b01-f01-r101';"
```

Read one telemetry message after restart:

```bash
timeout 5 mosquitto_sub -h localhost -p 1883 -t 'campus/bldg_01/floor_01/room_101/telemetry' -C 1
```

## Fault Testing Commands

### Show Only Faulty Messages

```bash
timeout 15 mosquitto_sub -h localhost -p 1883 -t 'campus/+/+/+/telemetry' | grep -v '"fault": "none"'
```

### Watch Node Dropout Warnings

```bash
docker compose logs -f simulator | grep fleet_health_warning
```

### Increase Fault Probability Temporarily

Set in `config/config.yaml`:

```yaml
faults:
  enabled: true
  probability: 0.5
```

Then rebuild:

```bash
docker compose up --build -d simulator
```

### Restore Normal Fault Probability

Set:

```yaml
faults:
  enabled: true
  probability: 0.01
```

Then rebuild:

```bash
docker compose up --build -d simulator
```

## Fast Examples

Watch one room:

```bash
mosquitto_sub -h localhost -p 1883 -t 'campus/bldg_01/floor_01/room_101/telemetry'
```

Watch one floor:

```bash
mosquitto_sub -h localhost -p 1883 -t 'campus/bldg_01/floor_03/+/telemetry'
```

Watch whole building:

```bash
mosquitto_sub -h localhost -p 1883 -t 'campus/bldg_01/+/+/telemetry'
```

Command one room:

```bash
mosquitto_pub -h localhost -p 1883 -t 'campus/bldg_01/floor_01/room_101/command' -m '{\"hvac_mode\":\"ON\",\"target_temp\":26}'
```

Command one floor:

```bash
mosquitto_pub -h localhost -p 1883 -t 'campus/bldg_01/floor_03/command' -m '{\"hvac_mode\":\"ECO\",\"target_temp\":24}'
```

Command whole fleet:

```bash
mosquitto_pub -h localhost -p 1883 -t 'campus/bldg_01/command' -m '{\"hvac_mode\":\"OFF\",\"target_temp\":22}'
```
