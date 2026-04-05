#!/usr/bin/env bash
# HydraHive Extension - Vikunja (Task Management)
# Lädt das aktuelle Vikunja-Binary von dl.vikunja.io, richtet
# SQLite-Datenbank und systemd-Service ein.
# Idempotent: erneuter Aufruf aktualisiert Vikunja.

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[Vikunja]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

VIKUNJA_DIR="/opt/vikunja"
VIKUNJA_BINARY="${VIKUNJA_DIR}/vikunja"
VIKUNJA_DATA="/var/lib/vikunja"
VIKUNJA_CONF_DIR="/etc/vikunja"
VIKUNJA_CONF="${VIKUNJA_CONF_DIR}/config.yaml"
VIKUNJA_USER="vikunja"
VIKUNJA_PORT="3456"
VIKUNJA_DL_BASE="https://dl.vikunja.io/vikunja"
HH_CONF="/etc/hydrahive/vikunja.json"

info "=== Vikunja installieren ==="

_SERVER_IP="$(hostname -I | awk '{print $1}')"

# --- Abhängigkeiten ---
info "Installiere Abhängigkeiten..."
apt-get update -qq
apt-get install -y --quiet curl wget sqlite3 python3 2>/dev/null \
    | grep -E "^(Get|Entpacken|Einrichten)" || true

# --- Neueste Version ermitteln (GitHub API) ---
info "Ermittle neueste Vikunja-Version..."
LATEST_VERSION="$(curl -sf 'https://api.github.com/repos/go-vikunja/vikunja/releases/latest' \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('tag_name','v2.2.2'))" 2>/dev/null \
    || echo 'v2.2.2')"
info "Neueste Version: ${LATEST_VERSION}"

# --- Schon installiert und aktuell? ---
if [ -x "${VIKUNJA_BINARY}" ]; then
    INSTALLED_VERSION="$("${VIKUNJA_BINARY}" version 2>/dev/null | grep -oP 'v[\d.]+' | head -1 || echo "")"
    if [ "${INSTALLED_VERSION}" = "${LATEST_VERSION}" ]; then
        success "Vikunja ${INSTALLED_VERSION} bereits aktuell"
        systemctl start vikunja 2>/dev/null || true
        exit 0
    fi
    info "Update von ${INSTALLED_VERSION} auf ${LATEST_VERSION}..."
    systemctl stop vikunja 2>/dev/null || true
fi

# --- Binary herunterladen ---
mkdir -p "${VIKUNJA_DIR}"

# Vikunja v2.x: GitHub Release als ZIP (enthält Binary + Frontend)
DL_URL="https://github.com/go-vikunja/vikunja/releases/download/${LATEST_VERSION}/vikunja-${LATEST_VERSION}-linux-amd64-full.zip"

info "Lade vikunja-${LATEST_VERSION}-linux-amd64-full.zip herunter..."
apt-get install -y --quiet unzip 2>/dev/null | grep -E "^(Entpacken|Einrichten)" || true
curl -fSL "${DL_URL}" -o "/tmp/vikunja.zip" \
    || error "Download fehlgeschlagen: ${DL_URL}"

# Prüfsumme verifizieren
CHECKSUM_URL="https://github.com/go-vikunja/vikunja/releases/download/${LATEST_VERSION}/vikunja-${LATEST_VERSION}-linux-amd64-full.zip.sha256"
if curl -fsSL "${CHECKSUM_URL}" -o "/tmp/vikunja.zip.sha256" 2>/dev/null; then
    EXPECTED_HASH="$(awk '{print $1}' /tmp/vikunja.zip.sha256)"
    ACTUAL_HASH="$(sha256sum /tmp/vikunja.zip | awk '{print $1}')"
    if [ -n "${EXPECTED_HASH}" ] && [ "${EXPECTED_HASH}" != "${ACTUAL_HASH}" ]; then
        rm -f /tmp/vikunja.zip /tmp/vikunja.zip.sha256
        error "SHA256-Prüfsumme stimmt NICHT überein!"
    fi
    success "SHA256-Prüfsumme korrekt: ${ACTUAL_HASH:0:16}..."
    rm -f /tmp/vikunja.zip.sha256
else
    warn "Checksum-Datei nicht verfügbar — überspringe Verifikation"
fi

info "Entpacke ZIP..."
rm -rf /tmp/vikunja-extract
mkdir -p /tmp/vikunja-extract
unzip -o -q /tmp/vikunja.zip -d /tmp/vikunja-extract
rm -f /tmp/vikunja.zip

# Binary finden (kann vikunja oder Vikunja heißen)
FOUND_BIN="$(find /tmp/vikunja-extract -name 'vikunja' -type f -executable | head -1)"
if [ -z "${FOUND_BIN}" ]; then
    FOUND_BIN="$(find /tmp/vikunja-extract -name 'vikunja*' -type f | head -1)"
fi
[ -n "${FOUND_BIN}" ] || error "Binary nicht im ZIP gefunden"
mv "${FOUND_BIN}" "${VIKUNJA_BINARY}"
chmod 755 "${VIKUNJA_BINARY}"
rm -rf /tmp/vikunja-extract
success "Vikunja ${LATEST_VERSION} installiert: ${VIKUNJA_BINARY}"

# --- System-User ---
if ! id "${VIKUNJA_USER}" &>/dev/null; then
    if getent group "${VIKUNJA_USER}" &>/dev/null; then
        useradd -r -s /bin/false -d "${VIKUNJA_DATA}" -m -g "${VIKUNJA_USER}" "${VIKUNJA_USER}"
    else
        useradd -r -s /bin/false -d "${VIKUNJA_DATA}" -m "${VIKUNJA_USER}"
    fi
    success "System-User '${VIKUNJA_USER}' angelegt"
fi

# --- Verzeichnisse ---
mkdir -p "${VIKUNJA_DATA}" "${VIKUNJA_CONF_DIR}"
chown -R "${VIKUNJA_USER}:${VIKUNJA_USER}" "${VIKUNJA_DIR}" "${VIKUNJA_DATA}"

# --- Zufälliges JWT-Secret ---
JWT_SECRET="$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 48)"

# --- Konfiguration (nur schreiben wenn noch nicht vorhanden) ---
if [ ! -f "${VIKUNJA_CONF}" ]; then
    info "Erstelle ${VIKUNJA_CONF}..."
    cat > "${VIKUNJA_CONF}" << YAMLEOF
service:
  JWTSecret: "${JWT_SECRET}"
  interface: "0.0.0.0:${VIKUNJA_PORT}"
  frontendurl: "http://${_SERVER_IP}:${VIKUNJA_PORT}/"
  enableregistration: true
  enablelinksharing: true
  enablepublicteams: true

database:
  type: "sqlite"
  path: "${VIKUNJA_DATA}/vikunja.db"

files:
  basepath: "${VIKUNJA_DATA}/files"
  maxsize: "20MB"

mailer:
  enabled: false

log:
  level: "WARNING"
  standardout: true
YAMLEOF
    chown root:"${VIKUNJA_USER}" "${VIKUNJA_CONF}"
    chmod 640 "${VIKUNJA_CONF}"
    success "Konfiguration erstellt"
else
    info "${VIKUNJA_CONF} bereits vorhanden — überspringe"
    # Sicherstellen dass Bind-Adresse korrekt ist
    sed -i "s|interface:.*|interface: \"0.0.0.0:${VIKUNJA_PORT}\"|" "${VIKUNJA_CONF}"
fi

# --- Daten-Verzeichnis für Files anlegen ---
mkdir -p "${VIKUNJA_DATA}/files"
chown -R "${VIKUNJA_USER}:${VIKUNJA_USER}" "${VIKUNJA_DATA}"

# --- systemd Service ---
cat > /etc/systemd/system/vikunja.service << SVCEOF
[Unit]
Description=Vikunja Task Management
After=network.target

[Service]
Type=simple
User=${VIKUNJA_USER}
Group=${VIKUNJA_USER}
WorkingDirectory=${VIKUNJA_DATA}
ExecStart=${VIKUNJA_BINARY} --config ${VIKUNJA_CONF}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=VIKUNJA_SERVICE_ROOTPATH=${VIKUNJA_DATA}

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable vikunja
systemctl restart vikunja
success "Service 'vikunja' gestartet auf Port ${VIKUNJA_PORT}"

# --- Warten auf Erreichbarkeit ---
info "Warte auf Vikunja (bis 30 s)..."
for i in $(seq 1 15); do
    sleep 2
    if curl -sf "http://127.0.0.1:${VIKUNJA_PORT}/api/v1/info" &>/dev/null; then
        break
    fi
done
if curl -sf "http://127.0.0.1:${VIKUNJA_PORT}/api/v1/info" &>/dev/null; then
    success "Vikunja erreichbar"
else
    warn "Vikunja noch nicht erreichbar — prüfe: sudo systemctl status vikunja"
fi

# --- HydraHive Config ---
mkdir -p /etc/hydrahive
cat > "${HH_CONF}" << CFGEOF
{
  "installed": true,
  "version": "${LATEST_VERSION}",
  "url": "http://127.0.0.1:${VIKUNJA_PORT}",
  "port": ${VIKUNJA_PORT},
  "server_ip": "${_SERVER_IP}",
  "binary": "${VIKUNJA_BINARY}",
  "config": "${VIKUNJA_CONF}",
  "data_dir": "${VIKUNJA_DATA}"
}
CFGEOF
chown hydrahive:hydrahive "${HH_CONF}" 2>/dev/null || true
chmod 640 "${HH_CONF}"

echo ""
info "=== Vikunja installiert ==="
info "URL:     http://${_SERVER_IP}:${VIKUNJA_PORT}"
info "Daten:   ${VIKUNJA_DATA}"
info "Ersten Account im Browser anlegen (Registrierung aktiviert)"
info "API:     http://${_SERVER_IP}:${VIKUNJA_PORT}/api/v1"
