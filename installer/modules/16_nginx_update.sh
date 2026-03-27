#!/usr/bin/env bash
# 16_nginx_update.sh — nginx A2A-Proxy-Regeln einrichten
# Wird vom Doctor-Fix-Endpoint aufgerufen.
# Sicherer Ablauf: Backup → Copy → Test → Reload. Bei Fehler: automatisch zurückrollen.
set -euo pipefail

TEMPLATE="/opt/hydrahive/installer/hydrahive-console.nginx"
TARGET="/etc/nginx/sites-enabled/hydrahive-console"
BACKUP="${TARGET}.bak.$$"

# Template prüfen
[ -f "${TEMPLATE}" ] || { echo "FEHLER: Template nicht gefunden: ${TEMPLATE}"; exit 1; }
[ -f "${TARGET}" ]   || { echo "FEHLER: nginx-Site nicht gefunden: ${TARGET} — wurde HydraHive korrekt installiert?"; exit 1; }

# Backup
cp "${TARGET}" "${BACKUP}"
echo "Backup: ${BACKUP}"

# Template einspielen
cp "${TEMPLATE}" "${TARGET}"
echo "Template kopiert"

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
