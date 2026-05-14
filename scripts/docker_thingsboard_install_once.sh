#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="${TB_LOG_DIR:-/var/log/thingsboard}"
SUCCESS_TEXT="${TB_INSTALL_SUCCESS_TEXT:-Installation finished successfully!}"

if [ -d "$LOG_DIR" ]; then
  shopt -s nullglob
  install_logs=("$LOG_DIR"/install*.log)
  shopt -u nullglob

  if ((${#install_logs[@]} > 0)) && grep -Fq "$SUCCESS_TEXT" "${install_logs[@]}"; then
    echo "ThingsBoard installation already completed; skipping installer."
    exit 0
  fi
fi

echo "No completed ThingsBoard installation marker found; running installer."
exec start-tb-node.sh
