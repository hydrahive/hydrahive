# Voice-Provider — Architektur-Dokumentation

> Stand: 2026-04-21 | Issue: #793, #799

---

## Übersicht

HydraHive unterstützt mehrere STT- (Speech-to-Text) und TTS- (Text-to-Speech) Provider über eine einheitliche **VoiceProviderRegistry**. Provider werden zur Laufzeit registriert und über die Config-Layer angesprochen.

```
User Request (STT/TTS)
       ↓
VoiceConfigLayer
       ↓
  ┌─────────────────────────────────┐
  │ 1. User-Preference (SQLite)     │  ← Stimme/Provider pro User
  │ 2. Global-Provider (voice.json) │  ← Admin-Default
  │ 3. Registry-Default             │  ← Erster registrierter Provider
  └─────────────────────────────────┘
       ↓
  VoiceProviderRegistry
       ↓
  ┌──────────────────────────┐
  │ wyoming-stt  (STT)      │  faster-whisper via Wyoming-Protokoll
  │ wyoming-tts  (TTS)      │  Piper via Wyoming-Protokoll
  │ minimax-t2a (TTS)       │  MiniMax Cloud TTS
  └──────────────────────────┘
```

---

## Verfügbare Provider

### STT-Provider

| ID | Name | Technologie | Lokal/Cloud | Default |
|----|------|-------------|-------------|---------|
| `wyoming-stt` | Wyoming Whisper | faster-whisper | Lokal (Docker) | ✅ |

### TTS-Provider

| ID | Name | Technologie | Lokal/Cloud | Default |
|----|------|-------------|-------------|---------|
| `wyoming-tts` | Wyoming Piper | Piper TTS | Lokal (Docker) | ✅ |
| `minimax-t2a` | MiniMax T2A | MiniMax Cloud | Cloud | — |

---

## Provider-Registry

### Stimme

**Datei:** `core/src/hydrahive_core/voice_providers/__init__.py`

```python
registry = VoiceProviderRegistry()  # Singleton

# Registrierung beim Setup:
setup_voice_registry()
# → wyoming-stt + wyoming-tts immer
# → minimax-t2a wenn MINIMAX_API_KEY gesetzt
```

**Kernmethoden:**

```python
registry.register(provider)              # Provider registrieren
registry.list_stt_providers()            # ["wyoming-stt"]
registry.list_tts_providers()           # ["wyoming-tts", "minimax-t2a"]
registry.get_stt("wyoming-stt")         # STTProvider-Instanz
registry.get_tts("minimax-t2a")        # TTSProvider-Instanz
registry.get_default_stt()             # STTProvider oder None
registry.get_default_tts()              # TTSProvider oder None
registry.set_default("tts", "minimax-t2a")  # Default setzen
```

### Provider-Basisklassen

**STT (`voice_providers/base.py`):**

```python
class STTProvider(ABC):
    provider_id: ClassVar[str]
    provider_name: ClassVar[str]

    async def recognize(audio_bytes: bytes, *, language: str = "de") -> STTResult
    async def get_languages() -> list[str]
    async def is_available() -> bool  # Default: True
```

**TTS (`voice_providers/base.py`):**

```python
class TTSProvider(ABC):
    provider_id: ClassVar[str]
    provider_name: ClassVar[str]

    async def synthesize(text: str, *, voice: str | None = None, **opts) -> TTSResult
    async def get_voices(language: str | None = None) -> list[Voice]
    async def is_available() -> bool  # Default: True
```

---

## Konfigurations-Layer

**Datei:** `voice_providers/config.py` — `VoiceConfigLayer`

### Config-Datei (`/etc/hydrahive/voice.json`)

```json
{
  "voice": {
    "tts_provider": "minimax-t2a",
    "stt_provider": "wyoming-stt"
  },
  "default_agent": "personal_admin"
}
```

**Legacy-Format ( Wyoming-only, bis #797):**

```json
{
  "stt_url": "http://127.0.0.1:10300",
  "tts_url": "http://127.0.0.1:10200",
  "default_agent": "personal_admin"
}
```

### User-Stimm-Preferences (SQLite)

**Tabelle:** `voice_preferences`

```sql
CREATE TABLE voice_preferences (
    username TEXT NOT NULL,
    provider_type TEXT NOT NULL CHECK(provider_type IN ('stt','tts')),
    provider_id TEXT NOT NULL,
    voice_id TEXT,
    updated_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (username, provider_type)
);
```

### Provider-Wahl (Priorität)

```
1. User-Preference (SQLite)     → Stimme/Provider pro User überschreibbar
2. Global-Provider (voice.json) → Admin-Default für alle User
3. Registry-Default            → Erster registrierter Provider (Fallback)
```

---

## Endpoints

Alle Voice-Endpoints erfordern Auth (Bearer-Token).

| Methode | Pfad | Beschreibung |
|---------|------|-------------|
| `POST` | `/voice/stt` | Audio (WAV) → Text |
| `POST` | `/voice/tts` | Text → Audio (WAV) |
| `POST` | `/voice/pipeline` | Audio → Agent → Audio (Full Duplex) |
| `GET` | `/voice/status` | Alle Provider + Status + verfügbare Stimmen |
| `GET` | `/voice/preferences` | User-Preferences |
| `PUT` | `/voice/preferences` | User-Preferences setzen |
| `PUT` | `/voice/providers/default` | Admin: Global-Default-Provider setzen |

### API-Details

Siehe: `docs/api/voice.md`

---

## Provider implementieren

### 1. Provider-Klasse erstellen

```python
# voice_providers/my_provider.py
from .base import TTSProvider, STTProvider
from .types import TTSResult, STTResult, Voice, AudioFormat

class MyTTSProvider(TTSProvider):
    provider_id = "my-tts"
    provider_name = "My TTS"

    async def synthesize(self, text: str, *, voice: str | None = None, **opts) -> TTSResult:
        # Audio generieren
        audio_bytes = await self._call_tts_api(text, voice)
        return TTSResult(
            audio=audio_bytes,
            format=AudioFormat(mime="audio/wav", sample_rate=22050),
            duration_sec=len(audio_bytes) / 22050 / 2
        )

    async def get_voices(self, language: str | None = None) -> list[Voice]:
        return [
            Voice(id="default", name="Default Voice", language="de", gender="male"),
        ]
```

### 2. Provider registrieren

```python
# main.py oder voice_providers/__init__.py
from .my_provider import MyTTSProvider
registry.register(MyTTSProvider())
```

### 3. Provider-Konfiguration (optional)

Falls der Provider API-Keys oder URLs braucht:

```python
# voice_providers/config.py erweitern
# oder /etc/hydrahive/voice.json:
{
  "voice": {
    "tts_provider": "my-tts",
    "providers": {
      "my-tts": {
        "api_key": "...",
        "base_url": "https://..."
      }
    }
  }
}
```

---

## Troubleshooting

### Provider nicht verfügbar (503)

```
HTTP 503: "STT-Service nicht erreichbar — ist die Voice-Extension installiert?"
```

1. Prüfe ob Provider registriert ist: `GET /voice/status`
2. Prüfe Firewall/Netzwerk wenn Cloud-Provider
3. Prüfe Logs: `journalctl -u hydrahive-core`

### MiniMax TTS schlägt fehl

```
MiniMax Error: "your current token plan not support model"
```

→ Das Modell `speech-2.8-hd` ist das einzige im 10x Starter Plan. Prüfe:
```bash
curl https://api.minimax.io/anthropic/v1/models \
  -H "Authorization: Bearer $MINIMAX_API_KEY"
```

### Wyoming Container startet nicht

```bash
docker ps -a | grep hydrahive
docker logs hydrahive-stt
docker logs hydrahive-tts
```

---

## Siehe auch

- [Voice User Guide](voice-user-guide.md)
- [API: Voice](api/voice.md)
- [Issue #793](https://github.com/hydrahive/hydrahive/issues/793) — Voice Provider-Abstraktion
