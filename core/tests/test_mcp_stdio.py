"""
test_mcp_stdio.py — stdio-Transport für MCP-Client
"""
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _stub_mcp_modules(monkeypatch):
    """mcp-Package stuben, damit Tests ohne echtes mcp-Paket laufen."""
    import types
    mcp_mod = types.ModuleType("mcp")

    class _StdioParams:
        def __init__(self, command, args, env=None, cwd=None):
            self.command = command
            self.args = args
            self.env = env
            self.cwd = cwd
    mcp_mod.StdioServerParameters = _StdioParams
    mcp_mod.ClientSession = MagicMock
    monkeypatch.setitem(sys.modules, "mcp", mcp_mod)

    stdio_sub = types.ModuleType("mcp.client.stdio")
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_sub)
    client_sub = types.ModuleType("mcp.client")
    monkeypatch.setitem(sys.modules, "mcp.client", client_sub)


def test_stdio_params_requires_command(monkeypatch):
    _stub_mcp_modules(monkeypatch)
    # Reimport in case already loaded
    import importlib
    from hydrahive_core import mcp_client as mc
    importlib.reload(mc)

    with pytest.raises(ValueError) as exc:
        mc._stdio_params({"transport": "stdio"})
    assert "command" in str(exc.value).lower()


def test_stdio_params_builds_correctly(monkeypatch):
    _stub_mcp_modules(monkeypatch)
    import importlib
    from hydrahive_core import mcp_client as mc
    importlib.reload(mc)

    params = mc._stdio_params({
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "pkg"],
        "env": {"TOKEN": "abc"},
    })
    assert params.command == "npx"
    assert params.args == ["-y", "pkg"]
    assert params.env["TOKEN"] == "abc"
    # PATH automatisch ergänzt
    assert "PATH" in params.env


def test_stdio_params_preserves_provided_path(monkeypatch):
    _stub_mcp_modules(monkeypatch)
    import importlib
    from hydrahive_core import mcp_client as mc
    importlib.reload(mc)

    params = mc._stdio_params({
        "command": "x",
        "env": {"PATH": "/custom/path"},
    })
    assert params.env["PATH"] == "/custom/path"


def test_stdio_params_accepts_cwd(monkeypatch):
    _stub_mcp_modules(monkeypatch)
    import importlib
    from hydrahive_core import mcp_client as mc
    importlib.reload(mc)

    params = mc._stdio_params({
        "command": "x", "cwd": "/tmp/wd",
    })
    assert params.cwd == "/tmp/wd"


@pytest.mark.asyncio
async def test_dispatcher_routes_stdio_to_list(monkeypatch):
    _stub_mcp_modules(monkeypatch)
    import importlib
    from hydrahive_core import mcp_client as mc
    importlib.reload(mc)
    mc._tools_cache.clear()

    _called = {}

    async def _fake_stdio_list(cfg):
        _called["list"] = cfg
        return [{"name": "x", "description": "", "inputSchema": {}}]

    monkeypatch.setattr(mc, "_stdio_list_tools", _fake_stdio_list)

    cfg = {"transport": "stdio", "command": "echo"}
    tools = await mc.list_mcp_tools("test_stdio", cfg)
    assert tools == [{"name": "x", "description": "", "inputSchema": {}}]
    assert _called.get("list") == cfg


@pytest.mark.asyncio
async def test_dispatcher_routes_stdio_to_call(monkeypatch):
    _stub_mcp_modules(monkeypatch)
    import importlib
    from hydrahive_core import mcp_client as mc
    importlib.reload(mc)

    _called = {}

    async def _fake_stdio_call(cfg, tn, args):
        _called["cfg"] = cfg
        _called["tn"] = tn
        _called["args"] = args
        return "result-text"

    monkeypatch.setattr(mc, "_stdio_call_tool", _fake_stdio_call)

    cfg = {"transport": "stdio", "command": "echo"}
    out = await mc.call_mcp_tool("s", cfg, "my_tool", {"k": "v"})
    assert out == "result-text"
    assert _called["tn"] == "my_tool"
    assert _called["args"] == {"k": "v"}


@pytest.mark.asyncio
async def test_dispatcher_defaults_to_http(monkeypatch):
    _stub_mcp_modules(monkeypatch)
    import importlib
    from hydrahive_core import mcp_client as mc
    importlib.reload(mc)
    mc._tools_cache.clear()

    async def _fake_http_list(url, hdrs):
        return [{"name": "via_http"}]

    monkeypatch.setattr(mc, "_http_list_tools", _fake_http_list)

    tools = await mc.list_mcp_tools("x", {"url": "http://x"})
    assert tools[0]["name"] == "via_http"


@pytest.mark.asyncio
async def test_cache_hit_skips_call(monkeypatch):
    _stub_mcp_modules(monkeypatch)
    import importlib
    import time as _time
    from hydrahive_core import mcp_client as mc
    importlib.reload(mc)

    mc._tools_cache["cached_server"] = {
        "tools": [{"name": "cached_tool"}],
        "ts": _time.time(),
    }
    called = False

    async def _fail_stdio(cfg):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(mc, "_stdio_list_tools", _fail_stdio)
    tools = await mc.list_mcp_tools("cached_server", {"transport": "stdio", "command": "x"})
    assert tools[0]["name"] == "cached_tool"
    assert not called
