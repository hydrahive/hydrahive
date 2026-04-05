#!/usr/bin/env bash
set -euo pipefail
if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi

info "Deinstalliere AdGuard Home..."

systemctl stop    adguardhome 2>/dev/null || true
systemctl disable adguardhome 2>/dev/null || true
rm -f /etc/systemd/system/adguardhome.service
systemctl daemon-reload

# Binary + Konfig entfernen
rm -rf /opt/adguard-home

# HydraHive Config entfernen
rm -f /etc/hydrahive/adguard-home.json

# System-User entfernen
userdel adguardhome 2>/dev/null || true
groupdel agarhom 2>/dev/null || true

# Laufzeitdaten NICHT löschen — Blocklist-Stats, eigene Regeln etc.
warn "Daten in /var/lib/adguard-home wurden NICHT gelöscht (Query-Logs, Blocklists, Statistiken)."
warn "Manuell löschen: sudo rm -rf /var/lib/adguard-home"

success "AdGuard Home deinstalliert"
