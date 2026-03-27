#!/usr/bin/env bash
# HydraHive Installer - Modul 14: SearXNG (nativ, kein Docker)
# Installiert SearXNG als systemd-Service auf Port 8888 (nur localhost).
# Idempotent: erneuter Aufruf aktualisiert Code + Konfiguration.

SEARXNG_DIR="/opt/searxng"
SEARXNG_VENV="${SEARXNG_DIR}/venv"
SEARXNG_USER="searxng"
SEARXNG_CONF_DIR="/etc/searxng"
SEARXNG_CONF="${SEARXNG_CONF_DIR}/settings.yml"
SEARXNG_PORT="8888"

info "=== SearXNG Web-Suche ==="

# --- System-Abhängigkeiten ---
apt-get install -y --quiet git python3-dev python3-venv \
    libxslt1-dev libxml2-dev zlib1g-dev build-essential \
    2>/dev/null | grep -E "^(Get|Entpacken|Einrichten)" || true
success "System-Abhängigkeiten vorhanden"

# --- System-User ---
if ! id "${SEARXNG_USER}" &>/dev/null; then
    useradd -r -s /bin/false -d "${SEARXNG_DIR}" "${SEARXNG_USER}"
    success "System-User '${SEARXNG_USER}' angelegt"
else
    info "System-User '${SEARXNG_USER}' bereits vorhanden"
fi

# --- Konfig-Verzeichnis ---
mkdir -p "${SEARXNG_CONF_DIR}"
chown root:"${SEARXNG_USER}" "${SEARXNG_CONF_DIR}"
chmod 750 "${SEARXNG_CONF_DIR}"

# --- Git clone / pull ---
if [ ! -d "${SEARXNG_DIR}/.git" ]; then
    info "Klone SearXNG..."
    git clone --depth 1 --quiet https://github.com/searxng/searxng "${SEARXNG_DIR}"
    success "SearXNG geklont: ${SEARXNG_DIR}"
else
    info "Aktualisiere SearXNG..."
    git -C "${SEARXNG_DIR}" pull --ff-only --quiet 2>/dev/null || true
    success "SearXNG aktuell"
fi
chown -R "${SEARXNG_USER}:${SEARXNG_USER}" "${SEARXNG_DIR}"

# --- venv anlegen ---
if [ ! -x "${SEARXNG_VENV}/bin/python" ]; then
    info "Lege venv an..."
    python3 -m venv "${SEARXNG_VENV}"
    success "venv angelegt: ${SEARXNG_VENV}"
fi

# --- pip install ---
info "Installiere SearXNG-Abhängigkeiten..."
"${SEARXNG_VENV}/bin/pip" install --quiet --upgrade pip
"${SEARXNG_VENV}/bin/pip" install --quiet -e "${SEARXNG_DIR}"
success "SearXNG-Pakete installiert"

# --- settings.yml (nur beim ersten Mal anlegen, nicht überschreiben) ---
if [ ! -f "${SEARXNG_CONF}" ]; then
    info "Schreibe ${SEARXNG_CONF}..."
    SEARXNG_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    cat > "${SEARXNG_CONF}" << YAMLEOF
# SearXNG Konfiguration — verwaltet von HydraHive Installer
# Nur lokal erreichbar (127.0.0.1:${SEARXNG_PORT}), kein öffentlicher Zugriff.

general:
  debug: false
  instance_name: "HydraHive Search"

server:
  port: ${SEARXNG_PORT}
  bind_address: "127.0.0.1"
  secret_key: "${SEARXNG_SECRET}"
  base_url: false
  image_proxy: false

ui:
  default_theme: simple

search:
  safe_search: 0
  autocomplete: ""
  default_lang: "de-DE"
  formats:
    - html
    - json

outgoing:
  request_timeout: 6.0
  max_request_timeout: 15.0

engines:
  - name: duckduckgo
    engine: duckduckgo
    shortcut: ddg

  - name: wikipedia
    engine: wikipedia
    shortcut: wp
    base_url: "https://de.wikipedia.org/"
    timeout: 6.0

  - name: stackoverflow
    engine: stackoverflow
    shortcut: so

  - name: github
    engine: github
    shortcut: gh

  - name: startpage
    engine: startpage
    shortcut: sp
    timeout: 8.0
YAMLEOF
    chown root:"${SEARXNG_USER}" "${SEARXNG_CONF}"
    chmod 640 "${SEARXNG_CONF}"
    success "${SEARXNG_CONF} geschrieben"
else
    info "${SEARXNG_CONF} bereits vorhanden — wird nicht überschrieben"
fi

# --- systemd-Service ---
cat > /etc/systemd/system/searxng.service << 'UNIT'
[Unit]
Description=SearXNG — Privacy-respecting metasearch engine (HydraHive)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=searxng
Group=searxng
WorkingDirectory=/opt/searxng
Environment=SEARXNG_SETTINGS_PATH=/etc/searxng/settings.yml
ExecStart=/opt/searxng/venv/bin/python -m searx.webapp
Restart=on-failure
RestartSec=5

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/tmp /opt/searxng

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable searxng --quiet

if systemctl is-active --quiet searxng; then
    systemctl restart searxng
    info "SearXNG neugestartet"
else
    systemctl start searxng
fi

# --- Health-Check ---
info "Warte auf SearXNG..."
HEALTH_OK=0
for i in 1 2 3 4 5 6; do
    sleep 3
    HTTP_STATUS=$(curl -so /dev/null -w "%{http_code}" \
        "http://127.0.0.1:${SEARXNG_PORT}/search?q=test&format=json" 2>/dev/null || echo "000")
    if [ "${HTTP_STATUS}" = "200" ]; then
        HEALTH_OK=1
        break
    fi
    info "  Warte... ($i/6) [HTTP ${HTTP_STATUS}]"
done

if [ "${HEALTH_OK}" -eq 1 ]; then
    success "SearXNG läuft: http://127.0.0.1:${SEARXNG_PORT}/"
else
    warn "SearXNG antwortet noch nicht — prüfe mit: journalctl -u searxng -n 30"
fi
