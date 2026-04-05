#!/usr/bin/env bash
# HydraHive Extension - Sonarr deinstallieren
set -euo pipefail

if ! declare -f info &>/dev/null; then
    info()    { echo "[INFO] $1"; }
    success() { echo "[OK] $1"; }
    warn()    { echo "[WARN] $1"; }
fi

info "Deinstalliere Sonarr..."

# --- Service stoppen ---
systemctl stop sonarr 2>/dev/null || true
systemctl disable sonarr 2>/dev/null || true
rm -f /etc/systemd/system/sonarr.service
systemctl daemon-reload

# --- Programm-Verzeichnis entfernen ---
warn "Installationsverzeichnis /opt/sonarr wird entfernt."
rm -rf /opt/sonarr
success "Programm-Verzeichnis /opt/sonarr entfernt"

# --- Daten-Verzeichnis ---
warn "Sonarr-Daten unter /var/lib/sonarr werden NICHT automatisch gelöscht."
warn "Manuell entfernen: sudo rm -rf /var/lib/sonarr"

# --- HydraHive Config entfernen ---
rm -f /etc/hydrahive/sonarr.json

# --- User/Gruppe entfernen ---
userdel sonarr 2>/dev/null || true
groupdel sonarr 2>/dev/null || true

success "Sonarr deinstalliert"
