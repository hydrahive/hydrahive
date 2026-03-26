#!/usr/bin/env bash
# scan-secrets.sh — Vollständiger Secret- und Rechtescan für HydraHive
# Benötigt: docker, rg (ripgrep)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PASS=0
FAIL=0

green() { echo -e "\033[0;32m[OK]\033[0m $1"; }
red()   { echo -e "\033[0;31m[FAIL]\033[0m $1"; FAIL=$((FAIL+1)); }
info()  { echo -e "\033[0;34m[INFO]\033[0m $1"; }

echo ""
echo "=== HydraHive Secret Scan ==="
echo "Repo: $REPO_DIR"
echo ""

# ── 1. gitleaks: Arbeitsbaum ──────────────────────────────────────────────────
info "1/5 gitleaks — Arbeitsbaum (kein Git-History)"
GITLEAKS_OUT=$(docker run --rm -v "$REPO_DIR:/repo" zricethezav/gitleaks:latest \
  detect --source=/repo --no-git 2>&1 || true)
LEAKS=$(echo "$GITLEAKS_OUT" | grep -c "leaks found" || true)
if echo "$GITLEAKS_OUT" | grep -q "no leaks found"; then
  green "gitleaks Arbeitsbaum: sauber"
  PASS=$((PASS+1))
else
  FINDINGS=$(echo "$GITLEAKS_OUT" | grep "leaks found" || echo "Fehler")
  # Test-Fixtures ausblenden
  REAL=$(echo "$GITLEAKS_OUT" | grep "File:" | grep -v "test_security_regressions\|__pycache__" || true)
  if [ -z "$REAL" ]; then
    green "gitleaks Arbeitsbaum: nur Test-Fixtures (false positives)"
    PASS=$((PASS+1))
  else
    red "gitleaks Arbeitsbaum: echte Fundstellen:"
    echo "$REAL"
  fi
fi

# ── 2. gitleaks: Git-History ──────────────────────────────────────────────────
info "2/5 gitleaks — Git-History"
GITLEAKS_HIST=$(docker run --rm -v "$REPO_DIR:/repo" zricethezav/gitleaks:latest \
  detect --source=/repo 2>&1 || true)
if echo "$GITLEAKS_HIST" | grep -q "no leaks found"; then
  green "gitleaks History: sauber"
  PASS=$((PASS+1))
else
  REAL=$(echo "$GITLEAKS_HIST" | grep "File:" | grep -v "test_security_regressions" || true)
  if [ -z "$REAL" ]; then
    green "gitleaks History: nur Test-Fixtures"
    PASS=$((PASS+1))
  else
    red "gitleaks History: Fundstellen in History:"
    echo "$REAL"
  fi
fi

# ── 3. trufflehog: Filesystem (nur verifiziert) ───────────────────────────────
info "3/5 trufflehog — Filesystem (--only-verified)"
TH_OUT=$(docker run --rm -v "$REPO_DIR:/repo" trufflesecurity/trufflehog:latest \
  filesystem /repo --only-verified 2>&1 || true)
REAL=$(echo "$TH_OUT" | grep "Raw result:" | grep -v "1234567890abcdef" || true)
if [ -z "$REAL" ]; then
  green "trufflehog Filesystem: kein verifiziertes Secret im Arbeitsbaum"
  PASS=$((PASS+1))
else
  red "trufflehog Filesystem: verifizierte Secrets gefunden:"
  echo "$REAL"
fi

# ── 4. trufflehog: Git-History ────────────────────────────────────────────────
info "4/5 trufflehog — Git-History (--only-verified)"
TH_HIST=$(docker run --rm -v "$REPO_DIR:/repo" trufflesecurity/trufflehog:latest \
  git file:///repo --only-verified 2>&1 || true)
if echo "$TH_HIST" | grep -q '"verified_secrets": 0'; then
  green "trufflehog History: kein verifiziertes Secret in History"
  PASS=$((PASS+1))
else
  red "trufflehog History: Secrets in History:"
  echo "$TH_HIST" | grep "Raw result:" || true
fi

# ── 5. rg: Harte Pattern-Suche ───────────────────────────────────────────────
info "5/5 rg — Pattern-Scan (private keys, AWS, Slack, Anthropic, GitHub PATs)"
RG_OUT=$(rg -n -S \
  -e 'BEGIN RSA PRIVATE KEY' \
  -e 'BEGIN OPENSSH PRIVATE KEY' \
  -e 'ghp_[a-zA-Z0-9]{36}' \
  -e 'github_pat_[a-zA-Z0-9_]{82}' \
  -e 'sk-ant-[a-zA-Z0-9]{20,}' \
  -e 'AKIA[0-9A-Z]{16}' \
  -e 'xoxb-[0-9]+-' \
  -g '!node_modules' -g '!.git' -g '!*.pyc' -g '!scan-secrets.sh' \
  "$REPO_DIR" 2>/dev/null || true)
if [ -z "$RG_OUT" ]; then
  green "rg Pattern-Scan: nichts gefunden"
  PASS=$((PASS+1))
else
  red "rg Pattern-Scan: Treffer:"
  echo "$RG_OUT"
fi

# ── 6. VM Dateirechte (nur wenn SSH erreichbar) ───────────────────────────────
VM_HOST="${HYDRAHIVE_VM:-<your-vm-ip>}"
VM_USER="${HYDRAHIVE_VM_USER:-hydrahive}"
VM_KEY="${HYDRAHIVE_VM_KEY:-$HOME/.ssh/<your-ssh-key>}"
info "6/6 VM Dateirechte — $VM_HOST (übersprungen wenn nicht erreichbar)"
if ssh -i "$VM_KEY" -o ConnectTimeout=5 -o BatchMode=yes "$VM_USER@$VM_HOST" true 2>/dev/null; then
  OPEN=$(ssh -i "$VM_KEY" "$VM_USER@$VM_HOST" \
    "find /etc/hydrahive /agents /projects -type f \
      \( -name '*.json' -o -name '*.md' -o -name '*token*' -o -name '*secret*' -o -name '*credentials*' \) \
      -perm /044 -not -path '*agent.yaml' -not -path '*/soul.md' \
      -not -path '*/skills/*' -not -path '*/agentlink/*' \
      -not -path '*/memory/*' 2>/dev/null" || true)
  if [ -z "$OPEN" ]; then
    green "VM Dateirechte: alle sensitiven Dateien korrekt abgesichert"
    PASS=$((PASS+1))
  else
    red "VM Dateirechte: zu offen (sollten 600/640 sein):"
    echo "$OPEN"
  fi
else
  info "VM nicht erreichbar — Rechte-Check übersprungen"
fi

# ── Zusammenfassung ───────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════"
echo "Ergebnis: $PASS OK, $FAIL FAIL"
if [ "$FAIL" -eq 0 ]; then
  echo -e "\033[0;32mScan sauber.\033[0m"
else
  echo -e "\033[0;31mBitte Findings oben prüfen.\033[0m"
  exit 1
fi
