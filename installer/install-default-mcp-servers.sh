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
#   Python-basiert (je eigenes venv unter /opt/hydrahive/mcp-venvs/<id>/):
#     - mcp-server-fetch   (1 Tool)    — isoliert wg. httpx<0.28 Konflikt
#     - mcp-server-git     (12 Tools)
#     - mcp-server-time    (2 Tools)
#     - mcp-server-sqlite  (6 Tools)
#
# Jeder Python-MCP bekommt ein eigenes venv, um Dependency-Konflikte mit
# dem Core-venv (litellm braucht httpx==0.28.1) zu vermeiden. Idempotent:
# venv+Paket schon vorhanden → skip.
#
# GitHub braucht einen Token in /etc/hydrahive/github_token. Fehlt die
# Datei → GitHub-MCP wird NICHT registriert (aber andere schon).

set -euo pipefail

HYDRAHIVE_DIR="${HYDRAHIVE_DIR:-/opt/hydrahive}"
VENV="${HYDRAHIVE_DIR}/venv"
MCP_VENVS_DIR="${HYDRAHIVE_DIR}/mcp-venvs"
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
# 3) Python-Pakete in isolierte venvs installieren (idempotent)
#    Je ein eigenes venv unter /opt/hydrahive/mcp-venvs/<id>/
#    Core-venv bleibt unangetastet → kein httpx-Konflikt mehr.
# ═════════════════════════════════════════════════════════════════════════
if ! command -v python3 >/dev/null 2>&1; then
    warn "python3 nicht gefunden — Python-MCP-Server übersprungen"
else
    mkdir -p "${MCP_VENVS_DIR}"
    chown hydrahive:hydrahive "${MCP_VENVS_DIR}" 2>/dev/null || true

    # id → pip-Paketname
    declare -A PY_MCP=(
        ["fetch"]="mcp-server-fetch"
        ["git"]="mcp-server-git"
        ["time"]="mcp-server-time"
        ["sqlite"]="mcp-server-sqlite"
    )
    for id in "${!PY_MCP[@]}"; do
        pkg="${PY_MCP[$id]}"
        vdir="${MCP_VENVS_DIR}/${id}"
        bin="${vdir}/bin/${pkg}"
        if [ -x "${bin}" ]; then
            info "mcp-venv ${id}: bereits installiert"
            continue
        fi
        if [ ! -x "${vdir}/bin/pip" ]; then
            info "venv ${id} anlegen unter ${vdir}"
            python3 -m venv "${vdir}" >/dev/null 2>&1 || { warn "venv ${id} anlegen fehlgeschlagen"; continue; }
            "${vdir}/bin/pip" install --quiet --upgrade pip >/dev/null 2>&1 || true
        fi
        info "pip install ${pkg} (venv ${id})"
        if "${vdir}/bin/pip" install --quiet "${pkg}" >/dev/null 2>&1; then
            success "${pkg} installiert → ${bin}"
        else
            warn "${pkg} Installation fehlgeschlagen"
        fi
    done
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

# Migration: alte Einträge, die noch auf das Core-venv zeigen, auf die
# isolierten mcp-venvs umstellen. Nur Python-MCPs (fetch/git/time/sqlite).
_old_prefix = "${VENV}/bin/mcp-server-"
_new_root = "${MCP_VENVS_DIR}"
_migrated = []
for s in d.get("servers", []):
    cmd = s.get("command", "")
    if cmd.startswith(_old_prefix):
        _sid = s.get("id")
        _binname = cmd[len("${VENV}/bin/"):]
        s["command"] = f"{_new_root}/{_sid}/bin/{_binname}"
        _migrated.append(_sid)
if _migrated:
    print("migrated:", _migrated)

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
        "command": "${MCP_VENVS_DIR}/fetch/bin/mcp-server-fetch", "args": [], "env": {},
    },
    {
        "id": "git", "name": "Git MCP", "transport": "stdio",
        "command": "${MCP_VENVS_DIR}/git/bin/mcp-server-git", "args": [], "env": {},
    },
    {
        "id": "time", "name": "Time", "transport": "stdio",
        "command": "${MCP_VENVS_DIR}/time/bin/mcp-server-time", "args": [], "env": {},
    },
    {
        "id": "sqlite", "name": "SQLite", "transport": "stdio",
        "command": "${MCP_VENVS_DIR}/sqlite/bin/mcp-server-sqlite",
        "args": ["--db-path", "${WORKSPACE_DIR}/mcp.sqlite"], "env": {},
    },
    {
        "id": "maestro", "name": "Maestro Workflow", "transport": "stdio",
        "command": "/usr/bin/node",
        "args": ["/usr/bin/maestro-workflow-mcp"],
        "env": {},
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
        cmd = s["command"]
        if os.path.isabs(cmd):
            if not os.path.isfile(cmd):
                continue
        d["servers"].append(s)
        added.append(s["id"])

open(p, "w").write(json.dumps(d, indent=2))
print("added:", added or "(nichts neues)")
PYEOF

success "MCP-Default-Setup abgeschlossen"
