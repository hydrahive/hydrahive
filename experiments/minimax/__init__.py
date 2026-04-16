"""
MiniMax Integration Package
HydraHive Multimodal Support
"""

from .minimax_client import MiniMaxClient, MiniMaxClientSync, MiniMaxModel, MediaFormat
from .minimax_tools import (
    MiniMaxToolConfig,
    ToolRegistry,
    ImageGenTool,
    VideoGenTool,
    MusicGenTool,
    TTSTool,
    STTTool,
    VisionTool,
    MiniMaxToolFactory,
    get_tools_for_agent
)

__all__ = [
    # Client
    "MiniMaxClient",
    "MiniMaxClientSync",
    "MiniMaxModel",
    "MediaFormat",
    # Tools
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
]

__version__ = "1.0.0"