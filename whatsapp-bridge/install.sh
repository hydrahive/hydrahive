#!/usr/bin/env bash
# install.sh — HydraHive WhatsApp Bridge installieren
# Usage: sudo bash install.sh

set -euo pipefail

BRIDGE_DIR="/opt/hydrahive/whatsapp-bridge"
SESSIONS_DIR="/etc/hydrahive/whatsapp-sessions"
SERVICE_NAME="hydrahive-whatsapp-bridge"

GREEN="\033[0;32m"; BLUE="\033[0;34m"; RED="\033[0;31m"; NC="\033[0m"
info()    { echo -e "${BLUE}[WhatsApp Bridge]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

[ "$(id -u)" -eq 0 ] || error "Bitte als root ausführen: sudo bash $0"

# Node.js prüfen
node --version &>/dev/null || error "Node.js nicht gefunden. Bitte Node.js >= 18 installieren."
NODE_MAJOR=$(node -e "process.stdout.write(process.version.slice(1).split('.')[0])")
[ "$NODE_MAJOR" -ge 18 ] || error "Node.js >= 18 erforderlich (gefunden: $(node --version))"

# Chromium für Puppeteer installieren
info "Chromium-Abhängigkeiten installieren ..."
apt-get install -y --no-install-recommends \
    chromium-browser \
    libgbm1 libxss1 libxcomposite1 libxdamage1 libasound2 \
    libatk-bridge2.0-0 libgtk-3-0 libnss3 libx11-xcb1 2>/dev/null || \
apt-get install -y --no-install-recommends \
    chromium \
    libgbm1 libxss1 libxcomposite1 libxdamage1 libasound2t64 \
    libatk-bridge2.0-0 libgtk-3-0 libnss3 libx11-xcb1 2>/dev/null || true

info "Installiere WhatsApp Bridge nach ${BRIDGE_DIR} ..."
mkdir -p "${BRIDGE_DIR}" "${SESSIONS_DIR}"
chmod 700 "${SESSIONS_DIR}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "${SCRIPT_DIR}/bridge.js"    "${BRIDGE_DIR}/"
cp "${SCRIPT_DIR}/package.json" "${BRIDGE_DIR}/"

info "npm install ..."
cd "${BRIDGE_DIR}"
PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true npm install --omit=dev

# Bridge-Secret generieren falls nicht vorhanden
SECRET_FILE="/etc/hydrahive/whatsapp_bridge_secret"
if [ ! -f "${SECRET_FILE}" ]; then
    openssl rand -hex 32 > "${SECRET_FILE}"
    chmod 600 "${SECRET_FILE}"
    success "Bridge-Secret generiert: ${SECRET_FILE}"
fi
BRIDGE_SECRET=$(cat "${SECRET_FILE}")

# Chromium-Pfad ermitteln
CHROMIUM_PATH=$(which chromium-browser 2>/dev/null || which chromium 2>/dev/null || echo "")

# systemd-Service anlegen
info "systemd-Service ${SERVICE_NAME} anlegen ..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=HydraHive WhatsApp Bridge (whatsapp-web.js)
After=network.target

[Service]
Type=simple
WorkingDirectory=${BRIDGE_DIR}
ExecStart=/usr/bin/node ${BRIDGE_DIR}/bridge.js
Restart=always
RestartSec=10
Environment=BRIDGE_PORT=8767
Environment=SESSIONS_DIR=${SESSIONS_DIR}
Environment=CORE_URL=http://127.0.0.1:8765
Environment=BRIDGE_SECRET=${BRIDGE_SECRET}
Environment=PUPPETEER_EXECUTABLE_PATH=${CHROMIUM_PATH}
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"

# Secret in Core-Umgebung eintragen (falls /etc/hydrahive/environment existiert)
ENV_FILE="/etc/hydrahive/environment"
if [ -f "${ENV_FILE}" ]; then
    if grep -q "WHATSAPP_BRIDGE_SECRET" "${ENV_FILE}"; then
        sed -i "s/^WHATSAPP_BRIDGE_SECRET=.*/WHATSAPP_BRIDGE_SECRET=${BRIDGE_SECRET}/" "${ENV_FILE}"
    else
        echo "WHATSAPP_BRIDGE_SECRET=${BRIDGE_SECRET}" >> "${ENV_FILE}"
    fi
fi

success "WhatsApp Bridge installiert!"
echo ""
echo "  Status: systemctl status ${SERVICE_NAME}"
echo "  Logs:   journalctl -u ${SERVICE_NAME} -f"
