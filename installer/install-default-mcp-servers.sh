#!/usr/bin/env bash
# install-default-mcp-servers.sh — Standard-MCP-Server installieren + registrieren
#
# Wird von update.sh aufgerufen. Idempotent: überspringt Pakete die schon
# installiert sind, registriert MCP-Server nur wenn ID noch nicht in
# /etc/hydrahive/mcp_servers.json steht.
#
# Servers die installiert werden:
#   npm-basiert (global):
#     - @modelcontextprotocol/server-github         (26 Tools)
#     - @modelcontextprotocol/server-filesystem     (14 Tools, sandboxed /tmp/hydrahive-workspace)
#     - @modelcontextprotocol/server-sequential-thinking (1 Tool)
#   Python-basiert (im Core-venv):
#     - mcp-server-fetch   (1 Tool)
#     - mcp-server-git     (12 Tools)
#     - mcp-server-time    (2 Tools)
#     - mcp-server-sqlite  (6 Tools)
#
# GitHub braucht einen Token in /etc/hydrahive/github_token. Fehlt die
# Datei → GitHub-MCP wird NICHT registriert (aber andere schon).

set -euo pipefail

HYDRAHIVE_DIR="${HYDRAHIVE_DIR:-/opt/hydrahive}"
VENV="${HYDRAHIVE_DIR}/venv"
ETC_DIR="/etc/hydrahive"
MCP_CONFIG="${ETC_DIR}/mcp_servers.json"
WORKSPACE_DIR="/tmp/hydrahive-workspace"
GITHUB_TOKEN_FILE="${ETC_DIR}/github_token"

GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; NC="\033[0m"
info()    { echo -e "${BLUE}[MCP-Setup]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }

# ═════════════════════════════════════════════════════════════════════════
# 1) Workspace für Filesystem-MCP sicherstellen
# ═════════════════════════════════════════════════════════════════════════
if [ ! -d "${WORKSPACE_DIR}" ]; then
    mkdir -p "${WORKSPACE_DIR}"
    chown hydrahive:hydrahive "${WORKSPACE_DIR}" 2>/dev/null || true
    info "Workspace ${WORKSPACE_DIR} erstellt"
fi

# ═════════════════════════════════════════════════════════════════════════
# 2) npm-Pakete installieren (idempotent via npm ls)
# ═════════════════════════════════════════════════════════════════════════
if ! command -v npm >/dev/null 2>&1; then
    warn "npm nicht gefunden — npm-basierte MCP-Server werden übersprungen"
else
    NPM_PKGS=(
        "@modelcontextprotocol/server-github"
        "@modelcontextprotocol/server-filesystem"
        "@modelcontextprotocol/server-sequential-thinking"
    )
    for pkg in "${NPM_PKGS[@]}"; do
        if npm ls -g "${pkg}" --depth=0 >/dev/null 2>&1; then
            info "npm ${pkg}: bereits installiert"
        else
            info "npm install -g ${pkg}"
            npm install -g "${pkg}" >/dev/null 2>&1 && \
                success "${pkg} installiert" || \
                warn "${pkg} Installation fehlgeschlagen"
        fi
    done
fi

# ═════════════════════════════════════════════════════════════════════════
# 3) Python-Pakete ins Core-venv installieren (idempotent)
# ═════════════════════════════════════════════════════════════════════════
if [ ! -x "${VENV}/bin/pip" ]; then
    warn "Core-venv nicht gefunden unter ${VENV} — Python-MCP-Server übersprungen"
else
    PY_PKGS=("mcp-server-fetch" "mcp-server-git" "mcp-server-time" "mcp-server-sqlite")
    for pkg in "${PY_PKGS[@]}"; do
        if "${VENV}/bin/pip" show "${pkg}" >/dev/null 2>&1; then
            info "pip ${pkg}: bereits installiert"
        else
            info "pip install ${pkg}"
            "${VENV}/bin/pip" install "${pkg}" >/dev/null 2>&1 && \
                success "${pkg} installiert" || \
                warn "${pkg} Installation fehlgeschlagen"
        fi
    done
    # mcp-server-fetch downgraded httpx auf <0.28; litellm braucht aber 0.28.1.
    # Wir pinnen httpx zurück — fetch läuft trotzdem (getestet).
    if "${VENV}/bin/pip" show httpx 2>/dev/null | grep -q "Version: 0.27"; then
        info "httpx zurück auf 0.28.1 (litellm-Konflikt lösen)"
        "${VENV}/bin/pip" install "httpx==0.28.1" >/dev/null 2>&1 || true
    fi
fi

# ═════════════════════════════════════════════════════════════════════════
# 4) MCP-Server in mcp_servers.json registrieren (idempotent)
# ═════════════════════════════════════════════════════════════════════════
if [ ! -f "${MCP_CONFIG}" ]; then
    echo '{"servers": []}' > "${MCP_CONFIG}"
    chmod 640 "${MCP_CONFIG}"
    chown root:hydrahive "${MCP_CONFIG}" 2>/dev/null || true
fi

# GitHub-Token lesen (falls vorhanden)
GITHUB_TOKEN=""
if [ -f "${GITHUB_TOKEN_FILE}" ]; then
    GITHUB_TOKEN="$(cat "${GITHUB_TOKEN_FILE}" | tr -d '\n')"
fi

python3 <<PYEOF
import json, os
p = "${MCP_CONFIG}"
d = json.load(open(p))
existing = {s.get("id") for s in d.get("servers", [])}

candidates = [
    {
        "id": "filesystem", "name": "Filesystem MCP", "transport": "stdio",
        "command": "mcp-server-filesystem", "args": ["${WORKSPACE_DIR}"], "env": {},
    },
    {
        "id": "sequential-thinking", "name": "Sequential Thinking", "transport": "stdio",
        "command": "mcp-server-sequential-thinking", "args": [], "env": {},
    },
    {
        "id": "fetch", "name": "HTTP Fetch", "transport": "stdio",
        "command": "${VENV}/bin/mcp-server-fetch", "args": [], "env": {},
    },
    {
        "id": "git", "name": "Git MCP", "transport": "stdio",
        "command": "${VENV}/bin/mcp-server-git", "args": [], "env": {},
    },
    {
        "id": "time", "name": "Time", "transport": "stdio",
        "command": "${VENV}/bin/mcp-server-time", "args": [], "env": {},
    },
    {
        "id": "sqlite", "name": "SQLite", "transport": "stdio",
        "command": "${VENV}/bin/mcp-server-sqlite",
        "args": ["--db-path", "${WORKSPACE_DIR}/mcp.sqlite"], "env": {},
    },
]

token = "${GITHUB_TOKEN}"
if token:
    candidates.insert(0, {
        "id": "github", "name": "GitHub MCP", "transport": "stdio",
        "command": "mcp-server-github", "args": [],
        "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": token},
    })

added = []
for s in candidates:
    if s["id"] not in existing:
        # Nur registrieren wenn Binary/Command auffindbar — sonst kommt Error pro Request
        cmd = s["command"]
        if os.path.isabs(cmd):
            if not os.path.isfile(cmd):
                continue
        # Non-absolute: wir nehmen an im PATH (wurde in Schritt 2/3 installiert)
        d["servers"].append(s)
        added.append(s["id"])

open(p, "w").write(json.dumps(d, indent=2))
print("added:", added or "(nichts neues)")
PYEOF

success "MCP-Default-Setup abgeschlossen"
