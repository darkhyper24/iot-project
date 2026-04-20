"""Build SSL context for gmqtt TLS connections to HiveMQ."""

from __future__ import annotations

import logging
import ssl
from pathlib import Path

logger = logging.getLogger(__name__)


def ssl_context_from_config(config: dict) -> ssl.SSLContext | None:
    mqtt = config.get("mqtt", {})
    if not mqtt.get("use_tls"):
        return None

    cafile = mqtt.get("tls_cafile")
    certfile = mqtt.get("tls_certfile")
    keyfile = mqtt.get("tls_keyfile")

    ctx = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    if cafile:
        ca_path = Path(cafile)
        if not ca_path.is_file():
            logger.error(
                "MQTT TLS is enabled but CA file is missing at %s. "
                "Run scripts/gen_broker_keystore.sh or set mqtt.tls_cafile.",
                ca_path,
            )
        else:
            ctx.load_verify_locations(cafile=str(ca_path))
    else:
        logger.error(
            "MQTT TLS is enabled but mqtt.tls_cafile is empty; set it to your broker CA (e.g. config/certs/ca.crt).",
        )

    if certfile and keyfile:
        cf, kf = Path(certfile), Path(keyfile)
        if cf.is_file() and kf.is_file():
            ctx.load_cert_chain(certfile=str(cf), keyfile=str(kf))
        else:
            logger.warning("TLS client cert/key missing; skipping client cert auth")

    chk = mqtt.get("tls_check_hostname", True)
    if isinstance(chk, str):
        chk = chk.lower() in ("1", "true", "yes", "on")
    ctx.check_hostname = bool(chk)
    return ctx
