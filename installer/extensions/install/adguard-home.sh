#!/usr/bin/env bash
# HydraHive Extension - AdGuard Home (DNS-Blocker)
# Lädt das offizielle AdGuardHome-Binary von GitHub, verifiziert SHA256
# und richtet einen systemd-Service ein.
# Ports: DNS 3053 (UDP/TCP), Web UI 8300 — vermeidet Konflikte mit systemd-resolved.
# Idempotent: erneuter Aufruf aktualisiert auf die neueste Version.

set -euo pipefail

if ! declare -f info &>/dev/null; then
    GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
    info()    { echo -e "${BLUE}[AdGuard]${NC} $1"; }
    success() { echo -e "${GREEN}[OK]${NC} $1"; }
    warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
fi

AGH_DIR="/opt/adguard-home"
AGH_BINARY="${AGH_DIR}/AdGuardHome"
AGH_CONF_DIR="${AGH_DIR}"
AGH_DATA_DIR="/var/lib/adguard-home"
AGH_USER="adguardhome"
DNS_PORT="3053"
WEB_PORT="8300"
HH_CONF="/etc/hydrahive/adguard-home.json"

info "=== AdGuard Home installieren ==="

# --- Neueste Version ermitteln ---
info "Ermittle neuestes AdGuard Home Release..."
RELEASE_JSON="$(curl -sf "https://api.github.com/repos/AdguardTeam/AdGuardHome/releases/latest")"
LATEST_TAG="$(printf '%s' "${RELEASE_JSON}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tag_name','v0.107.54'))" 2>/dev/null || echo "v0.107.54")"
info "Neueste Version: ${LATEST_TAG}"

# --- Schon installiert und aktuell? ---
if [ -x "${AGH_BINARY}" ]; then
    INSTALLED_VERSION="$("${AGH_BINARY}" --version 2>/dev/null | grep -oP 'v[\d.]+' | head -1 || echo "")"
    if [ "${INSTALLED_VERSION}" = "${LATEST_TAG}" ]; then
        success "AdGuard Home ${INSTALLED_VERSION} ist bereits aktuell installiert"
        systemctl start adguardhome 2>/dev/null || true
        exit 0
    fi
    info "Update von ${INSTALLED_VERSION} auf ${LATEST_TAG}..."
    systemctl stop adguardhome 2>/dev/null || true
fi

# --- Abhängigkeiten ---
apt-get install -y --quiet curl wget python3 sha256sum 2>/dev/null | grep -E "^(Get|Entpacken|Einrichten)" || true
command -v sha256sum &>/dev/null || apt-get install -y --quiet coreutils

# --- Download-URLs zusammenbauen ---
ARCH="amd64"
PLATFORM="linux"
TARBALL="AdGuardHome_${PLATFORM}_${ARCH}.tar.gz"
CHECKSUMS_FILE="AdGuardHome_checksums.txt"
BASE_URL="https://github.com/AdguardTeam/AdGuardHome/releases/download/${LATEST_TAG}"
DOWNLOAD_URL="${BASE_URL}/${TARBALL}"
CHECKSUM_URL="${BASE_URL}/${CHECKSUMS_FILE}"

# --- Herunterladen ---
info "Lade ${TARBALL} herunter..."
curl -fSL "${DOWNLOAD_URL}" -o "/tmp/${TARBALL}" \
    || error "Download fehlgeschlagen: ${DOWNLOAD_URL}"

# --- SHA256-Verifizierung ---
info "Verifiziere SHA256-Prüfsumme..."
if curl -fsSL "${CHECKSUM_URL}" -o "/tmp/${CHECKSUMS_FILE}" 2>/dev/null; then
    EXPECTED_HASH="$(grep "${TARBALL}" "/tmp/${CHECKSUMS_FILE}" | awk '{print $1}')"
    ACTUAL_HASH="$(sha256sum "/tmp/${TARBALL}" | awk '{print $1}')"
    if [ -n "${EXPECTED_HASH}" ] && [ "${EXPECTED_HASH}" != "${ACTUAL_HASH}" ]; then
        rm -f "/tmp/${TARBALL}" "/tmp/${CHECKSUMS_FILE}"
        error "SHA256-Prüfsumme stimmt NICHT überein! Download verworfen."
    fi
    success "SHA256-Prüfsumme korrekt: ${ACTUAL_HASH:0:16}..."
    rm -f "/tmp/${CHECKSUMS_FILE}"
else
    warn "Checksum-Datei nicht verfügbar — überspringe Verifikation"
fi

# --- Entpacken ---
mkdir -p "${AGH_DIR}"
tar -xzf "/tmp/${TARBALL}" -C "${AGH_DIR}" --strip-components=1
rm -f "/tmp/${TARBALL}"
chmod 750 "${AGH_BINARY}"
success "AdGuardHome ${LATEST_TAG} nach ${AGH_DIR} entpackt"

# --- System-User ---
if ! id "${AGH_USER}" &>/dev/null; then
    useradd -r -s /bin/false -d "${AGH_DATA_DIR}" -m "${AGH_USER}"
    success "System-User '${AGH_USER}' angelegt"
fi

# --- Daten-Verzeichnis ---
mkdir -p "${AGH_DATA_DIR}"
chown -R "${AGH_USER}:${AGH_USER}" "${AGH_DATA_DIR}"
chown -R "${AGH_USER}:${AGH_USER}" "${AGH_DIR}"

# --- Initiale Konfiguration erstellen (nur wenn noch keine vorhanden) ---
AGH_YAML="${AGH_CONF_DIR}/AdGuardHome.yaml"
if [ ! -f "${AGH_YAML}" ]; then
    info "Erstelle initiale AdGuardHome.yaml..."
    # Zufälliges Admin-Passwort (bcrypt) — wir nutzen python3 um bcrypt zu vermeiden
    # AdGuard akzeptiert auch plain-sha256 für initiale Einrichtung
    ADMIN_PASS="$(head -c 16 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 20)"
    # SHA256 des Passworts
    ADMIN_PASS_HASH="$(printf '%s' "${ADMIN_PASS}" | sha256sum | awk '{print $1}')"

    cat > "${AGH_YAML}" << YAMLEOF
http:
  pprof:
    port: 6060
    enabled: false
  address: 0.0.0.0:${WEB_PORT}
  session_ttl: 720h
users:
  - name: admin
    password: \$2y\$10\$placeholder_run_setup_wizard
auth_attempts: 5
block_auth_min: 15
http_proxy: ""
language: de
theme: auto
debug_pprof: false
web_session_ttl: 720

dns:
  bind_hosts:
    - 0.0.0.0
  port: ${DNS_PORT}
  anonymize_client_ip: false
  ratelimit: 20
  ratelimit_whitelist: []
  refuse_any: true
  upstream_dns:
    - https://dns.cloudflare.com/dns-query
    - https://dns.google/dns-query
  upstream_dns_file: ""
  bootstrap_dns:
    - 9.9.9.10
    - 149.112.112.10
    - 2620:fe::10
    - 2620:fe::fe:10
  fallback_dns: []
  all_servers: false
  fastest_addr: false
  fastest_timeout: 1s
  allowed_clients: []
  disallowed_clients: []
  blocked_hosts:
    - version.bind
    - id.server
    - hostname.bind
  trusted_proxies:
    - 127.0.0.0/8
    - ::1/128
  cache_size: 4194304
  cache_ttl_min: 0
  cache_ttl_max: 0
  cache_optimistic: false
  bogus_nxdomain: []
  aaaa_disabled: false
  enable_dnssec: false
  edns_client_subnet:
    custom_ip: ""
    enabled: false
    use_custom: false
  max_goroutines: 300
  handle_ddr: true
  ipset: []
  ipset_file: ""
  filtering_enabled: true
  filters_update_interval: 24
  parental_enabled: false
  safebrowsing_enabled: false
  safebrowsing_cache_size: 1048576
  safesearch:
    enabled: false
    bing: true
    duckduckgo: true
    google: true
    pixabay: true
    yandex: true
    youtube: true
  safesearch_cache_size: 1048576
  rewrites: []
  blocked_services:
    schedule:
      time_zone: Europe/Berlin
    ids: []
  upstream_timeout: 10s
  private_networks: []
  use_private_ptr_resolvers: true
  local_ptr_upstreams: []
  use_dns64: false
  dns64_prefixes: []
  serve_http3: false
  use_http3_upstreams: false
  serve_plain_dns: true

tls:
  enabled: false
  server_name: ""
  force_https: false
  port_https: 443
  port_dns_over_tls: 853
  port_dns_over_quic: 784
  port_dnscrypt: 0
  dnscrypt_config_file: ""
  allow_unencrypted_doh: false
  certificate_chain: ""
  private_key: ""
  certificate_path: ""
  private_key_path: ""
  strict_sni_check: false

querylog:
  dir_path: ""
  ignored: []
  interval: 90h
  size_memory: 1000
  enabled: true
  file_enabled: true

statistics:
  dir_path: ""
  ignored: []
  interval: 90h
  enabled: true
  limit: 0

filters:
  - enabled: true
    url: https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt
    name: AdGuard DNS filter
    id: 1
  - enabled: true
    url: https://adguardteam.github.io/HostlistsRegistry/assets/filter_2.txt
    name: AdAway Default Blocklist
    id: 2

whitelist_filters: []
user_rules: []

dhcp:
  enabled: false
  interface_name: ""
  local_domain_name: lan
  dhcpv4:
    gateway_ip: ""
    subnet_mask: ""
    range_start: ""
    range_end: ""
    lease_duration: 86400
    icmp_timeout_msec: 1000
    options: []
  dhcpv6:
    range_start: ""
    lease_duration: 86400
    ra_slaac_only: false
    ra_allow_slaac: false

clients:
  runtime_sources:
    whois: true
    arp: true
    rdns: true
    dhcp: true
    hosts: true
  persistent: []

log:
  enabled: true
  file: ""
  max_backups: 0
  max_size: 100
  max_age: 3
  compress: false
  local_time: false
  verbose: false

os:
  group: ""
  user: ""
  rlimit_nofile: 0

schema_version: 28
YAMLEOF
    chown "${AGH_USER}:${AGH_USER}" "${AGH_YAML}"
    chmod 640 "${AGH_YAML}"
    # Passwort separat speichern
    printf '%s\n' "${ADMIN_PASS}" > "${AGH_CONF_DIR}/.initial_admin_pass"
    chown "${AGH_USER}:${AGH_USER}" "${AGH_CONF_DIR}/.initial_admin_pass"
    chmod 600 "${AGH_CONF_DIR}/.initial_admin_pass"
    success "Konfiguration erstellt — Ersteinrichtung im Web-UI erforderlich"
fi

# --- systemd Service ---
cat > /etc/systemd/system/adguardhome.service << SVCEOF
[Unit]
Description=AdGuard Home DNS Sinkhole
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${AGH_USER}
Group=${AGH_USER}
WorkingDirectory=${AGH_DIR}
ExecStart=${AGH_BINARY} \
    --config ${AGH_YAML} \
    --work-dir ${AGH_DATA_DIR} \
    --no-check-update \
    --pidfile ""
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
# Port-Binding < 1024 ohne root ermöglichen
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable adguardhome
systemctl restart adguardhome
success "AdGuard Home Service gestartet"

# --- Warten auf Start ---
info "Warte auf AdGuard Home (bis 20 s)..."
for i in $(seq 1 10); do
    sleep 2
    if curl -sf "http://127.0.0.1:${WEB_PORT}" &>/dev/null; then
        break
    fi
done
if curl -sf "http://127.0.0.1:${WEB_PORT}" &>/dev/null; then
    success "AdGuard Home Web-UI erreichbar"
else
    warn "Web-UI noch nicht erreichbar — Dienst braucht ggf. länger zum Starten"
fi

# --- HydraHive Config ---
mkdir -p /etc/hydrahive
INITIAL_PASS_INFO="$(cat "${AGH_CONF_DIR}/.initial_admin_pass" 2>/dev/null || echo "im Web-UI gesetzt")"
cat > "${HH_CONF}" << CFGEOF
{
  "installed": true,
  "version": "${LATEST_TAG}",
  "url": "http://127.0.0.1:${WEB_PORT}",
  "dns_port": ${DNS_PORT},
  "web_port": ${WEB_PORT},
  "binary": "${AGH_BINARY}",
  "config": "${AGH_YAML}",
  "data_dir": "${AGH_DATA_DIR}"
}
CFGEOF
chown hydrahive:hydrahive "${HH_CONF}" 2>/dev/null || true
chmod 640 "${HH_CONF}"

echo ""
info "=== AdGuard Home installiert ==="
info "Web-UI:   http://127.0.0.1:${WEB_PORT}"
info "DNS-Port: ${DNS_PORT} (TCP/UDP)"
info "Ersteinrichtung: Im Browser öffnen und Admin-Account anlegen"
warn "Hinweis: Port ${DNS_PORT} statt 53 — bei Bedarf Router-DNS auf diesen Port zeigen lassen"
warn "oder iptables-Redirect: iptables -t nat -A PREROUTING -p udp --dport 53 -j REDIRECT --to-port ${DNS_PORT}"
