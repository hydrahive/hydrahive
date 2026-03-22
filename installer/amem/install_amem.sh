#!/usr/bin/env bash
set -euo pipefail

OCTOPOS_USER="${OCTOPOS_USER:-octopos}"
OCTOPOS_GROUP="${OCTOPOS_GROUP:-octopos}"
AMEM_DIR="${AMEM_DIR:-/opt/amem}"
AMEM_SEARCH_DIR="${AMEM_SEARCH_DIR:-/opt/amem-search}"
AMEM_REPO_URL="${AMEM_REPO_URL:-https://github.com/agiresearch/A-mem.git}"
AMEM_ENV_FILE="${AMEM_ENV_FILE:-/etc/octopos/amem.env}"
MCP_CONFIG_FILE="${MCP_CONFIG_FILE:-/etc/octopos/mcp_servers.json}"
AMEM_MCP_URL="${AMEM_MCP_URL:-http://127.0.0.1:8020/sse}"
AMEM_SEARCH_UI_URL="${AMEM_SEARCH_UI_URL:-http://127.0.0.1:8021}"

info()    { echo "[A-MEM] $1"; }
success() { echo "[A-MEM] OK: $1"; }
warn()    { echo "[A-MEM] WARN: $1"; }

[ "$(id -u)" -eq 0 ] || { echo "Bitte als root ausfuehren"; exit 1; }

mkdir -p /etc/octopos /var/log/octopos /var/lib/octopos/amem
chown -R "${OCTOPOS_USER}:${OCTOPOS_GROUP}" /var/lib/octopos/amem

if [ -d "${AMEM_DIR}/.git" ]; then
  info "Aktualisiere A-MEM Repo..."
  git -C "${AMEM_DIR}" fetch --depth 1 origin main
  git -C "${AMEM_DIR}" reset --hard origin/main
else
  info "Klonen von A-MEM Repo..."
  rm -rf "${AMEM_DIR}"
  git clone --depth 1 "${AMEM_REPO_URL}" "${AMEM_DIR}"
fi

python3.12 -m venv "${AMEM_DIR}/.venv"
"${AMEM_DIR}/.venv/bin/pip" install -q --upgrade pip setuptools wheel
"${AMEM_DIR}/.venv/bin/pip" install -q -r "${AMEM_DIR}/requirements.txt"
"${AMEM_DIR}/.venv/bin/pip" install -q -e "${AMEM_DIR}"
"${AMEM_DIR}/.venv/bin/pip" install -q mcp flask requests

install -d -m 755 "${AMEM_SEARCH_DIR}"
install -m 755 "$(dirname "$0")/amem_mcp_server.py" "${AMEM_DIR}/amem_mcp_server.py"
install -m 755 "$(dirname "$0")/search_ui.py" "${AMEM_SEARCH_DIR}/search_ui.py"
chown -R "${OCTOPOS_USER}:${OCTOPOS_GROUP}" "${AMEM_DIR}" "${AMEM_SEARCH_DIR}"

if [ ! -f "${AMEM_ENV_FILE}" ]; then
  AMEM_MODEL="$(python3 - <<'PY'
import json
import urllib.request

try:
    with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as resp:
        data = json.load(resp)
    models = [m.get("name") for m in data.get("models", []) if m.get("name")]
    for candidate in ("qwen2.5:7b", "llama3.1:8b", "llama3.2:3b"):
        if candidate in models:
            print(candidate)
            break
    else:
        print(models[0] if models else "qwen2.5:7b")
except Exception:
    print("qwen2.5:7b")
PY
)"
  cat > "${AMEM_ENV_FILE}" <<ENV
OLLAMA_HOST=http://127.0.0.1:11434
AMEM_LLM_BACKEND=ollama
AMEM_LLM_MODEL=${AMEM_MODEL}
AMEM_EMBEDDING_MODEL=all-MiniLM-L6-v2
AMEM_HOST=127.0.0.1
AMEM_PORT=8020
AMEM_CHROMADB_DIR=/var/lib/octopos/amem/chromadb_data
AMEM_LOG_FILE=/var/log/octopos/amem_mcp.log
ENV
fi
chown "${OCTOPOS_USER}:${OCTOPOS_GROUP}" "${AMEM_ENV_FILE}"
chmod 600 "${AMEM_ENV_FILE}"

install -m 644 "$(dirname "$0")/octopos-amem.service" /etc/systemd/system/octopos-amem.service
install -m 644 "$(dirname "$0")/octopos-amem-search-ui.service" /etc/systemd/system/octopos-amem-search-ui.service
systemctl daemon-reload
systemctl enable --now octopos-amem.service
systemctl enable --now octopos-amem-search-ui.service

python3 - <<PY
import json
from pathlib import Path
path = Path('${MCP_CONFIG_FILE}')
path.parent.mkdir(parents=True, exist_ok=True)
try:
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
except Exception:
    data = {}
servers = [s for s in data.get('servers', []) if s.get('id') != 'amem']
servers.append({
    'id': 'amem',
    'name': 'A-MEM Shared Memory',
    'transport': 'sse',
    'url': '${AMEM_MCP_URL}',
    'headers': {},
    'meta': {'role': 'shared_memory', 'search_ui_url': '${AMEM_SEARCH_UI_URL}'},
})
path.write_text(json.dumps({'servers': servers}, indent=2), encoding='utf-8')
PY
chown "${OCTOPOS_USER}:${OCTOPOS_GROUP}" "${MCP_CONFIG_FILE}"
chmod 600 "${MCP_CONFIG_FILE}"

success "A-MEM lokal installiert"
