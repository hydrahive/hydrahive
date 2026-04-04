#!/usr/bin/env bash
# HydraHive Extension - Monica CRM deinstallieren
set -euo pipefail

if ! command -v info &>/dev/null 2>&1 || ! type -t info | grep -q function; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[Monica]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

info "=== Monica CRM deinstallieren ==="

# Service stoppen
systemctl stop monica 2>/dev/null || true
systemctl disable monica 2>/dev/null || true
rm -f /etc/systemd/system/monica.service
systemctl daemon-reload

# Datenbank löschen (Config lesen für Credentials)
MONICA_CONFIG="/etc/hydrahive/monica.json"
if [ -f "${MONICA_CONFIG}" ]; then
    DB_NAME=$(python3 -c "import json; print(json.load(open('${MONICA_CONFIG}')).get('db_name','monica'))" 2>/dev/null || echo "monica")
    DB_USER=$(python3 -c "import json; print(json.load(open('${MONICA_CONFIG}')).get('db_user','monica'))" 2>/dev/null || echo "monica")
    mysql -e "DROP DATABASE IF EXISTS ${DB_NAME};" 2>/dev/null || true
    mysql -e "DROP USER IF EXISTS '${DB_USER}'@'localhost';" 2>/dev/null || true
    success "Datenbank '${DB_NAME}' gelöscht"
fi

# Dateien entfernen
rm -rf /opt/monica
rm -f "${MONICA_CONFIG}"

# System-User entfernen
userdel -r monica 2>/dev/null || true

# nginx Proxy entfernen
NGINX_CONF="/etc/nginx/sites-available/hydrahive-console"
if [ -f "${NGINX_CONF}" ]; then
    sed -i '/# Monica CRM/,/}/d' "${NGINX_CONF}" 2>/dev/null || true
    nginx -t 2>/dev/null && systemctl reload nginx 2>/dev/null || true
fi

success "Monica CRM deinstalliert"
