#!/usr/bin/env bash
# migrate-v2.sh — Konvertiert v1 Agents zu v2 Projekten (#591)
#
# Ablauf:
#   1. Backup von /agents/ nach /agents.v1-backup/
#   2. users.json lesen → allowed_projects → members-Map fuer Projekte
#   3. Fuer jeden Agent: Projekt erstellen wenn noch keins existiert
#   4. agent.yaml → config.yaml konvertieren
#   5. soul.md → AGENT.md kopieren
#   6. memory/ uebernehmen
#   7. Disabled/Ephemeral Agents ueberspringen
#
# WAS WIRD UEBERNOMMEN:
#   - LLM: model, temperature, max_tokens, thinking_budget, fallback_models
#   - Provider wird aus Model abgeleitet (anthropic/openai/google/ollama)
#   - identity (Name) und description
#   - execution_modes.default → execution_mode (safe/elevated/unrestricted)
#   - members: User aus users.json die allowed_projects enthalten
#   - soul.md-Inhalt als AGENT.md
#   - memory/-Dateien
#
# WAS ENTFAELLT ABSICHTLICH (v1-only):
#   - tools: v2 hat feste 9 Core-Tools (shell_exec, file_*, web_search, memory_*, ask_agent)
#   - role: v2 hat kein Role-System mehr
#   - workflow: v2 Orchestrator hat keinen Worker-Dispatch
#   - heartbeat_tasks: Feature in v2 noch nicht implementiert
#   - allowed_agents: v2-Konzept ist Projekt-Members + ask_agent Tool
#   - execution_modes.{safe,unrestricted}.permissions: v2 hat Tool-Ebene Security
#
# NICHT AUTOMATISIERT (manuell pflegen nach Migration):
#   - Messenger-Config (messenger.yaml pro Projekt)
#   - Gitea/Matrix-Room-Links
#
# IDEMPOTENZ:
#   - Wenn config.yaml existiert → Projekt komplett ueberspringen
#   - Fuer Re-Migration: config.yaml manuell loeschen
#
# Usage: sudo bash scripts/migrate-v2.sh [--dry-run]
#
# WICHTIG: Laeuft auf dem Server, nicht auf Lilith!

set -euo pipefail

AGENTS_DIR="/agents"
PROJECTS_DIR="/projects"
BACKUP_DIR="/agents.v1-backup"
VENV="/opt/hydrahive/venv"
DRY_RUN=false

if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
    echo "=== DRY RUN — keine Änderungen ==="
fi

GREEN="\033[0;32m"; BLUE="\033[0;34m"; YELLOW="\033[1;33m"; RED="\033[0;31m"; NC="\033[0m"
info()    { echo -e "${BLUE}[Migrate]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "${YELLOW}[SKIP]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; }

migrated=0
skipped=0
errors=0

# users.json → Members-Map (project_id → [usernames]) via allowed_projects (#591)
USERS_MAP_JSON=$("$VENV/bin/python3" -c "
import json
result = {}
try:
    with open('/etc/hydrahive/users.json') as f:
        users = json.load(f)
    for username, udata in users.items():
        for pid in udata.get('allowed_projects', []) or []:
            result.setdefault(pid, []).append(username)
except Exception:
    pass
print(json.dumps(result))
" 2>/dev/null || echo '{}')
info "Members-Map aus users.json: $(echo "$USERS_MAP_JSON" | "$VENV/bin/python3" -c "import json,sys; d=json.load(sys.stdin); print(len(d), 'Projekte mit expliziten Members')")"

# 1. Backup
if [[ "$DRY_RUN" == false ]]; then
    if [[ ! -d "$BACKUP_DIR" ]]; then
        info "Erstelle Backup: $AGENTS_DIR → $BACKUP_DIR"
        cp -a "$AGENTS_DIR" "$BACKUP_DIR"
        success "Backup erstellt"
    else
        info "Backup existiert bereits: $BACKUP_DIR"
    fi
fi

# 2. Agents durchgehen
for agent_dir in "$AGENTS_DIR"/*/; do
    agent_id=$(basename "$agent_dir")

    # Disabled Agents überspringen
    if [[ "$agent_id" == _*_disabled ]]; then
        warn "$agent_id — disabled, übersprungen"
        skipped=$((skipped + 1))
        continue
    fi

    # Sessions-DB und andere Nicht-Agent-Verzeichnisse überspringen
    if [[ "$agent_id" == sessions* ]] || [[ ! -d "$agent_dir" ]]; then
        continue
    fi

    # agent.yaml muss existieren
    if [[ ! -f "$agent_dir/agent.yaml" ]]; then
        warn "$agent_id — keine agent.yaml, übersprungen"
        skipped=$((skipped + 1))
        continue
    fi

    # Ephemeral Agents überspringen (z.B. file_specialist_32d9cfc1)
    if [[ "$agent_id" == *_???????? ]] && [[ ${#agent_id} -gt 20 ]]; then
        warn "$agent_id — ephemeral, übersprungen"
        skipped=$((skipped + 1))
        continue
    fi

    # Ziel-Projekt bestimmen
    project_dir="$PROJECTS_DIR/$agent_id"

    # Projekt existiert schon?
    if [[ -f "$project_dir/config.yaml" ]]; then
        warn "$agent_id — config.yaml existiert bereits, übersprungen"
        skipped=$((skipped + 1))
        continue
    fi

    info "Migriere: $agent_id"

    # 3. config.yaml aus agent.yaml generieren (Python-Helper)
    config_yaml=$(USERS_MAP_JSON="$USERS_MAP_JSON" "$VENV/bin/python3" -c "
import yaml, sys, json, os

with open('$agent_dir/agent.yaml') as f:
    raw = yaml.safe_load(f)

# LLM-Config extrahieren
llm = raw.get('llm', {})
model = llm.get('model', 'claude-sonnet-4-6')
temperature = llm.get('temperature', 0.7)
max_tokens = llm.get('max_tokens', 4096)
thinking_budget = llm.get('thinking_budget', 0)
fallback_models = llm.get('fallback_models', [])

# Provider aus Model ableiten
provider = 'anthropic'
if 'gpt' in model.lower() or 'openai' in model.lower():
    provider = 'openai'
elif 'gemini' in model.lower():
    provider = 'google'
elif 'ollama' in model.lower() or 'llama' in model.lower():
    provider = 'ollama'

# llm.api_key_env uebernehmen (Codex #3: Daten-Verlust-Fix)
api_key_env = llm.get('api_key_env', '') or ''

# Failover aus fallback_models
failover = []
for fm in fallback_models:
    fp = 'anthropic'
    if 'gpt' in fm.lower(): fp = 'openai'
    elif 'gemini' in fm.lower(): fp = 'google'
    failover.append({'provider': fp, 'model': fm})

# #591: execution_modes.default → execution_mode (safe/elevated/unrestricted)
exec_modes = raw.get('execution_modes', {}) or {}
default_mode = (exec_modes.get('default') or 'safe').strip().lower()
if default_mode not in ('safe', 'elevated', 'unrestricted'):
    default_mode = 'safe'

# #591: Members aus users.json allowed_projects Mapping
users_map = json.loads(os.environ.get('USERS_MAP_JSON', '{}'))
members = list(users_map.get('$agent_id', []))
if not members:
    # Fallback: nur admin (behaelt bisheriges Verhalten bei unbekannten Projekten)
    members = ['admin']

# identity.description aus agent.yaml, nur Fallback auf Migration-Text
_existing_desc = raw.get('description', '') or ''
if not _existing_desc:
    _existing_desc = f\"Migriert von Agent {raw.get('id', '$agent_id')}\"

# config.yaml zusammenbauen
config = {
    'id': '$agent_id',
    'version': '2.0.0',
    'identity': {
        'name': raw.get('identity', '$agent_id'),
        'description': _existing_desc,
    },
    'llm': {
        'provider': provider,
        'model': model,
        'temperature': temperature,
        'max_tokens': max_tokens,
        'thinking_budget': thinking_budget,
        'api_key_env': api_key_env,
        'failover': failover,
    },
    'execution_mode': default_mode,
    'plugins': [],
    'repos': [],
    'sources': [],
    'members': members,
}

yaml.dump(config, sys.stdout, default_flow_style=False, allow_unicode=True, sort_keys=False)
" 2>/dev/null) || {
        error "$agent_id — config.yaml Konvertierung fehlgeschlagen"
        errors=$((errors + 1))
        continue
    }

    if [[ "$DRY_RUN" == true ]]; then
        echo "  → config.yaml:"
        echo "$config_yaml" | head -10 | sed 's/^/    /'
        echo "    ..."

        if [[ -f "$agent_dir/soul.md" ]]; then
            echo "  → AGENT.md: $(wc -l < "$agent_dir/soul.md") Zeilen"
        fi

        memory_count=0
        if [[ -d "$agent_dir/memory" ]]; then
            memory_count=$(find "$agent_dir/memory/" -maxdepth 1 -type f 2>/dev/null | wc -l)
        fi
        echo "  → Memory: $memory_count Dateien"
        migrated=$((migrated + 1))
        continue
    fi

    # Projekt-Verzeichnis erstellen
    mkdir -p "$project_dir/memory"

    # config.yaml schreiben
    echo "$config_yaml" > "$project_dir/config.yaml"

    # soul.md → AGENT.md
    if [[ -f "$agent_dir/soul.md" ]]; then
        cp "$agent_dir/soul.md" "$project_dir/AGENT.md"
    else
        # Minimale AGENT.md wenn keine soul.md existiert
        cat > "$project_dir/AGENT.md" <<AGENTMD
# $agent_id

Migriert von Agent $agent_id. Bitte AGENT.md anpassen.
AGENTMD
    fi

    # Memory übernehmen
    if [[ -d "$agent_dir/memory" ]] && [[ "$(ls -A "$agent_dir/memory/" 2>/dev/null)" ]]; then
        cp -a "$agent_dir/memory/"* "$project_dir/memory/" 2>/dev/null || true
    fi

    # Berechtigungen setzen
    chown -R hydrahive:hydrahive "$project_dir"

    # Migration-Report pro Agent (#591)
    _members_str=$(grep -A 20 "^members:" "$project_dir/config.yaml" 2>/dev/null | head -10 | grep "^- " | sed 's/^- //' | tr '\n' ',' | sed 's/,$//')
    _exec_mode=$(grep "^execution_mode:" "$project_dir/config.yaml" 2>/dev/null | awk '{print $2}')
    _memory_count=0
    if [[ -d "$project_dir/memory" ]]; then
        _memory_count=$(find "$project_dir/memory/" -maxdepth 1 -type f 2>/dev/null | wc -l)
    fi
    _agent_md_lines=$(wc -l < "$project_dir/AGENT.md" 2>/dev/null || echo 0)
    success "$agent_id → $project_dir"
    echo "    Members: ${_members_str:-admin}"
    echo "    Execution-Mode: ${_exec_mode:-safe}"
    echo "    AGENT.md: ${_agent_md_lines} Zeilen, Memory: ${_memory_count} Dateien"
    echo "    Nicht uebernommen: tools (v2 = 9 Core-Tools), role, workflow, heartbeat_tasks"
    migrated=$((migrated + 1))
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  Migration abgeschlossen"
echo -e "  ${GREEN}Migriert: $migrated${NC}"
echo -e "  ${YELLOW}Übersprungen: $skipped${NC}"
if [[ $errors -gt 0 ]]; then
    echo -e "  ${RED}Fehler: $errors${NC}"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
if [[ "$DRY_RUN" == true ]]; then
    echo "Dies war ein Dry-Run. Für echte Migration: sudo bash $0"
else
    echo "Backup unter: $BACKUP_DIR"
    echo "Neustart: sudo systemctl restart hydrahive-core"
fi
