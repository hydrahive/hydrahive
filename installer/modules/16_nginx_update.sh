#!/usr/bin/env bash
# 16_nginx_update.sh — fehlende nginx-Regeln in bestehende Konfig einfügen
# Wird vom Doctor-Fix-Endpoint aufgerufen (idempotent).
set -euo pipefail

TARGET="/etc/nginx/sites-enabled/hydrahive-console"
BACKUP="${TARGET}.bak.$$"

[ -f "${TARGET}" ] || { echo "FEHLER: nginx-Site nicht gefunden: ${TARGET}"; exit 1; }

CHANGED=0
cp "${TARGET}" "${BACKUP}"

restore_and_exit() {
    echo "FEHLER: nginx -t fehlgeschlagen — stelle Backup wieder her"
    cp "${BACKUP}" "${TARGET}"
    rm -f "${BACKUP}"
    exit 1
}

# --- 1. A2A-Regeln ---
HAS_WELL_KNOWN=$(grep -c "\.well-known" "${TARGET}" 2>/dev/null || true)
HAS_A2A=$(grep -c "location /a2a/" "${TARGET}" 2>/dev/null || true)

if [ "${HAS_WELL_KNOWN}" -eq 0 ] || [ "${HAS_A2A}" -eq 0 ]; then
    A2A_BLOCK='
    # A2A Federation: Agent Card + Task-Eingang direkt proxyen (kein /api-Prefix)
    location /.well-known/ {
        proxy_pass         http://127.0.0.1:8765/.well-known/;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   Connection        "";
        proxy_read_timeout    60s;
    }

    location /a2a/ {
        proxy_pass         http://127.0.0.1:8765/a2a/;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   Connection        "";
        proxy_read_timeout    300s;
        proxy_connect_timeout 5s;
    }
'
    python3 - "${TARGET}" "${A2A_BLOCK}" << 'PYEOF'
import sys, pathlib
target = pathlib.Path(sys.argv[1])
block  = sys.argv[2]
content = target.read_text()
idx = content.rfind("}")
if idx == -1:
    print("FEHLER: Kein schliessender } gefunden", file=sys.stderr); sys.exit(1)
target.write_text(content[:idx] + block + "\n}\n")
PYEOF
    CHANGED=1
    echo "A2A-Blöcke eingefügt"
else
    echo "A2A-Regeln bereits vorhanden"
fi

# --- 2. /projects/ Block ---
HAS_PROJECTS=$(grep -c "location /projects/" "${TARGET}" 2>/dev/null || true)

if [ "${HAS_PROJECTS}" -eq 0 ]; then
    PROJECTS_BLOCK='
    # Projekt-Dateien: Agenten-Outputs per HTTP erreichbar
    location /projects/ {
        alias /projects/;
        autoindex on;
        autoindex_exact_size off;
        add_header Cache-Control "no-store";
    }
'
    python3 - "${TARGET}" "${PROJECTS_BLOCK}" << 'PYEOF'
import sys, pathlib
target = pathlib.Path(sys.argv[1])
block  = sys.argv[2]
content = target.read_text()
idx = content.rfind("}")
if idx == -1:
    print("FEHLER: Kein schliessender } gefunden", file=sys.stderr); sys.exit(1)
target.write_text(content[:idx] + block + "\n}\n")
PYEOF
    CHANGED=1
    echo "/projects/-Block eingefügt"
else
    echo "/projects/-Block bereits vorhanden"
fi

# --- 3. client_max_body_size ---
# AdminFun (#AdminFun) kann 200MB MP3s hochladen → Limit entsprechend hochsetzen
HAS_MAX_BODY=$(grep -c "client_max_body_size" "${TARGET}" 2>/dev/null || true)

if [ "${HAS_MAX_BODY}" -eq 0 ]; then
    python3 - "${TARGET}" << 'PYEOF'
import sys, pathlib
target = pathlib.Path(sys.argv[1])
content = target.read_text()
lines = content.splitlines()
for i, line in enumerate(lines):
    if 'server {' in line:
        lines.insert(i + 1, '    client_max_body_size   200M;')
        break
target.write_text('\n'.join(lines) + '\n')
PYEOF
    CHANGED=1
    echo "client_max_body_size 200M eingefügt"
else
    # Bestehenden Wert auf 200M hochziehen (für alte Installationen mit 50M oder niedriger)
    if ! grep -qE "client_max_body_size\s+200M" "${TARGET}"; then
        sed -i -E 's/client_max_body_size[[:space:]]+[0-9]+[KMG]?;/client_max_body_size   200M;/g' "${TARGET}"
        CHANGED=1
        echo "client_max_body_size auf 200M hochgesetzt"
    else
        echo "client_max_body_size bereits 200M"
    fi
fi

# --- 4. www-data in hydrahive-Gruppe ---
if getent group hydrahive > /dev/null 2>&1; then
    usermod -aG hydrahive www-data 2>/dev/null || true
    echo "www-data zur hydrahive-Gruppe hinzugefügt/bestätigt"
fi

# --- nginx testen + neu laden ---
if [ "${CHANGED}" -eq 0 ]; then
    echo "Alle nginx-Regeln bereits vorhanden — nichts zu tun"
    rm -f "${BACKUP}"
    exit 0
fi

if ! nginx -t 2>&1; then
    restore_and_exit
fi

if ! systemctl reload nginx; then
    echo "FEHLER: nginx reload fehlgeschlagen — stelle Backup wieder her"
    cp "${BACKUP}" "${TARGET}"
    rm -f "${BACKUP}"
    systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || true
    exit 1
fi

rm -f "${BACKUP}"
echo "nginx neu geladen — alle Regeln aktiv"
