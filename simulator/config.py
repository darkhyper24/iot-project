import os
import yaml


def load_config(path: str = "config/config.yaml") -> dict:
    with open(path, "r") as f:
        config = yaml.safe_load(f)

    # Environment variable overrides
    overrides = {
        ("database", "host"): "DB_HOST",
        ("database", "port"): ("DB_PORT", int),
        ("database", "user"): "DB_USER",
        ("database", "password"): "DB_PASSWORD",
        ("database", "dbname"): "DB_NAME",
        ("simulation", "tick_interval"): ("SIM_TICK_INTERVAL", float),
        ("simulation", "max_jitter"): ("SIM_MAX_JITTER", float),
        ("simulation", "db_sync_interval"): ("SIM_DB_SYNC_INTERVAL", float),
        ("simulation", "time_acceleration"): ("SIM_TIME_ACCELERATION", float),
        ("building", "floors"): ("SIM_FLOORS", int),
        ("building", "rooms_per_floor"): ("SIM_ROOMS_PER_FLOOR", int),
        ("mqtt", "broker_host"): "MQTT_BROKER_HOST",
        ("mqtt", "broker_port"): ("MQTT_BROKER_PORT", int),
        ("thermal", "alpha"): ("SIM_THERMAL_ALPHA", float),
        ("thermal", "beta"): ("SIM_THERMAL_BETA", float),
        ("thermal", "default_temp"): ("SIM_DEFAULT_TEMP", float),
        ("thermal", "outside_temp"): ("SIM_OUTSIDE_TEMP", float),
        ("thermal", "occupancy_heat"): ("SIM_OCCUPANCY_HEAT", float),
        ("thermal", "light_threshold"): ("SIM_LIGHT_THRESHOLD", int),
        ("faults", "probability"): ("SIM_FAULT_PROBABILITY", float),
        ("heartbeat", "interval"): ("SIM_HEARTBEAT_INTERVAL", int),
        ("heartbeat", "timeout"): ("SIM_HEARTBEAT_TIMEOUT", int),
    }

    for keys, env in overrides.items():
        if isinstance(env, tuple):
            env_name, cast = env
        else:
            env_name, cast = env, str

        value = os.environ.get(env_name)
        if value is not None:
            section, key = keys
            config[section][key] = cast(value)

    return config
