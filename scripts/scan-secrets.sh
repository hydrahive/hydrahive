#!/usr/bin/env bash
# scan-secrets.sh — Secret-Scan für HydraHive
#
# Modi:
#   --fast   Hook-/Deploy-tauglicher Scan. Kein Docker-Default.
#            Nutzt lokales trufflehog (TRUFFLEHOG_BIN oder PATH) wenn verfügbar,
#            plus rg-Pattern-Scan. Findings werden redacted ausgegeben.
#   --full   Vollscan (gitleaks + trufflehog History). Nutzt Docker nur wenn
#            HYDRAHIVE_SECRET_SCAN_DOCKER=1 (kein automatischer Pull ohne Opt-in).
#   (leer)   Alias für --full (abwärtskompatibel).
#
# Env:
#   TRUFFLEHOG_BIN                Pfad zu lokalem trufflehog-Binary.
#   HYDRAHIVE_SECRET_SCAN_DOCKER  1 → Docker-Scans erlaubt (opt-in).
#   HYDRAHIVE_SKIP_SECRET_SCAN    1 → Sofort exit 0, Warnung auf stderr.
#   HYDRAHIVE_SCAN_ROOT           Override für Scan-Wurzel (Tests).
#   HYDRAHIVE_SCAN_ALLOWLIST      Override für Allowlist-Datei (Tests).
set -uo pipefail

MODE="full"
case "${1:-}" in
  --fast) MODE="fast" ;;
  --full|"") MODE="full" ;;
  -h|--help) sed -n '1,22p' "$0"; exit 0 ;;
  *) echo "Unbekannter Modus: $1" >&2; exit 2 ;;
esac

REPO_DIR="${HYDRAHIVE_SCAN_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ALLOWLIST="${HYDRAHIVE_SCAN_ALLOWLIST:-$REPO_DIR/scripts/secret-scan-allowlist.txt}"

# ── Bypass ────────────────────────────────────────────────────────────────────
if [ "${HYDRAHIVE_SKIP_SECRET_SCAN:-0}" = "1" ]; then
  echo "[WARN] HYDRAHIVE_SKIP_SECRET_SCAN=1 — Secret-Scan übersprungen." >&2
  echo "[WARN] Verantwortung liegt beim Ausführenden." >&2
  exit 0
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
green() { printf '\033[0;32m[OK]\033[0m %s\n' "$1"; }
red()   { printf '\033[0;31m[FAIL]\033[0m %s\n' "$1" >&2; }
info()  { printf '\033[0;34m[INFO]\033[0m %s\n' "$1"; }
warn()  { printf '\033[0;33m[WARN]\033[0m %s\n' "$1" >&2; }

# Redaction: zielgerichtete Secret-Pattern — nicht jede lange Zeichenkette.
# Pfade, Zeilennummern, Regelnamen bleiben lesbar. Form: [REDACTED:<kind>:<last4>].
# Private-Key-Payload wird als [REDACTED:KEY] markiert.
redact() {
  sed -E \
    -e 's/(gh[opsur]_[A-Za-z0-9]{28,})([A-Za-z0-9]{4})/[REDACTED:gh_token:\2]/g' \
    -e 's/(github_pat_[A-Za-z0-9_]{74,})([A-Za-z0-9_]{4})/[REDACTED:gh_pat:\2]/g' \
    -e 's/(sk-ant-[A-Za-z0-9_-]{16,})([A-Za-z0-9_-]{4})/[REDACTED:sk-ant:\2]/g' \
    -e 's/(sk[_-](live|test|proj)[_-][A-Za-z0-9_-]{16,})([A-Za-z0-9_-]{4})/[REDACTED:sk-\2:\3]/g' \
    -e 's/(AKIA[0-9A-Z]{12})([0-9A-Z]{4})/[REDACTED:AKIA:\2]/g' \
    -e 's/(ASIA[0-9A-Z]{12})([0-9A-Z]{4})/[REDACTED:ASIA:\2]/g' \
    -e 's/(xox[baprs]-[A-Za-z0-9-]{10,})([A-Za-z0-9]{4})/[REDACTED:xox:\2]/g' \
    -e 's/(AIza[0-9A-Za-z_-]{31})([0-9A-Za-z_-]{4})/[REDACTED:AIza:\2]/g' \
    -e 's/(ya29\.[0-9A-Za-z_-]{16,})([0-9A-Za-z_-]{4})/[REDACTED:ya29:\2]/g' \
    -e 's/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_=.-]*/[REDACTED:JWT]/g' \
    -e 's/(-----BEGIN [A-Z ]*PRIVATE KEY-----)[^-]*(-----END [A-Z ]*PRIVATE KEY-----)?/\1[REDACTED:KEY]\2/g' \
    -e 's/[A-Za-z0-9+\/]{64,}={0,2}/[REDACTED:base64]/g'
}

# Allowlist laden (Zeilen, '#' Kommentare und Leerzeilen ignoriert).
load_allowlist() {
  [ -f "$ALLOWLIST" ] || return 0
  grep -vE '^[[:space:]]*(#|$)' "$ALLOWLIST" || true
}

# Filtert stdin gegen Allowlist (grep -vE).
filter_allowlist() {
  local patterns
  patterns="$(load_allowlist | paste -sd'|' -)"
  if [ -z "$patterns" ]; then
    cat
  else
    grep -vE "$patterns" || true
  fi
}

# ── trufflehog (nur verifiziert) ─────────────────────────────────────────────
run_trufflehog_fast() {
  local bin="${TRUFFLEHOG_BIN:-}"
  if [ -z "$bin" ] && command -v trufflehog >/dev/null 2>&1; then
    bin="$(command -v trufflehog)"
  fi

  if [ -z "$bin" ]; then
    if [ "${HYDRAHIVE_SECRET_SCAN_DOCKER:-0}" = "1" ]; then
      info "trufflehog-Binary fehlt — nutze Docker (opt-in, kein Auto-Pull)."
      if ! docker image inspect trufflesecurity/trufflehog:latest >/dev/null 2>&1; then
        warn "Docker-Image trufflesecurity/trufflehog nicht lokal. Kein automatischer Pull."
        warn "Manuell: docker pull trufflesecurity/trufflehog:latest"
        return 2
      fi
      local out
      out=$(docker run --rm -v "$REPO_DIR:/repo" trufflesecurity/trufflehog:latest \
        filesystem /repo --only-verified --no-update 2>&1 || true)
      parse_trufflehog_output "$out"
      return $?
    fi
    warn "trufflehog nicht gefunden (TRUFFLEHOG_BIN oder PATH)."
    warn "Verifizierter Scan übersprungen. rg-Pattern-Scan läuft weiter."
    warn "Opt-in Docker: HYDRAHIVE_SECRET_SCAN_DOCKER=1"
    return 2
  fi

  info "trufflehog ($bin) — filesystem --only-verified"
  local out
  out=$("$bin" filesystem "$REPO_DIR" --only-verified --no-update 2>&1 || true)
  parse_trufflehog_output "$out"
}

parse_trufflehog_output() {
  local out="$1"
  local findings
  findings=$(printf '%s\n' "$out" | grep -E '^(Found verified|Raw result:|Detector Type:|File:|Line:)' || true)
  if [ -z "$findings" ]; then
    green "trufflehog: kein verifiziertes Secret"
    return 0
  fi
  local filtered
  filtered=$(printf '%s\n' "$findings" | filter_allowlist)
  if [ -z "$filtered" ]; then
    green "trufflehog: nur Allowlist-Treffer"
    return 0
  fi
  red "trufflehog: verifizierte Fundstellen:"
  printf '%s\n' "$filtered" | redact >&2
  return 1
}

# ── rg Pattern-Scan ──────────────────────────────────────────────────────────
run_rg_patterns() {
  if ! command -v rg >/dev/null 2>&1; then
    warn "rg (ripgrep) fehlt — Pattern-Scan übersprungen."
    return 2
  fi
  info "rg — Pattern-Scan (PAT, AWS, Slack, Anthropic, Private Keys)"
  local out
  out=$(rg -n -S \
    -e 'BEGIN RSA PRIVATE KEY' \
    -e 'BEGIN OPENSSH PRIVATE KEY' \
    -e 'ghp_[a-zA-Z0-9]{36}' \
    -e 'github_pat_[a-zA-Z0-9_]{82}' \
    -e 'sk-ant-[a-zA-Z0-9_-]{20,}' \
    -e 'AKIA[0-9A-Z]{16}' \
    -e 'xoxb-[0-9]+-[0-9]+-' \
    -g '!node_modules' -g '!.git' -g '!*.pyc' \
    -g '!scripts/scan-secrets.sh' \
    -g '!scripts/secret-scan-allowlist.txt' \
    "$REPO_DIR" 2>/dev/null || true)
  if [ -z "$out" ]; then
    green "rg: keine Pattern-Treffer"
    return 0
  fi
  local filtered
  filtered=$(printf '%s\n' "$out" | filter_allowlist)
  if [ -z "$filtered" ]; then
    green "rg: nur Allowlist-Treffer"
    return 0
  fi
  red "rg: Pattern-Funde:"
  printf '%s\n' "$filtered" | redact >&2
  return 1
}

# ── Fast Mode ────────────────────────────────────────────────────────────────
if [ "$MODE" = "fast" ]; then
  echo "=== HydraHive Secret Scan (fast) ==="
  echo "Repo: $REPO_DIR"
  [ -f "$ALLOWLIST" ] && echo "Allowlist: $ALLOWLIST"
  fail=0
  ran=0
  # Return-Codes: 0=clean, 1=findings, 2=scanner-missing (nicht gelaufen)
  rc_tf=0; run_trufflehog_fast || rc_tf=$?
  [ "$rc_tf" -ne 2 ] && ran=$((ran+1))
  [ "$rc_tf" -eq 1 ] && fail=1
  rc_rg=0; run_rg_patterns || rc_rg=$?
  [ "$rc_rg" -ne 2 ] && ran=$((ran+1))
  [ "$rc_rg" -eq 1 ] && fail=1
  if [ "$ran" -eq 0 ]; then
    red "Kein Secret-Scanner verfügbar — Scan ungültig, Push/Deploy blockiert."
    echo "[HINT] Installiere trufflehog oder ripgrep." >&2
    echo "[HINT] Opt-in Docker: HYDRAHIVE_SECRET_SCAN_DOCKER=1" >&2
    echo "[HINT] Bypass (nur mit Grund): HYDRAHIVE_SKIP_SECRET_SCAN=1" >&2
    exit 1
  fi
  if [ "$fail" -ne 0 ]; then
    red "Secret-Scan: Fundstellen — Push/Deploy blockiert."
    echo "[HINT] Allowlist: $ALLOWLIST" >&2
    echo "[HINT] Bypass (nur mit Grund): HYDRAHIVE_SKIP_SECRET_SCAN=1" >&2
    exit 1
  fi
  green "Fast-Scan sauber."
  exit 0
fi

# ── Full Mode ────────────────────────────────────────────────────────────────
# Vollscan mit gitleaks + trufflehog History. Ohne Docker-Opt-in nur Hinweis.
echo "=== HydraHive Secret Scan (full) ==="
echo "Repo: $REPO_DIR"
PASS=0
FAIL=0
RAN=0

if [ "${HYDRAHIVE_SECRET_SCAN_DOCKER:-0}" = "1" ] && command -v docker >/dev/null 2>&1; then
  info "1/4 gitleaks — Arbeitsbaum (Docker opt-in)"
  if docker image inspect zricethezav/gitleaks:latest >/dev/null 2>&1; then
    GL_OUT=$(docker run --rm -v "$REPO_DIR:/repo" zricethezav/gitleaks:latest \
      detect --source=/repo --no-git 2>&1 || true)
    REAL=$(printf '%s\n' "$GL_OUT" | grep "File:" | filter_allowlist || true)
    if [ -z "$REAL" ]; then
      green "gitleaks Arbeitsbaum: sauber / nur Allowlist"
      PASS=$((PASS+1)); RAN=$((RAN+1))
    else
      red "gitleaks Arbeitsbaum:"
      printf '%s\n' "$REAL" | redact >&2
      FAIL=$((FAIL+1)); RAN=$((RAN+1))
    fi
  else
    warn "gitleaks-Image nicht lokal — kein Auto-Pull. Überspringe."
  fi

  info "2/4 gitleaks — Git-History (Docker opt-in)"
  if docker image inspect zricethezav/gitleaks:latest >/dev/null 2>&1; then
    GL_HIST=$(docker run --rm -v "$REPO_DIR:/repo" zricethezav/gitleaks:latest \
      detect --source=/repo 2>&1 || true)
    REAL=$(printf '%s\n' "$GL_HIST" | grep "File:" | filter_allowlist || true)
    if [ -z "$REAL" ]; then
      green "gitleaks History: sauber / nur Allowlist"
      PASS=$((PASS+1)); RAN=$((RAN+1))
    else
      red "gitleaks History:"
      printf '%s\n' "$REAL" | redact >&2
      FAIL=$((FAIL+1)); RAN=$((RAN+1))
    fi
  fi
else
  info "Docker-Scans deaktiviert (HYDRAHIVE_SECRET_SCAN_DOCKER!=1). Überspringe gitleaks."
fi

info "3/4 trufflehog — Filesystem (--only-verified)"
rc_tf=0; run_trufflehog_fast || rc_tf=$?
if [ "$rc_tf" -eq 0 ]; then
  PASS=$((PASS+1)); RAN=$((RAN+1))
elif [ "$rc_tf" -eq 1 ]; then
  FAIL=$((FAIL+1)); RAN=$((RAN+1))
fi

info "4/4 rg — Pattern-Scan"
rc_rg=0; run_rg_patterns || rc_rg=$?
if [ "$rc_rg" -eq 0 ]; then
  PASS=$((PASS+1)); RAN=$((RAN+1))
elif [ "$rc_rg" -eq 1 ]; then
  FAIL=$((FAIL+1)); RAN=$((RAN+1))
fi

echo ""
echo "════════════════════════════════════"
echo "Ergebnis: $PASS OK, $FAIL FAIL (Scanner gelaufen: $RAN)"
if [ "$RAN" -eq 0 ]; then
  red "Kein Secret-Scanner verfügbar — Scan ungültig."
  echo "[HINT] Installiere trufflehog oder ripgrep (optional: gitleaks + HYDRAHIVE_SECRET_SCAN_DOCKER=1)." >&2
  exit 1
fi
if [ "$FAIL" -eq 0 ]; then
  green "Scan sauber."
  exit 0
else
  red "Findings oben prüfen."
  exit 1
fi
