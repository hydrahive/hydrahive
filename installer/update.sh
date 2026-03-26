#!/usr/bin/env bash
# HydraHive Update-Script — direkt auf der VM ausführen
# Usage: sudo bash /opt/hydrahive/update.sh
#
# Ablauf:
#   1. Repo klonen: lokales Gitea (primär) → GitHub (Fallback)
#   2. Core rsync → /opt/hydrahive/core/
#   3. pip install -e . im venv
#   4. Console npm ci + build
#   5. dist/ → /opt/hydrahive/console/
#   6. hydrahive-core neustarten
#
# WICHTIG: Alle Schritte in main() gewrapped damit bash den ganzen Script
#           parst bevor er ausgeführt wird — verhindert Self-Copy-Bugs.

set -euo pipefail

# Konstanten außerhalb von main, damit Traps sie sehen
HYDRAHIVE_DIR="/opt/hydrahive"
VENV="${HYDRAHIVE_DIR}/venv"
GITHUB_REPO="https://github.com/hydrahive/hydrahive.git"
GITEA_CONFIG="/etc/hydrahive/gitea_config.json"
TOKEN_FILE="/etc/hydrahive/github_token"
TMPDIR_BASE="/tmp/hydrahive-update-$$"
UPDATE_LOG="/var/log/hydrahive-update.log"
UPDATE_STATUS_FILE="/var/run/hydrahive-update.json"

GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
info()    { echo -e "${BLUE}[Update]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

cleanup() { rm -rf "${TMPDIR_BASE}"; }
trap cleanup EXIT

on_error() {
    local rc=$?
    echo "{\"status\":\"error\",\"finished_at\":\"$(date -Iseconds)\",\"error\":\"update.sh failed (rc=${rc})\"}" \
        > "${UPDATE_STATUS_FILE}" 2>/dev/null || true
    echo "[$(date -Iseconds)] ERROR rc=${rc}" >> "${UPDATE_LOG}" 2>/dev/null || true
    exit "${rc}"
}
trap on_error ERR

main() {
    [ "$(id -u)" -eq 0 ] || error "Bitte als root ausführen: sudo bash $0"

    # Gitea als primäre Quelle, GitHub als Fallback
    local CLONE_URL=""
    if [ -f "${GITEA_CONFIG}" ]; then
        local GITEA_URL GITEA_TOKEN GITEA_ORG GITEA_REPO
        GITEA_URL=$(python3 -c "import json; d=json.load(open('${GITEA_CONFIG}')); print(d.get('url',''))" 2>/dev/null || echo "")
        GITEA_TOKEN=$(python3 -c "import json; d=json.load(open('${GITEA_CONFIG}')); print(d.get('token',''))" 2>/dev/null || echo "")
        GITEA_ORG=$(python3 -c "import json; d=json.load(open('${GITEA_CONFIG}')); print(d.get('org','hydrahive'))" 2>/dev/null || echo "hydrahive")
        GITEA_REPO=$(python3 -c "import json; d=json.load(open('${GITEA_CONFIG}')); print(d.get('repo', d.get('org','hydrahive')))" 2>/dev/null || echo "${GITEA_ORG}")
        if [ -n "${GITEA_URL}" ] && [ -n "${GITEA_TOKEN}" ]; then
            CLONE_URL="${GITEA_URL}/${GITEA_ORG}/${GITEA_REPO}.git"
            CLONE_URL="${CLONE_URL/http:\/\//http:\/\/${GITEA_ORG}:${GITEA_TOKEN}@}"
            info "Klone von lokalem Gitea: ${GITEA_URL}/${GITEA_ORG}/${GITEA_REPO}"
        fi
    fi
    if [ -z "${CLONE_URL}" ]; then
        CLONE_URL="${GITHUB_REPO}"
        if [ -f "${TOKEN_FILE}" ]; then
            local GH_TOKEN
            GH_TOKEN=$(tr -d '[:space:]' < "${TOKEN_FILE}")
            CLONE_URL="https://${GH_TOKEN}@github.com/hydrahive/hydrahive.git"
        fi
        info "Fallback: klone von GitHub"
    fi

    mkdir -p "$(dirname "${UPDATE_LOG}")"
    touch "${UPDATE_LOG}"
    exec >> "${UPDATE_LOG}" 2>&1

    echo ""
    echo "=== Self-Update $(date -Iseconds) ==="
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "   HydraHive Update"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    echo "{\"status\":\"running\",\"started_at\":\"$(date -Iseconds)\",\"commit\":\"\"}" \
        > "${UPDATE_STATUS_FILE}" 2>/dev/null || true

    # --- 1. Repo klonen ---
    info "Klone aktuellen Stand..."
    git clone --depth 1 --quiet "${CLONE_URL}" "${TMPDIR_BASE}" \
        || error "git clone fehlgeschlagen"
    success "Repo geklont"

    # --- 2. Core aktualisieren ---
    info "Aktualisiere Core..."
    rsync -a --delete \
        --exclude='__pycache__' --exclude='*.pyc' \
        "${TMPDIR_BASE}/core/" "${HYDRAHIVE_DIR}/core/"
    success "Core-Dateien aktualisiert"

    # --- 3. Python-Dependencies ---
    info "Installiere Python-Dependencies..."
    "${VENV}/bin/pip" install -e "${HYDRAHIVE_DIR}/core/" -q \
        || error "pip install fehlgeschlagen"
    success "Python-Dependencies aktualisiert"

    # --- 4. Console bauen ---
    info "Baue Console..."
    local CONSOLE_SRC="${TMPDIR_BASE}/console"
    cd "${CONSOLE_SRC}"
    npm ci 2>&1 | grep -v "^npm warn" || error "npm ci fehlgeschlagen"
    npm run build 2>&1 | tail -5 || error "npm run build fehlgeschlagen"
    success "Console gebaut"

    # --- 5. Console deployen ---
    info "Deploye Console..."
    mkdir -p "${HYDRAHIVE_DIR}/console"
    rsync -a --delete "${CONSOLE_SRC}/dist/" "${HYDRAHIVE_DIR}/console/"
    chown -R www-data:www-data "${HYDRAHIVE_DIR}/console/"
    success "Console deployed"

    # --- 6. Service neustarten ---
    info "Starte hydrahive-core neu..."
    systemctl daemon-reload
    systemctl restart hydrahive-core
    # Aktiv warten bis active oder max 30s
    for i in $(seq 1 30); do sleep 1; systemctl is-active --quiet hydrahive-core && break; done

    if systemctl is-active --quiet hydrahive-core; then
        success "hydrahive-core läuft"
    else
        error "hydrahive-core konnte nicht starten — prüfe: journalctl -u hydrahive-core -n 30"
    fi

    # --- 7. QMD re-indexieren (optional) ---
    if command -v qmd &>/dev/null; then
        info "QMD: re-indexiere Memory..."
        sudo -u hydrahive bash -c "HOME=/home/hydrahive qmd update -q 2>/dev/null && qmd embed -q 2>/dev/null" || true
        success "QMD aktualisiert"
    fi

    # --- 8. Gitea sicherstellen ---
    if systemctl is-enabled --quiet gitea 2>/dev/null; then
        if systemctl is-active --quiet gitea; then
            info "Gitea läuft bereits"
        else
            info "Starte Gitea..."
            systemctl start gitea && success "Gitea gestartet" \
                || warn "Gitea konnte nicht gestartet werden"
        fi
    fi

    # --- 9. A-MEM aktualisieren (optional — Fehler nicht fatal) ---
    if [ -f "${TMPDIR_BASE}/installer/amem/install_amem.sh" ]; then
        info "Aktualisiere A-MEM..."
        bash "${TMPDIR_BASE}/installer/amem/install_amem.sh" \
            && success "A-MEM aktualisiert" \
            || warn "A-MEM Update fehlgeschlagen — wird übersprungen"
    fi

    # --- 10. update.sh + Service-Datei selbst aktualisieren ---
    # Sicher: main() ist vollständig in Memory — Self-Copy verwirrt bash nicht mehr.
    if [ -f "${TMPDIR_BASE}/installer/update.sh" ]; then
        cp "${TMPDIR_BASE}/installer/update.sh" "${HYDRAHIVE_DIR}/update.sh"
        chmod +x "${HYDRAHIVE_DIR}/update.sh"
        info "update.sh aktualisiert"
    fi
    if [ -f "${TMPDIR_BASE}/installer/hydrahive-selfupdate.service" ]; then
        cp "${TMPDIR_BASE}/installer/hydrahive-selfupdate.service" /etc/systemd/system/
        systemctl daemon-reload
        info "hydrahive-selfupdate.service aktualisiert"
    fi

    # --- 11. Versions-Info + Status-Datei ---
    local COMMIT COMMIT_FULL COMMIT_MSG
    COMMIT=$(git -C "${TMPDIR_BASE}" rev-parse --short HEAD 2>/dev/null || echo "unbekannt")
    COMMIT_FULL=$(git -C "${TMPDIR_BASE}" rev-parse HEAD 2>/dev/null || echo "")
    COMMIT_MSG=$(git -C "${TMPDIR_BASE}" log -1 --pretty=format:'%s' 2>/dev/null || echo "")

    echo "{\"status\":\"ok\",\"finished_at\":\"$(date -Iseconds)\",\"commit\":\"${COMMIT}\",\"commit_full\":\"${COMMIT_FULL}\",\"message\":\"${COMMIT_MSG}\"}" \
        > "${UPDATE_STATUS_FILE}" 2>/dev/null || true
    echo "[$(date -Iseconds)] OK commit=${COMMIT} msg=${COMMIT_MSG}" >> "${UPDATE_LOG}" 2>/dev/null || true

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    success "Update abgeschlossen (Commit: ${COMMIT})"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
}

main "$@"
