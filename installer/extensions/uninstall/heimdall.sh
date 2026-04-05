#!/usr/bin/env bash
set -euo pipefail
if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi

info "Deinstalliere Heimdall..."

# Queue-Worker stoppen
systemctl stop    heimdall 2>/dev/null || true
systemctl disable heimdall 2>/dev/null || true
rm -f /etc/systemd/system/heimdall.service
systemctl daemon-reload

# nginx-Konfiguration entfernen
rm -f /etc/nginx/sites-enabled/heimdall
rm -f /etc/nginx/sites-available/heimdall
nginx -t &>/dev/null && systemctl reload nginx 2>/dev/null || true

# PHP-FPM Pool entfernen
for phpv in 8.3 8.2 8.1 8.0; do
    POOL="/etc/php/${phpv}/fpm/pool.d/heimdall.conf"
    if [ -f "${POOL}" ]; then
        rm -f "${POOL}"
        systemctl restart "php${phpv}-fpm" 2>/dev/null || true
        break
    fi
done

# App-Verzeichnis entfernen (enthält SQLite-DB + Konfiguration)
warn "App-Verzeichnis /opt/heimdall wird entfernt (inkl. SQLite-Datenbank mit Dashboard-Konfiguration)."
warn "Backup vorher: sudo cp /opt/heimdall/database/app.sqlite ~/heimdall-backup.sqlite"
rm -rf /opt/heimdall

# HydraHive Config entfernen
rm -f /etc/hydrahive/heimdall.json

# System-User entfernen
userdel heimdall 2>/dev/null || true
groupdel heimdall 2>/dev/null || true

success "Heimdall deinstalliert"
