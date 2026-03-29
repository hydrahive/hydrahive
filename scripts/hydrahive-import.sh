#!/bin/bash
# hydrahive-import.sh — HydraHive-Export einspielen
#
# Entschlüsselt und importiert ein mit hydrahive-export.sh erstelltes Archiv
# auf eine frische HydraHive-Installation.
#
# VORAUSSETZUNG: HydraHive muss bereits installiert sein (install.sh ausgeführt).
# Die Services werden kurz gestoppt, Daten eingespielt, dann wieder gestartet.
#
# Verwendung:
#   sudo bash scripts/hydrahive-import.sh --input /tmp/hydrahive-export.tar.gz.enc
#   sudo bash scripts/hydrahive-import.sh --input /tmp/export.tar.gz.enc --dry-run

set -euo pipefail

# ── Optionen ─────────────────────────────────────────────────────────────────
INPUT=""
DRY_RUN=0
FORCE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --input)   INPUT="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --force)   FORCE=1; shift ;;
        *) echo "Unbekannte Option: $1"; exit 1 ;;
    esac
done

if [[ -z "$INPUT" ]]; then
    echo "FEHLER: --input DATEI ist erforderlich"
    echo "Verwendung: sudo bash $0 --input /tmp/hydrahive-export.tar.gz.enc"
    exit 1
fi
if [[ ! -f "$INPUT" ]]; then
    echo "FEHLER: Datei nicht gefunden: $INPUT"
    exit 1
fi

# ── Root-Check ────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]] && [[ $DRY_RUN -eq 0 ]]; then
    echo "FEHLER: Dieses Script muss als root ausgeführt werden."
    echo "       sudo bash $0 --input $INPUT"
    exit 1
fi

log()  { echo "==> $*"; }
info() { echo "    $*"; }
warn() { echo "    WARNUNG: $*"; }

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  HydraHive Import                                            ║"
echo "║  $(date '+%Y-%m-%d %H:%M:%S')                                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

info "Archiv: $INPUT ($(du -sh "$INPUT" | cut -f1))"
echo ""

if [[ $DRY_RUN -eq 1 ]]; then
    echo "[DRY-RUN] — keine Änderungen werden vorgenommen"
    echo ""
fi

# ── Systemcheck ───────────────────────────────────────────────────────────────
log "[0] Systemcheck"

if [[ ! -d /etc/hydrahive ]]; then
    echo "FEHLER: /etc/hydrahive/ nicht gefunden."
    echo "       HydraHive muss zuerst installiert werden (install.sh)."
    exit 1
fi

if [[ -d /agents ]] && [[ -n "$(ls -A /agents/ 2>/dev/null)" ]] && [[ $FORCE -eq 0 ]]; then
    echo ""
    warn "/agents/ ist nicht leer — vorhandene Daten würden überschrieben."
    echo ""
    echo "  Bestehende Agenten:"
    ls /agents/ 2>/dev/null | sed 's/^/    /'
    echo ""
    read -p "  Trotzdem importieren? (yes/no): " CONFIRM
    if [[ "$CONFIRM" != "yes" ]]; then
        echo "Import abgebrochen."
        exit 0
    fi
fi
echo ""

# ── Passwort abfragen ─────────────────────────────────────────────────────────
log "[1] Entschlüsselung"
echo ""
read -s -p "  Export-Passwort: " IMPORT_PASS
echo ""
echo ""

# Passwort testen (Manifest lesen)
TMPDIR_IMPORT=$(mktemp -d)
trap 'rm -rf "$TMPDIR_IMPORT"' EXIT

info "Entschlüssele und teste Archiv..."
if ! openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -pass "pass:$IMPORT_PASS" \
        -in "$INPUT" \
    | tar -xz -C "$TMPDIR_IMPORT" --wildcards "*/hydrahive-export-manifest.json" \
        2>/dev/null; then
    echo "FEHLER: Entschlüsselung fehlgeschlagen — falsches Passwort oder beschädigtes Archiv."
    exit 1
fi

MANIFEST=$(find "$TMPDIR_IMPORT" -name "hydrahive-export-manifest.json" | head -1)
if [[ -f "$MANIFEST" ]]; then
    info "Manifest gefunden:"
    python3 -c "
import json, sys
d = json.load(open('$MANIFEST'))
print(f\"    Exportiert: {d.get('exported_at', '?')} von {d.get('hostname', '?')}\")
print(f\"    HH-Version: {d.get('version', '?')}\")
print(f\"    A-MEM inkl.: {'ja' if d.get('include_amem') else 'nein'}\")
" 2>/dev/null || cat "$MANIFEST"
fi
echo ""

if [[ $DRY_RUN -eq 1 ]]; then
    echo "Dry-Run: Archiv ist gültig und entschlüsselbar."
    echo "Für echten Import: sudo bash $0 --input $INPUT"
    exit 0
fi

# ── Services stoppen ──────────────────────────────────────────────────────────
log "[2] HydraHive Services stoppen"
SERVICES_TO_RESTART=()
for svc in hydrahive-core hydrahive-amem hydrahive-amem-search-ui; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        systemctl stop "$svc"
        SERVICES_TO_RESTART+=("$svc")
        info "Gestoppt: $svc"
    fi
done
echo ""

# ── Sicherheits-Backup der bestehenden Daten ─────────────────────────────────
log "[3] Sicherheits-Backup bestehender Daten"
SAFETY_BACKUP="/var/backups/hydrahive-pre-import-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$SAFETY_BACKUP" \
    /agents/ \
    /etc/hydrahive/users.json \
    /etc/hydrahive/admin_credentials \
    /etc/hydrahive/jwt_secret \
    /etc/hydrahive/internal_secret \
    2>/dev/null || true
info "Backup erstellt: $SAFETY_BACKUP"
info "ROLLBACK: tar -xzf $SAFETY_BACKUP -C /"
echo ""

# ── Archiv einspielen ─────────────────────────────────────────────────────────
log "[4] Daten einspielen"
info "Entschlüssele und entpacke..."

openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -pass "pass:$IMPORT_PASS" \
    -in "$INPUT" \
| tar -xz \
    --absolute-names \
    --overwrite \
    --exclude '*/hydrahive-export-manifest.json' \
    -C / \
    2>/dev/null

info "Daten eingespielt"
echo ""

# ── Berechtigungen setzen ─────────────────────────────────────────────────────
log "[5] Berechtigungen korrigieren"
if id hydrahive &>/dev/null; then
    chown -R hydrahive:hydrahive /agents/ 2>/dev/null && info "/agents/ → hydrahive:hydrahive"
    chown -R root:hydrahive /etc/hydrahive/ 2>/dev/null
    chmod 750 /etc/hydrahive/ 2>/dev/null
    find /etc/hydrahive -type f -exec chmod 640 {} \; 2>/dev/null
    info "/etc/hydrahive/ → root:hydrahive (750/640)"
fi
if [[ -f /var/log/hydrahive/notifications.db ]]; then
    chown hydrahive:hydrahive /var/log/hydrahive/notifications.db 2>/dev/null || true
fi
echo ""

# ── BM25-Index neu aufbauen ───────────────────────────────────────────────────
log "[6] Memory-Indizes zurücksetzen"
find /agents -name "memory_index.db" -delete 2>/dev/null && info "BM25-Indizes gelöscht (werden beim Start neu aufgebaut)"
echo ""

# ── Services starten ──────────────────────────────────────────────────────────
log "[7] Services starten"
for svc in "${SERVICES_TO_RESTART[@]}"; do
    systemctl start "$svc" && info "Gestartet: $svc" || warn "$svc konnte nicht gestartet werden"
done
echo ""

# ── Abschluss ─────────────────────────────────────────────────────────────────
sleep 3
CORE_STATUS=$(systemctl is-active hydrahive-core 2>/dev/null || echo "unbekannt")

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✓ Import abgeschlossen                                      ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  hydrahive-core Status: $CORE_STATUS"
echo ""
echo "  Status prüfen:"
echo "    systemctl status hydrahive-core"
echo "    journalctl -u hydrahive-core -n 30"
echo ""
echo "  Rollback falls nötig:"
echo "    systemctl stop hydrahive-core"
echo "    tar -xzf $SAFETY_BACKUP -C /"
echo "    systemctl start hydrahive-core"
echo ""
