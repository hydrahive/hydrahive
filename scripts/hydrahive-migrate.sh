#!/bin/bash
# hydrahive-migrate.sh — Migriert einen laufenden HydraHive-Server zu HydraHive
#
# Führt folgende Schritte durch:
#   1. Stoppt hydrahive-Services
#   2. Erstellt hydrahive-User/Gruppe (falls nicht vorhanden)
#   3. Verlinkt /opt/hydrahive → /opt/hydrahive (oder verschiebt)
#   4. Verlinkt /etc/hydrahive → /etc/hydrahive (falls noch nicht)
#   5. Benennt Service-Files um
#   6. Startet hydrahive-Services
#
# VORSICHT: Nur auf Produktionssystemen ausführen die noch HydraHive-Namen haben!
# Prüfe vorher: systemctl list-units | grep hydrahive
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
echo "║  HydraHive Migration — HydraHive → HydraHive                  ║"
echo "║  $(date '+%Y-%m-%d %H:%M:%S')                                    ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ── Prüfe ob HydraHive überhaupt vorhanden ist ────────────────────────────────
log "[0/6] Systemzustand prüfen"

HYDRAHIVE_SERVICES=()
for svc in hydrahive-core hydrahive-conduwuit hydrahive-whatsapp-bridge hydrahive-amem; do
    if systemctl list-unit-files "${svc}.service" &>/dev/null 2>&1 | grep -q "${svc}"; then
        HYDRAHIVE_SERVICES+=("$svc")
    fi
done

if [[ ${#HYDRAHIVE_SERVICES[@]} -eq 0 ]] && [[ ! -d /opt/hydrahive ]] && [[ ! -d /etc/hydrahive ]]; then
    warn "Kein HydraHive-System gefunden — Migration nicht notwendig."
    echo "    Prüfe: /opt/hydrahive, /etc/hydrahive, systemctl list-units | grep hydrahive"
    exit 0
fi

info "Gefundene HydraHive-Services: ${HYDRAHIVE_SERVICES[*]:-keine}"
info "/opt/hydrahive existiert: $([ -d /opt/hydrahive ] && echo 'ja' || echo 'nein')"
info "/etc/hydrahive existiert: $([ -d /etc/hydrahive ] && echo 'ja' || echo 'nein')"
echo ""

# ── Backup erstellen ─────────────────────────────────────────────────────────
BACKUP_DIR="/var/backups/hydrahive-migration-$(date +%Y%m%d-%H%M%S)"
log "[PRE] Backup erstellen: $BACKUP_DIR"
if [[ $DRY_RUN -eq 0 ]]; then
    mkdir -p "$BACKUP_DIR"
    # Service-Files sichern
    for svc in "${HYDRAHIVE_SERVICES[@]}"; do
        svc_file="/etc/systemd/system/${svc}.service"
        if [[ -f "$svc_file" ]]; then
            cp "$svc_file" "$BACKUP_DIR/" && info "Gesichert: $svc_file"
        fi
    done
    # /etc/hydrahive sichern (nur Configs, keine Secrets-Warnung)
    if [[ -d /etc/hydrahive ]]; then
        cp -a /etc/hydrahive "$BACKUP_DIR/etc-hydrahive" && info "Gesichert: /etc/hydrahive → $BACKUP_DIR/etc-hydrahive"
    fi
    info "Backup abgeschlossen: $BACKUP_DIR"
    info "ROLLBACK-HINWEIS: Im Fehlerfall Backup wiederherstellen mit:"
    info "  cp $BACKUP_DIR/*.service /etc/systemd/system/"
    info "  systemctl daemon-reload && systemctl start hydrahive-core"
else
    info "[würde erstellen] $BACKUP_DIR"
fi
echo ""

# ── Schritt 1: HydraHive-Services stoppen ─────────────────────────────────────
log "[1/6] HydraHive-Services stoppen"
for svc in "${HYDRAHIVE_SERVICES[@]}"; do
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

# hydrahive-User-Mitgliedschaften auf hydrahive übertragen (sudo etc.)
if id hydrahive &>/dev/null; then
    HYDRAHIVE_GROUPS=$(id -Gn hydrahive 2>/dev/null | tr ' ' '\n' | grep -v '^hydrahive$' || true)
    for grp in $HYDRAHIVE_GROUPS; do
        info "Füge hydrahive zur Gruppe '$grp' hinzu"
        run usermod -aG "$grp" hydrahive 2>/dev/null || warn "Gruppe '$grp' konnte nicht hinzugefügt werden"
    done
fi
echo ""

# ── Schritt 3: /opt/hydrahive → /opt/hydrahive ────────────────────────────────
log "[3/6] /opt/hydrahive → /opt/hydrahive"
if [[ -d /opt/hydrahive ]] && [[ ! -L /opt/hydrahive ]]; then
    if [[ -d /opt/hydrahive ]]; then
        warn "/opt/hydrahive existiert bereits — überspringe Umbenennung"
        info "Symlink /opt/hydrahive → /opt/hydrahive nicht möglich (beide existieren)"
        info "Manuell prüfen: ls -la /opt/hydrahive /opt/hydrahive"
    else
        info "Verschiebe /opt/hydrahive → /opt/hydrahive"
        run mv /opt/hydrahive /opt/hydrahive
        info "Erstelle Symlink /opt/hydrahive → /opt/hydrahive (Rückwärtskompatibilität)"
        run ln -s /opt/hydrahive /opt/hydrahive
        info "Setze Eigentümer auf hydrahive"
        run chown -R hydrahive:hydrahive /opt/hydrahive
    fi
elif [[ -L /opt/hydrahive ]]; then
    info "/opt/hydrahive ist bereits ein Symlink — prüfe Ziel"
    TARGET=$(readlink /opt/hydrahive)
    info "Symlink zeigt auf: $TARGET"
else
    info "/opt/hydrahive nicht vorhanden — nichts zu tun"
fi
echo ""

# ── Schritt 4: /etc/hydrahive → /etc/hydrahive ────────────────────────────────
log "[4/6] /etc/hydrahive → /etc/hydrahive"
if [[ -d /etc/hydrahive ]] && [[ ! -L /etc/hydrahive ]]; then
    if [[ -d /etc/hydrahive ]]; then
        warn "/etc/hydrahive existiert bereits"
        info "Erstelle Symlink /etc/hydrahive → /etc/hydrahive (Configs bleiben erhalten)"
        # Wenn /etc/hydrahive leer ist, verschieben; sonst Symlink von hydrahive
        if [[ -z "$(ls -A /etc/hydrahive 2>/dev/null)" ]]; then
            run rmdir /etc/hydrahive
            run mv /etc/hydrahive /etc/hydrahive
            run ln -s /etc/hydrahive /etc/hydrahive
        else
            info "Beide Verzeichnisse haben Inhalt — manuell zusammenführen:"
            info "  diff -r /etc/hydrahive /etc/hydrahive"
        fi
    else
        info "Verschiebe /etc/hydrahive → /etc/hydrahive"
        run mv /etc/hydrahive /etc/hydrahive
        info "Erstelle Symlink /etc/hydrahive → /etc/hydrahive (Rückwärtskompatibilität)"
        run ln -s /etc/hydrahive /etc/hydrahive
        info "Setze Berechtigungen"
        run chown -R hydrahive:hydrahive /etc/hydrahive
        run chmod 750 /etc/hydrahive
    fi
elif [[ -L /etc/hydrahive ]]; then
    info "/etc/hydrahive ist bereits ein Symlink"
else
    info "/etc/hydrahive nicht vorhanden — nichts zu tun"
fi
echo ""

# ── Schritt 5: Service-Files umbenennen ──────────────────────────────────────
log "[5/6] Systemd Service-Files umbenennen"
declare -A SERVICE_MAP=(
    ["hydrahive-core"]="hydrahive-core"
    ["hydrahive-conduwuit"]="hydrahive-conduwuit"
    ["hydrahive-whatsapp-bridge"]="hydrahive-whatsapp-bridge"
    ["hydrahive-amem"]="hydrahive-amem"
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
            # Interne Referenzen auf hydrahive in der Service-Datei ersetzen
            if [[ $DRY_RUN -eq 0 ]]; then
                sed -i \
                    -e "s|/opt/hydrahive/|/opt/hydrahive/|g" \
                    -e "s|User=hydrahive|User=hydrahive|g" \
                    -e "s|Group=hydrahive|Group=hydrahive|g" \
                    -e "s|hydrahive-conduwuit|hydrahive-conduwuit|g" \
                    -e "s|hydrahive-amem|hydrahive-amem|g" \
                    -e "s|hydrahive-whatsapp-bridge|hydrahive-whatsapp-bridge|g" \
                    -e "s|hydrahive-core|hydrahive-core|g" \
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
    echo "    ls -la /opt/hydrahive /opt/hydrahive /etc/hydrahive /etc/hydrahive"
    echo ""
    echo "  ROLLBACK (falls nötig):"
    echo "    systemctl stop hydrahive-core"
    echo "    cp $BACKUP_DIR/*.service /etc/systemd/system/"
    echo "    systemctl daemon-reload && systemctl start hydrahive-core"
else
    echo "  Dry-Run abgeschlossen — keine Änderungen vorgenommen."
    echo "  Zum Ausführen: sudo bash $0"
fi
echo ""
