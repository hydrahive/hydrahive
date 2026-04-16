"""
MiniMax MCP Server für HydraHive
================================
Model Context Protocol Server für MiniMax Multimodal-Fähigkeiten.

Dieser Server implementiert den MCP Standard und kann direkt in HydraHive
über /mcp/servers eingebunden werden.

Verwendung in HydraHive:
{
    "id": "minimax-multimodal",
    "name": "MiniMax Media Tools",
    "transport": "streamableHttp",
    "url": "http://127.0.0.1:8182/mcp"
}

Starten:
    python -m hydrahive_core.minimax_mcp_server --port 8182
"""

import json
import asyncio
import argparse
from typing import Any, Optional
from dataclasses import dataclass, field
from enum import Enum
from aiohttp import web
from aiohttp.web import Request, Response

from .minimax_client import MiniMaxClient, MiniMaxClientSync


# ========================================================================
# MCP PROTOCOL TYPES
# ========================================================================

class MCPMethod(Enum):
    """MCP JSON-RPC Methoden"""
    INITIALIZE = "initialize"
    TOOLS_LIST = "tools/list"
    TOOLS_CALL = "tools/call"
    RESOURCES_LIST = "resources/list"
    RESOURCES_READ = "resources/read"
    PROMPTS_LIST = "prompts/list"


@dataclass
class MCPRequest:
    """MCP Request Wrapper"""
    method: str
    params: dict = field(default_factory=dict)
    id: Optional[Any] = None


@dataclass
class MCPResponse:
    """MCP Response Wrapper"""
    result: Any = None
    error: Optional[dict] = None
    id: Optional[Any] = None


# ========================================================================
# TOOL DEFINITIONS (MCP manifest)
# ========================================================================

TOOLS_MANIFEST = {
    "tools": [
        # ========== IMAGE ==========
        {
            "name": "minimax_image_generate",
            "description": "Generiert Bilder basierend auf Text-Beschreibung. Unterstützt verschiedene Stile und Größen.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detaillierte Bildbeschreibung (Englisch empfohlen)"
                    },
                    "size": {
                        "type": "string",
                        "description": "Bildgröße",
                        "enum": ["512x512", "768x768", "1024x1024", "1024x768", "768x1024"],
                        "default": "1024x1024"
                    },
                    "n": {
                        "type": "integer",
                        "description": "Anzahl Bilder (1-4)",
                        "default": 1,
                        "minimum": 1,
                        "maximum": 4
                    }
                },
                "required": ["prompt"]
            }
        },

        # ========== VIDEO ==========
        {
            "name": "minimax_video_generate",
            "description": "Generiert Videos basierend auf Text-Beschreibung. Unterstützt 6 oder 10 Sekunden Clips.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Videobeschreibung mit Bewegung und Szene"
                    },
                    "duration": {
                        "type": "integer",
                        "description": "Videolänge in Sekunden",
                        "enum": [6, 10],
                        "default": 6
                    },
                    "resolution": {
                        "type": "string",
                        "description": "Videoauflösung",
                        "enum": ["1280x720", "1920x1080"],
                        "default": "1280x720"
                    },
                    "wait": {
                        "type": "boolean",
                        "description": "Auf Fertigstellung warten (kann Minuten dauern)",
                        "default": true
                    }
                },
                "required": ["prompt"]
            }
        },

        # ========== MUSIC ==========
        {
            "name": "minimax_music_generate",
            "description": "Generiert Musik basierend auf Text-Beschreibung. Kann Genre, Stimmung und Instrumente angeben.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Musikbeschreibung (Genre, Stimmung, Tempo, Instrumente)"
                    },
                    "title": {
                        "type": "string",
                        "description": "Optionaler Titel für das Musikstück"
                    },
                    "style": {
                        "type": "string",
                        "description": "Musikstil",
                        "enum": ["pop", "rock", "jazz", "classical", "electronic", "acoustic", "ambient"],
                        "default": "pop"
                    },
                    "wait": {
                        "type": "boolean",
                        "description": "Auf Fertigstellung warten",
                        "default": true
                    }
                },
                "required": ["prompt"]
            }
        },

        # ========== TTS ==========
        {
            "name": "minimax_text_to_speech",
            "description": "Konvertiert Text zu Sprache mit natürlich klingender Stimme.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text der gesprochen werden soll"
                    },
                    "voice": {
                        "type": "string",
                        "description": "Stimmen-ID",
                        "enum": ["male-qn-qingse", "female-shaonv", "male-yunyang", "female-xiaoniu"],
                        "default": "male-qn-qingse"
                    },
                    "speed": {
                        "type": "number",
                        "description": "Sprechgeschwindigkeit (0.5 - 2.0)",
                        "default": 1.0,
                        "minimum": 0.5,
                        "maximum": 2.0
                    },
                    "output_file": {
                        "type": "string",
                        "description": "Zielpfad für Audio-Datei (MP3)"
                    }
                },
                "required": ["text"]
            }
        },

        # ========== STT ==========
        {
            "name": "minimax_speech_to_text",
            "description": "Transkribiert gesprochene Sprache zu Text.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "audio_file": {
                        "type": "string",
                        "description": "Pfad zur Audio-Datei (MP3, WAV, etc.)"
                    },
                    "language": {
                        "type": "string",
                        "description": "Sprache oder 'auto' für automatische Erkennung",
                        "default": "auto"
                    }
                },
                "required": ["audio_file"]
            }
        },

        # ========== VISION ==========
        {
            "name": "minimax_analyze_image",
            "description": "Analysiert ein Bild und beantwortet Fragen dazu.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "image_url": {
                        "type": "string",
                        "description": "URL oder Pfad zum Bild"
                    },
                    "question": {
                        "type": "string",
                        "description": "Frage zum Bild"
                    }
                },
                "required": ["image_url", "question"]
            }
        },

        # ========== QUOTA ==========
        {
            "name": "minimax_check_quota",
            "description": "Zeigt aktuelle API-Nutzung und verfügbare Credits.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        }
    ]
}


# ========================================================================
# MCP SERVER IMPLEMENTATION
# ========================================================================

class MiniMaxMCPServer:
    """
    MCP Server für MiniMax Multimodal API.
    Implementiert den MCP Standard (streamableHttp Transport).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        output_dir: str = "/tmp/hydrahive-media"
    ):
        """
        Args:
            api_key: MiniMax API Key (oder MINIMAX_API_KEY env)
            base_url: Optionaler Endpoint-Override
            output_dir: Verzeichnis für generierte Medien-Dateien
        """
        self.api_key = api_key
        self.base_url = base_url
        self.output_dir = output_dir
        self.client: Optional[MiniMaxClient] = None
        self._initialized = False

        # Output-Dir erstellen
        import os
        os.makedirs(output_dir, exist_ok=True)

    async def initialize(self):
        """Lazy Initialization des API Clients"""
        if self.client is None:
            self.client = MiniMaxClient(
                api_key=self.api_key,
                base_url=self.base_url
            )
        self._initialized = True

    async def handle_request(self, request: MCPRequest) -> MCPResponse:
        """Verarbeitet MCP Request"""
        try:
            method = request.method

            if method == MCPMethod.INITIALIZE.value:
                return await self._handle_initialize(request)
            elif method == MCPMethod.TOOLS_LIST.value:
                return await self._handle_tools_list(request)
            elif method == MCPMethod.TOOLS_CALL.value:
                return await self._handle_tools_call(request)
            elif method == MCPMethod.RESOURCES_LIST.value:
                return await self._handle_resources_list(request)
            else:
                return MCPResponse(
                    error={"code": -32601, "message": f"Method not found: {method}"},
                    id=request.id
                )
        except Exception as e:
            return MCPResponse(
                error={"code": -32603, "message": str(e)},
                id=request.id
            )

    async def _handle_initialize(self, request: MCPRequest) -> MCPResponse:
        """Protocol Initialization"""
        return MCPResponse(
            result={
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": True},
                    "resources": {}
                },
                "serverInfo": {
                    "name": "hydrahive-minimax-mcp",
                    "version": "1.0.0",
                    "description": "MiniMax Multimodal MCP Server for HydraHive"
                }
            },
            id=request.id
        )

    async def _handle_tools_list(self, request: MCPRequest) -> MCPResponse:
        """Liste aller verfügbaren Tools"""
        return MCPResponse(
            result={"tools": TOOLS_MANIFEST["tools"]},
            id=request.id
        )

    async def _handle_tools_call(self, request: MCPRequest) -> MCPResponse:
        """Tool-Aufruf ausführen"""
        params = request.params
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        await self.initialize()

        if tool_name == "minimax_image_generate":
            result = await self._tool_image_generate(arguments)
        elif tool_name == "minimax_video_generate":
            result = await self._tool_video_generate(arguments)
        elif tool_name == "minimax_music_generate":
            result = await self._tool_music_generate(arguments)
        elif tool_name == "minimax_text_to_speech":
            result = await self._tool_text_to_speech(arguments)
        elif tool_name == "minimax_speech_to_text":
            result = await self._tool_speech_to_text(arguments)
        elif tool_name == "minimax_analyze_image":
            result = await self._tool_analyze_image(arguments)
        elif tool_name == "minimax_check_quota":
            result = await self._tool_check_quota(arguments)
        else:
            raise ValueError(f"Unknown tool: {tool_name}")

        return MCPResponse(
            result={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, indent=2, ensure_ascii=False)
                    }
                ],
                "isError": False
            },
            id=request.id
        )

    async def _handle_resources_list(self, request: MCPRequest) -> MCPResponse:
        """Liste aller Resources (generierte Medien)"""
        import os
        files = []
        if os.path.exists(self.output_dir):
            for f in os.listdir(self.output_dir):
                files.append({
                    "uri": f"file://{self.output_dir}/{f}",
                    "name": f,
                    "mimeType": self._get_mime_type(f)
                })

        return MCPResponse(
            result={"resources": files},
            id=request.id
        )

    # ====================================================================
    # TOOL IMPLEMENTATIONS
    # ====================================================================

    async def _tool_image_generate(self, args: dict) -> dict:
        """Bild-Generierung Tool"""
        import uuid
        from pathlib import Path

        prompt = args["prompt"]
        size = args.get("size", "1024x1024")
        n = args.get("n", 1)

        urls = await self.client.generate_image(
            prompt=prompt,
            size=size,
            n=n
        )

        files = []
        for i, url in enumerate(urls):
            filename = f"image_{uuid.uuid4().hex[:8]}.png"
            filepath = Path(self.output_dir) / filename

            # Download
            import httpx
            async with httpx.AsyncClient() as c:
                resp = await c.get(url)
                resp.raise_for_status()
                filepath.write_bytes(resp.content)

            files.append({
                "url": url,
                "file": str(filepath),
                "size": size,
                "index": i
            })

        return {
            "success": True,
            "count": len(files),
            "images": files
        }

    async def _tool_video_generate(self, args: dict) -> dict:
        """Video-Generierung Tool"""
        import uuid
        from pathlib import Path

        prompt = args["prompt"]
        duration = args.get("duration", 6)
        resolution = args.get("resolution", "1280x720")
        wait = args.get("wait", True)

        result = await self.client.generate_video(
            prompt=prompt,
            duration=duration,
            resolution=resolution,
            wait_for_completion=wait,
            timeout=600.0
        )

        if result.get("status") == "completed" and result.get("video_url"):
            filename = f"video_{uuid.uuid4().hex[:8]}.mp4"
            filepath = Path(self.output_dir) / filename

            import httpx
            async with httpx.AsyncClient() as c:
                resp = await c.get(result["video_url"])
                resp.raise_for_status()
                filepath.write_bytes(resp.content)

            result["local_file"] = str(filepath)

        return result

    async def _tool_music_generate(self, args: dict) -> dict:
        """Musik-Generierung Tool"""
        import uuid
        from pathlib import Path

        prompt = args["prompt"]
        title = args.get("title")
        style = args.get("style", "pop")
        wait = args.get("wait", True)

        result = await self.client.generate_music(
            prompt=prompt,
            title=title,
            style=style,
            wait_for_completion=wait,
            timeout=300.0
        )

        if result.get("status") == "completed" and result.get("audio_url"):
            filename = f"music_{uuid.uuid4().hex[:8]}.mp3"
            filepath = Path(self.output_dir) / filename

            import httpx
            async with httpx.AsyncClient() as c:
                resp = await c.get(result["audio_url"])
                resp.raise_for_status()
                filepath.write_bytes(resp.content)

            result["local_file"] = str(filepath)

        return result

    async def _tool_text_to_speech(self, args: dict) -> dict:
        """TTS Tool"""
        import uuid
        from pathlib import Path

        text = args["text"]
        voice = args.get("voice", "male-qn-qingse")
        speed = args.get("speed", 1.0)

        audio_data = await self.client.text_to_speech(
            text=text,
            voice=voice,
            speed=speed
        )

        filename = f"tts_{uuid.uuid4().hex[:8]}.mp3"
        filepath = Path(self.output_dir) / filename
        filepath.write_bytes(audio_data)

        return {
            "success": True,
            "text_length": len(text),
            "voice": voice,
            "speed": speed,
            "file": str(filepath),
            "duration_seconds": len(audio_data) / 32000  # Approx
        }

    async def _tool_speech_to_text(self, args: dict) -> dict:
        """STT Tool"""
        from pathlib import Path

        audio_file = args["audio_file"]
        language = args.get("language", "auto")

        text = await self.client.speech_to_text_from_file(
            audio_path=audio_file,
            language=language
        )

        return {
            "success": True,
            "text": text,
            "language_detected": language,
            "chars": len(text)
        }

    async def _tool_analyze_image(self, args: dict) -> dict:
        """Vision Tool"""
        image_url = args["image_url"]
        question = args["question"]

        result = await self.client.analyze_image(
            image_url=image_url,
            prompt=question
        )

        return {
            "success": True,
            "analysis": result,
            "question": question
        }

    async def _tool_check_quota(self, args: dict) -> dict:
        """Quota Check Tool"""
        quota = await self.client.check_quota()
        return {"success": True, "quota": quota}

    def _get_mime_type(self, filename: str) -> str:
        """MIME Type aus Dateiendung"""
        ext = filename.rsplit(".", 1)[-1].lower()
        types = {
            "mp4": "video/mp4",
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "webp": "image/webp"
        }
        return types.get(ext, "application/octet-stream")


# ========================================================================
# HTTP HANDLER (streamableHttp Transport)
# ========================================================================

async def handle_mcp_request(request: Request) -> Response:
    """HTTP Handler für MCP Requests"""
    # Server aus App-State holen
    server: MiniMaxMCPServer = request.app["mcp_server"]

    # Request parsen
    body = await request.json()

    # Single oder Batch Request
    if isinstance(body, list):
        results = []
        for req in body:
            mcp_req = MCPRequest(
                method=req.get("method", ""),
                params=req.get("params", {}),
                id=req.get("id")
            )
            results.append(await server.handle_request(mcp_req))

        # Response
        return web.json_response([
            {"result": r.result, "error": r.error, "id": r.id}
            for r in results
        ])
    else:
        mcp_req = MCPRequest(
            method=body.get("method", ""),
            params=body.get("params", {}),
            id=body.get("id")
        )

        mcp_resp = await server.handle_request(mcp_req)

        return web.json_response({
            "result": mcp_resp.result,
            "error": mcp_resp.error,
            "id": mcp_resp.id
        })


async def handle_sse(request: Request) -> Response:
    """SSE Endpoint für Streaming Responses (optional)"""
    server: MiniMaxMCPServer = request.app["mcp_server"]

    # Stream-Response für lange Operationen
    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={"Content-Type": "text/event-stream"}
    )

    await response.prepare(request)

    # Polling für Status-Updates
    async def status_generator():
        import asyncio
        import json
        count = 0
        while count < 10:
            data = json.dumps({
                "type": "status",
                "message": f"Processing... ({count})"
            })
            await response.write(f"data: {data}\n\n".encode())
            await asyncio.sleep(1)
            count += 1

        await response.write(f"data: {json.dumps({'type': 'done'})}\n\n".encode())

    asyncio.create_task(status_generator())
    return response


# ========================================================================
# APP FACTORY
# ========================================================================

def create_app(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    output_dir: str = "/tmp/hydrahive-media"
) -> web.Application:
    """Erstellt aiohttp Application mit MCP Endpoints"""
    import os

    app = web.Application()

    # Server Instance
    server = MiniMaxMCPServer(
        api_key=api_key or os.environ.get("MINIMAX_API_KEY"),
        base_url=base_url,
        output_dir=output_dir
    )
    app["mcp_server"] = server

    # Routes
    app.router.add_post("/mcp", handle_mcp_request)
    app.router.add_get("/mcp/stream", handle_sse)
    app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))

    return app


# ========================================================================
# CLI ENTRY POINT
# ========================================================================

def main():
    parser = argparse.ArgumentParser(description="MiniMax MCP Server für HydraHive")
    parser.add_argument("--port", type=int, default=8182, help="Port (default: 8182)")
    parser.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    parser.add_argument("--api-key", help="MiniMax API Key")
    parser.add_argument("--base-url", help="MiniMax Base URL Override")
    parser.add_argument("--output-dir", default="/tmp/hydrahive-media", help="Output Directory")

    args = parser.parse_args()

    app = create_app(
        api_key=args.api_key,
        base_url=args.base_url,
        output_dir=args.output_dir
    )

    print(f"🟢 MiniMax MCP Server gestartet auf http://{args.host}:{args.port}")
    print(f"   Endpoint: http://{args.host}:{args.port}/mcp")
    print(f"   Output: {args.output_dir}")

    web.run_app(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()


__all__ = ["MiniMaxMCPServer", "create_app", "main"]