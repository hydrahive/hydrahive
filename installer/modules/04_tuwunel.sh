# Modul 04 — Tuwunel Matrix Homeserver (#41)
TUWUNEL_VERSION="0.5.0"
TUWUNEL_USER="tuwunel"
TUWUNEL_DIR="/var/lib/tuwunel"
TUWUNEL_CONFIG="/etc/tuwunel/tuwunel.toml"
TUWUNEL_BIN="/usr/local/bin/tuwunel"
TUWUNEL_ARCH="x86_64-unknown-linux-musl"

info "Installiere Tuwunel $TUWUNEL_VERSION..."

# Idempotenz: schon installiert und aktuell?
if [ -f "$TUWUNEL_BIN" ]; then
  INSTALLED=$("$TUWUNEL_BIN" --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1 || echo "0.0.0")
  if [ "$INSTALLED" = "$TUWUNEL_VERSION" ]; then
    success "Tuwunel $TUWUNEL_VERSION bereits installiert"
  else
    warn "Tuwunel $INSTALLED gefunden, update auf $TUWUNEL_VERSION..."
  fi
fi

# Binary herunterladen
DOWNLOAD_URL="https://github.com/matrix-construct/tuwunel/releases/download/v${TUWUNEL_VERSION}/tuwunel-${TUWUNEL_ARCH}"
info "Lade Tuwunel Binary herunter..."
if ! curl -sL --fail "$DOWNLOAD_URL" -o "$TUWUNEL_BIN.tmp"; then
  # Fallback: latest release via GitHub API
  info "Direktlink fehlgeschlagen, suche latest release..."
  LATEST_URL=$(curl -sL "https://api.github.com/repos/matrix-construct/tuwunel/releases/latest" | \
    python3 -c "
import sys,json
d=json.load(sys.stdin)
for a in d.get('assets',[]):
    if 'x86_64' in a['name'] and 'musl' in a['name'] and not a['name'].endswith('.sha256'):
        print(a['browser_download_url'])
        break
")
  if [ -z "$LATEST_URL" ]; then
    error "Tuwunel Binary konnte nicht gefunden werden. Bitte manuell installieren."
  fi
  curl -sL --fail "$LATEST_URL" -o "$TUWUNEL_BIN.tmp"
fi

chmod +x "$TUWUNEL_BIN.tmp"
mv "$TUWUNEL_BIN.tmp" "$TUWUNEL_BIN"
success "Tuwunel Binary installiert: $TUWUNEL_BIN"

# System-User anlegen (idempotent)
if ! id "$TUWUNEL_USER" &>/dev/null; then
  useradd -r -s /bin/false -d "$TUWUNEL_DIR" "$TUWUNEL_USER"
  success "System-User '$TUWUNEL_USER' angelegt"
else
  success "System-User '$TUWUNEL_USER' bereits vorhanden"
fi

# Verzeichnisse anlegen
mkdir -p "$TUWUNEL_DIR" "$(dirname "$TUWUNEL_CONFIG")"
chown -R "$TUWUNEL_USER:$TUWUNEL_USER" "$TUWUNEL_DIR"

# Hostname ermitteln
SERVER_NAME=$(hostname -f 2>/dev/null || hostname)

# Config schreiben (nur wenn noch nicht vorhanden)
if [ ! -f "$TUWUNEL_CONFIG" ]; then
  cat > "$TUWUNEL_CONFIG" << TOML
[global]
server_name = "${SERVER_NAME}"
database_path = "${TUWUNEL_DIR}/rocksdb"
port = 6167
address = "127.0.0.1"

# Nur lokale Registrierung — kein offener Server
allow_registration = true
registration_token = "$(openssl rand -hex 16)"

# Keine Federation — internes OctopOS-Netz
allow_federation = false

# Logging
log = "warn,tuwunel=info"

[global.tls]
# TLS wird von nginx termiert
# Tuwunel lauscht nur auf localhost
TOML
  chown "$TUWUNEL_USER:$TUWUNEL_USER" "$TUWUNEL_CONFIG"
  success "Tuwunel Konfiguration geschrieben: $TUWUNEL_CONFIG"
else
  success "Tuwunel Konfiguration bereits vorhanden"
fi

# Systemd-Unit schreiben
cat > /etc/systemd/system/octopos-tuwunel.service << UNIT
[Unit]
Description=OctopOS Tuwunel Matrix Homeserver
After=network.target
Documentation=https://github.com/matrix-construct/tuwunel

[Service]
Type=simple
User=${TUWUNEL_USER}
Group=${TUWUNEL_USER}
ExecStart=${TUWUNEL_BIN} --config ${TUWUNEL_CONFIG}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=octopos-tuwunel

# Haertung
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ReadWritePaths=${TUWUNEL_DIR}

[Install]
WantedBy=multi-user.target
UNIT

# Service aktivieren und starten
systemctl daemon-reload
systemctl enable octopos-tuwunel

if systemctl is-active --quiet octopos-tuwunel; then
  systemctl restart octopos-tuwunel
  success "Tuwunel neugestartet"
else
  systemctl start octopos-tuwunel
  success "Tuwunel gestartet"
fi

# Health-Check
sleep 2
if curl -sf "http://127.0.0.1:6167/_matrix/client/versions" &>/dev/null; then
  success "Tuwunel antwortet auf http://127.0.0.1:6167"
else
  warn "Tuwunel antwortet noch nicht — pruefe: journalctl -u octopos-tuwunel -n 20"
fi
