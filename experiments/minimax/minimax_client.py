"""
MiniMax API Client für HydraHive
===============================
Vollständiger Client für alle MiniMax-API-Endpunkte:
- Text/Chat (bereits in orchestrator_llm.py)
- Bild-Analyse (Vision)
- Bild-Generierung
- Video-Generierung
- Musik-Generierung
- TTS (Text-to-Speech)
- STT (Speech-to-Text)

Verwendung:
    from hydrahive_core.minimax_client import MiniMaxClient

    client = MiniMaxClient(api_key="your-key")
    result = await client.generate_image("A sunset over mountains", model="MiniMax-Image-01")
"""

import os
import asyncio
import httpx
import base64
from typing import Optional, Union, AsyncIterator
from pathlib import Path
from enum import Enum


class MiniMaxModel(Enum):
    """Verfügbare MiniMax-Modelle"""
    # Text
    M2_7 = "MiniMax-M2.7"
    M2_5 = "MiniMax-M2.5"

    # Vision
    VISION = "MiniMax-Text-01"

    # Image
    IMAGE_01 = "MiniMax-Image-01"

    # Video
    VIDEO_01 = "MiniMax-Video-01"

    # Music
    MUSIC = "MiniMax-Music-01"

    # Speech
    SPEECH_TTS = "speech-01"
    SPEECH_STT = "speech-01-turbo"


class MediaFormat(Enum):
    """Ausgabeformate für Medien-Generierung"""
    # Bild
    PNG = "png"
    JPG = "jpg"
    WEBP = "webp"

    # Video
    MP4 = "mp4"
    AVI = "avi"

    # Audio
    MP3 = "mp3"
    WAV = "wav"
    AAC = "aac"


class MiniMaxClient:
    """
    Async Client für MiniMax API
    Unterstützt alle Medien-Typen: Text, Vision, Image, Video, Music, Speech
    """

    BASE_URL = "https://api.minimax.io"
    API_VERSION = "v1"

    # Timeouts (in Sekunden)
    DEFAULT_TIMEOUT = 30.0
    LONG_POLL_TIMEOUT = 300.0  # Für Video/Musik (async jobs)

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        http_client: Optional[httpx.AsyncClient] = None
    ):
        """
        Args:
            api_key: MiniMax API Key (oder MINIMAX_API_KEY env)
            base_url: Optionaler Endpoint-Override (z.B. China: api.minimax.chat)
            timeout: Request-Timeout
            http_client: Optionaler httpx Client für Connection-Pooling
        """
        self.api_key = api_key or os.environ.get("MINIMAX_API_KEY", "")
        self.base_url = base_url or os.environ.get("MINIMAX_BASE_URL", self.BASE_URL)
        self.timeout = timeout

        # Interner HTTP Client
        self._client = http_client
        self._owns_client = http_client is None

        # Auth Header
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-Initialization des HTTP Clients"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.DEFAULT_TIMEOUT),
                headers={"Authorization": f"Bearer {self.api_key}"}
            )
        return self._client

    async def close(self):
        """Client schließen (bei eigenem HTTP Client)"""
        if self._owns_client and self._client:
            await self._client.aclose()

    # ========================================================================
    # IMAGE GENERATION (Bild-Generierung)
    # ========================================================================

    async def generate_image(
        self,
        prompt: str,
        model: str = "MiniMax-Image-01",
        n: int = 1,
        size: str = "1024x1024",
        timeout: float = 60.0
    ) -> list[str]:
        """
        Generiert Bilder basierend auf Text-Prompt.

        Args:
            prompt: Bildbeschreibung
            model: MiniMax Image Model
            n: Anzahl Bilder (1-4)
            size: Bildgröße (1024x1024, 512x512, etc.)
            timeout: Timeout für Request

        Returns:
            Liste von Bild-URLs
        """
        client = await self._get_client()

        payload = {
            "model": model,
            "prompt": prompt,
            "n": min(n, 4),
            "size": size
        }

        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as c:
            response = await c.post(
                f"{self.base_url}/api/{self.API_VERSION}/images",
                json=payload,
                headers=self._headers
            )
            response.raise_for_status()
            data = response.json()
            return data.get("images", [])

    async def generate_image_to_file(
        self,
        prompt: str,
        output_path: Union[str, Path],
        **kwargs
    ) -> Path:
        """
        Generiert Bild und speichert direkt in Datei.

        Args:
            prompt: Bildbeschreibung
            output_path: Zielpfad für Bilddatei
            **kwargs: Argumente für generate_image()

        Returns:
            Pfad zur gespeicherten Datei
        """
        import io
        from PIL import Image as PILImage

        # Erst URL holen
        urls = await self.generate_image(prompt, **kwargs)
        if not urls:
            raise ValueError("No image URLs returned")

        # Bild herunterladen
        client = await self._get_client()
        response = await client.get(urls[0])
        response.raise_for_status()

        # Speichern
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        with open(output, "wb") as f:
            f.write(response.content)

        return output

    # ========================================================================
    # VIDEO GENERATION (Video-Generierung)
    # ========================================================================

    async def generate_video(
        self,
        prompt: str,
        model: str = "MiniMax-Video-01",
        duration: int = 6,
        resolution: str = "1280x720",
        wait_for_completion: bool = True,
        poll_interval: float = 5.0,
        timeout: float = 600.0
    ) -> dict:
        """
        Generiert Video basierend auf Text-Prompt.

        Args:
            prompt: Video-Beschreibung
            model: MiniMax Video Model
            duration: Videolänge in Sekunden (6 oder 10)
            resolution: Auflösung (1280x720, 1920x1080)
            wait_for_completion: Warten bis Video fertig (True) oder sofort Return (False)
            poll_interval: Polling-Intervall in Sekunden
            timeout: Gesamt-Timeout für Video-Generierung

        Returns:
            Dict mit video_url, task_id, status
        """
        client = await self._get_client()

        payload = {
            "model": model,
            "prompt": prompt,
            "duration": duration,
            "resolution": resolution
        }

        # Job starten
        response = await client.post(
            f"{self.base_url}/api/{self.API_VERSION}/video_generation",
            json=payload,
            headers=self._headers
        )
        response.raise_for_status()
        result = response.json()

        task_id = result.get("task_id")

        if wait_for_completion:
            result = await self._wait_for_video(task_id, poll_interval, timeout)

        return result

    async def _wait_for_video(
        self,
        task_id: str,
        poll_interval: float,
        timeout: float
    ) -> dict:
        """Pollt bis Video-Generierung abgeschlossen ist."""
        client = await self._get_client()
        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise TimeoutError(f"Video generation timed out after {timeout}s")

            response = await client.get(
                f"{self.base_url}/api/{self.API_VERSION}/video_generation/{task_id}",
                headers=self._headers
            )
            response.raise_for_status()
            status = response.json()

            if status.get("status") == "completed":
                return status
            elif status.get("status") == "failed":
                raise RuntimeError(f"Video generation failed: {status.get('error')}")

            await asyncio.sleep(poll_interval)

    async def generate_video_to_file(
        self,
        prompt: str,
        output_path: Union[str, Path],
        **kwargs
    ) -> Path:
        """
        Generiert Video und speichert direkt in Datei.
        """
        result = await self.generate_video(prompt, wait_for_completion=True, **kwargs)
        video_url = result.get("video_url")

        if not video_url:
            raise ValueError("No video URL in result")

        # Herunterladen
        client = await self._get_client()
        response = await client.get(video_url)
        response.raise_for_status()

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        with open(output, "wb") as f:
            f.write(response.content)

        return output

    # ========================================================================
    # MUSIC GENERATION (Musik-Generierung)
    # ========================================================================

    async def generate_music(
        self,
        prompt: str,
        title: Optional[str] = None,
        style: Optional[str] = None,
        wait_for_completion: bool = True,
        poll_interval: float = 5.0,
        timeout: float = 300.0
    ) -> dict:
        """
        Generiert Musik basierend auf Text-Beschreibung.

        Args:
            prompt: Musik-Beschreibung (Genre, Stimmung, Instrumente)
            title: Optionaler Titel
            style: Musik-Stil (pop, rock, jazz, classical, etc.)
            wait_for_completion: Warten bis fertig
            poll_interval: Polling-Intervall
            timeout: Gesamt-Timeout

        Returns:
            Dict mit audio_url, task_id, status
        """
        client = await self._get_client()

        payload = {
            "model": "MiniMax-Music-01",
            "prompt": prompt
        }
        if title:
            payload["title"] = title
        if style:
            payload["style"] = style

        response = await client.post(
            f"{self.base_url}/api/{self.API_VERSION}/music_generation",
            json=payload,
            headers=self._headers
        )
        response.raise_for_status()
        result = response.json()

        task_id = result.get("task_id")

        if wait_for_completion:
            result = await self._wait_for_music(task_id, poll_interval, timeout)

        return result

    async def _wait_for_music(
        self,
        task_id: str,
        poll_interval: float,
        timeout: float
    ) -> dict:
        """Pollt bis Musik-Generierung abgeschlossen ist."""
        client = await self._get_client()
        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise TimeoutError(f"Music generation timed out after {timeout}s")

            response = await client.get(
                f"{self.base_url}/api/{self.API_VERSION}/music_generation/{task_id}",
                headers=self._headers
            )
            response.raise_for_status()
            status = response.json()

            if status.get("status") == "completed":
                return status
            elif status.get("status") == "failed":
                raise RuntimeError(f"Music generation failed: {status.get('error')}")

            await asyncio.sleep(poll_interval)

    async def generate_music_to_file(
        self,
        prompt: str,
        output_path: Union[str, Path],
        **kwargs
    ) -> Path:
        """Generiert Musik und speichert direkt in Datei."""
        result = await self.generate_music(prompt, wait_for_completion=True, **kwargs)
        audio_url = result.get("audio_url")

        if not audio_url:
            raise ValueError("No audio URL in result")

        client = await self._get_client()
        response = await client.get(audio_url)
        response.raise_for_status()

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        with open(output, "wb") as f:
            f.write(response.content)

        return output

    # ========================================================================
    # SPEECH (TTS & STT)
    # ========================================================================

    async def text_to_speech(
        self,
        text: str,
        model: str = "speech-01",
        voice: str = "male-qn-qingse",
        speed: float = 1.0,
        output_format: str = "mp3"
    ) -> bytes:
        """
        Konvertiert Text zu Sprache.

        Args:
            text: Text der gesprochen werden soll
            model: TTS Model
            voice: Stimmen-ID (male-qn-qingse, female-shaonv, etc.)
            speed: Sprechgeschwindigkeit (0.5 - 2.0)
            output_format: Ausgabeformat (mp3, wav, pcm)

        Returns:
            Audio-Daten als Bytes
        """
        client = await self._get_client()

        payload = {
            "model": model,
            "text": text,
            "voice": voice,
            "speed": speed,
            "output_format": output_format
        }

        response = await client.post(
            f"{self.base_url}/api/{self.API_VERSION}/t2a_2",
            json=payload,
            headers={"Authorization": f"Bearer {self.api_key}"}
        )
        response.raise_for_status()

        return response.content

    async def text_to_speech_to_file(
        self,
        text: str,
        output_path: Union[str, Path],
        **kwargs
    ) -> Path:
        """Speichert TTS-Ausgabe direkt in Datei."""
        audio_data = await self.text_to_speech(text, **kwargs)

        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)

        with open(output, "wb") as f:
            f.write(audio_data)

        return output

    async def speech_to_text(
        self,
        audio_data: bytes,
        model: str = "speech-01-turbo",
        language: str = "auto"
    ) -> str:
        """
        Konvertiert Sprache zu Text.

        Args:
            audio_data: Audio-Daten (MP3, WAV, etc.)
            model: STT Model
            language: Sprache ("auto" für automatische Erkennung)

        Returns:
            Transkribierter Text
        """
        client = await self._get_client()

        # Multipart Request für Audio
        files = {
            "file": ("audio.mp3", audio_data, "audio/mpeg")
        }
        data = {
            "model": model,
            "language": language
        }

        response = await client.post(
            f"{self.base_url}/api/{self.API_VERSION}/audio/transcriptions",
            files=files,
            data=data,
            headers={"Authorization": f"Bearer {self.api_key}"}
        )
        response.raise_for_status()

        result = response.json()
        return result.get("text", "")

    async def speech_to_text_from_file(
        self,
        audio_path: Union[str, Path],
        **kwargs
    ) -> str:
        """Transkribiert Audio-Datei direkt."""
        with open(audio_path, "rb") as f:
            audio_data = f.read()

        return await self.speech_to_text(audio_data, **kwargs)

    # ========================================================================
    # VISION (Bild-Analyse) - Für Agent-Tools
    # ========================================================================

    async def analyze_image(
        self,
        image_url: str,
        prompt: str,
        model: str = "MiniMax-M2.7"
    ) -> str:
        """
        Analysiert Bild und gibt Text-Beschreibung zurück.
        Für Agent-Tool: file_read mit Bild-Analyse.

        Args:
            image_url: URL oder Base64 des Bildes
            prompt: Frage/Aufgabe zum Bild
            model: Vision-fähiges Modell

        Returns:
            Text-Beschreibung/Antwort
        """
        client = await self._get_client()

        # Image URL oder Base64
        image_content = image_url if image_url.startswith("http") else image_url

        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_content}}
                    ]
                }
            ]
        }

        response = await client.post(
            f"{self.base_url}/api/{self.API_VERSION}/chat/completions",
            json=payload,
            headers=self._headers
        )
        response.raise_for_status()

        result = response.json()
        return result["choices"][0]["message"]["content"]

    # ========================================================================
    # UTILITY METHODS
    # ========================================================================

    async def check_quota(self) -> dict:
        """Gibt aktuelle Quota-Nutzung zurück."""
        client = await self._get_client()

        response = await client.get(
            f"{self.base_url}/api/{self.API_VERSION}/quota",
            headers=self._headers
        )
        response.raise_for_status()

        return response.json()

    async def health_check(self) -> bool:
        """Prüft ob API erreichbar ist."""
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.base_url}/api/{self.API_VERSION}/health",
                headers=self._headers
            )
            return response.status_code == 200
        except Exception:
            return False


# ========================================================================
# SYNC WRAPPER (für nicht-async Kontexte)
# ========================================================================

class MiniMaxClientSync:
    """
    Synchroner Wrapper für MiniMax Client.
    Für Agent-Tools die keine async unterstützen.
    """

    def __init__(self, **kwargs):
        self._async_client = MiniMaxClient(**kwargs)

    def generate_image(self, prompt: str, **kwargs):
        return asyncio.run(self._async_client.generate_image(prompt, **kwargs))

    def generate_image_to_file(self, prompt: str, output_path: str, **kwargs):
        return asyncio.run(self._async_client.generate_image_to_file(prompt, output_path, **kwargs))

    def generate_video(self, prompt: str, **kwargs):
        return asyncio.run(self._async_client.generate_video(prompt, **kwargs))

    def generate_video_to_file(self, prompt: str, output_path: str, **kwargs):
        return asyncio.run(self._async_client.generate_video_to_file(prompt, output_path, **kwargs))

    def generate_music(self, prompt: str, **kwargs):
        return asyncio.run(self._async_client.generate_music(prompt, **kwargs))

    def generate_music_to_file(self, prompt: str, output_path: str, **kwargs):
        return asyncio.run(self._async_client.generate_music_to_file(prompt, output_path, **kwargs))

    def text_to_speech(self, text: str, **kwargs):
        return asyncio.run(self._async_client.text_to_speech(text, **kwargs))

    def text_to_speech_to_file(self, text: str, output_path: str, **kwargs):
        return asyncio.run(self._async_client.text_to_speech_to_file(text, output_path, **kwargs))

    def speech_to_text(self, audio_data: bytes, **kwargs):
        return asyncio.run(self._async_client.speech_to_text(audio_data, **kwargs))

    def speech_to_text_from_file(self, audio_path: str, **kwargs):
        return asyncio.run(self._async_client.speech_to_text_from_file(audio_path, **kwargs))

    def analyze_image(self, image_url: str, prompt: str, **kwargs):
        return asyncio.run(self._async_client.analyze_image(image_url, prompt, **kwargs))


# ========================================================================
# EXPORTS
# ========================================================================

__all__ = [
    "MiniMaxClient",
    "MiniMaxClientSync",
    "MiniMaxModel",
    "MediaFormat"
]