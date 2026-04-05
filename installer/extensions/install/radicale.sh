#!/usr/bin/env bash
# HydraHive Extension - Radicale (CalDAV/CardDAV)
# Installiert Radicale via apt oder pip-venv als systemd-Service.
# Konfiguriert Port 5232, bindet an 0.0.0.0.
# Idempotent: erneuter Aufruf aktualisiert Radicale.

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[Radicale]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

RADICALE_USER="radicale"
RADICALE_DATA="/var/lib/radicale/collections"
RADICALE_CONF_DIR="/etc/radicale"
RADICALE_CONF="${RADICALE_CONF_DIR}/config"
RADICALE_LOG_DIR="/var/log/radicale"
RADICALE_PORT="5232"
RADICALE_VENV="/opt/radicale"
HH_CONF="/etc/hydrahive/radicale.json"

info "=== Radicale installieren ==="

_SERVER_IP="$(hostname -I | awk '{print $1}')"

# --- Abhängigkeiten ---
info "Installiere Abhängigkeiten..."
apt-get update -qq
apt-get install -y --quiet \
    python3 python3-pip python3-venv python3-dev \
    libssl-dev \
    2>/dev/null | grep -E "^(Get|Entpacken|Einrichten)" || true

# Radicale via apt versuchen, sonst venv
RADICALE_BIN=""
if apt-get install -y --quiet radicale 2>/dev/null; then
    RADICALE_BIN="$(command -v radicale 2>/dev/null || true)"
    success "Radicale via apt installiert"
fi

# Falls apt-Version nicht vorhanden oder veraltet → venv
if [ -z "${RADICALE_BIN}" ] || [ ! -x "${RADICALE_BIN}" ]; then
    info "Installiere Radicale in virtualenv ${RADICALE_VENV}..."
    python3 -m venv "${RADICALE_VENV}"
    "${RADICALE_VENV}/bin/pip" install --quiet --upgrade pip
    "${RADICALE_VENV}/bin/pip" install --quiet radicale
    RADICALE_BIN="${RADICALE_VENV}/bin/radicale"
    success "Radicale via pip in ${RADICALE_VENV} installiert"
fi

RADICALE_VERSION="$("${RADICALE_BIN}" --version 2>/dev/null | head -1 || echo 'unbekannt')"
success "Radicale ${RADICALE_VERSION} verfügbar: ${RADICALE_BIN}"

# --- System-User ---
if ! id "${RADICALE_USER}" &>/dev/null; then
    if getent group "${RADICALE_USER}" &>/dev/null; then
        useradd -r -s /bin/false -d /var/lib/radicale -g "${RADICALE_USER}" "${RADICALE_USER}"
    else
        useradd -r -s /bin/false -d /var/lib/radicale "${RADICALE_USER}"
    fi
    success "System-User '${RADICALE_USER}' angelegt"
fi

# --- Verzeichnisse ---
mkdir -p "${RADICALE_DATA}" "${RADICALE_CONF_DIR}" "${RADICALE_LOG_DIR}"
chown -R "${RADICALE_USER}:${RADICALE_USER}" /var/lib/radicale "${RADICALE_LOG_DIR}"
chmod 750 /var/lib/radicale "${RADICALE_DATA}"

# --- Konfiguration (nur schreiben wenn noch nicht vorhanden) ---
if [ ! -f "${RADICALE_CONF}" ]; then
    info "Erstelle ${RADICALE_CONF}..."
    # Passwort-Datei für htpasswd-artigen Zugang
    RADICALE_HTPASSWD="${RADICALE_CONF_DIR}/users"
    touch "${RADICALE_HTPASSWD}"
    chown "${RADICALE_USER}:${RADICALE_USER}" "${RADICALE_HTPASSWD}"
    chmod 640 "${RADICALE_HTPASSWD}"

    cat > "${RADICALE_CONF}" << CFGEOF
[server]
hosts = 0.0.0.0:${RADICALE_PORT}
max_connections = 20
max_content_length = 100000000
timeout = 30

[auth]
type = htpasswd
htpasswd_filename = ${RADICALE_HTPASSWD}
htpasswd_encryption = bcrypt
delay = 1

[storage]
filesystem_folder = ${RADICALE_DATA}

[logging]
level = warning
CFGEOF
    chown root:"${RADICALE_USER}" "${RADICALE_CONF}"
    chmod 640 "${RADICALE_CONF}"
    success "Konfiguration erstellt"

    # bcrypt-Paket installieren falls nötig (für htpasswd auth)
    if [ -x "${RADICALE_VENV}/bin/pip" ]; then
        "${RADICALE_VENV}/bin/pip" install --quiet bcrypt passlib 2>/dev/null || true
    else
        apt-get install -y --quiet python3-bcrypt python3-passlib 2>/dev/null || true
        pip3 install --quiet bcrypt passlib 2>/dev/null || true
    fi
else
    info "${RADICALE_CONF} bereits vorhanden — überspringe"
    # Sicherstellen dass Bind-Adresse korrekt ist
    sed -i "s|^hosts = .*|hosts = 0.0.0.0:${RADICALE_PORT}|" "${RADICALE_CONF}"
fi

# --- systemd Service ---
cat > /etc/systemd/system/radicale.service << SVCEOF
[Unit]
Description=Radicale CalDAV/CardDAV Server
After=network.target

[Service]
Type=simple
User=${RADICALE_USER}
Group=${RADICALE_USER}
ExecStart=${RADICALE_BIN} --config ${RADICALE_CONF}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable radicale
systemctl restart radicale
success "Service 'radicale' gestartet auf Port ${RADICALE_PORT}"

# --- Warten auf Erreichbarkeit ---
info "Warte auf Radicale (bis 20 s)..."
for i in $(seq 1 10); do
    sleep 2
    if curl -sf "http://127.0.0.1:${RADICALE_PORT}/" &>/dev/null; then
        break
    fi
done
if curl -sf "http://127.0.0.1:${RADICALE_PORT}/" &>/dev/null; then
    success "Radicale erreichbar"
else
    warn "Radicale noch nicht erreichbar — prüfe: sudo systemctl status radicale"
fi

# --- HydraHive Config ---
mkdir -p /etc/hydrahive
cat > "${HH_CONF}" << CFGEOF
{
  "installed": true,
  "url": "http://127.0.0.1:${RADICALE_PORT}",
  "port": ${RADICALE_PORT},
  "server_ip": "${_SERVER_IP}",
  "binary": "${RADICALE_BIN}",
  "config": "${RADICALE_CONF}",
  "data_dir": "${RADICALE_DATA}",
  "htpasswd": "${RADICALE_CONF_DIR}/users"
}
CFGEOF
chown hydrahive:hydrahive "${HH_CONF}" 2>/dev/null || true
chmod 640 "${HH_CONF}"

echo ""
info "=== Radicale installiert ==="
info "URL:       http://${_SERVER_IP}:${RADICALE_PORT}"
info "Daten:     ${RADICALE_DATA}"
info "Benutzer anlegen:"
info "  htpasswd-Variante: sudo python3 -c \\"
info "    'import bcrypt; print(\"user:\"+bcrypt.hashpw(b\"pass\",bcrypt.gensalt()).decode())' >> ${RADICALE_CONF_DIR}/users"
info "CalDAV-URL: http://${_SERVER_IP}:${RADICALE_PORT}/USER/COLLECTION/"
