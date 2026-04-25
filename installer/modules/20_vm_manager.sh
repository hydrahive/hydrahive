#!/bin/bash
# Modul 20 — QEMU/KVM VM-Manager + websockify VNC-Proxy (#895)
# Kann standalone als root ausgeführt werden: sudo bash 20_vm_manager.sh
#────────────────────────────────────────────────────────────────────────────

# Helper-Funktionen falls nicht vom Haupt-Installer definiert
if ! declare -f info >/dev/null 2>&1; then
  GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; NC="\033[0m"
  info()    { echo -e "${BLUE}[Info]${NC} $1"; }
  success() { echo -e "${GREEN}[OK]${NC} $1"; }
  warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
fi

info "Installiere QEMU/KVM + VM-Manager..."

DEBIAN_FRONTEND=noninteractive apt-get update -qq

DEPS=(qemu-system-x86 qemu-utils ovmf websockify cpu-checker)
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

# ── KVM-Kernel-Modul laden und persistent machen ───────────────────────
if [ ! -e /dev/kvm ]; then
  if grep -qE "vmx|svm" /proc/cpuinfo 2>/dev/null; then
    _cpu_vendor=$(grep -m1 "vendor_id" /proc/cpuinfo 2>/dev/null | awk '{print $3}')
    if [ "$_cpu_vendor" = "AuthenticAMD" ]; then
      modprobe kvm-amd 2>/dev/null && success "kvm-amd Modul geladen" || warn "kvm-amd konnte nicht geladen werden"
      echo "kvm-amd" > /etc/modules-load.d/kvm.conf
    else
      modprobe kvm-intel 2>/dev/null && success "kvm-intel Modul geladen" || warn "kvm-intel konnte nicht geladen werden"
      echo "kvm-intel" > /etc/modules-load.d/kvm.conf
    fi
    [ -e /dev/kvm ] && chmod 666 /dev/kvm && success "KVM aktiviert (/dev/kvm)" || warn "KVM-Modul geladen aber /dev/kvm fehlt — Reboot nötig"
  else
    warn "CPU unterstützt keine Hardware-Virtualisierung (kein vmx/svm) — KVM nicht möglich, QEMU läuft in TCG-Modus"
  fi
else
  success "KVM bereits aktiv (/dev/kvm)"
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
for dir in isos vms vnc-tokens disk-imports; do
  target="/var/lib/hydrahive/${dir}"
  mkdir -p "$target"
  chown hydrahive:hydrahive "$target"
  case "$dir" in
    vnc-tokens) chmod 700 "$target" ;;
    *)          chmod 750 "$target" ;;
  esac
done
success "Storage-Verzeichnisse (/var/lib/hydrahive/{isos,vms,vnc-tokens,disk-imports}) erstellt"

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

# ── /etc/qemu/bridge.conf vorbereiten (Bridge-Networking) ─────────────────
# Erlaubt qemu-bridge-helper br0 zu nutzen — ohne diese Datei verweigert
# QEMU Bridge-Mode auch wenn br0 existiert.
mkdir -p /etc/qemu
if [ ! -f /etc/qemu/bridge.conf ]; then
  echo "allow br0" > /etc/qemu/bridge.conf
  success "/etc/qemu/bridge.conf angelegt (allow br0)"
elif ! grep -q "^allow br0" /etc/qemu/bridge.conf 2>/dev/null; then
  echo "allow br0" >> /etc/qemu/bridge.conf
  success "/etc/qemu/bridge.conf: br0 hinzugefügt"
else
  success "/etc/qemu/bridge.conf: br0 bereits erlaubt"
fi
# qemu-bridge-helper braucht setuid-root für unprivilegierte Bridge-Nutzung
_qbh=$(dpkg -L qemu-system-x86 2>/dev/null | grep qemu-bridge-helper || true)
if [ -z "$_qbh" ]; then
  _qbh=$(find /usr -name qemu-bridge-helper 2>/dev/null | head -1)
fi
if [ -n "$_qbh" ] && [ -f "$_qbh" ]; then
  chmod u+s "$_qbh" 2>/dev/null && success "qemu-bridge-helper: setuid gesetzt" || warn "qemu-bridge-helper setuid fehlgeschlagen"
fi

success "VM-Manager installiert (QEMU/KVM + websockify)"
