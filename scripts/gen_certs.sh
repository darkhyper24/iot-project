#!/usr/bin/env bash
# Generates a project-local CA, a HiveMQ server cert, and bundles everything into
# config/certs/ (PEM + a JKS keystore consumable by HiveMQ CE). Idempotent: re-run
# overwrites the existing material so a fresh `docker compose up` stays consistent.
set -euo pipefail

CERT_DIR="$(cd "$(dirname "$0")/.." && pwd)/config/certs"
mkdir -p "$CERT_DIR"
cd "$CERT_DIR"

PASSWORD="changeit"
DAYS=3650
SUBJECT_CA="/C=EG/ST=Cairo/L=Cairo/O=SWAPD453/OU=Campus-IoT/CN=SWAPD453-Campus-Root-CA"
SUBJECT_SERVER="/C=EG/ST=Cairo/L=Cairo/O=SWAPD453/OU=Campus-IoT/CN=hivemq"

echo "==> Generating root CA"
openssl genrsa -out ca.key 4096
openssl req -x509 -new -nodes -key ca.key -sha256 -days "$DAYS" \
    -subj "$SUBJECT_CA" -out ca.crt

echo "==> Generating HiveMQ server key + CSR"
openssl genrsa -out hivemq.key 2048
openssl req -new -key hivemq.key -subj "$SUBJECT_SERVER" -out hivemq.csr

cat > hivemq_ext.cnf <<'EOF'
subjectAltName = @alt_names
extendedKeyUsage = serverAuth
[alt_names]
DNS.1 = hivemq
DNS.2 = localhost
IP.1  = 127.0.0.1
EOF

echo "==> Signing HiveMQ server cert with CA"
openssl x509 -req -in hivemq.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
    -out hivemq.crt -days "$DAYS" -sha256 -extfile hivemq_ext.cnf

echo "==> Building PKCS12 bundle for HiveMQ"
openssl pkcs12 -export -in hivemq.crt -inkey hivemq.key \
    -certfile ca.crt -name hivemq -out hivemq.p12 -passout "pass:$PASSWORD"

echo "==> Building JKS keystore (requires Java keytool)"
if command -v keytool >/dev/null 2>&1; then
    rm -f hivemq.jks
    keytool -importkeystore \
        -deststorepass "$PASSWORD" -destkeypass "$PASSWORD" \
        -destkeystore hivemq.jks -deststoretype JKS \
        -srckeystore hivemq.p12 -srcstoretype PKCS12 \
        -srcstorepass "$PASSWORD" -alias hivemq
else
    echo "   (keytool not found — skipping JKS; use the PKCS12 directly or run keytool inside the HiveMQ container)"
fi

rm -f hivemq.csr hivemq_ext.cnf ca.srl

echo "==> Done. Files in $CERT_DIR:"
ls -1 "$CERT_DIR"
