#!/bin/bash
# octopos-backup.sh — OctopOS-Daten von der VM sichern
# Läuft auf Lilith, zieht Backup per rsync/ssh
# Backups landen in: ~/octopos-backups/YYYY-MM-DD_HH-MM/
# Verwendung: ./scripts/octopos-backup.sh

set -e

VM="octopos@192.168.178.181"
SSH_KEY="$HOME/.ssh/claude_key_nopass"
SSH="ssh -i $SSH_KEY"
BACKUP_BASE="$HOME/octopos-backups"
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M")
DEST="$BACKUP_BASE/$TIMESTAMP"

mkdir -p "$DEST"

echo "==> OctopOS Backup — $TIMESTAMP"
echo "    Ziel: $DEST"
echo ""

echo "==> [1/3] /etc/octopos/ (Secrets, Users, Config)"
rsync -av -e "ssh -i $SSH_KEY" \
  "$VM:/etc/octopos/" \
  "$DEST/etc-octopos/"

echo ""
echo "==> [2/3] /agents/ (Agent-Konfigurationen)"
rsync -av -e "ssh -i $SSH_KEY" \
  "$VM:/agents/" \
  "$DEST/agents/"

echo ""
echo "==> [3/3] /projects/ (Projekte + Sessions)"
rsync -av --ignore-errors -e "ssh -i $SSH_KEY" \
  "$VM:/projects/" \
  "$DEST/projects/"

echo ""
echo "==> Backup-Größe:"
du -sh "$DEST"

# Alte Backups aufräumen — nur die letzten 10 behalten
echo ""
echo "==> Alte Backups aufräumen (behalte letzte 10):"
ls -dt "$BACKUP_BASE"/*/  | tail -n +11 | while read dir; do
  echo "    Lösche: $dir"
  rm -rf "$dir"
done

echo ""
echo "✓ Backup abgeschlossen: $DEST"
