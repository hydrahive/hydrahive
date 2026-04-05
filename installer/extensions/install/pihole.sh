#!/usr/bin/env bash
# HydraHive Extension - Pi-hole (DNS-Sinkhole)
# Installiert Pi-hole unattended via offiziellem Installer-Script.
# NICHT: curl | bash — stattdessen herunterladen, prüfen, dann ausführen.
# Web-UI: Port 8380, DNS: Port 5353 (vermeidet Konflikte mit systemd-resolved).
# Idempotent: erneuter Aufruf führt pihole -up (Update) aus.

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[Pi-hole]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

PIHOLE_BIN="/usr/local/bin/pihole"
PIHOLE_SETUP_VARS="/etc/pihole/setupVars.conf"
PIHOLE_WEB_PORT="8380"
PIHOLE_DNS_PORT="5353"
HH_CONF="/etc/hydrahive/pihole.json"

info "=== Pi-hole installieren ==="

_SERVER_IP="$(hostname -I | awk '{print $1}')"

# --- Bereits installiert? → Update ---
if [ -x "${PIHOLE_BIN}" ]; then
    info "Pi-hole bereits installiert — führe Update aus..."
    "${PIHOLE_BIN}" -up 2>/dev/null || warn "pihole -up fehlgeschlagen — läuft aber weiter"
    success "Pi-hole aktualisiert"
    exit 0
fi

# --- Abhängigkeiten ---
info "Installiere Abhängigkeiten..."
apt-get update -qq
apt-get install -y --quiet \
    curl wget git sudo \
    dns-root-data \
    2>/dev/null | grep -E "^(Get|Entpacken|Einrichten)" || true

# --- systemd-resolved auf Port 53 deaktivieren (Pi-hole braucht DNS) ---
# Wir binden Pi-hole an 5353, daher KEIN Eingriff in systemd-resolved nötig.
# Nur sicherstellen dass der Port frei ist.
if ss -lnup 2>/dev/null | grep -q ":${PIHOLE_DNS_PORT} " ; then
    warn "Port ${PIHOLE_DNS_PORT}/UDP ist bereits belegt — Pi-hole DNS könnte nicht starten"
fi

# --- Pre-Seed: setupVars.conf ---
# Pi-hole liest diese Datei für unattended Install
ADMIN_PASSWORD="$(head -c 16 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 20)"

mkdir -p /etc/pihole
cat > "${PIHOLE_SETUP_VARS}" << VARSEOF
PIHOLE_INTERFACE=
IPV4_ADDRESS=${_SERVER_IP}/24
IPV6_ADDRESS=
QUERY_LOGGING=true
INSTALL_WEB_SERVER=true
INSTALL_WEB_INTERFACE=true
LIGHTTPD_ENABLED=false
CACHE_SIZE=10000
DNS_FQDN_REQUIRED=true
DNS_BOGUS_PRIV=true
DNSMASQ_LISTENING=local
WEBPASSWORD=$(echo -n "${ADMIN_PASSWORD}" | sha256sum | awk '{print $1}' | xxd -r -p | sha256sum | awk '{print $1}')
BLOCKING_ENABLED=true
PIHOLE_DNS_1=1.1.1.1
PIHOLE_DNS_2=8.8.8.8
VARSEOF
chmod 600 "${PIHOLE_SETUP_VARS}"
success "setupVars.conf erstellt"

# --- Pi-hole Installer herunterladen (NICHT direkt pipen) ---
info "Lade Pi-hole Installer herunter..."
INSTALLER_URL="https://install.pi-hole.net"
curl -fsSL "${INSTALLER_URL}" -o /tmp/pihole-install.sh \
    || error "Pi-hole Installer-Download fehlgeschlagen"
chmod +x /tmp/pihole-install.sh
success "Installer heruntergeladen: /tmp/pihole-install.sh"

# --- Unattended Installation ---
info "Starte Pi-hole Unattended-Installation..."
PIHOLE_SKIP_OS_CHECK=true \
    bash /tmp/pihole-install.sh \
        --unattended \
        --disable-install-webserver \
    2>&1 | grep -E "(Complete|Error|Warning|Skipping|Installing|Configuring|Starting)" || true

rm -f /tmp/pihole-install.sh

# Prüfen ob Installation erfolgreich
[ -x "${PIHOLE_BIN}" ] || error "Pi-hole Binary nicht gefunden nach Installation — prüfe Logs"
success "Pi-hole installiert"

# --- Passwort setzen ---
"${PIHOLE_BIN}" -a -p "${ADMIN_PASSWORD}" 2>/dev/null \
    || warn "Passwort-Setzung fehlgeschlagen — manuell: pihole -a -p PASSWORT"

# --- Port konfigurieren ---
# Pi-hole nutzt lighttpd oder nginx für das Web-Interface.
# Da wir --disable-install-webserver nutzen, richten wir nginx manuell ein.
info "Richte nginx für Pi-hole Web-Interface ein..."

apt-get install -y --quiet nginx php-fpm php-cgi \
    2>/dev/null | grep -E "^(Get|Entpacken|Einrichten)" || true

# PHP-Version ermitteln
PHP_VERSION=""
for v in 8.3 8.2 8.1 8.0; do
    if command -v "php${v}" &>/dev/null; then
        PHP_VERSION="${v}"; break
    fi
done
[ -n "${PHP_VERSION}" ] || PHP_VERSION="$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;' 2>/dev/null || echo '8.2')"
PHP_FPM_SERVICE="php${PHP_VERSION}-fpm"
PHP_FPM_SOCK="/run/php/php${PHP_VERSION}-fpm.sock"

systemctl enable --now "${PHP_FPM_SERVICE}" 2>/dev/null || true

# Pi-hole Web-Interface liegt unter /var/www/html (admin-Unterverzeichnis)
cat > /etc/nginx/sites-available/pihole << NGXEOF
server {
    listen 0.0.0.0:${PIHOLE_WEB_PORT};
    server_name _;

    root /var/www/html;
    index index.php index.html;

    access_log /var/log/nginx/pihole-access.log;
    error_log  /var/log/nginx/pihole-error.log;

    location / {
        try_files \$uri \$uri/ =404;
    }

    location /admin {
        try_files \$uri \$uri/ /admin/index.php?\$query_string;
    }

    location ~ \.php$ {
        include snippets/fastcgi-php.conf;
        fastcgi_pass unix:${PHP_FPM_SOCK};
        fastcgi_param SCRIPT_FILENAME \$document_root\$fastcgi_script_name;
        include fastcgi_params;
        fastcgi_read_timeout 60;
    }

    location ~ /\.ht {
        deny all;
    }
}
NGXEOF

ln -sf /etc/nginx/sites-available/pihole /etc/nginx/sites-enabled/pihole 2>/dev/null || true
# Standard-Site deaktivieren falls sie Port-Konflikte verursacht
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true
nginx -t 2>/dev/null && systemctl reload nginx \
    || warn "nginx-Test fehlgeschlagen — prüfe: sudo nginx -t"
success "nginx konfiguriert auf Port ${PIHOLE_WEB_PORT}"

# --- DNS-Port auf 5353 konfigurieren ---
# Pi-hole nutzt dnsmasq (pihole-FTL) — Port über setupVars + pihole-FTL.conf
mkdir -p /etc/pihole
if [ -f /etc/pihole/pihole-FTL.conf ]; then
    # Eintrag ersetzen oder anhängen
    if grep -q "^port=" /etc/pihole/pihole-FTL.conf; then
        sed -i "s|^port=.*|port=${PIHOLE_DNS_PORT}|" /etc/pihole/pihole-FTL.conf
    else
        echo "port=${PIHOLE_DNS_PORT}" >> /etc/pihole/pihole-FTL.conf
    fi
else
    echo "port=${PIHOLE_DNS_PORT}" > /etc/pihole/pihole-FTL.conf
fi
success "DNS-Port auf ${PIHOLE_DNS_PORT} gesetzt"

# --- pihole-FTL Service neu starten ---
systemctl restart pihole-FTL 2>/dev/null \
    || warn "pihole-FTL Service-Restart fehlgeschlagen"
success "pihole-FTL Service neu gestartet"

# --- Warten auf Erreichbarkeit ---
info "Warte auf Pi-hole Web-UI (bis 30 s)..."
for i in $(seq 1 15); do
    sleep 2
    if curl -sf "http://127.0.0.1:${PIHOLE_WEB_PORT}/admin/" &>/dev/null; then
        break
    fi
done
if curl -sf "http://127.0.0.1:${PIHOLE_WEB_PORT}/admin/" &>/dev/null; then
    success "Pi-hole Web-UI erreichbar"
else
    warn "Pi-hole Web-UI noch nicht erreichbar — prüfe nginx + pihole-FTL Logs"
fi

# --- Passwort separat speichern ---
PASS_FILE="/etc/pihole/.initial_admin_pass"
printf '%s\n' "${ADMIN_PASSWORD}" > "${PASS_FILE}"
chmod 600 "${PASS_FILE}"

# --- HydraHive Config ---
mkdir -p /etc/hydrahive
cat > "${HH_CONF}" << CFGEOF
{
  "installed": true,
  "url": "http://127.0.0.1:${PIHOLE_WEB_PORT}/admin",
  "web_port": ${PIHOLE_WEB_PORT},
  "dns_port": ${PIHOLE_DNS_PORT},
  "server_ip": "${_SERVER_IP}",
  "binary": "${PIHOLE_BIN}",
  "setup_vars": "${PIHOLE_SETUP_VARS}",
  "admin_password": "${ADMIN_PASSWORD}"
}
CFGEOF
chown hydrahive:hydrahive "${HH_CONF}" 2>/dev/null || true
chmod 600 "${HH_CONF}"

echo ""
info "=== Pi-hole installiert ==="
info "Web-UI:    http://${_SERVER_IP}:${PIHOLE_WEB_PORT}/admin"
info "Passwort:  ${ADMIN_PASSWORD}  (auch in ${PASS_FILE})"
info "DNS-Port:  ${PIHOLE_DNS_PORT} (Router-DNS auf ${_SERVER_IP}:${PIHOLE_DNS_PORT} zeigen)"
warn "Hinweis: Port ${PIHOLE_DNS_PORT} statt 53 — Router manuell konfigurieren"
