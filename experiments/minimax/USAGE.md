# MiniMax Integration — Usage Reference

> **Status-Hinweis:** Dieses Dokument beschreibt das **Ziel-Verhalten** des
> PoC-Pakets, wenn es in HydraHive integriert wäre. Der Ist-Zustand ist ein
> Branch-Sketch ohne Tool-Registry-Eintrag, ohne MCP-Auto-Start und ohne
> pip-Installation. Als Leitfaden für die schrittweise Integration unter #680
> (Phase 1 = Image-Gen in #679). Status-Details in [`README.md`](README.md).

## Komplett-Paket

API Client + MCP Server + Native Tools für MiniMax Multimodal (Image / Video / Music / TTS / STT / Vision).

## Dateistruktur

```
hydrahive-minimax-integration/
├── core/src/hydrahive_core/
│   ├── minimax_client.py       # API Client (alle Endpoints)
│   ├── minimax_mcp_server.py   # MCP Server (streamableHttp)
│   ├── minimax_tools.py        # Native Agent-Tools
│   └── __init__.py
├── config/
│   ├── llm_config.yaml         # LLM Provider Config
│   └── mcp_servers.yaml        # MCP Server Config
└── README.md
```

## Installation

### 1. API Client (`minimax_client.py`)

```python
from hydrahive_core.minimax_client import MiniMaxClient

client = MiniMaxClient(
    api_key="MINIMAX_API_KEY",
    base_url="https://api.minimax.io",  # Optional: China-Endpoint
)

# Bild generieren
urls = await client.generate_image("A sunset over mountains")

# Video generieren (wartet auf Fertigstellung)
result = await client.generate_video("A cat playing piano", duration=6)

# Musik generieren
result = await client.generate_music("Upbeat pop music with guitar")

# TTS
audio = await client.text_to_speech("Hallo Welt!", voice="male-qn-qingse")

# STT
text = await client.speech_to_text_from_file("audio.mp3")

# Vision
analysis = await client.analyze_image(
    "https://example.com/image.jpg",
    "Was ist auf dem Bild?",
)
```

### 2. MCP Server (`minimax_mcp_server.py`)

**Starten:**

```bash
python -m hydrahive_core.minimax_mcp_server --port 8182
```

**In HydraHive einbinden** (`hydrahive.settings.json`):

```json
{
  "mcp_servers": [
    {
      "id": "minimax-multimodal",
      "name": "MiniMax Media Tools",
      "transport": "streamableHttp",
      "url": "http://127.0.0.1:8182/mcp"
    }
  ]
}
```

**Tool-Aufruf via MCP:**

```json
{
  "method": "tools/call",
  "params": {
    "name": "minimax_image_generate",
    "arguments": {
      "prompt": "A beautiful sunset",
      "size": "1024x1024"
    }
  }
}
```

### 3. Native Tools (`minimax_tools.py`)

**In Agent-Konfiguration:**

```yaml
# agent.yaml
id: media-agent
tools:
  - minimax_image_gen
  - minimax_video_gen
  - minimax_music_gen
  - minimax_tts
  - minimax_stt
  - minimax_vision
```

**Direkt verwenden:**

```python
from hydrahive_core.minimax_tools import MiniMaxToolFactory

# Einzelnes Tool
tool = MiniMaxToolFactory.create("minimax_image_gen")
result = tool.execute_sync("A cat")

# Alle Tools
tools = MiniMaxToolFactory.create_all()
result = tools["minimax_tts"].execute_sync("Hello")
```

## Umgebungsvariablen

```bash
# API Key (Pflicht)
MINIMAX_API_KEY=your-api-key

# Endpoint Override (optional, für China)
MINIMAX_BASE_URL=https://api.minimax.chat/v1

# Output-Verzeichnis (optional)
MINIMAX_OUTPUT_DIR=/projects/media-output
```

## Unterstützte Features

| Feature | Endpoint | Timeout | Notes |
|---|---|---|---|
| Text | `/chat/completions` | 30s | Bereits in #616 |
| Vision | `/chat/completions` (multimodal) | 30s | Inklusive |
| Image | `/images` | 60s | 1–4 Bilder |
| Video | `/video_generation` | 600s | Async Polling |
| Music | `/music_generation` | 300s | Async Polling |
| TTS | `/t2a_2` | 30s | MP3 / WAV |
| STT | `/audio/transcriptions` | 60s | Auto-Sprache |

## Agent-Prompt-Templates

### Bild-Generierung

```
Du bist ein Bild-Künstler Agent. Verwende minimax_image_gen um Bilder zu erstellen.
- Beschreibe Bilder detailliert und in Englisch
- Nutze passende Größen: 1024x1024 für quadratisch, 1024x768 für Landscape
- Speichere Ergebnisse im /projects/media/ Ordner
```

### Video-Generierung

```
Du bist ein Video-Produzent. Verwende minimax_video_gen für kurze Clips.
- Beschreibe Bewegungen und Szenen dynamisch
- 6s für Social Media, 10s für detaillierte Clips
- Nutze 1280x720 für Standard, 1920x1080 für HD
```

### Musik-Generierung

```
Du bist ein Musik-Komponist. Verwende minimax_music_gen für Hintergrundmusik.
- Definiere Genre, Tempo, Stimmung in der Beschreibung
- Verfügbare Stile: pop, rock, jazz, classical, electronic, acoustic, ambient
```

### TTS

```
Du bist ein Sprecher. Verwende minimax_tts für Sprachausgabe.
- Verfügbare Stimmen: male-qn-qingse, female-shaonv, male-yunyang, female-xiaoniu
- Geschwindigkeit: 0.5 (langsam) bis 2.0 (schnell)
- Speichere MP3-Dateien mit sprechendem Dateinamen
```

## Sicherheits-Hinweise

1. **API-Keys nie in Git** — `.gitignore` nutzen
2. **Output-Dir bereinigen** — Regelmäßig alte Dateien löschen
3. **Quota überwachen** — `minimax_check_quota` Tool nutzen
4. **Async-Polling** — Video/Musik können Minuten dauern, Timeout beachten

## Troubleshooting

### `No module named 'aiohttp'`

```bash
pip install aiohttp httpx
```

### `Video generation timed out`

```python
# Timeout erhöhen
client.generate_video(..., timeout=900.0)  # 15 min statt 10 min
```

### `Audio file not found`

```python
from pathlib import Path
path = Path("audio.mp3")
print(f"Exists: {path.exists()}, Abs: {path.resolve()}")
```

## Performance-Tipps

1. **Connection Pooling** — HTTP-Client wiederverwenden
2. **Batch-Requests** — Mehrere Bilder parallel generieren
3. **Lokaler Cache** — Generierte Medien zwischenspeichern
4. **Async Polling** — Video/Musik nicht blockieren

## Nützliche Links

- MiniMax API Docs
- HydraHive Docs
- MCP Protocol Spec
