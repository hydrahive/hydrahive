"""
radarr-manager Plugin — Radarr v3 API.

Tools:
  - radarr_movies:     Alle Filme in der Bibliothek auflisten
  - radarr_search:     Nach neuen Filmen suchen (TMDB-Lookup via Radarr)
  - radarr_add_movie:  Film zur Bibliothek hinzufügen
  - radarr_queue:      Aktuelle Download-Queue anzeigen
"""
import json
import urllib.request
import urllib.error
import urllib.parse


def _load_config(username: str, plugin_id: str = "radarr-manager") -> dict:
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
        tool_id="radarr_movies",
        description="Listet alle Filme in der Radarr-Bibliothek auf mit Status, Jahr und Qualitätsprofil.",
        parameters={
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Optionaler Suchbegriff zum Filtern nach Titel"},
                "missing_only": {"type": "boolean", "description": "Nur fehlende (noch nicht heruntergeladene) Filme anzeigen"},
                "base_url": {"type": "string", "description": "Radarr URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Radarr API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def radarr_movies(filter: str = "", missing_only: bool = False, base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            movies = _api(base_url, api_key, "/movie")
            if missing_only:
                movies = [m for m in movies if not m.get("hasFile", False)]
            if filter:
                fl = filter.lower()
                movies = [m for m in movies if fl in m.get("title", "").lower()]
            movies.sort(key=lambda m: m.get("title", ""))
            lines = [f"**Radarr Bibliothek ({len(movies)} Filme):**", ""]
            for m in movies[:50]:
                title = m.get("title", "?")
                year = m.get("year", "?")
                has_file = "✓" if m.get("hasFile") else "✗"
                status = m.get("status", "?")
                monitored = "📺" if m.get("monitored") else "  "
                lines.append(f"  {has_file} {monitored} {title} ({year})  [{status}]")
            if len(movies) > 50:
                lines.append(f"  ... und {len(movies) - 50} weitere")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="radarr_search",
        description="Sucht nach Filmen via Radarr (TMDB-Lookup). Gibt Ergebnisse mit TMDB-ID zum Hinzufügen zurück.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchbegriff (Filmtitel)"},
                "base_url": {"type": "string", "description": "Radarr URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Radarr API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["query"],
        },
    )
    def radarr_search(query: str, base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            params = urllib.parse.urlencode({"term": query})
            results = _api(base_url, api_key, f"/movie/lookup?{params}")
            if not results:
                return f"Keine Ergebnisse für '{query}'"
            lines = [f"**Radarr Suche: '{query}' ({len(results[:10])} Ergebnisse):**", ""]
            for m in results[:10]:
                title = m.get("title", "?")
                year = m.get("year", "?")
                tmdb_id = m.get("tmdbId", "?")
                overview = m.get("overview", "")[:80]
                in_library = " [IN BIBLIOTHEK]" if m.get("id") else ""
                lines.append(f"  **{title}** ({year}) — TMDB: {tmdb_id}{in_library}")
                if overview:
                    lines.append(f"    {overview}...")
                lines.append("")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="radarr_add_movie",
        description="Fügt einen Film zur Radarr-Bibliothek hinzu. TMDB-ID aus radarr_search verwenden.",
        parameters={
            "type": "object",
            "properties": {
                "tmdb_id": {"type": "integer", "description": "TMDB-ID des Films (aus radarr_search)"},
                "quality_profile_id": {"type": "integer", "description": "Quality-Profil-ID (default: 1)"},
                "root_folder": {"type": "string", "description": "Root-Folder-Pfad (default: erster verfügbarer)"},
                "monitored": {"type": "boolean", "description": "Film überwachen/herunterladen (default: true)"},
                "search_now": {"type": "boolean", "description": "Sofort nach dem Film suchen (default: true)"},
                "base_url": {"type": "string", "description": "Radarr URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Radarr API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["tmdb_id"],
        },
    )
    def radarr_add_movie(tmdb_id: int, quality_profile_id: int = 1, root_folder: str = "", monitored: bool = True, search_now: bool = True, base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            # Lookup movie details first
            results = _api(base_url, api_key, f"/movie/lookup/tmdb?tmdbId={tmdb_id}")
            if not results:
                return f"Film mit TMDB-ID {tmdb_id} nicht gefunden"
            movie = results if isinstance(results, dict) else results[0]
            # Get root folder if not provided
            if not root_folder:
                folders = _api(base_url, api_key, "/rootfolder")
                if not folders:
                    return "Fehler: Kein Root-Folder in Radarr konfiguriert"
                root_folder = folders[0]["path"]
            payload = {
                "tmdbId": tmdb_id,
                "title": movie.get("title", ""),
                "year": movie.get("year", 0),
                "qualityProfileId": quality_profile_id,
                "rootFolderPath": root_folder,
                "monitored": monitored,
                "addOptions": {"searchForMovie": search_now},
            }
            # Copy required fields from lookup
            for key in ("titleSlug", "images", "ratings", "genres"):
                if key in movie:
                    payload[key] = movie[key]
            result = _api(base_url, api_key, "/movie", method="POST", body=payload)
            title = result.get("title", "?")
            year = result.get("year", "?")
            movie_id = result.get("id", "?")
            return f"Film hinzugefügt: **{title}** ({year}), Radarr-ID: {movie_id}\nMonitoring: {monitored}, Sofortsuche: {search_now}"
        except urllib.error.HTTPError as e:
            body = e.read().decode() if hasattr(e, "read") else ""
            return f"HTTP Fehler {e.code}: {e.reason}\n{body[:200]}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="radarr_queue",
        description="Zeigt die aktuelle Radarr Download-Queue mit Status, Fortschritt und geschätzter Restzeit.",
        parameters={
            "type": "object",
            "properties": {
                "base_url": {"type": "string", "description": "Radarr URL (optional, sonst aus Config)"},
                "api_key": {"type": "string", "description": "Radarr API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def radarr_queue(base_url: str = "", api_key: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        base_url = base_url or cfg.get("base_url", "")
        api_key = api_key or cfg.get("api_key", "")
        if not base_url or not api_key:
            return "Fehler: base_url und api_key benötigt"
        try:
            data = _api(base_url, api_key, "/queue?pageSize=50&includeMovie=true")
            records = data.get("records", []) if isinstance(data, dict) else data
            if not records:
                return "Radarr Queue ist leer"
            lines = [f"**Radarr Queue ({len(records)} Einträge):**", ""]
            for item in records:
                title = item.get("movie", {}).get("title", item.get("title", "?"))
                year = item.get("movie", {}).get("year", "")
                status = item.get("status", "?")
                size_left = item.get("sizeleft", 0)
                size_total = item.get("size", 0)
                timeleft = item.get("timeleft", "")
                pct = (1 - size_left / size_total) * 100 if size_total > 0 else 0
                size_mb = size_total / 1024 / 1024
                year_str = f" ({year})" if year else ""
                timeleft_str = f" — ETA: {timeleft}" if timeleft else ""
                lines.append(f"  **{title}{year_str}**")
                lines.append(f"    Status: {status} | {pct:.1f}% von {size_mb:.0f} MB{timeleft_str}")
                if item.get("errorMessage"):
                    lines.append(f"    ⚠ {item['errorMessage']}")
                lines.append("")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            return f"HTTP Fehler {e.code}: {e.reason}"
        except Exception as e:
            return f"Fehler: {e}"
