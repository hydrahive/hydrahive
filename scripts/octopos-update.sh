#!/bin/bash
# octopos-update.sh — OctopOS auf der VM aktualisieren
# Läuft auf Lilith: git pull → rsync Core → npm build → rsync Console → restart
# Verwendung: ./scripts/octopos-update.sh

set -e

VM="octopos@192.168.178.181"
SSH_KEY="$HOME/.ssh/claude_key_nopass"
SSH="ssh -i $SSH_KEY"
RSYNC="rsync -av --exclude='__pycache__' --exclude='*.pyc' -e \"ssh -i $SSH_KEY\""
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> [1/5] git pull"
cd "$REPO"
git pull

echo ""
echo "==> [2/5] Core rsync → VM"
$SSH "$VM" "sudo chown -R octopos:octopos /opt/octopos/core/"
rsync -av --delete \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
  -e "ssh -i $SSH_KEY" \
  "$REPO/core/" \
  "$VM:/opt/octopos/core/"

echo ""
echo "==> [3/5] pip install auf VM"
$SSH "$VM" "cd /opt/octopos/core && /opt/octopos/venv/bin/pip install -e . -q"

echo ""
echo "==> [4/5] Console bauen"
npm --prefix "$REPO/console" run build

echo ""
echo "==> [4b/5] Console-Permissions für rsync setzen"
$SSH "$VM" "sudo chown -R octopos:octopos /opt/octopos/console/"

echo ""
echo "==> [5/5] Console rsync → VM + restart"
rsync -av --delete \
  -e "ssh -i $SSH_KEY" \
  "$REPO/console/dist/" \
  "$VM:/opt/octopos/console/"

$SSH "$VM" "sudo chown -R www-data:www-data /opt/octopos/console/ && sudo systemctl restart octopos-core"

echo ""
echo "==> Warte auf Start..."
sleep 3
$SSH "$VM" "sudo systemctl status octopos-core --no-pager | head -4"

echo ""
echo "==> [6/5] Gitea-Status prüfen"
$SSH "$VM" "systemctl is-active gitea && echo 'Gitea läuft' || echo 'WARNUNG: Gitea nicht aktiv — starte...'; sudo systemctl start gitea 2>/dev/null; true"

echo ""
echo "✓ Update abgeschlossen"
