"""
vnc_proxy.py — VNC Token-Management für websockify (#900)
================================================================
websockify TokenFile-Format:
  Dateiname: {token_dir}/{token}.cfg
  Inhalt:    {token}: 127.0.0.1:{vnc_port}
"""
from __future__ import annotations

import logging
import socket
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

_VNC_PROXY_DEFAULT_PORT = 6080


class VNCProxy:
    def __init__(self, token_dir: Path):
        self._token_dir = token_dir.resolve()
        # mkdir lazy in register() — Verzeichnis muss vom Installer angelegt sein
        self._map: dict[str, str] = {}

    def register(self, vm_id: str, vnc_port: int) -> str:
        """Registriert VM für VNC-Zugriff. Gibt Token zurück."""
        # Alten Token aufräumen falls vorhanden
        self.unregister(vm_id)

        token = secrets.token_hex(16)
        token_file = self._token_dir / f"{token}.cfg"
        token_file.write_text(f"{token}: 127.0.0.1:{vnc_port}\n", encoding="utf-8")
        token_file.chmod(0o600)
        self._map[vm_id] = token
        logger.info("VNC-Token registriert: vm=%s token=%s port=%d", vm_id, token[:8], vnc_port)
        return token

    def unregister(self, vm_id: str) -> None:
        """Entfernt Token-Datei für eine VM."""
        token = self._map.pop(vm_id, None)
        if token:
            token_file = self._token_dir / f"{token}.cfg"
            if token_file.exists():
                token_file.unlink()
            logger.info("VNC-Token entfernt: vm=%s token=%s", vm_id, token[:8])

    def get_token(self, vm_id: str) -> str | None:
        """Gibt Token für eine VM zurück, oder None."""
        return self._map.get(vm_id)

    def get_websocket_url(self, vm_id: str, host: str, scheme: str = "wss") -> str | None:
        """Gibt vollständigen WebSocket-URL zurück, oder None."""
        token = self._map.get(vm_id)
        if not token:
            return None
        return f"{scheme}://{host}/ws/vnc/?token={token}"

    @staticmethod
    def check_websockify(port: int = _VNC_PROXY_DEFAULT_PORT) -> bool:
        """Prüft ob websockify auf Port erreichbar ist (1s Timeout)."""
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except (OSError, socket.error):
            return False
