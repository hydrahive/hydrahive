# Modul 04 — conduwuit Matrix Homeserver (#41)
TUWUNEL_USER="conduwuit"
TUWUNEL_DIR="/var/lib/conduwuit"
TUWUNEL_CONFIG="/etc/conduwuit/conduwuit.toml"
TUWUNEL_BIN="/usr/local/bin/conduwuit"

info "Installiere conduwuit..."

# Idempotenz: Binary schon vorhanden?
if [ -f "$TUWUNEL_BIN" ]; then
  INSTALLED=$("$TUWUNEL_BIN" --version 2>/dev/null | grep -oP '\d+\.\d+\.\d+' | head -1 || echo "?")
  info "conduwuit $INSTALLED bereits installiert — pruefe auf Update..."
fi

# Aktuelles Release via GitHub API
info "Suche aktuelles conduwuit Release..."
RELEASE_INFO=$(curl -sL "https://api.github.com/repos/girlbossceo/conduwuit/releases/latest")
RELEASE_TAG=$(echo "$RELEASE_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tag_name',''))")

if [ -z "$RELEASE_TAG" ]; then
  error "conduwuit Release konnte nicht ermittelt werden."
fi
info "Gefunden: conduwuit $RELEASE_TAG"

# .deb Asset URL finden
DEB_URL=$(echo "$RELEASE_INFO" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for a in d.get('assets',[]):
    name = a['name']
    if name.endswith('.deb') and 'x86_64' in name and 'musl' in name and 'debug' not in name:
        print(a['browser_download_url'])
        break
")

if [ -z "$DEB_URL" ]; then
  # Fallback: statisches Binary
  info "Kein .deb gefunden, versuche statisches Binary..."
  BIN_URL=$(echo "$RELEASE_INFO" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for a in d.get('assets',[]):
    name = a['name']
    if 'x86_64' in name and 'musl' in name and 'debug' not in name \
       and not name.endswith('.sha256') and not name.endswith('.tar.gz') \
       and not name.endswith('.deb'):
        print(a['browser_download_url'])
        break
")
  if [ -z "$BIN_URL" ]; then
    error "Kein passendes conduwuit Asset gefunden fuer x86_64-musl."
  fi
  info "Lade Binary: $BIN_URL"
  curl -sL --fail "$BIN_URL" -o "$TUWUNEL_BIN.tmp"
  chmod +x "$TUWUNEL_BIN.tmp"
  mv "$TUWUNEL_BIN.tmp" "$TUWUNEL_BIN"
  success "conduwuit Binary installiert"
else
  info "Lade .deb: $DEB_URL"
  curl -sL --fail "$DEB_URL" -o /tmp/conduwuit.deb
  # dpkg gibt viele chown-Zeilen aus — filtern
  DEBIAN_FRONTEND=noninteractive dpkg -i /tmp/conduwuit.deb 2>&1 | grep -v "Eigent" | grep -v "erhalten" | grep -v "^$" || true
  rm -f /tmp/conduwuit.deb
  # .deb legt Binary nach /usr/sbin/conduwuit — einheitlicher Symlink
  if [ ! -f "$TUWUNEL_BIN" ]; then
    for candidate in /usr/sbin/conduwuit /usr/bin/conduwuit; do
      if [ -f "$candidate" ]; then
        ln -sf "$candidate" "$TUWUNEL_BIN"
        break
      fi
    done
  fi
  success "conduwuit $RELEASE_TAG via .deb installiert"
fi

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

# Config IMMER schreiben — .deb legt unbefuelltes Template an, das wir ueberschreiben muessen
# Registration-Token nur neu generieren wenn noch keiner vorhanden
if [ -f "$TUWUNEL_CONFIG" ]; then
  EXISTING_TOKEN=$(grep '^registration_token' "$TUWUNEL_CONFIG" | grep -oP '"\K[^"]+' || echo "")
else
  EXISTING_TOKEN=""
fi
# Placeholder oder leer -> frischen Token generieren
if echo "$EXISTING_TOKEN" | grep -qiE "change|example|placeholder|your|^$"; then
  EXISTING_TOKEN=""
fi
REG_TOKEN="${EXISTING_TOKEN:-$(openssl rand -hex 32)}"

cat > "$TUWUNEL_CONFIG" << TOML
[global]
server_name = "${SERVER_NAME}"
database_path = "${TUWUNEL_DIR}/rocksdb"
port = 6167
address = "127.0.0.1"

allow_registration = true
registration_token = "${REG_TOKEN}"

allow_federation = false

log = "warn,conduwuit=info"
TOML

chown "$TUWUNEL_USER:$TUWUNEL_USER" "$TUWUNEL_CONFIG"
success "conduwuit Konfiguration geschrieben (server_name: $SERVER_NAME)"
info "Registration-Token: $REG_TOKEN"

# Systemd-Unit schreiben
cat > /etc/systemd/system/octopos-conduwuit.service << UNIT
[Unit]
Description=OctopOS conduwuit Matrix Homeserver
After=network.target
Documentation=https://github.com/girlbossceo/conduwuit

[Service]
Type=simple
User=${TUWUNEL_USER}
Group=${TUWUNEL_USER}
ExecStart=${TUWUNEL_BIN} --config ${TUWUNEL_CONFIG}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=octopos-conduwuit
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ReadWritePaths=${TUWUNEL_DIR}

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable octopos-conduwuit

if systemctl is-active --quiet octopos-conduwuit; then
  systemctl restart octopos-conduwuit
  success "conduwuit neugestartet"
else
  systemctl start octopos-conduwuit
  success "conduwuit gestartet"
fi

# Health-Check — Retry-Loop (3x mit 3s Pause)
HEALTH_OK=0
for i in 1 2 3; do
  sleep 3
  if curl -sf "http://127.0.0.1:6167/_matrix/client/versions" &>/dev/null; then
    success "conduwuit antwortet auf http://127.0.0.1:6167"
    HEALTH_OK=1
    break
  fi
  info "Warte auf conduwuit... ($i/3)"
done
if [ "$HEALTH_OK" -eq 0 ]; then
  warn "conduwuit antwortet nicht — pruefe: journalctl -u octopos-conduwuit -n 30"
fi
