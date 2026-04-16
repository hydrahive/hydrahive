"""Tests für #668: Router-Chat propagiert `request_user` an Orchestrator.

Kernbedingung:
- auth liefert `("alice", "user")` → `request_user="alice"` an Orchestrator.
- auth liefert `("internal", "admin")` → `request_user=None`.
- Verhalten sowohl für `POST /agents/{id}/message` (non-stream) als auch
  für `POST /agents/{id}/message/stream` (streaming).
"""
from __future__ import annotations

from unittest import mock
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from hydrahive_core.router_agent_chat import register_agent_chat_routes


class _IncomingMessage(BaseModel):
    content: str
    sender: str | None = None
    execution_mode: str | None = None
    workspace_override: None = None
    images: list[dict] | None = None


def _make_app(agents_dir, *, auth=("alice", "user")):
    """Baut FastAPI + Router mit Mock-Orchestrator. Gibt
    (client, mock_orchestrator) zurück."""
    app = FastAPI()
    auth_router = APIRouter()

    def _require_auth():
        return auth

    def _check_message_rate(sender, agent_id):
        return

    # Mock-Agent + Discovery
    _agent_cfg = mock.MagicMock()
    _agent_cfg.identity = "Bob"
    discovery = mock.MagicMock()
    discovery.get.return_value = _agent_cfg

    # Mock-Sessions (nur Streaming braucht's für append)
    sessions = mock.MagicMock()
    sessions.append = AsyncMock()
    sessions.get_active = mock.MagicMock(return_value=mock.MagicMock(id="s1"))
    sessions.get_context = mock.MagicMock(return_value=[])

    orchestrator = mock.MagicMock()
    orchestrator.handle_message = AsyncMock(return_value=("ok", []))

    async def _stream_gen(**kwargs):
        yield f'data: {{"type":"text_delta","text":"captured:{kwargs.get("request_user")}"}}\n\n'
        yield 'data: {"done": true}\n\n'
    orchestrator.handle_message_stream = _stream_gen

    # Agent-Dir existiert (check in Route? — _check_agent_access etc.)
    (agents_dir / "bob").mkdir(parents=True, exist_ok=True)

    register_agent_chat_routes(
        app, auth_router,
        require_auth=_require_auth,
        require_auth_or_localhost=_require_auth,
        check_message_rate=_check_message_rate,
        discovery=discovery,
        agent_sessions=sessions,
        agent_orchestrator=orchestrator,
        agents_dir=str(agents_dir),
        audit_log=mock.MagicMock(),
        logger=mock.MagicMock(),
        incoming_message_model=_IncomingMessage,
        group_service=None,
    )
    app.include_router(auth_router)
    return TestClient(app), orchestrator


# ── Non-Streaming ────────────────────────────────────────────────────────────

def test_non_stream_forwards_request_user_for_auth_user(tmp_path):
    client, orch = _make_app(tmp_path / "agents", auth=("alice", "user"))
    r = client.post("/agents/bob/message", json={"content": "hi"})
    assert r.status_code == 200, r.text
    kwargs = orch.handle_message.call_args.kwargs
    assert kwargs["request_user"] == "alice"
    assert kwargs["sender"] == "alice"


def test_non_stream_internal_yields_none(tmp_path):
    client, orch = _make_app(tmp_path / "agents", auth=("internal", "admin"))
    r = client.post("/agents/bob/message", json={"content": "hi", "sender": "bot"})
    assert r.status_code == 200, r.text
    kwargs = orch.handle_message.call_args.kwargs
    assert kwargs["request_user"] is None
    # sender fällt auf body-sender, weil internal
    assert kwargs["sender"] == "bot"


# ── Streaming ────────────────────────────────────────────────────────────────

def test_stream_forwards_request_user_for_auth_user(tmp_path):
    client, _orch = _make_app(tmp_path / "agents", auth=("alice", "user"))
    r = client.post("/agents/bob/message/stream", json={"content": "hi"})
    assert r.status_code == 200
    # Der Mock-Stream echoed request_user in das SSE-Chunk zurück.
    assert "captured:alice" in r.text


def test_stream_internal_yields_none(tmp_path):
    client, _orch = _make_app(tmp_path / "agents", auth=("internal", "admin"))
    r = client.post("/agents/bob/message/stream",
                    json={"content": "hi", "sender": "bot"})
    assert r.status_code == 200
    assert "captured:None" in r.text
