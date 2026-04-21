"""Gemeinsame Wyoming-Protokoll-Helpers für STT/TTS-Provider.

Wyoming ist ein zeilenbasiertes JSON-Protokoll:
Header (JSON) + optionaler Daten-Block (JSON) + optionaler Binary-Payload.
"""
from __future__ import annotations

import asyncio
import json


async def open_connection(
    host: str, port: int, *, timeout: float = 10
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=timeout
    )


async def send_event(
    writer: asyncio.StreamWriter,
    etype: str,
    data: dict | None = None,
    payload: bytes = b"",
) -> None:
    header: dict = {"type": etype}
    data_bytes = b""
    if data:
        data_bytes = json.dumps(data, separators=(",", ":")).encode("utf-8")
        header["data_length"] = len(data_bytes)
    if payload:
        header["payload_length"] = len(payload)
    writer.write(json.dumps(header, separators=(",", ":")).encode("utf-8") + b"\n")
    if data_bytes:
        writer.write(data_bytes)
    if payload:
        writer.write(payload)
    await writer.drain()


async def recv_event(
    reader: asyncio.StreamReader,
) -> tuple[str, dict, bytes]:
    line = await asyncio.wait_for(reader.readline(), timeout=60)
    if not line:
        raise ConnectionError("Wyoming connection closed")
    header = json.loads(line.decode("utf-8"))
    etype = header.get("type", "")
    data: dict = {}
    data_length = header.get("data_length", 0)
    if data_length > 0:
        data_raw = await asyncio.wait_for(
            reader.readexactly(data_length), timeout=30
        )
        data = json.loads(data_raw)
    payload = b""
    payload_length = header.get("payload_length", 0)
    if payload_length > 0:
        payload = await asyncio.wait_for(
            reader.readexactly(payload_length), timeout=30
        )
    return etype, data, payload


async def probe_tcp(host: str, port: int, *, timeout: float = 3) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except Exception:
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True
