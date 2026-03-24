#!/usr/bin/env bash
# HydraHive Installer - Modul 12: VPN (Tailscale / Headscale)
# Installiert Tailscale-Client, optional Headscale als self-hosted Koordinator.
# Auth-Key wird später im Admin-Panel eingegeben — kein Account nötig beim Setup.
# Idempotent: bereits installierte Komponenten werden übersprungen.

VPN_CONFIG="/etc/hydrahive/vpn.json"
HEADSCALE_VERSION="0.23.0"
HEADSCALE_BIN="/usr/local/bin/headscale"
HEADSCALE_CFG="/etc/headscale/config.yaml"
HYDRAHIVE_USER="${HYDRAHIVE_USER:-hydrahive}"

info "=== VPN-Setup (Tailscale / Headscale) ==="

# --- VPN-Modus wählen ---
VPN_MODE="${VPN_MODE:-}"
if [ -z "${VPN_MODE}" ]; then
    echo ""
    echo "  VPN-Koordination:"
    echo "  [1] Tailscale (Standard) — Account auf tailscale.com"
    echo "  [2] Headscale (self-hosted) — eigener Koordinator auf dieser VM"
    echo "  [3] Überspringen — später im Admin-Panel konfigurieren"
    echo ""
    read -rp "  Auswahl [1/2/3, Standard: 1]: " _vpn_choice
    case "${_vpn_choice:-1}" in
        2) VPN_MODE="headscale" ;;
        3) VPN_MODE="skip" ;;
        *) VPN_MODE="tailscale" ;;
    esac
fi

if [ "${VPN_MODE}" = "skip" ]; then
    warn "VPN übersprungen — später unter Admin → VPN konfigurieren"
    echo '{"mode":"none","configured":false}' > "${VPN_CONFIG}"
    chmod 600 "${VPN_CONFIG}"
    chown "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${VPN_CONFIG}"
    return 0
fi

# --- Tailscale installieren (immer, egal ob tailscale oder headscale) ---
if ! command -v tailscale &>/dev/null; then
    info "Installiere Tailscale-Client..."
    curl -fsSL https://tailscale.com/install.sh | sh
    success "Tailscale-Client installiert"
else
    success "Tailscale-Client bereits installiert ($(tailscale version | head -1))"
fi

# sicherstellen dass tailscaled läuft
systemctl enable --now tailscaled 2>/dev/null || true

# --- Headscale installieren (optional) ---
if [ "${VPN_MODE}" = "headscale" ]; then
    info "Installiere Headscale (self-hosted Koordinator)..."

    ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
    case "${ARCH}" in
        amd64|x86_64) HS_ARCH="amd64" ;;
        arm64|aarch64) HS_ARCH="arm64" ;;
        *) error "Nicht unterstützte Architektur für Headscale: ${ARCH}" ;;
    esac

    if [ ! -f "${HEADSCALE_BIN}" ] || ! "${HEADSCALE_BIN}" version 2>/dev/null | grep -q "${HEADSCALE_VERSION}"; then
        HS_URL="https://github.com/juanfont/headscale/releases/download/v${HEADSCALE_VERSION}/headscale_${HEADSCALE_VERSION}_linux_${HS_ARCH}"
        curl -fsSL -o "${HEADSCALE_BIN}" "${HS_URL}"
        chmod +x "${HEADSCALE_BIN}"
        success "Headscale ${HEADSCALE_VERSION} installiert"
    else
        success "Headscale bereits installiert"
    fi

    # Headscale User + Verzeichnisse
    if ! id headscale &>/dev/null; then
        useradd -r -s /bin/false -d /var/lib/headscale headscale
    fi
    mkdir -p /etc/headscale /var/lib/headscale /var/run/headscale
    chown -R headscale:headscale /var/lib/headscale /var/run/headscale

    # Config schreiben (idempotent)
    SERVER_IP="$(hostname -I | awk '{print $1}')"
    if [ ! -f "${HEADSCALE_CFG}" ]; then
        cat > "${HEADSCALE_CFG}" << HSCFG
---
server_url: http://${SERVER_IP}:8089
listen_addr: 0.0.0.0:8089
grpc_listen_addr: 127.0.0.1:50443
grpc_allow_insecure: true

noise:
  private_key_path: /var/lib/headscale/noise_private.key

prefixes:
  v4: 100.64.0.0/10
  v6: fd7a:115c:a1e0::/48
  allocation: sequential

derp:
  server:
    enabled: false
  urls:
    - https://controlplane.tailscale.com/derpmap/default
  auto_update_enabled: true
  update_frequency: 24h

database:
  type: sqlite
  sqlite:
    path: /var/lib/headscale/db.sqlite

log:
  level: info

dns:
  magic_dns: true
  base_domain: hydrahive.net

unix_socket: /var/run/headscale/headscale.sock
unix_socket_permission: "0770"
HSCFG
        chown headscale:headscale "${HEADSCALE_CFG}"
        chmod 640 "${HEADSCALE_CFG}"
        success "Headscale-Config geschrieben: ${HEADSCALE_CFG}"
    else
        info "Headscale-Config bereits vorhanden"
    fi

    # headscale.service
    cat > /etc/systemd/system/headscale.service << UNIT
[Unit]
Description=Headscale — self-hosted Tailscale Koordinator
After=network.target
Documentation=https://headscale.net

[Service]
Type=simple
User=headscale
Group=headscale
ExecStart=${HEADSCALE_BIN} serve
Restart=always
RestartSec=5
RuntimeDirectory=headscale
StateDirectory=headscale
StandardOutput=journal
StandardError=journal
SyslogIdentifier=headscale

[Install]
WantedBy=multi-user.target
UNIT

    systemctl daemon-reload
    systemctl enable headscale

    if systemctl is-active --quiet headscale; then
        systemctl restart headscale
    else
        systemctl start headscale
    fi

    # Warten bis Headscale bereit ist
    _hs_ok=0
    for _i in 1 2 3 4 5; do
        sleep 2
        if "${HEADSCALE_BIN}" namespaces list &>/dev/null 2>&1; then
            _hs_ok=1
            break
        fi
    done

    # hydrahive-Namespace anlegen
    if [ "${_hs_ok}" -eq 1 ]; then
        "${HEADSCALE_BIN}" users create hydrahive 2>/dev/null || true
        success "Headscale läuft, User 'hydrahive' angelegt"
        HS_LOGIN_SERVER="http://${SERVER_IP}:8089"
    else
        warn "Headscale noch nicht bereit — Auth-Key später im Admin-Panel setzen"
        HS_LOGIN_SERVER="http://${SERVER_IP}:8089"
    fi

    # Tailscale gegen Headscale konfigurieren (aber noch nicht authentifizieren)
    VPN_LOGIN_SERVER="${HS_LOGIN_SERVER}"
    VPN_BACKEND="headscale"
else
    VPN_LOGIN_SERVER="https://controlplane.tailscale.com"
    VPN_BACKEND="tailscale"
fi

# --- VPN-Config für hydrahive-core schreiben ---
cat > "${VPN_CONFIG}" << VPNCFG
{
  "mode": "${VPN_BACKEND}",
  "configured": false,
  "auth_key": "",
  "login_server": "${VPN_LOGIN_SERVER}",
  "tailscale_ip": "",
  "hostname": "$(hostname -s)"
}
VPNCFG
chmod 600 "${VPN_CONFIG}"
chown "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${VPN_CONFIG}"

success "VPN-Setup abgeschlossen (Modus: ${VPN_BACKEND})"
info "Auth-Key im Admin-Panel unter Admin → VPN hinterlegen um den VPN zu aktivieren"
