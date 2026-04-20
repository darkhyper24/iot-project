import os
import yaml


def _parse_bool(raw: str) -> bool:
    return raw.lower() in ("1", "true", "yes", "on")


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
        ("mqtt", "use_tls"): "MQTT_USE_TLS",
        ("mqtt", "tls_cafile"): "MQTT_TLS_CAFILE",
        ("mqtt", "tls_certfile"): "MQTT_TLS_CERTFILE",
        ("mqtt", "tls_keyfile"): "MQTT_TLS_KEYFILE",
        ("mqtt", "tls_check_hostname"): "MQTT_TLS_CHECK_HOSTNAME",
        ("mqtt", "username"): "MQTT_USERNAME",
        ("mqtt", "password"): "MQTT_PASSWORD",
        ("mqtt", "credentials_file"): "MQTT_CREDENTIALS_FILE",
        ("thermal", "alpha"): ("SIM_THERMAL_ALPHA", float),
        ("thermal", "beta"): ("SIM_THERMAL_BETA", float),
        ("thermal", "default_temp"): ("SIM_DEFAULT_TEMP", float),
        ("thermal", "outside_temp"): ("SIM_OUTSIDE_TEMP", float),
        ("thermal", "occupancy_heat"): ("SIM_OCCUPANCY_HEAT", float),
        ("thermal", "light_threshold"): ("SIM_LIGHT_THRESHOLD", int),
        ("faults", "probability"): ("SIM_FAULT_PROBABILITY", float),
        ("heartbeat", "interval"): ("SIM_HEARTBEAT_INTERVAL", int),
        ("heartbeat", "timeout"): ("SIM_HEARTBEAT_TIMEOUT", int),
        ("phase2", "mqtt_rooms_per_floor"): ("PHASE2_MQTT_ROOMS_PER_FLOOR", int),
        ("phase2", "mqtt_connect_stagger_s"): ("PHASE2_MQTT_STAGGER_S", float),
    }

    bool_keys = {
        ("mqtt", "use_tls"),
        ("mqtt", "tls_check_hostname"),
    }

    for keys, env in overrides.items():
        if isinstance(env, tuple):
            env_name, cast = env
        else:
            env_name, cast = env, str

        value = os.environ.get(env_name)
        if value is not None:
            section, key = keys
            if keys in bool_keys:
                config[section][key] = _parse_bool(value)
            elif isinstance(cast, type) and cast is not str:
                config[section][key] = cast(value)
            else:
                config[section][key] = cast(value)

    coap = dict(config.get("phase2", {}).get("coap") or {})
    if os.environ.get("COAP_BIND_HOST"):
        coap["bind_host"] = os.environ["COAP_BIND_HOST"]
    if os.environ.get("COAP_BIND_PORT"):
        coap["bind_port"] = int(os.environ["COAP_BIND_PORT"])
    if os.environ.get("COAP_DTLS_ENABLED") is not None:
        coap["dtls_enabled"] = _parse_bool(os.environ["COAP_DTLS_ENABLED"])
    if os.environ.get("COAP_PSK_IDENTITY"):
        coap["psk_identity"] = os.environ["COAP_PSK_IDENTITY"]
    if os.environ.get("COAP_PSK_HEX"):
        coap["psk_hex"] = os.environ["COAP_PSK_HEX"]
    if os.environ.get("COAP_PSK_FILE"):
        coap["psk_file"] = os.environ["COAP_PSK_FILE"]
    if os.environ.get("COAP_DTLS_BIND_HOST"):
        coap["dtls_bind_host"] = os.environ["COAP_DTLS_BIND_HOST"]
    if os.environ.get("COAP_DTLS_BIND_PORT"):
        coap["dtls_bind_port"] = int(os.environ["COAP_DTLS_BIND_PORT"])
    if coap and "phase2" in config:
        config["phase2"]["coap"] = coap

    return config
