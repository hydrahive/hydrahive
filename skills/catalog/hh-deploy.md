---
skill: hh-deploy
version: 1.0
scope: on-demand
triggers: [deploy, deployen, update.sh, push und deploy, live bringen, auf server, server aktualisieren]
priority: 40
---

HydraHive Deploy-Workflow. Jeder Schritt ist Pflicht — keiner darf übersprungen werden.

## Workflow

1. **Syntax-Check** — vor dem Commit:
   ```bash
   python -m py_compile pfad/zur/datei.py
   ```

2. **Scope prüfen** — nur erwartete Dateien geändert:
   ```bash
   git status
   git diff --stat
   ```

3. **Commit** — präzise Message:
   ```
   fix|feat|refactor: Titel ≤ 70 Zeichen

   Warum, nicht was (was steht im Diff).

   Co-Authored-By: HydraHive Bot <bot@hydrahive.org>
   ```

4. **Push**:
   ```bash
   git push hydrahive main
   ```

5. **Deploy** via update.sh — nie manuell patchen:
   ```bash
   echo 'PASSWORT' | sudo -S bash /opt/hydrahive/update.sh
   ```

6. **Live-Verify**:
   ```bash
   systemctl is-active hydrahive-core.service
   journalctl -u hydrahive-core.service -n 20 --no-pager
   # Endpoint testen
   ```

## Verboten

- Direkte SSH-Edits auf Servern
- `git clean -fd` auf Servern (löscht venv → Server down)
- `git reset --hard` auf Servern
- "Fertig" melden ohne Live-Verify
