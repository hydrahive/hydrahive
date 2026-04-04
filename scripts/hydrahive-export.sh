#!/bin/bash
# hydrahive-export.sh — HydraHive-Installation exportieren (verschlüsseltes Archiv)
#
# Erstellt ein portables, AES-256-verschlüsseltes Archiv aller HydraHive-Daten:
#   - Agenten (Configs, Memory, Skills, Soul)
#   - Konfiguration (/etc/hydrahive/, ohne TLS-Zertifikate)
#   - Optionale A-MEM-Daten (--include-amem)
#
# Verwendung:
#   sudo bash scripts/hydrahive-export.sh [--output DATEI] [--include-amem] [--dry-run]
#
# Beispiel:
#   sudo bash scripts/hydrahive-export.sh --output /tmp/hydrahive-export.tar.gz.enc

set -euo pipefail

# ── Optionen ─────────────────────────────────────────────────────────────────
OUTPUT=""
INCLUDE_AMEM=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output)    OUTPUT="$2"; shift 2 ;;
        --include-amem) INCLUDE_AMEM=1; shift ;;
        --dry-run)   DRY_RUN=1; shift ;;
        *) echo "Unbekannte Option: $1"; exit 1 ;;
    esac
done

if [[ -z "$OUTPUT" ]]; then
    OUTPUT="/tmp/hydrahive-export-$(date +%Y%m%d-%H%M%S).tar.gz.enc"
fi

# ── Root-Check ────────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]] && [[ $DRY_RUN -eq 0 ]]; then
    echo "FEHLER: Dieses Script muss als root ausgeführt werden."
    echo "       sudo bash $0 $*"
    exit 1
fi

log()  { echo "==> $*"; }
info() { echo "    $*"; }

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  HydraHive Export                                            ║"
echo "║  $(date '+%Y-%m-%d %H:%M:%S')                                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── Systemcheck ───────────────────────────────────────────────────────────────
log "[0] Systemcheck"

if [[ ! -d /agents ]]; then
    echo "FEHLER: /agents/ nicht gefunden — ist HydraHive installiert?"
    exit 1
fi
if [[ ! -d /etc/hydrahive ]]; then
    echo "FEHLER: /etc/hydrahive/ nicht gefunden"
    exit 1
fi

# HydraHive-Version ermitteln
HH_VERSION=""
if [[ -f /opt/hydrahive/VERSION ]]; then
    HH_VERSION=$(cat /opt/hydrahive/VERSION)
elif [[ -f /opt/hydrahive/core/pyproject.toml ]]; then
    HH_VERSION=$(grep '^version' /opt/hydrahive/core/pyproject.toml 2>/dev/null | head -1 | cut -d'"' -f2 || echo "unknown")
fi
info "HydraHive-Version: ${HH_VERSION:-unbekannt}"
info "Ziel-Archiv:       $OUTPUT"
info "A-MEM inkludiert:  $([ $INCLUDE_AMEM -eq 1 ] && echo 'ja' || echo 'nein')"
echo ""

if [[ $DRY_RUN -eq 1 ]]; then
    echo "[DRY-RUN] — keine Änderungen werden vorgenommen"
    echo ""
fi

# ── Größenabschätzung ─────────────────────────────────────────────────────────
log "[1] Größenabschätzung"
AGENTS_SIZE=$(du -sh /agents/ 2>/dev/null | cut -f1)
ETC_SIZE=$(du -sh /etc/hydrahive/ 2>/dev/null | cut -f1)
DB_SIZE=$(du -sh /var/log/hydrahive/notifications.db 2>/dev/null | cut -f1 || echo "0")
info "/agents/:              $AGENTS_SIZE"
info "/etc/hydrahive/:       $ETC_SIZE"
info "notifications.db:      $DB_SIZE"
if [[ $INCLUDE_AMEM -eq 1 ]]; then
    AMEM_SIZE=$(du -sh /var/lib/hydrahive/amem/chromadb_data/ 2>/dev/null | cut -f1 || echo "n/a")
    info "A-MEM ChromaDB:        $AMEM_SIZE"
fi
echo ""

if [[ $DRY_RUN -eq 1 ]]; then
    echo "Dry-Run abgeschlossen. Für echten Export: sudo bash $0 --output $OUTPUT"
    exit 0
fi

# ── A-MEM stoppen (für konsistentes Backup) ──────────────────────────────────
AMEM_WAS_RUNNING=0
if [[ $INCLUDE_AMEM -eq 1 ]]; then
    log "[2] A-MEM pausieren (für konsistentes DB-Snapshot)"
    if systemctl is-active --quiet hydrahive-amem 2>/dev/null; then
        systemctl stop hydrahive-amem
        AMEM_WAS_RUNNING=1
        info "hydrahive-amem gestoppt"
    else
        info "hydrahive-amem war bereits gestoppt"
    fi
    echo ""
fi

# ── Passwort abfragen ─────────────────────────────────────────────────────────
log "[3] Verschlüsselungs-Passwort"
echo ""
echo "  Das Archiv wird mit AES-256 verschlüsselt."
echo "  Merke dir das Passwort — ohne es kann das Archiv nicht entschlüsselt werden."
echo ""
read -s -p "  Passwort: " EXPORT_PASS
echo ""
read -s -p "  Bestätigung: " EXPORT_PASS2
echo ""
if [[ "$EXPORT_PASS" != "$EXPORT_PASS2" ]]; then
    echo "FEHLER: Passwörter stimmen nicht überein."
    [[ $AMEM_WAS_RUNNING -eq 1 ]] && systemctl start hydrahive-amem
    exit 1
fi
if [[ ${#EXPORT_PASS} -lt 8 ]]; then
    echo "FEHLER: Passwort muss mindestens 8 Zeichen haben."
    [[ $AMEM_WAS_RUNNING -eq 1 ]] && systemctl start hydrahive-amem
    exit 1
fi
echo ""

# ── Manifest erstellen ────────────────────────────────────────────────────────
log "[4] Archiv erstellen und verschlüsseln"

TMPDIR_EXPORT=$(mktemp -d)
trap 'rm -rf "$TMPDIR_EXPORT"; [[ $AMEM_WAS_RUNNING -eq 1 ]] && systemctl start hydrahive-amem 2>/dev/null || true' EXIT

# Manifest
cat > "$TMPDIR_EXPORT/hydrahive-export-manifest.json" << MANIFEST
{
  "version": "${HH_VERSION:-unknown}",
  "exported_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "hostname": "$(hostname)",
  "include_amem": $INCLUDE_AMEM,
  "contents": [
    "/agents/",
    "/etc/hydrahive/ (ohne TLS-Zertifikate)",
    "/var/log/hydrahive/notifications.db"$([ $INCLUDE_AMEM -eq 1 ] && echo ',
    "/var/lib/hydrahive/amem/chromadb_data/"' || echo '')
  ]
}
MANIFEST

info "Sammle Daten..."

# tar + gzip + encrypt (streaming, kein temporäres Klartext-Archiv)
tar \
    --create \
    --gzip \
    --absolute-names \
    --ignore-failed-read \
    "$TMPDIR_EXPORT/hydrahive-export-manifest.json" \
    /agents/ \
    --exclude '/agents/*/.sessions' \
    --exclude '/agents/*/memory_index.db' \
    $(find /etc/hydrahive -maxdepth 1 -not -path '/etc/hydrahive/tls' -not -name '*.crt' -not -name '*.key' -not -name 'jwt_secret' -not -name 'internal_secret' | tail -n +2 | sed 's/^//' | tr '\n' ' ') \
    $([ -f /var/log/hydrahive/notifications.db ] && echo "/var/log/hydrahive/notifications.db" || echo "") \
    $([ $INCLUDE_AMEM -eq 1 ] && echo "/var/lib/hydrahive/amem/chromadb_data/" || echo "") \
    2>/dev/null \
| openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -pass "pass:$EXPORT_PASS" \
> "$OUTPUT"

EXPORT_SIZE=$(du -sh "$OUTPUT" 2>/dev/null | cut -f1)
info "Archiv erstellt: $OUTPUT ($EXPORT_SIZE)"
echo ""

# ── A-MEM wieder starten ──────────────────────────────────────────────────────
if [[ $AMEM_WAS_RUNNING -eq 1 ]]; then
    log "[5] A-MEM wieder starten"
    systemctl start hydrahive-amem
    info "hydrahive-amem gestartet"
    echo ""
fi

# ── Abschluss ─────────────────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  ✓ Export abgeschlossen                                      ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "  Archiv:  $OUTPUT"
echo "  Größe:   $EXPORT_SIZE"
echo ""
echo "  Import auf neuem Server:"
echo "    scp $OUTPUT user@newserver:/tmp/"
echo "    sudo bash hydrahive-import.sh --input /tmp/$(basename "$OUTPUT")"
echo ""
echo "  Direkter Transfer:"
echo "    sudo bash hydrahive-transfer.sh --target user@newserver --key ~/.ssh/id_ed25519"
echo ""
