#!/usr/bin/env bash
set -euo pipefail
if ! declare -f info  &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn  &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi

info "Deinstalliere SearXNG..."

systemctl stop searxng    2>/dev/null || true
systemctl disable searxng 2>/dev/null || true
rm -f /etc/systemd/system/searxng.service
systemctl daemon-reload

rm -rf /opt/searxng /etc/searxng
userdel -r searxng 2>/dev/null || true

# nginx-Eintrag entfernen
rm -f /etc/nginx/sites-enabled/searxng /etc/nginx/sites-available/searxng
nginx -t &>/dev/null && systemctl reload nginx 2>/dev/null || true

DEBIAN_FRONTEND=noninteractive apt-get remove -y uwsgi uwsgi-plugin-python3 2>/dev/null || true

success "SearXNG deinstalliert"
