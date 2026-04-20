Generated secrets (gitignored) live here.

1. Generate MQTT passwords, CoAP PSKs, and HiveMQ `credentials.xml`:

   python scripts/generate_campus_secrets.py

2. Files created:
   - mqtt_nodes.json — per MQTT-room username/password for the simulator
   - coap_psk.json — per CoAP-room PSK identity and key (hex)
   - ../hivemq/extensions/hivemq-file-rbac-extension/conf/credentials.xml — HiveMQ File RBAC ACLs

3. The script also adds a HiveMQ user `campus_observer` (password printed once to stdout) with a broad `campus/b01/#` role for host testing and wildcards — not used by the simulator.

4. Do not commit these files; rotate for production.
