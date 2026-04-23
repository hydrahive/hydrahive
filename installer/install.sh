#!/usr/bin/env bash
# HydraHive Installer — Hauptskript
# Usage:
#   git clone https://github.com/hydrahive/hydrahive.git /tmp/hydrahive
#   sudo bash /tmp/hydrahive/installer/install.sh
# (Kein curl|bash-Shortcut — der Installer kopiert das geklonte Repo
#  selber nach /opt/hydrahive, braucht also den lokalen Source-Tree.)
set -euo pipefail

HYDRAHIVE_VERSION="0.1.0"
export HYDRAHIVE_DIR="/opt/hydrahive"
MODULES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/modules"

# Farben — zentral definiert, alle Module nutzen diese
export RED="\033[0;31m"
export GREEN="\033[0;32m"
export YELLOW="\033[1;33m"
export BLUE="\033[0;34m"
export NC="\033[0m"

info()    { echo -e "${BLUE}[HydraHive]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

export -f info success warn error

echo ""
echo -e "${BLUE}╔══════════════════════════════════════╗${NC}"
echo -e "${BLUE}║     HydraHive Installer v${HYDRAHIVE_VERSION}          ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════╝${NC}"
echo ""

# Root-Check
if [ "$EUID" -ne 0 ]; then
  error "Bitte als root ausfuehren: sudo bash install.sh"
fi

# (Migration octopos→hydrahive entfernt — nicht mehr nötig)

# BL-02: Git safe.directory für HYDRAHIVE_DIR setzen. Wenn das Repo nach
# /opt/hydrahive geklont ist (häufiger Sysadmin-Weg), hat sudo-git später
# Probleme ("fatal: detected dubious ownership"). --system = systemweit,
# überlebt User-Wechsel. Idempotent.
git config --system --add safe.directory "${HYDRAHIVE_DIR}" 2>/dev/null || true

# --- #776: Warnung bei gesetzem HYDRAHIVE_UNRESTRICTED_ALLOW_ROOT --------------
# Dev-Setups nutzen manchmal diesen Flag (wenn keine proj_<id>-User provisioniert
# sind). Der Installer bricht NICHT ab — ist bewusste Dev-Wahl — aber die Warnung
# muss unuebersehbar sein. Siehe SECURITY.md "Risk-Modifying Environment Variables".
if [ "${HYDRAHIVE_UNRESTRICTED_ALLOW_ROOT:-0}" = "1" ]; then
    echo ""
    echo -e "${RED}╔══════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║  WARNUNG: HYDRAHIVE_UNRESTRICTED_ALLOW_ROOT=1                   ║${NC}"
    echo -e "${RED}║  shell_exec laeuft im unrestricted-Modus als ROOT,              ║${NC}"
    echo -e "${RED}║  NICHT als proj_<id>-User (Isolation deaktiviert).              ║${NC}"
    echo -e "${RED}║  Nur fuer lokale Dev-Setups, NIEMALS in Prod. Siehe SECURITY.md.║${NC}"
    echo -e "${RED}╚══════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
fi

# --- Dangling nginx-Symlinks bereinigen (von fehlgeschlagenen Vorinstalls) ---
for _sl in /etc/nginx/sites-enabled/*; do
    [ -L "$_sl" ] && [ ! -e "$_sl" ] && rm -f "$_sl" && warn "Veralteten nginx-Symlink entfernt: $_sl"
done

# --- Tailscale-Modus: Auth-Key vor Phase 1 abfragen ---
# TAILSCALE_AUTHKEY kann per Umgebungsvariable übergeben werden:
#   TAILSCALE_AUTHKEY=tskey-auth-xxx bash install.sh
# Oder der Installer fragt interaktiv (bei RZ-Servern empfohlen).
export TAILSCALE_AUTHKEY="${TAILSCALE_AUTHKEY:-}"
export TAILSCALE_SERVE="${TAILSCALE_SERVE:-0}"

if [ -z "${TAILSCALE_AUTHKEY}" ] && [ -t 0 ]; then
    echo ""
    echo -e "${BLUE}--- Netzwerk-Zugang ---${NC}"
    echo "  Für Rechenzentrum-Server empfohlen: Tailscale-only Modus."
    echo "  Die Console ist dann NUR über das Tailnet erreichbar — Ports 80/443 bleiben geschlossen."
    echo "  Auth-Key: https://login.tailscale.com/admin/settings/keys  (ephemeral, einmalig)"
    echo ""
    read -rp "  Tailscale Auth-Key (leer lassen für klassischen Port 80/443): " _ts_key_input
    if [ -n "${_ts_key_input}" ]; then
        TAILSCALE_AUTHKEY="${_ts_key_input}"
    fi
fi

if [ -n "${TAILSCALE_AUTHKEY}" ]; then
    TAILSCALE_SERVE=1
    info "Tailscale-Modus aktiv — Console wird nur über Tailnet erreichbar sein"
fi
export TAILSCALE_AUTHKEY TAILSCALE_SERVE

# --- Phase 1: Fundament ---
echo -e "${BLUE}--- Phase 1: Fundament ---${NC}"
source "${MODULES_DIR}/01_os_check.sh"
source "${MODULES_DIR}/02_gpu_detect.sh"
source "${MODULES_DIR}/03_dependencies.sh"
source "${MODULES_DIR}/04_tuwunel.sh"
source "${MODULES_DIR}/05_admin_account.sh"

echo ""
echo -e "${BLUE}--- Phase 2: Core Runtime ---${NC}"
source "${MODULES_DIR}/06_core_service.sh"

echo ""
echo -e "${BLUE}--- Phase 3: Web-Console ---${NC}"
source "${MODULES_DIR}/07_console.sh"
source "${MODULES_DIR}/08_ollama.sh"
source "${MODULES_DIR}/09_https.sh"

echo ""
echo -e "${BLUE}--- Phase 4: Git-Integration ---${NC}"
source "${MODULES_DIR}/10_gitea.sh"

echo ""
echo -e "${BLUE}--- Phase 5: AgentLink Hub ---${NC}"
source "${MODULES_DIR}/11_agentlink.sh"

echo ""
echo -e "${BLUE}--- Phase 6: VPN ---${NC}"
source "${MODULES_DIR}/12_vpn.sh"

# --- Modul 13: WhatsApp Bridge (optional) ---
echo ""
if [ -t 0 ]; then
    read -rp "WhatsApp Bridge installieren? (y/N) " INSTALL_WHATSAPP
else
    INSTALL_WHATSAPP="n"
fi
if [[ "${INSTALL_WHATSAPP,,}" == "y" ]]; then
    source "${MODULES_DIR}/13_whatsapp_bridge.sh"
fi

# --- Modul 14: SearXNG (optional) ---
echo ""
if [ -t 0 ]; then
    read -rp "SearXNG (lokale Suchmaschine) installieren? (y/N) " INSTALL_SEARXNG
else
    INSTALL_SEARXNG="n"
fi
if [[ "${INSTALL_SEARXNG,,}" == "y" ]]; then
    source "${MODULES_DIR}/14_searxng.sh"
fi

# --- Modul 18: Voice Interface (optional) ---
# Wyoming (Docker) oder MiniMax (Cloud) für STT + TTS.
# Legt /etc/hydrahive/voice.json an (nicht überschreiben wenn bereits vorhanden).
echo ""
if [ -t 0 ]; then
    read -rp "Voice Interface (STT + TTS) installieren? (y/N) " INSTALL_VOICE
else
    INSTALL_VOICE="n"
fi
if [[ "${INSTALL_VOICE,,}" == "y" ]]; then
    source "${MODULES_DIR}/18_voice.sh"
fi

echo ""
echo -e "${BLUE}--- Phase 7: Code Editor ---${NC}"
source "${MODULES_DIR}/15_codeserver.sh"

# Modul 16: nginx-Config aktualisieren (A2A-Blöcke, /projects/ etc.)
if [ -f "${MODULES_DIR}/16_nginx_update.sh" ]; then
    source "${MODULES_DIR}/16_nginx_update.sh"
fi

# Modul 17: System-Info Scan (Hardware-Erkennung)
if [ -f "${MODULES_DIR}/17_sysinfo_scan.sh" ]; then
    bash "${MODULES_DIR}/17_sysinfo_scan.sh" || true
fi

# Update-Script nach /opt/hydrahive/ kopieren
cp "$(dirname "${BASH_SOURCE[0]}")/update.sh" "${HYDRAHIVE_DIR}/update.sh"
chmod +x "${HYDRAHIVE_DIR}/update.sh"
success "Update-Script: sudo bash ${HYDRAHIVE_DIR}/update.sh"

# Installations-Commit in Update-Status schreiben
_install_commit="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --short HEAD 2>/dev/null || echo "unknown")"
_install_date="$(date -u +"%Y-%m-%dT%H:%M:%S+00:00")"
echo "{\"status\":\"ok\",\"commit\":\"${_install_commit}\",\"finished_at\":\"${_install_date}\",\"source\":\"installer\"}" \
    > /var/run/hydrahive-update.json
chown hydrahive:hydrahive /var/run/hydrahive-update.json 2>/dev/null || true

install -m 755 "$(dirname "${BASH_SOURCE[0]}")/apply-network-profile.sh" "${HYDRAHIVE_DIR}/apply-network-profile.sh"
install -m 440 "$(dirname "${BASH_SOURCE[0]}")/hydrahive-network-profile.sudoers" /etc/sudoers.d/hydrahive-network-profile
success "Network-Profile-Skript installiert"

# Self-Update Service + Auto-Update Service + Timer + sudo-Regel installieren
# #703: zwei Services — selfupdate.service für manuelle/Web-Triggers,
# autoupdate.service für Timer-Auto-Updates (mit HYDRAHIVE_AUTO_UPDATE=1).
install -m 644 "$(dirname "${BASH_SOURCE[0]}")/hydrahive-selfupdate.service" /etc/systemd/system/hydrahive-selfupdate.service
install -m 644 "$(dirname "${BASH_SOURCE[0]}")/hydrahive-autoupdate.service" /etc/systemd/system/hydrahive-autoupdate.service
install -m 644 "$(dirname "${BASH_SOURCE[0]}")/hydrahive-selfupdate.timer" /etc/systemd/system/hydrahive-selfupdate.timer
install -m 440 "$(dirname "${BASH_SOURCE[0]}")/hydrahive-update.sudoers" /etc/sudoers.d/hydrahive-update
install -m 440 "$(dirname "${BASH_SOURCE[0]}")/hydrahive-provisioner.sudoers" /etc/sudoers.d/hydrahive-provisioner
install -m 440 "$(dirname "${BASH_SOURCE[0]}")/hydrahive-installer.sudoers" /etc/sudoers.d/hydrahive-installer
systemctl daemon-reload
# Recovery-Safety-Net direkt aktivieren — Kill-Switch für Admin, die
# das nicht wollen: `touch /etc/hydrahive/disable_auto_update`.
systemctl enable --now hydrahive-selfupdate.timer 2>/dev/null || \
    warn "hydrahive-selfupdate.timer konnte nicht aktiviert werden — manuell prüfen"
success "Self-Update + Auto-Update-Timer installiert"

# Konfig-Dateien vorbereiten (hydrahive-core braucht Schreibrechte)
for _f in jwt_secret internal_secret llm_env llm_config.json gitea_config.json; do
    _path="/etc/hydrahive/${_f}"
    if [ ! -f "${_path}" ]; then
        touch "${_path}"
        chown hydrahive:hydrahive "${_path}"
        chmod 600 "${_path}"
    fi
done

install -m 755 "$(dirname "${BASH_SOURCE[0]}")/amem/install_amem.sh" "${HYDRAHIVE_DIR}/install_amem.sh"
"$(dirname "${BASH_SOURCE[0]}")/amem/install_amem.sh"
success "A-MEM installiert"

# www-data (nginx) braucht Lesezugriff auf /projects/ (hydrahive-Gruppe)
if id www-data &>/dev/null; then
    usermod -aG hydrahive www-data
    systemctl reload nginx 2>/dev/null || true
    success "nginx (www-data) zur hydrahive-Gruppe hinzugefügt — /projects/ erreichbar"
fi

# Standard-Projekte installieren (v2: /projects/ statt /agents/) (#580)
_DEFAULT_PROJECTS_DIR="$(dirname "${BASH_SOURCE[0]}")/default-projects"
if [ -d "${_DEFAULT_PROJECTS_DIR}" ]; then
    _installed=0
    for _proj_dir in "${_DEFAULT_PROJECTS_DIR}"/*/; do
        _proj_id="$(basename "${_proj_dir}")"
        if [ ! -f "/projects/${_proj_id}/config.yaml" ]; then
            mkdir -p "/projects/${_proj_id}"
            cp -r "${_proj_dir}"* "/projects/${_proj_id}/"
            chown -R hydrahive:hydrahive "/projects/${_proj_id}"
            _installed=$((_installed + 1))
        fi
    done
    if [ $_installed -gt 0 ]; then
        success "${_installed} Standard-Projekte installiert (hydrahive_support, ...)"
    else
        info "Standard-Projekte bereits vorhanden — übersprungen"
    fi
fi

# ── BL-08: Service-Health-Gate vor End-Banner ──────────────────────────────
# Exit 1 wenn kritische Services nicht aktiv — verhindert dass der User den
# Success-Banner sieht obwohl das System nicht läuft (täuschender Exit 0).
FAIL_COUNT=0
FAILED_SERVICES=""

# Kritische Units — müssen active sein.
# Achtung: hydrahive-conduwuit (nicht conduwuit — deb-Paket-Service ist
# bewusst disabled durch BL-07-Fix in 04_tuwunel.sh).
for _unit in hydrahive-core hydrahive-conduwuit nginx redis-server; do
  if ! systemctl is-active --quiet "${_unit}" 2>/dev/null; then
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILED_SERVICES="${FAILED_SERVICES} ${_unit}"
  fi
done

# Optionale Units — nur fail wenn installiert (Unit-File existiert) aber inactive.
for _unit in hydrahive-agentlink hydrahive-codeserver hydrahive-amem; do
  if ! systemctl list-unit-files "${_unit}.service" 2>/dev/null | grep -q "${_unit}.service"; then
    continue
  fi
  if ! systemctl is-active --quiet "${_unit}" 2>/dev/null; then
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILED_SERVICES="${FAILED_SERVICES} ${_unit}"
  fi
done

if [ "${FAIL_COUNT}" -gt 0 ]; then
  echo ""
  echo -e "${RED}╔════════════════════════════════════════════════════════╗${NC}"
  echo -e "${RED}║  INSTALLATION UNVOLLSTAENDIG — ${FAIL_COUNT} Service(s) tot  ║${NC}"
  echo -e "${RED}╚════════════════════════════════════════════════════════╝${NC}"
  echo ""
  echo -e "${RED}Tote Services:${NC}${FAILED_SERVICES}"
  echo ""
  echo "Logs pruefen:"
  for _u in ${FAILED_SERVICES}; do
    echo "  journalctl -u ${_u} -n 20 --no-pager"
  done
  echo ""
  exit 1
fi

echo ""
echo -e "${GREEN}╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     Installation abgeschlossen       ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════╝${NC}"
echo ""
info "Profil:        $PROFILE"
info "Matrix:        http://127.0.0.1:6167"
info "Core API:      http://127.0.0.1:8765"
if [ "${TAILSCALE_SERVE:-0}" = "1" ]; then
  _ts_hostname="$(tailscale status --json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('Self',{}).get('DNSName','').rstrip('.'))" 2>/dev/null || echo "")"
  if [ -n "${_ts_hostname}" ]; then
    info "Console:       https://${_ts_hostname}  (via Tailscale, kein öffentlicher Port nötig)"
  else
    info "Console:       https://<hostname>.tailnet.ts.net  (Tailscale-URL — tailscale status prüfen)"
  fi
  info "               Ports 80/443 sind NICHT geöffnet — nur über Tailnet erreichbar"
else
  info "Console:       https://$(hostname -I | awk '{print $1}')  (self-signed Cert, Browser-Warnung ignorieren)"
  info "               Für Let's Encrypt: DOMAIN=mein.host.de bash install.sh"
fi
info "Admin-Account: @admin:$(hostname -f 2>/dev/null || hostname)"
info "Login:         admin / ${CONSOLE_PASS}  (auch in /etc/hydrahive/admin_credentials)"
info "Credentials:   /etc/hydrahive/admin_credentials"
info "Projekte-Dir:  /projects"
info "AgentLink:     http://127.0.0.1:${AGENTLINK_PORT:-8010}/docs"
if systemctl is-active --quiet gitea 2>/dev/null; then
  SERVER_IP_OUT=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
  info "Gitea:         http://${SERVER_IP_OUT}:3002  (admin / siehe /etc/hydrahive/admin_credentials)"
fi
if systemctl is-active --quiet hydrahive-codeserver 2>/dev/null; then
  SERVER_IP_OUT=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
  info "Code Editor:   https://${SERVER_IP_OUT}/code/  (Passwort: siehe /etc/hydrahive/admin_credentials)"
fi
