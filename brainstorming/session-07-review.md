# HydraHive Review — Session 7

> Datum: 20. März 2026  
> Teilnehmer: Till · Claude Web · Local Claude  
> Zweck: Vollständige Bestandsaufnahme, Lückenanalyse, neue Issues

---

## 1. Was steht und funktioniert

### Core (Python/FastAPI)
- Agent-Discovery mit Hot-Reload (watchdog)
- Agent-Lifecycle: Start/Stop/Restart/Heartbeat
- Projekt-Loader mit Hot-Reload
- Session-Manager (Kontext pro Projekt, persistent)
- Orchestrator: Boss-Agent → Worker-Swarm → Antwort
- QMD Skill-Loader (scope: always/on-demand, priority)
- Tool-Registry mit BaseTool Interface + Permission-Check
- Built-in Tools: file_read, file_write, web_search, http_request, dispatch_task, spawn_agent
- Path-Safety: Agenten können nur auf /projects/<id>/ zugreifen
- Matrix-Integration: Agenten als Bots in Matrix-Rooms
- Provisioner: Linux-User, Samba-Share, Matrix-Room automatisch anlegen
- LLM-Adapter: litellm (Ollama/OpenAI) + direktes Anthropic SDK (OAuth-Token)
- User-Verwaltung: users.json + Matrix-Account-Registrierung
- JWT-Auth für Console

### Installer (Bash/Systemd)
- 8 Module, vollständig idempotent
- OS-Check (Debian 12, Ubuntu 22.04/24.04)
- GPU-Erkennung → Full/Lite Profil
- Conduwuit Matrix-Homeserver
- Systemd-Services: core, conduwuit, console (nginx)
- Ollama + Modelle (Full only)
- Setup-Wizard beim ersten Start

### Console (React/TypeScript)
- Login + Setup-Wizard (Ersteinrichtung)
- Dashboard (Live-Daten)
- Agenten: Liste, anlegen, bearbeiten, deaktivieren
- Projekte: Liste, anlegen (mit Provisioning)
- Chat: HTTP-Chat mit Boss-Agent, show_swarm Toggle
- System: Service-Status, Agent-Laufzeit (15s Auto-Refresh)
- Tools: Übersicht mit Parameter-Detail
- LLM-Config: Claude Max OAuth, OpenAI, Ollama
- Benutzer: Liste, anlegen, Passwort ändern, löschen

---

## 2. Bekannte Lücken (noch keine Issues)

### Sicherheit
- [ ] HTTPS fehlt — nginx läuft nur auf HTTP:80
- [ ] JWT Secret wird zufällig generiert, nach Core-Neustart alle Sessions ungültig
- [ ] Keine Rate-Limiting auf /auth/login
- [ ] Agenten können via http_request-Tool interne Services erreichen (SSRF)
- [ ] Keine Audit-Logs: wer hat wann was geändert?

### Stabilität / Betrieb
- [ ] Update-Mechanismus fehlt — wie bekommt HydraHive neue Versionen?
- [ ] Backup/Restore fehlt — /agents/, /projects/, /etc/hydrahive/ sichern
- [ ] Kein Health-Dashboard für Matrix-Rooms (sind Agenten wirklich online?)
- [ ] Core-Neustart verliert laufende Task-Agenten ohne Cleanup
- [ ] Keine Limits: ein Agent kann alle VRAM-Ressourcen blockieren

### Agent-Funktionalität
- [ ] AgentLink State-Transfer (#13, #53) — Handoffs zwischen Agenten
- [ ] Worker-Agenten haben keine eigene Matrix-Identität (nur Boss ist Bot)
- [ ] Kein Agent-Debugging: was hat der Agent gedacht? Kein Reasoning-Log
- [ ] QMD-Skills können nicht über Console verwaltet werden (nur SSH/vim)
- [ ] Soul.md nur als Textarea im Agenten-Formular — kein Syntax-Highlighting
- [ ] Kein Agent-Import/Export (Portable Agent Packages)
- [ ] Kein Agent-Cloning (Vorlage → neuer Agent)
- [ ] Task-Agent TTL nicht konfigurierbar per Projekt

### Projekte
- [ ] Projekt löschen fehlt in Console (nur Deprovision via API)
- [ ] Projekt-Einstellungen bearbeiten fehlt (show_swarm, Agenten neu zuweisen)
- [ ] Keine Projekt-Templates (vordefinierte Agenten-Kombinationen)
- [ ] Samba-Passwort für Projekt-User nicht über Console setzbar
- [ ] Keine Datei-Übersicht: welche Dateien liegen im Projekt-Ordner?

### Console / UX
- [ ] Dunkelmodus fehlt (Tailwind dark: Klasse ist vorbereitet, aber kein Toggle)
- [ ] Mobile-Ansicht nicht optimiert
- [ ] Keine Notifications/Toasts bei Erfolg/Fehler (nur inline Fehlermeldungen)
- [ ] Chat-History wird nicht angezeigt — nur neue Nachrichten nach Login
- [ ] Keine Markdown-Rendering in Chat-Antworten
- [ ] Kein Streaming — Antwort erscheint komplett nach LLM-Fertigstellung
- [ ] Agenten-Logs nicht einsehbar in Console (nur via journalctl)

### LLM / Modelle
- [ ] Claude OAuth-Token läuft ab — kein automatisches Renewal, kein Hinweis
- [ ] Kein Modell-Benchmarking: welches Modell ist schnell/gut für welchen Task?
- [ ] Ollama-Modelle können nicht gelöscht werden über Console
- [ ] Kein Fallback-Chain: wenn Ollama offline, automatisch zu Cloud wechseln?

### Fehlend vs OpenClaw
- [ ] Plugin-System (OpenClaw hat erweiterbare Provider-Plugins)
- [ ] Multi-Account Auth (OpenClaw rotiert zwischen mehreren Tokens)
- [ ] TTS/Voice-Output
- [ ] Web-Search als eigener konfigurierbarer Service (Perplexity, Brave, etc.)
- [ ] Channels: Pro Kanal andere Agenten-Konfiguration (OpenClaw Channels-Konzept)
- [ ] Pairing-Code für Mobile-Zugriff
- [ ] WhatsApp/Telegram/Discord Bridge
- [ ] Canvas/Artifact-Rendering in Chat
- [ ] Cron-Jobs: Agenten periodisch starten ohne User-Input
- [ ] Memory-System: persistentes Wissen über Sessions hinaus (nicht nur QMD)

---

## 3. Ideen die noch einfallen (Brainstorming)

### Produkt-Vision
- [ ] HydraHive als Proxmox-App (One-Click-Install)
- [ ] HydraHive als TrueNAS-App (du kennst das System)
- [ ] HydraHive Community Edition (kostenlos) vs Pro Edition (Support, mehr Features)
- [ ] Agent-Marketplace: vorgefertigte Agenten herunterladen

### Technisch interessant
- [ ] Agenten können andere Agenten anlegen (Meta-Agent)
- [ ] Projekt-Gruppen: mehrere Projekte mit geteilten Agenten
- [ ] Agenten-Kommunikation via AgentLink visualisieren (Graph-Ansicht)
- [ ] GPU-Monitoring in System-Screen (VRAM-Auslastung, Temperatur)
- [ ] Automatischer Modell-Wechsel bei VRAM-Engpass

---

## 4. Vorgeschlagene neue Issues

> Diese werden nach dem Review-Gespräch zu dritt priorisiert und angelegt.

### Prio 1 — Kritisch für Produkt
- HTTPS/TLS für nginx
- JWT Secret persistent (nicht bei jedem Core-Neustart neu)
- Update-Mechanismus (git pull + pip install + npm build)
- Backup-Script für /agents/, /projects/, /etc/hydrahive/
- Streaming in Chat (Server-Sent Events)
- Markdown-Rendering in Chat

### Prio 2 — Wichtig für Alltag
- Chat-History beim Login laden
- Projekt bearbeiten (Agenten neu zuweisen)
- Projekt löschen in Console
- QMD-Skills über Console verwalten
- Dunkelmodus Toggle
- Toast-Notifications
- Agent-Reasoning-Log (was hat der Agent gedacht?)

### Prio 3 — Nice-to-have
- AgentLink State-Transfer (#13, #53) fertigstellen
- GPU-Monitoring in System-Screen
- Cron-Jobs für Agenten
- Agent-Import/Export
- Dark Mode

---

## 5. Offene Fragen für das Review-Gespräch

1. Soll HydraHive eine eigene Domain bekommen (hydrahive.io)?
2. Wann ist der richtige Zeitpunkt für Session 7 (Monetarisierung)?
3. Welche OpenClaw-Features sind wirklich relevant für HydraHive-Zielgruppe?
4. Plugin-System: lohnt sich das vor dem ersten Release?
5. AgentLink: vor oder nach dem ersten öffentlichen Release?

---

*Dieses Dokument wird im Review-Gespräch zu dritt ausgefüllt und ergänzt.*
