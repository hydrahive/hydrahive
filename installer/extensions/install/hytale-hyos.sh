#!/usr/bin/env bash
# HydraHive Extension — Hytale Dedicated Server + HyOS Web-Dashboard
# Native Installation (kein Docker):
#   1. Java 25 (Adoptium Temurin)
#   2. Hytale Downloader + Server
#   3. HyOS Dashboard (Node.js + Next.js)
#   4. systemd Services für beide
#
# Ports: 5520/udp (Hytale Game), 3100/tcp (HyOS Dashboard)
# Idempotent: erneuter Aufruf aktualisiert auf neueste Version.

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[Hytale]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

HYTALE_DIR="/opt/hytale"
HYTALE_USER="hytale"
HYTALE_PORT="5520"
HYOS_PORT="3100"
HYOS_DIR="/opt/hyos"

info "=== Hytale Server + HyOS Dashboard installieren ==="

# --- System-User ---
if ! id "${HYTALE_USER}" &>/dev/null; then
    useradd -r -m -d "${HYTALE_DIR}" -s /usr/sbin/nologin "${HYTALE_USER}"
    success "User ${HYTALE_USER} erstellt"
fi
mkdir -p "${HYTALE_DIR}" "${HYOS_DIR}"

# --- Java 25 (Adoptium Temurin) ---
if ! java -version 2>&1 | grep -q "25\|21"; then
    info "Installiere Java (Adoptium Temurin)..."
    mkdir -p /etc/apt/keyrings
    # GPG Key immer frisch holen (dearmor → .gpg binär)
    curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public \
        -o /tmp/adoptium-key.asc \
        || error "Adoptium GPG-Key Download fehlgeschlagen"
    gpg --batch --yes --dearmor -o /etc/apt/keyrings/adoptium.gpg /tmp/adoptium-key.asc 2>/dev/null \
        || error "Adoptium GPG-Key Dearmor fehlgeschlagen"
    rm -f /tmp/adoptium-key.asc
    chmod 644 /etc/apt/keyrings/adoptium.gpg
    echo "deb [signed-by=/etc/apt/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb $(lsb_release -cs) main" \
        > /etc/apt/sources.list.d/adoptium.list
    apt-get update -qq 2>&1 | grep -v "^W:" || true
    apt-get install -y -qq temurin-25-jre 2>/dev/null || {
        warn "temurin-25-jre nicht verfügbar, versuche temurin-21-jre..."
        apt-get install -y -qq temurin-21-jre || error "Java Installation fehlgeschlagen"
    }
    success "Java installiert: $(java -version 2>&1 | head -1)"
else
    success "Java 25 bereits vorhanden"
fi

# --- Hytale Server Download ---
info "Lade Hytale Server herunter..."
cd "${HYTALE_DIR}"

# Hytale Downloader
ARCH="amd64"
[ "$(uname -m)" = "aarch64" ] && ARCH="arm64"
DOWNLOADER_URL="https://downloader.hytale.com/hytale-downloader.zip"

if [ ! -f "${HYTALE_DIR}/hytale-downloader-linux-${ARCH}" ]; then
    curl -fsSL "${DOWNLOADER_URL}" -o /tmp/hytale-downloader.zip
    unzip -o /tmp/hytale-downloader.zip -d "${HYTALE_DIR}" 2>/dev/null || true
    rm -f /tmp/hytale-downloader.zip
    chmod +x "${HYTALE_DIR}/hytale-downloader-linux-${ARCH}" 2>/dev/null || true
    success "Hytale Downloader installiert"
fi

# Server-Dateien: Hytale Downloader nutzt OAuth Device-Flow.
# Er gibt einen Link + Code aus → User klickt den Link im Browser,
# authentifiziert sich → Downloader fährt automatisch fort.
# Kein User-Input im Terminal nötig!
if [ ! -f "${HYTALE_DIR}/Server/HytaleServer.jar" ]; then
    info "Starte Hytale Server-Download (OAuth-Authentifizierung nötig)..."
    info ""
    info "═══════════════════════════════════════════════════════════════"
    info "  WICHTIG: Gleich erscheint ein Link + Code."
    info "  Öffne den Link im Browser, melde dich mit deinem"
    info "  Hytale-Account an und gib den Code ein."
    info "  Der Download startet dann automatisch!"
    info "═══════════════════════════════════════════════════════════════"
    info ""
    cd "${HYTALE_DIR}"
    # Timeout 15min — genug Zeit für OAuth-Login
    timeout 900 sudo -u "${HYTALE_USER}" ./hytale-downloader-linux-${ARCH} 2>&1 || {
        warn "Downloader beendet (Timeout oder Fehler)"
        warn "Falls Auth fehlgeschlagen: Extension nochmal installieren"
    }
    # Entpacken falls ZIP heruntergeladen wurde
    ARCHIVE=$(ls -t "${HYTALE_DIR}"/*.zip 2>/dev/null | grep -v "hytale-downloader" | head -1)
    if [ -n "${ARCHIVE}" ]; then
        info "Entpacke ${ARCHIVE}..."
        cd "${HYTALE_DIR}"
        unzip -o "${ARCHIVE}" 2>/dev/null || true
        success "Server-Dateien entpackt"
    fi
fi

if [ -f "${HYTALE_DIR}/Server/HytaleServer.jar" ]; then
    success "Hytale Server bereit: ${HYTALE_DIR}/Server/HytaleServer.jar"
else
    warn "Server-Dateien nicht gefunden — Auth war möglicherweise nicht erfolgreich."
    warn "Extension nochmal installieren um den Download zu wiederholen."
fi

chown -R "${HYTALE_USER}:${HYTALE_USER}" "${HYTALE_DIR}"

# --- Firewall ---
if command -v ufw &>/dev/null; then
    ufw allow ${HYTALE_PORT}/udp comment "Hytale Game Server" 2>/dev/null || true
    ufw allow ${HYOS_PORT}/tcp comment "HyOS Dashboard" 2>/dev/null || true
    success "Firewall: Port ${HYTALE_PORT}/udp + ${HYOS_PORT}/tcp geöffnet"
fi

# --- systemd Service: Hytale Server ---
cat > /etc/systemd/system/hytale-server.service << EOF
[Unit]
Description=Hytale Dedicated Server
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=${HYTALE_USER}
WorkingDirectory=${HYTALE_DIR}
ExecStart=/usr/bin/java -Xmx4G -Xms2G -jar Server/HytaleServer.jar --assets Assets.zip
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable hytale-server 2>/dev/null || true
success "systemd Service: hytale-server"

# --- HyOS Dashboard (Native, kein Docker) ---
info "Installiere HyOS Dashboard..."

if [ ! -d "${HYOS_DIR}/.git" ]; then
    git clone --depth 1 https://github.com/EditMySave/HyOS.git "${HYOS_DIR}/src" 2>&1 | tail -3
    success "HyOS Repo geklont"
else
    cd "${HYOS_DIR}/src" && git pull --ff-only 2>&1 | tail -3
    success "HyOS aktualisiert"
fi

# Node.js Check
if ! command -v node &>/dev/null; then
    warn "Node.js nicht installiert — HyOS Dashboard braucht Node.js 18+"
    info "Installiere via: curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt-get install -y nodejs"
else
    # HyOS bauen
    cd "${HYOS_DIR}/src"
    if [ -f "package.json" ]; then
        npm ci --prefer-offline 2>&1 | tail -3 || npm install 2>&1 | tail -3
        npm run build 2>&1 | tail -5 || warn "HyOS Build fehlgeschlagen"
        success "HyOS Dashboard gebaut"
    fi
fi

# --- systemd Service: HyOS Dashboard ---
cat > /etc/systemd/system/hyos-dashboard.service << EOF
[Unit]
Description=HyOS Hytale Server Dashboard
After=network.target hytale-server.service

[Service]
Type=simple
User=${HYTALE_USER}
WorkingDirectory=${HYOS_DIR}/src
ExecStart=/usr/bin/node node_modules/.bin/next start -p ${HYOS_PORT}
Restart=on-failure
RestartSec=10
Environment=NODE_ENV=production
Environment=HYTALE_SERVER_DIR=${HYTALE_DIR}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable hyos-dashboard 2>/dev/null || true
success "systemd Service: hyos-dashboard"

# --- Starten ---
chown -R "${HYTALE_USER}:${HYTALE_USER}" "${HYTALE_DIR}" "${HYOS_DIR}"

if [ -f "${HYTALE_DIR}/Server/HytaleServer.jar" ]; then
    systemctl start hytale-server 2>/dev/null || warn "Hytale Server Start fehlgeschlagen"
    systemctl start hyos-dashboard 2>/dev/null || warn "HyOS Dashboard Start fehlgeschlagen"
    info ""
    success "=== Installation abgeschlossen ==="
    info "Hytale Server: Port ${HYTALE_PORT}/udp"
    info "HyOS Dashboard: http://localhost:${HYOS_PORT}"
else
    systemctl start hyos-dashboard 2>/dev/null || true
    info ""
    success "=== Grundinstallation abgeschlossen ==="
    info "Java, Downloader und HyOS Dashboard sind installiert."
    info "HyOS Dashboard: http://localhost:${HYOS_PORT}"
    info ""
    warn "Hytale Server-Dateien fehlen noch — siehe Anleitung oben."
fi
