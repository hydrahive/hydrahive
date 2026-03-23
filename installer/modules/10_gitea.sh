#!/usr/bin/env bash
# HydraHive Installer - Modul 10: Gitea (lokales Git-Repository-System)
#
# - Lädt Gitea-Binary herunter und installiert sie nach /usr/local/bin/gitea
# - Legt git-Systemuser + Verzeichnisse an (/opt/gitea, /etc/gitea)
# - Schreibt app.ini mit SQLite-Backend, Port 3001 (nur localhost)
# - Erstellt systemd-Service und startet Gitea
# - Legt Admin-User 'hydrahive' an, generiert API-Token
# - Speichert Token nach /etc/hydrahive/gitea_config.json
# - Fügt nginx-Proxy auf Port 3002 hinzu (externer Zugriff)
# - Idempotent: bereits installiertes Gitea wird übersprungen / nur Token erneuert

GITEA_VERSION="1.21.11"
GITEA_BINARY="/usr/local/bin/gitea"
GITEA_WORK_DIR="/opt/gitea"
GITEA_CONF_DIR="/etc/gitea"
GITEA_CONF="${GITEA_CONF_DIR}/app.ini"
GITEA_SERVICE="gitea"
GITEA_USER="git"
GITEA_PORT="3001"
GITEA_NGINX_PORT="3002"
GITEA_ADMIN="hydrahive"
GITEA_CONFIG_FILE="/etc/hydrahive/gitea_config.json"
NGINX_GITEA_CONF="/etc/nginx/sites-available/gitea"

info "Installiere Gitea ${GITEA_VERSION}..."

# ────────────────────────────────────────────────── Binary

if [ -f "${GITEA_BINARY}" ] && "${GITEA_BINARY}" --version 2>/dev/null | grep -q "${GITEA_VERSION}"; then
    info "Gitea ${GITEA_VERSION} bereits installiert — überspringe Download"
else
    info "Lade Gitea ${GITEA_VERSION} herunter..."
    GITEA_URL="https://dl.gitea.com/gitea/${GITEA_VERSION}/gitea-${GITEA_VERSION}-linux-amd64"
    if ! curl -fsSL -o "${GITEA_BINARY}.tmp" "${GITEA_URL}"; then
        warn "Gitea-Download fehlgeschlagen (${GITEA_URL}) — Gitea wird nicht installiert"
        warn "Projektverwaltung ohne Git-Versionierung möglich, aber Agent-Git-Tools stehen nicht zur Verfügung"
        exit 0   # kein harter Fehler — HydraHive funktioniert ohne Gitea
    fi
    mv "${GITEA_BINARY}.tmp" "${GITEA_BINARY}"
    chmod +x "${GITEA_BINARY}"
    success "Gitea-Binary: ${GITEA_BINARY}"
fi

# ────────────────────────────────────────────────── System-User + Verzeichnisse

if ! id "${GITEA_USER}" &>/dev/null; then
    useradd --system --shell /bin/bash --home-dir "${GITEA_WORK_DIR}" --create-home "${GITEA_USER}"
    success "System-User '${GITEA_USER}' angelegt"
else
    info "System-User '${GITEA_USER}' bereits vorhanden"
fi

mkdir -p "${GITEA_WORK_DIR}/data" "${GITEA_WORK_DIR}/log" "${GITEA_WORK_DIR}/custom"
chown -R "${GITEA_USER}:${GITEA_USER}" "${GITEA_WORK_DIR}"

mkdir -p "${GITEA_CONF_DIR}"
chown root:"${GITEA_USER}" "${GITEA_CONF_DIR}"
chmod 750 "${GITEA_CONF_DIR}"

# ────────────────────────────────────────────────── app.ini (idempotent)

if [ ! -f "${GITEA_CONF}" ]; then
    info "Schreibe Gitea-Konfiguration..."

    SK=$(openssl rand -hex 32)
    IT=$(openssl rand -hex 32)
    JWT=$(openssl rand -base64 32 | tr -d '=+/' | head -c 43)
    SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")

    cat > "${GITEA_CONF}" << APPINI
APP_NAME = HydraHive Gitea
RUN_USER = ${GITEA_USER}
RUN_MODE = prod

[database]
DB_TYPE  = sqlite3
PATH     = ${GITEA_WORK_DIR}/data/gitea.db

[repository]
ROOT = ${GITEA_WORK_DIR}/data/repositories

[server]
HTTP_ADDR    = 127.0.0.1
HTTP_PORT    = ${GITEA_PORT}
ROOT_URL     = http://${SERVER_IP}:${GITEA_NGINX_PORT}/
DOMAIN       = ${SERVER_IP}
DISABLE_SSH  = true
OFFLINE_MODE = true

[security]
INSTALL_LOCK   = true
SECRET_KEY     = ${SK}
INTERNAL_TOKEN = ${IT}

[oauth2]
JWT_SECRET = ${JWT}

[service]
DISABLE_REGISTRATION      = true
REQUIRE_SIGNIN_VIEW       = false
DEFAULT_KEEP_EMAIL_PRIVATE = true

[log]
ROOT_PATH = ${GITEA_WORK_DIR}/log
MODE      = file
LEVEL     = Warn

[webhook]
ALLOWED_HOST_LIST = *
APPINI

    chown root:"${GITEA_USER}" "${GITEA_CONF}"
    chmod 660 "${GITEA_CONF}"
    success "Gitea app.ini geschrieben"
else
    info "Gitea app.ini bereits vorhanden — überspringe"
fi

# ────────────────────────────────────────────────── Systemd-Service

cat > "/etc/systemd/system/${GITEA_SERVICE}.service" << UNIT
[Unit]
Description=Gitea (HydraHive Git-Server)
After=network.target

[Service]
Type=simple
User=${GITEA_USER}
Group=${GITEA_USER}
WorkingDirectory=${GITEA_WORK_DIR}
ExecStart=${GITEA_BINARY} web --config ${GITEA_CONF} --work-path ${GITEA_WORK_DIR}
Restart=always
RestartSec=5
Environment=HOME=${GITEA_WORK_DIR}

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "${GITEA_SERVICE}" &>/dev/null

if systemctl is-active --quiet "${GITEA_SERVICE}"; then
    systemctl restart "${GITEA_SERVICE}"
    success "Gitea neugestartet"
else
    systemctl start "${GITEA_SERVICE}"
    success "Gitea gestartet"
fi

# Warten bis Gitea antwortet
GITEA_OK=0
for i in 1 2 3 4 5; do
    sleep 3
    if curl -sf "http://127.0.0.1:${GITEA_PORT}/api/v1/version" &>/dev/null; then
        GITEA_OK=1
        break
    fi
    info "Warte auf Gitea... (${i}/5)"
done

if [ "${GITEA_OK}" -eq 0 ]; then
    warn "Gitea antwortet nicht — pruefe: journalctl -u ${GITEA_SERVICE} -n 20"
    warn "Git-Tools stehen möglicherweise nicht zur Verfügung"
    exit 0
fi
success "Gitea läuft auf http://127.0.0.1:${GITEA_PORT}"

# ────────────────────────────────────────────────── Admin-User + API-Token

# Gitea-Admin-Passwort: gleich wie HydraHive-Admin oder generiert
GITEA_ADMIN_PASS="${CONSOLE_PASS:-$(openssl rand -base64 18 | tr -d '/+=' | head -c 20)}"

# Prüfen ob Admin bereits existiert
EXISTING_USER=$(curl -sf "http://127.0.0.1:${GITEA_PORT}/api/v1/users/search?q=${GITEA_ADMIN}" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok' if d.get('data') else '')" 2>/dev/null || echo "")

if [ -z "${EXISTING_USER}" ]; then
    sudo -u "${GITEA_USER}" GITEA_WORK_DIR="${GITEA_WORK_DIR}" \
        "${GITEA_BINARY}" admin user create \
        --config "${GITEA_CONF}" \
        --work-path "${GITEA_WORK_DIR}" \
        --username "${GITEA_ADMIN}" \
        --password "${GITEA_ADMIN_PASS}" \
        --email "admin@hydrahive.local" \
        --admin 2>&1 | grep -v "^$" || true
    success "Gitea-Admin '${GITEA_ADMIN}' angelegt"
else
    # Passwort aktualisieren damit es synchron bleibt
    sudo -u "${GITEA_USER}" GITEA_WORK_DIR="${GITEA_WORK_DIR}" \
        "${GITEA_BINARY}" admin user change-password \
        --config "${GITEA_CONF}" \
        --work-path "${GITEA_WORK_DIR}" \
        --username "${GITEA_ADMIN}" \
        --password "${GITEA_ADMIN_PASS}" 2>&1 | grep -v "^$" || true
    info "Gitea-Admin '${GITEA_ADMIN}' Passwort aktualisiert"
fi

# API-Token generieren (immer neu — vorherige werden ungültig nach Restart)
GITEA_TOKEN=$(sudo -u "${GITEA_USER}" GITEA_WORK_DIR="${GITEA_WORK_DIR}" \
    "${GITEA_BINARY}" admin user generate-access-token \
    --config "${GITEA_CONF}" \
    --work-path "${GITEA_WORK_DIR}" \
    --username "${GITEA_ADMIN}" \
    --token-name "hydrahive-core-$(date +%s)" \
    --scopes "write:repository,read:repository,write:user,read:user,write:issue,read:issue,write:notification" \
    --raw 2>/dev/null | tr -d '[:space:]')

if [ -z "${GITEA_TOKEN}" ]; then
    warn "API-Token konnte nicht generiert werden — Git-Tools arbeiten ohne Authentifizierung"
    GITEA_TOKEN=""
fi

# Token in /etc/hydrahive/gitea_config.json speichern
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
cat > "${GITEA_CONFIG_FILE}" << GITCFG
{
  "url": "http://127.0.0.1:${GITEA_PORT}",
  "token": "${GITEA_TOKEN}",
  "org": "${GITEA_ADMIN}",
  "webhook_secret": ""
}
GITCFG
chown hydrahive:octopos "${GITEA_CONFIG_FILE}" 2>/dev/null || true
chmod 600 "${GITEA_CONFIG_FILE}"
success "Gitea-Config: ${GITEA_CONFIG_FILE}"

# ────────────────────────────────────────────────── nginx-Proxy (Port 3002)

cat > "${NGINX_GITEA_CONF}" << NGINXCONF
server {
    listen ${GITEA_NGINX_PORT};
    server_name _;

    client_max_body_size 50M;

    location / {
        proxy_pass         http://127.0.0.1:${GITEA_PORT};
        proxy_set_header   Host             \$host;
        proxy_set_header   X-Real-IP        \$remote_addr;
        proxy_set_header   X-Forwarded-For  \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto http;
    }
}
NGINXCONF

ln -sf "${NGINX_GITEA_CONF}" "/etc/nginx/sites-enabled/gitea" 2>/dev/null || true

if nginx -t &>/dev/null; then
    systemctl reload nginx &>/dev/null && success "nginx: Gitea-Proxy auf Port ${GITEA_NGINX_PORT} aktiviert"
else
    warn "nginx -t Fehler nach Gitea-Konfiguration:"
    nginx -t
fi

# ────────────────────────────────────────────────── Ergebnis

success "Gitea installiert und konfiguriert"
info "  Intern:   http://127.0.0.1:${GITEA_PORT}"
info "  Extern:   http://${SERVER_IP}:${GITEA_NGINX_PORT}"
info "  Login:    ${GITEA_ADMIN} / ${GITEA_ADMIN_PASS}"
info "  API-Token in: ${GITEA_CONFIG_FILE}"

export GITEA_ADMIN_PASS
