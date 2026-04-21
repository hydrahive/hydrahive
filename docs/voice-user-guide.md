# Voice — Benutzerhandbuch

> Stand: 2026-04-21 | Issue: #793, #799

---

## Voice nutzen

HydraHive unterstützt Sprachausgabe (TTS) und Spracheingabe (STT) über verschiedene Provider. Die Konfiguration erfolgt entweder durch den Admin (globaler Standard) oder pro User.

### Voraussetzungen

- [ ] HydraHive Core läuft
- [ ] Voice-Extension installiert (Wyoming Docker oder MiniMax API-Key)
- [ ] Admin hat Provider konfiguriert unter `/voice/providers/default`

---

## Provider und Stimmen wechseln

### Schritt 1: Status prüfen

Öffne `/voice` (VoicePage) oder rufe auf:

```bash
curl http://localhost:8765/voice/status \
  -H "Authorization: Bearer <token>"
```

Antwort zeigt alle verfügbaren Provider und Stimmen:

```json
{
  "stt_providers": [
    { "id": "wyoming-stt", "name": "Wyoming Whisper", "available": true }
  ],
  "tts_providers": [
    { "id": "wyoming-tts", "name": "Wyoming Piper", "available": true, "voices": [...] },
    { "id": "minimax-t2a", "name": "MiniMax T2A", "available": true, "voices": [...] }
  ],
  "current_tts": { "provider": "wyoming-tts", "voice": "de_DE-thorsten-high" },
  "current_stt": { "provider": "wyoming-stt" }
}
```

### Schritt 2: Provider auswählen

**Admin (globaler Default):**

1. Öffne SystemPage → Voice
2. Wähle TTS-Provider aus dem Dropdown
3. Wähle STT-Provider aus dem Dropdown
4. Speichern

**Oder per API:**

```bash
# Global-Provider setzen (Admin)
curl -X PUT http://localhost:8765/voice/providers/default \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"provider_type": "tts", "provider_id": "minimax-t2a"}'
```

### Schritt 3: Stimme auswählen (TTS)

**Pro User (VoicePage):**

1. Öffne `/voice`
2. Wähle Provider im TTS-Dropdown
3. Wähle Stimme im Stimme-Dropdown
4. Stimme wird automatisch gespeichert

**Per API:**

```bash
curl -X PUT http://localhost:8765/voice/preferences \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"provider_type": "tts", "provider_id": "minimax-t2a", "voice_id": "speech-2.8-hd"}'
```

---

## Verfügbare Provider

### Wyoming (lokal)

| Typ | Provider | Stimme | Voraussetzung |
|-----|----------|--------|--------------|
| STT | `wyoming-stt` | — | Docker Container mit faster-whisper |
| TTS | `wyoming-tts` | `de_DE-thorsten-high` | Docker Container mit Piper |

**Installation:**

```bash
# Im Installer: Modul 18_voice.sh auswählen
# Docker wird automatisch installiert
```

### MiniMax (Cloud)

| Typ | Provider | Modelle | Voraussetzung |
|-----|----------|---------|--------------|
| TTS | `minimax-t2a` | `speech-2.8-hd` | MiniMax API-Key |

**Konfiguration:**

1. Öffne LlmConfigPage
2. MiniMax Tab auswählen
3. API-Key eintragen
4. Speichern

**Hinweis:** MiniMax ist ein Cloud-Service — Audio wird für TTS an MiniMax-Server gesendet.

---

## Voice in Chat

### Chat-Vorlesen

Wenn TTS aktiviert ist, kannst du den Agent bitten, seine Antwort vorzulesen:

```
User: Erkläre mir Docker in 3 Sätzen
Agent: [Antwortet + liest sie vor, wenn TTS aktiv]
```

### Voice-Pipeline (Full Duplex)

Sende Audio, der Agent antwortet mit Audio:

```bash
curl -X POST http://localhost:8765/voice/pipeline \
  -H "Authorization: Bearer <token>" \
  --data-binary @aufnahme.wav \
  --output antwort.wav
```

---

## Fehlerbehandlung

### "Provider nicht registriert"

```
ValueError: TTS-Provider nicht registriert: minimax-t2a
```

→ MiniMax API-Key prüfen (LlmConfigPage → MiniMax Tab)

### "STT-Service nicht erreichbar"

```
HTTP 503: "STT-Service nicht erreichbar"
```

→ Wyoming Docker Container prüfen:

```bash
docker ps | grep hydrahive
docker logs hydrahive-stt
```

### "MiniMax Token-Plan unterstützt Modell nicht"

```
MiniMax Error: "your current token plan not support model"
```

→ Nur `speech-2.8-hd` ist im 10x Starter Plan verfügbar. Stimme in VoicePage auf dieses Modell setzen.

---

## VoicePage

Die VoicePage (`/voice`) bietet:

- **TTS-Dropdown:** Provider wählen
- **STT-Dropdown:** Provider wählen
- **Stimme-Dropdown:** Stimme pro Provider
- **Test-Buttons:** Kurze TTS/STT-Tests
- **Status:** Verfügbarkeit aller Provider

---

## Siehe auch

- [Voice Provider Architektur](voice-providers.md)
- [API: Voice](api/voice.md)
