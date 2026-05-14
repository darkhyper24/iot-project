TLS material for MQTT (Python gmqtt) and HiveMQ broker keystore.

Generate a dev broker keystore (Java keytool required):

  ./scripts/gen_broker_keystore.sh

This writes:
  - broker.jks  (mount as /opt/hivemq/certs/broker.jks when using config/hivemq/conf/config.xml)
  - ca.crt      (simulator MQTT_TLS_CAFILE)
  - client.crt / client.key (optional mTLS to broker)

Do not commit secrets; add *.jks and private keys only via local generation or your PKI.

After generating `broker.jks`, activate TLS in HiveMQ by copying `config/hivemq/conf/config.with-tls.xml`
over `config/hivemq/conf/config.xml` and setting simulator env via `.env` (see `config/hivemq/README.txt`).
