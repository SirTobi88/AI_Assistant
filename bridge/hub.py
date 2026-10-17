"""Bridge state between Claude Code (hooks, sessions) and the knob.

* A Unix socket (knob_hook.py talks to it) turns hook requests into knob
  prompts and waits for the answer.
* Session list, options and monitor values are pushed to the knob whenever
  they change and again on every (re)connect.
* Commands from the knob go to the adapter (Claude Code CLI / desktop app).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import time
from pathlib import Path
from typing import Protocol as _Protocol

try:
    from . import protocol, voice
    from .knob_hook import SOCKET_PATH
except ImportError:  # run as a script
    import protocol
    import voice
    from knob_hook import SOCKET_PATH

DEFAULT_MODELS = ["fable", "opus", "sonnet", "haiku"]
DEFAULT_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
DEFAULT_MODES = ["auto", "default", "acceptEdits", "plan"]   # the desktop app's menu order


class Adapter(_Protocol):
    """What the knob can make a Claude session do (control.KeystrokeAdapter).
    Gets the session as it was before the change; returns whether it worked."""
    def switch(self, session: protocol.Session) -> bool: ...
    def set(self, session: protocol.Session, key: str, value) -> bool: ...
    def interrupt(self, session: protocol.Session) -> bool: ...


class LoggingAdapter:
    """Control switched off: say what would have happened."""
    def __init__(self, log=print) -> None:
        self.log = log

    def switch(self, session) -> bool:
        self.log(f"control off: would show {session.label}")
        return False

    def set(self, session, key: str, value) -> bool:
        self.log(f"control off: would set {key}={value} for {session.label}")
        return False

    def interrupt(self, session) -> bool:
        self.log(f"control off: would interrupt {session.label}")
        return False

    def prompt(self, session, text: str) -> bool:
        self.log(f"control off: would send {text!r} to {session.label}")
        return False

    def fill(self, session, text: str) -> bool:
        self.log(f"control off: would put {text!r} in {session.label}'s prompt box")
        return False


class Hub:
    def __init__(self, adapter: Adapter | None = None, log=print,
                 socket_path: Path = SOCKET_PATH) -> None:
        self.log = log
        self.adapter = adapter or LoggingAdapter(log)
        self.socket_path = socket_path
        self.link = None
        self.sessions: list[protocol.Session] = []
        self.active: str | None = None
        self.monitor: dict | None = None
        self.options = protocol.options_msg(DEFAULT_MODELS, DEFAULT_EFFORTS, DEFAULT_MODES)
        self._pending: dict[str, tuple[protocol.Prompt, asyncio.Future]] = {}
        self._ids = itertools.count(1)
        self._server: asyncio.AbstractServer | None = None
        self._last_sessions_msg: dict | None = None
        # push-to-talk: the take being recorded, transcripts waiting on the knob
        self.voice_cfg: dict = {}
        self._take: voice.Take | None = None
        self._stt: dict[str, str] = {}
        self._stt_ids = itertools.count(1)

    # ---- lifecycle

    async def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        self._server = await asyncio.start_unix_server(self._serve, path=str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        self.log(f"Hub listening on {self.socket_path}")

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        for _, fut in self._pending.values():
            if not fut.done():
                fut.set_result(None)

    async def attach(self, link) -> None:
        """A knob connected: give it the full picture."""
        self.link = link
        await self._send(self.options)
        self._last_sessions_msg = None
        await self.publish_sessions()
        if self.monitor:
            await self._send(self.monitor)
        for prompt, fut in list(self._pending.values()):
            if not fut.done():
                await self._send(prompt.msg())

    def detach(self) -> None:
        self.link = None

    @property
    def connected(self) -> bool:
        return self.link is not None

    async def _send(self, msg: dict) -> bool:
        return bool(self.link) and await self.link.send(msg)

    # ---- sessions / monitor

    async def set_sessions(self, sessions: list[protocol.Session], active: str | None = None) -> None:
        self.sessions = sessions
        ids = {s.sid for s in sessions}
        if active:
            self.active = active
        if self.active not in ids:
            self.active = sessions[0].sid if sessions else None
        await self.publish_sessions()

    async def publish_sessions(self) -> None:
        msg = protocol.sessions_msg(self.sessions, self.active)
        if msg != self._last_sessions_msg and await self._send(msg):
            self._last_sessions_msg = msg

    async def set_monitor(self, monitor: dict) -> None:
        if monitor != self.monitor:
            self.monitor = monitor
            await self._send(monitor)

    # ---- prompts

    async def ask(self, prompt: protocol.Prompt) -> list[int] | None:
        """Show a prompt on the knob; the chosen indices, or None."""
        if not self.connected:
            return None
        fut = asyncio.get_running_loop().create_future()
        self._pending[prompt.id] = (prompt, fut)
        try:
            if not await self._send(prompt.msg()):
                return None
            return await asyncio.wait_for(fut, timeout=prompt.timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending.pop(prompt.id, None)
            answered = fut.done() and not fut.cancelled() and fut.result() is not None
            if not answered:            # the knob still shows it: take it back
                await self._send(protocol.prompt_done_msg(prompt.id))

    def resolve_sid(self, short: str) -> str | None:
        for s in self.sessions:
            if protocol.short_sid(s.sid) == short:
                return s.sid
        return None

    def on_command(self, cmd: protocol.Command) -> None:
        a = dict(cmd.args)
        if "sid" in a:
            full = self.resolve_sid(a["sid"])
            if full is None:
                self.log(f"Knob command {cmd.t} for unknown session {a['sid']}")
                return
            a["sid"] = full
        if cmd.t == "answer":
            entry = self._pending.get(a["id"])
            if entry and not entry[1].done():
                prompt = entry[0]
                if all(c < len(prompt.options) for c in a["choices"]):
                    entry[1].set_result(a["choices"])
            return
        session = next((s for s in self.sessions if s.sid == a.get("sid")), None)
        if cmd.t == "switch":
            self.active = a["sid"]
            self.adapter.switch(session)
            asyncio.ensure_future(self.publish_sessions())
            return
        if cmd.t == "set":
            # The adapter sees the old values (mode cycle, fast toggle); the
            # list shows the new one right away and the next hook event confirms.
            if self.adapter.set(session, a["key"], a["value"]):
                setattr(session, a["key"], a["value"])
            # Resend either way: on failure this undoes the knob's optimistic change.
            self._last_sessions_msg = None
            asyncio.ensure_future(self.publish_sessions())
            return
        if cmd.t == "interrupt":
            self.adapter.interrupt(session)
            return
        if cmd.t == "menu":
            menu = getattr(self.adapter, "menu", None)
            if session is not None and menu is not None:
                menu(session, a["key"], a["open"])
            return
        if cmd.t == "hello":
            self.log(f"Knob says hello: protocol {a.get('v')}, firmware {a.get('fw')}")
            return
        if cmd.t == "ptt":
            if a["on"]:
                self._take = voice.Take()
            elif self._take is not None:
                asyncio.ensure_future(self._finish_take(self._take))
            return
        if cmd.t == "stt_send":
            text = self._stt.pop(a["id"], None)
            target = next((s for s in self.sessions if s.sid == self.active), None)
            prompt = getattr(self.adapter, "prompt", None)
            if text and target is not None and prompt is not None:
                prompt(target, text)
            elif text:
                self.log("Voice: no session to send the text to")
            return
        if cmd.t == "stt_edit":
            # Into the session's prompt box, to finish it on the computer.
            text = self._stt.pop(a["id"], None)
            target = next((s for s in self.sessions if s.sid == self.active), None)
            fill = getattr(self.adapter, "fill", None)
            if text and target is not None and fill is not None:
                fill(target, text)
            elif text:
                self.log("Voice: no session to put the text in")
            return
        if cmd.t == "stt_drop":
            self._stt.pop(a["id"], None)
            return
        self.log(f"Knob command {cmd.t} {a} (not handled yet)")

    # ---- push-to-talk

    def on_audio(self, frame: bytes) -> None:
        if self._take is not None:
            self._take.feed(frame)

    async def _finish_take(self, take: voice.Take) -> None:
        await asyncio.sleep(0.3)            # frames still on their way after "ptt off"
        if self._take is take:
            self._take = None
        samples = take.samples()
        t0 = time.monotonic()
        try:
            text = await asyncio.get_running_loop().run_in_executor(
                None, voice.transcribe, samples, self.voice_cfg)
        except Exception as e:  # whisper failed, timed out, missing
            self.log(f"Voice: transcription failed: {e}")
            text = ""
        self.log(f"Voice: {len(samples) / voice.SAMPLE_RATE:.1f} s -> {text!r} "
                 f"({time.monotonic() - t0:.1f} s)")
        stt_id = f"v{next(self._stt_ids)}"
        if text:
            self._stt = {stt_id: text}     # only the newest take can be sent
            await self._send(protocol.stt_msg(stt_id, text, True))
        else:
            await self._send(protocol.stt_msg(stt_id, "(nichts verstanden)", True))

    async def ask_request(self, req: dict) -> list[int] | None:
        """A prompt as knob_hook.py / the mod endpoint describe it -> the knob's choices."""
        prompt = protocol.Prompt(
            id=f"p{next(self._ids)}", sid=str(req.get("sid", "")),
            kind=str(req.get("kind", "ask")), title=str(req.get("title", "")),
            text=str(req.get("text", "")), options=[str(o) for o in req.get("options", [])],
            multi=bool(req.get("multi")), confirm=tuple(int(i) for i in req.get("confirm", [])),
            timeout=int(req.get("timeout", 30)))
        t0 = time.monotonic()
        choices = await self.ask(prompt)
        self.log(f"Prompt {prompt.id} ({prompt.kind} {prompt.title}): "
                 f"{choices} after {time.monotonic() - t0:.1f}s")
        return choices

    # ---- Unix socket for knob_hook.py

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        reply: dict = {"choices": None}
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            req = json.loads(line.decode() or "{}")
            if req.get("op") == "ask":
                reply["choices"] = await self.ask_request(req)
        except (ValueError, asyncio.TimeoutError, ConnectionError) as e:
            self.log(f"Hub request failed: {e!r}")
        try:
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
            writer.close()
        except (ConnectionError, OSError):
            pass
