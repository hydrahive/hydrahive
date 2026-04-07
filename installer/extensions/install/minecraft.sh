#!/usr/bin/env bash
# HydraHive Extension — Minecraft Java Edition Server (PaperMC)
# Port: 25565 (Game), 25575 (RCON)
# Idempotent: erneuter Aufruf aktualisiert auf neuste Paper-Version

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[MC]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

MC_DIR="/opt/minecraft"
MC_USER="minecraft"
MC_PORT="25565"
RCON_PORT="25575"
RCON_PASS="minecraft_$(hostname | md5sum | head -c12)"
PAPER_API="https://api.papermc.io/v2/projects/paper"

info "=== Minecraft Java Edition Server (PaperMC) installieren ==="

# --- Java installieren ---
info "Prüfe Java..."
if ! command -v java &>/dev/null || ! java -version 2>&1 | grep -q "21\|17"; then
    info "Installiere Java 21..."
    apt-get update -qq 2>&1 | tail -1
    apt-get install -y -qq openjdk-21-jre-headless 2>&1 | tail -3
    success "Java 21 installiert"
else
    success "Java bereits vorhanden: $(java -version 2>&1 | head -1)"
fi

# --- System-User ---
if ! id "${MC_USER}" &>/dev/null; then
    useradd -r -m -d "${MC_DIR}" -s /bin/bash "${MC_USER}"
    success "User ${MC_USER} erstellt"
fi
mkdir -p "${MC_DIR}"

# --- Neueste PaperMC Version holen ---
info "Hole neueste PaperMC Version..."
LATEST_VERSION=$(curl -s "${PAPER_API}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['versions'][-1])")
LATEST_BUILD=$(curl -s "${PAPER_API}/versions/${LATEST_VERSION}/builds" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['builds'][-1]['build'])")
JAR_NAME="paper-${LATEST_VERSION}-${LATEST_BUILD}.jar"
DOWNLOAD_URL="${PAPER_API}/versions/${LATEST_VERSION}/builds/${LATEST_BUILD}/downloads/${JAR_NAME}"

info "Lade PaperMC ${LATEST_VERSION} Build ${LATEST_BUILD}..."
curl -s -o "${MC_DIR}/server.jar" "${DOWNLOAD_URL}"
success "PaperMC heruntergeladen: ${JAR_NAME}"

# --- EULA akzeptieren ---
echo "eula=true" > "${MC_DIR}/eula.txt"
success "EULA akzeptiert"

# --- server.properties konfigurieren ---
if [ ! -f "${MC_DIR}/server.properties" ]; then
    cat > "${MC_DIR}/server.properties" << EOF
server-port=${MC_PORT}
rcon.port=${RCON_PORT}
rcon.password=${RCON_PASS}
enable-rcon=true
online-mode=false
max-players=20
view-distance=10
simulation-distance=8
difficulty=normal
gamemode=survival
motd=HydraHive Minecraft Server
EOF
    success "server.properties erstellt"
else
    # RCON sicherstellen
    grep -q "enable-rcon=true" "${MC_DIR}/server.properties" || echo "enable-rcon=true" >> "${MC_DIR}/server.properties"
    grep -q "rcon.port=" "${MC_DIR}/server.properties" || echo "rcon.port=${RCON_PORT}" >> "${MC_DIR}/server.properties"
    grep -q "rcon.password=" "${MC_DIR}/server.properties" || echo "rcon.password=${RCON_PASS}" >> "${MC_DIR}/server.properties"
    success "server.properties bereits vorhanden — RCON geprüft"
fi

# --- Ownership ---
chown -R "${MC_USER}:${MC_USER}" "${MC_DIR}"

# --- Firewall ---
if command -v ufw &>/dev/null; then
    ufw allow ${MC_PORT}/tcp comment "Minecraft Server" 2>/dev/null || true
    ufw allow ${RCON_PORT}/tcp comment "Minecraft RCON" 2>/dev/null || true
    success "Firewall: Port ${MC_PORT}/tcp + ${RCON_PORT}/tcp geöffnet"
fi

# --- systemd Service ---
cat > /etc/systemd/system/minecraft.service << EOF
[Unit]
Description=Minecraft Java Edition Server (PaperMC)
After=network.target

[Service]
Type=simple
User=${MC_USER}
WorkingDirectory=${MC_DIR}
ExecStart=/usr/bin/java -Xms1G -Xmx4G -jar ${MC_DIR}/server.jar nogui
ExecStop=/usr/bin/kill -SIGTERM \$MAINPID
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable minecraft 2>/dev/null || true
systemctl start minecraft 2>/dev/null || warn "Server Start fehlgeschlagen — prüfe logs: journalctl -u minecraft"
success "systemd Service: minecraft"

# --- Credentials speichern ---
cat > /etc/hydrahive/minecraft.json << EOF
{
    "rcon_host": "127.0.0.1",
    "rcon_port": ${RCON_PORT},
    "rcon_password": "${RCON_PASS}",
    "game_port": ${MC_PORT},
    "server_dir": "${MC_DIR}",
    "version": "${LATEST_VERSION}",
    "build": "${LATEST_BUILD}"
}
EOF
chmod 600 /etc/hydrahive/minecraft.json

info ""
success "=== Minecraft Server Installation abgeschlossen ==="
info "Game Port:   ${MC_PORT}/tcp"
info "RCON Port:   ${RCON_PORT}/tcp"
info "RCON Pass:   ${RCON_PASS}"
info "Version:     PaperMC ${LATEST_VERSION} Build ${LATEST_BUILD}"
info "Logs:        journalctl -u minecraft -f"
