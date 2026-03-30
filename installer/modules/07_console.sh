#!/usr/bin/env bash
# HydraHive Installer - Modul 07: hydrahive-console (React + nginx)
#
# - Baut die Console aus dem Repo-Verzeichnis (npm ci + npm run build)
# - Kopiert dist/ nach /opt/hydrahive/console/
# - Schreibt nginx-Konfiguration (Port 80, Proxy /api/ → Core :8765)
# - Idempotent: erneuter Aufruf aktualisiert Build + Konfiguration

CONSOLE_SRC="$(realpath "$(dirname "${BASH_SOURCE[0]}")/../../console")"
CONSOLE_DIST="/opt/hydrahive/console"
NGINX_CONF="/etc/nginx/sites-available/hydrahive-console"
NGINX_ENABLED="/etc/nginx/sites-enabled/hydrahive-console"
NGINX_DEFAULT="/etc/nginx/sites-enabled/default"

info "Installiere hydrahive-console..."

# --- Port 80 pruefen ---
if ss -tlnp 2>/dev/null | grep -q ':80 '; then
    PORT80_PROC=$(ss -tlnp | grep ':80 ' | grep -oP 'users:\(\("([^"]+)"' | head -1 | grep -oP '"([^"]+)"' | tr -d '"' || echo "unbekannt")
    if [ "${PORT80_PROC}" = "apache2" ] || systemctl is-active --quiet apache2 2>/dev/null; then
        warn "Apache2 laeuft auf Port 80 — deaktiviere Apache2"
        systemctl stop apache2 &>/dev/null || true
        systemctl disable apache2 &>/dev/null || true
        success "Apache2 deaktiviert"
    elif [ "${PORT80_PROC}" != "nginx" ] && [ "${PORT80_PROC}" != "unbekannt" ]; then
        warn "Port 80 belegt von: ${PORT80_PROC} — nginx koennte nicht starten"
    fi
fi

# --- Node.js pruefen ---
if ! command -v node &>/dev/null; then
    info "Installiere Node.js 22..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - &>/dev/null
    apt-get install -y nodejs &>/dev/null
    success "Node.js $(node --version) installiert"
else
    info "Node.js $(node --version) bereits vorhanden"
fi

# --- nginx pruefen ---
if ! command -v nginx &>/dev/null; then
    info "Installiere nginx..."
    apt-get install -y nginx &>/dev/null
    success "nginx installiert"
else
    info "nginx $(nginx -v 2>&1 | cut -d/ -f2) bereits vorhanden"
fi

# --- Console bauen ---
if [ ! -d "${CONSOLE_SRC}" ]; then
    error "console/ nicht gefunden (${CONSOLE_SRC}) — Installer muss aus dem geklonten Repo ausgefuehrt werden"
fi

info "Installiere npm-Abhaengigkeiten..."
pushd "${CONSOLE_SRC}" > /dev/null || error "cd ${CONSOLE_SRC} fehlgeschlagen"
npm install --silent 2>&1 | grep -v "^npm warn" || true
success "npm-Abhaengigkeiten installiert"

info "Baue Console (npm run build)..."
npm run build --silent
popd > /dev/null
success "Console gebaut: ${CONSOLE_SRC}/dist/"

# --- Build nach /opt/hydrahive/console/ kopieren ---
mkdir -p "${CONSOLE_DIST}"
rm -rf "${CONSOLE_DIST:?}"/*
cp -r "${CONSOLE_SRC}/dist/." "${CONSOLE_DIST}/"
chown -R www-data:www-data "${CONSOLE_DIST}"
success "Console-Dateien nach ${CONSOLE_DIST} kopiert"

# --- nginx-Konfiguration ---
cat > "${NGINX_CONF}" << 'NGINXCONF'
server {
    listen 80;
    server_name _;

    root /opt/hydrahive/console;
    index index.html;

    # 502/503 nur für Browser-Navigationen → auto-refreshende Wartungsseite
    error_page 502 503 /502.html;
    location = /502.html {
        internal;
    }

    # SPA-Fallback: alle nicht gefundenen Routen an index.html
    location / {
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-store, no-cache";
    }

    # API-Proxy → HydraHive Core
    location /api/ {
        proxy_pass         http://127.0.0.1:8765/;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   Connection        "";
        proxy_read_timeout    300s;
        proxy_connect_timeout 5s;
        proxy_next_upstream   error timeout;
        proxy_intercept_errors off;
    }

    # A2A Federation: Agent Card + Task-Eingang direkt proxyen (kein /api-Prefix)
    location /.well-known/ {
        proxy_pass         http://127.0.0.1:8765/.well-known/;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   Connection        "";
        proxy_read_timeout    60s;
    }

    location /a2a/ {
        proxy_pass         http://127.0.0.1:8765/a2a/;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   Connection        "";
        proxy_read_timeout    300s;
        proxy_connect_timeout 5s;
    }

    # Statische Assets: lange Cache-Laufzeit
    location /assets/ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Projekt-Dateien: Agenten-Outputs per HTTP erreichbar
    location /projects/ {
        alias /projects/;
        autoindex on;
        autoindex_exact_size off;
        add_header Cache-Control "no-store";
    }
}
NGINXCONF

# Default-Site deaktivieren wenn vorhanden
[ -L "${NGINX_DEFAULT}" ] && rm -f "${NGINX_DEFAULT}"

# Site aktivieren
ln -sf "${NGINX_CONF}" "${NGINX_ENABLED}"

# www-data (nginx) braucht Lesezugriff auf /projects/ (hydrahive-Gruppe)
if id www-data &>/dev/null; then
    usermod -aG hydrahive www-data
fi

success "nginx-Konfiguration geschrieben"

# Konfiguration testen
if ! nginx -t &>/dev/null; then
    warn "nginx -t meldet Fehler:"
    nginx -t
fi

# nginx neu laden oder starten
systemctl enable nginx &>/dev/null
if systemctl is-active --quiet nginx; then
    systemctl reload nginx
    success "nginx neu geladen"
else
    systemctl start nginx
    success "nginx gestartet"
fi

# Health-Check
HEALTH_OK=0
for i in 1 2 3; do
    sleep 2
    HTTP_STATUS=$(curl -so /dev/null -w "%{http_code}" http://127.0.0.1/ 2>/dev/null || echo "000")
    if [ "${HTTP_STATUS}" = "200" ]; then
        success "Console erreichbar: http://127.0.0.1/"
        HEALTH_OK=1
        break
    fi
    info "Warte auf nginx... ($i/3) [HTTP ${HTTP_STATUS}]"
done
if [ "${HEALTH_OK}" -eq 0 ]; then
    warn "Console nicht erreichbar — pruefe: nginx -t && journalctl -u nginx -n 20"
fi
