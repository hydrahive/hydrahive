#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# HydraHive Tailscale Quick-Connect
#
# Verbindet eine HydraHive-Instanz mit dem Tailnet — nur API Key nötig.
# Danach ist die Webkonsole über die Tailscale-IP erreichbar.
#
# Usage:
#   bash tailscale-connect.sh <TAILSCALE_API_KEY>
#   bash tailscale-connect.sh tskey-api-xxxx
#
# Was passiert:
#   1. Tailscale installieren (falls nicht vorhanden)
#   2. Auth-Key über die Tailscale API generieren
#   3. Server ins Tailnet verbinden
#   4. API Key in HydraHive speichern
#   5. Tailscale-IP anzeigen → fertig
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN="\033[0;32m"; BLUE="\033[0;34m"; RED="\033[0;31m"; NC="\033[0m"
info()    { echo -e "${BLUE}[Tailscale]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
error()   { echo -e "${RED}[FEHLER]${NC} $1"; exit 1; }

# ── Argument prüfen ──────────────────────────────────────────────────────────

API_KEY="${1:-}"
if [ -z "$API_KEY" ]; then
    echo ""
    echo "Usage: bash tailscale-connect.sh <TAILSCALE_API_KEY>"
    echo ""
    echo "Den API Key findest du unter:"
    echo "  https://login.tailscale.com/admin/settings/keys"
    echo "  → Generate access token"
    echo ""
    exit 1
fi

# ── 1. Tailscale installieren ────────────────────────────────────────────────

if command -v tailscale &>/dev/null; then
    info "Tailscale bereits installiert: $(tailscale version | head -1)"
else
    info "Installiere Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
    success "Tailscale installiert"
fi

# Daemon starten falls nicht aktiv
if ! systemctl is-active --quiet tailscaled 2>/dev/null; then
    info "Starte tailscaled Daemon..."
    sudo systemctl enable --now tailscaled
    sleep 2
fi

# ── 2. Auth-Key generieren ───────────────────────────────────────────────────

info "Generiere Auth-Key über Tailscale API..."
AUTH_RESPONSE=$(curl -s -X POST \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"capabilities":{"devices":{"create":{"reusable":false,"ephemeral":false,"preauthorized":true}}},"expirySeconds":3600}' \
    "https://api.tailscale.com/api/v2/tailnet/-/keys" 2>&1)

AUTH_KEY=$(echo "$AUTH_RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('key',''))" 2>/dev/null || echo "")

if [ -z "$AUTH_KEY" ]; then
    error "Auth-Key konnte nicht generiert werden. API Key gültig?\nAntwort: $AUTH_RESPONSE"
fi
success "Auth-Key generiert"

# ── 3. Hostname bestimmen ────────────────────────────────────────────────────

HOSTNAME="hydrahive-$(hostname -s 2>/dev/null || echo 'node')"
info "Hostname: $HOSTNAME"

# ── 4. Mit Tailnet verbinden ─────────────────────────────────────────────────

info "Verbinde mit Tailnet..."
sudo tailscale up --authkey="$AUTH_KEY" --hostname="$HOSTNAME" --reset 2>&1 || error "tailscale up fehlgeschlagen"

sleep 3

TS_IP=$(tailscale ip -4 2>/dev/null || echo "")
if [ -z "$TS_IP" ]; then
    error "Keine Tailscale-IP erhalten. Verbindung fehlgeschlagen?"
fi
success "Verbunden! Tailscale-IP: $TS_IP"

# ── 5. API Key in HydraHive speichern ────────────────────────────────────────

HYDRAHIVE_CONFIG="/etc/hydrahive/tailscale.json"
if [ -d "/etc/hydrahive" ]; then
    echo "{\"api_key\":\"${API_KEY}\",\"tailnet\":\"-\"}" | sudo tee "$HYDRAHIVE_CONFIG" > /dev/null
    sudo chmod 600 "$HYDRAHIVE_CONFIG"
    success "API Key in HydraHive gespeichert ($HYDRAHIVE_CONFIG)"

    # Core neustarten falls er läuft
    if systemctl is-active --quiet hydrahive-core 2>/dev/null; then
        sudo systemctl restart hydrahive-core
        info "HydraHive Core neugestartet"
    fi
else
    info "/etc/hydrahive existiert nicht — HydraHive noch nicht installiert?"
fi

# ── 6. Ergebnis anzeigen ─────────────────────────────────────────────────────

echo ""
echo "════════════════════════════════════════════════════════════════"
echo ""
echo -e "  ${GREEN}Tailscale verbunden!${NC}"
echo ""
echo -e "  Tailscale-IP:  ${BLUE}${TS_IP}${NC}"
echo -e "  Hostname:      ${BLUE}${HOSTNAME}${NC}"
echo ""
echo -e "  Webkonsole:    ${BLUE}https://${TS_IP}${NC}"
echo ""
echo "  Diesen Server findest du jetzt auf jeder anderen"
echo "  HydraHive-Instanz unter Federation → HydraHive suchen"
echo ""
echo "════════════════════════════════════════════════════════════════"
echo ""
