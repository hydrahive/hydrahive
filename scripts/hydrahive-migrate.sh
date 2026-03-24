#!/bin/bash
# hydrahive-migrate.sh — Migriert einen laufenden OctopOS-Server zu HydraHive
#
# Führt folgende Schritte durch:
#   1. Stoppt octopos-Services
#   2. Erstellt hydrahive-User/Gruppe (falls nicht vorhanden)
#   3. Verlinkt /opt/octopos → /opt/hydrahive (oder verschiebt)
#   4. Verlinkt /etc/octopos → /etc/hydrahive (falls noch nicht)
#   5. Benennt Service-Files um
#   6. Startet hydrahive-Services
#
# VORSICHT: Nur auf Produktionssystemen ausführen die noch OctopOS-Namen haben!
# Prüfe vorher: systemctl list-units | grep octopos
#
# Verwendung: sudo bash scripts/hydrahive-migrate.sh [--dry-run]
#             Ohne --dry-run werden Änderungen tatsächlich durchgeführt.

set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=1
    echo "[DRY-RUN] Keine Änderungen werden vorgenommen."
fi

log()  { echo "==> $*"; }
info() { echo "    $*"; }
warn() { echo "    WARNUNG: $*"; }

run() {
    if [[ $DRY_RUN -eq 1 ]]; then
        echo "    [würde ausführen] $*"
    else
        "$@"
    fi
}

# ── Root-Check ──────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]] && [[ $DRY_RUN -eq 0 ]]; then
    echo "FEHLER: Dieses Skript muss als root ausgeführt werden."
    echo "       Verwendung: sudo bash $0"
    exit 1
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║  HydraHive Migration — OctopOS → HydraHive                  ║"
echo "║  $(date '+%Y-%m-%d %H:%M:%S')                                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── Prüfe ob OctopOS überhaupt vorhanden ist ────────────────────────────────
log "[0/6] Systemzustand prüfen"

OCTOPOS_SERVICES=()
for svc in octopos-core octopos-conduwuit octopos-whatsapp-bridge octopos-amem; do
    if systemctl list-unit-files "${svc}.service" &>/dev/null 2>&1 | grep -q "${svc}"; then
        OCTOPOS_SERVICES+=("$svc")
    fi
done

if [[ ${#OCTOPOS_SERVICES[@]} -eq 0 ]] && [[ ! -d /opt/octopos ]] && [[ ! -d /etc/octopos ]]; then
    warn "Kein OctopOS-System gefunden — Migration nicht notwendig."
    echo "    Prüfe: /opt/octopos, /etc/octopos, systemctl list-units | grep octopos"
    exit 0
fi

info "Gefundene OctopOS-Services: ${OCTOPOS_SERVICES[*]:-keine}"
info "/opt/octopos existiert: $([ -d /opt/octopos ] && echo 'ja' || echo 'nein')"
info "/etc/octopos existiert: $([ -d /etc/octopos ] && echo 'ja' || echo 'nein')"
echo ""

# ── Backup erstellen ─────────────────────────────────────────────────────────
BACKUP_DIR="/var/backups/hydrahive-migration-$(date +%Y%m%d-%H%M%S)"
log "[PRE] Backup erstellen: $BACKUP_DIR"
if [[ $DRY_RUN -eq 0 ]]; then
    mkdir -p "$BACKUP_DIR"
    # Service-Files sichern
    for svc in "${OCTOPOS_SERVICES[@]}"; do
        svc_file="/etc/systemd/system/${svc}.service"
        if [[ -f "$svc_file" ]]; then
            cp "$svc_file" "$BACKUP_DIR/" && info "Gesichert: $svc_file"
        fi
    done
    # /etc/octopos sichern (nur Configs, keine Secrets-Warnung)
    if [[ -d /etc/octopos ]]; then
        cp -a /etc/octopos "$BACKUP_DIR/etc-octopos" && info "Gesichert: /etc/octopos → $BACKUP_DIR/etc-octopos"
    fi
    info "Backup abgeschlossen: $BACKUP_DIR"
    info "ROLLBACK-HINWEIS: Im Fehlerfall Backup wiederherstellen mit:"
    info "  cp $BACKUP_DIR/*.service /etc/systemd/system/"
    info "  systemctl daemon-reload && systemctl start octopos-core"
else
    info "[würde erstellen] $BACKUP_DIR"
fi
echo ""

# ── Schritt 1: OctopOS-Services stoppen ─────────────────────────────────────
log "[1/6] OctopOS-Services stoppen"
for svc in "${OCTOPOS_SERVICES[@]}"; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        info "Stoppe: $svc"
        run systemctl stop "$svc"
    else
        info "Bereits gestoppt: $svc"
    fi
done
echo ""

# ── Schritt 2: hydrahive User/Gruppe anlegen ─────────────────────────────────
log "[2/6] hydrahive User/Gruppe anlegen"
if id hydrahive &>/dev/null; then
    info "User 'hydrahive' existiert bereits"
else
    info "Lege User 'hydrahive' an"
    run useradd --system --no-create-home --shell /usr/sbin/nologin hydrahive
fi

# octopos-User-Mitgliedschaften auf hydrahive übertragen (sudo etc.)
if id octopos &>/dev/null; then
    OCTOPOS_GROUPS=$(id -Gn octopos 2>/dev/null | tr ' ' '\n' | grep -v '^octopos$' || true)
    for grp in $OCTOPOS_GROUPS; do
        info "Füge hydrahive zur Gruppe '$grp' hinzu"
        run usermod -aG "$grp" hydrahive 2>/dev/null || warn "Gruppe '$grp' konnte nicht hinzugefügt werden"
    done
fi
echo ""

# ── Schritt 3: /opt/octopos → /opt/hydrahive ────────────────────────────────
log "[3/6] /opt/octopos → /opt/hydrahive"
if [[ -d /opt/octopos ]] && [[ ! -L /opt/octopos ]]; then
    if [[ -d /opt/hydrahive ]]; then
        warn "/opt/hydrahive existiert bereits — überspringe Umbenennung"
        info "Symlink /opt/octopos → /opt/hydrahive nicht möglich (beide existieren)"
        info "Manuell prüfen: ls -la /opt/octopos /opt/hydrahive"
    else
        info "Verschiebe /opt/octopos → /opt/hydrahive"
        run mv /opt/octopos /opt/hydrahive
        info "Erstelle Symlink /opt/octopos → /opt/hydrahive (Rückwärtskompatibilität)"
        run ln -s /opt/hydrahive /opt/octopos
        info "Setze Eigentümer auf hydrahive"
        run chown -R hydrahive:hydrahive /opt/hydrahive
    fi
elif [[ -L /opt/octopos ]]; then
    info "/opt/octopos ist bereits ein Symlink — prüfe Ziel"
    TARGET=$(readlink /opt/octopos)
    info "Symlink zeigt auf: $TARGET"
else
    info "/opt/octopos nicht vorhanden — nichts zu tun"
fi
echo ""

# ── Schritt 4: /etc/octopos → /etc/hydrahive ────────────────────────────────
log "[4/6] /etc/octopos → /etc/hydrahive"
if [[ -d /etc/octopos ]] && [[ ! -L /etc/octopos ]]; then
    if [[ -d /etc/hydrahive ]]; then
        warn "/etc/hydrahive existiert bereits"
        info "Erstelle Symlink /etc/octopos → /etc/hydrahive (Configs bleiben erhalten)"
        # Wenn /etc/hydrahive leer ist, verschieben; sonst Symlink von octopos
        if [[ -z "$(ls -A /etc/hydrahive 2>/dev/null)" ]]; then
            run rmdir /etc/hydrahive
            run mv /etc/octopos /etc/hydrahive
            run ln -s /etc/hydrahive /etc/octopos
        else
            info "Beide Verzeichnisse haben Inhalt — manuell zusammenführen:"
            info "  diff -r /etc/octopos /etc/hydrahive"
        fi
    else
        info "Verschiebe /etc/octopos → /etc/hydrahive"
        run mv /etc/octopos /etc/hydrahive
        info "Erstelle Symlink /etc/octopos → /etc/hydrahive (Rückwärtskompatibilität)"
        run ln -s /etc/hydrahive /etc/octopos
        info "Setze Berechtigungen"
        run chown -R hydrahive:hydrahive /etc/hydrahive
        run chmod 750 /etc/hydrahive
    fi
elif [[ -L /etc/octopos ]]; then
    info "/etc/octopos ist bereits ein Symlink"
else
    info "/etc/octopos nicht vorhanden — nichts zu tun"
fi
echo ""

# ── Schritt 5: Service-Files umbenennen ──────────────────────────────────────
log "[5/6] Systemd Service-Files umbenennen"
declare -A SERVICE_MAP=(
    ["octopos-core"]="hydrahive-core"
    ["octopos-conduwuit"]="hydrahive-conduwuit"
    ["octopos-whatsapp-bridge"]="hydrahive-whatsapp-bridge"
    ["octopos-amem"]="hydrahive-amem"
)

for old_name in "${!SERVICE_MAP[@]}"; do
    new_name="${SERVICE_MAP[$old_name]}"
    old_file="/etc/systemd/system/${old_name}.service"
    new_file="/etc/systemd/system/${new_name}.service"

    if [[ -f "$old_file" ]]; then
        if [[ -f "$new_file" ]]; then
            info "${new_name}.service existiert bereits — überspringe"
        else
            info "Kopiere: ${old_name}.service → ${new_name}.service"
            run cp "$old_file" "$new_file"
            # Interne Referenzen auf octopos in der Service-Datei ersetzen
            if [[ $DRY_RUN -eq 0 ]]; then
                sed -i \
                    -e "s|/opt/octopos/|/opt/hydrahive/|g" \
                    -e "s|User=octopos|User=hydrahive|g" \
                    -e "s|Group=octopos|Group=hydrahive|g" \
                    -e "s|octopos-conduwuit|hydrahive-conduwuit|g" \
                    -e "s|octopos-amem|hydrahive-amem|g" \
                    -e "s|octopos-whatsapp-bridge|hydrahive-whatsapp-bridge|g" \
                    -e "s|octopos-core|hydrahive-core|g" \
                    "$new_file"
                info "Pfade in ${new_name}.service aktualisiert"
            fi
        fi
    else
        info "${old_name}.service nicht gefunden — überspringe"
    fi
done

if [[ $DRY_RUN -eq 0 ]]; then
    run systemctl daemon-reload
    info "systemctl daemon-reload ausgeführt"
fi
echo ""

# ── Schritt 6: hydrahive-Services starten ───────────────────────────────────
log "[6/6] hydrahive-Services starten"
for old_name in "${!SERVICE_MAP[@]}"; do
    new_name="${SERVICE_MAP[$old_name]}"
    new_file="/etc/systemd/system/${new_name}.service"
    if [[ -f "$new_file" ]] || [[ $DRY_RUN -eq 1 ]]; then
        info "Starte: $new_name"
        run systemctl enable --now "$new_name" || warn "$new_name konnte nicht gestartet werden"
    fi
done
echo ""

# ── Zusammenfassung ──────────────────────────────────────────────────────────
log "Migration abgeschlossen"
echo ""
if [[ $DRY_RUN -eq 0 ]]; then
    echo "  Status prüfen:"
    echo "    systemctl status hydrahive-core"
    echo "    journalctl -u hydrahive-core -n 30"
    echo ""
    echo "  Symlinks prüfen:"
    echo "    ls -la /opt/octopos /opt/hydrahive /etc/octopos /etc/hydrahive"
    echo ""
    echo "  ROLLBACK (falls nötig):"
    echo "    systemctl stop hydrahive-core"
    echo "    cp $BACKUP_DIR/*.service /etc/systemd/system/"
    echo "    systemctl daemon-reload && systemctl start octopos-core"
else
    echo "  Dry-Run abgeschlossen — keine Änderungen vorgenommen."
    echo "  Zum Ausführen: sudo bash $0"
fi
echo ""
