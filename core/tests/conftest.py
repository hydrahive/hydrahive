"""
conftest.py — Pytest-Konfiguration

Richtet sys.path ein und mockt schwere Abhängigkeiten (discord, watchdog, litellm etc.)
damit Unit-Tests ohne den kompletten Produktions-Stack laufen.
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

# src/ ins Python-Pfad
SRC_DIR = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))

# Schwere/fehlende Dependencies mocken bevor der Code importiert wird
_MOCK_MODULES = [
    "watchdog", "watchdog.events", "watchdog.observers",
    "litellm",
    "discord", "discord.ext", "discord.ext.commands",
    "telegram", "telegram.ext",
    "matrix_nio", "nio",
    "redis",
    "websockets",
    "slowapi", "slowapi.util", "slowapi.errors",
    "croniter",
    "anthropic",
    "jose", "jose.jwt",
    "aiohttp",
    "fastapi", "fastapi.security", "fastapi.responses", "fastapi.middleware",
    "fastapi.middleware.cors", "fastapi.staticfiles",
    "uvicorn",
    "starlette", "starlette.responses", "starlette.requests",
    "python_jose", "cryptography",
]

for mod_name in _MOCK_MODULES:
    if mod_name not in sys.modules:
        # Hierarchische Mocks: discord.ext braucht discord als Parent
        parts = mod_name.split(".")
        for i in range(1, len(parts) + 1):
            key = ".".join(parts[:i])
            if key not in sys.modules:
                sys.modules[key] = MagicMock()
