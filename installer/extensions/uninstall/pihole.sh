#!/usr/bin/env bash
# HydraHive Extension - Pi-hole deinstallieren
set -euo pipefail

if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi

info "Deinstalliere Pi-hole..."

# --- pihole-FTL Service stoppen ---
systemctl stop    pihole-FTL 2>/dev/null || true
systemctl disable pihole-FTL 2>/dev/null || true

# --- Pi-hole eigenen Uninstaller nutzen (falls vorhanden) ---
if [ -x /usr/local/bin/pihole ]; then
    info "Führe pihole uninstall aus..."
    PIHOLE_SKIP_OS_CHECK=true pihole uninstall --unattended 2>/dev/null \
        || warn "pihole uninstall fehlgeschlagen — bereinige manuell"
fi

# --- nginx-Konfiguration für Pi-hole entfernen ---
rm -f /etc/nginx/sites-enabled/pihole
rm -f /etc/nginx/sites-available/pihole
nginx -t &>/dev/null && systemctl reload nginx 2>/dev/null || true
success "nginx-Konfiguration entfernt"

# --- Verbleibende Pi-hole Dateien entfernen ---
# (pihole uninstall lässt manchmal Reste)
rm -f /usr/local/bin/pihole
rm -rf /opt/pihole
rm -f /etc/systemd/system/pihole-FTL.service
rm -f /etc/init.d/pihole-FTL 2>/dev/null || true

# apt-Pakete entfernen die Pi-hole installiert hat
apt-get remove -y --quiet \
    lighttpd dns-root-data \
    2>/dev/null | grep -E "^(Entferne|Removing)" || true

systemctl daemon-reload

# --- HydraHive Config entfernen ---
rm -f /etc/hydrahive/pihole.json

# --- Konfiguration und Logs NICHT löschen ---
warn "Pi-hole-Konfiguration /etc/pihole wurde NICHT gelöscht (Blocklists, Custom-Rules etc.)."
warn "Manuell entfernen: sudo rm -rf /etc/pihole"

success "Pi-hole deinstalliert"
