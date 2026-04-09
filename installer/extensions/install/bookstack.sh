#!/usr/bin/env bash
# HydraHive Extension - BookStack (Wiki/Dokumentation)
# Installiert BookStack als Laravel-App mit MariaDB hinter nginx.
# PHP-FPM + MariaDB, kein Docker, kein Flatpak.
# Idempotent: erneuter Aufruf aktualisiert den Code via git pull.

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[BookStack]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

BS_DIR="/opt/bookstack"
BS_USER="bookstack"
BS_DB="bookstack"
BS_DB_USER="bookstack"
BS_DB_PASS="$(head -c 16 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 24)"
BS_PORT="8500"
HH_CONF="/etc/hydrahive/bookstack.json"

info "=== BookStack installieren ==="

_SERVER_IP="$(hostname -I | awk '{print $1}')"

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

# --- System-Abhängigkeiten ---
info "Installiere Abhängigkeiten (PHP, MariaDB, Composer, git)..."
apt-get update -qq
apt-get install -y --quiet \
    php-cli php-mysql php-curl php-xml php-mbstring \
    php-gd php-zip php-intl php-ldap php-tokenizer \
    mariadb-server mariadb-client \
    git curl unzip \
    2>/dev/null | grep -E "^(Get|Entpacken|Einrichten)" || true

PHP_VERSION="$(detect_php)"
[ -n "${PHP_VERSION}" ] || error "Keine PHP-Version gefunden nach Installation"
success "PHP ${PHP_VERSION} erkannt"

# Composer installieren falls nicht vorhanden
if ! command -v composer &>/dev/null; then
    info "Installiere Composer..."
    curl -fsSL https://getcomposer.org/installer -o /tmp/composer-setup.php
    php /tmp/composer-setup.php --install-dir=/usr/local/bin --filename=composer --quiet
    rm -f /tmp/composer-setup.php
    success "Composer installiert"
fi

# MariaDB starten
systemctl enable --now mariadb

# --- System-User ---
if ! id "${BS_USER}" &>/dev/null; then
    if getent group "${BS_USER}" &>/dev/null; then
        useradd -r -s /bin/false -d "${BS_DIR}" -g "${BS_USER}" "${BS_USER}"
    else
        useradd -r -s /bin/false -d "${BS_DIR}" "${BS_USER}"
    fi
    success "System-User '${BS_USER}' angelegt"
fi
usermod -aG "${BS_USER}" www-data 2>/dev/null || true

# --- BookStack herunterladen / aktualisieren ---
if [ -d "${BS_DIR}/.git" ]; then
    info "BookStack bereits geklont — aktualisiere..."
    # Erkennen ob alte nginx-Installation oder neue artisan-serve
    _USES_NGINX=false
    [ -f /etc/nginx/sites-enabled/bookstack ] && _USES_NGINX=true
    if $_USES_NGINX; then
        systemctl stop bookstack-queue 2>/dev/null || true
    else
        systemctl stop bookstack bookstack-queue 2>/dev/null || true
    fi
    git -C "${BS_DIR}" fetch --quiet origin
    git -C "${BS_DIR}" reset --hard origin/release --quiet 2>/dev/null \
        || git -C "${BS_DIR}" reset --hard origin/main --quiet
    chown -R "${BS_USER}:${BS_USER}" "${BS_DIR}"
    sudo -u "${BS_USER}" composer install \
        --no-dev --no-interaction --quiet \
        --working-dir="${BS_DIR}" 2>/dev/null || true
    sudo -u "${BS_USER}" php "${BS_DIR}/artisan" migrate --force --quiet 2>/dev/null || true
    success "BookStack aktualisiert"
    if $_USES_NGINX; then
        systemctl reload nginx 2>/dev/null || true
        systemctl start bookstack-queue 2>/dev/null || true
    else
        systemctl start bookstack bookstack-queue 2>/dev/null || true
    fi
    exit 0
fi

info "Klone BookStack von GitHub..."
rm -rf "${BS_DIR}"
git clone --depth 1 --branch release \
    https://github.com/BookStackApp/BookStack.git "${BS_DIR}" \
    || git clone --depth 1 \
        https://github.com/BookStackApp/BookStack.git "${BS_DIR}" \
    || error "git clone fehlgeschlagen"
success "BookStack geklont"

# --- Datenbank einrichten ---
info "Richte Datenbank ein..."
# Nur anlegen wenn noch nicht vorhanden (idempotent)
mysql -e "CREATE DATABASE IF NOT EXISTS ${BS_DB} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" 2>/dev/null
mysql -e "CREATE USER IF NOT EXISTS '${BS_DB_USER}'@'localhost' IDENTIFIED BY '${BS_DB_PASS}';" 2>/dev/null
mysql -e "GRANT ALL PRIVILEGES ON ${BS_DB}.* TO '${BS_DB_USER}'@'localhost';" 2>/dev/null
mysql -e "FLUSH PRIVILEGES;" 2>/dev/null
success "Datenbank '${BS_DB}' bereit"

# --- .env konfigurieren ---
cp "${BS_DIR}/.env.example" "${BS_DIR}/.env" 2>/dev/null \
    || touch "${BS_DIR}/.env"

cat > "${BS_DIR}/.env" << ENVEOF
APP_NAME=BookStack
APP_ENV=production
APP_KEY=
APP_DEBUG=false
APP_URL=http://${_SERVER_IP}:${BS_PORT}

LOG_CHANNEL=daily

DB_CONNECTION=mysql
DB_HOST=127.0.0.1
DB_PORT=3306
DB_DATABASE=${BS_DB}
DB_USERNAME=${BS_DB_USER}
DB_PASSWORD=${BS_DB_PASS}

CACHE_DRIVER=file
SESSION_DRIVER=file
QUEUE_CONNECTION=sync

MAIL_DRIVER=log
ENVEOF

chown "${BS_USER}:${BS_USER}" "${BS_DIR}/.env"
chmod 640 "${BS_DIR}/.env"

# --- PHP-Abhängigkeiten installieren ---
info "Installiere PHP-Abhängigkeiten via Composer..."
chown -R "${BS_USER}:${BS_USER}" "${BS_DIR}"
sudo -u "${BS_USER}" composer install \
    --no-dev --no-interaction --quiet \
    --working-dir="${BS_DIR}" 2>/dev/null \
    || warn "composer install hatte Fehler — BookStack läuft ggf. trotzdem"

# --- App-Key generieren ---
php "${BS_DIR}/artisan" key:generate --force --quiet 2>/dev/null \
    || { FALLBACK_KEY="base64:$(head -c 32 /dev/urandom | base64)"; \
         sed -i "s|APP_KEY=|APP_KEY=${FALLBACK_KEY}|" "${BS_DIR}/.env"; }
success "App-Key generiert"

# --- Storage + Symlinks ---
mkdir -p "${BS_DIR}/storage/framework"/{sessions,views,cache} \
         "${BS_DIR}/storage/uploads"/{images,files,drawio} \
         "${BS_DIR}/bootstrap/cache"
php "${BS_DIR}/artisan" storage:link --quiet 2>/dev/null || true

# --- Datenbank-Migration ---
info "Führe Datenbank-Migration aus..."
php "${BS_DIR}/artisan" migrate --force --quiet \
    || warn "Migration fehlgeschlagen — beim ersten Aufruf wiederholt"

# --- Berechtigungen ---
chown -R "${BS_USER}:${BS_USER}" "${BS_DIR}"
chmod -R 755 "${BS_DIR}/storage" "${BS_DIR}/bootstrap/cache" 2>/dev/null || true
success "Berechtigungen gesetzt"

# --- PHP-Binary ermitteln ---
PHP_BIN="$(command -v php${PHP_VERSION} 2>/dev/null || command -v php)"

# --- systemd Service (artisan serve, wie Monica) ---
info "Konfiguriere systemd Service (Port ${BS_PORT})..."
cat > /etc/systemd/system/bookstack.service << SVCEOF
[Unit]
Description=BookStack Wiki
After=network.target mariadb.service

[Service]
Type=simple
User=${BS_USER}
Group=${BS_USER}
WorkingDirectory=${BS_DIR}
ExecStart=${PHP_BIN} artisan serve --host=0.0.0.0 --port=${BS_PORT}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

# --- Queue-Worker Service ---
cat > /etc/systemd/system/bookstack-queue.service << SVCEOF
[Unit]
Description=BookStack Queue Worker
After=network.target mariadb.service
Requires=mariadb.service

[Service]
Type=simple
User=${BS_USER}
Group=${BS_USER}
WorkingDirectory=${BS_DIR}
ExecStart=${PHP_BIN} artisan queue:work --queue=default --sleep=3 --tries=3 --timeout=90
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable --now bookstack bookstack-queue
success "Services 'bookstack' + 'bookstack-queue' gestartet"

# --- Warten auf Erreichbarkeit ---
info "Warte auf BookStack (bis 30 s)..."
for i in $(seq 1 15); do
    sleep 2
    if curl -sf "http://127.0.0.1:${BS_PORT}" &>/dev/null; then
        break
    fi
done
if curl -sf "http://127.0.0.1:${BS_PORT}" &>/dev/null; then
    success "BookStack erreichbar"
else
    warn "BookStack noch nicht erreichbar — prüfe: journalctl -u bookstack"
fi

# --- HydraHive Config ---
mkdir -p /etc/hydrahive
cat > "${HH_CONF}" << CFGEOF
{
  "installed": true,
  "url": "http://127.0.0.1:${BS_PORT}",
  "port": ${BS_PORT},
  "server_ip": "${_SERVER_IP}",
  "dir": "${BS_DIR}",
  "php_version": "${PHP_VERSION}",
  "db_name": "${BS_DB}",
  "db_user": "${BS_DB_USER}",
  "db_pass": "${BS_DB_PASS}",
  "service": "bookstack"
}
CFGEOF
chown hydrahive:hydrahive "${HH_CONF}" 2>/dev/null || true
chmod 600 "${HH_CONF}"

echo ""
info "=== BookStack installiert ==="
info "URL:        http://${_SERVER_IP}:${BS_PORT}"
info "Login:      admin@admin.com / password"
info "WICHTIG: Passwort sofort nach dem Login ändern!"
info "Verz.:      ${BS_DIR}"
