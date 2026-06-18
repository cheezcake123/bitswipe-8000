#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/bitswipe}"
SERVICE_NAME="${SERVICE_NAME:-bitswipe}"
APP_PORT="${APP_PORT:-8000}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
INSTALL_TAILSCALE="${INSTALL_TAILSCALE:-1}"
CONFIGURE_UFW="${CONFIGURE_UFW:-1}"

msg() {
  printf '\n==> %s\n' "$*"
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

if ! command -v sudo >/dev/null 2>&1; then
  fail "sudo is required."
fi

if [ ! -f "${APP_DIR}/requirements.txt" ]; then
  fail "requirements.txt not found in ${APP_DIR}. Upload or clone the app first."
fi

if [ ! -f "${APP_DIR}/.env" ]; then
  if [ -f "${APP_DIR}/.env.production.example" ]; then
    cp "${APP_DIR}/.env.production.example" "${APP_DIR}/.env"
  fi
  fail "Create ${APP_DIR}/.env and fill OPENAI_API_KEY, BINANCE keys, and OWNER_PASSWORD before installing."
fi

msg "Installing Ubuntu packages"
sudo apt-get update
sudo apt-get install -y curl ca-certificates python3 python3-venv python3-pip ufw

if [ "${INSTALL_TAILSCALE}" = "1" ]; then
  msg "Installing / connecting Tailscale"
  if ! command -v tailscale >/dev/null 2>&1; then
    curl -fsSL https://tailscale.com/install.sh | sh
  fi
  if ! tailscale status >/dev/null 2>&1; then
    echo "A Tailscale login URL may appear below. Open it and approve this server."
    sudo tailscale up --ssh
  fi
fi

msg "Creating Python virtualenv"
if [ ! -d "${APP_DIR}/.venv" ]; then
  "${PYTHON_BIN}" -m venv "${APP_DIR}/.venv"
fi
"${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${APP_DIR}/.venv/bin/python" -m pip install -r "${APP_DIR}/requirements.txt"

msg "Installing systemd service"
tmp_service="$(mktemp)"
sed \
  -e "s|__APP_DIR__|${APP_DIR}|g" \
  -e "s|__SERVICE_NAME__|${SERVICE_NAME}|g" \
  -e "s|__SERVICE_USER__|${SERVICE_USER}|g" \
  -e "s|__APP_PORT__|${APP_PORT}|g" \
  "${APP_DIR}/deploy/lightsail/crypto-analyzer.service.template" > "${tmp_service}"

sudo cp "${tmp_service}" "/etc/systemd/system/${SERVICE_NAME}.service"
rm -f "${tmp_service}"
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}.service"
sudo systemctl restart "${SERVICE_NAME}.service"

if [ "${CONFIGURE_UFW}" = "1" ]; then
  msg "Configuring local firewall for Tailscale-only app access"
  sudo ufw allow OpenSSH
  sudo ufw allow in on tailscale0 to any port "${APP_PORT}" proto tcp
  sudo ufw --force enable
fi

TAILSCALE_IP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"

msg "Done"
echo "Service: sudo systemctl status ${SERVICE_NAME}"
echo "Logs:    sudo journalctl -u ${SERVICE_NAME} -f"
if [ -n "${TAILSCALE_IP}" ]; then
  echo "Phone URL with Tailscale on: http://${TAILSCALE_IP}:${APP_PORT}"
else
  echo "Run 'tailscale ip -4' to find the private phone URL."
fi
