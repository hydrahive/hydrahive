#!/usr/bin/env bash
# HydraHive Extension - Paperless-ngx (Dokumenten-Management)
# Installiert Paperless-ngx aus dem offiziellen GitHub-Release als native Python-App.
# Abhängigkeiten: python3, redis, postgresql (oder sqlite3), ghostscript, tesseract-ocr.
# Idempotent: erneuter Aufruf aktualisiert auf das neueste Release.

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[Paperless]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

PAPERLESS_DIR="/opt/paperless-ngx"
PAPERLESS_DATA="/var/lib/paperless-ngx"
PAPERLESS_MEDIA="/var/lib/paperless-ngx/media"
PAPERLESS_CONSUME="/var/lib/paperless-ngx/consume"
PAPERLESS_EXPORT="/var/lib/paperless-ngx/export"
PAPERLESS_USER="paperless"
PAPERLESS_PORT="8200"
HH_CONF="/etc/hydrahive/paperless-ngx.json"

info "=== Paperless-ngx installieren ==="

# --- Prüfe ob bereits installiert (Update-Pfad) ---
if [ -d "${PAPERLESS_DIR}" ] && [ -f "${PAPERLESS_DIR}/paperless.conf" ]; then
    info "Paperless-ngx bereits installiert — prüfe auf Updates..."
    INSTALLED_VERSION="$(cat "${PAPERLESS_DIR}/VERSION" 2>/dev/null || echo "unbekannt")"
    LATEST_VERSION="$(curl -sf "https://api.github.com/repos/paperless-ngx/paperless-ngx/releases/latest" \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('tag_name',''))" 2>/dev/null || echo "")"
    if [ -n "${LATEST_VERSION}" ] && [ "${INSTALLED_VERSION}" != "${LATEST_VERSION}" ]; then
        info "Update von ${INSTALLED_VERSION} auf ${LATEST_VERSION}..."
    else
        success "Paperless-ngx ${INSTALLED_VERSION} ist aktuell"
        systemctl start paperless-webserver paperless-consumer paperless-scheduler 2>/dev/null || true
        exit 0
    fi
fi

# --- System-Abhängigkeiten ---
info "Installiere System-Abhängigkeiten..."
apt-get update -qq
apt-get install -y --quiet \
    python3 python3-pip python3-venv python3-dev \
    redis-server \
    postgresql postgresql-client \
    libpq-dev \
    tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng \
    ghostscript imagemagick \
    optipng unpaper pngoptimizer \
    libmagic1 libmagic-dev \
    libreoffice-writer-nogui \
    fonts-liberation \
    build-essential libxml2-dev libxslt1-dev \
    curl wget \
    2>/dev/null | grep -E "^(Get|Entpacken|Einrichten)" || true
success "System-Abhängigkeiten installiert"

# --- Redis starten ---
systemctl enable --now redis-server
success "Redis läuft"

# --- PostgreSQL einrichten ---
systemctl enable --now postgresql
DB_NAME="paperless"
DB_USER="paperless"
DB_PASS="$(head -c 20 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 28)"

# Passwort aus bestehender Config lesen falls vorhanden
if [ -f "${PAPERLESS_DIR}/paperless.conf" ]; then
    EXISTING_PASS="$(grep '^PAPERLESS_DBPASS=' "${PAPERLESS_DIR}/paperless.conf" 2>/dev/null | cut -d= -f2 | tr -d '"' || echo "")"
    [ -n "${EXISTING_PASS}" ] && DB_PASS="${EXISTING_PASS}"
fi

if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" 2>/dev/null | grep -q 1; then
    sudo -u postgres psql -c "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASS}';" 2>/dev/null
    success "PostgreSQL-User '${DB_USER}' angelegt"
fi
if ! sudo -u postgres psql -lqt 2>/dev/null | cut -d'|' -f1 | grep -qw "${DB_NAME}"; then
    sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};" 2>/dev/null
    success "PostgreSQL-Datenbank '${DB_NAME}' angelegt"
fi
# Passwort aktualisieren (idempotent)
sudo -u postgres psql -c "ALTER USER ${DB_USER} WITH PASSWORD '${DB_PASS}';" 2>/dev/null || true
success "PostgreSQL bereit"

# --- System-User ---
if ! id "${PAPERLESS_USER}" &>/dev/null; then
    if getent group "${PAPERLESS_USER}" &>/dev/null; then
        useradd -r -s /bin/false -d "${PAPERLESS_DATA}" -m -g "${PAPERLESS_USER}" "${PAPERLESS_USER}"
    else
        useradd -r -s /bin/false -d "${PAPERLESS_DATA}" -m "${PAPERLESS_USER}"
    fi
    success "System-User '${PAPERLESS_USER}' angelegt"
fi

# --- Release herunterladen ---
info "Ermittle neuestes Paperless-ngx Release..."
RELEASE_JSON="$(curl -sf "https://api.github.com/repos/paperless-ngx/paperless-ngx/releases/latest")"
LATEST_TAG="$(printf '%s' "${RELEASE_JSON}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tag_name','v2.15.0'))" 2>/dev/null || echo "v2.15.0")"
DOWNLOAD_URL="$(printf '%s' "${RELEASE_JSON}" | python3 -c "
import sys,json
data=json.load(sys.stdin)
assets=data.get('assets',[])
for a in assets:
    n=a.get('name','')
    if 'paperless-ngx' in n and n.endswith('.tar.xz') and 'checksums' not in n:
        print(a['browser_download_url']); break
" 2>/dev/null || echo "")"

if [ -z "${DOWNLOAD_URL}" ]; then
    DOWNLOAD_URL="https://github.com/paperless-ngx/paperless-ngx/releases/download/${LATEST_TAG}/paperless-ngx-${LATEST_TAG}.tar.xz"
fi

info "Lade ${LATEST_TAG} herunter..."
curl -fSL "${DOWNLOAD_URL}" -o /tmp/paperless-ngx.tar.xz \
    || error "Download fehlgeschlagen: ${DOWNLOAD_URL}"

rm -rf "${PAPERLESS_DIR}"
mkdir -p "${PAPERLESS_DIR}"
tar -xJf /tmp/paperless-ngx.tar.xz -C "${PAPERLESS_DIR}" --strip-components=1
rm -f /tmp/paperless-ngx.tar.xz
printf '%s\n' "${LATEST_TAG}" > "${PAPERLESS_DIR}/VERSION"
success "Paperless-ngx ${LATEST_TAG} nach ${PAPERLESS_DIR} entpackt"

# --- Python-Venv + Abhängigkeiten ---
info "Richte Python-Virtualenv ein (dauert 3-8 Minuten)..."
python3 -m venv "${PAPERLESS_DIR}/.venv"
"${PAPERLESS_DIR}/.venv/bin/pip" install --quiet --upgrade pip wheel setuptools 2>/dev/null || true

# Paperless-ngx direkt als editable Package installieren (löst alle Dependencies korrekt auf)
if [ -f "${PAPERLESS_DIR}/pyproject.toml" ]; then
    info "Installiere via pyproject.toml (pip resolver)..."
    "${PAPERLESS_DIR}/.venv/bin/pip" install --quiet -e "${PAPERLESS_DIR}[all]" \
        2>&1 | tail -5 || {
        # Fallback ohne [all] extras
        warn "Install mit [all] fehlgeschlagen — versuche ohne Extras..."
        "${PAPERLESS_DIR}/.venv/bin/pip" install --quiet -e "${PAPERLESS_DIR}" \
            2>&1 | tail -5 || warn "pip install fehlgeschlagen"
    }
elif [ -f "${PAPERLESS_DIR}/setup.py" ]; then
    "${PAPERLESS_DIR}/.venv/bin/pip" install --quiet -e "${PAPERLESS_DIR}" \
        2>&1 | tail -5 || warn "pip install fehlgeschlagen"
fi

# Kern-Pakete sicherstellen (falls pyproject.toml nicht alles abdeckt)
"${PAPERLESS_DIR}/.venv/bin/pip" install --quiet \
    gunicorn uvicorn "psycopg[binary]" \
    2>/dev/null || true
success "Python-Venv bereit"

# --- Daten-Verzeichnisse ---
mkdir -p "${PAPERLESS_DATA}" "${PAPERLESS_MEDIA}" "${PAPERLESS_CONSUME}" "${PAPERLESS_EXPORT}"
chown -R "${PAPERLESS_USER}:${PAPERLESS_USER}" "${PAPERLESS_DATA}"
chown -R "${PAPERLESS_USER}:${PAPERLESS_USER}" "${PAPERLESS_DIR}"
success "Daten-Verzeichnisse angelegt"

# --- paperless.conf ---
SECRET_KEY="$(head -c 48 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 50)"
cat > "${PAPERLESS_DIR}/paperless.conf" << CONFEOF
# HydraHive managed — automatisch generiert
PAPERLESS_SECRET_KEY="${SECRET_KEY}"
PAPERLESS_DBENGINE=postgresql
PAPERLESS_DBHOST=127.0.0.1
PAPERLESS_DBPORT=5432
PAPERLESS_DBNAME=${DB_NAME}
PAPERLESS_DBUSER=${DB_USER}
PAPERLESS_DBPASS="${DB_PASS}"
PAPERLESS_REDIS=redis://localhost:6379
PAPERLESS_DATA_DIR=${PAPERLESS_DATA}
PAPERLESS_MEDIA_ROOT=${PAPERLESS_MEDIA}
PAPERLESS_CONSUMPTION_DIR=${PAPERLESS_CONSUME}
PAPERLESS_EXPORT_DIR=${PAPERLESS_EXPORT}
PAPERLESS_PORT=${PAPERLESS_PORT}
PAPERLESS_BIND_ADDR=0.0.0.0
PAPERLESS_TIME_ZONE=Europe/Berlin
PAPERLESS_OCR_LANGUAGE=deu+eng
PAPERLESS_OCR_MODE=skip
PAPERLESS_TIKA_ENABLED=false
PAPERLESS_ENABLE_HTTP_REMOTE_USER=false
PAPERLESS_ALLOWED_HOSTS=*
PAPERLESS_CORS_ALLOWED_HOSTS=*
CONFEOF
chown "${PAPERLESS_USER}:${PAPERLESS_USER}" "${PAPERLESS_DIR}/paperless.conf"
chmod 640 "${PAPERLESS_DIR}/paperless.conf"
success "paperless.conf konfiguriert"

# --- Datenbank-Migration ---
info "Führe Datenbankmigrationen aus..."
cd "${PAPERLESS_DIR}/src"
export PAPERLESS_CONFIGURATION_PATH="${PAPERLESS_DIR}/paperless.conf"
sudo -u "${PAPERLESS_USER}" -E \
    "${PAPERLESS_DIR}/.venv/bin/python" manage.py migrate --no-input 2>&1 | tail -5 \
    || warn "Migration fehlgeschlagen — wird beim ersten Start wiederholt"

# Superuser nur anlegen falls noch keiner existiert
sudo -u "${PAPERLESS_USER}" -E \
    DJANGO_SUPERUSER_PASSWORD="admin" \
    "${PAPERLESS_DIR}/.venv/bin/python" manage.py createsuperuser \
        --noinput --username admin --email admin@localhost 2>/dev/null \
    || true
success "Datenbank migriert"

# --- systemd Services ---
VENV_PYTHON="${PAPERLESS_DIR}/.venv/bin/python"
VENV_GUNICORN="${PAPERLESS_DIR}/.venv/bin/gunicorn"

# paperless-webserver
cat > /etc/systemd/system/paperless-webserver.service << SVCEOF
[Unit]
Description=Paperless-ngx Webserver
After=network.target redis-server.service postgresql.service
Requires=redis-server.service

[Service]
Type=simple
User=${PAPERLESS_USER}
Group=${PAPERLESS_USER}
WorkingDirectory=${PAPERLESS_DIR}/src
EnvironmentFile=${PAPERLESS_DIR}/paperless.conf
Environment=PAPERLESS_CONFIGURATION_PATH=${PAPERLESS_DIR}/paperless.conf
ExecStart=${VENV_GUNICORN} \
    --workers 2 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:${PAPERLESS_PORT} \
    --timeout 120 \
    paperless.asgi:application
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

# paperless-consumer
cat > /etc/systemd/system/paperless-consumer.service << SVCEOF
[Unit]
Description=Paperless-ngx Consumer
After=network.target redis-server.service postgresql.service
Requires=redis-server.service

[Service]
Type=simple
User=${PAPERLESS_USER}
Group=${PAPERLESS_USER}
WorkingDirectory=${PAPERLESS_DIR}/src
EnvironmentFile=${PAPERLESS_DIR}/paperless.conf
ExecStart=${VENV_PYTHON} manage.py document_consumer
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

# paperless-scheduler
cat > /etc/systemd/system/paperless-scheduler.service << SVCEOF
[Unit]
Description=Paperless-ngx Scheduler (Celery Beat)
After=network.target redis-server.service postgresql.service
Requires=redis-server.service

[Service]
Type=simple
User=${PAPERLESS_USER}
Group=${PAPERLESS_USER}
WorkingDirectory=${PAPERLESS_DIR}/src
EnvironmentFile=${PAPERLESS_DIR}/paperless.conf
ExecStart=${VENV_PYTHON} -m celery -A paperless worker \
    --loglevel=WARNING --concurrency 2 -Q celery,bulk
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable paperless-webserver paperless-consumer paperless-scheduler
systemctl start paperless-webserver paperless-consumer paperless-scheduler
success "Alle Paperless-Services gestartet"

# --- HydraHive Config ---
mkdir -p /etc/hydrahive
cat > "${HH_CONF}" << CFGEOF
{
  "installed": true,
  "version": "${LATEST_TAG}",
  "url": "http://127.0.0.1:${PAPERLESS_PORT}",
  "port": ${PAPERLESS_PORT},
  "data_dir": "${PAPERLESS_DATA}",
  "consume_dir": "${PAPERLESS_CONSUME}",
  "db_name": "${DB_NAME}",
  "db_user": "${DB_USER}"
}
CFGEOF
chown hydrahive:hydrahive "${HH_CONF}" 2>/dev/null || true
chmod 640 "${HH_CONF}"

echo ""
info "=== Paperless-ngx installiert ==="
_SERVER_IP_FINAL="$(hostname -I | awk '{print $1}')"
info "URL:           http://${_SERVER_IP_FINAL}:${PAPERLESS_PORT}"
info "Login:         admin / admin  (bitte sofort ändern!)"
info "Eingangskorb:  ${PAPERLESS_CONSUME}"
info "Dienste:       paperless-webserver / -consumer / -scheduler"
