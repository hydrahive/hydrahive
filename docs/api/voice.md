# API: Voice

> Stand: 2026-04-21 | Issue: #793, #799

Alle Endpoints erfordern Authentifizierung via Bearer-Token (`Authorization: Bearer <jwt>`).

---

## POST /voice/stt

Erkennt Sprache aus einer Audio-Datei und gibt den Text zurück.

**Request:**

```
POST /voice/stt
Authorization: Bearer <token>
Content-Type: multipart/form-data

audio: <WAV-Datei>
```

**Response (200):**

```json
{
  "text": "Das ist der erkannte Text"
}
```

**Response (400):** Leere Audio-Datei

```json
{
  "detail": "Leere Audio-Datei"
}
```

**Response (503):** STT-Service nicht erreichbar

```json
{
  "detail": "STT-Service nicht erreichbar — ist die Voice-Extension installiert?"
}
```

---

## POST /voice/tts

Wandelt Text in Audio um (WAV-Format).

**Request:**

```
POST /voice/tts
Authorization: Bearer <token>
Content-Type: application/json

{
  "text": "Hallo Welt, das ist eine Sprachausgabe"
}
```

**Response (200):**

```
Content-Type: audio/wav
Content-Disposition: inline; filename=voice.wav

<binary WAV data>
```

**Response (400):** Leerer Text

```json
{
  "detail": "text erforderlich"
}
```

**Response (503):** TTS-Service nicht erreichbar

```json
{
  "detail": "TTS-Service nicht erreichbar — ist die Voice-Extension installiert?"
}
```

---

## POST /voice/pipeline

Full-Duplex Voice: Audio rein, Audio raus. Der Agent erhält den transkribierten Text, antwortet, und die Antwort wird vorgelesen.

**Request:**

```
POST /voice/pipeline
Authorization: Bearer <token>
Content-Type: multipart/form-data

audio: <WAV-Datei>
agent_id: <optional: agent-id> (default: personal_admin)
```

**Response (200):**

```
Content-Type: audio/wav
Content-Disposition: inline; filename=voice.wav
X-Voice-Input: <erkannter Text>
X-Voice-Agent: <agent-id>

<binary WAV data>
```

**Response (400):** Kein Text erkannt

```json
{
  "detail": "Kein Text erkannt"
}
```

---

## GET /voice/status

Gibt Status aller Provider und verfügbare Stimmen zurück.

**Request:**

```
GET /voice/status
Authorization: Bearer <token>
```

**Response (200):**

```json
{
  "installed": true,
  "stt": {
    "host": "127.0.0.1",
    "port": 10300,
    "available": true
  },
  "tts": {
    "host": "127.0.0.1",
    "port": 10200,
    "available": true
  },
  "stt_providers": [
    {
      "id": "wyoming-stt",
      "name": "Wyoming Whisper",
      "available": true,
      "languages": ["de", "en"]
    }
  ],
  "tts_providers": [
    {
      "id": "minimax-t2a",
      "name": "MiniMax T2A",
      "available": true,
      "voices": [
        { "id": "speech-2.8-hd", "name": "HD Stimme", "language": "de", "gender": null }
      ]
    },
    {
      "id": "wyoming-tts",
      "name": "Wyoming Piper",
      "available": true,
      "voices": [
        { "id": "de_DE-thorsten-high", "name": "Thorsten High", "language": "de", "gender": "male" }
      ]
    }
  ],
  "current_stt": { "provider": "wyoming-stt" },
  "current_tts": { "provider": "minimax-t2a", "voice": "speech-2.8-hd" },
  "global_stt_provider": "wyoming-stt",
  "global_tts_provider": "minimax-t2a",
  "user_preferences": {
    "stt_provider": "wyoming-stt",
    "stt_voice": null,
    "tts_provider": "minimax-t2a",
    "tts_voice": "speech-2.8-hd"
  },
  "default_agent": "personal_admin"
}
```

---

## GET /voice/preferences

Gibt die Voice-Preferences des aktuellen Users zurück.

**Request:**

```
GET /voice/preferences
Authorization: Bearer <token>
```

**Response (200):**

```json
{
  "stt_provider": "wyoming-stt",
  "stt_voice": null,
  "tts_provider": "minimax-t2a",
  "tts_voice": "speech-2.8-hd"
}
```

---

## PUT /voice/preferences

Setzt Voice-Preferences für den aktuellen User.

**Request:**

```
PUT /voice/preferences
Authorization: Bearer <token>
Content-Type: application/json

{
  "provider_type": "tts",
  "provider_id": "minimax-t2a",
  "voice_id": "speech-2.8-hd"
}
```

**Felder:**

| Feld | Typ | Beschreibung |
|------|-----|--------------|
| `provider_type` | string | `"stt"` oder `"tts"` |
| `provider_id` | string | Provider-ID (z.B. `wyoming-stt`, `minimax-t2a`) |
| `voice_id` | string? | Stimme-ID (optional, provider-spezifisch) |

**Response (200):**

```json
{
  "stt_provider": "wyoming-stt",
  "stt_voice": null,
  "tts_provider": "minimax-t2a",
  "tts_voice": "speech-2.8-hd"
}
```

**Response (400):** Unbekannter Provider

```json
{
  "detail": "TTS-Provider nicht registriert: unknown-provider"
}
```

---

## PUT /voice/providers/default

Setzt den globalen Default-Provider (Admin nur).

**Request:**

```
PUT /voice/providers/default
Authorization: Bearer <admin-token>
Content-Type: application/json

{
  "provider_type": "tts",
  "provider_id": "minimax-t2a"
}
```

**Response (200):**

```json
{
  "global_stt_provider": "wyoming-stt",
  "global_tts_provider": "minimax-t2a"
}
```

**Response (403):** Non-Admin versucht Provider zu setzen

```json
{
  "detail": "Nur Admins dürfen den globalen Provider ändern"
}
```

---

## Fehler-Codes

| HTTP | Bedeutung |
|------|----------|
| 200 | Erfolgreich |
| 400 | Ungültige Anfrage (leerer Text, leeres Audio) |
| 401 | Nicht authentifiziert |
| 403 | Keine Berechtigung (Admin-Endpoint) |
| 404 | Agent nicht gefunden |
| 422 | Unprocessable Entity (Pydantic-Validierungsfehler) |
| 503 | Voice-Service nicht erreichbar |

---

## Siehe auch

- [Voice Provider Architektur](../voice-providers.md)
- [Voice User Guide](../voice-user-guide.md)
