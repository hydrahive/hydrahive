#!/usr/bin/env bash
# HydraHive Extension - Vikunja deinstallieren
set -euo pipefail

if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi

info "Deinstalliere Vikunja..."

# --- Service stoppen ---
systemctl stop    vikunja 2>/dev/null || true
systemctl disable vikunja 2>/dev/null || true
rm -f /etc/systemd/system/vikunja.service
systemctl daemon-reload

# --- Binary + Installationsverzeichnis entfernen ---
warn "Installationsverzeichnis /opt/vikunja wird entfernt."
rm -rf /opt/vikunja
success "Installationsverzeichnis /opt/vikunja entfernt"

# --- Konfiguration entfernen ---
rm -rf /etc/vikunja

# --- HydraHive Config entfernen ---
rm -f /etc/hydrahive/vikunja.json

# --- System-User entfernen ---
userdel vikunja 2>/dev/null || true
groupdel vikunja 2>/dev/null || true

# --- Daten NICHT löschen ---
warn "Aufgaben und Daten in /var/lib/vikunja wurden NICHT gelöscht."
warn "Manuell entfernen: sudo rm -rf /var/lib/vikunja"

success "Vikunja deinstalliert"
