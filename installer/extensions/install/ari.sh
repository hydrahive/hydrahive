#!/usr/bin/env bash
# HydraHive Extension - Ari (Tor / Onion Router)
# Installiert Tor nativ via apt, konfiguriert SocksPort + ControlPort.
# Idempotent: erneuter Aufruf aktualisiert Konfiguration und startet den Dienst neu.

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[Ari/Tor]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

TOR_CONF="/etc/tor/torrc"
TOR_DATA="/var/lib/tor"
SOCKS_PORT="9050"
CONTROL_PORT="9051"
CONTROL_PASS_FILE="/etc/hydrahive/ari-control.pass"
HH_CONF="/etc/hydrahive/ari.json"

info "=== Ari (Tor) installieren ==="

# --- apt installieren ---
info "Installiere Tor via apt..."
apt-get update -qq
apt-get install -y --quiet tor 2>/dev/null | grep -E "^(Get|Entpacken|Einrichten)" || true
success "Tor installiert: $(tor --version 2>/dev/null | head -1)"

# --- Control-Passwort erzeugen (nur beim ersten Mal) ---
if [ ! -f "${CONTROL_PASS_FILE}" ]; then
    CLEARTEXT_PASS="$(head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32)"
    HASHED_PASS="$(tor --hash-password "${CLEARTEXT_PASS}" 2>/dev/null | tail -1)"
    mkdir -p /etc/hydrahive
    printf '%s\n' "${CLEARTEXT_PASS}" > "${CONTROL_PASS_FILE}"
    chmod 600 "${CONTROL_PASS_FILE}"
    chown root:root "${CONTROL_PASS_FILE}"
    success "Control-Passwort generiert und gespeichert: ${CONTROL_PASS_FILE}"
else
    CLEARTEXT_PASS="$(cat "${CONTROL_PASS_FILE}")"
    HASHED_PASS="$(tor --hash-password "${CLEARTEXT_PASS}" 2>/dev/null | tail -1)"
    info "Vorhandenes Control-Passwort wird verwendet"
fi

# --- torrc konfigurieren ---
info "Konfiguriere /etc/tor/torrc..."
# Backup falls noch keine HydraHive-Sektion vorhanden
if ! grep -q "# HydraHive" "${TOR_CONF}" 2>/dev/null; then
    cp "${TOR_CONF}" "${TOR_CONF}.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
fi

cat > "${TOR_CONF}" << TOREOF
# HydraHive managed torrc — automatisch generiert, nicht manuell bearbeiten

# SOCKS-Proxy (alle Schnittstellen für lokale Nutzung)
SocksPort 127.0.0.1:${SOCKS_PORT}

# Control-Port für externe Steuerung (z.B. Stem-Library, Nyx)
ControlPort 127.0.0.1:${CONTROL_PORT}
HashedControlPassword ${HASHED_PASS}

# Daten-Verzeichnis
DataDirectory ${TOR_DATA}

# Logging
Log notice file /var/log/tor/notices.log

# Exit-Policies: kein Exit-Node (nur Client-Betrieb)
ExitPolicy reject *:*
TOREOF

chmod 644 "${TOR_CONF}"
success "torrc konfiguriert"

# --- Log-Verzeichnis ---
mkdir -p /var/log/tor
chown debian-tor:debian-tor /var/log/tor 2>/dev/null || chown tor:tor /var/log/tor 2>/dev/null || true

# --- Dienst starten / neu laden ---
systemctl daemon-reload
systemctl enable tor@default
if systemctl is-active tor@default &>/dev/null; then
    info "Lade Tor-Konfiguration neu..."
    systemctl reload tor@default || systemctl restart tor@default
else
    systemctl start tor@default
fi

# --- Warten bis Tor gestartet ist ---
info "Warte auf Tor-Start (bis 30 s)..."
for i in $(seq 1 15); do
    sleep 2
    if systemctl is-active tor@default &>/dev/null; then
        break
    fi
done

if systemctl is-active tor@default &>/dev/null; then
    success "Tor-Dienst läuft"
else
    warn "Tor-Dienst läuft noch nicht — prüfe: sudo systemctl status tor@default"
fi

# --- Verbindungstest via SOCKS ---
if command -v curl &>/dev/null; then
    if curl -sf --socks5 "127.0.0.1:${SOCKS_PORT}" --max-time 10 "https://check.torproject.org/api/ip" &>/dev/null; then
        success "Tor-Verbindung erfolgreich getestet"
    else
        warn "Tor-Test nicht erfolgreich — Tor braucht ggf. noch einen Moment zum Bootstrappen"
    fi
fi

# --- HydraHive Config ---
mkdir -p /etc/hydrahive
cat > "${HH_CONF}" << CFGEOF
{
  "installed": true,
  "socks_port": ${SOCKS_PORT},
  "control_port": ${CONTROL_PORT},
  "control_pass_file": "${CONTROL_PASS_FILE}",
  "data_dir": "${TOR_DATA}"
}
CFGEOF
chown hydrahive:hydrahive "${HH_CONF}" 2>/dev/null || true
chmod 640 "${HH_CONF}"

echo ""
info "=== Ari (Tor) installiert ==="
info "SOCKS5-Proxy:  127.0.0.1:${SOCKS_PORT}"
info "Control-Port:  127.0.0.1:${CONTROL_PORT}"
info "Control-Pass:  $(cat "${CONTROL_PASS_FILE}")"
info "Dienst:        sudo systemctl status tor@default"
info "Testen:        curl --socks5 127.0.0.1:${SOCKS_PORT} https://check.torproject.org/api/ip"
