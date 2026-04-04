#!/usr/bin/env bash
# HydraHive Extension - Monica CRM (native Installation)
# Installiert Monica als Laravel-App mit MySQL auf localhost.
# Idempotent: erneuter Aufruf aktualisiert Code + Konfiguration.
# Kann standalone ausgeführt werden: sudo bash monica-crm.sh

set -euo pipefail

if ! command -v info &>/dev/null 2>&1 || ! type -t info | grep -q function; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[Monica]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

MONICA_DIR="/opt/monica"
MONICA_USER="monica"
MONICA_DB="monica"
MONICA_DB_USER="monica"
MONICA_DB_PASS="$(head -c 16 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 24)"
MONICA_PORT="8180"

info "=== Monica CRM installieren ==="

# --- Prüfe ob bereits installiert ---
if [ -d "${MONICA_DIR}" ] && [ -f "${MONICA_DIR}/.env" ]; then
    info "Monica bereits installiert — aktualisiere..."
    cd "${MONICA_DIR}"
    sudo -u "${MONICA_USER}" git pull --quiet 2>/dev/null || true
    sudo -u "${MONICA_USER}" composer install --no-dev --no-interaction --quiet 2>/dev/null || true
    sudo -u "${MONICA_USER}" php artisan migrate --force --quiet 2>/dev/null || true
    success "Monica aktualisiert"
    exit 0
fi

# --- System-Abhängigkeiten ---
info "Installiere Abhängigkeiten (PHP 8.2, MySQL, Composer)..."
apt-get update -qq

# PHP + Extensions
apt-get install -y --quiet \
    php8.3-fpm php8.3-cli php8.3-mysql php8.3-xml php8.3-mbstring \
    php8.3-curl php8.3-zip php8.3-gd php8.3-intl php8.3-bcmath \
    php8.3-redis php8.3-common \
    2>/dev/null | grep -E "^(Get|Entpacken|Einrichten)" || true

# Falls PHP 8.3 nicht verfügbar, 8.2 oder 8.1 versuchen
if ! command -v php8.3 &>/dev/null; then
    for phpv in php8.2 php8.1; do
        v="${phpv#php}"
        apt-get install -y --quiet \
            ${phpv}-fpm ${phpv}-cli ${phpv}-mysql ${phpv}-xml ${phpv}-mbstring \
            ${phpv}-curl ${phpv}-zip ${phpv}-gd ${phpv}-intl ${phpv}-bcmath \
            ${phpv}-redis ${phpv}-common 2>/dev/null && break || true
    done
fi
PHP_BIN=$(which php8.3 || which php8.2 || which php8.1 || which php)
success "PHP: $($PHP_BIN --version | head -1)"

# MySQL/MariaDB
if ! command -v mysql &>/dev/null; then
    apt-get install -y --quiet mariadb-server mariadb-client \
        2>/dev/null | grep -E "^(Get|Entpacken|Einrichten)" || true
    systemctl enable --now mariadb
fi
success "MySQL/MariaDB verfügbar"

# Composer
if ! command -v composer &>/dev/null; then
    curl -sS https://getcomposer.org/installer | $PHP_BIN -- --install-dir=/usr/local/bin --filename=composer --quiet
fi
success "Composer verfügbar"

# --- System-User ---
if ! id "${MONICA_USER}" &>/dev/null; then
    useradd -r -s /bin/false -d "${MONICA_DIR}" -m "${MONICA_USER}"
    success "System-User '${MONICA_USER}' angelegt"
fi

# --- Datenbank ---
info "Richte Datenbank ein..."
mysql -e "CREATE DATABASE IF NOT EXISTS ${MONICA_DB} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" 2>/dev/null
mysql -e "CREATE USER IF NOT EXISTS '${MONICA_DB_USER}'@'localhost' IDENTIFIED BY '${MONICA_DB_PASS}';" 2>/dev/null
mysql -e "GRANT ALL PRIVILEGES ON ${MONICA_DB}.* TO '${MONICA_DB_USER}'@'localhost';" 2>/dev/null
mysql -e "FLUSH PRIVILEGES;" 2>/dev/null
success "Datenbank '${MONICA_DB}' bereit"

# --- Monica herunterladen ---
info "Lade Monica herunter..."
git clone --depth 1 https://github.com/monicahq/monica.git "${MONICA_DIR}" 2>/dev/null
chown -R "${MONICA_USER}:${MONICA_USER}" "${MONICA_DIR}"
success "Monica geklont nach ${MONICA_DIR}"

# --- .env konfigurieren ---
cd "${MONICA_DIR}"
sudo -u "${MONICA_USER}" cp .env.example .env

# App-Key generieren und Konfiguration setzen
MONICA_APP_KEY=$(sudo -u "${MONICA_USER}" $PHP_BIN artisan key:generate --show 2>/dev/null || echo "")
if [ -z "${MONICA_APP_KEY}" ]; then
    MONICA_APP_KEY="base64:$(head -c 32 /dev/urandom | base64)"
fi

cat > "${MONICA_DIR}/.env" << ENVEOF
APP_NAME=Monica
APP_ENV=production
APP_KEY=${MONICA_APP_KEY}
APP_DEBUG=false
APP_URL=http://localhost:${MONICA_PORT}
APP_TRUSTED_PROXIES=*

LOG_CHANNEL=daily

DB_CONNECTION=mysql
DB_HOST=127.0.0.1
DB_PORT=3306
DB_DATABASE=${MONICA_DB}
DB_USERNAME=${MONICA_DB_USER}
DB_PASSWORD=${MONICA_DB_PASS}

CACHE_DRIVER=file
SESSION_DRIVER=file
QUEUE_CONNECTION=sync

MAIL_MAILER=log

DEFAULT_MAX_UPLOAD_SIZE=10240
ENVEOF

chown "${MONICA_USER}:${MONICA_USER}" "${MONICA_DIR}/.env"
chmod 600 "${MONICA_DIR}/.env"
success ".env konfiguriert"

# --- Composer Install ---
info "Installiere PHP-Abhängigkeiten (dauert ~2 Min)..."
sudo -u "${MONICA_USER}" composer install --no-dev --no-interaction --optimize-autoloader --quiet \
    --working-dir="${MONICA_DIR}" 2>&1 | tail -3 || error "Composer install fehlgeschlagen"
success "Composer install abgeschlossen"

# --- Datenbank-Migration ---
info "Führe Datenbank-Migration aus..."
cd "${MONICA_DIR}"
sudo -u "${MONICA_USER}" $PHP_BIN artisan migrate --force --quiet
sudo -u "${MONICA_USER}" $PHP_BIN artisan storage:link --quiet 2>/dev/null || true
success "Datenbank migriert"

# --- systemd Service ---
cat > /etc/systemd/system/monica.service << SVCEOF
[Unit]
Description=Monica CRM
After=network.target mariadb.service

[Service]
Type=simple
User=${MONICA_USER}
Group=${MONICA_USER}
WorkingDirectory=${MONICA_DIR}
ExecStart=${PHP_BIN} artisan serve --host=127.0.0.1 --port=${MONICA_PORT}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable --now monica
success "systemd Service 'monica' gestartet auf Port ${MONICA_PORT}"

# --- nginx Proxy ---
NGINX_CONF="/etc/nginx/sites-available/hydrahive-console"
if [ -f "${NGINX_CONF}" ] && ! grep -q "location /monica" "${NGINX_CONF}"; then
    # Vor dem letzten } einfügen
    sed -i '/^}/i \
    # Monica CRM\
    location /monica/ {\
        proxy_pass http://127.0.0.1:'"${MONICA_PORT}"'/;\
        proxy_set_header Host $host;\
        proxy_set_header X-Real-IP $remote_addr;\
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\
        proxy_set_header X-Forwarded-Proto $scheme;\
    }' "${NGINX_CONF}"
    nginx -t 2>/dev/null && systemctl reload nginx
    success "nginx Proxy: /monica/ → localhost:${MONICA_PORT}"
fi

# --- HydraHive Config speichern ---
MONICA_CONFIG="/etc/hydrahive/monica.json"
cat > "${MONICA_CONFIG}" << CFGEOF
{
  "installed": true,
  "url": "http://127.0.0.1:${MONICA_PORT}",
  "port": ${MONICA_PORT},
  "db_name": "${MONICA_DB}",
  "db_user": "${MONICA_DB_USER}",
  "db_pass": "${MONICA_DB_PASS}",
  "app_key": "${MONICA_APP_KEY}"
}
CFGEOF
chown hydrahive:hydrahive "${MONICA_CONFIG}" 2>/dev/null || true
chmod 600 "${MONICA_CONFIG}"
success "Config gespeichert: ${MONICA_CONFIG}"

# --- Warten bis Monica läuft ---
info "Warte auf Monica..."
for i in $(seq 1 15); do
    sleep 2
    if curl -sf "http://127.0.0.1:${MONICA_PORT}" &>/dev/null; then
        break
    fi
done

if curl -sf "http://127.0.0.1:${MONICA_PORT}" &>/dev/null; then
    success "Monica CRM läuft auf http://127.0.0.1:${MONICA_PORT}"
else
    warn "Monica startet noch — prüfe mit: sudo systemctl status monica"
fi

echo ""
info "=== Monica CRM installiert ==="
info "URL:      http://127.0.0.1:${MONICA_PORT}"
info "Erstelle deinen ersten Account auf der Weboberfläche"
info "API-Token: Monica → Settings → API → Create New Token"
info "Plugin: Trage URL + Token in Mein Agent → Kontakte ein"
