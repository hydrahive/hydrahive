#!/usr/bin/env bash
# OctopOS Installer — Hauptskript
# Usage: curl -sSL https://get.octopos.io | bash
set -euo pipefail

OCTOPOS_VERSION="0.1.0"
export OCTOPOS_DIR="/opt/octopos"
MODULES_DIR="$(dirname "${BASH_SOURCE[0]}")/modules"

# Farben — zentral definiert, alle Module nutzen diese
export RED="\033[0;31m"
export GREEN="\033[0;32m"
export YELLOW="\033[1;33m"
export BLUE="\033[0;34m"
export NC="\033[0m"

info()    { echo -e "${BLUE}[OctopOS]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

export -f info success warn error

echo ""
echo -e "${BLUE}╔══════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     OctopOS Installer v${OCTOPOS_VERSION}          ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════╝${NC}"
echo ""

# Root-Check
if [ "$EUID" -ne 0 ]; then
  error "Bitte als root ausfuehren: sudo bash install.sh"
fi

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

# Update-Script nach /opt/octopos/ kopieren
cp "$(dirname "${BASH_SOURCE[0]}")/update.sh" "${OCTOPOS_DIR}/update.sh"
chmod +x "${OCTOPOS_DIR}/update.sh"
success "Update-Script: sudo bash ${OCTOPOS_DIR}/update.sh"

# Konfig-Dateien vorbereiten (octopos-core braucht Schreibrechte)
for _f in jwt_secret llm_env llm_config.json; do
    _path="/etc/octopos/${_f}"
    if [ ! -f "${_path}" ]; then
        touch "${_path}"
        chown octopos:octopos "${_path}"
        chmod 600 "${_path}"
    fi
done

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
info "Credentials:   /etc/octopos/admin_credentials"
info "Agenten-Dir:   /agents"
