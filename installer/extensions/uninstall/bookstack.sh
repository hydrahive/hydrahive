#!/usr/bin/env bash
# HydraHive Extension - BookStack deinstallieren
set -euo pipefail

if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi

info "Deinstalliere BookStack..."

# --- Services stoppen (artisan serve + queue) ---
systemctl stop    bookstack bookstack-queue 2>/dev/null || true
systemctl disable bookstack bookstack-queue 2>/dev/null || true
rm -f /etc/systemd/system/bookstack.service
rm -f /etc/systemd/system/bookstack-queue.service
systemctl daemon-reload

# --- Legacy: nginx-Konfiguration entfernen (alte Installationen) ---
if [ -f /etc/nginx/sites-enabled/bookstack ] || [ -f /etc/nginx/sites-available/bookstack ]; then
    rm -f /etc/nginx/sites-enabled/bookstack
    rm -f /etc/nginx/sites-available/bookstack
    nginx -t &>/dev/null && systemctl reload nginx 2>/dev/null || true
    success "Legacy nginx-Config entfernt"
fi

# --- Legacy: PHP-FPM Pool entfernen (alte Installationen) ---
for phpv in 8.3 8.2 8.1 8.0; do
    POOL="/etc/php/${phpv}/fpm/pool.d/bookstack.conf"
    if [ -f "${POOL}" ]; then
        rm -f "${POOL}"
        systemctl restart "php${phpv}-fpm" 2>/dev/null || true
        break
    fi
done

# --- App-Verzeichnis entfernen ---
warn "App-Verzeichnis /opt/bookstack wird entfernt (enthält Code, Uploads, Konfiguration)."
warn "Datenbank-Backup vorher empfohlen: mysqldump bookstack > ~/bookstack-backup.sql"
rm -rf /opt/bookstack
success "App-Verzeichnis /opt/bookstack entfernt"

# --- Datenbank entfernen ---
warn "BookStack-Datenbank 'bookstack' wird entfernt..."
mysql -e "DROP DATABASE IF EXISTS bookstack;" 2>/dev/null || true
mysql -e "DROP USER IF EXISTS 'bookstack'@'localhost';" 2>/dev/null || true
mysql -e "FLUSH PRIVILEGES;" 2>/dev/null || true
success "Datenbank 'bookstack' entfernt"

# --- Logs entfernen ---
rm -f /var/log/nginx/bookstack-access.log /var/log/nginx/bookstack-error.log

# --- HydraHive Config entfernen ---
rm -f /etc/hydrahive/bookstack.json

# --- System-User entfernen ---
userdel bookstack 2>/dev/null || true
groupdel bookstack 2>/dev/null || true

success "BookStack deinstalliert"
