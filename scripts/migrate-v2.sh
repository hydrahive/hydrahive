#!/usr/bin/env bash
# migrate-v2.sh — Konvertiert v1 Agents zu v2 Projekten
#
# Ablauf:
#   1. Backup von /agents/ nach /agents.v1-backup/
#   2. Für jeden Agent: Projekt erstellen wenn noch keins existiert
#   3. agent.yaml → config.yaml konvertieren (LLM-Config extrahieren)
#   4. soul.md → AGENT.md kopieren
#   5. memory/ übernehmen
#   6. Disabled/Ephemeral Agents überspringen
#
# Usage: sudo bash scripts/migrate-v2.sh [--dry-run]
#
# WICHTIG: Läuft auf dem Server, nicht auf Lilith!

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
        ((skipped++))
        continue
    fi

    # Sessions-DB und andere Nicht-Agent-Verzeichnisse überspringen
    if [[ "$agent_id" == sessions* ]] || [[ ! -d "$agent_dir" ]]; then
        continue
    fi

    # agent.yaml muss existieren
    if [[ ! -f "$agent_dir/agent.yaml" ]]; then
        warn "$agent_id — keine agent.yaml, übersprungen"
        ((skipped++))
        continue
    fi

    # Ephemeral Agents überspringen (z.B. file_specialist_32d9cfc1)
    if [[ "$agent_id" == *_???????? ]] && [[ ${#agent_id} -gt 20 ]]; then
        warn "$agent_id — ephemeral, übersprungen"
        ((skipped++))
        continue
    fi

    # Ziel-Projekt bestimmen
    project_dir="$PROJECTS_DIR/$agent_id"

    # Projekt existiert schon?
    if [[ -f "$project_dir/config.yaml" ]]; then
        warn "$agent_id — config.yaml existiert bereits, übersprungen"
        ((skipped++))
        continue
    fi

    info "Migriere: $agent_id"

    # 3. config.yaml aus agent.yaml generieren (Python-Helper)
    config_yaml=$("$VENV/bin/python3" -c "
import yaml, sys

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

# Failover aus fallback_models
failover = []
for fm in fallback_models:
    fp = 'anthropic'
    if 'gpt' in fm.lower(): fp = 'openai'
    elif 'gemini' in fm.lower(): fp = 'google'
    failover.append({'provider': fp, 'model': fm})

# config.yaml zusammenbauen
config = {
    'id': '$agent_id',
    'version': '2.0.0',
    'identity': {
        'name': raw.get('identity', '$agent_id'),
        'description': f\"Migriert von Agent {raw.get('id', '$agent_id')}\",
    },
    'llm': {
        'provider': provider,
        'model': model,
        'temperature': temperature,
        'max_tokens': max_tokens,
        'thinking_budget': thinking_budget,
        'failover': failover,
    },
    'plugins': [],
    'repos': [],
    'sources': [],
    'members': ['admin'],
}

yaml.dump(config, sys.stdout, default_flow_style=False, allow_unicode=True, sort_keys=False)
" 2>/dev/null) || {
        error "$agent_id — config.yaml Konvertierung fehlgeschlagen"
        ((errors++))
        continue
    }

    if [[ "$DRY_RUN" == true ]]; then
        echo "  → config.yaml:"
        echo "$config_yaml" | head -10 | sed 's/^/    /'
        echo "    ..."

        if [[ -f "$agent_dir/soul.md" ]]; then
            echo "  → AGENT.md: $(wc -l < "$agent_dir/soul.md") Zeilen"
        fi

        memory_count=$(ls "$agent_dir/memory/" 2>/dev/null | wc -l)
        echo "  → Memory: $memory_count Dateien"
        ((migrated++))
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

    success "$agent_id → $project_dir (config.yaml + AGENT.md + $(ls "$project_dir/memory/" 2>/dev/null | wc -l) Memory-Dateien)"
    ((migrated++))
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
