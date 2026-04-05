#!/usr/bin/env bash
# HydraHive Extension - Radicale deinstallieren
set -euo pipefail

if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi

info "Deinstalliere Radicale..."

# --- Service stoppen ---
systemctl stop    radicale 2>/dev/null || true
systemctl disable radicale 2>/dev/null || true
rm -f /etc/systemd/system/radicale.service
systemctl daemon-reload

# --- Konfiguration entfernen ---
rm -rf /etc/radicale

# --- Logs entfernen ---
rm -rf /var/log/radicale

# --- venv entfernen (falls pip-Installation) ---
if [ -d /opt/radicale ]; then
    warn "virtualenv /opt/radicale wird entfernt."
    rm -rf /opt/radicale
    success "virtualenv /opt/radicale entfernt"
fi

# --- apt-Paket entfernen (falls apt-Installation) ---
if dpkg -l radicale &>/dev/null 2>&1; then
    apt-get remove -y --quiet radicale 2>/dev/null || true
    success "radicale apt-Paket entfernt"
fi

# --- HydraHive Config entfernen ---
rm -f /etc/hydrahive/radicale.json

# --- System-User entfernen ---
userdel radicale 2>/dev/null || true
groupdel radicale 2>/dev/null || true

# --- Kalender-/Kontaktdaten NICHT löschen ---
warn "Kalender- und Kontaktdaten in /var/lib/radicale wurden NICHT gelöscht."
warn "Manuell entfernen: sudo rm -rf /var/lib/radicale"

success "Radicale deinstalliert"
