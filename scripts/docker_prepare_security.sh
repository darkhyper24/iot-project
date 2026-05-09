#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f config/certs/broker.jks || ! -f config/certs/ca.crt ]]; then
  echo "Generating HiveMQ TLS material..."
  scripts/gen_broker_keystore.sh
else
  echo "HiveMQ TLS material already exists."
fi

if [[ ! -f config/secrets/mqtt_nodes.json \
   || ! -f config/secrets/coap_psk.json \
   || ! -f config/secrets/system_clients.json \
   || ! -f config/hivemq/extensions/hivemq-file-rbac-extension/conf/credentials.xml ]]; then
  echo "Generating simulator, CoAP, HiveMQ, and system-client secrets..."
  python scripts/generate_campus_secrets.py
else
  echo "Campus secrets already exist."
fi
