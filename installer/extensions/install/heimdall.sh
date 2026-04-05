#!/usr/bin/env bash
# HydraHive Extension - Heimdall (App-Dashboard)
# Installiert Heimdall als Laravel/PHP-App hinter nginx.
# PHP-FPM + SQLite, kein Docker, kein Flatpak.
# Idempotent: erneuter Aufruf aktualisiert den Code via git pull.

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[Heimdall]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

HEIMDALL_DIR="/opt/heimdall"
HEIMDALL_USER="heimdall"
HEIMDALL_PORT="8400"
NGINX_CONF="/etc/nginx/sites-available/heimdall"
NGINX_ENABLED="/etc/nginx/sites-enabled/heimdall"
HH_CONF="/etc/hydrahive/heimdall.json"

info "=== Heimdall installieren ==="

# --- PHP-Version ermitteln ---
detect_php() {
    for v in 8.3 8.2 8.1 8.0; do
        if command -v "php${v}" &>/dev/null; then
            echo "${v}"; return
        fi
    done
    if command -v php &>/dev/null; then
        php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;'
        return
    fi
    echo ""
}

# --- System-Abhängigkeiten installieren ---
info "Installiere Abhängigkeiten (PHP, nginx, SQLite3, Composer)..."
apt-get update -qq
apt-get install -y --quiet \
    php-fpm php-cli php-sqlite3 php-curl php-xml php-mbstring \
    php-gd php-zip php-intl php-bcmath php-tokenizer \
    nginx \
    git curl \
    sqlite3 \
    2>/dev/null | grep -E "^(Get|Entpacken|Einrichten)" || true

# composer installieren falls nicht vorhanden
if ! command -v composer &>/dev/null; then
    info "Installiere Composer..."
    curl -fsSL https://getcomposer.org/installer -o /tmp/composer-setup.php
    php /tmp/composer-setup.php --install-dir=/usr/local/bin --filename=composer --quiet
    rm -f /tmp/composer-setup.php
    success "Composer installiert: $(composer --version 2>/dev/null | head -1)"
fi

PHP_VERSION="$(detect_php)"
[ -n "${PHP_VERSION}" ] || error "Keine PHP-Version gefunden nach Installation"
PHP_FPM_SERVICE="php${PHP_VERSION}-fpm"
PHP_FPM_SOCK="/run/php/php${PHP_VERSION}-fpm.sock"
success "PHP ${PHP_VERSION} erkannt, Socket: ${PHP_FPM_SOCK}"

# --- System-User ---
if ! id "${HEIMDALL_USER}" &>/dev/null; then
    # Gruppe existiert evtl. noch von vorherigem Install
    if getent group "${HEIMDALL_USER}" &>/dev/null; then
        useradd -r -s /bin/false -d "${HEIMDALL_DIR}" -g "${HEIMDALL_USER}" "${HEIMDALL_USER}"
    else
        useradd -r -s /bin/false -d "${HEIMDALL_DIR}" "${HEIMDALL_USER}"
    fi
    success "System-User '${HEIMDALL_USER}' angelegt"
fi
# www-data muss heimdall-Gruppe kennen für PHP-FPM
usermod -aG "${HEIMDALL_USER}" www-data 2>/dev/null || true

# --- Heimdall herunterladen / aktualisieren ---
if [ -d "${HEIMDALL_DIR}/.git" ]; then
    info "Heimdall bereits geklont — aktualisiere..."
    git -C "${HEIMDALL_DIR}" fetch --quiet origin
    git -C "${HEIMDALL_DIR}" reset --hard origin/master --quiet 2>/dev/null \
        || git -C "${HEIMDALL_DIR}" reset --hard origin/main --quiet
    sudo -u "${HEIMDALL_USER}" composer install \
        --no-dev --no-interaction --quiet \
        --working-dir="${HEIMDALL_DIR}" 2>/dev/null || true
    success "Heimdall aktualisiert"
else
    info "Klone Heimdall von GitHub..."
    rm -rf "${HEIMDALL_DIR}"
    git clone --depth 1 https://github.com/linuxserver/Heimdall.git "${HEIMDALL_DIR}" \
        || error "git clone fehlgeschlagen"
    success "Heimdall geklont"
    info "Installiere PHP-Abhängigkeiten..."
    composer install \
        --no-dev --no-interaction --quiet \
        --working-dir="${HEIMDALL_DIR}" 2>/dev/null \
        || warn "composer install hatte Fehler — Heimdall läuft ggf. trotzdem"
fi

# --- .env konfigurieren ---
if [ ! -f "${HEIMDALL_DIR}/.env" ]; then
    cp "${HEIMDALL_DIR}/.env.example" "${HEIMDALL_DIR}/.env" 2>/dev/null \
        || cat > "${HEIMDALL_DIR}/.env" << ENVEOF
APP_NAME=Heimdall
APP_ENV=production
APP_KEY=
APP_DEBUG=false
APP_URL=http://SERVER_IP_PLACEHOLDER:${HEIMDALL_PORT}
DB_CONNECTION=sqlite
DB_DATABASE=${HEIMDALL_DIR}/database/app.sqlite
LOG_CHANNEL=daily
CACHE_DRIVER=file
SESSION_DRIVER=file
QUEUE_CONNECTION=sync
ENVEOF
fi
# APP_URL auf tatsächliche Server-IP setzen
_SERVER_IP=$(hostname -I | awk '{print $1}')
sed -i "s|^APP_URL=.*|APP_URL=http://${_SERVER_IP}:${HEIMDALL_PORT}|" "${HEIMDALL_DIR}/.env"
sed -i "s|^APP_ENV=.*|APP_ENV=production|" "${HEIMDALL_DIR}/.env"

# --- App-Key generieren ---
if grep -q "^APP_KEY=$" "${HEIMDALL_DIR}/.env" || grep -q "^APP_KEY=SomeRandomString" "${HEIMDALL_DIR}/.env"; then
    php "${HEIMDALL_DIR}/artisan" key:generate --force --quiet \
        2>/dev/null || true
fi

# --- Datenbank und Storage vorbereiten ---
mkdir -p "${HEIMDALL_DIR}/database" "${HEIMDALL_DIR}/storage/framework"/{sessions,views,cache}
touch "${HEIMDALL_DIR}/database/app.sqlite" 2>/dev/null || true
php "${HEIMDALL_DIR}/artisan" migrate --force --quiet 2>/dev/null \
    || warn "Migration fehlgeschlagen — wird beim ersten Aufruf wiederholt"
php "${HEIMDALL_DIR}/artisan" storage:link --quiet 2>/dev/null || true

# --- Berechtigungen ---
chown -R "${HEIMDALL_USER}:${HEIMDALL_USER}" "${HEIMDALL_DIR}"
chmod -R 755 "${HEIMDALL_DIR}/storage" "${HEIMDALL_DIR}/bootstrap/cache" 2>/dev/null || true
chmod 664 "${HEIMDALL_DIR}/database/app.sqlite" 2>/dev/null || true
success "Berechtigungen gesetzt"

# --- PHP-FPM Pool für Heimdall ---
PHP_POOL_DIR="/etc/php/${PHP_VERSION}/fpm/pool.d"
if [ -d "${PHP_POOL_DIR}" ]; then
    cat > "${PHP_POOL_DIR}/heimdall.conf" << POOLEOF
[heimdall]
user = ${HEIMDALL_USER}
group = ${HEIMDALL_USER}
listen = /run/php/heimdall-fpm.sock
listen.owner = www-data
listen.group = www-data
listen.mode = 0660
pm = dynamic
pm.max_children = 10
pm.start_servers = 2
pm.min_spare_servers = 1
pm.max_spare_servers = 3
pm.max_requests = 500
chdir = /
POOLEOF
    PHP_FPM_SOCK="/run/php/heimdall-fpm.sock"
    systemctl restart "${PHP_FPM_SERVICE}" 2>/dev/null || true
    success "PHP-FPM Pool 'heimdall' konfiguriert"
else
    warn "PHP-FPM Pool-Verzeichnis nicht gefunden — nutze Standard-Socket"
fi

# --- nginx-Konfiguration ---
info "Konfiguriere nginx (Port ${HEIMDALL_PORT})..."
cat > "${NGINX_CONF}" << NGXEOF
server {
    listen 0.0.0.0:${HEIMDALL_PORT};
    server_name _;

    root ${HEIMDALL_DIR}/public;
    index index.php;

    access_log /var/log/nginx/heimdall-access.log;
    error_log  /var/log/nginx/heimdall-error.log;

    location / {
        try_files \$uri \$uri/ /index.php?\$query_string;
    }

    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:${PHP_FPM_SOCK};
        fastcgi_param SCRIPT_FILENAME \$realpath_root\$fastcgi_script_name;
        include fastcgi_params;
        fastcgi_read_timeout 120;
    }

    location ~ /\.(?!well-known) {
        deny all;
    }

    client_max_body_size 64M;
}
NGXEOF

ln -sf "${NGINX_CONF}" "${NGINX_ENABLED}" 2>/dev/null || true
nginx -t 2>/dev/null && systemctl reload nginx || warn "nginx-Konfigurationstest fehlgeschlagen — prüfe manuell"
success "nginx konfiguriert und neu geladen"

# --- systemd Service für Heimdall-Queue-Worker ---
cat > /etc/systemd/system/heimdall.service << SVCEOF
[Unit]
Description=Heimdall App Dashboard
After=network.target nginx.service ${PHP_FPM_SERVICE}.service
Requires=${PHP_FPM_SERVICE}.service

[Service]
Type=simple
User=${HEIMDALL_USER}
Group=${HEIMDALL_USER}
WorkingDirectory=${HEIMDALL_DIR}
ExecStart=/usr/bin/php artisan queue:work --queue=default --sleep=3 --tries=3 --timeout=90
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable nginx "${PHP_FPM_SERVICE}" heimdall
systemctl start "${PHP_FPM_SERVICE}" heimdall
systemctl reload nginx 2>/dev/null || systemctl start nginx
success "Alle Services gestartet"

# --- Warten auf Erreichbarkeit ---
info "Warte auf Heimdall (bis 20 s)..."
for i in $(seq 1 10); do
    sleep 2
    if curl -sf "http://127.0.0.1:${HEIMDALL_PORT}" &>/dev/null; then
        break
    fi
done
if curl -sf "http://127.0.0.1:${HEIMDALL_PORT}" &>/dev/null; then
    success "Heimdall erreichbar"
else
    warn "Heimdall noch nicht erreichbar — prüfe nginx + PHP-FPM Logs"
fi

# --- HydraHive Config ---
mkdir -p /etc/hydrahive
cat > "${HH_CONF}" << CFGEOF
{
  "installed": true,
  "url": "http://127.0.0.1:${HEIMDALL_PORT}",
  "port": ${HEIMDALL_PORT},
  "dir": "${HEIMDALL_DIR}",
  "php_version": "${PHP_VERSION}",
  "nginx_conf": "${NGINX_CONF}"
}
CFGEOF
chown hydrahive:hydrahive "${HH_CONF}" 2>/dev/null || true
chmod 640 "${HH_CONF}"

echo ""
info "=== Heimdall installiert ==="
info "URL:      http://127.0.0.1:${HEIMDALL_PORT}"
info "Verz.:    ${HEIMDALL_DIR}"
info "Kein Standard-Login — Registrierung direkt im Browser"
