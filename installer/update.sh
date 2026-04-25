#!/usr/bin/env bash
# HydraHive Update-Script — direkt auf der VM ausführen
# Usage: sudo bash /opt/hydrahive/update.sh
#
# Ablauf:
#   1. Repo klonen: lokales Gitea (primär) → GitHub (Fallback)
#   2. Core rsync → /opt/hydrahive/core/
#   3. pip install -e . im venv
#   4. Console npm ci + build
#   5. dist/ → /var/www/hydrahive-console/
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

    # #703: Auto-Update-Kill-Switch. Nur aktiv wenn via
    # hydrahive-autoupdate.service (Timer) aufgerufen — Web-Trigger über
    # hydrahive-selfupdate.service und `sudo bash update.sh` ignorieren
    # den Flag absichtlich.
    if [ "${HYDRAHIVE_AUTO_UPDATE:-0}" = "1" ] && [ -f /etc/hydrahive/disable_auto_update ]; then
        info "auto-update disabled via /etc/hydrahive/disable_auto_update"
        exit 0
    fi

    # #703: Locking gegen parallele Update-Läufe (Timer + Web-Button etc.).
    # non-blocking — wer den Lock nicht kriegt, exitet still mit 0 damit
    # systemd den Service nicht als failed markiert.
    exec 200>/var/run/hydrahive-update.lock
    if ! flock -n 200; then
        info "Update läuft bereits (lockfile /var/run/hydrahive-update.lock) — Abbruch"
        exit 0
    fi

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
    # Sicherstellen dass /etc/hydrahive hydrahive gehört (überlebt Reboots nicht immer)
    mkdir -p /etc/hydrahive
    chown -R hydrahive:hydrahive /etc/hydrahive 2>/dev/null || true
    # User-Session persistent halten damit QEMU-Scopes Service-Neustarts überleben
    loginctl enable-linger hydrahive 2>/dev/null || true

    info "Aktualisiere Core..."
    rsync -a --delete \
        --exclude='__pycache__' --exclude='*.pyc' \
        "${TMPDIR_BASE}/core/" "${HYDRAHIVE_DIR}/core/"
    success "Core-Dateien aktualisiert"

    # --- 2a. Scripts aktualisieren ---
    if [ -d "${TMPDIR_BASE}/scripts" ]; then
        rsync -a "${TMPDIR_BASE}/scripts/" "${HYDRAHIVE_DIR}/scripts/"
    fi

    # --- 2b0. Bundled Agent Templates aktualisieren ---
    # Repo-gepflegte Template-Agents brauchen Updates an agent.yaml/soul.md.
    # Runtime-State wie memory/, skills/ und user-eigene personal_*-Agents darf
    # dabei nicht überschrieben werden.
    if [ -d "${TMPDIR_BASE}/agents" ]; then
        mkdir -p /agents
        BUNDLED_TEMPLATES=("hydrahive_support")
        for bundled in "${BUNDLED_TEMPLATES[@]}"; do
            src_yaml="${TMPDIR_BASE}/agents/${bundled}/agent.yaml"
            if [ ! -f "${src_yaml}" ]; then
                error "bundled template '${bundled}' fehlt im Repo (${src_yaml})"
            fi
            dst_dir="/agents/${bundled}"
            mkdir -p "${dst_dir}"
            install -m 644 -o hydrahive -g hydrahive "${src_yaml}" "${dst_dir}/agent.yaml"
            src_soul="${TMPDIR_BASE}/agents/${bundled}/soul.md"
            if [ -f "${src_soul}" ]; then
                install -m 644 -o hydrahive -g hydrahive "${src_soul}" "${dst_dir}/soul.md"
            fi
            info "Bundled Agent Template aktualisiert: ${bundled}"
        done
        rsync -a --ignore-existing \
            --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
            "${TMPDIR_BASE}/agents/" /agents/
        chown -R hydrahive:hydrahive /agents/ 2>/dev/null || true
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
    # #848: extras=hf zieht transformers mit (HuggingFace-Tokenizer fuer MiniMax/Kimi).
    # Ohne hf faellt Gate #843 auf chars/3.2-Heuristik zurueck.
    "${VENV}/bin/pip" install -e "${HYDRAHIVE_DIR}/core/[hf]" -q \
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
    # qemu-utils/qemu-system-x86_64 für VM-Manager (#895)
    for pkg in ffmpeg jq tree bubblewrap qemu-utils qemu-system-x86 websockify; do
        if ! dpkg -l "$pkg" 2>/dev/null | grep -q "^ii"; then
            info "Installiere fehlende Abhängigkeit: $pkg"
            apt-get install -y -qq "$pkg" || warn "$pkg konnte nicht installiert werden"
        fi
    done
    # VM-Manager: Storage-Verzeichnisse + kvm-Gruppe (idempotent, #895)
    if dpkg -l qemu-utils 2>/dev/null | grep -q "^ii"; then
        chown hydrahive:hydrahive /var/lib/hydrahive 2>/dev/null || true
        for _vmdir in isos vms vnc-tokens; do
            _t="/var/lib/hydrahive/${_vmdir}"
            mkdir -p "$_t" && chown hydrahive:hydrahive "$_t"
            [ "$_vmdir" = "vnc-tokens" ] && chmod 700 "$_t" || chmod 750 "$_t"
        done
        # disk-imports Verzeichnis (#908)
        _t="/var/lib/hydrahive/disk-imports"
        mkdir -p "$_t" && chown hydrahive:hydrahive "$_t" && chmod 750 "$_t"
        id -nGz hydrahive 2>/dev/null | grep -qzxF "kvm" || \
            usermod -aG kvm hydrahive 2>/dev/null || true
        # KVM-Kernel-Modul laden und persistent machen (#895)
        if [ ! -e /dev/kvm ]; then
            _cpu_vendor=$(grep -m1 "vendor_id" /proc/cpuinfo 2>/dev/null | awk '{print $3}')
            if grep -qE "vmx|svm" /proc/cpuinfo 2>/dev/null; then
                if [ "$_cpu_vendor" = "AuthenticAMD" ]; then
                    modprobe kvm-amd 2>/dev/null && info "kvm-amd Modul geladen" || warn "kvm-amd konnte nicht geladen werden"
                    echo "kvm-amd" > /etc/modules-load.d/kvm.conf
                else
                    modprobe kvm-intel 2>/dev/null && info "kvm-intel Modul geladen" || warn "kvm-intel konnte nicht geladen werden"
                    echo "kvm-intel" > /etc/modules-load.d/kvm.conf
                fi
                [ -e /dev/kvm ] && chmod 666 /dev/kvm && info "KVM aktiviert (/dev/kvm)" || warn "KVM-Modul geladen aber /dev/kvm fehlt noch — Reboot nötig"
            else
                warn "CPU unterstützt keine Hardware-Virtualisierung (kein vmx/svm) — KVM nicht möglich"
            fi
        else
            info "KVM bereits aktiv (/dev/kvm)"
        fi
        # websockify systemd-Service (idempotent)
        if [ -x /usr/bin/websockify ] || [ -x /usr/local/bin/websockify ]; then
            info "#895: websockify-Service einrichten..."
            cat > /etc/systemd/system/hydrahive-websockify.service << 'WSEOF'
[Unit]
Description=HydraHive VNC WebSocket Proxy
After=network.target hydrahive-core.service

[Service]
User=hydrahive
ExecStart=/usr/bin/websockify --token-plugin=TokenFile --token-source=/var/lib/hydrahive/vnc-tokens/ 6080
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
WSEOF
            systemctl daemon-reload
            systemctl enable --now hydrahive-websockify.service 2>/dev/null && \
                info "websockify-Service aktiv (Port 6080)" || \
                warn "websockify-Service konnte nicht gestartet werden"
        fi
    fi

    # Bridge br0 wird NICHT automatisch eingerichtet — zu riskant auf Produktionsservern.
    # Manuell: sudo bash installer/modules/20_vm_manager_bridge.sh

    # #895: /ws/vnc/ WebSocket-Location in nginx einfügen falls fehlend
    _nginx_cfg_vnc=""
    for _cand in /etc/nginx/sites-available/hydrahive-console /etc/nginx/sites-enabled/hydrahive-console; do
        if [ -f "${_cand}" ] && [ ! -L "${_cand}" ]; then _nginx_cfg_vnc="${_cand}"; break
        elif [ -f "${_cand}" ]; then _nginx_cfg_vnc=$(readlink -f "${_cand}"); break; fi
    done
    if [ -n "${_nginx_cfg_vnc}" ] && ! grep -q "ws/vnc" "${_nginx_cfg_vnc}"; then
        info "#895: VNC WebSocket-Location in nginx einfügen"
        _backup_vnc="${_nginx_cfg_vnc}.bak-vnc-$(date +%s)"
        cp "${_nginx_cfg_vnc}" "${_backup_vnc}"
        python3 <<'PYEOF'
import re, sys, os
p = ""
for cand in ["/etc/nginx/sites-available/hydrahive-console", "/etc/nginx/sites-enabled/hydrahive-console"]:
    if os.path.exists(cand):
        p = os.path.realpath(cand); break
if not p:
    sys.exit("nginx-Config nicht gefunden")
txt = open(p).read()
if "ws/vnc" in txt:
    sys.exit(0)
block = (
    "    # VM-Manager VNC WebSocket (#895)\n"
    "    location /ws/vnc/ {\n"
    "        proxy_pass         http://127.0.0.1:6080/;\n"
    "        proxy_http_version 1.1;\n"
    "        proxy_set_header   Upgrade    $http_upgrade;\n"
    "        proxy_set_header   Connection \"upgrade\";\n"
    "        proxy_set_header   Host       $host;\n"
    "        proxy_read_timeout 3600s;\n"
    "    }\n\n"
)
new_txt, count = re.subn(r"(\s*location /api/ \{)", "\n" + block + r"\1", txt, count=1)
if count == 0:
    sys.exit("marker 'location /api/ {' nicht gefunden")
open(p, "w").write(new_txt)
print("OK")
PYEOF
        if nginx -t 2>&1 | head -3; then
            systemctl reload nginx 2>&1 | head -2 && \
                info "#895: nginx VNC WebSocket-Location aktiv (/ws/vnc/ → :6080)" || \
                warn "#895: nginx reload fehlgeschlagen"
        else
            warn "#895: nginx -t fehlgeschlagen nach VNC-Patch — Rollback"
            cp "${_backup_vnc}" "${_nginx_cfg_vnc}"
        fi
    fi

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
    mkdir -p /var/www/hydrahive-console/
    rsync -a --delete "${CONSOLE_SRC}/dist/" /var/www/hydrahive-console/
    chown -R www-data:www-data /var/www/hydrahive-console/
    success "Console deployed"

    # --- 5a. nginx-Config-Migration: alter Pfad /opt/hydrahive/console → neuer /var/www/hydrahive-console ---
    # BL-14-Strukturfix: bestehende Installationen haben nginx root noch auf /opt/hydrahive/console.
    # Patch idempotent, greift nur wenn alter Pfad noch drin steht.
    _NGINX_MIGRATED=0
    for _cfg in /etc/nginx/sites-available/hydrahive-console /etc/nginx/sites-available/hydrahive-console-https; do
        if [ -f "${_cfg}" ] && grep -q "root /opt/hydrahive/console" "${_cfg}"; then
            sed -i 's|root /opt/hydrahive/console|root /var/www/hydrahive-console|g' "${_cfg}"
            _NGINX_MIGRATED=1
            info "nginx root migriert in ${_cfg}: /opt/hydrahive/console → /var/www/hydrahive-console"
        fi
    done
    if [ "${_NGINX_MIGRATED}" -eq 1 ]; then
        if nginx -t &>/dev/null; then
            systemctl reload nginx && success "nginx neu geladen nach Pfad-Migration"
        else
            warn "nginx -t meldet Fehler nach Pfad-Migration — nicht reloaded"
            nginx -t
        fi
    fi

    # --- 5a2. System Handbook deployen (Anti-Documentation-Drift #170) ---
    if [ -f "${TMPDIR_BASE}/installer/system_handbook.md" ]; then
        install -m 644 -o hydrahive -g hydrahive "${TMPDIR_BASE}/installer/system_handbook.md" /etc/hydrahive/system_handbook.md
        info "System Handbook deployed"
    fi

    # --- 5a2b. Instance-Policy Stub anlegen (#711) ---
    # Stub wird NUR angelegt wenn nicht vorhanden — lokale Admin-Änderungen bleiben erhalten.
    if [ ! -f "/etc/hydrahive/instance_policy.md" ]; then
        cat > /etc/hydrahive/instance_policy.md << 'EOF'
# Instance Policy

Diese Datei enthält instanzweite Regeln für alle Agenten dieser HydraHive-Instanz.
Sie wird durch Updates nicht überschrieben.

## Token-Disziplin bei Codearbeiten

1. Vor Codeänderungen maximal eine gebündelte Explorationsrunde: `git status --short`, gezielte `rg`-Suchen und relevante Dateiabschnitte mit `sed -n`. Keine sequentiellen Einzel-Greps ohne neue Erkenntnis.
2. Vor Patches erst Kontext eindeutig machen: Dateiabschnitt lesen, dann genau patchen. Keine blind wiederholten `file_patch`-Retries.
3. Testumgebung zuerst einmal feststellen: `which pytest || true`, `python3 -m pytest --version || true`. Wenn keine Testumgebung existiert: Syntax-Check + Bericht, nicht mehrere Suchrunden.
4. Git-Flow direkt: `git status`, `git add ...`, `git commit ...`, `git push`. `git pull --rebase` nur wenn Push abgelehnt wird. Kein Tool-Discovery-Ritual für bekannte Git-Befehle.
5. Wenn nach 10 Tool-Runden kein Patch steht: stoppen, Zwischenbefund liefern, Plan korrigieren.
EOF
        chown hydrahive:hydrahive /etc/hydrahive/instance_policy.md
        chmod 644 /etc/hydrahive/instance_policy.md
        info "Instance Policy Stub angelegt (#711)"
    fi

    # --- 5a2c. ToolGuard Config-Stub anlegen (#717 / #719) ---
    # Stub nur anlegen wenn fehlt. Guard startet per Default als deaktiviert —
    # Admin muss canonical_path für die Instanz setzen und enabled=true wählen,
    # sonst könnte die Block-Message einen Pfad nennen, der auf der Instanz
    # gar nicht existiert (Default /home/till/octopos ist nur auf Tills Dev-
    # System sinnvoll).
    if [ ! -f "/etc/hydrahive/tool_guard.json" ]; then
        cat > /etc/hydrahive/tool_guard.json << 'EOF'
{
  "_comment": "HydraHive ToolGuard config (#717). Guard blockiert Schreibaktionen in stale Checkouts. canonical_path = Pfad, in dem Writes erlaubt sind. stale_write_roots = Pfade, in denen nur Diagnose erlaubt ist. Fuer diese Instanz: canonical_path setzen (z.B. /var/lib/hydrahive/workspace oder /home/<admin>/hydrahive) und enabled=true. Solange enabled=false, laeuft der Guard nicht scharf.",
  "canonical_path": "",
  "stale_write_roots": [
    "/projects/hydrahivedev/repo",
    "/home/octopos/hydrahive",
    "/opt/hydrahive/core"
  ],
  "enabled": false
}
EOF
        chown hydrahive:hydrahive /etc/hydrahive/tool_guard.json
        chmod 644 /etc/hydrahive/tool_guard.json
        info "ToolGuard Config-Stub angelegt (#719) — enabled=false bis Admin canonical_path setzt"
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
    # #687/#704: Runtime-State-Dirs idempotent absichern, damit bestehende
    # Instanzen nach einem Update neue Pfade kriegen (z.B. /var/lib/hydrahive/jobs)
    # bevor der Core die neu gezogene Version lädt.
    # Primär-Quelle: frisch geklonter Installer-Tree (TMPDIR_BASE). Fallback:
    # bereits installierte Kopie unter HYDRAHIVE_DIR, falls dort schon vorhanden.
    _RUNTIME_HELPER="${TMPDIR_BASE}/installer/lib/ensure_runtime_dirs.sh"
    if [ ! -f "${_RUNTIME_HELPER}" ]; then
        _RUNTIME_HELPER="${HYDRAHIVE_DIR}/installer/lib/ensure_runtime_dirs.sh"
    fi
    if [ -f "${_RUNTIME_HELPER}" ]; then
        info "Sichere Runtime-State-Verzeichnisse..."
        # shellcheck source=./lib/ensure_runtime_dirs.sh
        HYDRAHIVE_USER="hydrahive" HYDRAHIVE_GROUP="hydrahive" source "${_RUNTIME_HELPER}"
    else
        # #703 Follow-up: nicht mehr hart abbrechen, nur warnen. Der Hotfix
        # 8f17e4f macht Core-Komponenten gegen fehlende Runtime-Dirs tolerant,
        # und ein hartes exit 1 an dieser Stelle ist selbst eine Bootstrap-Falle:
        # bricht update.sh hier ab, wird die neue update.sh (die den Fix mitbringt)
        # nie deployed, und die Instanz hängt fest.
        warn "Runtime-Helper nicht gefunden (weder ${TMPDIR_BASE}/installer/lib/ noch ${HYDRAHIVE_DIR}/installer/lib/). Update läuft trotzdem weiter."
    fi

    # BL-15 Follow-up: Installer-Module werden von update.sh NICHT re-run,
    # daher greift der BL-15-Hash-Sync (06_core_service.sh) bei reinen Updates
    # nicht. Wir rufen die Kern-Logik hier idempotent nochmal auf, damit auch
    # bestehende Installationen den Admin-Hash in users.json synchron halten
    # mit dem console_password in admin_credentials. Idempotent — bei
    # passendem Hash wird nichts geschrieben.
    # #813: admin_credentials muss für hydrahive-User lesbar sein.
    # 06_core_service.sh setzt chown hydrahive:hydrahive 600 beim Install —
    # bei ältere Installationen liegt die Datei aber mitunter root:root 600
    # (Core-Child kann nicht lesen → Matrix-Token + Gitea-Credentials scheitern).
    if [ -f /etc/hydrahive/admin_credentials ]; then
        chown hydrahive:hydrahive /etc/hydrahive/admin_credentials 2>/dev/null || true
        chmod 600 /etc/hydrahive/admin_credentials 2>/dev/null || true
    fi

    if [ -f /etc/hydrahive/admin_credentials ] && [ -f /etc/hydrahive/users.json ]; then
        info "BL-15: Admin-Hash/Passwort-Sync pruefen..."
        CONSOLE_PASS=$(grep -E '^console_password=' /etc/hydrahive/admin_credentials | cut -d= -f2-) || CONSOLE_PASS=""
        if [ -n "${CONSOLE_PASS}" ]; then
            CONSOLE_PASS="${CONSOLE_PASS}" python3 - <<'PY' || warn "BL-15 Sync fehlgeschlagen — Login ggf. ueber Fallback"
import json, hashlib, secrets, os, sys
pwd = os.environ.get("CONSOLE_PASS", "")
if not pwd:
    sys.exit(0)
try:
    with open("/etc/hydrahive/users.json") as f:
        users = json.load(f)
except Exception:
    users = {}
admin = users.get("admin", {}) or {}
existing = admin.get("password_hash", "")
if existing.startswith("pbkdf2b:"):
    try:
        _, salt_hex, h_hex = existing.split(":", 2)
        check = hashlib.pbkdf2_hmac("sha256", pwd.encode(), bytes.fromhex(salt_hex), 260_000)
        if check.hex() == h_hex:
            sys.exit(0)  # Hash bereits aktuell, nichts zu tun
    except Exception:
        pass
salt = secrets.token_bytes(16)
h = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt, 260_000)
admin["password_hash"] = f"pbkdf2b:{salt.hex()}:{h.hex()}"
admin.setdefault("role", "admin")
admin.setdefault("group", "admin")
users["admin"] = admin
with open("/etc/hydrahive/users.json", "w") as f:
    json.dump(users, f, indent=2)
print("[BL-15 update.sh] admin-Hash in users.json aktualisiert")
PY
        fi
    fi

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
            # pip install -e nur wenn searx noch nicht als Paket bekannt (Erstinstall)
            # Bei Folge-Updates reicht git pull — editable install ist bereits aktiv
            if ! /opt/searxng/venv/bin/pip show searx >/dev/null 2>&1; then
                /opt/searxng/venv/bin/pip install --quiet --no-build-isolation -e /opt/searxng || true
            fi
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
    if [ -d "${TMPDIR_BASE}/installer/lib" ]; then
        # #704/#703: lib/ enthält shared helpers (ensure_runtime_dirs.sh).
        # Muss beim Update mitkopiert werden, sonst läuft der Runtime-Helper
        # nicht aus /opt/hydrahive/installer/lib/.
        mkdir -p "${HYDRAHIVE_DIR}/installer/lib"
        rsync -a "${TMPDIR_BASE}/installer/lib/" "${HYDRAHIVE_DIR}/installer/lib/"
        info "Installer-lib aktualisiert"
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

        # #554 H5: Collab-WebSocket-Location einfügen (wenn noch nicht drin).
        # Die allgemeine /api/-Location setzt Connection "" → tötet WS-Upgrades.
        # Deshalb eine regex-Location VOR der /api/-Location mit Upgrade-Header.
        info "#554 H5 Patcher gestartet (_nginx_cfg=${_nginx_cfg:-<leer>})"
        if [ -z "${_nginx_cfg}" ]; then
            warn "#554 H5: keine nginx-Config gefunden unter /etc/nginx/sites-*/hydrahive-console — überspringe"
        elif grep -q "projects/\[\\^/\\]+/collab" "${_nginx_cfg}"; then
            info "#554 H5: Collab-Location schon in ${_nginx_cfg} vorhanden"
        else
            info "nginx-Config patchen: Collab-WebSocket-Location einfuegen (#554)"
            _backup_collab="${_nginx_cfg}.bak-collab-$(date +%s)"
            cp "${_nginx_cfg}" "${_backup_collab}"
            python3 <<PYEOF
import re
p = "${_nginx_cfg}"
txt = open(p).read()
if "projects/[^/]+/collab" in txt:
    raise SystemExit(0)
block = (
    "    # #554: Collaborative Composer — WebSocket-Upgrade.\n"
    "    location ~ ^/api/projects/[^/]+/collab\$ {\n"
    "        include /etc/nginx/snippets/hydrahive-security-headers.conf;\n"
    "        rewrite ^/api(/.*)\$ \$1 break;\n"
    "        proxy_pass         http://127.0.0.1:8765;\n"
    "        proxy_http_version 1.1;\n"
    "        proxy_set_header   Host              \$host;\n"
    "        proxy_set_header   X-Real-IP         \$remote_addr;\n"
    "        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;\n"
    "        proxy_set_header   Upgrade           \$http_upgrade;\n"
    "        proxy_set_header   Connection        \"upgrade\";\n"
    "        proxy_read_timeout 3600s;\n"
    "        proxy_connect_timeout 5s;\n"
    "    }\n\n"
)
new_txt, count = re.subn(r"(\s*location /api/ \{)", "\n" + block + r"\1", txt, count=1)
if count == 0:
    raise SystemExit("marker 'location /api/ {' nicht gefunden")
open(p, "w").write(new_txt)
PYEOF
            if nginx -t 2>&1 | head -20; then
                systemctl reload nginx 2>&1 | head -5 && \
                    info "#554 H5: nginx Collab-Location aktiv" || \
                    warn "#554 H5: nginx reload fehlgeschlagen — Config gepatcht aber nicht aktiv"
            else
                warn "#554 H5: nginx -t nach Collab-Patch fehlgeschlagen — Rollback aus ${_backup_collab}"
                cp "${_backup_collab}" "${_nginx_cfg}"
            fi
        fi
    fi

    # #895: ISO-Upload-Location in nginx-Config einfügen (client_max_body_size 0 für große ISOs)
    _nginx_cfg_iso=""
    for _cand in /etc/nginx/sites-available/hydrahive-console /etc/nginx/sites-enabled/hydrahive-console; do
        if [ -f "${_cand}" ] && [ ! -L "${_cand}" ]; then
            _nginx_cfg_iso="${_cand}"
            break
        elif [ -f "${_cand}" ]; then
            _nginx_cfg_iso=$(readlink -f "${_cand}")
            break
        fi
    done
    if [ -n "${_nginx_cfg_iso}" ] && ! grep -q "admin/vms/isos/upload" "${_nginx_cfg_iso}"; then
        info "#895: ISO-Upload-Location in nginx einfügen (client_max_body_size 0)"
        _backup_iso="${_nginx_cfg_iso}.bak-iso-$(date +%s)"
        cp "${_nginx_cfg_iso}" "${_backup_iso}"
        python3 <<PYEOF
import re, sys
p = "${_nginx_cfg_iso}"
txt = open(p).read()
if "admin/vms/isos/upload" in txt:
    sys.exit(0)
block = (
    "    # VM-Manager ISO-Upload (#895) — vor /api/ damit client_max_body_size greift\n"
    "    location = /api/admin/vms/isos/upload {\n"
    "        include /etc/nginx/snippets/hydrahive-security-headers.conf;\n"
    "        proxy_pass         http://127.0.0.1:8765/admin/vms/isos/upload;\n"
    "        proxy_http_version 1.1;\n"
    "        proxy_set_header   Host              \$host;\n"
    "        proxy_set_header   X-Real-IP         \$remote_addr;\n"
    "        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;\n"
    "        proxy_set_header   Connection        \"\";\n"
    "        proxy_read_timeout    7200s;\n"
    "        proxy_connect_timeout 5s;\n"
    "        client_max_body_size  0;\n"
    "        proxy_request_buffering off;\n"
    "    }\n\n"
)
new_txt, count = re.subn(r"(\s*location /api/ \{)", "\n" + block + r"\1", txt, count=1)
if count == 0:
    sys.exit("marker 'location /api/ {' nicht gefunden")
open(p, "w").write(new_txt)
PYEOF
        if nginx -t 2>&1 | head -5; then
            systemctl reload nginx 2>&1 | head -3 && \
                info "#895: nginx ISO-Upload-Location aktiv (client_max_body_size 0)" || \
                warn "#895: nginx reload fehlgeschlagen"
        else
            warn "#895: nginx -t fehlgeschlagen nach ISO-Patch — Rollback aus ${_backup_iso}"
            cp "${_backup_iso}" "${_nginx_cfg_iso}"
        fi
    fi

    # #908: Disk-Import-Upload-Location in nginx-Config einfügen
    _nginx_cfg_import=""
    for _cand in /etc/nginx/sites-available/hydrahive-console /etc/nginx/sites-enabled/hydrahive-console; do
        if [ -f "${_cand}" ] && [ ! -L "${_cand}" ]; then
            _nginx_cfg_import="${_cand}"
            break
        elif [ -f "${_cand}" ]; then
            _nginx_cfg_import=$(readlink -f "${_cand}")
            break
        fi
    done
    if [ -n "${_nginx_cfg_import}" ] && ! grep -q "admin/vms/import/upload" "${_nginx_cfg_import}"; then
        info "#908: Disk-Import-Upload-Location in nginx einfügen (client_max_body_size 0)"
        _backup_import="${_nginx_cfg_import}.bak-diskimport-$(date +%s)"
        cp "${_nginx_cfg_import}" "${_backup_import}"
        python3 <<PYEOF
import re, sys
p = "${_nginx_cfg_import}"
txt = open(p).read()
if "admin/vms/import/upload" in txt:
    sys.exit(0)
block = (
    "    # VM-Disk-Import (#908)\n"
    "    location = /api/admin/vms/import/upload {\n"
    "        include /etc/nginx/snippets/hydrahive-security-headers.conf;\n"
    "        proxy_pass         http://127.0.0.1:8765/admin/vms/import/upload;\n"
    "        proxy_http_version 1.1;\n"
    "        proxy_set_header   Host              \$host;\n"
    "        proxy_set_header   X-Real-IP         \$remote_addr;\n"
    "        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;\n"
    "        proxy_set_header   Connection        \"\";\n"
    "        proxy_read_timeout    7200s;\n"
    "        proxy_connect_timeout 5s;\n"
    "        client_max_body_size  0;\n"
    "        proxy_request_buffering off;\n"
    "    }\n\n"
)
new_txt, count = re.subn(r"(\s*location /api/ \{)", "\n" + block + r"\1", txt, count=1)
if count == 0:
    sys.exit("marker 'location /api/ {' nicht gefunden")
open(p, "w").write(new_txt)
PYEOF
        if nginx -t 2>&1 | head -5; then
            systemctl reload nginx 2>&1 | head -3 && \
                info "#908: nginx Disk-Import-Upload-Location aktiv (client_max_body_size 0)" || \
                warn "#908: nginx reload fehlgeschlagen"
        else
            warn "#908: nginx -t fehlgeschlagen nach Disk-Import-Patch — Rollback aus ${_backup_import}"
            cp "${_backup_import}" "${_nginx_cfg_import}"
        fi
    fi

    # #908-fix: client_body_timeout + proxy_send_timeout in Upload-Locations nachrüsten
    _nginx_cfg_to=""
    for _cand in /etc/nginx/sites-available/hydrahive-console /etc/nginx/sites-enabled/hydrahive-console; do
        if [ -f "${_cand}" ] && [ ! -L "${_cand}" ]; then _nginx_cfg_to="${_cand}"; break
        elif [ -f "${_cand}" ]; then _nginx_cfg_to=$(readlink -f "${_cand}"); break; fi
    done
    if [ -n "${_nginx_cfg_to}" ] && grep -q "admin/vms/import/upload\|admin/vms/isos/upload" "${_nginx_cfg_to}" && ! grep -q "client_body_timeout" "${_nginx_cfg_to}"; then
        info "#908-fix: client_body_timeout + proxy_send_timeout in Upload-Locations einfügen"
        _backup_to="${_nginx_cfg_to}.bak-timeout-$(date +%s)"
        cp "${_nginx_cfg_to}" "${_backup_to}"
        python3 <<'PYEOF'
import re, sys, os
p = os.environ.get("_nginx_cfg_to") or ""
# Pfad kommt via Shell-Heredoc-Expansion nicht durch — direkt suchen
import glob
for cand in ["/etc/nginx/sites-available/hydrahive-console", "/etc/nginx/sites-enabled/hydrahive-console"]:
    if os.path.exists(cand):
        p = os.path.realpath(cand); break
if not p:
    sys.exit("nginx-Config nicht gefunden")
txt = open(p).read()
if "client_body_timeout" in txt:
    sys.exit(0)
def patch_location(txt, marker):
    # Nach proxy_read_timeout 7200s; in der jeweiligen Upload-Location einfügen
    pattern = rf'(location = {re.escape(marker)} \{{[^}}]*?proxy_read_timeout\s+7200s;)'
    replacement = r'\1\n        proxy_send_timeout    7200s;\n        client_body_timeout   7200s;'
    return re.sub(pattern, replacement, txt, flags=re.DOTALL)
txt = patch_location(txt, "/api/admin/vms/isos/upload")
txt = patch_location(txt, "/api/admin/vms/import/upload")
open(p, "w").write(txt)
print("OK")
PYEOF
        if nginx -t 2>&1 | head -3; then
            systemctl reload nginx 2>&1 | head -2 && \
                info "#908-fix: nginx Upload-Timeouts aktualisiert (client_body_timeout 7200s)" || \
                warn "#908-fix: nginx reload fehlgeschlagen"
        else
            warn "#908-fix: nginx -t fehlgeschlagen — Rollback aus ${_backup_to}"
            cp "${_backup_to}" "${_nginx_cfg_to}"
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
    # #703: Auto-Update-Pfad (Timer + separater Service). Nur Dateien
    # aktualisieren — enable/disable-State bleibt wie er ist, damit
    # Admins die den Timer bewusst disabled haben nicht überrascht werden.
    if [ -f "${TMPDIR_BASE}/installer/hydrahive-autoupdate.service" ]; then
        cp "${TMPDIR_BASE}/installer/hydrahive-autoupdate.service" /etc/systemd/system/
        systemctl daemon-reload
        info "hydrahive-autoupdate.service aktualisiert"
    fi
    if [ -f "${TMPDIR_BASE}/installer/hydrahive-selfupdate.timer" ]; then
        cp "${TMPDIR_BASE}/installer/hydrahive-selfupdate.timer" /etc/systemd/system/
        systemctl daemon-reload
        info "hydrahive-selfupdate.timer aktualisiert"
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
