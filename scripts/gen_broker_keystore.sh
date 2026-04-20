#!/usr/bin/env bash
# Generate self-signed TLS material + broker.jks for HiveMQ tls-tcp-listener on 8883.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/config/certs"
mkdir -p "$OUT"
cd "$OUT"

PASS="${HIVEMQ_KEYSTORE_PASS:-changeit}"

openssl req -x509 -newkey rsa:2048 \
  -keyout server.key -out server.crt -days 825 -nodes \
  -subj "/CN=hivemq"

openssl pkcs12 -export \
  -in server.crt -inkey server.key -out server.p12 \
  -password "pass:${PASS}" -name broker

keytool -importkeystore -noprompt \
  -srckeystore server.p12 -srcstoretype PKCS12 -srcstorepass "${PASS}" \
  -destkeystore broker.jks -deststoretype JKS -deststorepass "${PASS}"

cp server.crt ca.crt

echo "Wrote ${OUT}/broker.jks (password ${PASS}), ca.crt, server.key"
echo "Next: cp config/hivemq/conf/config.with-tls.xml config/hivemq/conf/config.xml"
echo "Set .env: MQTT_USE_TLS=true MQTT_BROKER_PORT=8883 MQTT_TLS_CHECK_HOSTNAME=false"
echo "Then: docker compose up --build"
