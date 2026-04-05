#!/usr/bin/env bash
# HydraHive Extension - Radarr (native Installation)
# Lädt das aktuelle Radarr-Binary von servarr.com herunter und installiert
# es als systemd-Service unter /opt/radarr.
# Idempotent: erneuter Aufruf aktualisiert Radarr.

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[Radarr]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

RADARR_DIR="/opt/radarr"
RADARR_DATA="/var/lib/radarr"
RADARR_USER="radarr"
RADARR_PORT="7878"
RADARR_DL_URL="https://radarr.servarr.com/v1/update/master/updatefile?os=linux&runtime=netcore&arch=x64"
HH_CONF="/etc/hydrahive/radarr.json"

info "=== Radarr installieren ==="

# --- Abhängigkeiten ---
info "Installiere Abhängigkeiten..."
apt-get update -qq
apt-get install -y --quiet curl wget sqlite3 2>/dev/null | grep -E "^(Get|Entpacken|Einrichten)" || true
success "Abhängigkeiten bereit"

# --- System-User ---
if ! id "${RADARR_USER}" &>/dev/null; then
    if getent group "${RADARR_USER}" &>/dev/null; then
        useradd -r -s /bin/false -d "${RADARR_DATA}" -g "${RADARR_USER}" "${RADARR_USER}"
    else
        useradd -r -s /bin/false -d "${RADARR_DATA}" "${RADARR_USER}"
    fi
    success "System-User '${RADARR_USER}' angelegt"
fi

# --- Verzeichnisse ---
mkdir -p "${RADARR_DIR}" "${RADARR_DATA}"
chown "${RADARR_USER}:${RADARR_USER}" "${RADARR_DATA}"

# --- Service stoppen falls läuft (Update-Fall) ---
systemctl stop radarr 2>/dev/null || true

# --- Radarr herunterladen und entpacken ---
info "Lade Radarr herunter..."
TMP_TAR="/tmp/radarr_linux.tar.gz"
curl -fSL "${RADARR_DL_URL}" -o "${TMP_TAR}" \
    || error "Radarr-Download fehlgeschlagen"
success "Download abgeschlossen"

info "Entpacke nach ${RADARR_DIR}..."
tar -xzf "${TMP_TAR}" -C /opt --overwrite \
    || error "Entpacken fehlgeschlagen"
rm -f "${TMP_TAR}"

# Das Archiv entpackt nach /opt/Radarr — auf lowercase umbenennen wenn nötig
if [ -d "/opt/Radarr" ] && [ ! -L "/opt/Radarr" ]; then
    rm -rf "${RADARR_DIR}"
    mv /opt/Radarr "${RADARR_DIR}"
fi

chown -R "${RADARR_USER}:${RADARR_USER}" "${RADARR_DIR}"
success "Radarr entpackt nach ${RADARR_DIR}"

# --- systemd Service ---
cat > /etc/systemd/system/radarr.service << SVCEOF
[Unit]
Description=Radarr - Movie Collection Manager
After=network.target

[Service]
Type=simple
User=${RADARR_USER}
Group=${RADARR_USER}
WorkingDirectory=${RADARR_DIR}
ExecStart=${RADARR_DIR}/Radarr -nobrowser -data=${RADARR_DATA}
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
systemctl enable radarr
systemctl start radarr
success "Service 'radarr' gestartet auf Port ${RADARR_PORT}"

# --- Warten auf Erreichbarkeit ---
info "Warte auf Radarr (bis 30 s)..."
for i in $(seq 1 15); do
    sleep 2
    if curl -sf "http://127.0.0.1:${RADARR_PORT}/" &>/dev/null; then
        break
    fi
done
if curl -sf "http://127.0.0.1:${RADARR_PORT}/" &>/dev/null; then
    success "Radarr erreichbar"
else
    warn "Radarr noch nicht erreichbar — prüfe: sudo systemctl status radarr"
fi

# --- HydraHive Config ---
SERVER_IP=$(hostname -I | awk '{print $1}')
mkdir -p /etc/hydrahive
cat > "${HH_CONF}" << CFGEOF
{
  "installed": true,
  "url": "http://127.0.0.1:${RADARR_PORT}",
  "port": ${RADARR_PORT},
  "server_ip": "${SERVER_IP}",
  "install_dir": "${RADARR_DIR}",
  "data_dir": "${RADARR_DATA}"
}
CFGEOF
chown hydrahive:hydrahive "${HH_CONF}" 2>/dev/null || true
chmod 640 "${HH_CONF}"

echo ""
info "=== Radarr installiert ==="
info "URL:      http://${SERVER_IP}:${RADARR_PORT}"
info "Daten:    ${RADARR_DATA}"
info "Beim ersten Aufruf API-Key unter Settings → General notieren"
