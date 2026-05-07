# IoT Campus Simulator

This repository contains a Dockerized campus IoT simulator for a 200-room building. It models rooms as independent asyncio tasks, publishes telemetry and heartbeat data over MQTT, and persists room state in Postgres so the simulation can recover after restart.

## Where To Start

- Run the project: [docker-compose.yaml](./docker-compose.yaml)
- Main simulator entrypoint: [simulator/app.py](./simulator/app.py)
- Manual command reference: [docs/docker_commands.md](./docs/docker_commands.md)
- Step-by-step manual test flow: [docs/manual_testing.md](./docs/manual_testing.md)

## Project Structure

### Top-level files

- [README.md](./README.md)
  Repository overview and file map.

- [simulator/__main__.py](./simulator/__main__.py)
  Package launcher used by `python -m simulator`.

- [Dockerfile](./Dockerfile)
  Builds the simulator container image.

- [docker-compose.yaml](./docker-compose.yaml)
  Local stack:
  - `postgres` — simulator database (host port **5433**)
  - `postgres-tb` — ThingsBoard database (internal only)
  - `hivemq` — HiveMQ Community Edition MQTT (**1883**)
  - `thingsboard` — ThingsBoard CE UI (**9090** → container 8080)
  - `simulator` — campus engine (MQTT to HiveMQ; CoAP UDP **5693** → 5683)
  - `gateway-floor-01` … `gateway-floor-10` — Node-RED (host **1880–1882**, **1890–1896**)

- [requirements.txt](./requirements.txt)
  Python dependencies used by the simulator.

- [.gitignore](./.gitignore)
  Git ignore rules.

- [LICENSE](./LICENSE)
  Project license file.

- [docs/docker_commands.md](./docs/docker_commands.md)
  Copy/paste command reference for interacting with one room, one floor, or the whole fleet.

- [docs/manual_testing.md](./docs/manual_testing.md)
  Full step-by-step manual testing guide for sections 1 and 2.

## `config/`

- [config/config.yaml](./config/config.yaml)
  Main simulator configuration:
  - building size
  - simulation timing
  - thermal constants
  - MQTT settings
  - fault settings
  - heartbeat settings

## `simulator/`

This folder contains the simulator application code.

- [simulator/**init**.py](./simulator/__init__.py)
  Marks `simulator` as a Python package.

- [simulator/app.py](./simulator/app.py)
  Main async application bootstrap. It:
  - loads config
  - connects to Postgres
  - connects to MQTT
  - initializes the world engine
  - handles shutdown

- [simulator/config/loader.py](./simulator/config/loader.py)
  Loads YAML config and applies environment variable overrides.

- [simulator/domain/physics.py](./simulator/domain/physics.py)
  Pure simulation logic helpers:
  - outside temperature
  - thermal leakage
  - HVAC effect
  - occupancy
  - light correlation
  - humidity updates

- [simulator/domain/faults.py](./simulator/domain/faults.py)
  Fault injection logic for:
  - sensor drift
  - frozen sensor
  - telemetry delay
  - node dropout

## `simulator/domain/`

- [simulator/domain/**init**.py](./simulator/domain/__init__.py)
  Package marker.

- [simulator/domain/room.py](./simulator/domain/room.py)
  Defines the `Room` model. Each room stores:
  - identity
  - environmental state
  - actuator state
  - telemetry serialization
  - heartbeat payload
  - command application
  - per-tick state updates

## `simulator/engine/`

- [simulator/engine/**init**.py](./simulator/engine/__init__.py)
  Package marker.

- [simulator/engine/world_engine.py](./simulator/engine/world_engine.py)
  Core orchestration layer. It:
  - creates the fleet of rooms
  - starts one asyncio task per room
  - publishes telemetry and heartbeat
  - runs periodic DB sync
  - tracks fleet health
  - handles drift compensation and startup jitter

- [simulator/engine/commands.py](./simulator/engine/commands.py)
  Parses MQTT command messages and applies them to:
  - one room
  - one floor
  - the whole building

## `simulator/persistence/`

- [simulator/persistence/**init**.py](./simulator/persistence/__init__.py)
  Package marker.

- [simulator/persistence/database.py](./simulator/persistence/database.py)
  Async Postgres persistence layer. It handles:
  - DB connection
  - schema creation
  - loading room state on startup
  - periodic save points
  - command-triggered save points

## Typical Navigation

If you want to understand:

- Startup flow: `python -m simulator` -> [simulator/app.py](./simulator/app.py)
- Room behavior: [room.py](./simulator/domain/room.py) + [physics.py](./simulator/domain/physics.py) + [faults.py](./simulator/domain/faults.py)
- Fleet/task orchestration: [world_engine.py](./simulator/engine/world_engine.py)
- MQTT command handling: [commands.py](./simulator/engine/commands.py)
- Persistence: [database.py](./simulator/persistence/database.py)
- Manual demo/testing commands: [docs/docker_commands.md](./docs/docker_commands.md)

## Performance Logging Script

Use `scripts/performance.sh` to run the stack and collect per-run logs under `perf_logs/run_<RUN_ID>/`.

**Run it:**

```bash
chmod +x scripts/performance.sh
./scripts/performance.sh
```

**What it produces:**

- `perf_logs/run_<RUN_ID>/simulator.log`
- `perf_logs/run_<RUN_ID>/latency.log`
- `perf_logs/run_<RUN_ID>/stats.log`
- `perf_logs/run_<RUN_ID>/run.info`
