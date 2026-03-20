#!/usr/bin/env bash
# OctopOS Update-Script — direkt auf der VM ausführen
# Usage: sudo bash /opt/octopos/update.sh
#
# Ablauf:
#   1. Repo von GitHub klonen (shallow, temp-Verzeichnis)
#   2. Core rsync → /opt/octopos/core/
#   3. pip install -e . im venv
#   4. Console npm ci + build
#   5. dist/ → /opt/octopos/console/
#   6. octopos-core neustarten

set -euo pipefail

OCTOPOS_DIR="/opt/octopos"
VENV="${OCTOPOS_DIR}/venv"
GITHUB_REPO="https://github.com/tilleulenspiegel/octopos.git"
TOKEN_FILE="/etc/octopos/github_token"
TMPDIR_BASE="/tmp/octopos-update-$$"

# Farben
GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
info()    { echo -e "${BLUE}[Update]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

[ "$(id -u)" -eq 0 ] || error "Bitte als root ausführen: sudo bash $0"

# Token für private Repos (optional)
CLONE_URL="${GITHUB_REPO}"
if [ -f "${TOKEN_FILE}" ]; then
    TOKEN=$(cat "${TOKEN_FILE}" | tr -d '[:space:]')
    CLONE_URL="https://${TOKEN}@github.com/tilleulenspiegel/octopos.git"
    info "GitHub-Token gefunden — klone privates Repo"
fi

cleanup() { rm -rf "${TMPDIR_BASE}"; }
trap cleanup EXIT

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "   OctopOS Update"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# --- 1. Repo klonen ---
info "Klone aktuellen Stand von GitHub..."
git clone --depth 1 --quiet "${CLONE_URL}" "${TMPDIR_BASE}" \
    || error "git clone fehlgeschlagen — Token in ${TOKEN_FILE} hinterlegt?"
success "Repo geklont"

# --- 2. Core aktualisieren ---
info "Aktualisiere Core..."
rsync -a --delete \
    --exclude='__pycache__' --exclude='*.pyc' \
    "${TMPDIR_BASE}/core/" "${OCTOPOS_DIR}/core/"
success "Core-Dateien aktualisiert"

# --- 3. Python-Dependencies ---
info "Installiere Python-Dependencies..."
"${VENV}/bin/pip" install -e "${OCTOPOS_DIR}/core/" -q \
    || error "pip install fehlgeschlagen"
success "Python-Dependencies aktualisiert"

# --- 4. Console bauen ---
info "Baue Console..."
CONSOLE_SRC="${TMPDIR_BASE}/console"
cd "${CONSOLE_SRC}"
npm ci 2>&1 | grep -v "^npm warn" || error "npm ci fehlgeschlagen"
npm run build 2>&1 | tail -5 || error "npm run build fehlgeschlagen"
success "Console gebaut"

# --- 5. Console deployen ---
info "Deploye Console..."
mkdir -p "${OCTOPOS_DIR}/console"
rsync -a --delete "${CONSOLE_SRC}/dist/" "${OCTOPOS_DIR}/console/"
chown -R www-data:www-data "${OCTOPOS_DIR}/console/"
success "Console deployed"

# --- 6. Service neustarten ---
info "Starte octopos-core neu..."
systemctl daemon-reload
systemctl restart octopos-core
sleep 3

if systemctl is-active --quiet octopos-core; then
    success "octopos-core läuft"
else
    error "octopos-core konnte nicht starten — prüfe: journalctl -u octopos-core -n 30"
fi

# --- Versions-Info ---
COMMIT=$(git -C "${TMPDIR_BASE}" rev-parse --short HEAD 2>/dev/null || echo "unbekannt")
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
success "Update abgeschlossen (Commit: ${COMMIT})"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
