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

# ────────────────────────────────────────────────── Fallback-Logging
# Müssen VOR dem ersten Aufruf definiert sein (falls nicht via source aus install.sh)
if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi
if ! declare -f error   &>/dev/null; then error()   { echo "[ERROR] $1"; exit 1; }; fi

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

_gitea_admin() {
    sudo -u "${GITEA_USER}" env \
        HOME="${GITEA_WORK_DIR}" \
        GITEA_WORK_DIR="${GITEA_WORK_DIR}" \
        "${GITEA_BINARY}" "$@"
}

# Guard-Variable für frühzeitigen Abbruch (ersetzt bare `return` außerhalb Funktion)
_GITEA_SKIP=0

info "Installiere Gitea ${GITEA_VERSION}..."

# ────────────────────────────────────────────────── Binary

if [ -f "${GITEA_BINARY}" ] && "${GITEA_BINARY}" --version 2>/dev/null | grep -q "${GITEA_VERSION}"; then
    info "Gitea ${GITEA_VERSION} bereits installiert — überspringe Download"
elif [ "${_GITEA_SKIP}" -eq 0 ]; then
    info "Lade Gitea ${GITEA_VERSION} herunter..."
    GITEA_URL="https://dl.gitea.com/gitea/${GITEA_VERSION}/gitea-${GITEA_VERSION}-linux-amd64"
    GITEA_SHA_URL="${GITEA_URL}.sha256"
    if ! curl -fsSL -o "${GITEA_BINARY}.tmp" "${GITEA_URL}"; then
        warn "Gitea-Download fehlgeschlagen (${GITEA_URL}) — Gitea wird nicht installiert"
        warn "Projektverwaltung ohne Git-Versionierung möglich, aber Agent-Git-Tools stehen nicht zur Verfügung"
        _GITEA_SKIP=1
    else
        mv "${GITEA_BINARY}.tmp" "${GITEA_BINARY}"
        chmod +x "${GITEA_BINARY}"
        success "Gitea-Binary: ${GITEA_BINARY}"
    fi

fi

# ────────────────────────────────────────────────── System-User + Verzeichnisse

if [ "${_GITEA_SKIP}" -eq 0 ]; then
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
fi

# ────────────────────────────────────────────────── app.ini (idempotent)

if [ "${_GITEA_SKIP}" -eq 0 ]; then
    if [ ! -f "${GITEA_CONF}" ]; then
        info "Schreibe Gitea-Konfiguration..."

        SK=$(openssl rand -hex 32)
        IT=$(openssl rand -hex 32)
        _raw="$(openssl rand -base64 64)"; _clean="${_raw//[\/+=]/}"; JWT="${_clean:0:43}"
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
fi

# ────────────────────────────────────────────────── Systemd-Service

if [ "${_GITEA_SKIP}" -eq 0 ]; then
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
fi

# ────────────────────────────────────────────────── Warten bis Gitea antwortet

if [ "${_GITEA_SKIP}" -eq 0 ]; then
    GITEA_OK=0
    for i in $(seq 1 20); do
        sleep 3
        if curl -sf --max-time 5 "http://127.0.0.1:${GITEA_PORT}/api/v1/version" &>/dev/null; then
            GITEA_OK=1
            break
        fi
        info "Warte auf Gitea... (${i}/20)"
    done

    if [ "${GITEA_OK}" -eq 0 ]; then
        warn "Gitea antwortet nicht nach 60s — pruefe: journalctl -u ${GITEA_SERVICE} -n 20"
        warn "Admin-User und Token werden übersprungen — bei Neuinstall erneut versuchen"
        _GITEA_SKIP=1
    else
        success "Gitea läuft auf http://127.0.0.1:${GITEA_PORT}"
    fi
fi

# ────────────────────────────────────────────────── Admin-User + API-Token

if [ "${_GITEA_SKIP}" -eq 0 ]; then
    # Gitea-Admin-Passwort: gleich wie HydraHive-Admin oder generiert
    if [ -z "${GITEA_ADMIN_PASS:-}" ]; then
        _raw="$(openssl rand -base64 32)"; _clean="${_raw//[\/+=]/}"; GITEA_ADMIN_PASS="${CONSOLE_PASS:-${_clean:0:20}}"
    fi

    # Kurze Extrapause damit SQLite vollständig initialisiert ist (verhindert "database is locked")
    sleep 3

    # Prüfen ob Admin bereits existiert
    EXISTING_USER=$(curl -sf "http://127.0.0.1:${GITEA_PORT}/api/v1/users/search?q=${GITEA_ADMIN}" \
        | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok' if d.get('data') and len(d['data'])>0 else 'missing')" 2>/dev/null || echo "missing")

    if [ "${EXISTING_USER}" = "missing" ]; then
        info "Lege Gitea-Admin '${GITEA_ADMIN}' an..."
        _gitea_admin_log="$(mktemp)"
        if _gitea_admin admin user create \
            --config "${GITEA_CONF}" \
            --work-path "${GITEA_WORK_DIR}" \
            --username "${GITEA_ADMIN}" \
            --password "${GITEA_ADMIN_PASS}" \
            --email "admin@hydrahive.local" \
            --admin \
            --must-change-password=false >"${_gitea_admin_log}" 2>&1; then
            grep -v "^$" "${_gitea_admin_log}" || true
            rm -f "${_gitea_admin_log}"
            success "Gitea-Admin '${GITEA_ADMIN}' angelegt"
        else
            grep -v "^$" "${_gitea_admin_log}" || true
            rm -f "${_gitea_admin_log}"
            warn "Gitea-Admin '${GITEA_ADMIN}' konnte nicht angelegt werden — Token wird übersprungen"
            _GITEA_SKIP=1
        fi
    else
        info "Gitea-Admin '${GITEA_ADMIN}' bereits vorhanden — aktualisiere Passwort"
        _gitea_admin_log="$(mktemp)"
        if _gitea_admin admin user change-password \
            --config "${GITEA_CONF}" \
            --work-path "${GITEA_WORK_DIR}" \
            --username "${GITEA_ADMIN}" \
            --password "${GITEA_ADMIN_PASS}" >"${_gitea_admin_log}" 2>&1; then
            grep -v "^$" "${_gitea_admin_log}" || true
            rm -f "${_gitea_admin_log}"
            info "Gitea-Admin '${GITEA_ADMIN}' Passwort aktualisiert"
        else
            grep -v "^$" "${_gitea_admin_log}" || true
            rm -f "${_gitea_admin_log}"
            warn "Gitea-Admin '${GITEA_ADMIN}' Passwort konnte nicht aktualisiert werden — Token wird übersprungen"
            _GITEA_SKIP=1
        fi
    fi

    # API-Token generieren (immer neu — vorherige werden ungültig nach Restart)
    GITEA_TOKEN=""
    if [ "${_GITEA_SKIP}" -eq 0 ]; then
        GITEA_TOKEN=$(_gitea_admin admin user generate-access-token \
            --config "${GITEA_CONF}" \
            --work-path "${GITEA_WORK_DIR}" \
            --username "${GITEA_ADMIN}" \
            --token-name "hydrahive-core-$(date +%s)" \
            --scopes "write:repository,read:repository,write:user,read:user,write:issue,read:issue,write:notification" \
            --raw 2>/dev/null | tr -d '[:space:]') || true

        if [ -z "${GITEA_TOKEN}" ]; then
            warn "API-Token konnte nicht generiert werden — Git-Tools arbeiten ohne Authentifizierung"
            GITEA_TOKEN=""
        fi
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
    chown hydrahive:hydrahive "${GITEA_CONFIG_FILE}" 2>/dev/null || true
    chmod 600 "${GITEA_CONFIG_FILE}"
    success "Gitea-Config: ${GITEA_CONFIG_FILE}"

    # Gitea-Credentials explizit in admin_credentials speichern
    _CRED_FILE="/etc/hydrahive/admin_credentials"
    touch "${_CRED_FILE}"
    if ! grep -q '^gitea_username=' "${_CRED_FILE}" 2>/dev/null; then
        echo "gitea_username=${GITEA_ADMIN}" >> "${_CRED_FILE}"
    else
        sed -i "s|^gitea_username=.*|gitea_username=${GITEA_ADMIN}|" "${_CRED_FILE}"
    fi
    if ! grep -q '^gitea_password=' "${_CRED_FILE}" 2>/dev/null; then
        echo "gitea_password=${GITEA_ADMIN_PASS}" >> "${_CRED_FILE}"
    else
        sed -i "s|^gitea_password=.*|gitea_password=${GITEA_ADMIN_PASS}|" "${_CRED_FILE}"
    fi
    success "Gitea-Credentials in admin_credentials gespeichert"
fi

# ────────────────────────────────────────────────── nginx-Proxy (Port 3002)
# nginx-Config wird immer angelegt wenn Binary vorhanden (unabhängig von Health-Check)

if [ -f "${GITEA_BINARY}" ]; then
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
fi

# ────────────────────────────────────────────────── Ergebnis

SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")

if [ "${_GITEA_SKIP}" -eq 0 ]; then
    success "Gitea installiert und konfiguriert"
    info "  Intern:   http://127.0.0.1:${GITEA_PORT}"
    info "  Extern:   http://${SERVER_IP}:${GITEA_NGINX_PORT}"
    info "  Login:    ${GITEA_ADMIN} / ${GITEA_ADMIN_PASS:-siehe ${GITEA_CONFIG_FILE}}"
    info "  API-Token in: ${GITEA_CONFIG_FILE}"
    export GITEA_ADMIN_PASS
else
    warn "Gitea wurde nicht vollständig installiert — HydraHive läuft ohne Git-Integration"
fi
