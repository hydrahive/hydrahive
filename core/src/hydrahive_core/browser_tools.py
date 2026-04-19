"""
browser_tools.py — Browser-Automatisierung via Playwright (#43)

Stellt folgende Tools bereit:
  browser_navigate    — URL öffnen, Seiteninhalt als Text zurückgeben
  browser_screenshot  — Screenshot als base64-PNG
  browser_click       — Element per CSS-Selektor klicken
  browser_fill        — Eingabefeld befüllen
  browser_evaluate    — JavaScript auf der Seite ausführen
  browser_close       — Browser-Session beenden

Jeder Agent bekommt eine eigene BrowserContext-Instanz (getrennte Cookies/State).
Browser-Sessions werden nach 10 Minuten Inaktivität automatisch geschlossen.

Playwright muss einmalig installiert werden:
    playwright install chromium --with-deps
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import os
import socket
import time
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SSRF-Schutz (#745)
# ---------------------------------------------------------------------------

class UnsafeURLError(ValueError):
    """URL waere Server-Side-Request-Forgery-Vektor (loopback, private Netze,
    Cloud-Metadata, nicht-erlaubte Schemas)."""


_ALLOWED_SCHEMES = frozenset({"http", "https"})


def _is_private_ip(ip: str) -> bool:
    """True wenn IPv4/IPv6-Adresse in einem Bereich ist, den ein Agent nie
    von sich aus ansteuern sollte: loopback, private, link-local,
    Cloud-Metadata (169.254.169.254), reserved, multicast."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        # kein gueltiges IP-Literal → Hostname, Caller macht DNS-Lookup
        return False
    return bool(
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _validate_safe_url(url: str) -> str:
    """Validiert eine URL gegen SSRF. Wirft UnsafeURLError bei Problemen,
    gibt die URL sonst unveraendert zurueck.

    Schutzstufen:
    1. Schema-Allowlist: nur http/https (blockt file://, javascript:, ftp://, data:)
    2. Hostname-Parse: muss vorhanden sein
    3. Loopback-Namen blocken (localhost, localhost.localdomain, broadcasthost)
    4. IP-Literal-Block: loopback/private/link-local/reserved/multicast
    5. DNS-Resolve + Block: Hostname der zu einer privaten IP aufloest
       (schuetzt gegen DNS-Rebinding und interne Hostnames)

    Env-Override HYDRAHIVE_BROWSER_ALLOW_PRIVATE=1 schaltet den Block aus.
    Nur fuer Dev/Debug — in Prod nicht setzen. Schema-Check bleibt aktiv.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"Schema '{scheme}' nicht erlaubt (nur http/https): {url}"
        )
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise UnsafeURLError(f"Hostname fehlt: {url}")

    if os.environ.get("HYDRAHIVE_BROWSER_ALLOW_PRIVATE") == "1":
        logger.warning(
            "browser_navigate: SSRF-Check uebersprungen wegen "
            "HYDRAHIVE_BROWSER_ALLOW_PRIVATE=1 (url=%s)", url
        )
        return url

    if host in {"localhost", "localhost.localdomain", "broadcasthost", "ip6-localhost", "ip6-loopback"}:
        raise UnsafeURLError(f"Loopback-Hostname nicht erlaubt: {host}")

    # IP-Literal direkt im URL (inkl. IPv6 in eckigen Klammern, urllib strippt die schon)
    if _is_private_ip(host):
        raise UnsafeURLError(f"Private/Loopback-IP nicht erlaubt: {host}")

    # Hostname → DNS-Resolve → IPs pruefen (schuetzt vor DNS-Rebinding + internen Namen)
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UnsafeURLError(f"DNS-Resolution fehlgeschlagen ({host}): {e}") from e
    for info in infos:
        sockaddr = info[4]
        resolved_ip = sockaddr[0]
        # IPv6-Scope-ID wegschneiden (z.B. "fe80::1%eth0")
        if "%" in resolved_ip:
            resolved_ip = resolved_ip.split("%", 1)[0]
        if _is_private_ip(resolved_ip):
            raise UnsafeURLError(
                f"Hostname {host!r} loest auf private/loopback-IP {resolved_ip} auf"
            )

    return url

# ---------------------------------------------------------------------------
# Session-Management
# ---------------------------------------------------------------------------

_playwright = None          # playwright-Instanz (lazy)
_browser    = None          # geteilter headless Chromium
_contexts: dict[str, Any] = {}   # agent_id → BrowserContext
_pages:    dict[str, Any] = {}   # agent_id → aktuelle Page
_last_use: dict[str, float] = {} # agent_id → timestamp

_SESSION_TIMEOUT = 600.0  # 10 Minuten Inaktivität → Session schliessen
_INIT_LOCK = asyncio.Lock()


async def _ensure_browser():
    """Stellt sicher dass Playwright + Browser gestartet sind (lazy init)."""
    global _playwright, _browser
    async with _INIT_LOCK:
        if _browser is not None and _browser.is_connected():
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "playwright nicht installiert. "
                "Auf dem Server ausführen: "
                "source /opt/hydrahive/venv/bin/activate && "
                "playwright install chromium --with-deps"
            )
        # PLAYWRIGHT_BROWSERS_PATH setzen falls nicht in Umgebung
        import os
        if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
            for candidate in ["/opt/hydrahive/playwright-browsers", os.path.expanduser("~/.cache/ms-playwright")]:
                if os.path.isdir(candidate):
                    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = candidate
                    logger.info("PLAYWRIGHT_BROWSERS_PATH=%s", candidate)
                    break
        if _playwright is None:
            _playwright = await async_playwright().start()
        _browser = await _playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        logger.info("Playwright Chromium Browser gestartet")


async def _get_page(agent_id: str):
    """Gibt die aktuelle Page für einen Agenten zurück, erstellt falls nötig."""
    await _ensure_browser()
    _last_use[agent_id] = time.monotonic()

    if agent_id not in _contexts:
        ctx = await _browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        _contexts[agent_id] = ctx
        page = await ctx.new_page()
        _pages[agent_id] = page
        logger.info("Browser-Session gestartet für Agent %s", agent_id)

    return _pages[agent_id]


async def close_session(agent_id: str) -> None:
    """Browser-Session für einen Agenten explizit schliessen."""
    if agent_id in _contexts:
        try:
            await _contexts[agent_id].close()
        except Exception:
            pass
        _contexts.pop(agent_id, None)
        _pages.pop(agent_id, None)
        _last_use.pop(agent_id, None)
        logger.info("Browser-Session geschlossen für Agent %s", agent_id)


async def cleanup_idle_sessions() -> int:
    """Schliesst Sessions die länger als SESSION_TIMEOUT inaktiv waren."""
    now = time.monotonic()
    to_close = [
        agent_id for agent_id, last in list(_last_use.items())
        if now - last > _SESSION_TIMEOUT
    ]
    for agent_id in to_close:
        await close_session(agent_id)
    if to_close:
        logger.info("Browser-Cleanup: %d idle Sessions geschlossen", len(to_close))
    return len(to_close)


def _extract_text(html_text: str, max_chars: int = 8000) -> str:
    """Extrahiert lesbaren Text aus einer Seite (kein HTML-Overhead)."""
    import re
    # Script/Style-Blöcke entfernen
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_text, flags=re.DOTALL | re.IGNORECASE)
    # HTML-Tags entfernen
    text = re.sub(r"<[^>]+>", " ", text)
    # HTML-Entities
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    # Mehrfach-Whitespace normalisieren
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


# ---------------------------------------------------------------------------
# Tool-Klassen
# ---------------------------------------------------------------------------

from .tool_registry import BaseTool


class BrowserNavigateTool(BaseTool):
    @property
    def id(self) -> str:
        return "browser_navigate"

    @property
    def name(self) -> str:
        return "Browser Navigate"

    @property
    def description(self) -> str:
        return (
            "Öffnet eine URL im Browser und gibt den Seiteninhalt als Text zurück. "
            "Ideal für Web-Scraping, Formular-Workflows und visuelle Seitenprüfung. "
            "Unterstützt JavaScript-gerenderte Seiten (SPA, React, etc.)."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Die URL die geöffnet werden soll (http/https)",
                },
                "wait_for": {
                    "type": "string",
                    "description": "'load' (Standard), 'domcontentloaded', 'networkidle' oder ein CSS-Selektor der auf das Element wartet",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in Millisekunden (Standard: 30000)",
                },
            },
            "required": ["url"],
        }

    async def execute(
        self,
        agent_id:  str,
        project_id: str,
        url:       str,
        wait_for:  str = "load",
        timeout:   int = 30000,
        **kwargs,
    ) -> dict:
        # #745: SSRF-Validierung vor Page-Load. Wirft UnsafeURLError, die wir in
        # einen sauberen error-dict-Return umwandeln (kein Stacktrace leaken).
        try:
            _validate_safe_url(url)
        except UnsafeURLError as e:
            logger.warning("browser_navigate blockiert (SSRF): agent=%s url=%s reason=%s",
                           agent_id, url, e)
            return {"error": f"URL nicht erlaubt: {e}"}

        try:
            page = await _get_page(agent_id)

            # wait_for ist entweder ein Lifecycle-Event oder ein CSS-Selektor
            lifecycle_events = {"load", "domcontentloaded", "networkidle", "commit"}
            if wait_for in lifecycle_events:
                await page.goto(url, wait_until=wait_for, timeout=timeout)
            else:
                await page.goto(url, wait_until="load", timeout=timeout)
                await page.wait_for_selector(wait_for, timeout=timeout)

            title   = await page.title()
            content = await page.content()
            text    = _extract_text(content)
            current = page.url

            logger.info("browser_navigate: agent=%s url=%s title=%r", agent_id, url, title)
            return {
                "url":   current,
                "title": title,
                "text":  text,
                "length": len(text),
            }
        except Exception as e:
            return {"error": str(e)}


class BrowserScreenshotTool(BaseTool):
    @property
    def id(self) -> str:
        return "browser_screenshot"

    @property
    def name(self) -> str:
        return "Browser Screenshot"

    @property
    def description(self) -> str:
        return (
            "Erstellt einen Screenshot der aktuellen Browser-Seite (oder eines Elements). "
            "Gibt ein base64-kodiertes PNG zurück das direkt mit dem LLM analysiert werden kann."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS-Selektor für Screenshot eines bestimmten Elements (optional, Standard: ganze Seite)",
                },
                "full_page": {
                    "type": "boolean",
                    "description": "Gesamte Seite screenshotten (auch scrollbarer Inhalt) — Standard: false",
                },
            },
            "required": [],
        }

    async def execute(
        self,
        agent_id:  str,
        project_id: str,
        selector:  str = "",
        full_page: bool = False,
        **kwargs,
    ) -> dict:
        try:
            page = await _get_page(agent_id)

            if selector:
                element = await page.query_selector(selector)
                if not element:
                    return {"error": f"Element '{selector}' nicht gefunden"}
                png = await element.screenshot()
            else:
                png = await page.screenshot(full_page=full_page)

            b64 = base64.b64encode(png).decode()
            logger.info("browser_screenshot: agent=%s selector=%r size=%d bytes", agent_id, selector, len(png))
            return {
                "image_base64": b64,
                "format": "png",
                "size_bytes": len(png),
            }
        except Exception as e:
            return {"error": str(e)}


class BrowserClickTool(BaseTool):
    @property
    def id(self) -> str:
        return "browser_click"

    @property
    def name(self) -> str:
        return "Browser Click"

    @property
    def description(self) -> str:
        return (
            "Klickt auf ein Element auf der aktuellen Browser-Seite. "
            "Unterstützt CSS-Selektoren und Text-basierte Suche."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS-Selektor des Elements (z.B. 'button.submit', '#login-btn', 'text=Anmelden')",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in ms bis Element klickbar ist (Standard: 10000)",
                },
            },
            "required": ["selector"],
        }

    async def execute(
        self,
        agent_id:  str,
        project_id: str,
        selector:  str,
        timeout:   int = 10000,
        **kwargs,
    ) -> dict:
        try:
            page = await _get_page(agent_id)
            await page.click(selector, timeout=timeout)
            await page.wait_for_load_state("load", timeout=5000)
            title = await page.title()
            logger.info("browser_click: agent=%s selector=%r", agent_id, selector)
            return {"clicked": True, "selector": selector, "page_title": title}
        except Exception as e:
            return {"error": str(e)}


class BrowserFillTool(BaseTool):
    @property
    def id(self) -> str:
        return "browser_fill"

    @property
    def name(self) -> str:
        return "Browser Fill"

    @property
    def description(self) -> str:
        return (
            "Befüllt ein Eingabefeld (input, textarea) auf der aktuellen Browser-Seite. "
            "Löscht vorherigen Inhalt und tippt den neuen Wert."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS-Selektor des Eingabefelds (z.B. 'input[name=email]', '#password')",
                },
                "value": {
                    "type": "string",
                    "description": "Wert der in das Feld eingetippt werden soll",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in ms bis Element bereit ist (Standard: 10000)",
                },
            },
            "required": ["selector", "value"],
        }

    async def execute(
        self,
        agent_id:  str,
        project_id: str,
        selector:  str,
        value:     str,
        timeout:   int = 10000,
        **kwargs,
    ) -> dict:
        try:
            page = await _get_page(agent_id)
            await page.fill(selector, value, timeout=timeout)
            logger.info("browser_fill: agent=%s selector=%r len=%d", agent_id, selector, len(value))
            return {"filled": True, "selector": selector}
        except Exception as e:
            return {"error": str(e)}


class BrowserEvaluateTool(BaseTool):
    @property
    def id(self) -> str:
        return "browser_evaluate"

    @property
    def name(self) -> str:
        return "Browser Evaluate"

    @property
    def description(self) -> str:
        return (
            "Führt JavaScript auf der aktuellen Browser-Seite aus und gibt das Ergebnis zurück. "
            "Nützlich um Daten aus dem DOM zu extrahieren oder Aktionen auszuführen die keine "
            "direkten Selektoren haben. Nur im execution_mode='unrestricted' verfügbar (#752)."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "script": {
                    "type": "string",
                    "description": "JavaScript-Code der ausgeführt wird. Rückgabewert wird serialisiert.",
                },
            },
            "required": ["script"],
        }

    async def execute(
        self,
        agent_id:  str,
        project_id: str,
        script:    str,
        **kwargs,
    ) -> dict:
        # #752: browser_evaluate führt beliebiges JS im Page-Context aus
        # (Cookie-Exfil, DOM-Manipulation, XHR). Nur unrestricted freischalten.
        if kwargs.get("_execution_mode") != "unrestricted":
            logger.warning(
                "browser_evaluate blockiert: agent=%s execution_mode=%s",
                agent_id, kwargs.get("_execution_mode"),
            )
            return {
                "error": (
                    "browser_evaluate ist auf execution_mode='unrestricted' beschränkt (#752). "
                    "Nutze browser_screenshot/browser_click/browser_fill/browser_navigate für "
                    "Standard-Operationen oder setze execution_mode=unrestricted (Admin-only)."
                ),
            }
        try:
            page = await _get_page(agent_id)
            result = await page.evaluate(script)
            logger.info("browser_evaluate: agent=%s script_len=%d", agent_id, len(script))
            return {"result": result}
        except Exception as e:
            return {"error": str(e)}


class BrowserCloseTool(BaseTool):
    @property
    def id(self) -> str:
        return "browser_close"

    @property
    def name(self) -> str:
        return "Browser Close"

    @property
    def description(self) -> str:
        return (
            "Schliesst die Browser-Session des Agenten (inkl. Cookies, Tabs, State). "
            "Rufe dies am Ende eines Browser-Workflows auf um Ressourcen freizugeben."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    async def execute(self, agent_id: str, project_id: str, **kwargs) -> dict:
        await close_session(agent_id)
        return {"closed": True, "agent_id": agent_id}
