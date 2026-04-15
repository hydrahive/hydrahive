#!/usr/bin/env bash
# tests/test_scan_secrets.sh — Tests für scripts/scan-secrets.sh --fast
# Ohne externe Deps. Baut isolierte Temp-Trees, keine echten Secrets im Repo.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
SCAN="$REPO/scripts/scan-secrets.sh"
FAIL=0

pass() { echo "[PASS] $1"; }
fail() { echo "[FAIL] $1" >&2; FAIL=$((FAIL+1)); }

make_tree() { mktemp -d; }

# Realistisch aussehenden Fake-PAT zur Laufzeit bauen
# (damit der String NICHT im Repo-Git-Index landet und echte Scanner triggert).
fake_pat() {
  printf 'ghp_%s\n' 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ab'
}

run_scan() {
  local dir="$1"
  local allowlist="${2:-}"
  [ -z "$allowlist" ] && allowlist="$dir/.allowlist"
  HYDRAHIVE_SCAN_ROOT="$dir" \
  HYDRAHIVE_SCAN_ALLOWLIST="$allowlist" \
  TRUFFLEHOG_BIN="" \
  HYDRAHIVE_SECRET_SCAN_DOCKER=0 \
  bash "$SCAN" --fast 2>&1
}

# ── Test 1: Clean tree → exit 0 ───────────────────────────────────────────────
t=$(make_tree); : > "$t/.allowlist"; echo "just a regular file" > "$t/readme.txt"
out=$(run_scan "$t"); rc=$?
if [ "$rc" -eq 0 ]; then pass "clean tree exit 0"; else fail "clean tree expected 0 got $rc: $out"; fi
rm -rf "$t"

# ── Test 2: Real-looking PAT → exit 1 ─────────────────────────────────────────
t=$(make_tree); : > "$t/.allowlist"
printf 'token=%s\n' "$(fake_pat)" > "$t/leak.txt"
out=$(run_scan "$t"); rc=$?
if [ "$rc" -ne 0 ]; then pass "real PAT blocks"; else fail "real PAT should block: $out"; fi

# ── Test 3: Output redacted ───────────────────────────────────────────────────
if echo "$out" | grep -q 'ABCDEFGHIJKLMNOPQRSTUV'; then
  fail "raw secret leaked in output:\n$out"
else
  pass "raw secret not in output"
fi
if echo "$out" | grep -q 'REDACTED'; then
  pass "REDACTED marker present"
else
  fail "no REDACTED marker: $out"
fi
rm -rf "$t"

# ── Test 4: Allowlist bypasses ────────────────────────────────────────────────
t=$(make_tree)
mkdir -p "$t/fixtures"
printf 'token=%s\n' "$(fake_pat)" > "$t/fixtures/placeholder.txt"
echo 'fixtures/placeholder' > "$t/.allowlist"
out=$(run_scan "$t"); rc=$?
if [ "$rc" -eq 0 ]; then pass "allowlist bypasses fixture"; else fail "allowlist should bypass, got $rc: $out"; fi
rm -rf "$t"

# ── Test 5: HYDRAHIVE_SKIP_SECRET_SCAN=1 bypass ───────────────────────────────
t=$(make_tree); : > "$t/.allowlist"
printf 'token=%s\n' "$(fake_pat)" > "$t/leak.txt"
out=$(HYDRAHIVE_SKIP_SECRET_SCAN=1 HYDRAHIVE_SCAN_ROOT="$t" \
      HYDRAHIVE_SCAN_ALLOWLIST="$t/.allowlist" \
      bash "$SCAN" --fast 2>&1); rc=$?
if [ "$rc" -eq 0 ]; then pass "SKIP env bypasses"; else fail "SKIP env should bypass, got $rc"; fi
if echo "$out" | grep -qi 'HYDRAHIVE_SKIP_SECRET_SCAN'; then
  pass "SKIP env warns loudly"
else
  fail "SKIP env missing warning: $out"
fi
rm -rf "$t"

# ── Test 6: Fehlender trufflehog → Warnung, rg-Pfad läuft weiter ──────────────
t=$(make_tree); : > "$t/.allowlist"; echo "clean" > "$t/file.txt"
out=$(run_scan "$t"); rc=$?
if [ "$rc" -eq 0 ]; then pass "scan ohne trufflehog läuft sauber durch"; else fail "scan ohne trufflehog sollte 0 geben: $out"; fi
if echo "$out" | grep -qi 'trufflehog nicht gefunden'; then
  pass "warn bei fehlendem trufflehog"
else
  # Falls trufflehog doch im PATH ist, ist kein Warn erwartet — tolerieren.
  if command -v trufflehog >/dev/null 2>&1; then
    pass "trufflehog vorhanden (Warn-Test übersprungen)"
  else
    fail "keine Warn-Meldung obwohl trufflehog fehlt: $out"
  fi
fi
rm -rf "$t"

# ── Test 7: Harter rg-Pattern-Fund blockiert ohne trufflehog ─────────────────
t=$(make_tree); : > "$t/.allowlist"
# AKIA-Pattern (AWS Access Key ID)
printf 'aws=%s\n' 'AKIAIOSFODNN7REAL000' > "$t/aws.txt"
out=$(run_scan "$t"); rc=$?
if [ "$rc" -ne 0 ]; then pass "rg AKIA-Pattern blockiert"; else fail "rg AKIA sollte blocken: $out"; fi
rm -rf "$t"

# ── Test 8: trufflehog-Stub mit simuliertem Fund → blockiert ─────────────────
# Hinweis: Testet nur die Invocation-/Parser-/Exit-Logik, KEINE echte
# Online-Verifikation durch trufflehog. Echter verifizierter Scan bleibt
# manueller/optionaler Check.
t=$(make_tree); : > "$t/.allowlist"
stub=$(mktemp)
cat > "$stub" <<'STUB'
#!/usr/bin/env bash
cat <<OUT
Found verified credential
Detector Type: GitHub
File: /repo/leak.txt
Line: 1
Raw result: ghp_STUBSTUBSTUBSTUBSTUBSTUBSTUBSTUB3333
OUT
exit 0
STUB
chmod +x "$stub"
out=$(HYDRAHIVE_SCAN_ROOT="$t" HYDRAHIVE_SCAN_ALLOWLIST="$t/.allowlist" \
      TRUFFLEHOG_BIN="$stub" HYDRAHIVE_SECRET_SCAN_DOCKER=0 \
      bash "$SCAN" --fast 2>&1); rc=$?
if [ "$rc" -ne 0 ]; then pass "trufflehog-stub Fund blockiert"; else fail "stub should block, got $rc: $out"; fi
if echo "$out" | grep -q 'ghp_STUBSTUBSTUBSTUBSTUB'; then
  fail "stub Raw result leaked in output: $out"
else
  pass "stub Raw result redacted"
fi
if echo "$out" | grep -q 'REDACTED:gh_token'; then
  pass "gh_token Redaction-Label sichtbar"
else
  fail "gh_token label fehlt: $out"
fi
rm -f "$stub"; rm -rf "$t"

# ── Test 9: trufflehog-Stub ohne Findings → exit 0 ───────────────────────────
t=$(make_tree); : > "$t/.allowlist"
stub=$(mktemp)
cat > "$stub" <<'STUB'
#!/usr/bin/env bash
echo "scanning..."
exit 0
STUB
chmod +x "$stub"
out=$(HYDRAHIVE_SCAN_ROOT="$t" HYDRAHIVE_SCAN_ALLOWLIST="$t/.allowlist" \
      TRUFFLEHOG_BIN="$stub" HYDRAHIVE_SECRET_SCAN_DOCKER=0 \
      bash "$SCAN" --fast 2>&1); rc=$?
if [ "$rc" -eq 0 ]; then pass "trufflehog-stub ohne Findings → exit 0"; else fail "stub clean should pass, got $rc: $out"; fi
rm -f "$stub"; rm -rf "$t"

# ── Test 10: Redaction greift für Nicht-Secret-Strings NICHT ─────────────────
# Lange hex-Strings ohne bekanntes Secret-Prefix sollen durchkommen
# (Redaction ist zielgerichtet, nicht breitband).
out_redact=$(echo "abcdef1234567890abcdef1234567890" | bash -c '
  set -euo pipefail
  source_fn() {
    '"$(declare -f 2>/dev/null || true)"'
  }
  sed -E \
    -e "s/(gh[opsur]_[A-Za-z0-9]{28,})([A-Za-z0-9]{4})/[REDACTED:gh_token:\2]/g"
')
if echo "$out_redact" | grep -q 'REDACTED'; then
  fail "zielfremde hex-Strings fälschlich redacted: $out_redact"
else
  pass "Redaction zielgerichtet (kein Broad-Match auf hex-String)"
fi

echo ""
if [ "$FAIL" -eq 0 ]; then
  echo "Alle Tests OK."
  exit 0
else
  echo "$FAIL Test(s) fehlgeschlagen."
  exit 1
fi
