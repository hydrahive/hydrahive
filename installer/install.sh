#!/usr/bin/env bash
# HydraHive Installer — Hauptskript
# Usage: curl -sSL https://get.hydrahive.org | bash
set -euo pipefail

HYDRAHIVE_VERSION="0.1.0"
export HYDRAHIVE_DIR="/opt/hydrahive"
MODULES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/modules"

# Farben — zentral definiert, alle Module nutzen diese
export RED="\033[0;31m"
export GREEN="\033[0;32m"
export YELLOW="\033[1;33m"
export BLUE="\033[0;34m"
export NC="\033[0m"

info()    { echo -e "${BLUE}[HydraHive]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

export -f info success warn error

echo ""
echo -e "${BLUE}╔══════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     HydraHive Installer v${HYDRAHIVE_VERSION}          ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════╝${NC}"
echo ""

# Root-Check
if [ "$EUID" -ne 0 ]; then
  error "Bitte als root ausfuehren: sudo bash install.sh"
fi

# --- Upgrade-Pfad: /etc/hydrahive → /etc/hydrahive ---
if [ -d "/etc/hydrahive" ] && [ ! -d "/etc/hydrahive" ]; then
  warn "Alte Installation erkannt: /etc/hydrahive gefunden — migriere nach /etc/hydrahive ..."
  mv /etc/hydrahive /etc/hydrahive
  ln -s /etc/hydrahive /etc/hydrahive
  success "Migration abgeschlossen (/etc/hydrahive → /etc/hydrahive, Symlink gesetzt)"
elif [ -d "/etc/hydrahive" ] && [ -d "/etc/hydrahive" ]; then
  # Beide existieren — Symlink setzen falls noch nicht vorhanden
  if [ ! -L "/etc/hydrahive" ]; then
    ln -s /etc/hydrahive /etc/hydrahive 2>/dev/null || true
  fi
fi

# --- Dangling nginx-Symlinks bereinigen (von fehlgeschlagenen Vorinstalls) ---
for _sl in /etc/nginx/sites-enabled/*; do
    [ -L "$_sl" ] && [ ! -e "$_sl" ] && rm -f "$_sl" && warn "Veralteten nginx-Symlink entfernt: $_sl"
done

# --- Phase 1: Fundament ---
echo -e "${BLUE}--- Phase 1: Fundament ---${NC}"
source "${MODULES_DIR}/01_os_check.sh"
source "${MODULES_DIR}/02_gpu_detect.sh"
source "${MODULES_DIR}/03_dependencies.sh"
source "${MODULES_DIR}/04_tuwunel.sh"
source "${MODULES_DIR}/05_admin_account.sh"

echo ""
echo -e "${BLUE}--- Phase 2: Core Runtime ---${NC}"
source "${MODULES_DIR}/06_core_service.sh"

echo ""
echo -e "${BLUE}--- Phase 3: Web-Console ---${NC}"
source "${MODULES_DIR}/07_console.sh"
source "${MODULES_DIR}/08_ollama.sh"
source "${MODULES_DIR}/09_https.sh"

echo ""
echo -e "${BLUE}--- Phase 4: Git-Integration ---${NC}"
source "${MODULES_DIR}/10_gitea.sh"

echo ""
echo -e "${BLUE}--- Phase 5: AgentLink Hub ---${NC}"
source "${MODULES_DIR}/11_agentlink.sh"

echo ""
echo -e "${BLUE}--- Phase 6: VPN ---${NC}"
source "${MODULES_DIR}/12_vpn.sh"

# --- Modul 13: WhatsApp Bridge (optional) ---
echo ""
if [ -t 0 ]; then
    read -rp "WhatsApp Bridge installieren? (y/N) " INSTALL_WHATSAPP
else
    INSTALL_WHATSAPP="n"
fi
if [[ "${INSTALL_WHATSAPP,,}" == "y" ]]; then
    source "${MODULES_DIR}/13_whatsapp_bridge.sh"
fi

echo ""
echo -e "${BLUE}--- Phase 7: Code Editor ---${NC}"
source "${MODULES_DIR}/15_codeserver.sh"

# Update-Script nach /opt/hydrahive/ kopieren
cp "$(dirname "${BASH_SOURCE[0]}")/update.sh" "${HYDRAHIVE_DIR}/update.sh"
chmod +x "${HYDRAHIVE_DIR}/update.sh"
success "Update-Script: sudo bash ${HYDRAHIVE_DIR}/update.sh"

# Installations-Commit in Update-Status schreiben
_install_commit="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
_install_date="$(date -u +"%Y-%m-%dT%H:%M:%S+00:00")"
echo "{\"status\":\"ok\",\"commit\":\"${_install_commit}\",\"finished_at\":\"${_install_date}\",\"source\":\"installer\"}" \
    > /var/run/hydrahive-update.json
chown hydrahive:hydrahive /var/run/hydrahive-update.json 2>/dev/null || true

install -m 755 "$(dirname "${BASH_SOURCE[0]}")/apply-network-profile.sh" "${HYDRAHIVE_DIR}/apply-network-profile.sh"
install -m 440 "$(dirname "${BASH_SOURCE[0]}")/hydrahive-network-profile.sudoers" /etc/sudoers.d/hydrahive-network-profile
success "Network-Profile-Skript installiert"

# Self-Update Service + sudo-Regel installieren
install -m 644 "$(dirname "${BASH_SOURCE[0]}")/hydrahive-selfupdate.service" /etc/systemd/system/hydrahive-selfupdate.service
install -m 440 "$(dirname "${BASH_SOURCE[0]}")/hydrahive-update.sudoers" /etc/sudoers.d/hydrahive-update
install -m 440 "$(dirname "${BASH_SOURCE[0]}")/hydrahive-provisioner.sudoers" /etc/sudoers.d/hydrahive-provisioner
install -m 440 "$(dirname "${BASH_SOURCE[0]}")/hydrahive-installer.sudoers" /etc/sudoers.d/hydrahive-installer
systemctl daemon-reload
success "Self-Update-Service installiert"

# Konfig-Dateien vorbereiten (hydrahive-core braucht Schreibrechte)
for _f in jwt_secret internal_secret llm_env llm_config.json gitea_config.json; do
    _path="/etc/hydrahive/${_f}"
    if [ ! -f "${_path}" ]; then
        touch "${_path}"
        chown hydrahive:hydrahive "${_path}"
        chmod 600 "${_path}"
    fi
done

install -m 755 "$(dirname "${BASH_SOURCE[0]}")/amem/install_amem.sh" "${HYDRAHIVE_DIR}/install_amem.sh"
"$(dirname "${BASH_SOURCE[0]}")/amem/install_amem.sh"
success "A-MEM installiert"

# www-data (nginx) braucht Lesezugriff auf /projects/ (hydrahive-Gruppe)
if id www-data &>/dev/null; then
    usermod -aG hydrahive www-data
    systemctl reload nginx 2>/dev/null || true
    success "nginx (www-data) zur hydrahive-Gruppe hinzugefügt — /projects/ erreichbar"
fi

echo ""
echo -e "${GREEN}╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     Installation abgeschlossen       ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════╝${NC}"
echo ""
info "Profil:        $PROFILE"
info "Matrix:        http://127.0.0.1:6167"
info "Core API:      http://127.0.0.1:8765"
info "Console:       https://$(hostname -I | awk '{print $1}')  (self-signed Cert, Browser-Warnung ignorieren)"
info "               Für Let's Encrypt: DOMAIN=mein.host.de bash install.sh"
info "Admin-Account: @admin:$(hostname -f 2>/dev/null || hostname)"
info "Login:         admin / ${CONSOLE_PASS}"
info "Credentials:   /etc/hydrahive/admin_credentials"
info "Agenten-Dir:   /agents"
info "AgentLink:     http://127.0.0.1:${AGENTLINK_PORT:-8010}/docs"
if systemctl is-active --quiet gitea 2>/dev/null; then
  SERVER_IP_OUT=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
  info "Gitea:         http://${SERVER_IP_OUT}:3002  (admin / ${GITEA_ADMIN_PASS:-siehe /etc/hydrahive/gitea_config.json})"
fi
if systemctl is-active --quiet hydrahive-codeserver 2>/dev/null; then
  SERVER_IP_OUT=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
  _CS_PASS=$(grep '^codeserver_password=' /etc/hydrahive/admin_credentials 2>/dev/null | cut -d= -f2- || echo "siehe /etc/hydrahive/admin_credentials")
  info "Code Editor:   https://${SERVER_IP_OUT}/code/  (Passwort: ${_CS_PASS})"
fi
