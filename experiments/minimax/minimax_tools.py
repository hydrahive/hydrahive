"""
HydraHive Native Tools für MiniMax Multimodal
=============================================
Agent-Tools für Bild, Video, Musik und Sprache.

Diese Tools können direkt in Agent-Konfiguration verwendet werden:

{
    "id": "media-agent",
    "tools": [
        "minimax_image_gen",
        "minimax_video_gen",
        "minimax_music_gen",
        "minimax_tts",
        "minimax_stt",
        "minimax_vision"
    ]
}
"""

import os
import json
import asyncio
import re
from pathlib import Path
from typing import Optional, Union
from dataclasses import dataclass

from .minimax_client import MiniMaxClient, MiniMaxClientSync


# ========================================================================
# CONFIGURATION
# ========================================================================

@dataclass
class MiniMaxToolConfig:
    """Konfiguration für MiniMax Tools"""
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    output_dir: str = "/tmp/hydrahive-media"
    default_size: str = "1024x1024"
    default_video_duration: int = 6
    default_voice: str = "male-qn-qingse"

    @classmethod
    def from_env(cls) -> "MiniMaxToolConfig":
        return cls(
            api_key=os.environ.get("MINIMAX_API_KEY"),
            base_url=os.environ.get("MINIMAX_BASE_URL"),
            output_dir=os.environ.get("MINIMAX_OUTPUT_DIR", "/tmp/hydrahive-media")
        )


# ========================================================================
# TOOL REGISTRY
# ========================================================================

class ToolRegistry:
    """Registry aller MiniMax Tools für HydraHive"""

    TOOLS = {
        "minimax_image_gen": {
            "name": "minimax_image_gen",
            "description": "Generiert Bilder basierend auf Text-Beschreibung. Perfekt für Illustrationen, Konzepte, und kreative Inhalte.",
            "parameters": {
                "prompt": {"type": "string", "required": True, "description": "Bildbeschreibung"},
                "size": {"type": "string", "default": "1024x1024"},
                "count": {"type": "integer", "default": 1, "min": 1, "max": 4}
            }
        },
        "minimax_video_gen": {
            "name": "minimax_video_gen",
            "description": "Generiert kurze Videos (6-10s) basierend auf Text. Für Animationen, Werbung, Social Media.",
            "parameters": {
                "prompt": {"type": "string", "required": True, "description": "Videobeschreibung"},
                "duration": {"type": "integer", "default": 6, "options": [6, 10]},
                "resolution": {"type": "string", "default": "1280x720"}
            }
        },
        "minimax_music_gen": {
            "name": "minimax_music_gen",
            "description": "Generiert Musik basierend auf Stil, Genre und Stimmung. Für Podcasts, Videos, Spiele.",
            "parameters": {
                "prompt": {"type": "string", "required": True, "description": "Musikbeschreibung"},
                "style": {"type": "string", "default": "pop"},
                "title": {"type": "string", "required": False}
            }
        },
        "minimax_tts": {
            "name": "minimax_tts",
            "description": "Text-zu-Sprache. Konvertiert Text in natürliche Sprachausgabe.",
            "parameters": {
                "text": {"type": "string", "required": True, "description": "Text zum Vorlesen"},
                "voice": {"type": "string", "default": "male-qn-qingse"},
                "speed": {"type": "number", "default": 1.0, "min": 0.5, "max": 2.0}
            }
        },
        "minimax_stt": {
            "name": "minimax_stt",
            "description": "Sprache-zu-Text. Transkribiert Audio-Dateien.",
            "parameters": {
                "audio_path": {"type": "string", "required": True, "description": "Pfad zur Audio-Datei"},
                "language": {"type": "string", "default": "auto"}
            }
        },
        "minimax_vision": {
            "name": "minimax_vision",
            "description": "Analysiert Bilder und beantwortet Fragen. Extrahiert Informationen aus Fotos, Screenshots, Dokumenten.",
            "parameters": {
                "image_path": {"type": "string", "required": True, "description": "Pfad oder URL zum Bild"},
                "question": {"type": "string", "required": True, "description": "Frage zum Bild"}
            }
        }
    }

    @classmethod
    def get_tool_schema(cls, tool_name: str) -> dict:
        """Gibt JSON-Schema für Tool"""
        return cls.TOOLS.get(tool_name, {})

    @classmethod
    def list_tools(cls) -> list[dict]:
        """Liste aller Tools"""
        return list(cls.TOOLS.values())


# ========================================================================
# TOOL IMPLEMENTATIONS
# ========================================================================

class ImageGenTool:
    """Tool: Bild-Generierung"""

    def __init__(self, config: Optional[MiniMaxToolConfig] = None):
        self.config = config or MiniMaxToolConfig.from_env()
        self.client = MiniMaxClientSync(
            api_key=self.config.api_key,
            base_url=self.config.base_url
        )

    async def execute(self, prompt: str, size: str = None, count: int = 1) -> dict:
        """
        Generiert Bilder.

        Args:
            prompt: Bildbeschreibung
            size: Größe (default aus config)
            count: Anzahl Bilder

        Returns:
            Dict mit Bild-URLs und lokalen Pfaden
        """
        os.makedirs(self.config.output_dir, exist_ok=True)

        size = size or self.config.default_size

        # Generieren
        urls = self.client.generate_image(
            prompt=prompt,
            size=size,
            n=count
        )

        # Lokal speichern
        files = []
        for i, url in enumerate(urls):
            import uuid
            filename = f"img_{uuid.uuid4().hex[:8]}.png"
            filepath = Path(self.config.output_dir) / filename

            # Download (hier synchron für Tool-Interface)
            import httpx
            resp = httpx.get(url, timeout=30)
            resp.raise_for_status()
            filepath.write_bytes(resp.content)

            files.append({
                "index": i,
                "url": url,
                "local_path": str(filepath),
                "filename": filename
            })

        return {
            "success": True,
            "count": len(files),
            "images": files,
            "prompt": prompt
        }

    def execute_sync(self, prompt: str, size: str = None, count: int = 1) -> dict:
        """Synchroner Wrapper für nicht-async Kontexte"""
        return asyncio.run(self.execute(prompt, size, count))


class VideoGenTool:
    """Tool: Video-Generierung"""

    def __init__(self, config: Optional[MiniMaxToolConfig] = None):
        self.config = config or MiniMaxToolConfig.from_env()
        self.client = MiniMaxClientSync(
            api_key=self.config.api_key,
            base_url=self.config.base_url
        )

    async def execute(
        self,
        prompt: str,
        duration: int = None,
        resolution: str = "1280x720"
    ) -> dict:
        """
        Generiert Video.

        Args:
            prompt: Videobeschreibung
            duration: Länge in Sekunden (6 oder 10)
            resolution: Auflösung

        Returns:
            Dict mit Video-URL, Status, lokaler Pfad
        """
        os.makedirs(self.config.output_dir, exist_ok=True)

        duration = duration or self.config.default_video_duration

        result = self.client.generate_video(
            prompt=prompt,
            duration=duration,
            resolution=resolution,
            wait_for_completion=True,
            timeout=600.0
        )

        # Lokal speichern wenn fertig
        if result.get("status") == "completed" and result.get("video_url"):
            import uuid
            filename = f"video_{uuid.uuid4().hex[:8]}.mp4"
            filepath = Path(self.config.output_dir) / filename

            import httpx
            resp = httpx.get(result["video_url"], timeout=120)
            resp.raise_for_status()
            filepath.write_bytes(resp.content)

            result["local_path"] = str(filepath)
            result["filename"] = filename

        return result

    def execute_sync(self, prompt: str, duration: int = None, resolution: str = "1280x720") -> dict:
        return asyncio.run(self.execute(prompt, duration, resolution))


class MusicGenTool:
    """Tool: Musik-Generierung"""

    def __init__(self, config: Optional[MiniMaxToolConfig] = None):
        self.config = config or MiniMaxToolConfig.from_env()
        self.client = MiniMaxClientSync(
            api_key=self.config.api_key,
            base_url=self.config.base_url
        )

    async def execute(
        self,
        prompt: str,
        style: str = "pop",
        title: str = None
    ) -> dict:
        """
        Generiert Musik.

        Args:
            prompt: Musikbeschreibung
            style: Genre/Stil
            title: Optionaler Titel

        Returns:
            Dict mit Audio-URL, Status, lokaler Pfad
        """
        os.makedirs(self.config.output_dir, exist_ok=True)

        result = self.client.generate_music(
            prompt=prompt,
            style=style,
            title=title,
            wait_for_completion=True,
            timeout=300.0
        )

        if result.get("status") == "completed" and result.get("audio_url"):
            import uuid
            filename = f"music_{uuid.uuid4().hex[:8]}.mp3"
            filepath = Path(self.config.output_dir) / filename

            import httpx
            resp = httpx.get(result["audio_url"], timeout=60)
            resp.raise_for_status()
            filepath.write_bytes(resp.content)

            result["local_path"] = str(filepath)
            result["filename"] = filename

        return result

    def execute_sync(self, prompt: str, style: str = "pop", title: str = None) -> dict:
        return asyncio.run(self.execute(prompt, style, title))


class TTSTool:
    """Tool: Text-zu-Sprache"""

    def __init__(self, config: Optional[MiniMaxToolConfig] = None):
        self.config = config or MiniMaxToolConfig.from_env()
        self.client = MiniMaxClientSync(
            api_key=self.config.api_key,
            base_url=self.config.base_url
        )

    async def execute(
        self,
        text: str,
        voice: str = None,
        speed: float = 1.0
    ) -> dict:
        """
        Konvertiert Text zu Sprache.

        Args:
            text: Text zum Vorlesen
            voice: Stimmen-ID
            speed: Geschwindigkeit

        Returns:
            Dict mit lokalem Pfad zur Audio-Datei
        """
        os.makedirs(self.config.output_dir, exist_ok=True)

        voice = voice or self.config.default_voice

        audio_data = self.client.text_to_speech(
            text=text,
            voice=voice,
            speed=speed
        )

        import uuid
        filename = f"tts_{uuid.uuid4().hex[:8]}.mp3"
        filepath = Path(self.config.output_dir) / filename
        filepath.write_bytes(audio_data)

        return {
            "success": True,
            "text_length": len(text),
            "voice": voice,
            "speed": speed,
            "local_path": str(filepath),
            "filename": filename,
            "duration_estimate": len(audio_data) / 32000
        }

    def execute_sync(self, text: str, voice: str = None, speed: float = 1.0) -> dict:
        return asyncio.run(self.execute(text, voice, speed))


class STTTool:
    """Tool: Sprache-zu-Text"""

    def __init__(self, config: Optional[MiniMaxToolConfig] = None):
        self.config = config or MiniMaxToolConfig.from_env()
        self.client = MiniMaxClientSync(
            api_key=self.config.api_key,
            base_url=self.config.base_url
        )

    async def execute(self, audio_path: str, language: str = "auto") -> dict:
        """
        Transkribiert Audio zu Text.

        Args:
            audio_path: Pfad zur Audio-Datei
            language: Sprache oder "auto"

        Returns:
            Dict mit transkribiertem Text
        """
        path = Path(audio_path)
        if not path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        text = self.client.speech_to_text_from_file(
            audio_path=str(path),
            language=language
        )

        return {
            "success": True,
            "text": text,
            "language": language,
            "chars": len(text),
            "words": len(text.split())
        }

    def execute_sync(self, audio_path: str, language: str = "auto") -> dict:
        return asyncio.run(self.execute(audio_path, language))


class VisionTool:
    """Tool: Bild-Analyse"""

    def __init__(self, config: Optional[MiniMaxToolConfig] = None):
        self.config = config or MiniMaxToolConfig.from_env()
        self.client = MiniMaxClientSync(
            api_key=self.config.api_key,
            base_url=self.config.base_url
        )

    async def execute(self, image_path: str, question: str) -> dict:
        """
        Analysiert Bild und beantwortet Frage.

        Args:
            image_path: Pfad oder URL zum Bild
            question: Frage zum Bild

        Returns:
            Dict mit Analyse-Ergebnis
        """
        # URL oder lokaler Pfad
        image_url = image_path if image_path.startswith("http") else image_path

        result = self.client.analyze_image(
            image_url=image_url,
            prompt=question
        )

        return {
            "success": True,
            "analysis": result,
            "question": question,
            "image": image_path
        }

    def execute_sync(self, image_path: str, question: str) -> dict:
        return asyncio.run(self.execute(image_path, question))


# ========================================================================
# TOOL FACTORY
# ========================================================================

class MiniMaxToolFactory:
    """Factory für MiniMax Tools"""

    _tools = {
        "minimax_image_gen": ImageGenTool,
        "minimax_video_gen": VideoGenTool,
        "minimax_music_gen": MusicGenTool,
        "minimax_tts": TTSTool,
        "minimax_stt": STTTool,
        "minimax_vision": VisionTool
    }

    @classmethod
    def create(cls, tool_name: str, config: Optional[MiniMaxToolConfig] = None):
        """Erstellt Tool-Instanz"""
        tool_class = cls._tools.get(tool_name)
        if not tool_class:
            raise ValueError(f"Unknown tool: {tool_name}")
        return tool_class(config)

    @classmethod
    def create_all(cls, config: Optional[MiniMaxToolConfig] = None) -> dict:
        """Erstellt alle Tools"""
        return {name: cls.create(name, config) for name in cls._tools.keys()}


# ========================================================================
# HYDRAHIVE INTEGRATION HELPERS
# ========================================================================

def get_tools_for_agent() -> list[dict]:
    """
    Gibt Tool-Definitionen für Agent-Konfiguration.
    Verwendung in agent.yaml oder API:

    tools:
      - minimax_image_gen
      - minimax_video_gen
      - minimax_music_gen
      - minimax_tts
      - minimax_stt
      - minimax_vision
    """
    return ToolRegistry.list_tools()


def register_in_orchestrator():
    """
    Helper um Tools im Orchestrator zu registrieren.

    In orchestrator_tools.py einfügen:

    from .minimax_tools import MiniMaxToolFactory, ToolRegistry

    def get_available_tools():
        base_tools = [...]  # Bestehende Tools
        minimax_tools = [t["name"] for t in ToolRegistry.list_tools()]
        return base_tools + minimax_tools
    """
    pass


# ========================================================================
# EXPORTS
# ========================================================================

__all__ = [
    "MiniMaxToolConfig",
    "ToolRegistry",
    "ImageGenTool",
    "VideoGenTool",
    "MusicGenTool",
    "TTSTool",
    "STTTool",
    "VisionTool",
    "MiniMaxToolFactory",
    "get_tools_for_agent",
    "register_in_orchestrator"
]