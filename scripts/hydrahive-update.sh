#!/bin/bash
# hydrahive-update.sh — HydraHive auf der VM aktualisieren
# Läuft auf Lilith: git pull → rsync Core → npm build → rsync Console → restart
# Verwendung: ./scripts/hydrahive-update.sh

set -e

# Konfiguration: scripts/hydrahive.conf anlegen um Defaults zu überschreiben
CONF="$(dirname "$0")/hydrahive.conf"
VM="octopos@192.168.1.100"
SSH_KEY="$HOME/.ssh/id_rsa"
INSTALL_DIR="/opt/hydrahive"
INSTALL_USER="hydrahive"
SERVICE_NAME="hydrahive-core"
[ -f "$CONF" ] && source "$CONF"
SSH="ssh -i $SSH_KEY"
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> [1/5] git pull (hydrahive remote)"
cd "$REPO"
# Versuche hydrahive-Remote, dann gitea-local, dann origin
git pull hydrahive main 2>/dev/null || git pull gitea-local main 2>/dev/null || git pull 2>/dev/null || echo "   (kein Remote erreichbar — lokalen Stand deployen)"

echo ""
echo "==> [2/5] Core rsync → VM"
$SSH "$VM" "sudo chown -R ${INSTALL_USER}:${INSTALL_USER} ${INSTALL_DIR}/core/"
rsync -av --delete \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
  -e "ssh -i $SSH_KEY" \
  "$REPO/core/" \
  "$VM:${INSTALL_DIR}/core/"

echo ""
echo "==> [3/5] pip install auf VM"
$SSH "$VM" "cd ${INSTALL_DIR}/core && ${INSTALL_DIR}/venv/bin/pip install -e . -q"

echo ""
echo "==> [4/5] Console bauen"
npm --prefix "$REPO/console" run build

echo ""
echo "==> [4b/5] Console-Permissions für rsync setzen"
$SSH "$VM" "sudo chown -R ${INSTALL_USER}:${INSTALL_USER} ${INSTALL_DIR}/console/"

echo ""
echo "==> [5/5] Console rsync → VM + restart"
rsync -av --delete \
  -e "ssh -i $SSH_KEY" \
  "$REPO/console/dist/" \
  "$VM:${INSTALL_DIR}/console/"

$SSH "$VM" "sudo chown -R www-data:www-data ${INSTALL_DIR}/console/ && sudo systemctl restart ${SERVICE_NAME}"

echo ""
echo "==> Warte auf Start..."
sleep 3
$SSH "$VM" "sudo systemctl status ${SERVICE_NAME} --no-pager | head -4"

echo ""
echo "==> [6/5] Gitea-Status prüfen"
$SSH "$VM" "systemctl is-active gitea && echo 'Gitea läuft' || echo 'WARNUNG: Gitea nicht aktiv — starte...'; sudo systemctl start gitea 2>/dev/null; true"

echo ""
echo "==> Commit-Stand in Update-Status schreiben"
COMMIT=$(git rev-parse --short HEAD)
DEPLOY_DATE=$(date -u +"%Y-%m-%dT%H:%M:%S+00:00")
$SSH "$VM" "sudo bash -c 'echo \"{\\\"status\\\":\\\"ok\\\",\\\"commit\\\":\\\"${COMMIT}\\\",\\\"finished_at\\\":\\\"${DEPLOY_DATE}\\\"}\" > /var/run/octopos-update.json && chown ${INSTALL_USER}:${INSTALL_USER} /var/run/octopos-update.json'"
echo "   Commit: $COMMIT"

echo ""
echo "✓ Update abgeschlossen"
