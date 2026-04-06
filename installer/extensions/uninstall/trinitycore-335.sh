#!/usr/bin/env bash
set -euo pipefail
if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[TC-335]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
fi

info "=== TrinityCore 3.3.5a deinstallieren ==="

systemctl stop trinitycore-335-world trinitycore-335-auth 2>/dev/null || true
systemctl disable trinitycore-335-world trinitycore-335-auth 2>/dev/null || true
rm -f /etc/systemd/system/trinitycore-335-world.service
rm -f /etc/systemd/system/trinitycore-335-auth.service
systemctl daemon-reload

# Datenbanken behalten? Sicherheitshalber nur droppen wenn explizit gewünscht
# mysql -u root -e "DROP DATABASE IF EXISTS trinity335_auth; DROP DATABASE IF EXISTS trinity335_characters; DROP DATABASE IF EXISTS trinity335_world; DROP USER IF EXISTS 'trinity335'@'localhost';" 2>/dev/null || true

rm -rf /opt/trinitycore-335
userdel -r trinity335 2>/dev/null || true
rm -f /etc/hydrahive/trinitycore-335.json

if command -v ufw &>/dev/null; then
    ufw delete allow 8085/tcp 2>/dev/null || true
    ufw delete allow 3724/tcp 2>/dev/null || true
fi

success "TrinityCore 3.3.5a deinstalliert (Datenbanken behalten)"
