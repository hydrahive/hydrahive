#!/usr/bin/env bash
# HydraHive Installer - Modul 15: code-server (Browser-IDE)
# Installiert code-server, richtet es als Service ein und injiziert nginx /code/ Proxy.
# Idempotent.

CODESERVER_PORT="8766"
CODESERVER_CONFIG_DIR="/opt/hydrahive/.config/code-server"
CODESERVER_SERVICE="hydrahive-codeserver"
HYDRAHIVE_USER="hydrahive"
CRED_FILE="/etc/hydrahive/admin_credentials"
NGINX_CONF="/etc/nginx/sites-available/hydrahive-console"

info "Installiere code-server (Browser-IDE)..."

# --- 1. Passwort lesen oder generieren ---
CS_PASS=$(grep '^codeserver_password=' "${CRED_FILE}" 2>/dev/null | cut -d= -f2- || true)
if [ -z "${CS_PASS}" ]; then
    CS_PASS=$(tr -dc 'A-Za-z0-9!@#%^&*' < /dev/urandom | head -c 24 || true)
    if [ -z "${CS_PASS}" ]; then
        CS_PASS="$(openssl rand -hex 16)"
    fi
fi

# --- 2. Aktuelle Version ermitteln und code-server installieren ---
CS_VERSION=$(curl -fsSL "https://api.github.com/repos/coder/code-server/releases/latest" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'].lstrip('v'))" 2>/dev/null) || true
if [ -z "${CS_VERSION}" ]; then
    CS_VERSION="4.96.4"  # fallback
    warn "GitHub-API nicht erreichbar — verwende Fallback-Version ${CS_VERSION}"
fi

# Prüfen ob bereits korrekte Version installiert ist
NEEDS_INSTALL=1
if [ -x "/opt/codeserver/bin/code-server" ]; then
    INSTALLED_VERSION=$(/opt/codeserver/bin/code-server --version 2>/dev/null | head -1 | awk '{print $1}' || echo "")
    if [ "${INSTALLED_VERSION}" = "${CS_VERSION}" ]; then
        info "code-server ${CS_VERSION} bereits installiert — überspringe Download"
        NEEDS_INSTALL=0
    else
        info "Aktualisiere code-server ${INSTALLED_VERSION:-unbekannt} → ${CS_VERSION}..."
    fi
fi

if [ "${NEEDS_INSTALL}" -eq 1 ]; then
    mkdir -p /opt/codeserver
    CS_URL="https://github.com/coder/code-server/releases/download/v${CS_VERSION}/code-server-${CS_VERSION}-linux-amd64.tar.gz"
    info "Lade code-server ${CS_VERSION} herunter..."
    if curl -fsSL -o /tmp/codeserver.tar.gz "${CS_URL}"; then
        tar -xzf /tmp/codeserver.tar.gz -C /opt/codeserver --strip-components=1
        rm -f /tmp/codeserver.tar.gz
        success "code-server ${CS_VERSION} installiert"
    else
        warn "code-server Download fehlgeschlagen — überspringe Installation"
        return 0
    fi
fi

# --- 3. Verzeichnisse anlegen und Konfiguration schreiben ---
mkdir -p "${CODESERVER_CONFIG_DIR}"
mkdir -p "/opt/hydrahive/.local/share/code-server"
chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${CODESERVER_CONFIG_DIR}"
chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "/opt/hydrahive/.local"

cat > "${CODESERVER_CONFIG_DIR}/config.yaml" << CFG
bind-addr: 127.0.0.1:${CODESERVER_PORT}
auth: password
password: ${CS_PASS}
cert: false
disable-telemetry: true
disable-update-check: true
CFG
chown "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${CODESERVER_CONFIG_DIR}/config.yaml"
chmod 600 "${CODESERVER_CONFIG_DIR}/config.yaml"

success "code-server Konfiguration geschrieben"

# --- 4. systemd-Service anlegen ---
cat > "/etc/systemd/system/${CODESERVER_SERVICE}.service" << SVCEOF
[Unit]
Description=HydraHive Code Editor (code-server)
After=network.target

[Service]
Type=simple
User=${HYDRAHIVE_USER}
ExecStart=/opt/codeserver/bin/code-server --config ${CODESERVER_CONFIG_DIR}/config.yaml
Restart=always
RestartSec=5
Environment=HOME=/opt/hydrahive

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable "${CODESERVER_SERVICE}"
if systemctl is-active --quiet "${CODESERVER_SERVICE}"; then
    systemctl restart "${CODESERVER_SERVICE}"
    success "code-server neu gestartet"
else
    systemctl start "${CODESERVER_SERVICE}"
    success "code-server gestartet"
fi

# --- 5. nginx /code/ Location Block injizieren ---
if ! grep -q "location /code/" "${NGINX_CONF}" 2>/dev/null; then
    python3 << PYEOF
import pathlib
conf_path = pathlib.Path('${NGINX_CONF}')
conf = conf_path.read_text()
if 'location /code/' not in conf:
    block = '''
    # Code Editor (code-server)
    location /code/ {
        proxy_pass         http://127.0.0.1:${CODESERVER_PORT}/;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade           \$http_upgrade;
        proxy_set_header   Connection        "upgrade";
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto https;
        proxy_read_timeout 3600s;
    }
'''
    idx = conf.rfind('}')
    conf = conf[:idx] + block + conf[idx:]
    conf_path.write_text(conf)
    print('Injected /code/ location block')
else:
    print('/code/ location already present')
PYEOF
    success "nginx /code/ Location Block injiziert"
else
    info "nginx /code/ Location Block bereits vorhanden"
fi

# --- 6. nginx testen und neu laden ---
if nginx -t &>/dev/null; then
    if systemctl is-active --quiet nginx; then
        systemctl reload nginx
        success "nginx neu geladen"
    else
        systemctl start nginx
        success "nginx gestartet"
    fi
else
    warn "nginx -t meldet Fehler nach /code/-Injection — bitte manuell prüfen"
    nginx -t
fi

# --- 7. Health-Check code-server ---
HEALTH_OK=0
for i in 1 2 3 4 5; do
    sleep 2
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${CODESERVER_PORT}/healthz" 2>/dev/null || echo "000")
    if [ "${HTTP_CODE}" = "200" ]; then
        success "code-server erreichbar (Port ${CODESERVER_PORT})"
        HEALTH_OK=1
        break
    fi
    info "Warte auf code-server... ($i/5) [HTTP ${HTTP_CODE}]"
done
[ "${HEALTH_OK}" -eq 0 ] && warn "code-server nicht erreichbar — prüfe: journalctl -u ${CODESERVER_SERVICE} -n 20"

# --- 8. Passwort in CRED_FILE speichern ---
if ! grep -q '^codeserver_password=' "${CRED_FILE}" 2>/dev/null; then
    echo "codeserver_password=${CS_PASS}" >> "${CRED_FILE}"
    success "code-server Passwort in ${CRED_FILE} gespeichert"
fi

SERVER_IP="$(hostname -I | awk '{print $1}')" || SERVER_IP="127.0.0.1"
echo ""
info "Code Editor:   https://${SERVER_IP}/code/"
info "Passwort:      ${CS_PASS}  (auch in ${CRED_FILE})"
