#!/usr/bin/env bash
# 16_nginx_update.sh — fehlende A2A-Proxy-Regeln in bestehende nginx-Konfig einfügen
# Wird vom Doctor-Fix-Endpoint aufgerufen.
# Wichtig: ersetzt die Konfig NICHT — injiziert nur die fehlenden location-Blöcke.
set -euo pipefail

TARGET="/etc/nginx/sites-enabled/hydrahive-console"
BACKUP="${TARGET}.bak.$$"

[ -f "${TARGET}" ] || { echo "FEHLER: nginx-Site nicht gefunden: ${TARGET}"; exit 1; }

# Prüfen ob Regeln bereits vorhanden
HAS_WELL_KNOWN=$(grep -c "\.well-known" "${TARGET}" 2>/dev/null || true)
HAS_A2A=$(grep -c "/a2a/" "${TARGET}" 2>/dev/null || true)

if [ "${HAS_WELL_KNOWN}" -gt 0 ] && [ "${HAS_A2A}" -gt 0 ]; then
    echo "A2A-Regeln bereits vorhanden — nichts zu tun"
    exit 0
fi

# Backup
cp "${TARGET}" "${BACKUP}"
echo "Backup: ${BACKUP}"

# A2A-Blöcke als Marker vor dem letzten } einfügen
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

# Letztes `}` (Ende des letzten server-Blocks) durch A2A-Blöcke + `}` ersetzen
python3 - "${TARGET}" "${A2A_BLOCK}" << 'PYEOF'
import sys, pathlib
target = pathlib.Path(sys.argv[1])
block  = sys.argv[2]
content = target.read_text()
# Letztes } im File ersetzen
idx = content.rfind("}")
if idx == -1:
    print("FEHLER: Kein schliessender } gefunden", file=sys.stderr)
    sys.exit(1)
new_content = content[:idx] + block + "\n}\n"
target.write_text(new_content)
PYEOF

echo "A2A-Blöcke eingefügt"

# nginx-Konfig testen
if ! nginx -t 2>&1; then
    echo "FEHLER: nginx -t fehlgeschlagen — stelle Backup wieder her"
    cp "${BACKUP}" "${TARGET}"
    rm -f "${BACKUP}"
    echo "Backup wiederhergestellt — nginx-Konfig unverändert"
    exit 1
fi

# nginx neu laden
if ! systemctl reload nginx; then
    echo "FEHLER: nginx reload fehlgeschlagen — stelle Backup wieder her"
    cp "${BACKUP}" "${TARGET}"
    rm -f "${BACKUP}"
    systemctl reload nginx 2>/dev/null || systemctl restart nginx 2>/dev/null || true
    echo "Backup wiederhergestellt"
    exit 1
fi

rm -f "${BACKUP}"
echo "nginx neu geladen — A2A-Proxy-Regeln aktiv"
