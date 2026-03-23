# OctopOS Changelog

## 2026-03-23

### Fixed

- `fix(console): handle expired my-agent sessions`
- `My Agent` zeigte bei abgelaufenem oder ungültigem JWT nur leere Tabs.
- Die Konsole meldet 401-Antworten jetzt sichtbar als abgelaufene Sitzung und loggt den Nutzer sauber aus.
- Betroffene Bereiche:
  - `console/src/lib/api.ts`
  - `console/src/hooks/useAuth.tsx`
  - `console/src/pages/MyAgentPage.tsx`

### Result

- Einstellungen, Skills und MCP für `Mein Agent` sind bei gültiger Sitzung wieder sichtbar.
- Abgelaufene Browser-Sessions landen nicht mehr stillschweigend auf leeren Seiten.

### Fixed

- `fix(core): improve platform status reporting for WKS and Discord`
- WKS wird im Plattform-Überblick nicht mehr nur über die konfigurierte IP als verbunden markiert.
- Discord-Status behandelt `is_connected` jetzt robust als Property oder Callable.
- Betroffene Bereiche:
  - `core/src/octopos_core/router_user_integrations.py`
  - `core/tests/test_security_regressions.py`

### Result

- Der Plattform-Überblick zeigt Konfiguration und echte Verbindung sauberer getrennt an.
- WKS und Discord liefern keine falschen Positivstatus mehr durch reine Konfigurationswerte.
