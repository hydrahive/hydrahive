#!/usr/bin/env bash
# HydraHive Uninstaller
# Entfernt alle HydraHive-Komponenten von diesem System.
# Usage: sudo bash uninstall.sh [--yes]

set -euo pipefail

RED="\033[0;31m"; GREEN="\033[0;32m"; YELLOW="\033[1;33m"; BLUE="\033[0;34m"; NC="\033[0m"
info()    { echo -e "${BLUE}[Uninstall]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR]${NC} Bitte als root ausfuehren: sudo bash uninstall.sh"
    exit 1
fi

echo ""
echo -e "${RED}╔══════════════════════════════════════╗${NC}"
echo -e "${RED}║     HydraHive Uninstaller            ║${NC}"
echo -e "${RED}╚══════════════════════════════════════╝${NC}"
echo ""
warn "Dies entfernt ALLE HydraHive-Komponenten, Daten und Konfigurationen."
warn "Agenten, Projekte, Backups — alles wird gelöscht."
echo ""

if [[ "${1:-}" != "--yes" ]]; then
    read -rp "Wirklich deinstallieren? Tippe 'ja' zum Bestätigen: " CONFIRM
    if [[ "${CONFIRM}" != "ja" ]]; then
        echo "Abgebrochen."
        exit 0
    fi
fi

# ── 1. Services stoppen und deaktivieren ─────────────────────────────────────

info "Stoppe und deaktiviere Services..."

SERVICES=(
    hydrahive-core
    hydrahive-conduwuit
    hydrahive-agentlink
    hydrahive-chromadb
    hydrahive-amem
    hydrahive-whatsapp-bridge
    hydrahive-selfupdate
    hydrahive-autoupdate
    hydrahive-selfupdate.timer
    gitea
    tailscaled
)

for svc in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "${svc}" 2>/dev/null; then
        systemctl stop "${svc}" && info "  Gestoppt: ${svc}"
    fi
    if systemctl is-enabled --quiet "${svc}" 2>/dev/null; then
        systemctl disable "${svc}" 2>/dev/null && true
    fi
done

success "Services gestoppt"

# ── 2. Systemd Unit-Dateien entfernen ────────────────────────────────────────

info "Entferne Systemd-Units..."
for f in \
    /etc/systemd/system/hydrahive-core.service \
    /etc/systemd/system/hydrahive-conduwuit.service \
    /etc/systemd/system/hydrahive-agentlink.service \
    /etc/systemd/system/hydrahive-chromadb.service \
    /etc/systemd/system/hydrahive-amem.service \
    /etc/systemd/system/hydrahive-whatsapp-bridge.service \
    /etc/systemd/system/hydrahive-selfupdate.service \
    /etc/systemd/system/hydrahive-autoupdate.service \
    /etc/systemd/system/hydrahive-selfupdate.timer \
    /lib/systemd/system/gitea.service \
    /etc/systemd/system/gitea.service; do
    [ -f "$f" ] && rm -f "$f" && info "  Entfernt: $f"
done
systemctl daemon-reload
success "Systemd-Units entfernt"

# ── 3. nginx-Konfiguration bereinigen ────────────────────────────────────────

info "Bereinige nginx..."
for f in \
    /etc/nginx/sites-enabled/hydrahive-console \
    /etc/nginx/sites-available/hydrahive-console \
    /etc/nginx/sites-enabled/gitea \
    /etc/nginx/sites-available/gitea; do
    [ -f "$f" ] || [ -L "$f" ] && rm -f "$f" && info "  Entfernt: $f"
done

# default wieder aktivieren falls vorhanden
[ -f /etc/nginx/sites-available/default ] && \
    ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default 2>/dev/null || true

if nginx -t &>/dev/null; then
    systemctl reload nginx 2>/dev/null || true
fi
success "nginx bereinigt"

# ── 4. TLS-Zertifikate ───────────────────────────────────────────────────────

[ -d /etc/hydrahive/tls ] && rm -rf /etc/hydrahive/tls && info "TLS-Zertifikate entfernt"

# ── 5. sudoers entfernen ─────────────────────────────────────────────────────

info "Entferne sudoers-Regeln..."
for f in /etc/sudoers.d/hydrahive-*; do
    [ -f "$f" ] && rm -f "$f" && info "  Entfernt: $f"
done
success "sudoers bereinigt"

# ── 6. Daten und Konfiguration löschen ───────────────────────────────────────

info "Lösche Daten-Verzeichnisse..."
for dir in \
    /opt/hydrahive \
    /agents \
    /projects \
    /etc/hydrahive \
    /etc/gitea \
    /opt/gitea \
    /var/lib/hydrahive \
    /var/lib/conduwuit \
    /var/lib/matrix-conduit; do
    if [ -d "$dir" ]; then
        rm -rf "$dir"
        info "  Gelöscht: $dir"
    fi
done

# Binaries
[ -f /usr/local/bin/gitea   ] && rm -f /usr/local/bin/gitea   && info "  Gelöscht: gitea binary"
[ -f /usr/local/bin/conduwuit ] && true  # conduwuit via dpkg entfernen

# Laufzeit-Dateien
rm -f /var/run/hydrahive-update.json 2>/dev/null || true

success "Daten gelöscht"

# ── 7. Packages entfernen ────────────────────────────────────────────────────

info "Entferne Packages..."

# conduwuit via dpkg
if dpkg -l conduwuit &>/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get remove -y conduwuit &>/dev/null && success "  conduwuit entfernt"
fi

# PostgreSQL (nur wenn von uns installiert — nur entfernen wenn agentlink-DB leer)
if dpkg -l postgresql &>/dev/null 2>&1; then
    warn "  PostgreSQL vorhanden — wird NICHT automatisch entfernt (könnte andere DBs enthalten)"
    warn "  Manuell: sudo apt remove postgresql postgresql-contrib && sudo rm -rf /var/lib/postgresql"
fi

# Redis — ebenfalls vorsichtig
if dpkg -l redis-server &>/dev/null 2>&1; then
    warn "  Redis vorhanden — wird NICHT automatisch entfernt"
    warn "  Manuell: sudo apt remove redis-server"
fi

# ── 8. System-User entfernen ─────────────────────────────────────────────────

info "Entferne System-User..."
for user in hydrahive conduwuit git; do
    if id "${user}" &>/dev/null; then
        userdel -r "${user}" 2>/dev/null || userdel "${user}" 2>/dev/null || true
        info "  User entfernt: ${user}"
    fi
done
success "System-User entfernt"

# ── Abschluss ─────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     HydraHive vollständig entfernt   ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════╝${NC}"
echo ""
warn "Manuelle Schritte falls gewünscht:"
warn "  sudo apt remove postgresql postgresql-contrib redis-server"
warn "  sudo apt autoremove"
echo ""
