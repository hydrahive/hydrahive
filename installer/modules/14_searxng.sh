#!/usr/bin/env bash
# HydraHive Installer - Modul 14: SearXNG (nativ, kein Docker)
# Installiert SearXNG als systemd-Service auf Port 8888 (nur localhost).
# Idempotent: erneuter Aufruf aktualisiert Code + Konfiguration.
# Kann standalone ausgeführt werden: sudo bash 14_searxng.sh

set -euo pipefail

# Standalone-kompatible Helper (werden von install.sh ggf. überschrieben)
if ! command -v info &>/dev/null 2>&1 || ! type -t info | grep -q function; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[SearXNG]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

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
chmod 755 "${SEARXNG_CONF_DIR}"

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
"${SEARXNG_VENV}/bin/pip" install --quiet --upgrade pip setuptools wheel
if [ -f "${SEARXNG_DIR}/requirements.txt" ]; then
    "${SEARXNG_VENV}/bin/pip" install --quiet -r "${SEARXNG_DIR}/requirements.txt"
fi
# Kein 'pip install -e .' — SearXNG's setup.py lädt settings.yml beim Build,
# was beim ersten Aufruf noch nicht existiert. Stattdessen: .pth Datei damit
# Python searxng direkt aus /opt/searxng importiert.
PYTHON_VERSION=$("${SEARXNG_VENV}/bin/python" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "${SEARXNG_DIR}" > "${SEARXNG_VENV}/lib/python${PYTHON_VERSION}/site-packages/searxng.pth"
success "SearXNG-Pakete installiert"

# --- settings.yml (nur beim ersten Mal anlegen, nicht überschreiben) ---
if [ ! -f "${SEARXNG_CONF}" ]; then
    info "Schreibe ${SEARXNG_CONF}..."
    SEARXNG_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    # Basis: SearXNG-eigene settings.yml (validiert gegen das Schema),
    # nur secret_key und instance_name anpassen.
    cp "${SEARXNG_DIR}/searx/settings.yml" "${SEARXNG_CONF}"
    # secret_key setzen
    sed -i "s|secret_key: \"ultrasecretkey\".*|secret_key: \"${SEARXNG_SECRET}\"|" "${SEARXNG_CONF}"
    # SEARXNG_SECRET env-override deaktivieren (inline-Kommentar entfernen nicht nötig, nur den Wert setzen)
    # instance_name setzen
    sed -i 's|instance_name: "SearXNG"|instance_name: "HydraHive Search"|' "${SEARXNG_CONF}"
    chown root:"${SEARXNG_USER}" "${SEARXNG_CONF}"
    chmod 644 "${SEARXNG_CONF}"
    success "${SEARXNG_CONF} geschrieben"
fi

# JSON-Format sicherstellen (auch bei bereits vorhandener settings.yml)
# sed ist hier nicht portabel (unterschiedliche Kommentar-Formate je SearXNG-Version)
python3 - "${SEARXNG_CONF}" <<'PYEOF'
import sys, re
path = sys.argv[1]
try:
    content = open(path).read()
except Exception:
    sys.exit(0)
# Normalisiere den formats-Block auf html + json, unabhängig vom Ausgangsformat
if re.search(r'^  formats:', content, re.MULTILINE):
    # formats-Block vorhanden — sicherstellen dass json drin ist
    if '- json' not in content:
        content = re.sub(
            r'^(  formats:\n(?:    - \w+\n)*)',
            lambda m: m.group(0) + '    - json\n',
            content, flags=re.MULTILINE,
        )
        # Edge-case: leerer formats-Block (kein einziger Eintrag)
        content = re.sub(
            r'^  formats:\s*\n(?!    - )',
            '  formats:\n    - html\n    - json\n',
            content, flags=re.MULTILINE,
        )
elif re.search(r'^  # formats:', content, re.MULTILINE):
    # Kommentierter formats-Block — durch echten ersetzen
    content = re.sub(
        r'^  # formats:.*\n',
        '  formats:\n    - html\n    - json\n',
        content, flags=re.MULTILINE,
    )
else:
    # Kein formats-Block — direkt nach search: einfügen
    content = re.sub(
        r'^(search:\n)',
        r'\g<1>  formats:\n    - html\n    - json\n',
        content, flags=re.MULTILINE,
    )
open(path, 'w').write(content)
PYEOF
info "JSON-Format in ${SEARXNG_CONF} sichergestellt"

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

# Fallback-Funktionen falls Script standalone laeuft (nicht via source aus install.sh)
if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi
if ! declare -f error   &>/dev/null; then error()   { echo "[ERROR] $1"; exit 1; }; fi

info "Warte auf SearXNG..."
HEALTH_OK=0
for i in 1 2 3 4 5 6; do
    sleep 3
    HTTP_STATUS=$(curl -so /dev/null -w "%{http_code}" \
        "http://127.0.0.1:${SEARXNG_PORT}/" 2>/dev/null || echo "000")
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
