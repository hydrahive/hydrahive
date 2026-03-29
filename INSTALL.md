# HydraHive — Installation

> Für die vollständige Deployment-Dokumentation siehe [DEPLOYMENT.md](DEPLOYMENT.md).

---

## Voraussetzungen

- Ubuntu 22.04 oder 24.04 LTS (x86_64)
- Root-Zugang (SSH)
- Min. 4 GB RAM, 20 GB Disk
- Internetzugang (Paketinstallation, Modell-Download)

---

## Schnellinstallation

```bash
# 1. Repo klonen
apt-get install -y git
git clone https://github.com/hydrahive/hydrahive /opt/hydrahive-src
cd /opt/hydrahive-src

# 2. Installer ausführen
sudo bash installer/install.sh
```

Der Installer läuft vollautomatisch (~10–15 Minuten). Am Ende erscheint eine Zusammenfassung mit URL und Admin-Passwort.

**Mit eigenem Domainnamen:**
```bash
sudo DOMAIN=mein.server.de bash installer/install.sh
```

---

## Erster Login

Nach der Installation:

```
URL:      https://<IP-der-VM>
User:     admin
Passwort: siehe Ausgabe des Installers
          oder: grep console_password /etc/hydrahive/admin_credentials
```

> Bei self-signed TLS: Zertifikatswarnung im Browser bestätigen.

---

## LLM-API-Key einrichten

Ohne API-Key kann kein Agent antworten. Unter **Settings → LLM** den gewünschten Provider aktivieren und den Key eintragen — oder direkt:

```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." >> /etc/hydrahive/llm_env
sudo systemctl restart hydrahive-core
```

Unterstützte Provider: **Anthropic Claude**, **OpenAI**, **Google Gemini**, **Mistral**, **Ollama** (lokal).

---

## Erster Agent

Nach dem Login öffnet sich automatisch der persönliche Agent. Beim ersten Chat startet ein kurzes Onboarding — der Agent fragt nach Name und Aufgaben und speichert alles in seinem Memory.

Weitere Agenten unter **Agents → New Agent** anlegen.

---

## Update

```bash
# Vom Entwicklungsrechner (empfohlen)
./scripts/hydrahive-update.sh

# Oder auf der VM direkt
sudo systemctl start hydrahive-selfupdate
```

---

## Backup & Migration

- **Backup:** Settings → Backup → "Backup erstellen"
- **Migration:** Settings → Migration → Export/Import/Transfer
- **Automatisches Backup (Cron):** `./scripts/hydrahive-backup.sh`

---

## Hilfe & Diagnose

```bash
# Service-Status
systemctl is-active hydrahive-core hydrahive-amem nginx

# Health-Check
curl http://127.0.0.1:8765/health

# Logs
journalctl -u hydrahive-core -n 50
```

Ausführliche Troubleshooting-Anleitung: [DEPLOYMENT.md#troubleshooting](DEPLOYMENT.md#7-troubleshooting)
