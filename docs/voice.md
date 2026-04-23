# Voice — Konfiguration und Nutzung

> Stand: 2026-04-23 | Issue: #793, #799

---

## Was ist HydraHive Voice?

HydraHive abstrahiert Sprachdienste (STT = Spracherkennung, TTS = Sprachsynthese) hinter einem einheitlichen Provider-Modell. Du kannst zwischen lokaler und Cloud-basierter Technologie wählen, ohne die API zu ändern.

**Unterstützte Provider:**

| Modus | STT | TTS |
|-------|-----|-----|
| Lokal (Docker) | Wyoming Whisper (`wyoming-stt`) | Wyoming Piper (`wyoming-tts`) |
| Cloud | — | MiniMax T2A (`minimax-t2a`) |

---

## Config-Datei

**Pfad:** `/etc/hydrahive/voice.json`

```json
{
  "voice": {
    "stt_provider": "wyoming-stt",
    "tts_provider": "wyoming-tts"
  },
  "default_agent": "personal_admin"
}
```

**Legacy-Format (Wyoming-only, bis #797):**

```json
{
  "stt_url": "http://127.0.0.1:10300",
  "tts_url": "http://127.0.0.1:10200",
  "default_agent": "personal_admin"
}
```

Das Legacy-Format wird beim Laden automatisch erkannt und auf das neue Schema abgebildet.

---

## Provider auswählen

### Global (Admin — alle User)

**Per API:**

```bash
# TTS-Provider setzen
curl -X PUT http://localhost:8765/voice/providers/default \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"provider_type": "tts", "provider_id": "minimax-t2a"}'

# STT-Provider setzen
curl -X PUT http://localhost:8765/voice/providers/default \
  -H "Authorization: Bearer <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"provider_type": "stt", "provider_id": "wyoming-stt"}'
```

**Was passiert:** Die Auswahl wird in `/etc/hydrahive/voice.json` unter `voice.tts_provider` / `voice.stt_provider` geschrieben. Alle User verwenden diesen Provider, sofern sie keine eigene Präferenz haben.

### Pro User (Stimme/Provider überschreiben)

```bash
# Stimme für User setzen
curl -X PUT http://localhost:8765/voice/preferences \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"provider_type": "tts", "provider_id": "minimax-t2a", "voice_id": "male-qn-qingse"}'
```

Gespeichert in der SQLite-DB `voice.db` (`voice_preferences`-Tabelle).

### Priorität

```
User-Preference (SQLite) → Global (voice.json) → Registry-Default
```

---

## Backend-Key setzen

### MiniMax API-Key

MiniMax TTS nutzt denselben API-Key wie MiniMax Image/Music/Video. Der Key wird über die **LlmConfigPage** (MiniMax-Tab) oder direkt in `/etc/hydrahive/llm_config.json` eingetragen:

```json
{
  "minimax": {
    "api_key": "dein-minimax-api-key"
  }
}
```

**Token-Plan-Hinweis:** Im 10x Starter Plan ist nur `speech-2.8-hd` enthalten. Andere Modelle (`speech-02-*`, `speech-2.6-*`, `speech-2.8-turbo`) sind **nicht** im Plan und führen zu `"your current token plan not support model"`.

### Wyoming Docker

Wyoming braucht keinen API-Key. Es wird über Module 18 (`18_voice.sh`) installiert. Dabei wird automatisch `voice.json` geschrieben:

```json
{
  "voice": {
    "stt_provider": "wyoming-stt",
    "tts_provider": "wyoming-tts"
  },
  "default_agent": "personal_admin"
}
```

---

## Verfügbare Stimmen (MiniMax TTS)

| Voice-ID | Name | Gender |
|----------|------|--------|
| `male-qn-qingse` | Male — Jugendlich | male |
| `male-qn-jingying` | Male — Seriös | male |
| `male-qn-badao` | Male — Dominant | male |
| `female-shaonv` | Female — Jung | female |
| `female-yujie` | Female — Elegant | female |
| `female-chengshu` | Female — Reif | female |
| `female-tianmei` | Female — Sanft | female |
| `presenter_male` | Presenter — Male | male |
| `presenter_female` | Presenter — Female | female |

Alle Stimmen sind multilingual (`language: mul`).

---

## Wyoming STT/TTS (lokal)

Wyoming läuft als Docker-Container auf dem Host:

- **STT:** Port 10300 (faster-whisper, Modell `small`, Sprache `de`)
- **TTS:** Port 10200 (Piper, Stimme `de_DE-thorsten-high`)

Container werden über `docker compose` in `/opt/hydrahive-voice/` verwaltet. Logs:

```bash
docker ps | grep hydrahive
docker logs hydrahive-stt
docker logs hydrahive-tts
```

---

## Installer: voice.json anlegen

Der Haupt-Installer (`install.sh`) hat kein obligatorisches Voice-Modul. Um bei der Installation ein Default-`voice.json` anzulegen, wurde `installer/modules/18_voice.sh` als Extension-Modul bereitgestellt.

Die Extension wird über den Extension-Manager aktiviert oder direkt per:

```bash
sudo bash /opt/hydrahive/installer/extensions/install/voice.sh
```

Falls keine Extension installiert wird und `voice.json` fehlt, kannst du sie manuell anlegen:

```bash
sudo mkdir -p /etc/hydrahive
sudo touch /etc/hydrahive/voice.json
sudo chmod 600 /etc/hydrahive/voice.json
```

Mit folgendem Inhalt:

```json
{
  "voice": {
    "stt_provider": "wyoming-stt",
    "tts_provider": "wyoming-tts"
  },
  "default_agent": "personal_admin"
}
```

---

## Status prüfen

```bash
curl http://localhost:8765/voice/status \
  -H "Authorization: Bearer <token>"
```

Antwort zeigt alle registrierten Provider, ihre Verfügbarkeit und verfügbare Stimmen.

---

## Endpoints (Überblick)

| Methode | Pfad | Funktion |
|---------|------|----------|
| `POST` | `/voice/stt` | Audio (WAV) → Text |
| `POST` | `/voice/tts` | Text → Audio (WAV) |
| `POST` | `/voice/pipeline` | Audio → Agent → Audio |
| `GET` | `/voice/status` | Alle Provider + Status |
| `GET` | `/voice/preferences` | User-Stimm-Präferenzen |
| `PUT` | `/voice/preferences` | User-Stimm-Präferenzen setzen |
| `PUT` | `/voice/providers/default` | Global-Default setzen (Admin) |

---

## Siehe auch

- [Voice Provider Architektur](voice-providers.md)
- [Voice User Guide](voice-user-guide.md)
- [API-Referenz](api-reference.md)