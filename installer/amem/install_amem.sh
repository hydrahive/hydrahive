#!/usr/bin/env bash
set -euo pipefail

HYDRAHIVE_USER="${HYDRAHIVE_USER:-hydrahive}"
HYDRAHIVE_GROUP="${HYDRAHIVE_GROUP:-hydrahive}"
AMEM_DIR="${AMEM_DIR:-/opt/amem}"
AMEM_SEARCH_DIR="${AMEM_SEARCH_DIR:-/opt/amem-search}"
AMEM_REPO_URL="${AMEM_REPO_URL:-https://github.com/agiresearch/A-mem.git}"
# Commit-Pin für reproduzierbare Builds (Issue #159).
# Geprüfter Stand: 2026-03-23, A-MEM HEAD zum Zeitpunkt der HydraHive-Zertifizierung.
# Um auf einen neueren Stand zu aktualisieren: AMEM_COMMIT=<neuer-sha> prüfen und hier setzen.
AMEM_COMMIT="${AMEM_COMMIT:-ceffb860f0712bbae97b184d440df62bc910ca8d}"
AMEM_ENV_FILE="${AMEM_ENV_FILE:-/etc/hydrahive/amem.env}"
MCP_CONFIG_FILE="${MCP_CONFIG_FILE:-/etc/hydrahive/mcp_servers.json}"
AMEM_MCP_URL="${AMEM_MCP_URL:-http://127.0.0.1:8020/sse}"
AMEM_BIND_HOST="${AMEM_BIND_HOST:-0.0.0.0}"
AMEM_PUBLIC_HOST="${AMEM_PUBLIC_HOST:-$(hostname -I 2>/dev/null | awk '{print $1}' || echo 127.0.0.1)}"
AMEM_SEARCH_UI_URL="${AMEM_SEARCH_UI_URL:-http://${AMEM_PUBLIC_HOST}:8021}"

info()    { echo "[A-MEM] $1"; }
success() { echo "[A-MEM] OK: $1"; }
warn()    { echo "[A-MEM] WARN: $1"; }

[ "$(id -u)" -eq 0 ] || { echo "Bitte als root ausfuehren"; exit 1; }

mkdir -p /etc/hydrahive /var/log/hydrahive /var/lib/hydrahive/amem
chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_GROUP}" /var/lib/hydrahive/amem

git config --global --add safe.directory "${AMEM_DIR}" >/dev/null 2>&1 || true

if [ -d "${AMEM_DIR}/.git" ]; then
  info "Aktualisiere A-MEM Repo..."
  git -C "${AMEM_DIR}" fetch --depth 1 origin main
  git -C "${AMEM_DIR}" reset --hard origin/main
else
  info "Klonen von A-MEM Repo..."
  rm -rf "${AMEM_DIR}"
  git clone --depth 1 "${AMEM_REPO_URL}" "${AMEM_DIR}"
fi

# Optionaler Commit-Pin: wenn AMEM_COMMIT gesetzt, auf den exakten Commit wechseln
if [ -n "${AMEM_COMMIT}" ]; then
  info "Pinne A-MEM auf Commit ${AMEM_COMMIT}..."
  git -C "${AMEM_DIR}" fetch --depth 1 origin "${AMEM_COMMIT}" 2>/dev/null || \
    git -C "${AMEM_DIR}" fetch origin 2>/dev/null || true
  git -C "${AMEM_DIR}" checkout "${AMEM_COMMIT}"
fi

# Tatsächlichen HEAD-Commit loggen (Supply-Chain-Transparenz)
AMEM_ACTUAL_COMMIT="$(git -C "${AMEM_DIR}" rev-parse HEAD 2>/dev/null || echo unknown)"
info "A-MEM HEAD-Commit: ${AMEM_ACTUAL_COMMIT}"
echo "${AMEM_ACTUAL_COMMIT}" > /var/lib/hydrahive/amem/installed_commit.txt

python3.12 -m venv "${AMEM_DIR}/.venv"
"${AMEM_DIR}/.venv/bin/pip" install -q --upgrade pip setuptools wheel
"${AMEM_DIR}/.venv/bin/pip" install -q -r "${AMEM_DIR}/requirements.txt"
"${AMEM_DIR}/.venv/bin/pip" install -q -e "${AMEM_DIR}"
"${AMEM_DIR}/.venv/bin/pip" install -q mcp flask requests

install -d -m 755 "${AMEM_SEARCH_DIR}"
install -m 755 "$(dirname "$0")/amem_mcp_server.py" "${AMEM_DIR}/amem_mcp_server.py"
install -m 755 "$(dirname "$0")/search_ui.py" "${AMEM_SEARCH_DIR}/search_ui.py"
chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_GROUP}" "${AMEM_DIR}" "${AMEM_SEARCH_DIR}"

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

if [ -f "${AMEM_ENV_FILE}" ]; then
  python3 - <<PY
from pathlib import Path

path = Path("${AMEM_ENV_FILE}")
entries = {}
for line in path.read_text(encoding="utf-8").splitlines():
    if not line or line.lstrip().startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    entries[key.strip()] = value.strip()

entries["OLLAMA_HOST"] = entries.get("OLLAMA_HOST", "http://127.0.0.1:11434")
entries["AMEM_LLM_BACKEND"] = entries.get("AMEM_LLM_BACKEND", "ollama")
entries["AMEM_LLM_MODEL"] = "${AMEM_MODEL}"
entries["AMEM_EMBEDDING_MODEL"] = entries.get("AMEM_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
entries["AMEM_HOST"] = "${AMEM_BIND_HOST}"
entries["AMEM_PORT"] = entries.get("AMEM_PORT", "8020")
entries["AMEM_CHROMADB_DIR"] = entries.get("AMEM_CHROMADB_DIR", "/var/lib/hydrahive/amem/chromadb_data")
entries["AMEM_LOG_FILE"] = entries.get("AMEM_LOG_FILE", "/var/log/hydrahive/amem_mcp.log")

ordered = [
    "OLLAMA_HOST",
    "AMEM_LLM_BACKEND",
    "AMEM_LLM_MODEL",
    "AMEM_EMBEDDING_MODEL",
    "AMEM_HOST",
    "AMEM_PORT",
    "AMEM_CHROMADB_DIR",
    "AMEM_LOG_FILE",
]
path.write_text("\n".join(f"{key}={entries[key]}" for key in ordered) + "\n", encoding="utf-8")
PY
else
  cat > "${AMEM_ENV_FILE}" <<ENV
OLLAMA_HOST=http://127.0.0.1:11434
AMEM_LLM_BACKEND=ollama
AMEM_LLM_MODEL=${AMEM_MODEL}
AMEM_EMBEDDING_MODEL=all-MiniLM-L6-v2
AMEM_HOST=${AMEM_BIND_HOST}
AMEM_PORT=8020
AMEM_CHROMADB_DIR=/var/lib/hydrahive/amem/chromadb_data
AMEM_LOG_FILE=/var/log/hydrahive/amem_mcp.log
ENV
fi
chown "${HYDRAHIVE_USER}:${HYDRAHIVE_GROUP}" "${AMEM_ENV_FILE}"
chmod 600 "${AMEM_ENV_FILE}"

install -m 644 "$(dirname "$0")/hydrahive-amem.service" /etc/systemd/system/hydrahive-amem.service
install -m 644 "$(dirname "$0")/hydrahive-amem-search-ui.service" /etc/systemd/system/hydrahive-amem-search-ui.service
systemctl daemon-reload
systemctl enable --now hydrahive-amem.service
systemctl enable --now hydrahive-amem-search-ui.service

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
chown "${HYDRAHIVE_USER}:${HYDRAHIVE_GROUP}" "${MCP_CONFIG_FILE}"
chmod 600 "${MCP_CONFIG_FILE}"

success "A-MEM lokal installiert"
