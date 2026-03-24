#!/usr/bin/env bash
# HydraHive Installer — Modul 11: AgentLink
# Installiert AgentLink (Docker Compose) als zentralen Agent-Koordinations-Hub.
# Idempotent: bereits laufende Container werden neu gestartet.

AGENTLINK_DIR="${HYDRAHIVE_DIR}/agentlink"
AGENTLINK_PORT="${AGENTLINK_PORT:-8010}"
AGENTLINK_CONFIG="/etc/hydrahive/agentlink.json"
AGENTLINK_DB_PASS="${AGENTLINK_DB_PASS:-$(openssl rand -hex 16)}"

info "Installiere AgentLink..."

# --- Docker installieren (idempotent) ---
if ! command -v docker &>/dev/null; then
    info "Installiere Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    success "Docker installiert"
else
    success "Docker bereits vorhanden ($(docker --version | cut -d' ' -f3 | tr -d ','))"
fi

# --- Docker Compose Plugin prüfen ---
if ! docker compose version &>/dev/null; then
    info "Installiere Docker Compose Plugin..."
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker-compose-plugin
    success "Docker Compose Plugin installiert"
else
    success "Docker Compose bereits vorhanden"
fi

# --- AgentLink-Repo klonen / updaten ---
if [ -d "${AGENTLINK_DIR}/.git" ]; then
    info "AgentLink-Repo updaten..."
    git -C "${AGENTLINK_DIR}" pull -q
    success "AgentLink-Repo aktuell"
else
    info "Klone AgentLink-Repo..."
    git clone -q https://github.com/tilleulenspiegel/agentlink.git "${AGENTLINK_DIR}"
    success "AgentLink geklont"
fi

# --- DB-Passwort aus bestehender Config lesen (idempotent) ---
if [ -f "${AGENTLINK_CONFIG}" ]; then
    EXISTING_PASS=$(python3 -c "import json; d=json.load(open('${AGENTLINK_CONFIG}')); print(d.get('db_password',''))" 2>/dev/null || echo "")
    if [ -n "${EXISTING_PASS}" ]; then
        AGENTLINK_DB_PASS="${EXISTING_PASS}"
    fi
fi

# --- .env für Docker Compose schreiben ---
cat > "${AGENTLINK_DIR}/.env" << ENV
POSTGRES_PASSWORD=${AGENTLINK_DB_PASS}
POSTGRES_DB=agentlink
AGENTLINK_PORT=${AGENTLINK_PORT}
ENV

# --- docker-compose.yml anpassen: Port auf AGENTLINK_PORT ---
# Backend-Port von 8000 auf konfigurierten Port mappen
if grep -q '"8000:8000"' "${AGENTLINK_DIR}/docker-compose.yml" 2>/dev/null; then
    sed -i "s/\"8000:8000\"/\"${AGENTLINK_PORT}:8000\"/g" "${AGENTLINK_DIR}/docker-compose.yml"
fi

# --- Container starten ---
info "Starte AgentLink-Container..."
cd "${AGENTLINK_DIR}"
docker compose up -d --remove-orphans postgres redis backend 2>&1 | grep -E "Started|Running|Created|Error" || true

# --- Warten bis Backend erreichbar ---
info "Warte auf AgentLink Backend..."
for i in $(seq 1 20); do
    if curl -sf "http://127.0.0.1:${AGENTLINK_PORT}/health" &>/dev/null; then
        success "AgentLink Backend erreichbar auf Port ${AGENTLINK_PORT}"
        break
    fi
    sleep 2
    if [ "$i" -eq 20 ]; then
        warn "AgentLink Backend nicht erreichbar nach 40s — prüfe: docker compose -f ${AGENTLINK_DIR}/docker-compose.yml logs backend"
    fi
done

# --- Config speichern ---
if [ ! -f "${AGENTLINK_CONFIG}" ]; then
    cat > "${AGENTLINK_CONFIG}" << CFG
{
  "url": "http://127.0.0.1:${AGENTLINK_PORT}",
  "db_password": "${AGENTLINK_DB_PASS}",
  "enabled": true
}
CFG
    chmod 640 "${AGENTLINK_CONFIG}"
    chown hydrahive:hydrahive "${AGENTLINK_CONFIG}" 2>/dev/null || true
    success "AgentLink-Config geschrieben: ${AGENTLINK_CONFIG}"
else
    success "AgentLink-Config bereits vorhanden"
fi

# --- Systemd-Override damit AgentLink beim Boot startet ---
DOCKER_OVERRIDE="/etc/systemd/system/hydrahive-agentlink.service"
if [ ! -f "${DOCKER_OVERRIDE}" ]; then
    cat > "${DOCKER_OVERRIDE}" << UNIT
[Unit]
Description=HydraHive AgentLink Hub
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${AGENTLINK_DIR}
ExecStart=/usr/bin/docker compose up -d --remove-orphans postgres redis backend
ExecStop=/usr/bin/docker compose stop postgres redis backend
TimeoutStartSec=60

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable hydrahive-agentlink
    success "Systemd-Service hydrahive-agentlink aktiviert"
fi

info "AgentLink URL:  http://127.0.0.1:${AGENTLINK_PORT}"
info "AgentLink Docs: http://127.0.0.1:${AGENTLINK_PORT}/docs"
success "AgentLink installiert"
