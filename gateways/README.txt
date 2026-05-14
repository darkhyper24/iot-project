Node-RED floor gateway data directories (Phase 2).

Each gateway-floor-XX service bind-mounts ./gateways/floor-XX to /data (Node-RED user data).

Host UI ports (container internal 1880):
  Floors 1–3:  http://localhost:1880  … 1882
  Floors 4–10: http://localhost:1890  … 1896

CoAP target for flows: simulator:5683 (UDP) on the Docker network.
