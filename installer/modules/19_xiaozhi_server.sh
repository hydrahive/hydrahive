#!/usr/bin/env bash
# HydraHive Installer - Modul 19: XiaoZhi ESP32 Server
# Installiert den xiaozhi-esp32-server als Docker-Container.
# Dieser Server verbindet ESP32-Voice-Geräte (z.B. HA Voice PE) mit HydraHive.
# Idempotent: bereits laufender Container wird übersprungen.

set -euo pipefail

XIAOZHI_DIR="/opt/xiaozhi-esp32-server"
HYDRAHIVE_HOST="${HYDRAHIVE_HOST:-127.0.0.1}"
HYDRAHIVE_PORT="${HYDRAHIVE_PORT:-8765}"
SERVER_HOST="${SERVER_HOST:-$(hostname -I | awk '{print $1}')}"

# Fallback-Funktionen falls Script standalone läuft
if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi
if ! declare -f error   &>/dev/null; then error()   { echo "[ERROR] $1"; exit 1; }; fi

info "=== XiaoZhi ESP32 Server Setup ==="

# --- Docker-Check ---
if ! command -v docker &>/dev/null; then
    error "Docker nicht gefunden. Bitte zuerst Modul 18 (Voice) ausführen."
fi

# --- Verzeichnisse anlegen ---
mkdir -p "${XIAOZHI_DIR}/data" "${XIAOZHI_DIR}/models"

# --- Konfiguration schreiben (immer aktualisieren für Upgrade-Sicherheit) ---
CONFIG_FILE="${XIAOZHI_DIR}/data/.config.yaml"
if true; then
    info "Erstelle .config.yaml..."
    cat > "${CONFIG_FILE}" << YAML
server:
  websocket: "ws://${SERVER_HOST}:8000/xiaozhi/v1/"
  http: "http://${SERVER_HOST}:8003/"

selected_module:
  LLM: "OpenAILLM"
  ASR: "FunASR"
  TTS: "EdgeTTS"
  VAD: "SileroVAD"
  Memory: "local"

LLM:
  OpenAILLM:
    api_key: "hydrahive"
    base_url: "http://${HYDRAHIVE_HOST}:${HYDRAHIVE_PORT}/v1"
    model: "personal_admin"

tts:
  EdgeTTS:
    voice: "de-DE-ConradNeural"
    rate: 1.0

vad:
  SileroVAD:
    threshold: 0.5
    min_silence_duration_ms: 500
YAML
    success "Konfiguration erstellt"
else
    info "Konfiguration bereits vorhanden — überspringe"
fi

# --- docker-compose.yml schreiben ---
cat > "${XIAOZHI_DIR}/docker-compose.yml" << 'COMPOSE'
services:
  xiaozhi-esp32-server:
    image: ghcr.io/xinnan-tech/xiaozhi-esp32-server:server_latest
    container_name: xiaozhi-esp32-server
    restart: unless-stopped
    ports:
      - "8000:8000"
      - "8003:8003"
    security_opt:
      - seccomp:unconfined
    environment:
      - TZ=Europe/Berlin
    volumes:
      - ./data:/opt/xiaozhi-esp32-server/data
      - ./models:/opt/xiaozhi-esp32-server/models
COMPOSE

# --- Container starten ---
if docker ps --format '{{.Names}}' | grep -q '^xiaozhi-esp32-server$'; then
    info "xiaozhi-esp32-server läuft bereits — überspringe"
else
    info "Starte xiaozhi-esp32-server..."
    cd "${XIAOZHI_DIR}"
    docker compose pull --quiet
    docker compose up -d

    # Health-Check: warte bis Port 8003 antwortet
    info "Warte auf OTA-Endpunkt (Port 8003)..."
    for i in $(seq 1 30); do
        if nc -z 127.0.0.1 8003 2>/dev/null; then
            success "xiaozhi-esp32-server läuft (Port 8003 erreichbar)"
            break
        fi
        sleep 2
        if [[ $i -eq 30 ]]; then
            warn "Timeout — Server startet möglicherweise noch (FunASR-Modell wird geladen)"
        fi
    done
fi

success "XiaoZhi ESP32 Server bereit"
info "  WebSocket: ws://${SERVER_HOST}:8000/xiaozhi/v1/"
info "  OTA:       http://${SERVER_HOST}:8003/xiaozhi/ota/"
info "  LLM:       http://${HYDRAHIVE_HOST}:${HYDRAHIVE_PORT}/v1"
info ""
info "ESP32-Firmware muss auf diesen OTA-Endpunkt zeigen:"
info "  CONFIG_OTA_URL=\"http://${SERVER_HOST}:8003/xiaozhi/ota/\""
