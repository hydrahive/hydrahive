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

