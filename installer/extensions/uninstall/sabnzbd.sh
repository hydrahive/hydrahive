#!/usr/bin/env bash
# HydraHive Extension - SABnzbd deinstallieren
set -euo pipefail

if ! declare -f info &>/dev/null; then
    info()    { echo "[INFO] $1"; }
    success() { echo "[OK] $1"; }
    warn()    { echo "[WARN] $1"; }
fi

info "Deinstalliere SABnzbd..."

# --- Service stoppen (beide möglichen Service-Namen) ---
for svc in sabnzbd sabnzbdplus; do
    systemctl stop "${svc}" 2>/dev/null || true
    systemctl disable "${svc}" 2>/dev/null || true
done
rm -f /etc/systemd/system/sabnzbd.service
systemctl daemon-reload

# --- Paket entfernen ---
if dpkg -l sabnzbdplus &>/dev/null 2>&1; then
    apt-get remove -y --purge sabnzbdplus 2>/dev/null || true
    success "Paket 'sabnzbdplus' entfernt"
fi

# --- Konfigurationsdatei in /etc/default ---
rm -f /etc/default/sabnzbdplus

# --- Daten-Verzeichnis ---
warn "SABnzbd-Daten unter /var/lib/sabnzbd werden NICHT automatisch gelöscht."
warn "Manuell entfernen: sudo rm -rf /var/lib/sabnzbd"

# --- HydraHive Config entfernen ---
rm -f /etc/hydrahive/sabnzbd.json

# --- User/Gruppe entfernen ---
userdel sabnzbd 2>/dev/null || true
groupdel sabnzbd 2>/dev/null || true

success "SABnzbd deinstalliert"
