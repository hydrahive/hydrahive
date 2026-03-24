#!/usr/bin/env bash
# install.sh — HydraHive WhatsApp Bridge installieren
# Usage: sudo bash install.sh

set -euo pipefail

BRIDGE_DIR="/opt/octopos/whatsapp-bridge"
SESSIONS_DIR="/etc/octopos/whatsapp-sessions"
SERVICE_NAME="octopos-whatsapp-bridge"

GREEN="\033[0;32m"; BLUE="\033[0;34m"; RED="\033[0;31m"; NC="\033[0m"
info()    { echo -e "${BLUE}[WhatsApp Bridge]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

[ "$(id -u)" -eq 0 ] || error "Bitte als root ausführen: sudo bash $0"

# Node.js prüfen
node --version &>/dev/null || error "Node.js nicht gefunden. Bitte Node.js >= 18 installieren."
NODE_MAJOR=$(node -e "process.stdout.write(process.version.slice(1).split('.')[0])")
[ "$NODE_MAJOR" -ge 18 ] || error "Node.js >= 18 erforderlich (gefunden: $(node --version))"

info "Installiere WhatsApp Bridge nach ${BRIDGE_DIR} ..."
mkdir -p "${BRIDGE_DIR}" "${SESSIONS_DIR}"
chmod 700 "${SESSIONS_DIR}"

# Bridge-Dateien kopieren (relativ zum Script-Verzeichnis)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "${SCRIPT_DIR}/bridge.js"     "${BRIDGE_DIR}/"
cp "${SCRIPT_DIR}/package.json"  "${BRIDGE_DIR}/"

info "npm install ..."
cd "${BRIDGE_DIR}"
npm install --omit=dev

# Bridge-Secret generieren falls nicht vorhanden
SECRET_FILE="/etc/octopos/whatsapp_bridge_secret"
if [ ! -f "${SECRET_FILE}" ]; then
    openssl rand -hex 32 > "${SECRET_FILE}"
    chmod 600 "${SECRET_FILE}"
    success "Bridge-Secret generiert: ${SECRET_FILE}"
fi
BRIDGE_SECRET=$(cat "${SECRET_FILE}")

# systemd-Service anlegen
info "systemd-Service ${SERVICE_NAME} anlegen ..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=HydraHive WhatsApp Bridge (Baileys)
After=network.target octopos-core.service
Wants=octopos-core.service

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
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}"

# Secret in Core-Umgebung eintragen
ENV_FILE="/etc/octopos/environment"
if [ -f "${ENV_FILE}" ]; then
    if grep -q "WHATSAPP_BRIDGE_SECRET" "${ENV_FILE}"; then
        sed -i "s/^WHATSAPP_BRIDGE_SECRET=.*/WHATSAPP_BRIDGE_SECRET=${BRIDGE_SECRET}/" "${ENV_FILE}"
    else
        echo "WHATSAPP_BRIDGE_SECRET=${BRIDGE_SECRET}" >> "${ENV_FILE}"
    fi
    info "Bridge-Secret in ${ENV_FILE} eingetragen — Core neu starten: systemctl restart octopos-core"
fi

success "WhatsApp Bridge installiert und gestartet!"
echo ""
echo "  Status: systemctl status ${SERVICE_NAME}"
echo "  Logs:   journalctl -u ${SERVICE_NAME} -f"
