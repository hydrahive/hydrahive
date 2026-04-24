#!/usr/bin/env bash
# seed-specialists.sh — Erstellt 9 Spezialist-Agenten in HydraHive.
# Idempotent: existierende Agenten werden übersprungen.
# Auth: USER + PASS als ENV (default admin/admin)
# Usage: USER=admin PASS=admin ./seed-specialists.sh

set -euo pipefail

HOST="${HOST:-http://localhost:8765}"
USER="${USER:-admin}"
PASS="${PASS:-admin}"
COOKIE="/tmp/hh_specialist_cookie.txt"

SPECIALISTS=(
  "id=coder|identity=Der Coder|soul=Du bist ein erfahrener Software-Entwickler. Du schreibst sauberen, wartbaren Code in allen gängigen Sprachen. Du hältst dich an Best Practices, kommentierst sparsam und lieferst funktionierende Lösungen."
  "id=reviewer|identity=Der Reviewer|soul=Du bist ein kritischer Code-Reviewer. Du prüfst Code auf Sicherheit, Korrektheit, Performance und Lesbarkeit. Du gibst konkretes, konstruktives Feedback."
  "id=doku|identity=Der Doku-Schreiber|soul=Du erstellst klare, verständliche Dokumentation. READMEs, Handbücher, API-Docs, Inline-Kommentare — du schreibst so dass auch Nicht-Entwickler es verstehen."
  "id=tester|identity=Der Tester|soul=Du entwirfst Testpläne und schreibst automatisierte Tests. Unit-Tests, Integration-Tests, E2E-Tests. Du findest Grenzfälle die andere übersehen."
  "id=researcher|identity=Der Researcher|soul=Du recherchierst gründlich und fasst präzise zusammen. Du bewertest Quellen kritisch und lieferst faktenbasierte Einschätzungen."
  "id=designer|identity=Der Designer|soul=Du denkst in UX und visueller Gestaltung. Layouts, Farbschemata, Benutzerführung — du gestaltest Interfaces die sich intuitiv anfühlen."
  "id=dba|identity=Der Datenbankexperte|soul=Du entwirfst effiziente Datenbankschemas, schreibst optimierte Queries und planst Migrationen. SQL und NoSQL, Indizes und Performance."
  "id=devops|identity=Der DevOps-Ingenieur|soul=Du kümmerst dich um Server, Docker, CI/CD und Deployments. Zuverlässigkeit und Automatisierung sind deine Kernwerte."
  "id=analyst|identity=Der Analyst|soul=Du wertest Daten aus, erkennst Muster und erstellst verständliche Reports. Zahlen sagst du die Wahrheit, Visualisierungen helfen sie zu verstehen."
)

# --- Login & Cookie ---
echo "[Login] $USER@$HOST …"
LOGIN_RESP=$(curl -s -c "$COOKIE" -b "$COOKIE" -X POST "$HOST/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"$USER\",\"password\":\"$PASS\"}" \
  -w "\n%{http_code}")

LOGIN_CODE=$(echo "$LOGIN_RESP" | tail -1)
if [[ "$LOGIN_CODE" != "200" && "$LOGIN_CODE" != "204" ]]; then
  echo "FEHLER: Login fehlgeschlagen (HTTP $LOGIN_CODE)" >&2
  exit 1
fi
echo "[Login] OK (Cookie gespeichert)"

# --- Helper ---
create_or_skip() {
  local spec="$1"
  local id identity soul

  # Parse fields from pipe-delimited string
  id=$(echo "$spec" | grep -oP '^id=\K[^|]+')
  identity=$(echo "$spec" | grep -oP 'identity=\K[^|]+')
  soul=$(echo "$spec" | grep -oP 'soul=\K.*')

  echo -n "[$id] $identity … "

  RESP=$(curl -s -b "$COOKIE" -X POST "$HOST/agents" \
    -H "Content-Type: application/json" \
    -d "$(printf '%s' "$(cat <<PAYLOAD
{
  "id": "$id",
  "type": "specialist",
  "identity": "$identity",
  "model": "claude-sonnet-4-6",
  "temperature": 0.7,
  "max_tokens": 4096,
  "soul": "$soul",
  "tools": [],
  "mcp_servers": [],
  "execution_mode_default": "safe",
  "risk_policy": "interactive"
}
PAYLOAD
)")" \
    -w "\n%{http_code}" 2>/dev/null)

  CODE=$(echo "$RESP" | tail -1)
  BODY=$(echo "$RESP" | sed '$d')

  case "$CODE" in
    201) echo "ERSTELLT" ;;
    409) echo "existiert bereits, übersprungen" ;;
    200) echo "ERSTELLT (200)" ;;
    *)   echo "FEHLER (HTTP $CODE): ${BODY:0:100}" >&2 ;;
  esac
}

# --- Seed ---
CREATED=0
SKIPPED=0
for spec in "${SPECIALISTS[@]}"; do
  RESULT=$(create_or_skip "$spec")
  echo "$RESULT"
  if [[ "$RESULT" == *"ERSTELLT"* ]]; then
    ((CREATED++))
  else
    ((SKIPPED++))
  fi
done

# --- Zusammenfassung ---
echo ""
echo "=== Ergebnis ==="
echo "Angelegt:    $CREATED"
echo "Übersprungen: $SKIPPED"
echo "Gesamt:      $(($CREATED + $SKIPPED))"

rm -f "$COOKIE"
