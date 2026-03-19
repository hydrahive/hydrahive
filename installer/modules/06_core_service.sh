#!/usr/bin/env bash
# OctopOS Installer - Modul 06: octopos-core Systemd-Service
# Installiert den Core in /opt/octopos/core, legt venv an,
# schreibt octopos-core.service und startet ihn.
# Idempotent: bereits laufender Service wird nach Update neugestartet.

CORE_DIR="${OCTOPOS_DIR}/core"
VENV_DIR="${OCTOPOS_DIR}/venv"
REPO_CORE="https://raw.githubusercontent.com/tilleulenspiegel/octopos/main/core"
SERVICE_NAME="octopos-core"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
OCTOPOS_USER="octopos"

info "Installiere octopos-core..."

# --- Verzeichnisse ---
mkdir -p "${CORE_DIR}/src/octopos_core" /agents /etc/octopos
chown -R "${OCTOPOS_USER}:${OCTOPOS_USER}" "${OCTOPOS_DIR}" /agents

# --- Python-Venv einrichten (idempotent) ---
if [ ! -f "${VENV_DIR}/bin/activate" ]; then
    info "Lege Python-venv an: ${VENV_DIR}"
    python3 -m venv "${VENV_DIR}"
    success "venv angelegt"
else
    info "venv bereits vorhanden"
fi

"${VENV_DIR}/bin/pip" install -q --upgrade pip
"${VENV_DIR}/bin/pip" install -q \
    fastapi "uvicorn[standard]" pydantic pyyaml watchdog litellm
success "Python-Abhängigkeiten installiert"

# --- Core-Quellcode aus Repo holen ---
info "Lade octopos-core Quellcode..."
for module in __init__.py agent_config.py agent_discovery.py agent_runtime.py main.py; do
    curl -sL --fail \
        "https://raw.githubusercontent.com/tilleulenspiegel/octopos/main/core/src/octopos_core/${module}" \
        -o "${CORE_DIR}/src/octopos_core/${module}"
done

# pyproject.toml für lokale Installation
curl -sL --fail \
    "https://raw.githubusercontent.com/tilleulenspiegel/octopos/main/core/pyproject.toml" \
    -o "${CORE_DIR}/pyproject.toml"

"${VENV_DIR}/bin/pip" install -q -e "${CORE_DIR}"
success "octopos-core installiert (${CORE_DIR})"

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
