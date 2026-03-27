#!/usr/bin/env bash
# HydraHive Installer — Modul 11: AgentLink (native, kein Docker)
# Installiert PostgreSQL, Redis und AgentLink als native systemd-Services.
# Idempotent: bereits vorhandene Installationen werden sicher übersprungen/aktualisiert.

AGENTLINK_DIR="${HYDRAHIVE_DIR}/agentlink"
AGENTLINK_PORT="${AGENTLINK_PORT:-8010}"
AGENTLINK_CONFIG="/etc/hydrahive/agentlink.json"
AGENTLINK_DATA="/var/lib/hydrahive/agentlink"
AGENTLINK_DB_PASS="${AGENTLINK_DB_PASS:-$(openssl rand -hex 16)}"
AGENTLINK_REPO="https://github.com/tilleulenspiegel/agentlink.git"
HYDRAHIVE_USER="hydrahive"

info "Installiere AgentLink (nativ)..."

# --- DB-Passwort aus bestehender Config lesen (idempotent) ---
if [ -f "${AGENTLINK_CONFIG}" ]; then
    _existing=$(python3 -c "import json; d=json.load(open('${AGENTLINK_CONFIG}')); print(d.get('db_password',''))" 2>/dev/null || true)
    [ -n "${_existing}" ] && AGENTLINK_DB_PASS="${_existing}"
fi

# ─── 1. PostgreSQL ───────────────────────────────────────────────────────────
info "PostgreSQL..."
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql postgresql-contrib
systemctl enable postgresql --now
systemctl is-active postgresql -q || { error "PostgreSQL startet nicht"; }

# DB + User anlegen (idempotent via DO $$ IF NOT EXISTS)
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='agentlink'" \
    | grep -q 1 || sudo -u postgres psql -c \
    "CREATE USER agentlink WITH PASSWORD '${AGENTLINK_DB_PASS}';"

sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='agentlink'" \
    | grep -q 1 || sudo -u postgres psql -c \
    "CREATE DATABASE agentlink OWNER agentlink;"

sudo -u postgres psql -c \
    "GRANT ALL PRIVILEGES ON DATABASE agentlink TO agentlink;" -q

success "PostgreSQL bereit"

# ─── 2. Redis ────────────────────────────────────────────────────────────────
info "Redis..."
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq redis-server
systemctl enable redis-server --now
systemctl is-active redis-server -q || { error "Redis startet nicht"; }
success "Redis bereit"

# ─── 3. AgentLink-Repo ───────────────────────────────────────────────────────
if [ -d "${AGENTLINK_DIR}/.git" ]; then
    info "AgentLink-Repo aktualisieren..."
    git -C "${AGENTLINK_DIR}" pull -q || warn "AgentLink-Update fehlgeschlagen — fahre mit vorhandenem Stand fort"
    success "AgentLink-Repo aktuell"
else
    info "Klone AgentLink-Repo..."
    if ! git clone -q "${AGENTLINK_REPO}" "${AGENTLINK_DIR}" 2>/dev/null; then
        warn "AgentLink-Repo nicht erreichbar — AgentLink wird übersprungen"
        warn "AgentLink kann später manuell installiert werden: git clone ${AGENTLINK_REPO} ${AGENTLINK_DIR}"
        return 0
    fi
    success "AgentLink geklont"
fi
chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${AGENTLINK_DIR}"

# ─── 4. Python venv + Abhängigkeiten ─────────────────────────────────────────
info "Python-Abhängigkeiten installieren (dauert etwas)..."
python3 -m venv "${AGENTLINK_DIR}/venv"
"${AGENTLINK_DIR}/venv/bin/pip" install -q --upgrade pip
"${AGENTLINK_DIR}/venv/bin/pip" install -q -r "${AGENTLINK_DIR}/backend/requirements.txt"
success "Python-Abhängigkeiten installiert"

# ─── 5. Datenverzeichnis + .env ──────────────────────────────────────────────
mkdir -p "${AGENTLINK_DATA}/chromadb"
chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${AGENTLINK_DATA}"

cat > "${AGENTLINK_DIR}/backend/.env" << ENV
DATABASE_URL=postgresql://agentlink:${AGENTLINK_DB_PASS}@localhost:5432/agentlink
REDIS_URL=redis://localhost:6379
CHROMA_HOST=localhost
CHROMA_PORT=8011
ENV
chown "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${AGENTLINK_DIR}/backend/.env"
chmod 640 "${AGENTLINK_DIR}/backend/.env"

# ─── 6. DB-Schema initialisieren ─────────────────────────────────────────────
info "Datenbank-Schema..."
pushd "${AGENTLINK_DIR}/backend" > /dev/null
DATABASE_URL="postgresql://agentlink:${AGENTLINK_DB_PASS}@localhost:5432/agentlink" \
    "${AGENTLINK_DIR}/venv/bin/python" -c \
    "from database import Base, engine; Base.metadata.create_all(engine); print('OK')" \
    && success "DB-Schema bereit" \
    || warn "DB-Schema konnte nicht initialisiert werden — ggf. beim ersten Start nachholen"
popd > /dev/null

# ─── 7. ChromaDB-Service ─────────────────────────────────────────────────────
cat > /etc/systemd/system/hydrahive-chromadb.service << UNIT
[Unit]
Description=HydraHive ChromaDB (Vektordatenbank)
After=network.target

[Service]
Type=simple
User=${HYDRAHIVE_USER}
ExecStart=${AGENTLINK_DIR}/venv/bin/chroma run \
    --host 127.0.0.1 \
    --port 8011 \
    --path ${AGENTLINK_DATA}/chromadb
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

# ─── 8. AgentLink-Backend-Service ────────────────────────────────────────────
cat > /etc/systemd/system/hydrahive-agentlink.service << UNIT
[Unit]
Description=HydraHive AgentLink Hub
After=network.target postgresql.service redis-server.service hydrahive-chromadb.service
Requires=postgresql.service redis-server.service

[Service]
Type=simple
User=${HYDRAHIVE_USER}
WorkingDirectory=${AGENTLINK_DIR}/backend
EnvironmentFile=${AGENTLINK_DIR}/backend/.env
ExecStart=${AGENTLINK_DIR}/venv/bin/uvicorn main:app \
    --host 127.0.0.1 \
    --port ${AGENTLINK_PORT} \
    --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable hydrahive-chromadb hydrahive-agentlink
systemctl start hydrahive-chromadb
sleep 2
systemctl start hydrahive-agentlink

# ─── 9. Warten bis Backend erreichbar ────────────────────────────────────────
info "Warte auf AgentLink Backend..."
for i in $(seq 1 15); do
    if curl -sf "http://127.0.0.1:${AGENTLINK_PORT}/health" &>/dev/null; then
        success "AgentLink Backend erreichbar auf Port ${AGENTLINK_PORT}"
        break
    fi
    sleep 2
    if [ "$i" -eq 15 ]; then
        warn "AgentLink Backend nicht erreichbar nach 30s"
        warn "Logs: journalctl -u hydrahive-agentlink -n 30"
    fi
done

# ─── 10. Config für HydraHive-Core schreiben ─────────────────────────────────
if [ ! -f "${AGENTLINK_CONFIG}" ]; then
    cat > "${AGENTLINK_CONFIG}" << CFG
{
  "base_url": "http://127.0.0.1:${AGENTLINK_PORT}",
  "ws_url": "ws://127.0.0.1:${AGENTLINK_PORT}",
  "db_password": "${AGENTLINK_DB_PASS}",
  "enabled": true
}
CFG
    chmod 640 "${AGENTLINK_CONFIG}"
    chown "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${AGENTLINK_CONFIG}" 2>/dev/null || true
    success "AgentLink-Config: ${AGENTLINK_CONFIG}"
else
    success "AgentLink-Config bereits vorhanden"
fi

info "AgentLink URL:  http://127.0.0.1:${AGENTLINK_PORT}"
info "AgentLink Docs: http://127.0.0.1:${AGENTLINK_PORT}/docs"
success "AgentLink installiert"
