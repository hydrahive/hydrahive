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

# --- Verzeichnisse ---
mkdir -p "${CORE_DIR}/src/hydrahive_core" /agents /projects /etc/hydrahive
chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${HYDRAHIVE_DIR}" /agents /projects
# /etc/hydrahive braucht hydrahive-Schreibrechte (jwt_secret, users.json etc.)
chown root:${HYDRAHIVE_USER} /etc/hydrahive
chmod 770 /etc/hydrahive

# --- Konfig-Dateien voranlegen (hydrahive-core braucht Schreibrechte) ---
for _f in jwt_secret llm_env llm_config.json gitea_config.json users.json admin_credentials; do
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
EXISTING_CONSOLE_PASS=$(grep -E '^console_password=' "${CRED_FILE}" 2>/dev/null | cut -d= -f2-)
if [ -n "${ADMIN_PASSWORD:-}" ]; then
    CONSOLE_PASS="${ADMIN_PASSWORD}"
elif [ -n "${EXISTING_CONSOLE_PASS:-}" ]; then
    CONSOLE_PASS="${EXISTING_CONSOLE_PASS}"
else
    CONSOLE_PASS="$(openssl rand -base64 18 | tr -d '/+=' | head -c 24)"
fi
# Nur schreiben wenn noch nicht vorhanden
if ! grep -q '^console_password=' "${CRED_FILE}" 2>/dev/null; then
    echo "console_password=${CONSOLE_PASS}" >> "${CRED_FILE}"
fi
export CONSOLE_PASS

# --- Core-Quellcode kopieren (Installer läuft aus dem geklonten Repo) ---
info "Kopiere hydrahive-core Quellcode..."
REPO_CORE="$(dirname "${BASH_SOURCE[0]}")/../../core"
REPO_CORE="$(realpath "${REPO_CORE}" 2>/dev/null || echo "${REPO_CORE}")"

if [ -d "${REPO_CORE}/src/hydrahive_core" ]; then
    cp -r "${REPO_CORE}/src" "${CORE_DIR}/"
    cp "${REPO_CORE}/pyproject.toml" "${CORE_DIR}/"
    success "hydrahive-core Quellcode bereit (${CORE_DIR})"
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
ExecStart=${VENV_DIR}/bin/uvicorn hydrahive_core.main:app --host 127.0.0.1 --port 8765 --no-access-log
Restart=always
RestartSec=5
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
for i in 1 2 3; do
    sleep 3
    if curl -sf "http://127.0.0.1:8765/health" &>/dev/null; then
        success "hydrahive-core antwortet auf http://127.0.0.1:8765"
        HEALTH_OK=1
        break
    fi
    info "Warte auf hydrahive-core... ($i/3)"
done
if [ "${HEALTH_OK}" -eq 0 ]; then
    warn "hydrahive-core antwortet nicht — pruefe: journalctl -u ${SERVICE_NAME} -n 30"
fi
