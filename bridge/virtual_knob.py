"""Virtual knob: the desktop simulator instead of BLE.

The bridge listens on a Unix socket; the simulator (`SIM_BRIDGE=<path>`)
connects and exchanges exactly the packets the GATT characteristics would
carry. ``SocketClient`` looks like the slice of BleakClient the bridge uses
(write_gatt_char / start_notify / is_connected / disconnect / mtu_size), so
the same session loop drives hardware and simulator.

Packet on the stream: channel (1 byte) + length (u16 little endian) + bytes.
    R  bridge -> knob   a write to RX (…0002)
    T  knob -> bridge   a notify on TX (…0003)
    Q  knob -> bridge   a notify on REQ (…0004)
"""
from __future__ import annotations

import asyncio
import os
import struct
from pathlib import Path
from typing import Awaitable, Callable

DEFAULT_SOCKET = Path.home() / ".config" / "ai-knob" / "knob.sock"
MTU = 185                       # what macOS negotiates with the real board

_CHANNELS = {"0002": b"R", "0003": b"T", "0004": b"Q"}


def _channel(uuid: str) -> bytes:
    ch = _CHANNELS.get(str(uuid).lower()[-4:])
    if ch is None:
        raise ValueError(f"unknown characteristic {uuid}")
    return ch


def pack(ch: bytes, data: bytes) -> bytes:
    return ch + struct.pack("<H", len(data)) + data


class SocketClient:
    mtu_size = MTU

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 log: Callable[[str], None] = print) -> None:
        self._reader, self._writer, self.log = reader, writer, log
        self._handlers: dict[bytes, Callable] = {}
        self.is_connected = True
        self.closed = asyncio.Event()
        self._task = asyncio.create_task(self._read_loop())

    async def start_notify(self, uuid: str, callback: Callable) -> None:
        self._handlers[_channel(uuid)] = callback

    async def write_gatt_char(self, uuid: str, data: bytes, response: bool = False) -> None:
        if _channel(uuid) != b"R":
            raise ValueError("only RX is writable")
        if not self.is_connected:
            raise ConnectionError("virtual knob gone")
        self._writer.write(pack(b"R", bytes(data)))
        await self._writer.drain()

    async def disconnect(self) -> None:
        self.is_connected = False
        try:
            self._writer.close()
        except OSError:
            pass
        self._task.cancel()
        self.closed.set()

    async def _read_loop(self) -> None:
        try:
            while True:
                head = await self._reader.readexactly(3)
                n = struct.unpack("<H", head[1:])[0]
                data = await self._reader.readexactly(n)
                cb = self._handlers.get(head[:1])
                if cb:
                    cb(None, bytearray(data))
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            self.is_connected = False
            self.closed.set()


async def serve(path: Path, run_session: Callable[..., Awaitable[bool]],
                stop_event: asyncio.Event, log: Callable[[str], None] = print) -> None:
    """Accept one simulator at a time and run the session loop on it."""
    busy = asyncio.Lock()

    async def on_conn(reader, writer):
        if busy.locked():
            log("Virtual knob: second simulator refused")
            writer.close()
            return
        async with busy:
            log("Virtual knob connected")
            client = SocketClient(reader, writer, log)
            try:
                await run_session(client, stop_event)
            finally:
                await client.disconnect()
            log("Virtual knob disconnected")

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    server = await asyncio.start_unix_server(on_conn, path=str(path))
    os.chmod(path, 0o600)
    log(f"Waiting for the simulator: SIM_BRIDGE={path}")
    try:
        await stop_event.wait()
    finally:
        server.close()
        await server.wait_closed()
