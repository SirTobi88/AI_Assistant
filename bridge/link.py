"""The BLE side of protocol v1: typed messages out (framed), commands in."""
from __future__ import annotations

import asyncio
from typing import Callable

try:
    from . import protocol
except ImportError:  # run as a script
    import protocol

RX_CHAR_UUID = "41494b6e-6f62-4272-6964-676500000002"
TX_CHAR_UUID = "41494b6e-6f62-4272-6964-676500000003"


class KnobLink:
    """Wraps a connected BleakClient. ``lock`` serialises every write to RX —
    the usage payloads too: a bare write landing between two frames of a
    framed message would make the knob drop that message."""

    def __init__(self, client, on_command: Callable[[protocol.Command], None],
                 log: Callable[[str], None] = print) -> None:
        self.client = client
        self.on_command = on_command
        self.log = log
        self.lock = asyncio.Lock()
        self._rx = protocol.Reassembler()
        self._msg_id = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    def chunk_size(self) -> int:
        mtu = getattr(self.client, "mtu_size", 0) or 0
        return max(20, min(protocol.DEFAULT_CHUNK, mtu - 3)) if mtu else protocol.DEFAULT_CHUNK

    async def start(self) -> bool:
        self._loop = asyncio.get_running_loop()
        try:
            # Bounded like the refresh subscription: CoreBluetooth may never
            # confirm the CCCD write on a half-open link.
            await asyncio.wait_for(self.client.start_notify(TX_CHAR_UUID, self._on_notify), timeout=10)
            return True
        except Exception as e:  # BleakError, ValueError, TimeoutError
            self.log(f"Command channel unavailable: {e!r}")
            return False

    def _on_notify(self, _char, data: bytearray) -> None:
        msg = self._rx.feed(bytes(data))
        cmd = protocol.parse_command(msg)
        if cmd is None:
            return
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.on_command, cmd)
        else:
            self.on_command(cmd)

    async def send(self, msg: dict) -> bool:
        self._msg_id = (self._msg_id + 1) & 0xFF
        writes = protocol.frame(msg, self._msg_id, self.chunk_size())
        async with self.lock:
            try:
                for w in writes:
                    # With response: frames must arrive in order and none may be lost.
                    await self.client.write_gatt_char(RX_CHAR_UUID, w, response=True)
            except Exception as e:
                self.log(f"Send failed ({msg.get('t')}): {e!r}")
                return False
        return True
