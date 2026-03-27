#!/usr/bin/env bash
set -euo pipefail
if ! declare -f info  &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn  &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi

info "Deinstalliere WhatsApp Bridge..."

systemctl stop hydrahive-whatsapp-bridge    2>/dev/null || true
systemctl disable hydrahive-whatsapp-bridge 2>/dev/null || true
rm -f /etc/systemd/system/hydrahive-whatsapp-bridge.service
systemctl daemon-reload

rm -rf /opt/hydrahive/whatsapp-bridge

success "WhatsApp Bridge deinstalliert"
