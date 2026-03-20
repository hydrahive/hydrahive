#!/usr/bin/env bash
# OctopOS Installer - Modul 06: octopos-core Systemd-Service
# Installiert den Core in /opt/octopos/core, legt venv an,
# schreibt octopos-core.service und startet ihn.
# Idempotent: bereits laufender Service wird nach Update neugestartet.

CORE_DIR="${OCTOPOS_DIR}/core"
VENV_DIR="${OCTOPOS_DIR}/venv"
SERVICE_NAME="octopos-core"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
OCTOPOS_USER="octopos"

info "Installiere octopos-core..."

# --- System-User anlegen (idempotent) ---
if ! id "${OCTOPOS_USER}" &>/dev/null; then
    useradd -r -s /bin/false -d "${OCTOPOS_DIR}" "${OCTOPOS_USER}"
    success "System-User '${OCTOPOS_USER}' angelegt"
else
    success "System-User '${OCTOPOS_USER}' bereits vorhanden"
fi

# --- Verzeichnisse ---
mkdir -p "${CORE_DIR}/src/octopos_core" /agents /projects /etc/octopos
chown -R "${OCTOPOS_USER}:${OCTOPOS_USER}" "${OCTOPOS_DIR}" /agents /projects
# /etc/octopos braucht octopos-Schreibrechte (jwt_secret, users.json etc.)
chown root:${OCTOPOS_USER} /etc/octopos
chmod 770 /etc/octopos

# --- Konfig-Dateien voranlegen (octopos-core braucht Schreibrechte) ---
for _f in jwt_secret llm_env llm_config.json users.json admin_credentials; do
    _path="/etc/octopos/${_f}"
    if [ ! -f "${_path}" ]; then
        touch "${_path}"
    fi
    chown "${OCTOPOS_USER}:${OCTOPOS_USER}" "${_path}"
    chmod 600 "${_path}"
done
# users.json braucht valides JSON als Startwert
if [ ! -s /etc/octopos/users.json ]; then
    echo '{}' > /etc/octopos/users.json
fi

# --- Core-Quellcode kopieren (Installer läuft aus dem geklonten Repo) ---
info "Kopiere octopos-core Quellcode..."
REPO_CORE="$(dirname "${BASH_SOURCE[0]}")/../../core"
REPO_CORE="$(realpath "${REPO_CORE}" 2>/dev/null || echo "${REPO_CORE}")"

if [ -d "${REPO_CORE}/src/octopos_core" ]; then
    cp -r "${REPO_CORE}/src" "${CORE_DIR}/"
    cp "${REPO_CORE}/pyproject.toml" "${CORE_DIR}/"
    success "octopos-core Quellcode bereit (${CORE_DIR})"
else
    error "core/src nicht gefunden (${REPO_CORE}) — Installer muss aus dem geklonten Repo ausgefuehrt werden"
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
success "Python-Abhängigkeiten aus pyproject.toml installiert"

chown -R "${OCTOPOS_USER}:${OCTOPOS_USER}" "${CORE_DIR}" "${VENV_DIR}"

# --- Systemd-Unit ---
cat > "${SERVICE_FILE}" << UNIT
[Unit]
Description=OctopOS Core Runtime
After=network.target octopos-conduwuit.service
Requires=octopos-conduwuit.service
Documentation=https://github.com/tilleulenspiegel/octopos

[Service]
Type=simple
User=${OCTOPOS_USER}
Group=${OCTOPOS_USER}
WorkingDirectory=${CORE_DIR}
ExecStart=${VENV_DIR}/bin/uvicorn octopos_core.main:app --host 127.0.0.1 --port 8765 --no-access-log
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=${CORE_DIR}/src
EnvironmentFile=-/etc/octopos/llm_env

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

if systemctl is-active --quiet "${SERVICE_NAME}"; then
    systemctl restart "${SERVICE_NAME}"
    success "octopos-core neugestartet"
else
    systemctl start "${SERVICE_NAME}"
    success "octopos-core gestartet"
fi

# Health-Check
HEALTH_OK=0
for i in 1 2 3; do
    sleep 3
    if curl -sf "http://127.0.0.1:8765/health" &>/dev/null; then
        success "octopos-core antwortet auf http://127.0.0.1:8765"
        HEALTH_OK=1
        break
    fi
    info "Warte auf octopos-core... ($i/3)"
done
if [ "${HEALTH_OK}" -eq 0 ]; then
    warn "octopos-core antwortet nicht — pruefe: journalctl -u ${SERVICE_NAME} -n 30"
fi
