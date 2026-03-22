#!/usr/bin/env bash
# OctopOS Update-Script — direkt auf der VM ausführen
# Usage: sudo bash /opt/octopos/update.sh
#
# Ablauf:
#   1. Repo klonen: lokales Gitea (primär) → GitHub (Fallback)
#   2. Core rsync → /opt/octopos/core/
#   3. pip install -e . im venv
#   4. Console npm ci + build
#   5. dist/ → /opt/octopos/console/
#   6. octopos-core neustarten

set -euo pipefail

OCTOPOS_DIR="/opt/octopos"
VENV="${OCTOPOS_DIR}/venv"
GITHUB_REPO="https://github.com/tilleulenspiegel/octopos.git"
GITEA_CONFIG="/etc/octopos/gitea_config.json"
TOKEN_FILE="/etc/octopos/github_token"
TMPDIR_BASE="/tmp/octopos-update-$$"
AMEM_ENABLED="${AMEM_ENABLED:-1}"
AMEM_URL="${AMEM_URL:-http://192.168.1.5:8020/sse}"
AMEM_SEARCH_UI_URL="${AMEM_SEARCH_UI_URL:-http://192.168.1.5:8021}"

# Log-Datei für Webhook-Deploy (damit GET /admin/update/status etwas zum Lesen hat)
UPDATE_LOG="/var/log/octopos-update.log"
UPDATE_STATUS_FILE="/var/run/octopos-update.json"

# Farben
GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
info()    { echo -e "${BLUE}[Update]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

[ "$(id -u)" -eq 0 ] || error "Bitte als root ausführen: sudo bash $0"

# Lokales Gitea als primäre Quelle, GitHub als Fallback
CLONE_URL=""
if [ -f "${GITEA_CONFIG}" ]; then
    GITEA_URL=$(python3 -c "import json; d=json.load(open('${GITEA_CONFIG}')); print(d.get('url',''))" 2>/dev/null || echo "")
    GITEA_TOKEN=$(python3 -c "import json; d=json.load(open('${GITEA_CONFIG}')); print(d.get('token',''))" 2>/dev/null || echo "")
    GITEA_ORG=$(python3 -c "import json; d=json.load(open('${GITEA_CONFIG}')); print(d.get('org','octopos'))" 2>/dev/null || echo "octopos")
    if [ -n "${GITEA_URL}" ] && [ -n "${GITEA_TOKEN}" ]; then
        # Interne URL umbauen: http://127.0.0.1:3001 → mit Token
        CLONE_URL="${GITEA_URL}/octopos/octopos.git"
        CLONE_URL="${CLONE_URL/http:\/\//http:\/\/${GITEA_ORG}:${GITEA_TOKEN}@}"
        info "Klone von lokalem Gitea: ${GITEA_URL}/octopos/octopos"
    fi
fi

if [ -z "${CLONE_URL}" ]; then
    CLONE_URL="${GITHUB_REPO}"
    if [ -f "${TOKEN_FILE}" ]; then
        GH_TOKEN=$(cat "${TOKEN_FILE}" | tr -d '[:space:]')
        CLONE_URL="https://${GH_TOKEN}@github.com/tilleulenspiegel/octopos.git"
    fi
    info "Fallback: klone von GitHub"
fi

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

mkdir -p "$(dirname "${UPDATE_LOG}")"
touch "${UPDATE_LOG}"
exec >> "${UPDATE_LOG}" 2>&1

echo ""
echo "=== Self-Update $(date -Iseconds) ==="

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "   OctopOS Update"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Status-Datei schreiben (für GET /admin/update/status)
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

# --- 7. QMD re-indexieren ---
if command -v qmd &>/dev/null; then
    info "QMD: re-indexiere Memory..."
    sudo -u octopos bash -c "HOME=/home/octopos qmd update -q 2>/dev/null && qmd embed -q 2>/dev/null" || true
    success "QMD aktualisiert"
fi

# --- 8. Gitea sicherstellen ---
if systemctl is-enabled --quiet gitea 2>/dev/null; then
    if systemctl is-active --quiet gitea; then
        info "Gitea läuft bereits"
    else
        info "Starte Gitea..."
        systemctl start gitea && success "Gitea gestartet" || warn "Gitea konnte nicht gestartet werden"
    fi
fi

# --- 9. MCP-Presets sicherstellen ---
python3 - <<PY
import json
from pathlib import Path

enabled = ${AMEM_ENABLED}
amem_url = "${AMEM_URL}"
amem_search_ui_url = "${AMEM_SEARCH_UI_URL}"
path = Path("/etc/octopos/mcp_servers.json")
path.parent.mkdir(parents=True, exist_ok=True)
try:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
except Exception:
    data = {}
servers = list(data.get("servers", []))
if enabled and not any(s.get("id") == "amem" for s in servers):
    servers.append({
        "id": "amem",
        "name": "A-MEM Shared Memory",
        "transport": "sse",
        "url": amem_url,
        "headers": {},
        "meta": {
            "role": "shared_memory",
            "search_ui_url": amem_search_ui_url,
        },
    })
path.write_text(json.dumps({"servers": servers}, indent=2), encoding="utf-8")
PY
chown octopos:octopos /etc/octopos/mcp_servers.json 2>/dev/null || true
chmod 600 /etc/octopos/mcp_servers.json 2>/dev/null || true
success "MCP-Presets aktualisiert"

# --- 10. Versions-Info + Status-Datei ---
COMMIT=$(git -C "${TMPDIR_BASE}" rev-parse --short HEAD 2>/dev/null || echo "unbekannt")
COMMIT_FULL=$(git -C "${TMPDIR_BASE}" rev-parse HEAD 2>/dev/null || echo "")
COMMIT_MSG=$(git -C "${TMPDIR_BASE}" log -1 --pretty=format:'%s' 2>/dev/null || echo "")

echo "{\"status\":\"ok\",\"finished_at\":\"$(date -Iseconds)\",\"commit\":\"${COMMIT}\",\"commit_full\":\"${COMMIT_FULL}\",\"message\":\"${COMMIT_MSG}\"}" \
    > "${UPDATE_STATUS_FILE}" 2>/dev/null || true
# Auch ins Log schreiben
echo "[$(date -Iseconds)] OK commit=${COMMIT} msg=${COMMIT_MSG}" >> "${UPDATE_LOG}" 2>/dev/null || true

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
success "Update abgeschlossen (Commit: ${COMMIT})"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
