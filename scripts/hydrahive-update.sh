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
# #302: Bei fehlgeschlagenem Pull abbrechen statt stale Code deployen
if ! git pull hydrahive main 2>/dev/null && ! git pull gitea-local main 2>/dev/null && ! git pull 2>/dev/null; then
    echo "   FEHLER: Kein Remote erreichbar — Abbruch, um stale Code zu vermeiden."
    echo "   Manuell prüfen: git remote -v && git status"
    exit 1
fi

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
# Playwright-Browser einmalig installieren (ohne --with-deps, da apt an unsigned Repos scheitert)
$SSH "$VM" "sudo -u ${INSTALL_USER} ${INSTALL_DIR}/venv/bin/playwright install chromium 2>&1 | tail -3 || true"

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
echo "==> [5b/5] Installer rsync → VM"
$SSH "$VM" "sudo chown -R ${SSH_USER}:${SSH_USER} ${INSTALL_DIR}/installer/ 2>/dev/null || true"
rsync -av --delete --no-owner --no-group \
  -e "ssh -i $SSH_KEY" \
  "$REPO/installer/" \
  "$VM:${INSTALL_DIR}/installer/"
$SSH "$VM" "sudo chown -R ${INSTALL_USER}:${INSTALL_USER} ${INSTALL_DIR}/installer/"

echo ""
echo "==> [5c/5] Docs rsync → VM"
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
echo "==> [5d/5] WhatsApp Bridge rsync + Neustart"
$SSH "$VM" "sudo chown -R ${SSH_USER}:${SSH_USER} ${INSTALL_DIR}/whatsapp-bridge/ 2>/dev/null || true"
rsync -av --delete --no-owner --no-group \
  --exclude='node_modules' --exclude='.git' --exclude='*.session' \
  -e "ssh -i $SSH_KEY" \
  "$REPO/whatsapp-bridge/" \
  "$VM:${INSTALL_DIR}/whatsapp-bridge/"
$SSH "$VM" "sudo chown -R ${INSTALL_USER}:${INSTALL_USER} ${INSTALL_DIR}/whatsapp-bridge/"
$SSH "$VM" "if [ ! -d ${INSTALL_DIR}/whatsapp-bridge/node_modules ]; then echo '   node_modules fehlen — führe npm install aus...'; cd ${INSTALL_DIR}/whatsapp-bridge && sudo npm install -q && echo '   npm install OK'; else echo '   node_modules OK'; fi"
$SSH "$VM" "sudo systemctl restart hydrahive-whatsapp-bridge 2>/dev/null && echo '   Bridge neu gestartet' || echo '   Bridge nicht aktiv (übersprungen)'"

echo ""
echo "==> [5e/5] Bundled Agents → VM (--ignore-existing, überschreibt KEINE user-eigenen Agents)"
# Bundled agents werden nur angelegt wenn sie noch nicht existieren (--ignore-existing).
# User-eigene Anpassungen bleiben erhalten.
$SSH "$VM" "sudo mkdir -p /agents && sudo chown -R ${SSH_USER}:${SSH_USER} /agents/"
rsync -av --no-owner --no-group --ignore-existing \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.git' \
  -e "ssh -i $SSH_KEY" \
  "$REPO/agents/" \
  "$VM:/agents/"
# Default-Agents aus installer/default-agents/ (nur neue, --ignore-existing)
if [ -d "$REPO/installer/default-agents" ]; then
  rsync -av --no-owner --no-group --ignore-existing \
    -e "ssh -i $SSH_KEY" \
    "$REPO/installer/default-agents/" \
    "$VM:/agents/"
fi
$SSH "$VM" "sudo chown -R ${INSTALL_USER}:${INSTALL_USER} /agents/"

echo ""
echo "==> [5f/5] nginx-Config sicherstellen (/projects/, A2A, client_max_body_size)"
$SSH "$VM" "sudo bash ${INSTALL_DIR}/installer/modules/16_nginx_update.sh 2>&1 | sed 's/^/   /'" || echo "   (nginx-Update übersprungen — kein Fehler)"

echo ""
echo "==> [6/5] Gitea-Status prüfen"
$SSH "$VM" "systemctl is-active gitea && echo 'Gitea läuft' || echo 'WARNUNG: Gitea nicht aktiv — starte...'; sudo systemctl start gitea 2>/dev/null; true"

echo ""
echo "==> [7/7] Samba-Shares: force group = hydrahive nachrüsten"
SAMBA_INCLUDES="/etc/samba/hydrahive-shares.conf"
$SSH "$VM" "if [ -f $SAMBA_INCLUDES ] && ! grep -q 'force group' $SAMBA_INCLUDES 2>/dev/null; then
  sudo sed -i '/create mask/i\\   force group = hydrahive' $SAMBA_INCLUDES
  sudo smbcontrol smbd reload-config 2>/dev/null || sudo systemctl reload smbd 2>/dev/null
  echo '   force group = hydrahive nachgerüstet + smbd reloaded'
else
  echo '   Samba OK (force group bereits gesetzt oder keine Shares)'
fi" || echo "   (Samba-Check übersprungen)"

echo ""
echo "==> Commit-Stand in Update-Status schreiben"
COMMIT=$(git rev-parse --short HEAD)
DEPLOY_DATE=$(date -u +"%Y-%m-%dT%H:%M:%S+00:00")
$SSH "$VM" "sudo bash -c 'echo \"{\\\"status\\\":\\\"ok\\\",\\\"commit\\\":\\\"${COMMIT}\\\",\\\"finished_at\\\":\\\"${DEPLOY_DATE}\\\"}\" > /var/run/hydrahive-update.json && chown ${INSTALL_USER}:${INSTALL_USER} /var/run/hydrahive-update.json'"
echo "   Commit: $COMMIT"

echo ""
echo "✓ Update abgeschlossen"
