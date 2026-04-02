# XiaoZhi Backend — HydraHive Voice Integration

## Schnellstart

### 1. XiaoZhi Server klonen
```bash
git clone https://github.com/xinnan-tech/xiaozhi-esp32-server.git
cd xiaozhi-esp32-server
```

### 2. Config einfügen
```bash
cp /opt/hydrahive/voice/xiaozhi-config/config.yaml data/.config.yaml
# IP anpassen:
sed -i 's/<HYDRAHIVE-IP>/192.168.178.181/g' data/.config.yaml
```

### 3. Server starten
```bash
# Mit Docker (empfohlen):
docker compose up -d

# Oder nativ:
pip install -r requirements.txt
python app.py
```

### 4. Voice PE verbinden
Die Voice PE muss auf den XiaoZhi WebSocket zeigen:
```
ws://<HYDRAHIVE-IP>:8000/xiaozhi/v1/
```

## Architektur

```
Voice PE (ESP32-S3)
    │
    │  WebSocket (OPUS Audio)
    ↓
XiaoZhi Backend Server
    │
    ├── VAD: SileroVAD (Spracherkennung)
    ├── ASR: FunASR (Speech-to-Text, lokal)
    ├── LLM: HydraHive Agent API
    └── TTS: EdgeTTS (Text-to-Speech)
    │
    │  WebSocket (OPUS Audio)
    ↓
Voice PE (Lautsprecher)
```

## Stimme ändern

In `data/.config.yaml` unter `tts.EdgeTTS.voice`:

| Stimme | Beschreibung |
|--------|-------------|
| `de-DE-ConradNeural` | Männlich, klar (Standard) |
| `de-DE-KatjaNeural` | Weiblich, warm |
| `de-DE-AmalaNeural` | Weiblich, jung |
| `de-DE-FlorianMultilingualNeural` | Männlich, mehrsprachig |

## Offline-Betrieb (ohne Internet)

Für komplett lokalen Betrieb ohne EdgeTTS:
1. HydraHive Voice-Extension installieren (Piper TTS)
2. In config.yaml `TTS` auf einen Custom-HTTP-Wrapper umstellen
3. Piper liefert `de_DE-thorsten-high` — gute Qualität, komplett offline
