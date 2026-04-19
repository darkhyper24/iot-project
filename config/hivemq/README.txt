Optional HiveMQ config overrides (Phase 2).

Mount custom broker config or ACL XML here and extend the hivemq service in docker-compose.yaml
with additional volume mappings (e.g. ./config/hivemq/conf -> /opt/hivemq/conf).

Default stack uses the stock hivemq/hivemq-ce image with persistent data in the hivemq-data volume.
