# Phase 3 End-to-End Testing Guide

This guide verifies Phase 3 from a fresh local stack through ThingsBoard, Node-RED, HiveMQ, the simulator, OTA updates, tamper detection, and dashboard data readiness.

Run all commands from the repository root, where `docker-compose.yaml` is located.

## 0. Prerequisites

Required host tools:

```bash
python --version
docker compose version
jq --version
mosquitto_pub --help
mosquitto_sub --help
```

Recommended Python version:

```txt
Python 3.11 or 3.12
```

If `mosquitto_pub` or `mosquitto_sub` is missing on Arch/Manjaro:

```bash
sudo pacman -S mosquitto
```

Install Python dependencies only if you plan to run host-side tests or helper scripts directly:

```bash
python -m pip install -r requirements.txt
```

## 1. Start the Stack

From a fresh checkout, Docker now prepares the generated secrets/certificates, initializes ThingsBoard, seeds the project entities, and regenerates the Node-RED gateway credentials:

```bash
docker compose up -d --build
```

Expected generated files after the startup bootstrap:

```txt
config/certs/ca.crt
config/certs/broker.jks
config/secrets/mqtt_nodes.json
config/secrets/coap_psk.json
config/secrets/system_clients.json
config/hivemq/extensions/hivemq-file-rbac-extension/conf/credentials.xml
```

## 2. Check Startup

Check service status, including one-shot bootstrap containers:

```bash
docker compose ps --all
```

Expected:

```txt
postgres                   healthy
postgres-tb                healthy
hivemq                     up
thingsboard                up, port 9090 mapped
simulator                  up
thingsboard-install        exited 0
thingsboard-bootstrap      exited 0
docker-prepare-security    exited 0
gateway-floor-01           up
...
gateway-floor-10           up
```

Watch logs in a second terminal:

```bash
docker compose logs -f simulator gateway-floor-01 thingsboard-bootstrap thingsboard
```

Expected bootstrap indicators:

```txt
Generating HiveMQ TLS material...        # or: HiveMQ TLS material already exists.
Generating simulator, CoAP... secrets... # or: Campus secrets already exist.
ThingsBoard API is ready
Running ... scripts/seed_thingsboard.py
Running ... scripts/generate_nodered_flows.py
Running ... scripts/seed_thingsboard_dashboard.py
ThingsBoard entities, gateway flows, and Phase 3 dashboard are ready.
```

To skip dashboard auto-seeding (useful when iterating manually in the UI):

```bash
TB_BOOTSTRAP_DASHBOARD=false docker compose up -d --build
```

Expected simulator indicators:

```txt
Fleet initialized
World engine running
Twin reporter connected
Desired-state subscriber connected
OTA subscriber connected
Broad-command fanout connected
```

Expected gateway indicators:

```txt
Connected to broker: nodered-gw-f01@mqtt://hivemq:1883
Connected to broker: tb-gw-f01@mqtt://thingsboard:1883
```

## 3. ThingsBoard Login

Open:

```txt
http://localhost:9090
```

The default Docker bootstrap uses ThingsBoard demo data, so the tenant login is:

```txt
tenant@thingsboard.org
tenant
```

If you already have an existing ThingsBoard DB and want to seed with your own tenant admin, start the stack with overrides:

```bash
TB_BOOTSTRAP_USERNAME=admin@gmail.com TB_BOOTSTRAP_PASSWORD=admins docker compose up -d --build
```

For a completely clean DB reset:

```bash
docker compose down -v
docker compose up -d --build
```

## 4. ThingsBoard Seeding

This is now automatic during `thingsboard-bootstrap`.

Expected seed output in logs:

```txt
MQTT device profile id: ...
CoAP  device profile id: ...
Assets created/updated.
Asset relations created/updated.
Floor aggregate + security devices created/updated and linked.
Devices created/updated and linked to rooms.
Done.
```

This creates:

```txt
Campus asset
Building asset
10 Floor assets
200 Room assets
200 Room devices
10 Floor aggregate devices
1 Security device
Room server attributes for Phase 3 spatial metadata
```

## 5. Node-RED Gateway Flows

Gateway flow regeneration is also automatic during `thingsboard-bootstrap`.

Expected gateway logs:

```bash
docker compose logs --tail=80 gateway-floor-01
```

Look for:

```txt
Started flows
Connected to broker: nodered-gw-f01@mqtt://hivemq:1883
Connected to broker: tb-gw-f01@mqtt://thingsboard:1883
```

Manual re-run, only if you intentionally changed ThingsBoard devices or gateway flow generation:

```bash
docker compose run --rm thingsboard-bootstrap
```

Or with a custom tenant admin:

```bash
TB_BOOTSTRAP_USERNAME=admin@gmail.com TB_BOOTSTRAP_PASSWORD=admins docker compose run --rm thingsboard-bootstrap
```

## 6. Generate Floor Plan Assets

```bash
python scripts/build_floor_plans.py
```

Expected files:

```txt
thingsboard/assets/floor_plans/floor-01.svg
...
thingsboard/assets/floor_plans/floor-10.svg
```

## 7. Prepare ThingsBoard API Helpers

Use these in a terminal for the remaining API checks:

```bash
export TB_URL=http://localhost:9090
export TB_USER=tenant@thingsboard.org
export TB_PASS=tenant

export TB_TOKEN=$(curl -s -X POST "$TB_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$TB_USER\",\"password\":\"$TB_PASS\"}" | jq -r .token)

dev_id () {
  curl -s "$TB_URL/api/tenant/devices?pageSize=500&page=0&textSearch=$1" \
    -H "X-Authorization: Bearer $TB_TOKEN" \
  | jq -r --arg n "$1" '.data[] | select(.name==$n) | .id.id'
}

asset_id () {
  curl -s "$TB_URL/api/tenant/assets?pageSize=500&page=0&textSearch=$1" \
    -H "X-Authorization: Bearer $TB_TOKEN" \
  | jq -r --arg n "$1" '.data[] | select(.name==$n) | .id.id'
}
```

Confirm login:

```bash
echo "$TB_TOKEN" | wc -c
```

Expected: a non-zero token length, usually hundreds of characters.

## 8. Test Digital Twin Hierarchy and Room Metadata

Check that all 200 room devices exist:

```bash
curl -s "$TB_URL/api/tenant/devices?pageSize=500&page=0" \
  -H "X-Authorization: Bearer $TB_TOKEN" \
| jq '[.data[].name | select(test("^b01-f[0-9]{2}-r[0-9]{3}$"))] | length'
```

Expected:

```txt
200
```

Check one room asset's Phase 3 server attributes:

```bash
RID=$(asset_id b01-f01-r001)

curl -s "$TB_URL/api/plugins/telemetry/ASSET/$RID/values/attributes/SERVER_SCOPE" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

Expected keys:

```txt
square_footage
occupant_capacity
coordinates_x
coordinates_y
room_type
```

Repeat for a CoAP-backed room:

```bash
RID=$(asset_id b01-f01-r020)

curl -s "$TB_URL/api/plugins/telemetry/ASSET/$RID/values/attributes/SERVER_SCOPE" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

Expected: same metadata keys.

## 9. Test Telemetry Ingestion for MQTT and CoAP Rooms

Wait at least 30 seconds after the simulator and gateways are running.

MQTT-backed room:

```bash
D=$(dev_id b01-f01-r001)

curl -s "$TB_URL/api/plugins/telemetry/DEVICE/$D/values/timeseries?keys=temperature,humidity,occupancy,light_level" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

CoAP-backed room:

```bash
D=$(dev_id b01-f01-r020)

curl -s "$TB_URL/api/plugins/telemetry/DEVICE/$D/values/timeseries?keys=temperature,humidity,occupancy,light_level" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

Expected for both:

```txt
temperature
humidity
occupancy
light_level
```

Each key should have a recent timestamp and value.

## 10. Test Client Attributes / Reported State

MQTT-backed room:

```bash
D=$(dev_id b01-f01-r001)

curl -s "$TB_URL/api/plugins/telemetry/DEVICE/$D/values/attributes/CLIENT_SCOPE" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

CoAP-backed room:

```bash
D=$(dev_id b01-f01-r020)

curl -s "$TB_URL/api/plugins/telemetry/DEVICE/$D/values/attributes/CLIENT_SCOPE" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

Expected keys:

```txt
hvac_mode_reported
lighting_dimmer_reported
target_temp_reported
current_version
last_seen
```

`last_seen` should refresh periodically.

## 11. Test Desired State Sync

Set shared desired attributes on an MQTT-backed room:

```bash
D=$(dev_id b01-f01-r001)

curl -s -X POST "$TB_URL/api/plugins/telemetry/DEVICE/$D/attributes/SHARED_SCOPE" \
  -H "X-Authorization: Bearer $TB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hvac_mode_desired":"ON","lighting_dimmer_desired":80,"target_temp_desired":24}'
```

The POST returns an empty body on success.

Verify shared attrs landed:

```bash
curl -s "$TB_URL/api/plugins/telemetry/DEVICE/$D/values/attributes/SHARED_SCOPE" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

Wait a few seconds, then verify reported attrs:

```bash
curl -s "$TB_URL/api/plugins/telemetry/DEVICE/$D/values/attributes/CLIENT_SCOPE" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

Expected:

```txt
hvac_mode_reported = ON
lighting_dimmer_reported = 80
target_temp_reported = 24
```

Repeat for a CoAP-backed room:

```bash
D=$(dev_id b01-f01-r020)

curl -s -X POST "$TB_URL/api/plugins/telemetry/DEVICE/$D/attributes/SHARED_SCOPE" \
  -H "X-Authorization: Bearer $TB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"hvac_mode_desired":"ECO","lighting_dimmer_desired":40,"target_temp_desired":22}'

sleep 5

curl -s "$TB_URL/api/plugins/telemetry/DEVICE/$D/values/attributes/CLIENT_SCOPE" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

Expected:

```txt
hvac_mode_reported = ECO
lighting_dimmer_reported = 40
target_temp_reported = 22
```

This proves desired/reported shadow sync works for both MQTT and CoAP-backed rooms.

## 12. Test Floor Aggregation

Check floor 1 aggregate device:

```bash
FD=$(dev_id b01-f01-floor)

curl -s "$TB_URL/api/plugins/telemetry/DEVICE/$FD/values/timeseries?keys=avg_temperature,room_sample_count" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

Expected:

```txt
avg_temperature
room_sample_count
```

After all 20 rooms on the floor have reported recently, expected:

```txt
room_sample_count = 20
```

## 13. Test Broad Building Command

Read the campus observer password:

```bash
OBS_PASS=$(jq -r 'to_entries[] | select(.value.user=="campus_observer") | .value.password' gateways/floor-01/flows_cred.json)
```

Publish a building-wide command:

```bash
mosquitto_pub -h localhost -p 8883 --cafile config/certs/ca.crt --insecure \
  -u campus_observer -P "$OBS_PASS" \
  -t campus/b01/cmd \
  -m '{"hvac_mode":"ECO","target_temp":23,"cmd_id":"building-eco-001"}'
```

Verify in Postgres:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus \
  -c "SELECT hvac_mode, COUNT(*) FROM room_states GROUP BY hvac_mode ORDER BY hvac_mode;"
```

Expected:

```txt
ECO | 200
```

If not all rows update immediately, wait for the simulator sync interval and retry.

## 14. Test Broad Floor Command

Publish a floor 5 command:

```bash
mosquitto_pub -h localhost -p 8883 --cafile config/certs/ca.crt --insecure \
  -u campus_observer -P "$OBS_PASS" \
  -t campus/b01/f05/cmd \
  -m '{"hvac_mode":"ON","target_temp":21,"cmd_id":"floor-05-on-001"}'
```

Verify floor 5:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus \
  -c "SELECT hvac_mode, COUNT(*) FROM room_states WHERE room_id LIKE 'b01-f05-%' GROUP BY hvac_mode ORDER BY hvac_mode;"
```

Expected:

```txt
ON | 20
```

Verify another floor was not changed by this floor command:

```bash
docker compose exec -T postgres psql -U iot_user -d iot_campus \
  -c "SELECT hvac_mode, COUNT(*) FROM room_states WHERE room_id LIKE 'b01-f04-%' GROUP BY hvac_mode ORDER BY hvac_mode;"
```

Expected: floor 4 remains whatever it was before the floor 5 command.

## 15. Test OTA Happy Path

Publish an OTA update to floor 5:

```bash
python scripts/ota_publish.py --scope floor --floor 5 --version 2 --alpha 0.02
```

Check simulator logs:

```bash
docker compose logs --tail=120 simulator | grep OTA
```

Expected:

```txt
OTA applied
floor=5
rooms=20
version=2
```

Verify an MQTT-backed floor 5 room:

```bash
D=$(dev_id b01-f05-r001)

curl -s "$TB_URL/api/plugins/telemetry/DEVICE/$D/values/attributes/CLIENT_SCOPE" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

Expected:

```txt
current_version = 2
```

Verify a CoAP-backed floor 5 room:

```bash
D=$(dev_id b01-f05-r020)

curl -s "$TB_URL/api/plugins/telemetry/DEVICE/$D/values/attributes/CLIENT_SCOPE" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

Expected:

```txt
current_version = 2
```

Verify another floor did not receive the floor 5 OTA:

```bash
D=$(dev_id b01-f04-r001)

curl -s "$TB_URL/api/plugins/telemetry/DEVICE/$D/values/attributes/CLIENT_SCOPE" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

Expected:

```txt
current_version is still the previous version
```

## 16. Test OTA Tamper Detection

Publish a deliberately bad OTA payload:

```bash
python scripts/ota_publish.py --scope room --floor 1 --room 101 --version 99 --alpha 0.04 --tamper
```

Check simulator logs:

```bash
docker compose logs --tail=100 simulator | grep -E "OTA hash MISMATCH|Tamper"
```

Expected:

```txt
OTA hash MISMATCH topic=campus/b01/f01/r101/ota/config
Tamper alert published
```

Verify ThingsBoard received the tamper telemetry:

```bash
SD=$(dev_id b01-security)

curl -s "$TB_URL/api/plugins/telemetry/DEVICE/$SD/values/timeseries?keys=ota_tamper,source_topic,actual_sha256,expected_sha256,client_id" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

Expected:

```txt
ota_tamper = true
source_topic = campus/b01/f01/r101/ota/config
expected_sha256
actual_sha256
client_id
```

Verify the bad OTA did not bump the target room to version 99:

```bash
D=$(dev_id b01-f01-r001)

curl -s "$TB_URL/api/plugins/telemetry/DEVICE/$D/values/attributes/CLIENT_SCOPE" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

Expected:

```txt
current_version is not 99
```

## 17. Test ThingsBoard UI Data Directly

Open:

```txt
http://localhost:9090
```

Login as:

```txt
tenant@thingsboard.org
tenant
```

### Room Asset Metadata

Navigate:

```txt
Entities -> Assets -> search b01-f01-r001 -> Attributes -> Server attributes
```

Expected:

```txt
square_footage
occupant_capacity
coordinates_x
coordinates_y
room_type
```

### Room Telemetry

Navigate:

```txt
Entities -> Devices -> search b01-f01-r001 -> Latest telemetry
```

Expected:

```txt
temperature
humidity
occupancy
light_level
```

### Room Client Attributes

Navigate:

```txt
Entities -> Devices -> search b01-f01-r001 -> Attributes -> Client attributes
```

Expected:

```txt
hvac_mode_reported
lighting_dimmer_reported
target_temp_reported
current_version
last_seen
```

### Shared Attribute Control

Navigate:

```txt
Entities -> Devices -> b01-f01-r001 -> Attributes -> Shared attributes
```

Set:

```txt
hvac_mode_desired = ON
lighting_dimmer_desired = 80
target_temp_desired = 24
```

Then return to:

```txt
Attributes -> Client attributes
```

Expected after a few seconds:

```txt
hvac_mode_reported = ON
lighting_dimmer_reported = 80
target_temp_reported = 24
```

### Floor Aggregation

Navigate:

```txt
Entities -> Devices -> search b01-f01-floor -> Latest telemetry
```

Expected:

```txt
avg_temperature
room_sample_count
```

### OTA Tamper Telemetry

Navigate:

```txt
Entities -> Devices -> search b01-security -> Latest telemetry
```

Expected after a tamper test:

```txt
ota_tamper
source_topic
expected_sha256
actual_sha256
client_id
```

## 18. Dashboard / Image Map Check

The dashboard is now built automatically by `scripts/seed_thingsboard_dashboard.py`
during `thingsboard-bootstrap`. It creates a single dashboard titled
`Campus Phase 3 Digital Twin` with one overview state plus ten floor states,
backed by the floor SVGs in `thingsboard/assets/floor_plans/`.

### Bootstrap verification

Confirm the dashboard exists and has the expected structure:

```bash
DASH_ID=$(curl -s "$TB_URL/api/tenant/dashboards?pageSize=200&page=0" \
  -H "X-Authorization: Bearer $TB_TOKEN" \
  | jq -r '.data[] | select(.title=="Campus Phase 3 Digital Twin") | .id.id')

echo "$DASH_ID"

curl -s "$TB_URL/api/dashboard/$DASH_ID" \
  -H "X-Authorization: Bearer $TB_TOKEN" \
  | jq '.configuration | {states: (.states|keys|length), aliases: (.entityAliases|length), widgets: (.widgets|length)}'
```

Expected:

```txt
states  = 11        # default + floor-01..floor-10
aliases >= 12       # all-rooms + floor-aggregates + (security?) + 10 per-floor
widgets >= 35
```

Confirm one room device has the dashboard metadata used by the Image Map:

```bash
D=$(dev_id b01-f01-r001)

curl -s "$TB_URL/api/plugins/telemetry/DEVICE/$D/values/attributes/SERVER_SCOPE" \
  -H "X-Authorization: Bearer $TB_TOKEN" | jq
```

Expected keys: `map_x`, `map_y`, `room_type`, `floor_no`, `room_on_floor`.
Both `map_x` and `map_y` must be in the range `0..1`.

### Optional manual UI walk-through

In ThingsBoard UI:

1. Open `Dashboards -> Campus Phase 3 Digital Twin`.
2. Default state shows the fleet table, floor-aggregate table, floor average chart,
   and (when populated) the b01-security tamper panel. An empty security panel is
   normal unless an OTA tamper test was run recently.
3. Switch state via the floor selector to `Floor 01` ... `Floor 10` to inspect
   the Image Map. Markers are colored by current temperature and tooltips show
   `temperature`, `occupancy`, `hvac_mode_reported`, and `last_seen`.
4. To change a room interactively, open the device detail page and write the
   shared attrs `hvac_mode_desired`, `lighting_dimmer_desired`, `target_temp_desired`.
   Reported values converge within a few seconds.

If your ThingsBoard CE build ships different widget FQNs and a widget renders as
"Unknown widget", reseed the dashboard with the env var override (or open the
dashboard JSON via the dashboard import/export menu and adjust widget bundles to
the names available in your instance):

```bash
TB_BOOTSTRAP_DASHBOARD=false docker compose up -d --build
docker compose run --rm thingsboard-bootstrap
```

## 19. Run Automated Tests

Unit tests:

```bash
python -m pytest -q tests/test_id_mapping.py tests/test_ota_hash.py tests/test_command_scopes.py
```

Optional ThingsBoard ingestion test:

```bash
RUN_TB_INGEST_TEST=1 \
TB_URL=http://localhost:9090 \
TB_USER=tenant@thingsboard.org \
TB_PASS=tenant \
python -m pytest -q tests/test_gateway_ingest.py
```

Optional dashboard integration test (verifies the auto-seeded dashboard and the
device-level map attributes against the running stack):

```bash
RUN_TB_DASHBOARD_TEST=1 \
TB_URL=http://localhost:9090 \
TB_USER=tenant@thingsboard.org \
TB_PASS=tenant \
python -m pytest -q tests/test_dashboard_integration.py
```

Pure unit tests for the dashboard generator (no stack required):

```bash
python -m pytest -q tests/test_dashboard_generation.py
```

Expected:

```txt
passed
```

## 20. Pass/Fail Checklist

Phase 3 is passing when all of these are true:

- `200` room devices exist in ThingsBoard.
- Room assets have `square_footage`, `occupant_capacity`, `coordinates_x`, `coordinates_y`, and `room_type`.
- MQTT room `b01-f01-r001` has live telemetry in ThingsBoard.
- CoAP room `b01-f01-r020` has live telemetry in ThingsBoard.
- Both MQTT and CoAP rooms publish client attributes.
- Shared desired attrs converge to reported attrs.
- Broad building command reaches `200` rooms.
- Broad floor command reaches only that floor's `20` rooms.
- Floor aggregate devices publish `avg_temperature`.
- OTA floor update changes `current_version` for exactly that floor.
- Tampered OTA is rejected and produces `b01-security` telemetry.
- Floor plan SVGs exist for floors 1-10.
- Dashboard data dependencies are visible in ThingsBoard UI.
- Dashboard `Campus Phase 3 Digital Twin` exists with 11 states.
- Room devices carry `map_x`, `map_y`, `room_type`, `floor_no`, `room_on_floor` SERVER_SCOPE attrs.
