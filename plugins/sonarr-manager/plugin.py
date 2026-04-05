"""
sonarr-manager Plugin — Sonarr v3 API.

Tools:
  - sonarr_series:     Alle Serien in der Bibliothek auflisten
  - sonarr_search:     Nach neuen Serien suchen (TVDB-Lookup via Sonarr)
  - sonarr_add_series: Serie zur Bibliothek hinzufügen
  - sonarr_queue:      Aktuelle Download-Queue anzeigen
"""
import json
import urllib.request
import urllib.error
import urllib.parse


def _load_config(username: str, plugin_id: str = "sonarr-manager") -> dict:
    path = f"/etc/hydrahive/user_app_config/{username}/{plugin_id}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _api(base_url: str, api_key: str, path: str, method: str = "GET", body: dict = None) -> dict | list:
    url = base_url.rstrip("/") + "/api/v3" + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Api-Key", api_key)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def register(api):

    @api.tool(
        tool_id="sonarr_series",
        description="Listet alle Serien in der Sonarr-Bibliothek auf mit Status, Episoden-Statistiken und Monitoring.",
        parameters={
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Optionaler Suchbegriff zum Filtern nach Titel"},
                "missing_only": {"type": "boolean", "description": "Nur Serien mit fehlenden Episoden anzeigen"},
                "base_url": {"type": "string", "description": "Sonarr URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Sonarr API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def sonarr_series(filter: str = "", missing_only: bool = False, base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            series = _api(base_url, api_key, "/series")
            if filter:
                fl = filter.lower()
                series = [s for s in series if fl in s.get("title", "").lower()]
            if missing_only:
                series = [s for s in series if s.get("statistics", {}).get("episodeFileCount", 0) < s.get("statistics", {}).get("episodeCount", 0)]
            series.sort(key=lambda s: s.get("title", ""))
            lines = [f"**Sonarr Bibliothek ({len(series)} Serien):**", ""]
            for s in series[:50]:
                title = s.get("title", "?")
                year = s.get("year", "?")
                status = s.get("status", "?")
                stats = s.get("statistics", {})
                ep_total = stats.get("episodeCount", 0)
                ep_have = stats.get("episodeFileCount", 0)
                seasons = stats.get("seasonCount", 0)
                monitored = "📺" if s.get("monitored") else "  "
                lines.append(f"  {monitored} **{title}** ({year}) — {ep_have}/{ep_total} Eps, {seasons} Staffeln [{status}]")
            if len(series) > 50:
                lines.append(f"  ... und {len(series) - 50} weitere")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="sonarr_search",
        description="Sucht nach Serien via Sonarr (TVDB-Lookup). Gibt Ergebnisse mit TVDB-ID zum Hinzufügen zurück.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchbegriff (Serientitel)"},
                "base_url": {"type": "string", "description": "Sonarr URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Sonarr API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["query"],
        },
    )
    def sonarr_search(query: str, base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            params = urllib.parse.urlencode({"term": query})
            results = _api(base_url, api_key, f"/series/lookup?{params}")
            if not results:
                return f"Keine Ergebnisse für '{query}'"
            lines = [f"**Sonarr Suche: '{query}' ({len(results[:10])} Ergebnisse):**", ""]
            for s in results[:10]:
                title = s.get("title", "?")
                year = s.get("year", "?")
                tvdb_id = s.get("tvdbId", "?")
                overview = s.get("overview", "")[:80]
                seasons = s.get("seasons", [])
                in_library = " [IN BIBLIOTHEK]" if s.get("id") else ""
                lines.append(f"  **{title}** ({year}) — TVDB: {tvdb_id}, {len(seasons)} Staffeln{in_library}")
                if overview:
                    lines.append(f"    {overview}...")
                lines.append("")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="sonarr_add_series",
        description="Fügt eine Serie zur Sonarr-Bibliothek hinzu. TVDB-ID aus sonarr_search verwenden.",
        parameters={
            "type": "object",
            "properties": {
                "tvdb_id": {"type": "integer", "description": "TVDB-ID der Serie (aus sonarr_search)"},
                "quality_profile_id": {"type": "integer", "description": "Quality-Profil-ID (default: 1)"},
                "root_folder": {"type": "string", "description": "Root-Folder-Pfad (default: erster verfügbarer)"},
                "monitored": {"type": "boolean", "description": "Serie überwachen (default: true)"},
                "season_folder": {"type": "boolean", "description": "Staffel-Unterordner anlegen (default: true)"},
                "search_now": {"type": "boolean", "description": "Sofort nach fehlenden Episoden suchen (default: false)"},
                "base_url": {"type": "string", "description": "Sonarr URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Sonarr API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["tvdb_id"],
        },
    )
    def sonarr_add_series(tvdb_id: int, quality_profile_id: int = 1, root_folder: str = "", monitored: bool = True, season_folder: bool = True, search_now: bool = False, base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            # Lookup serie details
            results = _api(base_url, api_key, f"/series/lookup?term=tvdb:{tvdb_id}")
            if not results:
                return f"Serie mit TVDB-ID {tvdb_id} nicht gefunden"
            serie = results[0]
            # Get root folder if not provided
            if not root_folder:
                folders = _api(base_url, api_key, "/rootfolder")
                if not folders:
                    return "Fehler: Kein Root-Folder in Sonarr konfiguriert"
                root_folder = folders[0]["path"]
            payload = {
                "tvdbId": tvdb_id,
                "title": serie.get("title", ""),
                "qualityProfileId": quality_profile_id,
                "rootFolderPath": root_folder,
                "monitored": monitored,
                "seasonFolder": season_folder,
                "addOptions": {"searchForMissingEpisodes": search_now, "monitor": "all"},
                "seasons": serie.get("seasons", []),
            }
            for key in ("titleSlug", "images", "year"):
                if key in serie:
                    payload[key] = serie[key]
            result = _api(base_url, api_key, "/series", method="POST", body=payload)
            title = result.get("title", "?")
            year = result.get("year", "?")
            serie_id = result.get("id", "?")
            seasons = len(result.get("seasons", []))
            return f"Serie hinzugefügt: **{title}** ({year}), {seasons} Staffeln, Sonarr-ID: {serie_id}\nMonitoring: {monitored}, Sofortsuche: {search_now}"
        except urllib.error.HTTPError as e:
            body = e.read().decode() if hasattr(e, "read") else ""
            return f"HTTP Fehler {e.code}: {e.reason}\n{body[:200]}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="sonarr_queue",
        description="Zeigt die aktuelle Sonarr Download-Queue mit Serien, Episoden, Status und Fortschritt.",
        parameters={
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "description": "Sonarr URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Sonarr API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def sonarr_queue(base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            data = _api(base_url, api_key, "/queue?pageSize=50&includeSeries=true&includeEpisode=true")
            records = data.get("records", []) if isinstance(data, dict) else data
            if not records:
                return "Sonarr Queue ist leer"
            lines = [f"**Sonarr Queue ({len(records)} Einträge):**", ""]
            for item in records:
                series_title = item.get("series", {}).get("title", "?")
                ep = item.get("episode", {})
                ep_str = f"S{ep.get('seasonNumber', '?'):02d}E{ep.get('episodeNumber', '?'):02d}" if ep else ""
                ep_title = ep.get("title", "")
                status = item.get("status", "?")
                size_left = item.get("sizeleft", 0)
                size_total = item.get("size", 0)
                timeleft = item.get("timeleft", "")
                pct = (1 - size_left / size_total) * 100 if size_total > 0 else 0
                size_mb = size_total / 1024 / 1024
                timeleft_str = f" — ETA: {timeleft}" if timeleft else ""
                lines.append(f"  **{series_title}** {ep_str} {ep_title}")
                lines.append(f"    Status: {status} | {pct:.1f}% von {size_mb:.0f} MB{timeleft_str}")
                if item.get("errorMessage"):
                    lines.append(f"    ⚠ {item['errorMessage']}")
                lines.append("")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"
