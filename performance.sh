#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="./perf_logs"
mkdir -p "$LOG_DIR"

docker compose up --build -d

# All three collection jobs run in parallel
docker compose logs -f simulator >> "$LOG_DIR/simulator.log" 2>&1 &
LOGS_PID=$!

docker compose logs -f simulator | grep -i latency > "$LOG_DIR/latency.log" 2>&1 &
LATENCY_PID=$!

# Stats loop for 30 minutes
END=$((SECONDS + 1800))
while [ $SECONDS -lt $END ]; do
    date +"%Y-%m-%d %H:%M:%S" >>  "$LOG_DIR/stats.log"
    docker stats --no-stream "$(docker compose ps -q simulator)" >> "$LOG_DIR/stats.log" 2>&1
    sleep 5
done

# Kill background log collectors after 30 minutes
kill $LOGS_PID $LATENCY_PID 2>/dev/null
echo "Done. Logs saved to $LOG_DIR"