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

    # GitHub ist primäre Quelle — funktioniert für alle User
    # Lokales Gitea wird als Override genutzt wenn /etc/hydrahive/use_local_gitea existiert
    local CLONE_URL="${GITHUB_REPO}"
    local GH_TOKEN=""
    if [ -f "${TOKEN_FILE}" ]; then
        GH_TOKEN=$(tr -d '[:space:]' < "${TOKEN_FILE}")
    fi
    info "Klone von GitHub: ${GITHUB_REPO}"

    # Optionaler lokaler Gitea-Override (nur für Entwickler mit use_local_gitea-Flag)
    if [ -f "/etc/hydrahive/use_local_gitea" ] && [ -f "${GITEA_CONFIG}" ]; then
        local GITEA_URL GITEA_TOKEN GITEA_ORG GITEA_REPO
        GITEA_URL=$(python3 -c "import json; d=json.load(open('${GITEA_CONFIG}')); print(d.get('url',''))" 2>/dev/null || echo "")
        GITEA_TOKEN=$(python3 -c "import json; d=json.load(open('${GITEA_CONFIG}')); print(d.get('token',''))" 2>/dev/null || echo "")
        GITEA_ORG=$(python3 -c "import json; d=json.load(open('${GITEA_CONFIG}')); print(d.get('org','hydrahive'))" 2>/dev/null || echo "hydrahive")
        GITEA_REPO=$(python3 -c "import json; d=json.load(open('${GITEA_CONFIG}')); print(d.get('repo', d.get('org','hydrahive')))" 2>/dev/null || echo "${GITEA_ORG}")
        if [ -n "${GITEA_URL}" ] && [ -n "${GITEA_TOKEN}" ]; then
            CLONE_URL="${GITEA_URL}/${GITEA_ORG}/${GITEA_REPO}.git"
            # Token wird unten per GIT_ASKPASS übergeben
            GH_TOKEN="${GITEA_TOKEN}"
            info "Lokaler Gitea-Override aktiv: ${GITEA_URL}/${GITEA_ORG}/${GITEA_REPO}"
        fi
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
    # Branch-Override: /etc/hydrahive/update_branch kann einen Branch-Namen enthalten
    # (z.B. "v2/project-architecture"). Ohne Datei oder leer → Default-Branch (main).
    local _BRANCH_FLAG=""
    local _BRANCH_OVERRIDE_FILE="/etc/hydrahive/update_branch"
    if [ -f "${_BRANCH_OVERRIDE_FILE}" ]; then
        local _BRANCH_NAME
        _BRANCH_NAME=$(tr -d '[:space:]' < "${_BRANCH_OVERRIDE_FILE}")
        if [ -n "${_BRANCH_NAME}" ]; then
            _BRANCH_FLAG="-b ${_BRANCH_NAME}"
            info "Branch-Override aktiv: ${_BRANCH_NAME}"
        fi
    fi

    info "Klone aktuellen Stand..."
    # Token per GIT_ASKPASS übergeben (transient, nicht in Prozessliste/Config)
    local _CLONE_ENV=""
    if [ -n "${GH_TOKEN}" ]; then
        local _ASKPASS_SCRIPT
        _ASKPASS_SCRIPT=$(mktemp)
        printf '#!/bin/sh\necho "%s"\n' "${GH_TOKEN}" > "${_ASKPASS_SCRIPT}"
        chmod +x "${_ASKPASS_SCRIPT}"
        # URL mit Username-Platzhalter für ASKPASS
        local _AUTH_URL="${CLONE_URL/https:\/\//https:\/\/hydrahive@}"
        GIT_ASKPASS="${_ASKPASS_SCRIPT}" timeout 300 git clone --depth 1 --single-branch ${_BRANCH_FLAG} --quiet "${_AUTH_URL}" "${TMPDIR_BASE}" \
            || { rm -f "${_ASKPASS_SCRIPT}"; error "git clone fehlgeschlagen (Timeout nach 5 Minuten oder Netzwerkfehler)"; }
        rm -f "${_ASKPASS_SCRIPT}"
    else
        timeout 300 git clone --depth 1 --single-branch ${_BRANCH_FLAG} --quiet "${CLONE_URL}" "${TMPDIR_BASE}" \
            || error "git clone fehlgeschlagen (Timeout nach 5 Minuten oder Netzwerkfehler)"
    fi
    success "Repo geklont"

    # --- 2. Core aktualisieren ---
    info "Aktualisiere Core..."
    rsync -a --delete \
        --exclude='__pycache__' --exclude='*.pyc' \
        "${TMPDIR_BASE}/core/" "${HYDRAHIVE_DIR}/core/"
    success "Core-Dateien aktualisiert"

    # --- 2a. Scripts aktualisieren ---
    if [ -d "${TMPDIR_BASE}/scripts" ]; then
        rsync -a "${TMPDIR_BASE}/scripts/" "${HYDRAHIVE_DIR}/scripts/"
    fi

    # --- 2b. Default-Projekte aktualisieren (v2: installer/default-projects/) ---
    # AGENT.md wird immer aus dem Repo übernommen (Persönlichkeit/Identität).
    # config.yaml: nur bei Neu-Installation kopieren — Runtime-Einstellungen (temperature,
    # max_tokens, members etc.) die der Admin per Console geändert hat, bleiben erhalten.
    if [ -d "${TMPDIR_BASE}/installer/default-projects" ]; then
        for _src in "${TMPDIR_BASE}/installer/default-projects"/*/; do
            [ -d "${_src}" ] || continue
            _id="$(basename "${_src}")"
            _dst="/projects/${_id}"
            mkdir -p "${_dst}/memory"
            # AGENT.md: immer aus Repo übernehmen (Identität/Persönlichkeit)
            [ -f "${_src}/AGENT.md" ] && cp "${_src}/AGENT.md" "${_dst}/AGENT.md"
            # config.yaml: nur kopieren wenn noch nicht vorhanden (Erstinstallation)
            if [ -f "${_src}/config.yaml" ] && [ ! -f "${_dst}/config.yaml" ]; then
                cp "${_src}/config.yaml" "${_dst}/config.yaml"
            fi
        done
        chown -R hydrahive:hydrahive /projects/ 2>/dev/null || true
        info "Default-Projekte aktualisiert"
    fi

    # --- 3. Python-Dependencies ---
    info "Installiere Python-Dependencies..."
    "${VENV}/bin/pip" install -e "${HYDRAHIVE_DIR}/core/" -q \
        || error "pip install fehlgeschlagen"
    success "Python-Dependencies aktualisiert"

    # --- 3a. Korrupte Packages reparieren + Playwright ---
    # Korrupte dist-info Verzeichnisse entfernen (z.B. ~dge-tts)
    find "${VENV}/lib/" -maxdepth 3 -name '~*' -type d -exec rm -rf {} + 2>/dev/null || true

    # Playwright Python-Modul sicherstellen
    if ! "${VENV}/bin/python3" -c "from playwright.sync_api import sync_playwright" 2>/dev/null; then
        info "Installiere Playwright Python-Modul..."
        "${VENV}/bin/pip" install -q "playwright~=1.40" || warn "playwright pip install fehlgeschlagen"
    fi

    # Playwright Browser installieren (idempotent)
    # --with-deps weggelassen: triggert apt update, scheitert an unsigned Repos (z.B. Plex)
    # System-Deps sind über den Installer bereits vorhanden
    if "${VENV}/bin/python3" -c "from playwright.sync_api import sync_playwright" 2>/dev/null; then
        if [ -z "$(find /root/.cache/ms-playwright /home/*/.cache/ms-playwright "${HYDRAHIVE_DIR}/playwright-browsers" 2>/dev/null -name 'chromium-*' -type d | head -1)" ]; then
            info "Installiere Playwright Chromium..."
            PLAYWRIGHT_BROWSERS_PATH="${HYDRAHIVE_DIR}/playwright-browsers" \
                "${VENV}/bin/playwright" install chromium 2>&1 | tail -3 || warn "Playwright install fehlgeschlagen"
            success "Playwright Chromium installiert"
        fi
    fi

    # --- 3b. System-Dependencies nachrüsten (idempotent) ---
    # bubblewrap (bwrap) ist PFLICHT fuer shell_exec safe/elevated-Sandbox (#605)
    for pkg in ffmpeg jq tree bubblewrap; do
        if ! dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
            info "Installiere fehlende Abhängigkeit: $pkg"
            apt-get install -y -qq "$pkg" || warn "$pkg konnte nicht installiert werden"
        fi
    done

    # --- 3c. Default-MCP-Server installieren + registrieren (idempotent, #620) ---
    if [ -f "${TMPDIR_BASE}/installer/install-default-mcp-servers.sh" ]; then
        info "Default-MCP-Server installieren..."
        bash "${TMPDIR_BASE}/installer/install-default-mcp-servers.sh" 2>&1 | \
            sed 's/^/  /' || warn "MCP-Server-Setup mit Warnungen"
    fi

    # --- 4. Console bauen ---
    info "Baue Console..."
    local CONSOLE_SRC="${TMPDIR_BASE}/console"
    cd "${CONSOLE_SRC}"
    npm ci --prefer-offline 2>&1 | grep -v "^npm warn" || error "npm install fehlgeschlagen"
    npm run build 2>&1 | tail -5 || error "npm run build fehlgeschlagen"
    success "Console gebaut"

    # --- 5. Console deployen ---
    info "Deploye Console..."
    mkdir -p "${HYDRAHIVE_DIR}/console"
    rsync -a --delete "${CONSOLE_SRC}/dist/" "${HYDRAHIVE_DIR}/console/"
    chown -R www-data:www-data "${HYDRAHIVE_DIR}/console/"
    success "Console deployed"

    # --- 5a2. System Handbook deployen (Anti-Documentation-Drift #170) ---
    if [ -f "${TMPDIR_BASE}/installer/system_handbook.md" ]; then
        install -m 644 -o hydrahive -g hydrahive "${TMPDIR_BASE}/installer/system_handbook.md" /etc/hydrahive/system_handbook.md
        info "System Handbook deployed"
    fi

    # --- 5a3. sysctl fuer bwrap-Sandbox (#605) ---
    # Ubuntu 24.04+ AppArmor-Restriction fuer unprivileged user namespaces aushebeln
    # damit bwrap (shell_exec Sandbox) UID-Maps setzen kann.
    # Codex-3 LOW: Fehler bei sysctl -p NICHT verschlucken — klare Fehlermeldung
    if [ -f "${TMPDIR_BASE}/installer/60-hydrahive-bwrap.conf" ]; then
        install -m 644 "${TMPDIR_BASE}/installer/60-hydrahive-bwrap.conf" /etc/sysctl.d/60-hydrahive-bwrap.conf
        if sysctl -p /etc/sysctl.d/60-hydrahive-bwrap.conf >/dev/null 2>&1; then
            # Verifizieren dass Wert wirklich 0 ist
            _unp_userns=$(sysctl -n kernel.apparmor_restrict_unprivileged_userns 2>/dev/null || echo "unset")
            if [ "${_unp_userns}" = "0" ]; then
                info "sysctl: bwrap-Sandbox aktiviert (#605)"
            else
                warn "bwrap-sysctl geschrieben, aber kernel.apparmor_restrict_unprivileged_userns=${_unp_userns} (erwartet: 0). shell_exec safe-Mode wird faktisch nicht laufen bis Host manuell konfiguriert ist."
            fi
        else
            warn "sysctl -p fehlgeschlagen fuer bwrap-Sandbox. shell_exec safe-Mode wird fail-closed greifen. Admin muss manuell: sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0"
        fi
    fi

    # --- 5b. sudoers: alle sudoers-Dateien synchronisieren (#298) ---
    for _sudoer in hydrahive-installer hydrahive-update hydrahive-provisioner hydrahive-network-profile; do
        if [ -f "${TMPDIR_BASE}/installer/${_sudoer}.sudoers" ]; then
            install -m 440 "${TMPDIR_BASE}/installer/${_sudoer}.sudoers" "/etc/sudoers.d/${_sudoer}"
            info "sudoers: ${_sudoer} aktualisiert"
        fi
    done

    # --- 5c. Plugins deployen (#110) ---
    if [ -d "${TMPDIR_BASE}/plugins" ]; then
        mkdir -p /plugins
        rsync -a "${TMPDIR_BASE}/plugins/" /plugins/
        chown -R hydrahive:hydrahive /plugins/
        info "Plugins deployed"
    fi

    # --- 5g. Projekt-Dateiberechtigungen fixen (Samba + Agent-Zugriff) ---
    for _proj in /projects/*/; do
        _pid="$(basename "${_proj}")"
        _files="${_proj}files"
        [ -d "${_files}" ] || continue
        # Gruppe hydrahive + group read/write für alle Dateien
        chgrp -R hydrahive "${_files}" 2>/dev/null
        chmod -R g+rw "${_files}" 2>/dev/null
        # Git Performance für große Repos
        if [ -d "${_files}/.git" ]; then
            git -C "${_files}" config core.preloadindex true 2>/dev/null
            git -C "${_files}" config core.fscache true 2>/dev/null
            git -C "${_files}" config gc.auto 256 2>/dev/null
        fi
    done
    info "Git Performance-Config für Projekt-Repos gesetzt"

    # --- 5h. v2-Migration: Agents → Projekte (einmalig) ---
    # Prüft ob Migration nötig ist und führt sie automatisch durch.
    # Flag-Datei /etc/hydrahive/.v2-migrated verhindert wiederholte Ausführung.
    if [ ! -f "/etc/hydrahive/.v2-migrated" ]; then
        # Prüfe ob es Agents gibt die noch kein v2-Projekt haben
        _needs_migrate=false
        for _agent_dir in /agents/*/; do
            [ ! -d "$_agent_dir" ] && continue
            _aid="$(basename "$_agent_dir")"
            [ "$_aid" = "sessions.db" ] && continue
            [ "$_aid" = "sessions.db-shm" ] && continue
            [ "$_aid" = "sessions.db-wal" ] && continue
            # Hat der Agent eine agent.yaml und das Projekt noch keine config.yaml?
            if [ -f "$_agent_dir/agent.yaml" ] && [ ! -f "/projects/$_aid/config.yaml" ]; then
                _needs_migrate=true
                break
            fi
        done

        if $_needs_migrate; then
            info "v2-Migration: Konvertiere Agents zu Projekten..."
            # Migration per Python — sicher und mit YAML-Parsing
            "${VENV}/bin/python3" - << 'MIGRATE_EOF'
import yaml, sys
from pathlib import Path

agents_dir = Path("/agents")
projects_dir = Path("/projects")
migrated = 0

for agent_dir in sorted(agents_dir.iterdir()):
    if not agent_dir.is_dir():
        continue
    agent_id = agent_dir.name
    if agent_id.startswith("sessions"):
        continue
    # Disabled Agents überspringen
    if agent_id.startswith("_") and agent_id.endswith("_disabled"):
        continue

    agent_yaml = agent_dir / "agent.yaml"
    if not agent_yaml.exists():
        continue

    project_dir = projects_dir / agent_id
    if (project_dir / "config.yaml").exists():
        continue  # Schon migriert

    # Gelöschte Projekte nicht neu erstellen (#566)
    if (project_dir / ".deleted").exists():
        continue

    # agent.yaml lesen
    try:
        raw = yaml.safe_load(agent_yaml.read_text())
    except Exception:
        continue

    llm = raw.get("llm", {})
    model = llm.get("model", "claude-sonnet-4-6")
    temperature = llm.get("temperature", 0.7)
    max_tokens = llm.get("max_tokens", 4096)
    fallback = llm.get("fallback_models", [])

    provider = "anthropic"
    if "gpt" in model.lower():
        provider = "openai"

    config = {
        "id": agent_id,
        "version": "2.0.0",
        "identity": {
            "name": raw.get("identity", agent_id),
            "description": f"Migriert von Agent {agent_id}",
        },
        "llm": {
            "provider": provider,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "failover": [{"provider": "anthropic", "model": m} for m in fallback],
        },
        "plugins": [],
        "repos": [],
        "sources": [],
        "members": ["admin"],
    }

    # Projekt-Verzeichnis + config.yaml
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "memory").mkdir(exist_ok=True)
    (project_dir / "config.yaml").write_text(
        yaml.dump(config, default_flow_style=False, allow_unicode=True, sort_keys=False)
    )

    # soul.md → AGENT.md
    soul_path = agent_dir / "soul.md"
    if soul_path.exists():
        (project_dir / "AGENT.md").write_text(soul_path.read_text())
    else:
        (project_dir / "AGENT.md").write_text(f"# {agent_id}\n\nMigriert von Agent {agent_id}.\n")

    # Memory kopieren
    memory_src = agent_dir / "memory"
    if memory_src.is_dir():
        memory_dst = project_dir / "memory"
        for mf in memory_src.glob("*.md"):
            (memory_dst / mf.name).write_text(mf.read_text())

    # Altes project.yaml backup falls vorhanden
    old_project_yaml = project_dir / "project.yaml"
    if old_project_yaml.exists():
        old_project_yaml.rename(project_dir / "project.yaml.v1-backup")

    migrated += 1

print(f"v2-Migration: {migrated} Agents → Projekte konvertiert")
MIGRATE_EOF

            # Berechtigungen setzen
            chown -R hydrahive:hydrahive /projects/ 2>/dev/null || true

            # Flag setzen — Migration nicht nochmal ausführen
            touch /etc/hydrahive/.v2-migrated
            success "v2-Migration abgeschlossen"
        else
            info "v2-Migration: nicht nötig (alle Agents haben bereits Projekte)"
            touch /etc/hydrahive/.v2-migrated
        fi
    fi

    # --- 6. Service neustarten ---
    info "Starte hydrahive-core neu..."
    systemctl daemon-reload
    systemctl restart hydrahive-core
    # Aktiv warten bis active oder max 30s
    # || true weil set -e sonst beim ersten is-active=1 (noch nicht active) abbricht
    for i in $(seq 1 30); do sleep 1; systemctl is-active --quiet hydrahive-core && break || true; done

    if systemctl is-active --quiet hydrahive-core; then
        success "hydrahive-core läuft"
    else
        error "hydrahive-core konnte nicht starten — prüfe: journalctl -u hydrahive-core -n 30"
    fi

    # --- 6b. sysinfo-Agent Memory aktualisieren ---
    if [ -f "${HYDRAHIVE_DIR}/installer/modules/17_sysinfo_scan.sh" ]; then
        bash "${HYDRAHIVE_DIR}/installer/modules/17_sysinfo_scan.sh" \
            && info "sysinfo-Memory aktualisiert" \
            || warn "sysinfo-Scan fehlgeschlagen — wird übersprungen"
    fi

    # --- 7. A-MEM Memory re-indexieren (optional) ---
    if command -v qmd &>/dev/null; then
        info "A-MEM: re-indexiere Memory..."
        sudo -u hydrahive bash -c "HOME=/home/hydrahive qmd update -q 2>/dev/null && qmd embed -q 2>/dev/null" || true
        success "A-MEM aktualisiert"
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

    # --- 8b. code-server neu starten falls installiert ---
    if systemctl is-enabled --quiet hydrahive-codeserver 2>/dev/null; then
        if systemctl is-active --quiet hydrahive-codeserver; then
            systemctl restart hydrahive-codeserver && success "code-server neu gestartet" \
                || warn "code-server Neustart fehlgeschlagen"
        else
            systemctl start hydrahive-codeserver && success "code-server gestartet" \
                || warn "code-server konnte nicht gestartet werden"
        fi
    fi

    # --- 9. A-MEM aktualisieren (optional — Fehler nicht fatal) ---
    if [ -f "${TMPDIR_BASE}/installer/amem/install_amem.sh" ]; then
        info "Aktualisiere A-MEM..."
        bash "${TMPDIR_BASE}/installer/amem/install_amem.sh" \
            && success "A-MEM aktualisiert" \
            || warn "A-MEM Update fehlgeschlagen — wird übersprungen"
    fi

    # --- 9b. SearXNG aktualisieren (optional — Fehler nicht fatal) ---
    if [ -d "/opt/searxng/.git" ]; then
        info "Aktualisiere SearXNG..."
        (
            sudo -u searxng git -C /opt/searxng pull --ff-only --quiet 2>/dev/null || true
            # msgspec wird beim Build von searx benötigt, muss vorher installiert sein
            /opt/searxng/venv/bin/pip install --quiet msgspec 2>/dev/null || true
            /opt/searxng/venv/bin/pip install --quiet -e /opt/searxng
            systemctl restart searxng
        ) && success "SearXNG aktualisiert" \
          || warn "SearXNG Update fehlgeschlagen — wird übersprungen"
    else
        info "SearXNG nicht installiert — überspringe Update"
    fi

    # --- 9c. AgentLink sicherstellen (optional — Fehler nicht fatal) ---
    if ! systemctl is-active --quiet hydrahive-agentlink 2>/dev/null; then
        if [ -f "${HYDRAHIVE_DIR}/installer/modules/11_agentlink.sh" ]; then
            info "AgentLink nicht aktiv — installiere/starte nach..."
            bash "${HYDRAHIVE_DIR}/installer/modules/11_agentlink.sh" \
                && success "AgentLink installiert/gestartet" \
                || warn "AgentLink konnte nicht gestartet werden"
        else
            warn "AgentLink nicht aktiv und kein Installer-Modul gefunden"
        fi
    fi

    # --- 10. Installer-Module + Installer-Assets aktualisieren ---
    if [ -d "${TMPDIR_BASE}/installer/modules" ]; then
        mkdir -p "${HYDRAHIVE_DIR}/installer/modules"
        rsync -a "${TMPDIR_BASE}/installer/modules/" "${HYDRAHIVE_DIR}/installer/modules/"
        chmod +x "${HYDRAHIVE_DIR}/installer/modules/"*.sh 2>/dev/null || true
        info "Installer-Module aktualisiert"
    fi
    if [ -d "${TMPDIR_BASE}/installer/extensions" ]; then
        mkdir -p "${HYDRAHIVE_DIR}/installer/extensions"
        rsync -a "${TMPDIR_BASE}/installer/extensions/" "${HYDRAHIVE_DIR}/installer/extensions/"
        chmod +x "${HYDRAHIVE_DIR}/installer/extensions/uninstall/"*.sh 2>/dev/null || true
        chmod +x "${HYDRAHIVE_DIR}/installer/extensions/install/"*.sh 2>/dev/null || true
        info "Extension-Manifeste aktualisiert"
    fi
    if [ -d "${TMPDIR_BASE}/whatsapp-bridge" ]; then
        # Quellcode immer aktualisieren (auch wenn Bridge nicht installiert)
        mkdir -p "${HYDRAHIVE_DIR}/whatsapp-bridge"
        rsync -a --exclude='node_modules' --exclude='.git' \
            "${TMPDIR_BASE}/whatsapp-bridge/" "${HYDRAHIVE_DIR}/whatsapp-bridge/"
        info "WhatsApp-Bridge Quellcode aktualisiert"

        # Wenn Bridge installiert ist: vollen Installer laufen lassen
        # (aktualisiert Service-File, Chrome-Libs, crashpad-Fix, etc.)
        if systemctl list-unit-files hydrahive-whatsapp-bridge.service &>/dev/null \
           && systemctl list-unit-files hydrahive-whatsapp-bridge.service | grep -q hydrahive-whatsapp-bridge; then
            info "Aktualisiere WhatsApp Bridge (Installer)..."
            HYDRAHIVE_DIR="${HYDRAHIVE_DIR}" \
                bash "${HYDRAHIVE_DIR}/installer/modules/13_whatsapp_bridge.sh" \
                && success "WhatsApp Bridge aktualisiert" \
                || warn "WhatsApp Bridge Update fehlgeschlagen — Bridge läuft möglicherweise noch"
        fi
    fi
    # Installer-Assets (nginx-Template etc.) im installer/-Verzeichnis aktuell halten
    for _asset in hydrahive-console.nginx hydrahive-installer.sudoers; do
        if [ -f "${TMPDIR_BASE}/installer/${_asset}" ]; then
            cp "${TMPDIR_BASE}/installer/${_asset}" "${HYDRAHIVE_DIR}/installer/${_asset}"
        fi
    done

    # #603/#610: nginx Security-Header Snippet nach /etc/nginx/snippets/ deployen.
    # Haupt-Config wird MINIMAL-INVASIV gepatcht (include-Zeile in location / und
    # location /api/) — mit Backup + nginx-t-Validation + Rollback bei Fehler.
    if [ -f "${TMPDIR_BASE}/installer/hydrahive-security-headers.conf" ]; then
        mkdir -p /etc/nginx/snippets
        install -m 644 "${TMPDIR_BASE}/installer/hydrahive-security-headers.conf" \
            /etc/nginx/snippets/hydrahive-security-headers.conf
        info "nginx Security-Header Snippet deployed (#603)"
        # Nginx-Config auto-patchen wenn vorhanden und Snippet noch nicht included
        _nginx_cfg=""
        for _cand in /etc/nginx/sites-available/hydrahive-console /etc/nginx/sites-enabled/hydrahive-console; do
            if [ -f "${_cand}" ] && [ ! -L "${_cand}" ]; then
                _nginx_cfg="${_cand}"
                break
            elif [ -f "${_cand}" ]; then
                # Symlink → sites-available lesen
                _nginx_cfg=$(readlink -f "${_cand}")
                break
            fi
        done
        if [ -n "${_nginx_cfg}" ] && ! grep -q "hydrahive-security-headers" "${_nginx_cfg}"; then
            info "nginx-Config patchen: include in location / + /api/ einfuegen"
            _backup="${_nginx_cfg}.bak-$(date +%s)"
            cp "${_nginx_cfg}" "${_backup}"
            python3 <<PYEOF
import re
p = "${_nginx_cfg}"
txt = open(p).read()
inc = "        include /etc/nginx/snippets/hydrahive-security-headers.conf;\n"
if "hydrahive-security-headers" not in txt:
    txt = re.sub(r"(location / \{\n)(\s+try_files)", r"\1" + inc + r"\2", txt, count=1)
    txt = re.sub(r"(location /api/ \{\n)(\s+proxy_pass)", r"\1" + inc + r"\2", txt, count=1)
    open(p, "w").write(txt)
PYEOF
            # Validate + reload oder rollback
            if nginx -t >/dev/null 2>&1; then
                systemctl reload nginx >/dev/null 2>&1 && \
                    info "nginx-Config gepatcht + reloaded — CSP aktiv" || \
                    warn "nginx reload fehlgeschlagen — Config ist gepatcht, aber nicht aktiv. systemctl status nginx pruefen."
            else
                warn "nginx -t nach CSP-Patch fehlgeschlagen — Rollback aus Backup ${_backup}"
                cp "${_backup}" "${_nginx_cfg}"
            fi
        elif [ -n "${_nginx_cfg}" ]; then
            # Include schon drin — nur noch reloaden falls Snippet aktualisiert wurde
            nginx -t >/dev/null 2>&1 && systemctl reload nginx >/dev/null 2>&1 || true
        fi
    fi

    # --- 10b. update.sh + Service-Datei selbst aktualisieren ---
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

    # nginx-Konfig wird absichtlich NICHT automatisch aktualisiert.
    # Nutzer können ihre Konfig (SSL, custom rules etc.) angepasst haben.
    # Fehlende A2A-Regeln → Doctor-Seite → Fix-Button.

    # --- 11. Versions-Info + Status-Datei ---
    local COMMIT COMMIT_FULL COMMIT_MSG
    COMMIT=$(git -C "${TMPDIR_BASE}" rev-parse --short HEAD 2>/dev/null || echo "unbekannt")
    COMMIT_FULL=$(git -C "${TMPDIR_BASE}" rev-parse HEAD 2>/dev/null || echo "")
    COMMIT_MSG=$(git -C "${TMPDIR_BASE}" log -1 --pretty=format:'%s' 2>/dev/null || echo "")
    # JSON-Sonderzeichen in Commit-Message escapen (Quotes, Backslashes)
    COMMIT_MSG_SAFE=$(echo "$COMMIT_MSG" | sed 's/\\/\\\\/g; s/"/\\"/g')

    echo "{\"status\":\"ok\",\"finished_at\":\"$(date -Iseconds)\",\"commit\":\"${COMMIT}\",\"commit_full\":\"${COMMIT_FULL}\",\"message\":\"${COMMIT_MSG_SAFE}\"}" \
        > "${UPDATE_STATUS_FILE}" 2>/dev/null || true
    echo "[$(date -Iseconds)] OK commit=${COMMIT} msg=${COMMIT_MSG}" >> "${UPDATE_LOG}" 2>/dev/null || true

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    success "Update abgeschlossen (Commit: ${COMMIT})"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    exit 0
}

main "$@"
