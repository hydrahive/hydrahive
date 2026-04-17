#!/usr/bin/env bash
# HydraHive Installer - Modul 06: hydrahive-core Systemd-Service
# Installiert den Core in /opt/hydrahive/core, legt venv an,
# schreibt hydrahive-core.service und startet ihn.
# Idempotent: bereits laufender Service wird nach Update neugestartet.

CORE_DIR="${HYDRAHIVE_DIR}/core"
VENV_DIR="${HYDRAHIVE_DIR}/venv"
SERVICE_NAME="hydrahive-core"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
HYDRAHIVE_USER="hydrahive"

info "Installiere hydrahive-core..."

# --- System-User anlegen (idempotent) ---
if ! id "${HYDRAHIVE_USER}" &>/dev/null; then
    useradd -r -s /bin/false -d "${HYDRAHIVE_DIR}" "${HYDRAHIVE_USER}"
    success "System-User '${HYDRAHIVE_USER}' angelegt"
else
    success "System-User '${HYDRAHIVE_USER}' bereits vorhanden"
fi

# --- Home-Verzeichnis für Model-Cache (A-MEM / sentence-transformers) ---
if [ ! -d "/home/${HYDRAHIVE_USER}" ]; then
    mkdir -p "/home/${HYDRAHIVE_USER}"
    chown "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "/home/${HYDRAHIVE_USER}"
    chmod 755 "/home/${HYDRAHIVE_USER}"
    success "Home-Verzeichnis /home/${HYDRAHIVE_USER} angelegt"
fi

# --- Passwordless sudo für Extension-Manager (sudo -n braucht NOPASSWD) ---
# #295: sudoers auf minimale Befehle einschränken — keine Wildcards wo vermeidbar
cat > /etc/sudoers.d/hydrahive << SUDOEOF
# Service-Management (nur hydrahive-* Services + nginx/smbd reload)
hydrahive ALL=(root) NOPASSWD: /bin/systemctl restart hydrahive-core
hydrahive ALL=(root) NOPASSWD: /bin/systemctl stop hydrahive-core
hydrahive ALL=(root) NOPASSWD: /bin/systemctl start hydrahive-core
hydrahive ALL=(root) NOPASSWD: /bin/systemctl restart hydrahive-*
hydrahive ALL=(root) NOPASSWD: /bin/systemctl stop hydrahive-*
hydrahive ALL=(root) NOPASSWD: /bin/systemctl start hydrahive-*
hydrahive ALL=(root) NOPASSWD: /bin/systemctl reload smbd
hydrahive ALL=(root) NOPASSWD: /bin/systemctl reload nginx

# Projekt-Isolation (nur /projects/ Pfad, kein Path-Traversal)
hydrahive ALL=(root) NOPASSWD: /usr/sbin/useradd --system --no-create-home -d /projects/* -s /usr/sbin/nologin *
hydrahive ALL=(root) NOPASSWD: /usr/sbin/userdel --remove *
hydrahive ALL=(root) NOPASSWD: /bin/chown -R hydrahive\:hydrahive /projects/*
hydrahive ALL=(root) NOPASSWD: /bin/chmod 750 /projects/*
hydrahive ALL=(root) NOPASSWD: /bin/mkdir -p /projects/*

# Samba (nur reload + Passwort-Operationen)
hydrahive ALL=(root) NOPASSWD: /usr/bin/smbpasswd -a *
hydrahive ALL=(root) NOPASSWD: /usr/bin/smbpasswd -x *
hydrahive ALL=(root) NOPASSWD: /usr/bin/smbcontrol smbd reload-config

# Debugging (nur hydrahive-* Unit-Logs, nicht alle)
hydrahive ALL=(root) NOPASSWD: /bin/journalctl -u hydrahive-*
hydrahive ALL=(root) NOPASSWD: /bin/journalctl -u nginx*

# Port-Freigabe
hydrahive ALL=(root) NOPASSWD: /usr/bin/fuser -k 8765/tcp

# UFW Status (readonly)
hydrahive ALL=(root) NOPASSWD: /usr/sbin/ufw status numbered

Defaults\:hydrahive !requiretty
SUDOEOF
chmod 440 /etc/sudoers.d/hydrahive

# --- Verzeichnisse ---
mkdir -p "${CORE_DIR}/src/hydrahive_core" /agents /projects /etc/hydrahive
chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${HYDRAHIVE_DIR}" /agents /projects
# /etc/hydrahive braucht hydrahive-Schreibrechte. Der Core laeuft als
# User hydrahive und legt beim ersten Start weitere Runtime-Config-Dateien
# an (z.B. migrations.json, master.key, groups.json).
chown root:${HYDRAHIVE_USER} /etc/hydrahive
chmod 770 /etc/hydrahive

# #690: Feature-Verzeichnisse vorab anlegen mit korrekten Rechten.
# /etc/hydrahive/{servers,skill_packages} — Daten: 0o750 root:hydrahive (analog /etc/hydrahive).
# /etc/hydrahive/{server_keys,wks_keys}   — PRIVATE KEYS: 0o700 hydrahive:hydrahive.
# /var/lib/hydrahive/{worktrees,users}    — Runtime-State: 0o750 hydrahive:hydrahive.
# /opt/hydrahive/{skills/catalog,backups} — App-Daten: skills/catalog 0o755 admin-befüllt,
#                                          backups 0o750 hydrahive:hydrahive.
for _dir in servers skill_packages; do
    mkdir -p "/etc/hydrahive/${_dir}"
    chown root:${HYDRAHIVE_USER} "/etc/hydrahive/${_dir}"
    chmod 750 "/etc/hydrahive/${_dir}"
done
for _dir in server_keys wks_keys; do
    mkdir -p "/etc/hydrahive/${_dir}"
    chown "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "/etc/hydrahive/${_dir}"
    chmod 700 "/etc/hydrahive/${_dir}"
done
# #687/#704: shared Helper — identisch von update.sh vor Core-Restart genutzt.
_RUNTIME_HELPER="${HYDRAHIVE_DIR:-/opt/hydrahive}/installer/lib/ensure_runtime_dirs.sh"
if [ ! -f "${_RUNTIME_HELPER}" ]; then
    # Frischer Install: Helper liegt noch im geklonten Installer-Tree, nicht
    # unter HYDRAHIVE_DIR. Resolve relativ zum Modul.
    _RUNTIME_HELPER="$(dirname "${BASH_SOURCE[0]}")/../lib/ensure_runtime_dirs.sh"
fi
# shellcheck source=../lib/ensure_runtime_dirs.sh
HYDRAHIVE_USER="${HYDRAHIVE_USER}" HYDRAHIVE_GROUP="${HYDRAHIVE_USER}" source "${_RUNTIME_HELPER}"
mkdir -p "${HYDRAHIVE_DIR}/skills/catalog"
chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${HYDRAHIVE_DIR}/skills"
chmod 755 "${HYDRAHIVE_DIR}/skills" "${HYDRAHIVE_DIR}/skills/catalog"
mkdir -p "${HYDRAHIVE_DIR}/backups"
chown "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${HYDRAHIVE_DIR}/backups"
chmod 750 "${HYDRAHIVE_DIR}/backups"

# --- Konfig-Dateien voranlegen (hydrahive-core braucht Schreibrechte) ---
for _f in jwt_secret internal_secret llm_env llm_config.json gitea_config.json users.json admin_credentials migrations.json; do
    _path="/etc/hydrahive/${_f}"
    if [ ! -f "${_path}" ]; then
        touch "${_path}"
    fi
    chown "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${_path}"
    chmod 600 "${_path}"
done
# users.json braucht valides JSON als Startwert
if [ ! -s /etc/hydrahive/users.json ]; then
    echo '{}' > /etc/hydrahive/users.json
fi

# --- Console-Admin-Passwort (idempotent) ---
# Aus Env-Variable, vorhandenem Eintrag oder neu generiert
CRED_FILE="/etc/hydrahive/admin_credentials"
EXISTING_CONSOLE_PASS=$(grep -E '^console_password=' "${CRED_FILE}" 2>/dev/null | cut -d= -f2-) || true
if [ -n "${ADMIN_PASSWORD:-}" ]; then
    CONSOLE_PASS="${ADMIN_PASSWORD}"
elif [ -n "${EXISTING_CONSOLE_PASS:-}" ]; then
    CONSOLE_PASS="${EXISTING_CONSOLE_PASS}"
else
    _raw="$(openssl rand -base64 32)"
    _clean="${_raw//[\/+=]/}"
    CONSOLE_PASS="${_clean:0:24}"
fi
# Nur schreiben wenn noch nicht vorhanden
if ! grep -q '^console_password=' "${CRED_FILE}" 2>/dev/null; then
    echo "console_password=${CONSOLE_PASS}" >> "${CRED_FILE}"
fi
export CONSOLE_PASS

# --- Core-Quellcode kopieren (Installer läuft aus dem geklonten Repo) ---
info "Kopiere hydrahive-core Quellcode..."
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO_CORE="${REPO_ROOT}/core"

if [ -d "${REPO_CORE}/src/hydrahive_core" ]; then
    cp -r "${REPO_CORE}/src" "${CORE_DIR}/"
    cp "${REPO_CORE}/pyproject.toml" "${CORE_DIR}/"
    [ -d "${REPO_CORE}/tests" ] && cp -r "${REPO_CORE}/tests" "${CORE_DIR}/"
    success "hydrahive-core Quellcode bereit (${CORE_DIR})"
else
    error "core/src nicht gefunden (${REPO_CORE}) — Installer muss aus dem geklonten Repo ausgefuehrt werden"
fi

# --- System-Agenten anlegen (idempotent — vorhandene soul.md/memory bleibt erhalten) ---
REPO_AGENTS="${REPO_ROOT}/agents"
if [ -d "${REPO_AGENTS}" ]; then
    for _agent_src in "${REPO_AGENTS}"/*/; do
        _agent_id="$(basename "${_agent_src}")"
        _agent_dst="/agents/${_agent_id}"
        mkdir -p "${_agent_dst}/memory"
        # agent.yaml und soul.md immer aktualisieren
        [ -f "${_agent_src}/agent.yaml" ] && cp "${_agent_src}/agent.yaml" "${_agent_dst}/agent.yaml"
        [ -f "${_agent_src}/soul.md"    ] && cp "${_agent_src}/soul.md"    "${_agent_dst}/soul.md"
        chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${_agent_dst}"
    done
    success "System-Agenten bereit (/agents)"
fi

# --- Python-Venv einrichten (idempotent) ---
if [ ! -f "${VENV_DIR}/bin/activate" ]; then
    info "Lege Python-venv an: ${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
    success "venv angelegt"
else
    info "venv bereits vorhanden"
fi

"${VENV_DIR}/bin/pip" install -q --upgrade pip

# pyproject.toml wurde bereits kopiert — immer verwenden
"${VENV_DIR}/bin/pip" install -q -e "${CORE_DIR}" \
    || error "pip install -e fehlgeschlagen — pruefe ${CORE_DIR}/pyproject.toml"
"${VENV_DIR}/bin/pip" install -q pytest pytest-asyncio httpx \
    || warn "pytest konnte nicht installiert werden — Unit-Tests nicht verfügbar"
success "Python-Abhängigkeiten aus pyproject.toml installiert"

chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${CORE_DIR}" "${VENV_DIR}"

# --- Systemd-Unit ---
cat > "${SERVICE_FILE}" << UNIT
[Unit]
Description=HydraHive Core Runtime
After=network.target hydrahive-conduwuit.service
Requires=hydrahive-conduwuit.service
Documentation=https://github.com/hydrahive/hydrahive

[Service]
Type=simple
User=${HYDRAHIVE_USER}
Group=${HYDRAHIVE_USER}
WorkingDirectory=${CORE_DIR}
ExecStart=${VENV_DIR}/bin/uvicorn hydrahive_core.main:app --host 127.0.0.1 --port 8765 --no-access-log --timeout-graceful-shutdown 5
Restart=always
RestartSec=3
TimeoutStopSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=${CORE_DIR}/src
EnvironmentFile=-/etc/hydrahive/llm_env

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

if systemctl is-active --quiet "${SERVICE_NAME}"; then
    systemctl restart "${SERVICE_NAME}"
    success "hydrahive-core neugestartet"
else
    systemctl start "${SERVICE_NAME}"
    success "hydrahive-core gestartet"
fi

# Health-Check
HEALTH_OK=0
for i in $(seq 1 30); do
    sleep 2
    if curl -sf "http://127.0.0.1:8765/health" &>/dev/null; then
        success "hydrahive-core antwortet auf http://127.0.0.1:8765"
        HEALTH_OK=1
        break
    fi
    if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
        warn "hydrahive-core ist nicht aktiv — letzte Logs:"
        journalctl -u "${SERVICE_NAME}" -n 30 --no-pager || true
        break
    fi
    info "Warte auf hydrahive-core... ($i/30)"
done
if [ "${HEALTH_OK}" -eq 0 ]; then
    warn "hydrahive-core antwortet nicht — pruefe: journalctl -u ${SERVICE_NAME} -n 30"
fi

# --- sysinfo-Agent Memory initial befüllen ---
SYSINFO_SCRIPT="${REPO_ROOT}/installer/modules/17_sysinfo_scan.sh"
if [ -f "${SYSINFO_SCRIPT}" ]; then
    bash "${SYSINFO_SCRIPT}" \
        && success "sysinfo-Memory initial befüllt" \
        || warn "sysinfo-Scan fehlgeschlagen — wird übersprungen"
fi
