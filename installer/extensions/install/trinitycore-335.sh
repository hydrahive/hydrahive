#!/usr/bin/env bash
# HydraHive Extension — TrinityCore 3.3.5a (WotLK)
# Native Kompilierung aus Source + MySQL Datenbanken
# Ports: 8085 (World), 3724 (Auth)
# Idempotent: erneuter Aufruf aktualisiert + rekompiliert

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[TC-335]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

TC_DIR="/opt/trinitycore-335"
TC_USER="trinity335"
TC_SOURCE="${TC_DIR}/source"
TC_BUILD="${TC_DIR}/build"
TC_SERVER="${TC_DIR}/server"
TC_DATA="${TC_DIR}/data"
WORLD_PORT="8085"
AUTH_PORT="3724"
DB_USER="trinity335"
DB_PASS="trinity335_$(hostname | md5sum | head -c8)"
CORES=$(nproc)

info "=== TrinityCore 3.3.5a (WotLK) installieren ==="
info "Kompilierung nutzt ${CORES} CPU-Kerne"

# --- System-User ---
if ! id "${TC_USER}" &>/dev/null; then
    useradd -r -m -d "${TC_DIR}" -s /bin/bash "${TC_USER}"
    success "User ${TC_USER} erstellt"
fi
mkdir -p "${TC_DIR}" "${TC_SOURCE}" "${TC_BUILD}" "${TC_SERVER}" "${TC_DATA}"

# --- Build-Dependencies ---
info "Installiere Build-Dependencies..."
apt-get update -qq 2>&1 | tail -1
apt-get install -y -qq \
    git clang cmake make gcc g++ \
    libmysqlclient-dev libssl-dev libbz2-dev libreadline-dev \
    libncurses-dev libboost-all-dev libfmt-dev \
    mysql-server p7zip-full \
    2>&1 | tail -3
success "Build-Dependencies installiert"

# --- MySQL Setup ---
info "Konfiguriere MySQL..."
if ! systemctl is-active mysql &>/dev/null; then
    systemctl start mysql
    systemctl enable mysql 2>/dev/null || true
fi

# DB-User + Datenbanken erstellen (idempotent)
mysql -u root -e "
    CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
    CREATE DATABASE IF NOT EXISTS trinity335_auth;
    CREATE DATABASE IF NOT EXISTS trinity335_characters;
    CREATE DATABASE IF NOT EXISTS trinity335_world;
    GRANT ALL PRIVILEGES ON trinity335_auth.* TO '${DB_USER}'@'localhost';
    GRANT ALL PRIVILEGES ON trinity335_characters.* TO '${DB_USER}'@'localhost';
    GRANT ALL PRIVILEGES ON trinity335_world.* TO '${DB_USER}'@'localhost';
    FLUSH PRIVILEGES;
" 2>/dev/null || warn "MySQL User/DB Setup — evtl. bereits vorhanden"
success "MySQL: Datenbanken trinity335_auth/characters/world"

# --- Source klonen / aktualisieren ---
if [ ! -d "${TC_SOURCE}/.git" ]; then
    info "Klone TrinityCore 3.3.5 Source (kann dauern)..."
    git clone --branch 3.3.5 --depth 1 \
        https://github.com/TrinityCore/TrinityCore.git "${TC_SOURCE}" 2>&1 | tail -3
    success "Source geklont (Branch 3.3.5)"
else
    info "Aktualisiere Source..."
    cd "${TC_SOURCE}" && git pull --ff-only 2>&1 | tail -3
    success "Source aktualisiert"
fi

# --- Kompilieren ---
info "Kompiliere TrinityCore (${CORES} Kerne, kann 15-30 Min dauern)..."
cd "${TC_BUILD}"
cmake "${TC_SOURCE}" \
    -DCMAKE_INSTALL_PREFIX="${TC_SERVER}" \
    -DCMAKE_C_COMPILER=/usr/bin/clang \
    -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
    -DTOOLS=1 \
    -DWITH_WARNINGS=0 \
    2>&1 | tail -5

make -j${CORES} 2>&1 | tail -10
make install 2>&1 | tail -5
success "Kompilierung abgeschlossen"

# --- Konfiguration ---
if [ ! -f "${TC_SERVER}/etc/worldserver.conf" ]; then
    cp "${TC_SERVER}/etc/worldserver.conf.dist" "${TC_SERVER}/etc/worldserver.conf"
    cp "${TC_SERVER}/etc/authserver.conf.dist" "${TC_SERVER}/etc/authserver.conf"

    # DB-Credentials eintragen
    sed -i "s|LoginDatabaseInfo.*=.*|LoginDatabaseInfo = \"127.0.0.1;3306;${DB_USER};${DB_PASS};trinity335_auth\"|" \
        "${TC_SERVER}/etc/worldserver.conf" "${TC_SERVER}/etc/authserver.conf"
    sed -i "s|WorldDatabaseInfo.*=.*|WorldDatabaseInfo = \"127.0.0.1;3306;${DB_USER};${DB_PASS};trinity335_world\"|" \
        "${TC_SERVER}/etc/worldserver.conf"
    sed -i "s|CharacterDatabaseInfo.*=.*|CharacterDatabaseInfo = \"127.0.0.1;3306;${DB_USER};${DB_PASS};trinity335_characters\"|" \
        "${TC_SERVER}/etc/worldserver.conf"

    # Data-Verzeichnis
    sed -i "s|DataDir.*=.*|DataDir = \"${TC_DATA}\"|" "${TC_SERVER}/etc/worldserver.conf"

    success "Konfiguration erstellt"
else
    success "Konfiguration bereits vorhanden"
fi

# --- SQL importieren ---
info "Importiere Datenbank-Schemas..."
if [ -d "${TC_SOURCE}/sql/base" ]; then
    for sql in "${TC_SOURCE}/sql/base/"*.sql; do
        [ -f "$sql" ] || continue
        db_name=$(basename "$sql" | grep -oP '(auth|characters|world)' || echo "")
        if [ -n "$db_name" ]; then
            mysql -u "${DB_USER}" -p"${DB_PASS}" "trinity335_${db_name}" < "$sql" 2>/dev/null || true
        fi
    done
    success "Datenbank-Schemas importiert"
fi

# --- Ownership ---
chown -R "${TC_USER}:${TC_USER}" "${TC_DIR}"

# --- Firewall ---
if command -v ufw &>/dev/null; then
    ufw allow ${WORLD_PORT}/tcp comment "TrinityCore 335 World" 2>/dev/null || true
    ufw allow ${AUTH_PORT}/tcp comment "TrinityCore 335 Auth" 2>/dev/null || true
    success "Firewall: Port ${WORLD_PORT}/tcp + ${AUTH_PORT}/tcp geöffnet"
fi

# --- systemd Services ---
cat > /etc/systemd/system/trinitycore-335-auth.service << EOF
[Unit]
Description=TrinityCore 3.3.5a Auth Server
After=network.target mysql.service
Requires=mysql.service

[Service]
Type=simple
User=${TC_USER}
WorkingDirectory=${TC_SERVER}/bin
ExecStart=${TC_SERVER}/bin/authserver
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/trinitycore-335-world.service << EOF
[Unit]
Description=TrinityCore 3.3.5a World Server
After=network.target mysql.service trinitycore-335-auth.service
Requires=mysql.service

[Service]
Type=simple
User=${TC_USER}
WorkingDirectory=${TC_SERVER}/bin
ExecStart=${TC_SERVER}/bin/worldserver
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable trinitycore-335-auth trinitycore-335-world 2>/dev/null || true
success "systemd Services: trinitycore-335-auth + trinitycore-335-world"

# --- Starten (nur wenn Maps vorhanden) ---
if [ -d "${TC_DATA}/dbc" ] || [ -d "${TC_DATA}/maps" ]; then
    systemctl start trinitycore-335-auth 2>/dev/null || warn "Auth Server Start fehlgeschlagen"
    systemctl start trinitycore-335-world 2>/dev/null || warn "World Server Start fehlgeschlagen"
    success "Server gestartet"
else
    warn "Map-Daten fehlen — Server kann noch nicht starten!"
fi

# --- DB-Passwort speichern ---
cat > /etc/hydrahive/trinitycore-335.json << EOF
{
    "db_user": "${DB_USER}",
    "db_pass": "${DB_PASS}",
    "world_port": ${WORLD_PORT},
    "auth_port": ${AUTH_PORT},
    "data_dir": "${TC_DATA}",
    "server_dir": "${TC_SERVER}"
}
EOF
chmod 600 /etc/hydrahive/trinitycore-335.json

info ""
success "=== TrinityCore 3.3.5a Installation abgeschlossen ==="
info "Auth Server: Port ${AUTH_PORT}/tcp"
info "World Server: Port ${WORLD_PORT}/tcp"
info "DB User: ${DB_USER} / ${DB_PASS}"
info ""
if [ ! -d "${TC_DATA}/maps" ]; then
    warn "═══════════════════════════════════════════════════════════════"
    warn "  WICHTIG: Map-Daten müssen noch extrahiert werden!"
    warn "  1. WoW 3.3.5a Client auf einem PC haben"
    warn "  2. Map-Extractor: ${TC_SERVER}/bin/mapextractor"
    warn "  3. Extrahierte Daten nach ${TC_DATA}/ kopieren:"
    warn "     dbc/ maps/ vmaps/ mmaps/"
    warn "  4. sudo systemctl start trinitycore-335-world"
    warn "═══════════════════════════════════════════════════════════════"
fi
