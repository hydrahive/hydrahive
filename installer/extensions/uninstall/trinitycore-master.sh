#!/usr/bin/env bash
set -euo pipefail
if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[TC-Master]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
fi

info "=== TrinityCore Master deinstallieren ==="

systemctl stop trinitycore-master-world trinitycore-master-auth 2>/dev/null || true
systemctl disable trinitycore-master-world trinitycore-master-auth 2>/dev/null || true
rm -f /etc/systemd/system/trinitycore-master-world.service
rm -f /etc/systemd/system/trinitycore-master-auth.service
systemctl daemon-reload

rm -rf /opt/trinitycore-master
userdel -r trinitymaster 2>/dev/null || true
rm -f /etc/hydrahive/trinitycore-master.json

if command -v ufw &>/dev/null; then
    ufw delete allow 8086/tcp 2>/dev/null || true
    ufw delete allow 3725/tcp 2>/dev/null || true
fi

success "TrinityCore Master deinstalliert (Datenbanken behalten)"
