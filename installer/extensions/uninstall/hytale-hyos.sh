#!/usr/bin/env bash
# HydraHive Extension — Hytale + HyOS Deinstallation

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[Hytale]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

info "=== Hytale Server + HyOS deinstallieren ==="

# Services stoppen
systemctl stop hyos-dashboard 2>/dev/null || true
systemctl stop hytale-server 2>/dev/null || true
systemctl disable hyos-dashboard 2>/dev/null || true
systemctl disable hytale-server 2>/dev/null || true
rm -f /etc/systemd/system/hytale-server.service
rm -f /etc/systemd/system/hyos-dashboard.service
systemctl daemon-reload

# Verzeichnisse entfernen
rm -rf /opt/hytale
rm -rf /opt/hyos

# User entfernen
userdel -r hytale 2>/dev/null || true

# Firewall
if command -v ufw &>/dev/null; then
    ufw delete allow 5520/udp 2>/dev/null || true
    ufw delete allow 3100/tcp 2>/dev/null || true
fi

success "Hytale Server + HyOS Dashboard deinstalliert"
