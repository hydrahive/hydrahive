#!/bin/bash
# hydrahive-transfer.sh — Direkter HydraHive Server-zu-Server Transfer
#
# Exportiert die aktuelle Installation, überträgt das Archiv per SSH
# und spielt es auf dem Ziel-Server ein — ohne lokale Zwischenspeicherung.
#
# VORAUSSETZUNG:
#   - Ziel-Server: HydraHive bereits installiert (install.sh ausgeführt)
#   - SSH-Zugang zum Ziel-Server (root oder sudo-User)
#
# Verwendung:
#   sudo bash scripts/hydrahive-transfer.sh \
#     --target user@192.168.1.100 \
#     --key ~/.ssh/id_ed25519 \
#     [--include-amem] [--port 22] [--dry-run]

set -euo pipefail

# ── Optionen ─────────────────────────────────────────────────────────────────
TARGET=""
SSH_KEY="$HOME/.ssh/id_ed25519"
SSH_PORT=22
INCLUDE_AMEM=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target)       TARGET="$2"; shift 2 ;;
        --key)          SSH_KEY="$2"; shift 2 ;;
        --port)         SSH_PORT="$2"; shift 2 ;;
        --include-amem) INCLUDE_AMEM=1; shift ;;
        --dry-run)      DRY_RUN=1; shift ;;
        *) echo "Unbekannte Option: $1"; exit 1 ;;
    esac
done

if [[ -z "$TARGET" ]]; then
    echo "FEHLER: --target user@server ist erforderlich"
    echo ""
    echo "Verwendung:"
    echo "  sudo bash $0 --target user@192.168.1.100 --key ~/.ssh/id_ed25519"
    echo "  sudo bash $0 --target root@newserver --include-amem"
    exit 1
fi

# ── Root-Check ────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]] && [[ $DRY_RUN -eq 0 ]]; then
    echo "FEHLER: Dieses Script muss als root ausgeführt werden."
    echo "       sudo bash $0 --target $TARGET --key $SSH_KEY"
    exit 1
fi

SSH_OPTS="-i $SSH_KEY -p $SSH_PORT -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
log()  { echo "==> $*"; }
info() { echo "    $*"; }
warn() { echo "    WARNUNG: $*"; }

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  HydraHive Transfer                                          ║"
echo "║  $(date '+%Y-%m-%d %H:%M:%S')                                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
info "Quelle:  $(hostname)"
info "Ziel:    $TARGET:$SSH_PORT"
info "SSH-Key: $SSH_KEY"
info "A-MEM:   $([ $INCLUDE_AMEM -eq 1 ] && echo 'inkludiert' || echo 'nicht inkludiert')"
echo ""

if [[ $DRY_RUN -eq 1 ]]; then
    echo "[DRY-RUN] — keine Änderungen werden vorgenommen"
    echo ""
fi

# ── SSH-Verbindung testen ─────────────────────────────────────────────────────
log "[0] SSH-Verbindung testen"
if [[ $DRY_RUN -eq 0 ]]; then
    if ! ssh $SSH_OPTS "$TARGET" "echo 'SSH OK'" 2>/dev/null; then
        echo "FEHLER: SSH-Verbindung zu $TARGET fehlgeschlagen."
        echo "       Prüfe: ssh $SSH_OPTS $TARGET"
        exit 1
    fi
    info "SSH-Verbindung: OK"

    # Prüfe ob hydrahive auf Ziel installiert ist
    if ! ssh $SSH_OPTS "$TARGET" "test -d /etc/hydrahive" 2>/dev/null; then
        echo "FEHLER: /etc/hydrahive/ auf Ziel-Server nicht gefunden."
        echo "       HydraHive muss auf dem Ziel-Server zuerst installiert werden."
        exit 1
    fi
    info "Ziel-Server: HydraHive installiert"
fi
echo ""

# ── Passwort abfragen ─────────────────────────────────────────────────────────
log "[1] Verschlüsselungs-Passwort"
echo ""
echo "  Das Archiv wird während der Übertragung AES-256-verschlüsselt."
echo ""
read -s -p "  Passwort: " TRANSFER_PASS
echo ""
read -s -p "  Bestätigung: " TRANSFER_PASS2
echo ""
if [[ "$TRANSFER_PASS" != "$TRANSFER_PASS2" ]]; then
    echo "FEHLER: Passwörter stimmen nicht überein."
    exit 1
fi
if [[ ${#TRANSFER_PASS} -lt 8 ]]; then
    echo "FEHLER: Passwort muss mindestens 8 Zeichen lang sein."
    exit 1
fi
echo ""

if [[ $DRY_RUN -eq 1 ]]; then
    echo "Dry-Run abgeschlossen."
    echo "Für echten Transfer: sudo bash $0 --target $TARGET --key $SSH_KEY"
    exit 0
fi

# ── A-MEM stoppen (falls inkludiert) ─────────────────────────────────────────
AMEM_WAS_RUNNING=0
if [[ $INCLUDE_AMEM -eq 1 ]]; then
    log "[2] A-MEM pausieren (für konsistenten DB-Snapshot)"
    if systemctl is-active --quiet hydrahive-amem 2>/dev/null; then
        systemctl stop hydrahive-amem
        AMEM_WAS_RUNNING=1
        info "hydrahive-amem gestoppt"
    fi
    echo ""
fi

trap '[[ $AMEM_WAS_RUNNING -eq 1 ]] && systemctl start hydrahive-amem 2>/dev/null || true' EXIT

# ── Manifest erstellen ────────────────────────────────────────────────────────
TMPDIR_TRANSFER=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TRANSFER"; [[ $AMEM_WAS_RUNNING -eq 1 ]] && systemctl start hydrahive-amem 2>/dev/null || true' EXIT

HH_VERSION=""
[[ -f /opt/hydrahive/core/pyproject.toml ]] && \
    HH_VERSION=$(grep '^version' /opt/hydrahive/core/pyproject.toml 2>/dev/null | head -1 | cut -d'"' -f2 || echo "")

cat > "$TMPDIR_TRANSFER/hydrahive-export-manifest.json" << MANIFEST
{
  "version": "${HH_VERSION:-unknown}",
  "exported_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "hostname": "$(hostname)",
  "include_amem": $INCLUDE_AMEM,
  "transfer_mode": true
}
MANIFEST

# ── Streaming Transfer ────────────────────────────────────────────────────────
log "[3] Streaming Transfer zu $TARGET"
info "tar | gzip | openssl → ssh → openssl | tar (kein lokales Klartext-Archiv)"
echo ""

IMPORT_SCRIPT=$(cat << 'REMOTE_IMPORT'
set -e
TMPDIR_REMOTE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_REMOTE"' EXIT

# Services stoppen
for svc in hydrahive-core hydrahive-amem hydrahive-amem-search-ui; do
    systemctl is-active --quiet "$svc" 2>/dev/null && systemctl stop "$svc" && echo "gestoppt: $svc" || true
done

# Sicherheits-Backup
# #301: Backup-Fehler nicht ignorieren
SAFETY="/var/backups/hydrahive-pre-transfer-$(date +%Y%m%d-%H%M%S).tar.gz"
if ! tar -czf "$SAFETY" /agents/ /etc/hydrahive/users.json /etc/hydrahive/jwt_secret /etc/hydrahive/internal_secret 2>/dev/null; then
    echo "FEHLER: Sicherheits-Backup fehlgeschlagen — Transfer abgebrochen"
    exit 1
fi
echo "backup: $SAFETY"

# Entschlüsseln und entpacken
# #294: kein --absolute-names — nur erlaubte Pfade extrahieren
openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -pass "env:HYDRAHIVE_PASS" \
| tar -xz --overwrite \
    --exclude '*/hydrahive-export-manifest.json' \
    --exclude '*..*' \
    -C / \
    agents/ etc/hydrahive/ \
    2>/dev/null || echo "warn: Einige Pfade nicht im Archiv (OK)"

# Berechtigungen
id hydrahive &>/dev/null && chown -R hydrahive:hydrahive /agents/ 2>/dev/null || true
id hydrahive &>/dev/null && chown -R root:hydrahive /etc/hydrahive/ 2>/dev/null || true
chmod 750 /etc/hydrahive/ 2>/dev/null || true
find /etc/hydrahive -type f -exec chmod 640 {} \; 2>/dev/null || true
[[ -f /var/log/hydrahive/notifications.db ]] && chown hydrahive:hydrahive /var/log/hydrahive/notifications.db 2>/dev/null || true

# BM25-Indizes löschen
find /agents -name "memory_index.db" -delete 2>/dev/null || true

# Services starten
for svc in hydrahive-core hydrahive-amem hydrahive-amem-search-ui; do
    systemctl is-enabled --quiet "$svc" 2>/dev/null && systemctl start "$svc" && echo "gestartet: $svc" || true
done

echo "TRANSFER ABGESCHLOSSEN"
REMOTE_IMPORT
)

tar \
    --create \
    --gzip \
    --absolute-names \
    --ignore-failed-read \
    "$TMPDIR_TRANSFER/hydrahive-export-manifest.json" \
    /agents/ \
    --exclude '/agents/*/.sessions' \
    --exclude '/agents/*/memory_index.db' \
    $(find /etc/hydrahive -maxdepth 1 -not -path '/etc/hydrahive/tls' -not -name '*.crt' -not -name '*.key' | tail -n +2 | tr '\n' ' ') \
    $([ -f /var/log/hydrahive/notifications.db ] && echo "/var/log/hydrahive/notifications.db" || echo "") \
    $([ $INCLUDE_AMEM -eq 1 ] && echo "/var/lib/hydrahive/amem/chromadb_data/" || echo "") \
    2>/dev/null \
| openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -pass "pass:$TRANSFER_PASS" \
| ssh $SSH_OPTS "$TARGET" \
    "HYDRAHIVE_PASS='$TRANSFER_PASS' bash -s" \
    << "SSHEOF"
$(echo "$IMPORT_SCRIPT")
SSHEOF

# ── A-MEM wieder starten ──────────────────────────────────────────────────────
if [[ $AMEM_WAS_RUNNING -eq 1 ]]; then
    log "[4] A-MEM wieder starten (Quelle)"
    systemctl start hydrahive-amem
    info "hydrahive-amem gestartet"
    echo ""
fi

# ── Abschluss ─────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✓ Transfer abgeschlossen                                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Status auf Ziel-Server prüfen:"
echo "    ssh $SSH_OPTS $TARGET 'systemctl status hydrahive-core'"
echo "    ssh $SSH_OPTS $TARGET 'journalctl -u hydrahive-core -n 20'"
echo ""
