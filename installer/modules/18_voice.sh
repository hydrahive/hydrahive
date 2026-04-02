#!/usr/bin/env bash
# HydraHive Installer - Modul 18: Voice Interface (STT + TTS)
# Installiert faster-whisper (STT) und Piper (TTS) als Docker-Container.
# Idempotent: bereits laufende Container werden übersprungen.

set -euo pipefail

VOICE_DIR="/opt/hydrahive-voice"
VOICE_CONFIG="/etc/hydrahive/voice.json"
HYDRAHIVE_USER="${HYDRAHIVE_USER:-hydrahive}"

# Fallback-Funktionen falls Script standalone läuft
if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi
if ! declare -f error   &>/dev/null; then error()   { echo "[ERROR] $1"; exit 1; }; fi

info "=== Voice Interface Setup (STT + TTS) ==="

# --- Docker installieren falls nötig ---
if ! command -v docker &>/dev/null; then
    info "Docker nicht gefunden — installiere..."
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    systemctl enable --now docker
    success "Docker installiert"
fi

# --- Verzeichnis anlegen ---
mkdir -p "${VOICE_DIR}"

# --- Docker Compose ---
info "Erstelle docker-compose.yml..."
cat > "${VOICE_DIR}/docker-compose.yml" <<'COMPOSE'
services:
  stt:
    image: rhasspy/wyoming-whisper
    container_name: hydrahive-stt
    restart: unless-stopped
    network_mode: host
    command: >
      --model small
      --language de
      --uri tcp://127.0.0.1:10300
    volumes:
      - stt-data:/data
    environment:
      - WHISPER_DEVICE=cpu

  tts:
    image: rhasspy/wyoming-piper
    container_name: hydrahive-tts
    restart: unless-stopped
    network_mode: host
    command: >
      --voice de_DE-thorsten-high
      --uri tcp://127.0.0.1:10200
    volumes:
      - tts-data:/data

volumes:
  stt-data:
  tts-data:
COMPOSE

# --- Container starten ---
info "Starte STT + TTS Container (erster Start lädt Modelle — kann 2-5 Min dauern)..."
cd "${VOICE_DIR}"
docker compose pull 2>&1
docker compose up -d 2>&1

# --- Config schreiben ---
info "Schreibe Voice-Config..."
cat > "${VOICE_CONFIG}" <<JSON
{
  "stt_url": "http://127.0.0.1:10300",
  "tts_url": "http://127.0.0.1:10200",
  "default_agent": "personal_admin"
}
JSON
chmod 600 "${VOICE_CONFIG}"
chown "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${VOICE_CONFIG}" 2>/dev/null || true

# --- Warten bis Services ready ---
info "Warte auf STT-Service..."
for i in $(seq 1 60); do
    if (echo | timeout 2 nc -w1 127.0.0.1 10300) >/dev/null 2>&1; then
        success "STT (faster-whisper) läuft auf Port 10300"
        break
    fi
    [ "$i" -eq 60 ] && warn "STT noch nicht bereit — startet im Hintergrund weiter"
    sleep 3
done

info "Warte auf TTS-Service..."
for i in $(seq 1 60); do
    if (echo | timeout 2 nc -w1 127.0.0.1 10200) >/dev/null 2>&1; then
        success "TTS (Piper) läuft auf Port 10200"
        break
    fi
    [ "$i" -eq 60 ] && warn "TTS noch nicht bereit — startet im Hintergrund weiter"
    sleep 3
done

success "Voice Interface installiert!"
info "  STT: http://127.0.0.1:10300 (faster-whisper, Deutsch)"
info "  TTS: http://127.0.0.1:10200 (Piper, de_DE-thorsten-high)"
info "  API: POST /api/voice, POST /api/voice/pipeline"
