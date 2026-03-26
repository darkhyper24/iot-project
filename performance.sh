#!/usr/bin/env bash
set -euo pipefail

RUN_ID="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="./perf_logs/run_${RUN_ID}"
mkdir -p "$LOG_DIR"

echo "Run ID: $RUN_ID" | tee "$LOG_DIR/run.info"

# ── Clean start ───────────────────────────────────────────────────────────────
docker compose down -v
docker compose up --build -d

echo "Waiting 35s for stack to stabilise and first DB sync to run..."
sleep 35

# ── Background: full simulator log ───────────────────────────────────────────
docker compose logs -f simulator >> "$LOG_DIR/simulator.log" 2>&1 &
LOGS_PID=$!

# ── Background: latency warnings only ────────────────────────────────────────
docker compose logs -f simulator | grep -i latency >> "$LOG_DIR/latency.log" 2>&1 &
LATENCY_PID=$!

# ── Background: fleet health warnings only ───────────────────────────────────
docker compose logs -f simulator | grep fleet_health_warning >> "$LOG_DIR/fleet_health.log" 2>&1 &
FLEET_PID=$!

# ── Background: telemetry for one room (floor 1, room 101) ───────────────────
docker compose exec -T mqtt-broker sh -lc \
  "mosquitto_sub -t 'campus/bldg_01/floor_01/room_101/telemetry'" \
  >> "$LOG_DIR/room101_telemetry.log" 2>&1 &
TELEM_PID=$!

# ── Background: per-room heartbeat ───────────────────────────────────────────
docker compose exec -T mqtt-broker sh -lc \
  "mosquitto_sub -t 'campus/bldg_01/floor_01/room_101/heartbeat'" \
  >> "$LOG_DIR/room101_heartbeat.log" 2>&1 &
HB_PID=$!

# ── Background: central fleet-monitoring heartbeat ───────────────────────────
docker compose exec -T mqtt-broker sh -lc \
  "mosquitto_sub -t 'campus/bldg_01/fleet_monitoring/heartbeat'" \
  >> "$LOG_DIR/fleet_heartbeat.log" 2>&1 &
FLEET_HB_PID=$!

# ── Background: faulty telemetry across whole fleet ──────────────────────────
docker compose exec -T mqtt-broker sh -lc \
  "mosquitto_sub -t 'campus/+/+/+/telemetry'" \
  | grep -v '"fault": "none"' >> "$LOG_DIR/faults.log" 2>&1 &
FAULT_PID=$!

# ── Background: occupied rooms across whole fleet ────────────────────────────
docker compose exec -T mqtt-broker sh -lc \
  "mosquitto_sub -t 'campus/+/+/+/telemetry'" \
  | grep '"occupancy": true' >> "$LOG_DIR/occupied.log" 2>&1 &
OCC_PID=$!

# ── Snapshot: verify DB has 200 rooms ────────────────────────────────────────
echo "--- DB room count ---" >> "$LOG_DIR/db_snapshots.log"
docker compose exec -T postgres psql -U iot_user -d iot_campus \
  -c "SELECT COUNT(*) AS room_count FROM room_states;" \
  >> "$LOG_DIR/db_snapshots.log" 2>&1

# ── Snapshot: verify floor 5 has 20 rooms ────────────────────────────────────
echo "--- Floor 05 count ---" >> "$LOG_DIR/db_snapshots.log"
docker compose exec -T postgres psql -U iot_user -d iot_campus \
  -c "SELECT COUNT(*) AS floor_05_count FROM room_states WHERE room_id LIKE 'b01-f05-%';" \
  >> "$LOG_DIR/db_snapshots.log" 2>&1

# ── Command: room-level ───────────────────────────────────────────────────────
echo "Sending room-level command to floor_01/room_101..."
docker compose exec -T mqtt-broker sh -lc \
  "mosquitto_pub -t 'campus/bldg_01/floor_01/room_101/command' \
   -m '{\"hvac_mode\":\"ON\",\"target_temp\":26,\"lighting_dimmer\":80}'"
sleep 3

echo "--- Room 101 after room command ---" >> "$LOG_DIR/db_snapshots.log"
docker compose exec -T postgres psql -U iot_user -d iot_campus \
  -c "SELECT room_id, hvac_mode, target_temp FROM room_states WHERE room_id = 'b01-f01-r101';" \
  >> "$LOG_DIR/db_snapshots.log" 2>&1

# ── Command: floor-level ──────────────────────────────────────────────────────
echo "Sending floor-level command to floor_02..."
docker compose exec -T mqtt-broker sh -lc \
  "mosquitto_pub -t 'campus/bldg_01/floor_02/command' \
   -m '{\"hvac_mode\":\"ECO\",\"target_temp\":24}'"
sleep 3

echo "--- Floor 02 after floor command ---" >> "$LOG_DIR/db_snapshots.log"
docker compose exec -T postgres psql -U iot_user -d iot_campus \
  -c "SELECT COUNT(*) AS floor_2_updated FROM room_states \
      WHERE room_id LIKE 'b01-f02-%' AND hvac_mode = 'ECO' AND target_temp = 24;" \
  >> "$LOG_DIR/db_snapshots.log" 2>&1

# ── Command: building-level ───────────────────────────────────────────────────
echo "Sending building-level command..."
docker compose exec -T mqtt-broker sh -lc \
  "mosquitto_pub -t 'campus/bldg_01/command' \
   -m '{\"hvac_mode\":\"OFF\",\"target_temp\":22}'"
sleep 3

echo "--- Building after building command ---" >> "$LOG_DIR/db_snapshots.log"
docker compose exec -T postgres psql -U iot_user -d iot_campus \
  -c "SELECT COUNT(*) AS building_reset FROM room_states \
      WHERE hvac_mode = 'OFF' AND target_temp = 22;" \
  >> "$LOG_DIR/db_snapshots.log" 2>&1

# ── Restart test ──────────────────────────────────────────────────────────────
echo "Testing persistence across simulator restart..."
docker compose exec -T mqtt-broker sh -lc \
  "mosquitto_pub -t 'campus/bldg_01/floor_01/room_101/command' \
   -m '{\"hvac_mode\":\"ON\",\"target_temp\":26}'"
sleep 3

echo "--- Room 101 BEFORE restart ---" >> "$LOG_DIR/db_snapshots.log"
docker compose exec -T postgres psql -U iot_user -d iot_campus \
  -c "SELECT room_id, hvac_mode, target_temp, last_temp, last_humidity, last_update \
      FROM room_states WHERE room_id = 'b01-f01-r101';" \
  >> "$LOG_DIR/db_snapshots.log" 2>&1

docker compose restart simulator
sleep 10

echo "--- Room 101 AFTER restart ---" >> "$LOG_DIR/db_snapshots.log"
docker compose exec -T postgres psql -U iot_user -d iot_campus \
  -c "SELECT room_id, hvac_mode, target_temp, last_temp, last_humidity, last_update \
      FROM room_states WHERE room_id = 'b01-f01-r101';" \
  >> "$LOG_DIR/db_snapshots.log" 2>&1

echo "--- One telemetry message after restart ---" >> "$LOG_DIR/db_snapshots.log"
docker compose exec -T mqtt-broker sh -lc \
  "timeout 5 mosquitto_sub -t 'campus/bldg_01/floor_01/room_101/telemetry' -C 1" \
  >> "$LOG_DIR/db_snapshots.log" 2>&1

# ── Stats loop for remaining time ────────────────────────────────────────────
END=$((SECONDS + 1800))
while [ $SECONDS -lt $END ]; do
    printf "Run ID: %s | %s\n" "$RUN_ID" "$(date +"%Y-%m-%d %H:%M:%S")" >> "$LOG_DIR/stats.log"
    docker stats --no-stream "$(docker compose ps -q simulator)" >> "$LOG_DIR/stats.log" 2>&1
    sleep 5
done

# ── Cleanup ───────────────────────────────────────────────────────────────────
kill $LOGS_PID $FLEET_PID $TELEM_PID $HB_PID $FLEET_HB_PID $FAULT_PID $OCC_PID 2>/dev/null
echo "Done. Logs saved to $LOG_DIR"