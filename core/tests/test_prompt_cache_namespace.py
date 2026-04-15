"""Prompt-Cache Key-Namespacing (#645 Follow-up).

Personal-Agent (`/agents/personal_<user>`) und Personal-Projekt-Boss
(`/projects/personal_<user>`) teilen sich die `agent_id="personal_<user>"`.
Vor dem Namespacing haben sie sich gegenseitig aus `_STATIC_PROMPT_CACHE`
gedrängt. Nach dem Fix bekommen sie getrennte Keys.
"""
from __future__ import annotations

import pytest

from hydrahive_core import orchestrator_context as oc


@pytest.fixture(autouse=True)
def _clear_cache():
    oc._STATIC_PROMPT_CACHE.clear()
    yield
    oc._STATIC_PROMPT_CACHE.clear()


def test_key_without_agent_dir_is_flat_agent_id():
    assert oc._prompt_cache_key("personal_till") == "personal_till"
    assert oc._prompt_cache_key("personal_till", None) == "personal_till"


def test_key_with_agent_dir_is_namespaced(tmp_path):
    agents = tmp_path / "agents" / "personal_till"
    projects = tmp_path / "projects" / "personal_till"
    agents.mkdir(parents=True)
    projects.mkdir(parents=True)

    k1 = oc._prompt_cache_key("personal_till", agents)
    k2 = oc._prompt_cache_key("personal_till", projects)

    assert k1 != k2
    assert k1.startswith("personal_till:")
    assert k2.startswith("personal_till:")
    assert str(agents.resolve()) in k1
    assert str(projects.resolve()) in k2


def test_invalidate_clears_both_legacy_and_namespaced_entries(tmp_path):
    agents = tmp_path / "agents" / "personal_till"
    projects = tmp_path / "projects" / "personal_till"
    agents.mkdir(parents=True)
    projects.mkdir(parents=True)

    # Seed: flacher Legacy-Key + zwei namespaced Keys
    oc._STATIC_PROMPT_CACHE["personal_till"] = ("legacy", 0.0, "h0")
    oc._STATIC_PROMPT_CACHE[oc._prompt_cache_key("personal_till", agents)] = ("a", 0.0, "h1")
    oc._STATIC_PROMPT_CACHE[oc._prompt_cache_key("personal_till", projects)] = ("p", 0.0, "h2")
    # Fremder Eintrag darf nicht betroffen sein
    oc._STATIC_PROMPT_CACHE["personal_other"] = ("x", 0.0, "h3")

    oc.invalidate_prompt_cache("personal_till")

    assert "personal_till" not in oc._STATIC_PROMPT_CACHE
    assert oc._prompt_cache_key("personal_till", agents) not in oc._STATIC_PROMPT_CACHE
    assert oc._prompt_cache_key("personal_till", projects) not in oc._STATIC_PROMPT_CACHE
    # Prefix-Separator muss greifen — "personal_other" bleibt
    assert "personal_other" in oc._STATIC_PROMPT_CACHE


def test_invalidate_does_not_match_similar_prefixes(tmp_path):
    """`personal_till` darf nicht `personal_tillmann` treffen."""
    oc._STATIC_PROMPT_CACHE["personal_till"] = ("a", 0.0, "h")
    oc._STATIC_PROMPT_CACHE["personal_tillmann"] = ("b", 0.0, "h")
    oc._STATIC_PROMPT_CACHE["personal_tillmann:/agents/personal_tillmann"] = ("c", 0.0, "h")

    oc.invalidate_prompt_cache("personal_till")

    assert "personal_till" not in oc._STATIC_PROMPT_CACHE
    assert "personal_tillmann" in oc._STATIC_PROMPT_CACHE
    assert "personal_tillmann:/agents/personal_tillmann" in oc._STATIC_PROMPT_CACHE


def test_key_fallback_on_unresolvable_path():
    """Nicht-existente Pfade müssen trotzdem einen Key liefern."""
    k = oc._prompt_cache_key("x", "/does/not/exist/at/all")
    assert k.startswith("x:")
    assert "does/not/exist" in k
