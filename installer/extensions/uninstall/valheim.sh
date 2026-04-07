#!/usr/bin/env bash
# HydraHive Extension — Valheim Uninstaller

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[VH]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
fi

info "=== Valheim Server deinstallieren ==="

systemctl stop valheim 2>/dev/null || true
systemctl disable valheim 2>/dev/null || true
rm -f /etc/systemd/system/valheim.service
systemctl daemon-reload
success "systemd Service entfernt"

rm -rf /opt/valheim
success "/opt/valheim entfernt"

userdel -r valheim 2>/dev/null || true
success "User valheim entfernt"

rm -f /etc/hydrahive/valheim.json
success "Credentials entfernt"

if command -v ufw &>/dev/null; then
    ufw delete allow 2456:2458/udp 2>/dev/null || true
fi

success "=== Valheim Server erfolgreich deinstalliert ==="
