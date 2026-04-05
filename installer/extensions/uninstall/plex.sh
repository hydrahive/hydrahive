#!/usr/bin/env bash
# HydraHive Extension - Plex Media Server deinstallieren
set -euo pipefail

if ! declare -f info &>/dev/null; then
    info()    { echo "[INFO] $1"; }
    success() { echo "[OK] $1"; }
    warn()    { echo "[WARN] $1"; }
fi

info "Deinstalliere Plex Media Server..."

# --- Service stoppen ---
systemctl stop plexmediaserver 2>/dev/null || true
systemctl disable plexmediaserver 2>/dev/null || true

# --- Paket entfernen ---
if dpkg -l plexmediaserver &>/dev/null 2>&1; then
    apt-get remove -y --purge plexmediaserver 2>/dev/null || true
    success "Paket 'plexmediaserver' entfernt"
fi

# --- Repository und GPG-Schlüssel entfernen ---
rm -f /etc/apt/sources.list.d/plexmediaserver.list
rm -f /etc/apt/keyrings/plex.gpg
rm -f /etc/apt/trusted.gpg.d/plexmediaserver.asc
apt-get update -qq 2>/dev/null || true
success "Plex apt-Repository entfernt"

# --- Daten-Verzeichnis ---
warn "Plex-Daten unter /var/lib/plexmediaserver werden NICHT automatisch gelöscht."
warn "Manuell entfernen: sudo rm -rf /var/lib/plexmediaserver"

# --- HydraHive Config entfernen ---
rm -f /etc/hydrahive/plex.json

# --- User/Gruppe entfernen (wird durch apt-purge angelegt) ---
userdel plex 2>/dev/null || true
groupdel plex 2>/dev/null || true

success "Plex Media Server deinstalliert"
