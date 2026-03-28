#!/usr/bin/env bash
set -euo pipefail
if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi

info "Deinstalliere Vaultwarden..."

systemctl stop vaultwarden    2>/dev/null || true
systemctl disable vaultwarden 2>/dev/null || true
rm -f /etc/systemd/system/vaultwarden.service
systemctl daemon-reload

rm -rf /opt/vaultwarden
rm -f /etc/hydrahive/vaultwarden.env
# Daten NICHT löschen — Passwörter sind zu wertvoll
warn "Daten in /var/lib/vaultwarden wurden NICHT gelöscht (Passwort-Datenbank)."
warn "Manuell löschen: sudo rm -rf /var/lib/vaultwarden"

userdel vaultwarden 2>/dev/null || true

# nginx-Einträge entfernen
NGINX_CONF="/etc/nginx/sites-available/hydrahive-console"
if [ -f "${NGINX_CONF}" ]; then
    sed -i '/# Vaultwarden/,/}/d' "${NGINX_CONF}" 2>/dev/null || true
    nginx -t &>/dev/null && systemctl reload nginx 2>/dev/null || true
fi

success "Vaultwarden deinstalliert (Daten erhalten)"
