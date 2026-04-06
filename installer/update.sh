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
        GIT_ASKPASS="${_ASKPASS_SCRIPT}" git clone --depth 1 --quiet "${_AUTH_URL}" "${TMPDIR_BASE}" \
            || { rm -f "${_ASKPASS_SCRIPT}"; error "git clone fehlgeschlagen"; }
        rm -f "${_ASKPASS_SCRIPT}"
    else
        git clone --depth 1 --quiet "${CLONE_URL}" "${TMPDIR_BASE}" \
            || error "git clone fehlgeschlagen"
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

    # --- 2b. System-Agenten aktualisieren (soul.md direkt, agent.yaml per Merge) ---
    # #310: agent.yaml wird GEMERGT statt überschrieben — Runtime-Einstellungen
    # (execution_modes, custom tools, temperature etc.) bleiben erhalten.
    # Nur soul.md wird direkt kopiert (Persönlichkeit gehört zum Repo).
    if [ -d "${TMPDIR_BASE}/agents" ]; then
        for _src in "${TMPDIR_BASE}/agents"/*/; do
            _id="$(basename "${_src}")"
            _dst="/agents/${_id}"
            mkdir -p "${_dst}/memory"
            # soul.md: immer aus Repo übernehmen (Persönlichkeit)
            [ -f "${_src}/soul.md" ] && cp "${_src}/soul.md" "${_dst}/soul.md"
            # agent.yaml: intelligent mergen (neue Tools addieren, Runtime-Config behalten)
            if [ -f "${_src}/agent.yaml" ]; then
                if [ -f "${_dst}/agent.yaml" ]; then
                    ${VENV}/bin/python3 "${HYDRAHIVE_DIR}/scripts/merge_agent_config.py" \
                        "${_src}/agent.yaml" "${_dst}/agent.yaml" 2>/dev/null \
                        || cp "${_src}/agent.yaml" "${_dst}/agent.yaml"
                else
                    cp "${_src}/agent.yaml" "${_dst}/agent.yaml"
                fi
            fi
        done
        chown -R hydrahive:hydrahive /agents/ 2>/dev/null || true
        info "System-Agenten aktualisiert"
    fi

    # --- 2c. Default-Agenten installieren (nur neue, bestehende nicht überschreiben) ---
    if [ -d "${TMPDIR_BASE}/installer/default-agents" ]; then
        _installed=0
        for _src in "${TMPDIR_BASE}/installer/default-agents"/*/; do
            _id="$(basename "${_src}")"
            if [ ! -d "/agents/${_id}" ]; then
                cp -r "${_src}" "/agents/${_id}"
                mkdir -p "/agents/${_id}/memory"
                _installed=$((_installed + 1))
            fi
        done
        chown -R hydrahive:hydrahive /agents/ 2>/dev/null || true
        if [ $_installed -gt 0 ]; then
            info "${_installed} neue Standard-Agenten installiert"
        fi
    fi

    # --- 3. Python-Dependencies ---
    info "Installiere Python-Dependencies..."
    "${VENV}/bin/pip" install -e "${HYDRAHIVE_DIR}/core/" -q \
        || error "pip install fehlgeschlagen"
    success "Python-Dependencies aktualisiert"

    # --- 3b. System-Dependencies nachrüsten (idempotent) ---
    for pkg in ffmpeg jq tree; do
        if ! dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
            info "Installiere fehlende Abhängigkeit: $pkg"
            apt-get install -y -qq "$pkg" || warn "$pkg konnte nicht installiert werden"
        fi
    done

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

    echo "{\"status\":\"ok\",\"finished_at\":\"$(date -Iseconds)\",\"commit\":\"${COMMIT}\",\"commit_full\":\"${COMMIT_FULL}\",\"message\":\"${COMMIT_MSG}\"}" \
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
