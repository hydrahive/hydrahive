"""
test_cache_control.py — Tests für _apply_cache_control() in orchestrator_llm.py
"""
import pytest
from hydrahive_core.orchestrator_llm import _apply_cache_control


def _sys(text):
    return {"role": "system", "content": text}

def _user(text):
    return {"role": "user", "content": text}

def _asst(text):
    return {"role": "assistant", "content": text}

def _has_cache(msg) -> bool:
    """True wenn mindestens ein Content-Block cache_control hat."""
    c = msg.get("content", "")
    if isinstance(c, list):
        return any(b.get("cache_control") for b in c)
    return False


class TestNichtAnthropic:
    def test_messages_unveraendert(self):
        msgs = [_sys("system"), _user("hallo"), _asst("hi")]
        result = _apply_cache_control(msgs, is_anthropic=False)
        assert result == msgs

    def test_leere_liste(self):
        assert _apply_cache_control([], is_anthropic=False) == []


class TestSystemMessage:
    def test_system_string_bekommt_cache_control(self):
        msgs = [_sys("mein system prompt")]
        result = _apply_cache_control(msgs, is_anthropic=True)
        assert len(result) == 1
        assert _has_cache(result[0])

    def test_system_string_wird_zu_content_liste(self):
        msgs = [_sys("prompt")]
        result = _apply_cache_control(msgs, is_anthropic=True)
        assert isinstance(result[0]["content"], list)
        assert result[0]["content"][0]["text"] == "prompt"

    def test_system_liste_letzter_block_bekommt_cache(self):
        content = [
            {"type": "text", "text": "block1"},
            {"type": "text", "text": "block2"},
        ]
        msgs = [{"role": "system", "content": content}]
        result = _apply_cache_control(msgs, is_anthropic=True)
        last = result[0]["content"][-1]
        assert last.get("cache_control") == {"type": "ephemeral"}

    def test_system_liste_erster_block_kein_cache(self):
        content = [
            {"type": "text", "text": "block1"},
            {"type": "text", "text": "block2"},
        ]
        msgs = [{"role": "system", "content": content}]
        result = _apply_cache_control(msgs, is_anthropic=True)
        assert not result[0]["content"][0].get("cache_control")

    def test_system_leerer_content_bleibt_unveraendert(self):
        # Leerer String: kein Cache-Tag, content bleibt wie es ist
        msgs = [{"role": "system", "content": ""}]
        result = _apply_cache_control(msgs, is_anthropic=True)
        # _tag_cache gibt msg unverändert zurück bei leerem content
        assert not _has_cache(result[0])


class TestHistoryCache:
    def test_strategische_breakpoints_bei_wenigen_msgs(self):
        # 4 User-Msgs: erste bekommt Cache (Breakpoint 2: erste User-Message)
        msgs = [_sys("s")] + [_user(f"u{i}") for i in range(4)]
        result = _apply_cache_control(msgs, is_anthropic=True)
        non_sys = [m for m in result if m.get("role") != "system"]
        cached = [m for m in non_sys if _has_cache(m)]
        assert len(cached) >= 1  # mindestens erste User-Message

    def test_strategische_breakpoints_bei_vielen_msgs(self):
        # 6 User-Nachrichten → Breakpoints: erste, Mitte, vorletzte
        msgs = [_sys("s")] + [_user(f"u{i}") for i in range(6)]
        result = _apply_cache_control(msgs, is_anthropic=True)
        non_sys = [m for m in result if m.get("role") != "system"]
        cached = [m for m in non_sys if _has_cache(m)]
        # 3 strategische Positionen: Anfang, Mitte, vorletzte
        assert len(cached) == 3
        # Erste User-Message hat Cache
        assert _has_cache(non_sys[0])

    def test_max_4_cache_breakpoints_gesamt(self):
        # 10 Nachrichten → max 4 Cache-Breakpoints insgesamt (1 System + 3 History)
        msgs = [_sys("s")] + [_user(f"u{i}") for i in range(10)]
        result = _apply_cache_control(msgs, is_anthropic=True)
        all_cached = [m for m in result if _has_cache(m)]
        assert len(all_cached) <= 4  # Anthropic-Limit

    def test_leere_content_kein_cache(self):
        msgs = [_sys("s"), {"role": "user", "content": ""}]
        result = _apply_cache_control(msgs, is_anthropic=True)
        non_sys = [m for m in result if m.get("role") != "system"]
        assert not _has_cache(non_sys[0])
