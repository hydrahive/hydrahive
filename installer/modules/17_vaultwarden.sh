#!/usr/bin/env bash
# HydraHive Installer - Modul 17: Vaultwarden (Passwort-Manager)
# Installiert Vaultwarden aus Source (Rust/Cargo) als systemd-Service hinter nginx /vault/.
# Idempotent. Bauzeit ~5-10 Minuten beim ersten Mal.
set -euo pipefail

VW_PORT="8768"
VW_DATA_DIR="/var/lib/vaultwarden"
VW_BIN="/opt/vaultwarden/vaultwarden"
VW_SERVICE="vaultwarden"
VW_USER="vaultwarden"
VW_SRC="/opt/vaultwarden/src"
NGINX_CONF="/etc/nginx/sites-available/hydrahive-console"
CRED_FILE="/etc/hydrahive/admin_credentials"

# Fallback-Funktionen falls Script standalone läuft
if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi
if ! declare -f error   &>/dev/null; then error()   { echo "[ERROR] $1"; exit 1; }; fi

info "Installiere Vaultwarden..."

# --- 1. Admin-Token generieren ---
VW_ADMIN_TOKEN=$(grep '^vaultwarden_admin_token=' "${CRED_FILE}" 2>/dev/null | cut -d= -f2- || true)
if [ -z "${VW_ADMIN_TOKEN}" ]; then
    VW_ADMIN_TOKEN=$(openssl rand -hex 32)
    echo "vaultwarden_admin_token=${VW_ADMIN_TOKEN}" >> "${CRED_FILE}"
fi

# --- 2. User anlegen ---
if ! id "${VW_USER}" &>/dev/null; then
    useradd -r -s /usr/sbin/nologin -d "${VW_DATA_DIR}" "${VW_USER}"
fi

# --- 3. Binary bereits vorhanden? ---
if [ -x "${VW_BIN}" ]; then
    info "Vaultwarden Binary bereits vorhanden — überspringe Build"
else
    # --- 3a. Build-Abhängigkeiten ---
    info "Installiere Build-Abhängigkeiten..."
    dpkg --configure -a 2>/dev/null || true
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
        build-essential pkg-config libssl-dev libsqlite3-dev curl git

    # --- 3b. Rust/Cargo installieren (falls nicht vorhanden) ---
    if ! command -v cargo &>/dev/null; then
        info "Installiere Rust-Toolchain..."
        curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal 2>&1
        source "$HOME/.cargo/env"
    fi
    export PATH="$HOME/.cargo/bin:$PATH"

    # --- 3c. Vaultwarden-Version ermitteln ---
    VW_VERSION=$(curl -fsSL "https://api.github.com/repos/dani-garcia/vaultwarden/releases/latest" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null) || true
    if [ -z "${VW_VERSION}" ]; then
        VW_VERSION="1.35.0"
        warn "GitHub-API nicht erreichbar — verwende Fallback-Version ${VW_VERSION}"
    fi
    info "Baue Vaultwarden ${VW_VERSION} aus Source (das dauert ~5-10 Minuten)..."

    # --- 3d. Source holen und bauen ---
    mkdir -p /opt/vaultwarden
    if [ -d "${VW_SRC}/.git" ]; then
        git -C "${VW_SRC}" fetch --tags -q
        git -C "${VW_SRC}" checkout "${VW_VERSION}" -q
    else
        git clone --branch "${VW_VERSION}" --depth 1 \
            https://github.com/dani-garcia/vaultwarden.git "${VW_SRC}"
    fi

    cd "${VW_SRC}"
    cargo build --features sqlite --release 2>&1
    cp target/release/vaultwarden "${VW_BIN}"
    chmod +x "${VW_BIN}"
    success "Vaultwarden ${VW_VERSION} gebaut und installiert"
fi

# --- 4. Web-Vault Assets herunterladen ---
WV_DIR="/opt/vaultwarden/web-vault"
if [ ! -d "${WV_DIR}" ]; then
    info "Lade Web-Vault Assets herunter..."
    # bw_web_builds liefert fertige Web-Assets ohne Build
    WV_VERSION=$(curl -fsSL "https://api.github.com/repos/dani-garcia/bw_web_builds/releases/latest" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null) || true
    WV_VERSION="${WV_VERSION:-v2024.6.2}"
    WV_URL="https://github.com/dani-garcia/bw_web_builds/releases/download/${WV_VERSION}/bw_web_${WV_VERSION}.tar.gz"
    if curl -fsSL -o /tmp/web-vault.tar.gz "${WV_URL}"; then
        tar -xzf /tmp/web-vault.tar.gz -C /opt/vaultwarden
        rm -f /tmp/web-vault.tar.gz
        success "Web-Vault Assets installiert"
    else
        warn "Web-Vault Download fehlgeschlagen — Vaultwarden läuft ohne Web-UI"
    fi
fi

# --- 5. Datenverzeichnis ---
mkdir -p "${VW_DATA_DIR}/attachments"
chown -R "${VW_USER}:${VW_USER}" "${VW_DATA_DIR}"
chown -R "${VW_USER}:${VW_USER}" /opt/vaultwarden

# --- 6. Environment-Config ---
cat > /etc/hydrahive/vaultwarden.env << ENVEOF
ROCKET_ADDRESS=127.0.0.1
ROCKET_PORT=${VW_PORT}
DATA_FOLDER=${VW_DATA_DIR}
WEB_VAULT_FOLDER=${WV_DIR}
WEB_VAULT_ENABLED=true
ADMIN_TOKEN=${VW_ADMIN_TOKEN}
DOMAIN=$([ -f /etc/hydrahive/tls/hydrahive.crt ] && echo "https" || echo "http")://$(hostname -I | awk '{print $1}')/vault
SIGNUPS_ALLOWED=false
INVITATIONS_ALLOWED=true
LOG_LEVEL=warn
ENVEOF
chmod 600 /etc/hydrahive/vaultwarden.env

# --- 7. systemd-Service ---
cat > /etc/systemd/system/vaultwarden.service << SVCEOF
[Unit]
Description=HydraHive Vaultwarden (Password Manager)
After=network.target

[Service]
User=${VW_USER}
Group=${VW_USER}
EnvironmentFile=/etc/hydrahive/vaultwarden.env
ExecStart=${VW_BIN}
WorkingDirectory=${VW_DATA_DIR}
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable "${VW_SERVICE}"
systemctl restart "${VW_SERVICE}"

# --- 8. nginx /vault/ Proxy ---
if [ -f "${NGINX_CONF}" ] && ! grep -q "location /vault/ {" "${NGINX_CONF}"; then
    info "Füge nginx /vault/ Proxy ein..."
    python3 - "${NGINX_CONF}" "${VW_PORT}" << 'PYEOF'
import sys
conf_path, port = sys.argv[1], sys.argv[2]
content = open(conf_path).read()
vault_block = f"""
    # Vaultwarden
    location /vault/ {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }}
    location /vault/notifications/hub {{
        proxy_pass http://127.0.0.1:{port}/vault/notifications/hub;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }}
"""
# Vor letzter schließender } einfügen
content = content.rstrip().rstrip('}').rstrip() + '\n' + vault_block + '\n}\n'
open(conf_path, 'w').write(content)
PYEOF
    nginx -t &>/dev/null && systemctl reload nginx 2>/dev/null || warn "nginx reload fehlgeschlagen — manuell prüfen"
fi

success "Vaultwarden installiert — Admin-Token in ${CRED_FILE}"
info "Admin-Panel: /vault/admin  (Token in ${CRED_FILE})"
