# Changelog

All notable changes to HydraHive are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added

- **Voice Provider-Architektur** ([#793](https://github.com/hydrahive/hydrahive/issues/793))
  - Provider-Registry für TTS/STT eingeführt ([#794](https://github.com/hydrahive/hydrahive/issues/794))
  - MiniMax T2A Provider ([#795](https://github.com/hydrahive/hydrahive/issues/795))
  - VoicePage mit Provider-Dropdowns ([#797](https://github.com/hydrahive/hydrahive/issues/797))
  - Dokumentation + Installer-Anpassung ([#799](https://github.com/hydrahive/hydrahive/issues/799))
    - `docs/voice-providers.md` — Architektur-Doku
    - `docs/voice-user-guide.md` — End-User-Doku
    - `docs/api/voice.md` — API-Referenz
    - Installer `18_voice.sh` — Wyoming oder MiniMax wählbar

## [0.x.x] — 2026-04-21

### Added

- MiniMax TTS Provider (`minimax-t2a`) mit Modell `speech-2.8-hd`
- MiniMax Token-Plan Monitoring Dashboard Widgets (Codex + MiniMax)
- Security-Audit Fixes (8 Critical+High Findings, #774-#781)
- Provider-Registry Abstraktion für Voice

### Changed

- Installer `18_voice.sh` — interaktive Wahl zwischen Wyoming und MiniMax
