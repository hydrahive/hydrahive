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
PASS_ENC=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$FTP_PASS")
FTP="ftp://${FTP_USER}:${PASS_ENC}@${FTP_HOST}/hydrahive.org"

echo "==> Deploye Website nach hydrahive.org..."
for f in index.html index.htm handbuch.html api.html technical.html development.html datenschutz.html impressum.html; do
  if [ -f "$WEBSITE/$f" ]; then
    curl -s --ftp-pasv -T "$WEBSITE/$f" "${FTP}/$f" && echo "  ✓ $f"
  fi
done
echo "==> Fertig: https://hydrahive.org"
