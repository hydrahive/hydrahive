#!/usr/bin/env bash
# HydraHive Extension - SABnzbd (native Installation)
# Installiert SABnzbd via apt (Ubuntu-Paket sabnzbdplus).
# Konfiguriert Port 8280 und bindet an 0.0.0.0 für Netzwerkzugriff.
# Idempotent: erneuter Aufruf aktualisiert SABnzbd.

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[SABnzbd]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

SAB_PORT="8280"
SAB_USER="sabnzbd"
SAB_DATA="/var/lib/sabnzbd"
SAB_INI="${SAB_DATA}/sabnzbd.ini"
SAB_DEFAULTS="/etc/default/sabnzbdplus"
HH_CONF="/etc/hydrahive/sabnzbd.json"

info "=== SABnzbd installieren ==="

# --- Paket installieren ---
info "Aktualisiere Paketliste und installiere sabnzbdplus..."
apt-get update -qq
apt-get install -y --quiet sabnzbdplus \
    2>/dev/null | grep -E "^(Get|Entpacken|Einrichten|sabnzbd)" || true
success "sabnzbdplus installiert: $(dpkg-query -W -f='${Version}' sabnzbdplus 2>/dev/null || echo 'unbekannt')"

# --- /etc/default/sabnzbdplus konfigurieren ---
# Host, Port und User für den System-Service einstellen
if [ -f "${SAB_DEFAULTS}" ]; then
    info "Konfiguriere ${SAB_DEFAULTS}..."
    # USER
    if grep -q "^USER=" "${SAB_DEFAULTS}"; then
        sed -i "s|^USER=.*|USER=${SAB_USER}|" "${SAB_DEFAULTS}"
    else
        echo "USER=${SAB_USER}" >> "${SAB_DEFAULTS}"
    fi
    # HOST
    if grep -q "^HOST=" "${SAB_DEFAULTS}"; then
        sed -i "s|^HOST=.*|HOST=0.0.0.0|" "${SAB_DEFAULTS}"
    else
        echo "HOST=0.0.0.0" >> "${SAB_DEFAULTS}"
    fi
    # PORT
    if grep -q "^PORT=" "${SAB_DEFAULTS}"; then
        sed -i "s|^PORT=.*|PORT=${SAB_PORT}|" "${SAB_DEFAULTS}"
    else
        echo "PORT=${SAB_PORT}" >> "${SAB_DEFAULTS}"
    fi
    success "${SAB_DEFAULTS} konfiguriert (Host=0.0.0.0, Port=${SAB_PORT})"
else
    warn "${SAB_DEFAULTS} nicht gefunden — schreibe Standardkonfiguration"
    cat > "${SAB_DEFAULTS}" << DEFEOF
USER=${SAB_USER}
HOST=0.0.0.0
PORT=${SAB_PORT}
DAEMON=1
DEFEOF
fi

# --- Daten-Verzeichnis ---
mkdir -p "${SAB_DATA}"
# sabnzbdplus legt den User selbst an; falls noch nicht vorhanden:
if ! id "${SAB_USER}" &>/dev/null; then
    if getent group "${SAB_USER}" &>/dev/null; then
        useradd -r -s /bin/false -d "${SAB_DATA}" -g "${SAB_USER}" "${SAB_USER}"
    else
        useradd -r -s /bin/false -d "${SAB_DATA}" "${SAB_USER}"
    fi
    success "System-User '${SAB_USER}' angelegt"
fi
chown -R "${SAB_USER}:${SAB_USER}" "${SAB_DATA}"

# --- sabnzbd.ini: Host + Port direkt setzen (falls Datei bereits existiert) ---
if [ -f "${SAB_INI}" ]; then
    info "Aktualisiere sabnzbd.ini (Host + Port)..."
    # sabnzbd.ini ist kein Standard-INI — hat __version__ ohne Section-Header
    # Deshalb sed statt configparser
    if grep -q '^\[misc\]' "${SAB_INI}"; then
        sed -i "/^\[misc\]/,/^\[/ { s/^host = .*/host = 0.0.0.0/; s/^port = .*/port = ${SAB_PORT}/; s/^auto_browser = .*/auto_browser = 0/; }" "${SAB_INI}"
    fi
    success "sabnzbd.ini aktualisiert"
fi

# --- systemd Service neu laden und starten ---
# Ubuntu-Paket bringt eigenen Init-Script / Service mit
# Versuche systemd-Unit zuerst, dann init.d
if systemctl list-unit-files sabnzbd.service &>/dev/null 2>&1 | grep -q sabnzbd; then
    SAB_SERVICE="sabnzbd"
elif systemctl list-unit-files sabnzbdplus.service &>/dev/null 2>&1 | grep -q sabnzbd; then
    SAB_SERVICE="sabnzbdplus"
else
    # Eigene Unit erstellen
    SAB_SERVICE="sabnzbd"
    SAB_BIN=$(which sabnzbdplus 2>/dev/null || which sabnzbd 2>/dev/null || echo "/usr/bin/sabnzbdplus")
    cat > /etc/systemd/system/sabnzbd.service << SVCEOF
[Unit]
Description=SABnzbd - Usenet Downloader
After=network.target

[Service]
Type=simple
User=${SAB_USER}
Group=${SAB_USER}
ExecStart=${SAB_BIN} -s 0.0.0.0:${SAB_PORT} -f ${SAB_DATA}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF
fi

systemctl daemon-reload
systemctl enable "${SAB_SERVICE}"
systemctl restart "${SAB_SERVICE}"
success "Service '${SAB_SERVICE}' gestartet auf Port ${SAB_PORT}"

# --- Warten auf Erreichbarkeit ---
info "Warte auf SABnzbd (bis 30 s)..."
for i in $(seq 1 15); do
    sleep 2
    if curl -sf "http://127.0.0.1:${SAB_PORT}/" &>/dev/null; then
        break
    fi
done
if curl -sf "http://127.0.0.1:${SAB_PORT}/" &>/dev/null; then
    success "SABnzbd erreichbar"
else
    warn "SABnzbd noch nicht erreichbar — prüfe: sudo systemctl status ${SAB_SERVICE}"
fi

# --- HydraHive Config ---
SERVER_IP=$(hostname -I | awk '{print $1}')
mkdir -p /etc/hydrahive
cat > "${HH_CONF}" << CFGEOF
{
  "installed": true,
  "url": "http://127.0.0.1:${SAB_PORT}",
  "port": ${SAB_PORT},
  "server_ip": "${SERVER_IP}",
  "data_dir": "${SAB_DATA}",
  "service": "${SAB_SERVICE}"
}
CFGEOF
chown hydrahive:hydrahive "${HH_CONF}" 2>/dev/null || true
chmod 640 "${HH_CONF}"

echo ""
info "=== SABnzbd installiert ==="
info "URL:      http://${SERVER_IP}:${SAB_PORT}"
info "Daten:    ${SAB_DATA}"
info "Beim ersten Aufruf Usenet-Server und Kategorien konfigurieren"
