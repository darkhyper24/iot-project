# Docker Command Reference

This file is a quick command catalog for manually interacting with the simulator.

Run all commands from the project root:

```bash
cd "/run/media/enkea/New Volume/University/Senior/Second Semester/SWAPD 453 IOT/Project/iot-project"
```

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
| HiveMQ MQTT | 1883 | Campus MQTT backbone |
| ThingsBoard UI | 9090 | http://localhost:9090 |
| ThingsBoard Edge RPC | 7070 | |
| Simulator Postgres | 5433 | |
| Simulator CoAP (UDP) | 5693 | Maps to container 5683 |
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

## Topic Structure

Use these topic shapes:

- Single room telemetry:
  `campus/bldg_01/floor_01/room_101/telemetry`
- Single room heartbeat:
  `campus/bldg_01/floor_01/room_101/heartbeat`
- Fleet monitoring heartbeat:
  `campus/bldg_01/fleet_monitoring/heartbeat`
- Single room command:
  `campus/bldg_01/floor_01/room_101/command`
- Single floor command:
  `campus/bldg_01/floor_01/command`
- Whole building command:
  `campus/bldg_01/command`

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
