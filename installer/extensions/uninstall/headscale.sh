#!/usr/bin/env bash
set -euo pipefail
if ! declare -f info  &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn  &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi

info "Deinstalliere Headscale..."

systemctl stop headscale    2>/dev/null || true
systemctl disable headscale 2>/dev/null || true
rm -f /etc/systemd/system/headscale.service
systemctl daemon-reload

rm -f /usr/local/bin/headscale
rm -rf /etc/headscale /var/lib/headscale
userdel -r headscale 2>/dev/null || true

success "Headscale deinstalliert"
