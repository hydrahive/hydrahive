#!/usr/bin/env bash
# HydraHive Extension — Valheim Dedicated Server
# Port: 2456-2458/udp
# Idempotent: erneuter Aufruf aktualisiert via SteamCMD

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[VH]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

VH_DIR="/opt/valheim"
VH_USER="valheim"
VH_PORT="2456"
WORLD_NAME="HydraHiveWorld"
SERVER_NAME="HydraHive Valheim"
SERVER_PASS="valheim_$(hostname | md5sum | head -c8)"
STEAMCMD_DIR="/opt/steamcmd"

info "=== Valheim Dedicated Server installieren ==="

# --- Dependencies ---
info "Installiere Dependencies..."
dpkg --add-architecture i386 2>/dev/null || true
apt-get update -qq 2>&1 | tail -1
apt-get install -y -qq lib32gcc-s1 curl tar 2>&1 | tail -3
success "Dependencies installiert"

# --- SteamCMD ---
if [ ! -f "${STEAMCMD_DIR}/steamcmd.sh" ]; then
    info "Installiere SteamCMD..."
    mkdir -p "${STEAMCMD_DIR}"
    curl -s -o /tmp/steamcmd.tar.gz "https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz"
    tar -xzf /tmp/steamcmd.tar.gz -C "${STEAMCMD_DIR}"
    rm /tmp/steamcmd.tar.gz
    success "SteamCMD installiert"
else
    success "SteamCMD bereits vorhanden"
fi

# --- System-User ---
if ! id "${VH_USER}" &>/dev/null; then
    useradd -r -m -d "${VH_DIR}" -s /bin/bash "${VH_USER}"
    success "User ${VH_USER} erstellt"
fi
mkdir -p "${VH_DIR}"

# --- Valheim Server installieren/updaten ---
info "Installiere/Aktualisiere Valheim Server via SteamCMD (App ID 896660)..."
"${STEAMCMD_DIR}/steamcmd.sh" \
    +force_install_dir "${VH_DIR}" \
    +login anonymous \
    +app_update 896660 validate \
    +quit 2>&1 | tail -10
success "Valheim Server installiert/aktualisiert"

# --- Konfiguration ---
mkdir -p "${VH_DIR}/.config/unity3d/IronGate/Valheim"
chown -R "${VH_USER}:${VH_USER}" "${VH_DIR}"

# --- Firewall ---
if command -v ufw &>/dev/null; then
    ufw allow ${VH_PORT}:$((VH_PORT+2))/udp comment "Valheim Server" 2>/dev/null || true
    success "Firewall: Port ${VH_PORT}-$((VH_PORT+2))/udp geöffnet"
fi

# --- systemd Service ---
cat > /etc/systemd/system/valheim.service << EOF
[Unit]
Description=Valheim Dedicated Server
After=network.target

[Service]
Type=simple
User=${VH_USER}
WorkingDirectory=${VH_DIR}
ExecStart=${VH_DIR}/valheim_server.x86_64 \
    -name "${SERVER_NAME}" \
    -port ${VH_PORT} \
    -world "${WORLD_NAME}" \
    -password "${SERVER_PASS}" \
    -public 0
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal
Environment=SteamAppId=892970

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable valheim 2>/dev/null || true
systemctl start valheim 2>/dev/null || warn "Server Start fehlgeschlagen — prüfe: journalctl -u valheim"
success "systemd Service: valheim"

# --- Credentials speichern ---
cat > /etc/hydrahive/valheim.json << EOF
{
    "server_name": "${SERVER_NAME}",
    "world_name": "${WORLD_NAME}",
    "port": ${VH_PORT},
    "password": "${SERVER_PASS}",
    "server_dir": "${VH_DIR}"
}
EOF
chmod 600 /etc/hydrahive/valheim.json

info ""
success "=== Valheim Server Installation abgeschlossen ==="
info "Server Name: ${SERVER_NAME}"
info "World:       ${WORLD_NAME}"
info "Port:        ${VH_PORT}-$((VH_PORT+2))/udp"
info "Passwort:    ${SERVER_PASS}"
info "Logs:        journalctl -u valheim -f"
