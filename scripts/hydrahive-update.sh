#!/bin/bash
# hydrahive-update.sh — HydraHive auf der VM aktualisieren
# Läuft auf Lilith: git pull → rsync Core → npm build → rsync Console → restart
# Verwendung: ./scripts/hydrahive-update.sh

set -e

# Konfiguration: scripts/hydrahive.conf anlegen um Defaults zu überschreiben
CONF="$(dirname "$0")/hydrahive.conf"
VM="hydrahive@192.168.1.100"
SSH_KEY="$HOME/.ssh/id_rsa"
INSTALL_DIR="/opt/hydrahive"
INSTALL_USER="hydrahive"
SERVICE_NAME="hydrahive-core"
[ -f "$CONF" ] && source "$CONF"
SSH="ssh -i $SSH_KEY"
SSH_USER="${VM%%@*}"   # Login-User aus VM-String (z.B. "hydrahive" aus "hydrahive@host")
REPO="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> [1/5] git pull (hydrahive remote)"
cd "$REPO"
# Versuche hydrahive-Remote, dann gitea-local, dann origin
git pull hydrahive main 2>/dev/null || git pull gitea-local main 2>/dev/null || git pull 2>/dev/null || echo "   (kein Remote erreichbar — lokalen Stand deployen)"

echo ""
echo "==> [2/5] Core rsync → VM"
# Vor rsync: SSH-User als Owner setzen damit rsync schreiben kann
$SSH "$VM" "sudo chown -R ${SSH_USER}:${SSH_USER} ${INSTALL_DIR}/core/"
rsync -av --delete --no-owner --no-group \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
  -e "ssh -i $SSH_KEY" \
  "$REPO/core/" \
  "$VM:${INSTALL_DIR}/core/"
# Nach rsync: Owner auf Service-User zurücksetzen
$SSH "$VM" "sudo chown -R ${INSTALL_USER}:${INSTALL_USER} ${INSTALL_DIR}/core/"

echo ""
echo "==> [3/5] pip install auf VM"
$SSH "$VM" "sudo -u ${INSTALL_USER} ${INSTALL_DIR}/venv/bin/pip install -e '${INSTALL_DIR}/core[dev]' -q"

echo ""
echo "==> [4/5] Console bauen"
npm --prefix "$REPO/console" run build

echo ""
echo "==> [4b/5] Console-Permissions für rsync setzen"
$SSH "$VM" "sudo chown -R ${SSH_USER}:${SSH_USER} ${INSTALL_DIR}/console/"

echo ""
echo "==> [5/5] Console rsync → VM + restart"
rsync -av --delete --no-owner --no-group \
  -e "ssh -i $SSH_KEY" \
  "$REPO/console/dist/" \
  "$VM:${INSTALL_DIR}/console/"

$SSH "$VM" "sudo chown -R www-data:www-data ${INSTALL_DIR}/console/"

# Sauberer Neustart: stop (synchron, wartet auf vollständiges Beenden),
# dann Port-Cleanup für eventuelle Zombies, dann start.
# Kein sleep-Raten — systemctl stop kehrt erst zurück wenn der Prozess wirklich weg ist.
$SSH "$VM" "sudo systemctl stop ${SERVICE_NAME}; sudo fuser -k 8765/tcp 2>/dev/null; sudo systemctl start ${SERVICE_NAME}"

echo ""
echo "==> [5b/5] Docs rsync → VM"
$SSH "$VM" "sudo mkdir -p ${INSTALL_DIR}/docs && sudo chown -R ${SSH_USER}:${SSH_USER} ${INSTALL_DIR}/docs/"
rsync -av --delete --no-owner --no-group \
  -e "ssh -i $SSH_KEY" \
  "$REPO/docs/" \
  "$VM:${INSTALL_DIR}/docs/"
$SSH "$VM" "sudo chown -R ${INSTALL_USER}:${INSTALL_USER} ${INSTALL_DIR}/docs/"
$SSH "$VM" "sudo cp ${INSTALL_DIR}/docs/handbook.md /agents/hydrahive_support/memory/handbook.md && sudo chown ${INSTALL_USER}:${INSTALL_USER} /agents/hydrahive_support/memory/handbook.md"

echo ""
echo "==> Warte auf Start..."
# Aktiv warten bis active oder max 20s
$SSH "$VM" "for i in \$(seq 1 20); do sleep 1; state=\$(systemctl is-active ${SERVICE_NAME}); [ \"\$state\" = 'active' ] && break; done"
$SSH "$VM" "sudo systemctl status ${SERVICE_NAME} --no-pager | head -4"

echo ""
echo "==> [6/5] Gitea-Status prüfen"
$SSH "$VM" "systemctl is-active gitea && echo 'Gitea läuft' || echo 'WARNUNG: Gitea nicht aktiv — starte...'; sudo systemctl start gitea 2>/dev/null; true"

echo ""
echo "==> Commit-Stand in Update-Status schreiben"
COMMIT=$(git rev-parse --short HEAD)
DEPLOY_DATE=$(date -u +"%Y-%m-%dT%H:%M:%S+00:00")
$SSH "$VM" "sudo bash -c 'echo \"{\\\"status\\\":\\\"ok\\\",\\\"commit\\\":\\\"${COMMIT}\\\",\\\"finished_at\\\":\\\"${DEPLOY_DATE}\\\"}\" > /var/run/hydrahive-update.json && chown ${INSTALL_USER}:${INSTALL_USER} /var/run/hydrahive-update.json'"
echo "   Commit: $COMMIT"

echo ""
echo "✓ Update abgeschlossen"
