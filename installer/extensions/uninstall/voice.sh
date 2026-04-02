#!/usr/bin/env bash
# Voice Interface deinstallieren
set -euo pipefail

VOICE_DIR="/opt/hydrahive-voice"

if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi

info "Stoppe Voice-Container..."
if [ -f "${VOICE_DIR}/docker-compose.yml" ]; then
    cd "${VOICE_DIR}"
    docker compose down -v 2>/dev/null || docker-compose down -v 2>/dev/null || true
fi

info "Entferne Voice-Verzeichnis..."
rm -rf "${VOICE_DIR}"
rm -f /etc/hydrahive/voice.json

success "Voice Interface deinstalliert"
