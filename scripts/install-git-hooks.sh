#!/usr/bin/env bash
# install-git-hooks.sh — setzt core.hooksPath auf .githooks und macht Hooks ausführbar.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
if [ ! -d .githooks ]; then
  echo "FEHLER: .githooks/ nicht im Repo vorhanden." >&2
  exit 1
fi
chmod +x .githooks/* 2>/dev/null || true
git config core.hooksPath .githooks
echo "Git hooks installiert: core.hooksPath=.githooks"
echo "Aktive Hooks:"
ls -1 .githooks/
echo ""
echo "Bypass eines Hooks (nur mit Grund): HYDRAHIVE_SKIP_SECRET_SCAN=1 git push"
