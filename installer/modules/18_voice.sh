#!/usr/bin/env bash
# HydraHive Installer - Modul 18: Voice Interface (STT + TTS)
#
# Option A: Wyoming (Docker) — faster-whisper + Piper lokal
# Option B: MiniMax (Cloud)  — MiniMax TTS Cloud, kein Docker nötig
#
# Idempotent: bereits laufende Container werden übersprungen.
# Backwards-kompatibel: Legacy voice.json mit stt_url/tts_url wird erkannt.

set -euo pipefail

VOICE_DIR="/opt/hydrahive-voice"
VOICE_CONFIG="/etc/hydrahive/voice.json"
HYDRAHIVE_USER="${HYDRAHIVE_USER:-hydrahive}"

# Fallback-Funktionen falls Script standalone läuft
if ! declare -f info    &>/dev/null; then info()    { echo "[INFO] $1"; }; fi
if ! declare -f success &>/dev/null; then success() { echo "[OK] $1"; }; fi
if ! declare -f warn    &>/dev/null; then warn()    { echo "[WARN] $1"; }; fi
if ! declare -f error   &>/dev/null; then error()   { echo "[ERROR] $1"; exit 1; }; fi
if ! declare -f ask     &>/dev/null; then
    ask() {
        local prompt="$1"
        local default="${2:-}"
        read -rp "$prompt [$default]: " reply
        echo "${reply:-$default}"
    }
fi

info "=== Voice Interface Setup (STT + TTS) ==="

# ── Legacy-Format erkennen ──────────────────────────────────────────────────
_read_existing_config() {
    if [ -f "${VOICE_CONFIG}" ]; then
        if grep -q '"voice":' "${VOICE_CONFIG}" 2>/dev/null; then
            echo "new"  # Bereits neues Format
        elif grep -q '"stt_url":' "${VOICE_CONFIG}" 2>/dev/null; then
            echo "legacy"  # Altes Format mit stt_url/tts_url
        else
            echo "empty"
        fi
    else
        echo "none"
    fi
}

# ── Provider wählen ────────────────────────────────────────────────────────
choose_provider() {
    local existing
    existing=$(_read_existing_config)

    if [ "$existing" = "legacy" ]; then
        info "Erkannte Legacy-Config (stt_url/tts_url) — starte Wyoming-Setup."
        echo "wyoming"
        return
    fi

    if [ "$existing" = "new" ]; then
        info "Erkannte bestehende Config — überspringe Provider-Wahl."
        # Bestehende Config auslesen und Provider ermitteln
        local current_tts
        current_tts=$(grep -o '"tts_provider": *"[^"]*"' "${VOICE_CONFIG}" 2>/dev/null | cut -d'"' -f4 || echo "")
        if [ -n "$current_tts" ]; then
            info "Bestehender TTS-Provider: $current_tts"
        fi
        # Admin muss manuell wählen — hier nur Wyoming als Default
        echo "wyoming"
        return
    fi

    info "Wähle den Voice-Provider:"
    echo ""
    echo "  [1] Wyoming (Docker) — faster-whisper (STT) + Piper (TTS)"
    echo "      Lokal, kein Cloud-Zugang nötig, braucht Docker + 4GB RAM"
    echo "      STT: Port 10300 | TTS: Port 10200"
    echo ""
    echo "  [2] MiniMax (Cloud) — MiniMax TTS"
    echo "      Cloud-basiert, keine Docker nötig, braucht MiniMax API-Key"
    echo "      TTS: MiniMax Cloud API | STT: weiterhin Wyoming"
    echo ""
    echo "  [3] Beides — Wyoming STT + MiniMax TTS"
    echo "      Kombination: lokaler STT + Cloud-TTS"
    echo ""

    local choice
    choice=$(ask "Auswahl" "1")
    case "$choice" in
        1) echo "wyoming" ;;
        2) echo "minimax" ;;
        3) echo "both" ;;
        *) echo "wyoming" ;;
    esac
}

# ── Wyoming-Setup (Docker) ────────────────────────────────────────────────
setup_wyoming() {
    info "=== Wyoming-Setup (Docker) ==="

    # Docker installieren falls nötig
    if ! command -v docker &>/dev/null; then
        info "Docker nicht gefunden — installiere..."
        apt-get -o DPkg::Lock::Timeout=120 update -qq
        apt-get -o DPkg::Lock::Timeout=120 install -y -qq ca-certificates curl gnupg
        install -m 0755 -d /etc/apt/keyrings
        curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null
        chmod a+r /etc/apt/keyrings/docker.gpg
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
        apt-get -o DPkg::Lock::Timeout=120 update -qq
        apt-get -o DPkg::Lock::Timeout=120 install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
        systemctl enable --now docker
        success "Docker installiert"
    fi

    # Verzeichnis anlegen
    mkdir -p "${VOICE_DIR}"

    # Docker Compose
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

    # Container starten
    info "Starte STT + TTS Container (erster Start lädt Modelle — kann 2-5 Min dauern)..."
    cd "${VOICE_DIR}"
    docker compose pull 2>&1 || true
    docker compose up -d 2>&1 || true

    # Warten bis Services ready
    info "Warte auf STT-Service..."
    local stt_ready=false
    for i in $(seq 1 60); do
        if (echo | timeout 2 nc -w1 127.0.0.1 10300) >/dev/null 2>&1; then
            success "STT (faster-whisper) läuft auf Port 10300"
            stt_ready=true
            break
        fi
        [ "$i" -eq 60 ] && warn "STT noch nicht bereit — startet im Hintergrund weiter"
        sleep 3
    done

    info "Warte auf TTS-Service..."
    local tts_ready=false
    for i in $(seq 1 60); do
        if (echo | timeout 2 nc -w1 127.0.0.1 10200) >/dev/null 2>&1; then
            success "TTS (Piper) läuft auf Port 10200"
            tts_ready=true
            break
        fi
        [ "$i" -eq 60 ] && warn "TTS noch nicht bereit — startet im Hintergrund weiter"
        sleep 3
    done

    echo "$stt_ready $tts_ready"
}

# ── MiniMax-Config schreiben ───────────────────────────────────────────────
write_minimax_config() {
    local tts_provider="${1:-minimax-t2a}"
    local stt_provider="${2:-wyoming-stt}"

    info "Schreibe Voice-Config..."

    # Bestehende Config laden falls vorhanden
    local existing_json="{}"
    if [ -f "${VOICE_CONFIG}" ]; then
        existing_json=$(cat "${VOICE_CONFIG}")
    fi

    # voice-Section schreiben (neues Format)
    cat > "${VOICE_CONFIG}" <<JSON
{
  "voice": {
    "tts_provider": "${tts_provider}",
    "stt_provider": "${stt_provider}"
  },
  "default_agent": "personal_admin"
}
JSON

    chmod 600 "${VOICE_CONFIG}"
    chown "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${VOICE_CONFIG}" 2>/dev/null || true

    info "Voice-Config geschrieben:"
    info "  TTS-Provider: ${tts_provider}"
    info "  STT-Provider: ${stt_provider}"
}

# ── Wyoming-Config schreiben (Legacy-kompatibel) ───────────────────────────
write_wyoming_config() {
    info "Schreibe Voice-Config (Legacy-Format)..."

    cat > "${VOICE_CONFIG}" <<JSON
{
  "stt_url": "http://127.0.0.1:10300",
  "tts_url": "http://127.0.0.1:10200",
  "default_agent": "personal_admin"
}
JSON

    chmod 600 "${VOICE_CONFIG}"
    chown "${HYDRAHIVE_USER}:${HYDRAHIVE_USER}" "${VOICE_CONFIG}" 2>/dev/null || true
}

# ── MiniMax API-Key prüfen ─────────────────────────────────────────────────
check_minimax_key() {
    local key
    key=$(grep -o '"minimax".*"api_key": *"[^"]*"' /etc/hydrahive/llm_config.json 2>/dev/null | cut -d'"' -f4 || \
          grep -o 'MINIMAX_API_KEY=[^[:space:]]*' /etc/environment 2>/dev/null | cut -d'=' -f2 || \
          echo "")

    if [ -z "$key" ]; then
        warn "MiniMax API-Key nicht in /etc/hydrahive/llm_config.json gefunden."
        warn "Bitte in LlmConfigPage konfigurieren."
    fi
    echo "$key"
}

# ── MAIN ───────────────────────────────────────────────────────────────────
main() {
    local provider
    provider=$(choose_provider)

    case "$provider" in
        wyoming)
            setup_wyoming
            write_wyoming_config
            success "Voice Interface installiert (Wyoming)!"
            info "  STT: http://127.0.0.1:10300 (faster-whisper, Deutsch)"
            info "  TTS: http://127.0.0.1:10200 (Piper, de_DE-thorsten-high)"
            info "  API: POST /api/voice, POST /api/voice/pipeline"
            ;;

        minimax)
            info "=== MiniMax-Setup (Cloud) ==="
            check_minimax_key
            write_minimax_config "minimax-t2a" "wyoming-stt"
            success "Voice Interface installiert (MiniMax TTS + Wyoming STT)!"
            info "  TTS: MiniMax Cloud (minimax-t2a)"
            info "  STT: http://127.0.0.1:10300 (faster-whisper, Deutsch)"
            info "  → Wyoming-STT Container muss noch installiert werden!"
            ;;

        both)
            info "=== Wyoming + MiniMax-Setup ==="
            local result
            result=$(setup_wyoming)
            check_minimax_key
            write_minimax_config "minimax-t2a" "wyoming-stt"
            success "Voice Interface installiert (Wyoming STT + MiniMax TTS)!"
            info "  STT: http://127.0.0.1:10300 (faster-whisper)"
            info "  TTS: MiniMax Cloud (minimax-t2a) — Default"
            info "  Wyoming-TTS läuft parallel auf Port 10200"
            ;;
    esac
}

main "$@"
