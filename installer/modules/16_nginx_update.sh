#!/usr/bin/env bash
# 16_nginx_update.sh — nginx-Konfig aktualisieren (A2A-Proxy-Regeln)
# Wird vom Doctor-Fix-Endpoint aufgerufen:
#   sudo -n /bin/bash /opt/hydrahive/installer/modules/16_nginx_update.sh
set -euo pipefail

TEMPLATE="/opt/hydrahive/installer/hydrahive-console.nginx"
TARGET="/etc/nginx/sites-enabled/hydrahive-console"

[ -f "${TEMPLATE}" ] || { echo "FEHLER: Template nicht gefunden: ${TEMPLATE}"; exit 1; }
[ -f "${TARGET}" ]   || { echo "FEHLER: Nginx-Site nicht gefunden: ${TARGET}"; exit 1; }

cp "${TEMPLATE}" "${TARGET}"
echo "nginx-Konfig kopiert"

nginx -t
systemctl reload nginx
echo "nginx neu geladen — A2A-Proxy-Regeln aktiv"
