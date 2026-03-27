#!/usr/bin/env bash
set -euo pipefail
if ! declare -f info  &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn  &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi

info "Deinstalliere code-server..."

systemctl stop hydrahive-codeserver    2>/dev/null || true
systemctl disable hydrahive-codeserver 2>/dev/null || true
rm -f /etc/systemd/system/hydrahive-codeserver.service
systemctl daemon-reload

rm -rf /opt/codeserver
rm -rf /opt/hydrahive/.config/code-server
rm -rf /opt/hydrahive/.local/share/code-server

# nginx /code/-Block aus Konfig entfernen
NGINX_CONF="/etc/nginx/sites-available/hydrahive-console"
if [ -f "${NGINX_CONF}" ]; then
    python3 - "${NGINX_CONF}" <<'PY'
import sys, re
path = sys.argv[1]
text = open(path).read()
# Entferne den /code/ location-Block
text = re.sub(r'\n\s*# Code Editor \(code-server\).*?(?=\n\s*(?:location|#|\}|$))', '', text, flags=re.DOTALL)
text = re.sub(r'\n\s*location /code/ \{[^}]*\}', '', text, flags=re.DOTALL)
open(path, 'w').write(text)
print("nginx /code/ Block entfernt")
PY
    nginx -t &>/dev/null && systemctl reload nginx 2>/dev/null || true
fi

# codeserver_password aus credentials entfernen
CRED_FILE="/etc/hydrahive/admin_credentials"
[ -f "${CRED_FILE}" ] && sed -i '/^codeserver_password=/d' "${CRED_FILE}" || true

success "code-server deinstalliert"
