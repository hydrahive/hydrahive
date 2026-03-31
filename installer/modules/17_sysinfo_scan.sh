#!/usr/bin/env bash
# 17_sysinfo_scan.sh — System-Zustand in hydrahive_sysinfo Memory schreiben
# Wird von update.sh nach Core-Neustart aufgerufen.
# Erzeugt lesbare Markdown-Snapshots für den System-Experten-Agenten.
set -euo pipefail

AGENT_MEMORY="/agents/hydrahive_sysinfo/memory"
HYDRAHIVE_DIR="/opt/hydrahive"
TS=$(date -Iseconds)

mkdir -p "${AGENT_MEMORY}"

# ------------------------------------------------------------------ Services
{
    echo "# HydraHive Services (Stand: ${TS})"
    echo ""
    echo "| Service | Status | Unit |"
    echo "|---|---|---|"
    for svc in hydrahive-core nginx gitea hydrahive-conduwuit hydrahive-agentlink \
               hydrahive-amem redis-server postgresql hydrahive-codeserver \
               hydrahive-whatsapp-bridge tailscaled; do
        status=$(systemctl is-active "${svc}" 2>/dev/null || echo "inaktiv")
        echo "| ${svc} | ${status} | systemctl status ${svc} |"
    done
    echo ""
    echo "## Offene Ports"
    echo '```'
    ss -tlnp 2>/dev/null | grep LISTEN | awk '{print $4, $6}' | sort || true
    echo '```'
} > "${AGENT_MEMORY}/system_services.md"

# ------------------------------------------------------------------ Konfiguration
{
    echo "# HydraHive Konfiguration (Stand: ${TS})"
    echo ""
    echo "## LLM-Provider"
    if [ -f /etc/hydrahive/llm_config.json ]; then
        echo '```json'
        python3 -c "
import json, sys
try:
    d = json.load(open('/etc/hydrahive/llm_config.json'))
    for name, cfg in d.get('providers', {}).items():
        masked = dict(cfg)
        if masked.get('api_key'):
            masked['api_key'] = '***' + masked['api_key'][-4:]
        print(f'{name}: enabled={masked.get(\"enabled\",False)}, key={masked.get(\"api_key\",\"—\")}')
except Exception as e:
    print(f'Nicht lesbar: {e}')
" 2>/dev/null || echo "Nicht lesbar"
        echo '```'
    else
        echo "llm_config.json fehlt"
    fi
    echo ""
    echo "## OAuth-Token"
    [ -f /etc/hydrahive/claude_oauth_token ] && echo "- Claude Max OAuth: **vorhanden**" || echo "- Claude Max OAuth: nicht konfiguriert"
    [ -f /etc/hydrahive/openai_codex_token.json ] && echo "- OpenAI Codex OAuth: **vorhanden**" || echo "- OpenAI Codex OAuth: nicht konfiguriert"
    echo ""
    echo "## VPN"
    if [ -f /etc/hydrahive/vpn.json ]; then
        python3 -c "import json; d=json.load(open('/etc/hydrahive/vpn.json')); print(f'Modus: {d.get(\"mode\",\"?\")}  IP: {d.get(\"ip\",\"?\")}  Server: {d.get(\"server\",\"?\")}')" 2>/dev/null || cat /etc/hydrahive/vpn.json
    else
        echo "Keine VPN-Konfig"
    fi
    echo ""
    echo "## Deployment-Stand"
    if [ -f /var/run/hydrahive-update.json ]; then
        python3 -c "import json; d=json.load(open('/var/run/hydrahive-update.json')); print(f'Commit: {d.get(\"commit\",\"?\")}  Zeit: {d.get(\"finished_at\",\"?\")}  Status: {d.get(\"status\",\"?\")}')" 2>/dev/null || true
    fi
} > "${AGENT_MEMORY}/system_config.md"

# ------------------------------------------------------------------ nginx
{
    echo "# nginx Konfiguration (Stand: ${TS})"
    echo ""
    NGINX_SITE="/etc/nginx/sites-enabled/hydrahive-console"
    if [ -f "${NGINX_SITE}" ]; then
        echo "## Site: ${NGINX_SITE}"
        echo '```nginx'
        cat "${NGINX_SITE}"
        echo '```'
    else
        echo "nginx-Site nicht gefunden: ${NGINX_SITE}"
    fi
} > "${AGENT_MEMORY}/system_nginx.md"

# ------------------------------------------------------------------ Agenten
{
    echo "# Installierte Agenten (Stand: ${TS})"
    echo ""
    echo "| Agent-ID | Typ | Modell | Tools |"
    echo "|---|---|---|---|"
    if [ -d /agents ]; then
        for agent_dir in /agents/*/; do
            agent_id=$(basename "${agent_dir}")
            yaml="${agent_dir}agent.yaml"
            if [ -f "${yaml}" ]; then
                agent_type=$(python3 -c "import yaml; d=yaml.safe_load(open('${yaml}')); print(d.get('type','?'))" 2>/dev/null || echo "?")
                model=$(python3 -c "import yaml; d=yaml.safe_load(open('${yaml}')); print(d.get('llm',{}).get('model','?'))" 2>/dev/null || echo "?")
                tools=$(python3 -c "import yaml; d=yaml.safe_load(open('${yaml}')); print(', '.join(d.get('tools',[])))" 2>/dev/null || echo "?")
                echo "| ${agent_id} | ${agent_type} | ${model} | ${tools} |"
            else
                echo "| ${agent_id} | (kein agent.yaml) | — | — |"
            fi
        done
    else
        echo "Kein /agents/-Verzeichnis gefunden"
    fi
} > "${AGENT_MEMORY}/system_agents.md"

# Eigentümer setzen
HYDRAHIVE_USER="${HYDRAHIVE_USER:-hydrahive}"
chown -R "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${AGENT_MEMORY}" 2>/dev/null || true

echo "sysinfo-Memory aktualisiert (${AGENT_MEMORY})"
