#!/bin/bash
# octopos-setup-cron.sh — Cronjob fuer automatisches Backup einrichten
# Einmalig ausfuehren: bash scripts/octopos-setup-cron.sh
#
# Ergebnis: taegliches Backup um 03:00 Uhr
# Log: ~/octopos-backups/cron.log

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_SCRIPT="$SCRIPT_DIR/octopos-backup.sh"
LOG_FILE="$HOME/octopos-backups/cron.log"
CRON_JOB="0 3 * * * $BACKUP_SCRIPT >> $LOG_FILE 2>&1"

# Backup-Dir anlegen
mkdir -p "$HOME/octopos-backups"

# Cronjob nur einmal eintragen
if crontab -l 2>/dev/null | grep -q "octopos-backup"; then
    echo "Cronjob bereits eingetragen:"
    crontab -l | grep octopos-backup
else
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "Cronjob eingetragen: täglich um 03:00 Uhr"
    echo "$CRON_JOB"
fi

echo ""
echo "Prüfen: crontab -l"
echo "Log:    tail -f $LOG_FILE"
