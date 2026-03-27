#!/usr/bin/env bash
# HydraHive Installer - Modul 09: HTTPS / TLS (#66)
#
# - Erzeugt self-signed Zertifikat (2048 RSA, 3650 Tage) unter /etc/hydrahive/tls/
# - Idempotent: bestehendes Zertifikat wird nicht überschrieben
# - Aktualisiert nginx-Konfiguration: Port 443 (TLS) + HTTP→HTTPS Redirect
# - Optionaler Let's Encrypt Hinweis wenn DOMAIN gesetzt ist

CERT_DIR="/etc/hydrahive/tls"
CERT_FILE="${CERT_DIR}/hydrahive.crt"
KEY_FILE="${CERT_DIR}/hydrahive.key"
NGINX_CONF="/etc/nginx/sites-available/hydrahive-console"
SERVER_IP="$(hostname -I | awk '{print $1}')"
SERVER_HOST="${DOMAIN:-${SERVER_IP}}"

info "Konfiguriere HTTPS (TLS)..."

# --- openssl pruefen ---
if ! command -v openssl &>/dev/null; then
    info "Installiere openssl..."
    apt-get install -y openssl &>/dev/null
    success "openssl installiert"
fi

# --- Zertifikat-Verzeichnis ---
mkdir -p "${CERT_DIR}"
chmod 700 "${CERT_DIR}"

# --- Self-signed Zertifikat erzeugen (idempotent) ---
if [ -f "${CERT_FILE}" ] && [ -f "${KEY_FILE}" ]; then
    EXPIRY=$(openssl x509 -enddate -noout -in "${CERT_FILE}" 2>/dev/null | cut -d= -f2 || echo "unbekannt")
    info "Zertifikat bereits vorhanden (gültig bis: ${EXPIRY}) — wird nicht überschrieben"
else
    info "Erzeuge self-signed Zertifikat für ${SERVER_HOST}..."
    openssl req -x509 -newkey rsa:2048 -sha256 -days 3650 -nodes \
        -keyout "${KEY_FILE}" \
        -out    "${CERT_FILE}" \
        -subj   "/CN=${SERVER_HOST}/O=HydraHive/C=DE" \
        -addext "subjectAltName=IP:${SERVER_IP},DNS:${SERVER_HOST}" \
        2>/dev/null
    chmod 600 "${KEY_FILE}"
    chmod 644 "${CERT_FILE}"
    success "Zertifikat erzeugt: ${CERT_FILE}"
fi

# --- nginx-Konfiguration auf HTTPS umstellen ---
cat > "${NGINX_CONF}" << NGINXCONF
# HTTP → HTTPS Redirect
server {
    listen 80;
    server_name _;
    return 301 https://\$host\$request_uri;
}

# HTTPS
server {
    listen 443 ssl;
    server_name _;

    ssl_certificate     ${CERT_FILE};
    ssl_certificate_key ${KEY_FILE};
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 10m;

    root /opt/hydrahive/console;
    index index.html;

    # 502/503 nur für Browser-Navigationen → auto-refreshende Wartungsseite
    error_page 502 503 /502.html;
    location = /502.html {
        internal;
    }

    # SPA-Fallback
    location / {
        try_files \$uri \$uri/ /index.html;
        add_header Cache-Control "no-store, no-cache";
    }

    # API-Proxy → HydraHive Core
    location /api/ {
        proxy_pass         http://127.0.0.1:8765/;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto https;
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
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   Connection        "";
        proxy_read_timeout    60s;
    }

    location /a2a/ {
        proxy_pass         http://127.0.0.1:8765/a2a/;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   Connection        "";
        proxy_read_timeout    300s;
        proxy_connect_timeout 5s;
    }

    # Statische Assets
    location /assets/ {
        expires 30d;
        add_header Cache-Control "public, immutable";
    }
}
NGINXCONF

success "nginx-Konfiguration auf HTTPS aktualisiert"

# --- nginx testen und neu laden ---
if ! nginx -t &>/dev/null; then
    warn "nginx -t meldet Fehler:"
    nginx -t
else
    if systemctl is-active --quiet nginx; then
        systemctl reload nginx
        success "nginx neu geladen"
    else
        systemctl start nginx
        success "nginx gestartet"
    fi
fi

# --- Health-Check ---
HEALTH_OK=0
for i in 1 2 3; do
    sleep 2
    HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" "https://${SERVER_IP}/" 2>/dev/null || echo "000")
    if [ "${HTTP_CODE}" = "200" ]; then
        success "Console erreichbar: https://${SERVER_IP}/"
        HEALTH_OK=1
        break
    fi
    info "Warte auf nginx TLS... ($i/3) [HTTP ${HTTP_CODE}]"
done
[ "${HEALTH_OK}" -eq 0 ] && warn "Console nicht erreichbar — pruefe: nginx -t && journalctl -u nginx -n 20"

# --- Let's Encrypt Hinweis ---
if [ -n "${DOMAIN:-}" ]; then
    echo ""
    info "Tipp: Für ein echtes Zertifikat via Let's Encrypt:"
    info "  apt-get install -y certbot python3-certbot-nginx"
    info "  certbot --nginx -d ${DOMAIN}"
fi
