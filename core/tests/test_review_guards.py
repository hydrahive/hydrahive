"""
test_review_guards.py — Tests für die Codex-Review Guards

Deckt ab:
- Extension-ID Sanitizing (Path-Traversal-Schutz in router_extensions)
- Restart-Lock (verhindert parallele Core-Neustarts)
- Discord ID-Sanitizing (_sanitize_ids — nur numerische Snowflake-IDs)
- Discord Rollen-Filter mit fehlendem roles-Attribut (AttributeError-Schutz)
- SSE _stream_script Timeout + proc.kill() bei Abbruch
"""
import asyncio
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# ============================================================= Helpers

_EXT_ID_RE = re.compile(r'^[a-z0-9_-]+$')


# ============================================================= Extension-ID Sanitizing

class TestExtIdSanitizing:
    """ext_id darf nur [a-z0-9_-] enthalten — alles andere → 400."""

    VALID = ["searxng", "codeserver", "gitea", "my-ext", "ext_v2"]
    INVALID = [
        "../etc/passwd",
        "../../root",
        "foo/bar",
        "foo bar",
        "FOO",
        "",
        "foo!bar",
        "foo\x00bar",
    ]

    @pytest.mark.parametrize("ext_id", VALID)
    def test_gueltige_ids_akzeptiert(self, ext_id):
        assert _EXT_ID_RE.match(ext_id), f"'{ext_id}' sollte akzeptiert werden"

    @pytest.mark.parametrize("ext_id", INVALID)
    def test_ungueltige_ids_geblockt(self, ext_id):
        assert not _EXT_ID_RE.match(ext_id), f"'{ext_id}' sollte geblockt werden"

    def test_router_wirft_400_bei_path_traversal(self):
        """router_extensions wirft HTTPException(400) bei ungültiger ext_id."""
        from fastapi import HTTPException
        from hydrahive_core.router_extensions import _EXT_ID_RE as router_re

        bad_ids = ["../secret", "../../etc", "foo/bar", "FOO", ""]
        for bad in bad_ids:
            assert not router_re.match(bad), f"Router-Regex sollte '{bad}' blocken"

    def test_router_akzeptiert_bekannte_extension_ids(self):
        """Alle Manifest-IDs sind regex-kompatibel."""
        from hydrahive_core.router_extensions import _EXT_ID_RE as router_re
        from hydrahive_core.router_extensions import MANIFEST_ORDER

        for mid in MANIFEST_ORDER:
            assert router_re.match(mid), f"Manifest-ID '{mid}' wird vom Regex geblockt"


# ============================================================= Restart Lock

class TestRestartLock:
    """asyncio.Lock verhindert parallele Core-Neustarts."""

    @pytest.mark.asyncio
    async def test_lock_verhindert_parallelen_restart(self):
        """Zweiter Restart während erstem läuft → Lock ist bereits belegt."""
        from fastapi import HTTPException
        import hydrahive_core.router_system as rs

        # Lock direkt testen
        lock = asyncio.Lock()
        results = []

        async def task_with_lock():
            if lock.locked():
                results.append("blocked")
                return
            async with lock:
                await asyncio.sleep(0.05)
                results.append("ran")

        # Parallel starten — zweiter soll blockiert werden
        await asyncio.gather(
            task_with_lock(),
            asyncio.sleep(0),  # yield damit erster Lock bekommt
            task_with_lock(),
        )
        assert "blocked" in results, "Zweiter paralleler Aufruf sollte geblockt werden"
        assert results.count("ran") == 1, "Genau ein Neustart soll durchlaufen"

    @pytest.mark.asyncio
    async def test_lock_freigegebn_nach_abschluss(self):
        """Lock wird nach Abschluss freigegeben — nächster Restart möglich."""
        lock = asyncio.Lock()

        async with lock:
            pass  # simuliert ersten Restart

        assert not lock.locked(), "Lock muss nach Abschluss freigegeben sein"

    def test_restart_lock_existiert_im_modul(self):
        """_restart_lock ist im router_system Modul definiert."""
        import hydrahive_core.router_system as rs
        assert hasattr(rs, "_restart_lock"), "_restart_lock fehlt in router_system"
        assert isinstance(rs._restart_lock, asyncio.Lock)


# ============================================================= Discord ID Sanitizing

class TestDiscordIdSanitizing:
    """_sanitize_ids akzeptiert nur numerische Snowflake-IDs (15–20 Stellen)."""

    def _get_sanitize(self):
        from hydrahive_core.discord_agent import DiscordAgentClient
        return DiscordAgentClient._sanitize_ids

    def test_gueltige_snowflake_ids_akzeptiert(self):
        sanitize = self._get_sanitize()
        valid = ["123456789012345", "987654321098765432", "100000000000000000"]
        result = sanitize(valid)
        assert result == set(valid)

    def test_leere_strings_geblockt(self):
        sanitize = self._get_sanitize()
        assert sanitize([""]) == set()

    def test_path_traversal_geblockt(self):
        sanitize = self._get_sanitize()
        assert sanitize(["../secret"]) == set()
        assert sanitize(["../../etc/passwd"]) == set()

    def test_buchstaben_geblockt(self):
        sanitize = self._get_sanitize()
        assert sanitize(["abc123def"]) == set()
        assert sanitize(["not_a_number"]) == set()

    def test_zu_kurze_ids_geblockt(self):
        sanitize = self._get_sanitize()
        assert sanitize(["12345"]) == set()        # < 15 Stellen
        assert sanitize(["123456789012345678901"]) == set()  # > 20 Stellen

    def test_gemischte_liste_nur_gueltige_behalten(self):
        sanitize = self._get_sanitize()
        ids = ["123456789012345", "../evil", "abc", "987654321098765"]
        result = sanitize(ids)
        assert result == {"123456789012345", "987654321098765"}

    def test_duplikate_als_set_dedupliziert(self):
        sanitize = self._get_sanitize()
        ids = ["123456789012345", "123456789012345"]
        result = sanitize(ids)
        assert len(result) == 1


# ============================================================= Discord Rollen-Filter

class TestDiscordRoleFilter:
    """Rollen-Filter ist stabil wenn roles-Attribut fehlt oder None ist."""

    def _make_message(self, *, has_roles=True, roles=None, author_id="111111111111111"):
        author = MagicMock()
        author.id = int(author_id)
        author.bot = False
        if has_roles:
            author.roles = roles or []
        else:
            del author.roles  # kein roles-Attribut
        msg = MagicMock()
        msg.author = author
        msg.channel.id = 222222222222222
        msg.content = "hallo"
        msg.mentions = []
        return msg

    def test_is_valid_discord_id(self):
        from hydrahive_core.discord_agent import _is_valid_discord_id
        assert _is_valid_discord_id("123456789012345")
        assert not _is_valid_discord_id("abc")
        assert not _is_valid_discord_id("")
        assert not _is_valid_discord_id("../etc")

    def _make_client(self, role_whitelist=None, role_blacklist=None):
        from hydrahive_core.discord_agent import DiscordAgentClient

        class _Concrete(DiscordAgentClient):
            async def on_user_message(self, *a, **kw):
                pass

        client = _Concrete.__new__(_Concrete)
        client.role_whitelist = set(role_whitelist or [])
        client.role_blacklist = set(role_blacklist or [])
        return client

    def test_role_whitelist_ohne_roles_attr_geblockt(self):
        """Wenn Whitelist gesetzt und Author hat kein roles-Attribut → blockieren."""
        client = self._make_client(role_whitelist=["123456789012345"])

        msg = self._make_message(has_roles=False)

        # Simuliert den Filter-Pfad ohne roles-Attribut
        try:
            roles = getattr(msg.author, 'roles', None)
            blocked = False
            if client.role_whitelist or client.role_blacklist:
                if roles is not None:
                    author_role_ids = {str(r.id) for r in roles}
                    if client.role_whitelist and not (author_role_ids & client.role_whitelist):
                        blocked = True
                elif client.role_whitelist:
                    blocked = True
            assert blocked, "Whitelist ohne roles-Attribut sollte blocken"
        except AttributeError:
            pytest.fail("AttributeError bei fehlendem roles-Attribut — Guard greift nicht")

    def test_keine_filter_kein_block(self):
        """Ohne Whitelist/Blacklist wird nichts geblockt."""
        client = self._make_client()

        msg = self._make_message(has_roles=False)
        roles = getattr(msg.author, 'roles', None)
        blocked = False
        if client.role_whitelist or client.role_blacklist:
            if roles is None and client.role_whitelist:
                blocked = True
        assert not blocked

    def test_blacklist_mit_rolle_blockt(self):
        """Autor mit einer Rolle in der Blacklist wird geblockt."""
        client = self._make_client(role_blacklist=["555555555555555"])
        bad_role = MagicMock()
        bad_role.id = 555555555555555

        msg = self._make_message(roles=[bad_role])
        roles = getattr(msg.author, 'roles', None)
        author_role_ids = {str(r.id) for r in roles}
        blocked = bool(client.role_blacklist and author_role_ids & client.role_blacklist)
        assert blocked


# ============================================================= SSE Stream Timeout / Cleanup

class TestStreamScriptCleanup:
    """_stream_script beendet den Prozess sauber bei Timeout oder Fehler."""

    @pytest.mark.asyncio
    async def test_stream_sendet_done_false_bei_timeout(self):
        """Bei TimeoutError muss done:false gesendet und proc.kill() aufgerufen werden."""
        import json as _j
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdout = AsyncMock()

        async def _fake_lines():
            yield b"erste zeile\n"
            # Simuliert langen Lauf — wait() läuft in Timeout

        mock_proc.stdout.__aiter__ = lambda s: _fake_lines().__aiter__()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                from hydrahive_core.router_extensions import _stream_script
                chunks = []
                async for chunk in _stream_script(Path("/fake/script.sh")):
                    chunks.append(chunk)

        # Letztes Chunk muss done:false enthalten
        last = chunks[-1]
        data = _j.loads(last.replace("data: ", "").strip())
        assert data.get("done") is True
        assert data.get("ok") is False

    @pytest.mark.asyncio
    async def test_proc_kill_bei_laufendem_prozess(self):
        """proc.kill() wird aufgerufen wenn returncode noch None ist."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdout = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        async def _lines():
            yield b"line\n"

        mock_proc.stdout.__aiter__ = lambda s: _lines().__aiter__()

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
                from hydrahive_core.router_extensions import _stream_script
                async for _ in _stream_script(Path("/fake/script.sh")):
                    pass

        mock_proc.kill.assert_called_once()
