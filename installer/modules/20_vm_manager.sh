#!/bin/bash
# Modul 20 — QEMU/KVM VM-Manager + websockify VNC-Proxy (#895)
#────────────────────────────────────────────────────────────────────────────

info "Installiere QEMU/KVM + VM-Manager..."

DEBIAN_FRONTEND=noninteractive apt-get update -qq

DEPS=(qemu-system-x86_64 qemu-utils ovmf websockify cpu-checker)
MISSING=()
for dep in "${DEPS[@]}"; do
  if ! dpkg -l "$dep" 2>/dev/null | grep -q "^ii"; then
    MISSING+=("$dep")
  fi
done

if [ ${#MISSING[@]} -eq 0 ]; then
  success "QEMU/KVM-Pakete bereits vorhanden"
else
  info "Installiere: ${MISSING[*]}"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${MISSING[@]}"
  success "QEMU/KVM-Pakete installiert: ${MISSING[*]}"
fi

# ── KVM-Verfügbarkeit prüfen ───────────────────────────────────────────
if command -v kvm-ok >/dev/null 2>&1; then
  if kvm-ok 2>/dev/null | grep -q "KVM acceleration can be used"; then
    success "KVM-Verfügbarkeit: OK"
  else
    warn "KVM nicht verfügbar — VM-Manager startet trotzdem (QEMU kann ohne KVM laufen)"
  fi
else
  warn "kvm-ok nicht gefunden — Hardware-Virtualisierung kann nicht geprüft werden"
fi

# ── User zur kvm-Gruppe hinzufügen ──────────────────────────────────────
if id -nGz hydrahive 2>/dev/null | grep -qzxF "kvm"; then
  success "hydrahive bereits in kvm-Gruppe"
else
  usermod -aG kvm hydrahive 2>/dev/null && success "hydrahive zu kvm-Gruppe hinzugefügt" || warn "kvm-Gruppe konnte nicht gesetzt werden (非 kritisch)"
fi

# ── Storage-Verzeichnisse anlegen ───────────────────────────────────────
# Basis-Verzeichnis muss hydrahive gehören damit vms.db erstellt werden kann
chown hydrahive:hydrahive /var/lib/hydrahive
for dir in isos vms vnc-tokens; do
  target="/var/lib/hydrahive/${dir}"
  mkdir -p "$target"
  chown hydrahive:hydrahive "$target"
  case "$dir" in
    vnc-tokens) chmod 700 "$target" ;;
    *)          chmod 750 "$target" ;;
  esac
done
success "Storage-Verzeichnisse (/var/lib/hydrahive/{isos,vms,vnc-tokens}) erstellt"

# ── websockify systemd-Service ────────────────────────────────────────────
cat > /etc/systemd/system/hydrahive-websockify.service << 'EOF'
[Unit]
Description=HydaHive VNC WebSocket Proxy
After=network.target hydrahive-core.service

[Service]
User=hydrahive
ExecStart=/usr/bin/websockify --token-plugin=TokenFile --token-source=/var/lib/hydrahive/vnc-tokens/ 6080
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl enable --now hydrahive-websockify.service 2>/dev/null
if systemctl is-active --quiet hydrahive-websockify; then
  success "websockify-Service aktiv und gestartet (Port 6080)"
else
  warn "websockify-Service konnte nicht gestartet werden — manuell prüfen: systemctl status hydrahive-websockify"
fi

success "VM-Manager installiert (QEMU/KVM + websockify)"
