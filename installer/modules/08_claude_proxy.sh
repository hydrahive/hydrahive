# Modul 08 — Claude OAuth Proxy (#38)
info "Richte Claude OAuth Proxy ein (Port 3456)..."

PROXY_UNIT="/etc/systemd/system/octopos-claude-proxy.service"
TOKEN_FILE="/etc/octopos/claude_oauth_token"

# Systemd-Unit schreiben
cat > "$PROXY_UNIT" << UNIT
[Unit]
Description=OctopOS Claude OAuth Proxy
After=network.target octopos-core.service

[Service]
Type=simple
User=octopos
Group=octopos
WorkingDirectory=/opt/octopos
ExecStart=/opt/octopos/venv/bin/python -m uvicorn octopos_core.claude_oauth_proxy:app --host 127.0.0.1 --port 3456 --log-level warning
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=octopos-claude-proxy

[Install]
WantedBy=multi-user.target
UNIT

# Token-Datei anlegen falls nicht vorhanden
if [ ! -f "$TOKEN_FILE" ]; then
    touch "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
    chown octopos:octopos "$TOKEN_FILE"
    warn "Claude OAuth Token noch nicht konfiguriert — bitte in LLM-Config hinterlegen"
fi

systemctl daemon-reload
systemctl enable octopos-claude-proxy

# Nur starten wenn Token vorhanden
if [ -s "$TOKEN_FILE" ]; then
    systemctl start octopos-claude-proxy
    sleep 2
    if curl -sf http://127.0.0.1:3456/health &>/dev/null; then
        success "Claude OAuth Proxy gestartet auf Port 3456"
    else
        warn "Claude OAuth Proxy gestartet aber antwortet noch nicht"
    fi
else
    info "Claude OAuth Proxy wird gestartet sobald Token konfiguriert ist"
fi
