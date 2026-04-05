#!/usr/bin/env bash
# HydraHive Extension - Sonarr (native Installation)
# Lädt das aktuelle Sonarr v4-Binary von services.sonarr.tv herunter und
# installiert es als systemd-Service unter /opt/sonarr.
# Idempotent: erneuter Aufruf aktualisiert Sonarr.

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[Sonarr]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

SONARR_DIR="/opt/sonarr"
SONARR_DATA="/var/lib/sonarr"
SONARR_USER="sonarr"
SONARR_PORT="8989"
SONARR_DL_URL="https://services.sonarr.tv/v1/download/main/latest?version=4&os=linux&arch=x64"
HH_CONF="/etc/hydrahive/sonarr.json"

info "=== Sonarr installieren ==="

# --- Abhängigkeiten ---
info "Installiere Abhängigkeiten..."
apt-get update -qq
apt-get install -y --quiet curl wget sqlite3 2>/dev/null | grep -E "^(Get|Entpacken|Einrichten)" || true
success "Abhängigkeiten bereit"

# --- System-User ---
if ! id "${SONARR_USER}" &>/dev/null; then
    if getent group "${SONARR_USER}" &>/dev/null; then
        useradd -r -s /bin/false -d "${SONARR_DATA}" -g "${SONARR_USER}" "${SONARR_USER}"
    else
        useradd -r -s /bin/false -d "${SONARR_DATA}" "${SONARR_USER}"
    fi
    success "System-User '${SONARR_USER}' angelegt"
fi

# --- Verzeichnisse ---
mkdir -p "${SONARR_DIR}" "${SONARR_DATA}"
chown "${SONARR_USER}:${SONARR_USER}" "${SONARR_DATA}"

# --- Service stoppen falls läuft (Update-Fall) ---
systemctl stop sonarr 2>/dev/null || true

# --- Sonarr herunterladen und entpacken ---
info "Lade Sonarr v4 herunter..."
TMP_TAR="/tmp/sonarr_linux.tar.gz"
curl -fSL "${SONARR_DL_URL}" -o "${TMP_TAR}" \
    || error "Sonarr-Download fehlgeschlagen"
success "Download abgeschlossen"

info "Entpacke nach ${SONARR_DIR}..."
tar -xzf "${TMP_TAR}" -C /opt --overwrite \
    || error "Entpacken fehlgeschlagen"
rm -f "${TMP_TAR}"

# Das Archiv entpackt nach /opt/Sonarr — auf lowercase umbenennen wenn nötig
if [ -d "/opt/Sonarr" ] && [ ! -L "/opt/Sonarr" ]; then
    rm -rf "${SONARR_DIR}"
    mv /opt/Sonarr "${SONARR_DIR}"
fi

chown -R "${SONARR_USER}:${SONARR_USER}" "${SONARR_DIR}"
success "Sonarr entpackt nach ${SONARR_DIR}"

# --- systemd Service ---
cat > /etc/systemd/system/sonarr.service << SVCEOF
[Unit]
Description=Sonarr - TV Series Collection Manager
After=network.target

[Service]
Type=simple
User=${SONARR_USER}
Group=${SONARR_USER}
WorkingDirectory=${SONARR_DIR}
ExecStart=${SONARR_DIR}/Sonarr -nobrowser -data=${SONARR_DATA}
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal
Environment=TMPDIR=/tmp

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable sonarr
systemctl start sonarr
success "Service 'sonarr' gestartet auf Port ${SONARR_PORT}"

# --- Warten auf Erreichbarkeit ---
info "Warte auf Sonarr (bis 30 s)..."
for i in $(seq 1 15); do
    sleep 2
    if curl -sf "http://127.0.0.1:${SONARR_PORT}/" &>/dev/null; then
        break
    fi
done
if curl -sf "http://127.0.0.1:${SONARR_PORT}/" &>/dev/null; then
    success "Sonarr erreichbar"
else
    warn "Sonarr noch nicht erreichbar — prüfe: sudo systemctl status sonarr"
fi

# --- HydraHive Config ---
SERVER_IP=$(hostname -I | awk '{print $1}')
mkdir -p /etc/hydrahive
cat > "${HH_CONF}" << CFGEOF
{
  "installed": true,
  "url": "http://127.0.0.1:${SONARR_PORT}",
  "port": ${SONARR_PORT},
  "server_ip": "${SERVER_IP}",
  "install_dir": "${SONARR_DIR}",
  "data_dir": "${SONARR_DATA}"
}
CFGEOF
chown hydrahive:hydrahive "${HH_CONF}" 2>/dev/null || true
chmod 640 "${HH_CONF}"

echo ""
info "=== Sonarr installiert ==="
info "URL:      http://${SERVER_IP}:${SONARR_PORT}"
info "Daten:    ${SONARR_DATA}"
info "Beim ersten Aufruf API-Key unter Settings → General notieren"
