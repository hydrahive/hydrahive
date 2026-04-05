#!/usr/bin/env bash
# HydraHive Extension - Plex Media Server (native Installation)
# Fügt das offizielle Plex-Apt-Repository hinzu und installiert plexmediaserver.
# Idempotent: erneuter Aufruf aktualisiert Plex auf die neueste Version.

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[Plex]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

PLEX_REPO_FILE="/etc/apt/sources.list.d/plexmediaserver.list"
PLEX_GPG_FILE="/etc/apt/trusted.gpg.d/plexmediaserver.asc"
PLEX_PORT="32400"
HH_CONF="/etc/hydrahive/plex.json"

info "=== Plex Media Server installieren ==="

# --- GPG-Schlüssel ---
if [ ! -f "${PLEX_GPG_FILE}" ]; then
    info "Füge Plex GPG-Schlüssel hinzu..."
    curl -fsSL "https://downloads.plex.tv/plex-keys/PlexSign.key" \
        | gpg --dearmor -o "${PLEX_GPG_FILE}" \
        || error "GPG-Schlüssel konnte nicht importiert werden"
    success "GPG-Schlüssel importiert"
fi

# --- Apt-Repository ---
if [ ! -f "${PLEX_REPO_FILE}" ]; then
    info "Füge Plex apt-Repository hinzu..."
    echo "deb [signed-by=${PLEX_GPG_FILE}] https://downloads.plex.tv/repo/deb public main" \
        > "${PLEX_REPO_FILE}"
    success "Plex-Repository eingetragen: ${PLEX_REPO_FILE}"
fi

# --- Paket installieren / aktualisieren ---
info "Aktualisiere Paketliste und installiere plexmediaserver..."
apt-get update -qq
apt-get install -y --quiet plexmediaserver \
    2>/dev/null | grep -E "^(Get|Entpacken|Einrichten|Wird|plexmediaserver)" || true
success "plexmediaserver installiert: $(dpkg-query -W -f='${Version}' plexmediaserver 2>/dev/null || echo 'unbekannt')"

# --- Daten-Verzeichnis sicherstellen ---
mkdir -p /var/lib/plexmediaserver/Library
chown -R plex:plex /var/lib/plexmediaserver 2>/dev/null || true
success "Daten-Verzeichnis /var/lib/plexmediaserver bereit"

# --- Service aktivieren und starten ---
systemctl daemon-reload
systemctl enable plexmediaserver
systemctl restart plexmediaserver
success "Service 'plexmediaserver' gestartet"

# --- Warten auf Erreichbarkeit ---
info "Warte auf Plex (bis 30 s)..."
for i in $(seq 1 15); do
    sleep 2
    if curl -sf "http://127.0.0.1:${PLEX_PORT}/web" &>/dev/null; then
        break
    fi
done
if curl -sf "http://127.0.0.1:${PLEX_PORT}/web" &>/dev/null; then
    success "Plex erreichbar"
else
    warn "Plex noch nicht erreichbar — prüfe: sudo systemctl status plexmediaserver"
fi

# --- HydraHive Config ---
SERVER_IP=$(hostname -I | awk '{print $1}')
mkdir -p /etc/hydrahive
cat > "${HH_CONF}" << CFGEOF
{
  "installed": true,
  "url": "http://127.0.0.1:${PLEX_PORT}/web",
  "port": ${PLEX_PORT},
  "server_ip": "${SERVER_IP}",
  "data_dir": "/var/lib/plexmediaserver"
}
CFGEOF
chown hydrahive:hydrahive "${HH_CONF}" 2>/dev/null || true
chmod 640 "${HH_CONF}"

echo ""
info "=== Plex Media Server installiert ==="
info "URL:      http://${SERVER_IP}:${PLEX_PORT}/web"
info "Daten:    /var/lib/plexmediaserver"
info "Beim ersten Aufruf im Browser Setup-Wizard durchlaufen"
