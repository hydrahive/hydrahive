#!/usr/bin/env bash
# HydraHive Extension — Minecraft Uninstaller

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[MC]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
fi

info "=== Minecraft Server deinstallieren ==="

systemctl stop minecraft 2>/dev/null || true
systemctl disable minecraft 2>/dev/null || true
rm -f /etc/systemd/system/minecraft.service
systemctl daemon-reload
success "systemd Service entfernt"

rm -rf /opt/minecraft
success "/opt/minecraft entfernt"

userdel -r minecraft 2>/dev/null || true
success "User minecraft entfernt"

rm -f /etc/hydrahive/minecraft.json
success "Credentials entfernt"

if command -v ufw &>/dev/null; then
    ufw delete allow 25565/tcp 2>/dev/null || true
    ufw delete allow 25575/tcp 2>/dev/null || true
fi

success "=== Minecraft Server erfolgreich deinstalliert ==="
