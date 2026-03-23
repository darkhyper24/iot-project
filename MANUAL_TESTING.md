# Manual Testing Guide

This guide walks through manual verification of the major requirements from sections 1 and 2 of the project document using Docker Compose and live MQTT / Postgres inspection.

## Prerequisites

- Open all terminals in the project root:

```bash
cd "/run/media/enkea/New Volume/University/Senior/Second Semester/SWAPD 453 IOT/Project/iot-project"
```

- Use `docker compose`, not `docker-compose`.

## Terminal Layout

Use 5 terminals:

- Terminal 1: Run the full stack
- Terminal 2: Watch one room's telemetry
- Terminal 3: Watch one room's heartbeat
- Terminal 4: Watch central fleet heartbeat
- Terminal 5: Send commands and inspect the database

## 0. Clean Start

If you want to avoid seeing old persisted values from earlier runs:

```bash
docker compose down -v
docker compose up --build
```

Leave Terminal 1 running.

Expected startup indicators in Terminal 1:

- `Connected to PostgreSQL`
- `Connected to MQTT broker`
- `Fleet initialized: 200 rooms`
- `World engine running`

## 1. Verify Telemetry for One Room

In Terminal 2:

```bash
docker compose exec -T mqtt-broker sh -lc "mosquitto_sub -t 'campus/bldg_01/floor_01/room_101/telemetry'"
```

What to check:

- Telemetry is continuously published
- Topic shape is `campus/bldg_01/floor_01/room_101/telemetry`
- Payload includes:
  - `sensor_id`
  - `timestamp`
  - `temperature`
  - `humidity`
  - `occupancy`
  - `light_level`
  - `hvac_mode`
  - `lighting_dimmer`
  - `target_temp`
  - `fault`
- Temperature changes gradually over time rather than jumping randomly

## 2. Verify Per-Room Heartbeat

In Terminal 3:

```bash
docker compose exec -T mqtt-broker sh -lc "mosquitto_sub -t 'campus/bldg_01/floor_01/room_101/heartbeat'"
```

What to check:

- You receive repeated heartbeat messages
- Payload includes:
  - `room_id`
  - `status`
  - `timestamp`

## 3. Verify Central Fleet-Monitoring Heartbeat

In Terminal 4:

```bash
docker compose exec -T mqtt-broker sh -lc "mosquitto_sub -t 'campus/bldg_01/fleet_monitoring/heartbeat'"
```

What to check:

- You receive heartbeat messages from many different rooms
- This demonstrates the separate fleet-monitoring topic

## 4. Verify Persistence Sync

Wait at least 35 seconds after startup so the periodic DB sync has time to run.

In Terminal 5:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT COUNT(*) AS room_count FROM room_states;"
```

Expected:

- `room_count` should be `200`

Then inspect a specific room:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT room_id, hvac_mode, target_temp, last_temp, last_humidity, last_update FROM room_states WHERE room_id = 'b01-f01-r101';"
```

What to check:

- The row exists
- `last_temp`, `last_humidity`, and `last_update` are populated

## 5. Verify Room-Specific Command Handling

In Terminal 5:

```bash
docker compose exec -T mqtt-broker sh -lc "mosquitto_pub -t 'campus/bldg_01/floor_01/room_101/command' -m '{\"hvac_mode\":\"ON\",\"target_temp\":26,\"lighting_dimmer\":80}'"
```

Then check Terminal 2.

What to check:

- `hvac_mode` becomes `ON`
- `target_temp` becomes `26.0`

Confirm the DB save point:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT room_id, hvac_mode, target_temp FROM room_states WHERE room_id = 'b01-f01-r101';"
```

Expected:

- `hvac_mode = ON`
- `target_temp = 26`

## 6. Verify Floor-Wide Command Handling

In Terminal 5:

```bash
docker compose exec -T mqtt-broker sh -lc "mosquitto_pub -t 'campus/bldg_01/floor_02/command' -m '{\"hvac_mode\":\"ECO\",\"target_temp\":24}'"
```

Then check:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT COUNT(*) AS floor_2_updated FROM room_states WHERE room_id LIKE 'b01-f02-%' AND hvac_mode = 'ECO' AND target_temp = 24;"
```

Expected:

- `floor_2_updated` should be `20`

## 7. Verify Building-Wide Command Handling

In Terminal 5:

```bash
docker compose exec -T mqtt-broker sh -lc "mosquitto_pub -t 'campus/bldg_01/command' -m '{\"hvac_mode\":\"OFF\",\"target_temp\":22}'"
```

Then check:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT COUNT(*) AS building_reset FROM room_states WHERE hvac_mode = 'OFF' AND target_temp = 22;"
```

Expected:

- `building_reset` should be `200`

## 8. Verify Restore on Restart

First, set a room to a known non-default command state:

```bash
docker compose exec -T mqtt-broker sh -lc "mosquitto_pub -t 'campus/bldg_01/floor_01/room_101/command' -m '{\"hvac_mode\":\"ON\",\"target_temp\":26}'"
```

Check the database before restart:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT room_id, hvac_mode, target_temp, last_temp, last_humidity, last_update FROM room_states WHERE room_id = 'b01-f01-r101';"
```

Restart only the simulator:

```bash
docker compose restart simulator
```

After a few seconds, check the database again:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT room_id, hvac_mode, target_temp, last_temp, last_humidity, last_update FROM room_states WHERE room_id = 'b01-f01-r101';"
```

Then read one telemetry message:

```bash
docker compose exec -T mqtt-broker sh -lc "timeout 5 mosquitto_sub -t 'campus/bldg_01/floor_01/room_101/telemetry' -C 1"
```

What to check:

- The room does not revert to brand-new defaults
- Commanded state is restored
- Temperature and humidity continue smoothly rather than resetting

## 9. Verify Fleet Size and Hierarchy

The simulator uses 1 building, 10 floors, and 20 rooms per floor.

Check total persisted rooms:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT COUNT(*) AS room_count FROM room_states;"
```

Check one floor count:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus -c "SELECT COUNT(*) AS floor_05_count FROM room_states WHERE room_id LIKE 'b01-f05-%';"
```

Expected:

- `room_count = 200`
- `floor_05_count = 20`

## 10. Verify Deterministic Physics Behavior

Use Terminal 2 and observe one room over time.

What to check:

- Temperature evolves gradually
- When `hvac_mode` changes, the actuator state changes in telemetry
- If `occupancy` becomes `true`, `light_level` should remain relatively high
- Values remain within realistic ranges

If you want to look for occupied rooms across the fleet:

```bash
docker compose exec -T mqtt-broker sh -lc "timeout 10 mosquitto_sub -t 'campus/+/+/+/telemetry'" | grep '\"occupancy\": true'
```

## 11. Verify Fault Modeling

To make faults visible quickly, temporarily increase fault probability.

Edit `config/config.yaml` and change:

```yaml
probability: 0.01
```

to:

```yaml
probability: 0.5
```

Then rebuild the simulator:

```bash
docker compose up --build -d simulator
```

Now watch fault-bearing telemetry:

```bash
docker compose exec -T mqtt-broker sh -lc "timeout 15 mosquitto_sub -t 'campus/+/+/+/telemetry'" | grep -v '"fault": "none"'
```

What to look for:

- `sensor_drift`
- `frozen_sensor`
- `telemetry_delay`

To observe node dropout / silent node detection:

```bash
docker compose logs -f simulator | grep fleet_health_warning
```

What to look for:

- `event: "node_silent"`
- increasing `seconds_silent`

## 12. Restore Normal Fault Rate

After fault testing, change `config/config.yaml` back to:

```yaml
probability: 0.01
```

Then rebuild:

```bash
docker compose up --build -d simulator
```

## 13. Stop the Stack

When done:

```bash
docker compose down
```

If you also want to delete persisted DB data:

```bash
docker compose down -v
```

## Quick Requirement Coverage

This manual flow lets you demonstrate:

- 200-room modeled fleet and hierarchy
- Config-driven room generation
- Structured MQTT topics
- Independent room telemetry
- Room heartbeat
- Central fleet-monitoring heartbeat
- Fleet, floor, and room command handling
- Periodic state sync
- Save point on command
- Restore after restart
- Deterministic thermal evolution
- Occupancy / light correlation
- Fault injection
- Fleet health warnings for silent nodes
