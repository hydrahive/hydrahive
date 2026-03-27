#!/usr/bin/env bash
set -euo pipefail
if ! declare -f info  &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn  &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi

info "Deinstalliere Gitea..."

systemctl stop gitea    2>/dev/null || true
systemctl disable gitea 2>/dev/null || true
rm -f /etc/systemd/system/gitea.service /lib/systemd/system/gitea.service
systemctl daemon-reload

rm -f /usr/local/bin/gitea
rm -rf /opt/gitea /etc/gitea /var/lib/gitea
userdel -r git 2>/dev/null || true

rm -f /etc/nginx/sites-enabled/gitea /etc/nginx/sites-available/gitea
nginx -t &>/dev/null && systemctl reload nginx 2>/dev/null || true

rm -f /etc/hydrahive/gitea_config.json

success "Gitea deinstalliert"
