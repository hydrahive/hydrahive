#!/usr/bin/env bash
set -euo pipefail
if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi

info "Deinstalliere Paperless-ngx..."

# Services stoppen + deaktivieren
for svc in paperless-webserver paperless-consumer paperless-scheduler; do
    systemctl stop    "${svc}" 2>/dev/null || true
    systemctl disable "${svc}" 2>/dev/null || true
    rm -f "/etc/systemd/system/${svc}.service"
done
systemctl daemon-reload

# App-Verzeichnis entfernen
rm -rf /opt/paperless-ngx

# HydraHive Config entfernen
rm -f /etc/hydrahive/paperless-ngx.json

# System-User entfernen
userdel paperless 2>/dev/null || true
groupdel paprss 2>/dev/null || true

# Daten und Datenbank NICHT löschen — Dokumente sind zu wertvoll
warn "Daten in /var/lib/paperless-ngx wurden NICHT gelöscht (Dokumente, OCR-Dateien)."
warn "Manuell löschen: sudo rm -rf /var/lib/paperless-ngx"
warn "PostgreSQL-Datenbank 'paperless' wurde NICHT gelöscht."
warn "Manuell löschen: sudo -u postgres psql -c 'DROP DATABASE paperless; DROP USER paperless;'"

success "Paperless-ngx deinstalliert (Daten erhalten)"
