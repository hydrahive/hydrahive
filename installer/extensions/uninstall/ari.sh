#!/usr/bin/env bash
set -euo pipefail
if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi

info "Deinstalliere Ari (Tor)..."

systemctl stop tor@default    2>/dev/null || true
systemctl disable tor@default 2>/dev/null || true

# Tor via apt entfernen
apt-get remove -y --quiet tor 2>/dev/null | grep -E "^(Entfern|Remov)" || true
apt-get autoremove -y --quiet 2>/dev/null || true

# Konfiguration entfernen (optional: torrc wurde von uns erzeugt)
rm -f /etc/tor/torrc
rm -f /etc/tor/torrc.bak.* 2>/dev/null || true

# HydraHive Config + Passwort-Datei entfernen
rm -f /etc/hydrahive/ari.json
rm -f /etc/hydrahive/ari-control.pass

# Tor-Daten NICHT löschen — Hidden-Service-Keys könnten darin liegen
warn "Tor-Daten unter /var/lib/tor wurden NICHT gelöscht (Hidden-Service-Keys!)."
warn "Manuell löschen: sudo rm -rf /var/lib/tor"

systemctl daemon-reload

success "Ari (Tor) deinstalliert"
