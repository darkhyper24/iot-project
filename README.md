# IoT Campus Simulator

This repository contains a Dockerized campus IoT simulator for a 200-room building. It models rooms as independent asyncio tasks, publishes telemetry and heartbeat data over MQTT, and persists room state in Postgres so the simulation can recover after restart.

## Where To Start

- Run the project: [docker-compose.yaml](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/docker-compose.yaml)
- Main simulator entrypoint: [simulator/main.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/main.py)
- Manual command reference: [DOCKER_COMMANDS.md](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/DOCKER_COMMANDS.md)
- Step-by-step manual test flow: [MANUAL_TESTING.md](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/MANUAL_TESTING.md)

## Project Structure

### Top-level files

- [README.md](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/README.md)
  Repository overview and file map.

- [main.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/main.py)
  Thin launcher that starts the simulator package with `asyncio.run(...)`.

- [Dockerfile](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/Dockerfile)
  Builds the simulator container image.

- [docker-compose.yaml](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/docker-compose.yaml)
  Defines the full local stack:
  - `simulator`
  - `postgres`
  - `mqtt-broker`

- [requirements.txt](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/requirements.txt)
  Python dependencies used by the simulator.

- [.gitignore](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/.gitignore)
  Git ignore rules.

- [LICENSE](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/LICENSE)
  Project license file.

- [DOCKER_COMMANDS.md](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/DOCKER_COMMANDS.md)
  Copy/paste command reference for interacting with one room, one floor, or the whole fleet.

- [MANUAL_TESTING.md](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/MANUAL_TESTING.md)
  Full step-by-step manual testing guide for sections 1 and 2.

## `config/`

- [config/config.yaml](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/config/config.yaml)
  Main simulator configuration:
  - building size
  - simulation timing
  - thermal constants
  - MQTT settings
  - fault settings
  - heartbeat settings

- [config/mosquitto.conf](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/config/mosquitto.conf)
  Mosquitto broker configuration used by Docker Compose.

## `simulator/`

This folder contains the simulator application code.

- [simulator/__init__.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/__init__.py)
  Marks `simulator` as a Python package.

- [simulator/main.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/main.py)
  Main async application bootstrap. It:
  - loads config
  - connects to Postgres
  - connects to MQTT
  - initializes the world engine
  - handles shutdown

- [simulator/config.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/config.py)
  Loads YAML config and applies environment variable overrides.

- [simulator/physics.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/physics.py)
  Pure simulation logic helpers:
  - outside temperature
  - thermal leakage
  - HVAC effect
  - occupancy
  - light correlation
  - humidity updates

- [simulator/faults.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/faults.py)
  Fault injection logic for:
  - sensor drift
  - frozen sensor
  - telemetry delay
  - node dropout

## `simulator/models/`

- [simulator/models/__init__.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/models/__init__.py)
  Package marker.

- [simulator/models/room.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/models/room.py)
  Defines the `Room` model. Each room stores:
  - identity
  - environmental state
  - actuator state
  - telemetry serialization
  - heartbeat payload
  - command application
  - per-tick state updates

## `simulator/engine/`

- [simulator/engine/__init__.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/engine/__init__.py)
  Package marker.

- [simulator/engine/world_engine.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/engine/world_engine.py)
  Core orchestration layer. It:
  - creates the fleet of rooms
  - starts one asyncio task per room
  - publishes telemetry and heartbeat
  - runs periodic DB sync
  - tracks fleet health
  - handles drift compensation and startup jitter

- [simulator/engine/commands.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/engine/commands.py)
  Parses MQTT command messages and applies them to:
  - one room
  - one floor
  - the whole building

## `simulator/persistence/`

- [simulator/persistence/__init__.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/persistence/__init__.py)
  Package marker.

- [simulator/persistence/database.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/persistence/database.py)
  Async Postgres persistence layer. It handles:
  - DB connection
  - schema creation
  - loading room state on startup
  - periodic save points
  - command-triggered save points



## Typical Navigation

If you want to understand:

- Startup flow: [main.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/main.py) -> [simulator/main.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/main.py)
- Room behavior: [room.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/models/room.py) + [physics.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/physics.py) + [faults.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/faults.py)
- Fleet/task orchestration: [world_engine.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/engine/world_engine.py)
- MQTT command handling: [commands.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/engine/commands.py)
- Persistence: [database.py](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/simulator/persistence/database.py)
- Manual demo/testing commands: [DOCKER_COMMANDS.md](/run/media/enkea/New%20Volume/University/Senior/Second%20Semester/SWAPD%20453%20IOT/Project/iot-project/DOCKER_COMMANDS.md)
