#!/usr/bin/env bash
# HydraHive Extension - Radarr deinstallieren
set -euo pipefail

if ! declare -f info &>/dev/null; then
    info()    { echo "[INFO] $1"; }
    success() { echo "[OK] $1"; }
    warn()    { echo "[WARN] $1"; }
fi

info "Deinstalliere Radarr..."

# --- Service stoppen ---
systemctl stop radarr 2>/dev/null || true
systemctl disable radarr 2>/dev/null || true
rm -f /etc/systemd/system/radarr.service
systemctl daemon-reload

# --- Programm-Verzeichnis entfernen ---
warn "Installationsverzeichnis /opt/radarr wird entfernt."
rm -rf /opt/radarr
success "Programm-Verzeichnis /opt/radarr entfernt"

# --- Daten-Verzeichnis ---
warn "Radarr-Daten unter /var/lib/radarr werden NICHT automatisch gelöscht."
warn "Manuell entfernen: sudo rm -rf /var/lib/radarr"

# --- HydraHive Config entfernen ---
rm -f /etc/hydrahive/radarr.json

# --- User/Gruppe entfernen ---
userdel radarr 2>/dev/null || true
groupdel radarr 2>/dev/null || true

success "Radarr deinstalliert"
