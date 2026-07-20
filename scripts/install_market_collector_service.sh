#!/usr/bin/env bash
set -euo pipefail

# Installs the research-only market collector as a separate systemd service.
# It does NOT modify or restart the BitSwipe web server and contains no order execution.

REPO_ROOT="${1:-$(pwd)}"
SERVICE_USER="${2:-$(id -un)}"
SERVICE_GROUP="${3:-$(id -gn)}"
PYTHON_BIN="${4:-${REPO_ROOT}/.venv/bin/python}"
SERVICE_TEMPLATE="${REPO_ROOT}/deploy/bitswipe-market-collector.service"
SERVICE_TARGET="/etc/systemd/system/bitswipe-market-collector.service"
ENV_DIR="/etc/bitswipe"
ENV_FILE="${ENV_DIR}/market-collector.env"
STORE_ROOT="${REPO_ROOT}/data/arena/market_store"

if [[ ! -f "${SERVICE_TEMPLATE}" ]]; then
  echo "ERROR: service template not found: ${SERVICE_TEMPLATE}" >&2
  exit 1
fi
if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "ERROR: Python executable not found: ${PYTHON_BIN}" >&2
  echo "Create/activate the project virtualenv and install requirements first." >&2
  exit 1
fi

mkdir -p "${STORE_ROOT}"
mkdir -p /tmp/bitswipe-market-collector-install
TMP_SERVICE="/tmp/bitswipe-market-collector-install/bitswipe-market-collector.service"

sed \
  -e "s|__BITSWIPE_USER__|${SERVICE_USER}|g" \
  -e "s|__BITSWIPE_GROUP__|${SERVICE_GROUP}|g" \
  -e "s|__BITSWIPE_ROOT__|${REPO_ROOT}|g" \
  -e "s|__BITSWIPE_PYTHON__|${PYTHON_BIN}|g" \
  "${SERVICE_TEMPLATE}" > "${TMP_SERVICE}"

sudo mkdir -p "${ENV_DIR}"
if [[ ! -f "${ENV_FILE}" ]]; then
  cat <<EOF | sudo tee "${ENV_FILE}" >/dev/null
BITSWIPE_COLLECTOR_SYMBOL=BTCUSDT
BITSWIPE_MARKET_STORE_ROOT=${STORE_ROOT}
EOF
fi

sudo cp "${TMP_SERVICE}" "${SERVICE_TARGET}"
sudo systemctl daemon-reload
sudo systemctl enable bitswipe-market-collector.service

echo
printf '%s\n' "Collector service installed but NOT started yet." \
printf '%s\n' "Start:   sudo systemctl start bitswipe-market-collector" \
printf '%s\n' "Status:  sudo systemctl status bitswipe-market-collector --no-pager" \
printf '%s\n' "Logs:    journalctl -u bitswipe-market-collector -f" \
printf '%s\n' "Stop:    sudo systemctl stop bitswipe-market-collector" \
printf '%s\n' "Data:    ${STORE_ROOT}" \
printf '%s\n' "Web server was not modified or restarted."
