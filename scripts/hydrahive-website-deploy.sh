#!/bin/bash
# hydrahive-website-deploy.sh — Website auf hydrahive.org deployen
# FTP-Credentials aus ~/.config/hydrahive/website.conf lesen

set -e

CONF="$HOME/.config/hydrahive/website.conf"
if [ ! -f "$CONF" ]; then
  echo "Fehler: $CONF nicht gefunden."
  echo "Anlegen mit:"
  echo "  mkdir -p ~/.config/hydrahive"
  echo "  echo 'FTP_USER=w021655e' > $CONF"
  echo "  echo 'FTP_PASS=...' >> $CONF"
  echo "  echo 'FTP_HOST=dd22628.kasserver.com' >> $CONF"
  echo "  chmod 600 $CONF"
  exit 1
fi
source "$CONF"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
WEBSITE="$REPO/website"
# #296: FTPS statt Klartext-FTP, Credentials nicht in URL/Prozessliste
# All-Inkl KAS unterstützt FTPS (Explicit TLS)
NETRC_TMP=$(mktemp)
chmod 600 "$NETRC_TMP"
echo "machine ${FTP_HOST} login ${FTP_USER} password ${FTP_PASS}" > "$NETRC_TMP"
trap "rm -f '$NETRC_TMP'" EXIT

echo "==> Deploye Website nach hydrahive.org (FTPS)..."
for f in index.html index.htm handbuch.html api.html technical.html development.html datenschutz.html impressum.html; do
  if [ -f "$WEBSITE/$f" ]; then
    curl -s --ftp-ssl-reqd --ftp-pasv \
      --netrc-file "$NETRC_TMP" \
      -T "$WEBSITE/$f" "ftp://${FTP_HOST}/hydrahive.org/$f" \
      && echo "  ✓ $f" || echo "  ✗ $f (Fehler)"
  fi
done
echo "==> Fertig: https://hydrahive.org"
