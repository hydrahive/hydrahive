#!/usr/bin/env bash
# HydraHive Installer - Modul 13: WhatsApp Bridge (whatsapp-web.js)
# Installiert die Node.js-basierte WhatsApp-Bridge unter ${INSTALL_DIR}/whatsapp-bridge/,
# schreibt einen eigenen Secret-File und startet den Dienst als hydrahive-whatsapp-bridge.
# Idempotent: bereits laufender Service wird nach Update neugestartet.

HYDRAHIVE_DIR="${HYDRAHIVE_DIR:-/opt/hydrahive}"
HYDRAHIVE_USER="${HYDRAHIVE_USER:-hydrahive}"

BRIDGE_SERVICE_NAME="hydrahive-whatsapp-bridge"
BRIDGE_SERVICE_FILE="/etc/systemd/system/${BRIDGE_SERVICE_NAME}.service"
BRIDGE_INSTALL_DIR="${HYDRAHIVE_DIR}/whatsapp-bridge"
BRIDGE_SECRET_FILE="/etc/hydrahive/whatsapp_bridge_secret"
BRIDGE_SESSION_DIR="/etc/hydrahive/whatsapp-sessions"

# Fallback-Funktionen falls Script standalone läuft (nicht via source aus install.sh)
if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi
if ! declare -f error   &>/dev/null; then error()   { echo "[ERROR] $1"; exit 1; }; fi

info "=== WhatsApp Bridge ==="

# --- Node.js ≥18 prüfen / installieren ---
_node_ok=0
if command -v node &>/dev/null; then
    _node_ver="$(node -e 'process.stdout.write(String(process.versions.node.split(".")[0]))' 2>/dev/null || echo 0)"
    if [ "${_node_ver}" -ge 18 ] 2>/dev/null; then
        _node_ok=1
        success "Node.js $(node --version) vorhanden"
    else
        warn "Node.js $(node --version) zu alt (brauche ≥18) — installiere neu via NodeSource"
    fi
fi

if [ "${_node_ok}" -eq 0 ]; then
    info "Installiere Node.js 20.x via NodeSource..."
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y nodejs
    success "Node.js $(node --version) installiert"
fi

# --- Chromium für Puppeteer (whatsapp-web.js) ---
if ! command -v chromium-browser &>/dev/null && ! command -v chromium &>/dev/null; then
    info "Installiere Chromium (Puppeteer-Backend für whatsapp-web.js)..."
    apt-get install -y chromium-browser || apt-get install -y chromium
    success "Chromium installiert"
else
    success "Chromium bereits vorhanden"
fi

# Chromium-Pfad ermitteln
_chromium_bin="$(command -v chromium-browser 2>/dev/null || command -v chromium 2>/dev/null || echo '')"

# --- Bridge-Verzeichnis anlegen ---
mkdir -p "${BRIDGE_INSTALL_DIR}"

# --- Quellcode rsync aus dem Repo ---
info "Kopiere whatsapp-bridge Quellcode..."
# Suche Quellcode: erstens neben dem Installer-Script, zweitens unter /opt/hydrahive/
REPO_BRIDGE="$(dirname "${BASH_SOURCE[0]}")/../../whatsapp-bridge"
REPO_BRIDGE="$(realpath "${REPO_BRIDGE}" 2>/dev/null || echo "${REPO_BRIDGE}")"
if [ ! -f "${REPO_BRIDGE}/bridge.js" ]; then
    REPO_BRIDGE="${HYDRAHIVE_DIR:-/opt/hydrahive}/whatsapp-bridge"
fi

if [ -f "${REPO_BRIDGE}/bridge.js" ]; then
    rsync -a --delete \
        --exclude='node_modules' \
        --exclude='.git' \
        "${REPO_BRIDGE}/" "${BRIDGE_INSTALL_DIR}/"
    success "whatsapp-bridge Quellcode bereit (${BRIDGE_INSTALL_DIR})"
else
    error "whatsapp-bridge/bridge.js nicht gefunden — bitte erst ein Update durchführen"
fi

chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${BRIDGE_INSTALL_DIR}"

# --- Chrome-Cache-Verzeichnis (gemeinsam, nicht im Home-Dir des Service-Users) ---
BRIDGE_CHROME_CACHE="/opt/hydrahive/puppeteer-cache"
mkdir -p "${BRIDGE_CHROME_CACHE}"

# --- npm install --omit=dev + Puppeteer-Chrome herunterladen ---
info "Installiere Node.js-Abhängigkeiten + lade Puppeteer-Chrome herunter..."
PUPPETEER_CACHE_DIR="${BRIDGE_CHROME_CACHE}" \
    npm install --omit=dev --prefix "${BRIDGE_INSTALL_DIR}" \
    || error "npm install fehlgeschlagen — pruefe ${BRIDGE_INSTALL_DIR}/package.json"

# Puppeteer-Chrome explizit herunterladen (falls nicht schon enthalten)
PUPPETEER_CACHE_DIR="${BRIDGE_CHROME_CACHE}" \
    node "${BRIDGE_INSTALL_DIR}/node_modules/puppeteer/install.mjs" 2>/dev/null \
    || PUPPETEER_CACHE_DIR="${BRIDGE_CHROME_CACHE}" \
       node -e "const p=require('${BRIDGE_INSTALL_DIR}/node_modules/puppeteer'); p.launch({headless:true,args:['--no-sandbox']}).then(b=>b.close()).catch(()=>{})" 2>/dev/null \
    || true  # Fehler nicht fatal — System-Chromium als Fallback

# Pfad zum heruntergeladenen Chrome ermitteln
_puppeteer_chrome="$(PUPPETEER_CACHE_DIR="${BRIDGE_CHROME_CACHE}" \
    node -e "try{const p=require('${BRIDGE_INSTALL_DIR}/node_modules/puppeteer');console.log(p.executablePath())}catch(e){}" 2>/dev/null || echo '')"

chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${BRIDGE_INSTALL_DIR}/node_modules"
chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${BRIDGE_CHROME_CACHE}" 2>/dev/null || true
success "Node.js-Abhängigkeiten installiert"

if [ -n "${_puppeteer_chrome}" ] && [ -f "${_puppeteer_chrome}" ]; then
    success "Puppeteer-Chrome verfügbar: ${_puppeteer_chrome}"
    _chromium_bin="${_puppeteer_chrome}"
elif [ -n "${_chromium_bin}" ]; then
    warn "Puppeteer-Chrome nicht gefunden — nutze System-Chromium: ${_chromium_bin}"
else
    warn "Kein Chrome gefunden — WhatsApp-Bridge benötigt manuelle Chromium-Installation"
fi

# --- BRIDGE_SECRET generieren (idempotent) ---
if [ ! -s "${BRIDGE_SECRET_FILE}" ]; then
    openssl rand -hex 32 > "${BRIDGE_SECRET_FILE}"
    success "BRIDGE_SECRET generiert: ${BRIDGE_SECRET_FILE}"
else
    info "BRIDGE_SECRET bereits vorhanden"
fi
chown "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${BRIDGE_SECRET_FILE}"
chmod 600 "${BRIDGE_SECRET_FILE}"

# --- Session-Verzeichnis ---
mkdir -p "${BRIDGE_SESSION_DIR}"
chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${BRIDGE_SESSION_DIR}"
chmod 750 "${BRIDGE_SESSION_DIR}"
success "Session-Verzeichnis: ${BRIDGE_SESSION_DIR}"

# --- Systemd-Unit schreiben ---
_puppeteer_env=""
if [ -n "${_chromium_bin}" ]; then
    _puppeteer_env="Environment=PUPPETEER_EXECUTABLE_PATH=${_chromium_bin}"
fi

_bridge_secret="$(cat "${BRIDGE_SECRET_FILE}" 2>/dev/null || echo '')"
_puppeteer_env=""
if [ -n "${_chromium_bin}" ]; then
    _puppeteer_env="Environment=PUPPETEER_EXECUTABLE_PATH=${_chromium_bin}"
fi

cat > "${BRIDGE_SERVICE_FILE}" << UNIT
[Unit]
Description=HydraHive WhatsApp Bridge (whatsapp-web.js)
After=network.target hydrahive-core.service
Wants=hydrahive-core.service
Documentation=https://github.com/hydrahive/hydrahive

[Service]
Type=simple
User=${HYDRAHIVE_USER}
Group=${HYDRAHIVE_USER}
WorkingDirectory=${BRIDGE_INSTALL_DIR}
ExecStart=/usr/bin/node bridge.js
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${BRIDGE_SERVICE_NAME}
EnvironmentFile=-/etc/hydrahive/llm_env
Environment=BRIDGE_PORT=8767
Environment=BRIDGE_SECRET=${_bridge_secret}
Environment=SESSIONS_DIR=${BRIDGE_SESSION_DIR}
Environment=CORE_URL=http://127.0.0.1:8765
Environment=PUPPETEER_CACHE_DIR=${BRIDGE_CHROME_CACHE}
${_puppeteer_env}

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "${BRIDGE_SERVICE_NAME}"

if systemctl is-active --quiet "${BRIDGE_SERVICE_NAME}"; then
    systemctl restart "${BRIDGE_SERVICE_NAME}"
    success "${BRIDGE_SERVICE_NAME} neugestartet"
else
    systemctl start "${BRIDGE_SERVICE_NAME}"
    success "${BRIDGE_SERVICE_NAME} gestartet"
fi

# --- Health-Check ---
sleep 3
if curl -sf "http://localhost:8767/health" &>/dev/null; then
    success "WhatsApp Bridge antwortet auf http://localhost:8767"
else
    warn "WhatsApp Bridge antwortet noch nicht — pruefe: journalctl -u ${BRIDGE_SERVICE_NAME} -n 30"
fi

info "Bridge läuft — QR-Code-Pairing über die Konsole (Einstellungen → WhatsApp)"
