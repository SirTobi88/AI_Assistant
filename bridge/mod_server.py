"""The bridge's endpoint for the `ai-knob` Claude Code mod (mod/ai-knob).

Each Claude session that runs the mod talks HTTP/1.1 to this Unix socket via
`$.http.fetch(url, { socketPath })` — one short request per call, `Connection:
close`, no keep-alive. Stdlib only.

    POST /v1/hello     {sid, cwd, title, entrypoint, version}        register
    POST /v1/state     {sid, state, model, effort, mode, fast,        report (on events
                        ctx_pct, tokens, rate_limits}                 and every 10 s)
    GET  /v1/commands?sid=…&wait=25                                   long poll -> {commands: […]}
    POST /v1/result    {sid, cmd, ok, error}                          a command's outcome
    POST /v1/ask       {sid, what, tool, input, suggestions}          blocks until the knob answers
    POST /v1/bye       {sid}

Commands for a session: {t:"set", key, value} | {t:"interrupt"} | {t:"prompt", text}.
`/v1/ask` answers in the shape the mod returns to Claude Code:
    what=permission -> {decision}  (PermissionRequest decision, or null)
    what=question   -> {answers}   (AskUserQuestion answers, or null)
    what=plan       -> {verdict}   (true / false, or null)
null always means: leave it to Claude Code's own dialog.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from . import prompts, protocol
    from .knob_activity import ALLOW_STALE_S, DONE_S, WORK_STALE_S
    from .sessions import short_model
except ImportError:  # run as a script
    import prompts
    import protocol
    from knob_activity import ALLOW_STALE_S, DONE_S, WORK_STALE_S
    from sessions import short_model

DEFAULT_SOCKET = Path.home() / ".config" / "ai-knob" / "mod.sock"
MAX_BODY = 256 * 1024
LONG_POLL_MAX = 30
GONE_AFTER_S = 60          # no heartbeat this long -> the session is gone
RATE_FRESH_S = 300

_DECAY = {protocol.THINKING: WORK_STALE_S, protocol.TOOL: WORK_STALE_S,
          protocol.COMPACTING: WORK_STALE_S, protocol.DONE: DONE_S,
          protocol.NEEDS_PERMISSION: ALLOW_STALE_S, protocol.ASKING: ALLOW_STALE_S}


@dataclass
class ModSession:
    sid: str
    cwd: str = ""
    title: str = ""
    entrypoint: str = ""
    state: int = protocol.IDLE
    state_ts: float = 0.0
    model: str = ""
    effort: str = ""
    mode: str = "default"
    fast: bool = False
    ctx_pct: int = -1
    tokens: int = -1
    rate_limits: list = field(default_factory=list)
    rate_ts: float = 0.0
    seen: float = 0.0
    commands: list = field(default_factory=list)
    pending: dict = field(default_factory=dict)       # key -> value queued, not yet confirmed
    wake: asyncio.Event = field(default_factory=asyncio.Event)

    def view(self, now: float) -> protocol.Session:
        state = self.state
        if now - self.state_ts > _DECAY.get(state, 10 ** 9):
            state = protocol.IDLE
        shown = {"model": short_model(self.model), "effort": self.effort, "mode": self.mode,
                 "fast": self.fast, **self.pending}
        label = Path(self.cwd).name if self.cwd else (self.title or self.sid[:8])
        return protocol.Session(
            sid=self.sid, label=label, state=state, ctx_pct=self.ctx_pct,
            model=str(shown["model"]), effort=str(shown["effort"]), mode=str(shown["mode"]),
            fast=bool(shown["fast"]), cwd=self.cwd, entrypoint=self.entrypoint)


class ModRegistry:
    """Sessions that run the mod, their command queues and their readings."""

    def __init__(self, log=print) -> None:
        self.log = log
        self.sessions: dict[str, ModSession] = {}

    def has(self, sid: str | None) -> bool:
        return bool(sid) and sid in self.sessions

    def get(self, sid: str) -> ModSession:
        s = self.sessions.get(sid)
        if s is None:
            s = self.sessions[sid] = ModSession(sid)
            self.log(f"Mod session {sid[:8]} connected")
        s.seen = time.time()
        return s

    def hello(self, body: dict) -> None:
        s = self.get(str(body["sid"]))
        for key in ("cwd", "title", "entrypoint"):
            if body.get(key):
                setattr(s, key, str(body[key]))

    def report(self, body: dict) -> None:
        s = self.get(str(body["sid"]))
        now = time.time()
        if isinstance(body.get("state"), int) and 0 <= body["state"] < 11:
            if body["state"] != s.state:
                s.state_ts = now
            s.state = body["state"]
        for key in ("model", "effort", "mode", "cwd", "title"):
            if isinstance(body.get(key), str) and body[key]:
                setattr(s, key, body[key])
        if isinstance(body.get("fast"), bool):
            s.fast = body["fast"]
        for key in ("ctx_pct", "tokens"):
            if isinstance(body.get(key), (int, float)):
                setattr(s, key, int(body[key]))
        if isinstance(body.get("rate_limits"), list) and body["rate_limits"]:
            s.rate_limits, s.rate_ts = body["rate_limits"], now

    def bye(self, sid: str) -> None:
        if self.sessions.pop(sid, None):
            self.log(f"Mod session {sid[:8]} gone")

    def prune(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        for sid in [sid for sid, s in self.sessions.items() if now - s.seen > GONE_AFTER_S]:
            self.bye(sid)

    def views(self, now: float | None = None) -> list[protocol.Session]:
        now = time.time() if now is None else now
        self.prune(now)
        return [s.view(now) for s in sorted(self.sessions.values(), key=lambda s: -s.state_ts)]

    # ---- commands

    def queue(self, sid: str, cmd: dict) -> bool:
        s = self.sessions.get(sid)
        if s is None:
            return False
        s.commands.append(cmd)
        if cmd.get("t") == "set":
            s.pending[cmd["key"]] = cmd["value"]
        s.wake.set()
        return True

    async def take(self, sid: str, wait: float) -> list[dict]:
        s = self.get(sid)
        if not s.commands:
            s.wake.clear()
            try:
                await asyncio.wait_for(s.wake.wait(), timeout=min(wait, LONG_POLL_MAX))
            except asyncio.TimeoutError:
                pass
        out, s.commands = s.commands, []
        return out

    def result(self, body: dict) -> None:
        s = self.get(str(body["sid"]))
        cmd = body.get("cmd") or {}
        if cmd.get("t") == "set":
            s.pending.pop(cmd.get("key"), None)
            if body.get("ok") and cmd.get("key") in ("model", "effort", "mode", "fast"):
                setattr(s, cmd["key"], cmd["value"])
        if not body.get("ok"):
            self.log(f"Mod {s.sid[:8]}: {cmd} failed: {body.get('error')}")

    # ---- limits

    def rate_limits(self, now: float | None = None) -> list | None:
        """The freshest rate-limit windows any session reported, or None."""
        now = time.time() if now is None else now
        fresh = [s for s in self.sessions.values() if s.rate_limits and now - s.rate_ts < RATE_FRESH_S]
        return max(fresh, key=lambda s: s.rate_ts).rate_limits if fresh else None


def usage_payload(rate_limits: list, now: float | None = None) -> dict | None:
    """The knob's legacy usage payload (s/sr/w/wr) from the mod's rateLimits."""
    now = time.time() if now is None else now
    by_kind = {r.get("kind"): r for r in rate_limits if isinstance(r, dict)}
    five, week = by_kind.get("five_hour"), by_kind.get("seven_day")
    if not five and not week:
        return None

    def mins(r):
        try:
            t = datetime.fromisoformat(str(r["resetsAt"]).replace("Z", "+00:00")).timestamp()
            return max(0, round((t - now) / 60))
        except (KeyError, ValueError, TypeError):
            return -1

    def pct(r):
        return float(r.get("percentUsed") or 0) if r else 0.0

    used_up = pct(five) >= 100 or pct(week) >= 100
    return {"s": pct(five), "sr": mins(five) if five else -1, "w": pct(week),
            "wr": mins(week) if week else -1, "st": "limited" if used_up else "allowed",
            "acct": "pro", "ok": True}


# ---------------------------------------------------------------- HTTP

class ModServer:
    def __init__(self, hub, registry: ModRegistry, socket_path: Path = DEFAULT_SOCKET, log=print) -> None:
        self.hub, self.registry, self.socket_path, self.log = hub, registry, socket_path, log
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        self._server = await asyncio.start_unix_server(self._conn, path=str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        self.log(f"Mod endpoint on {self.socket_path}")

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        status, reply = 500, {"error": "internal"}
        try:
            method, target, body = await asyncio.wait_for(_read_request(reader), timeout=10)
            status, reply = await self.route(method, target, body)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return
        except ValueError as e:
            status, reply = 400, {"error": str(e)}
        except Exception as e:  # never let one request take the bridge down
            self.log(f"Mod endpoint: {e!r}")
        data = json.dumps(reply).encode()
        head = (f"HTTP/1.1 {status} {'OK' if status == 200 else 'Error'}\r\n"
                f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n"
                "Connection: close\r\n\r\n").encode()
        try:
            writer.write(head + data)
            await writer.drain()
            writer.close()
        except (ConnectionError, OSError):
            pass

    async def route(self, method: str, target: str, body: dict) -> tuple[int, dict]:
        url = urlparse(target)
        path, query = url.path, parse_qs(url.query)
        reg = self.registry
        if method == "GET" and path == "/v1/commands":
            sid = (query.get("sid") or [""])[0]
            if not sid:
                raise ValueError("sid missing")
            wait = float((query.get("wait") or ["25"])[0])
            return 200, {"commands": await reg.take(sid, wait), "knob": self.hub.connected}
        if method != "POST":
            return 404, {"error": "not found"}
        if not isinstance(body.get("sid"), str) or not body["sid"]:
            raise ValueError("sid missing")
        if path == "/v1/hello":
            reg.hello(body)
            return 200, {"ok": True, "knob": self.hub.connected}
        if path == "/v1/state":
            reg.report(body)
            return 200, {"ok": True, "knob": self.hub.connected}
        if path == "/v1/result":
            reg.result(body)
            return 200, {"ok": True}
        if path == "/v1/bye":
            reg.bye(body["sid"])
            return 200, {"ok": True}
        if path == "/v1/ask":
            reg.get(body["sid"])
            return 200, await self.ask(body)
        return 404, {"error": "not found"}

    async def ask(self, body: dict) -> dict:
        what, sid = body.get("what"), body["sid"]
        inp = body.get("input") if isinstance(body.get("input"), dict) else {}
        if what == "permission":
            req, options = prompts.permission_request(body.get("tool"), inp, body.get("suggestions"), sid)
            choices = await self.hub.ask_request(req)
            return {"decision": prompts.permission_decision(options, choices, body.get("suggestions"))}
        if what == "question":
            answers = {}
            for req, q, options in prompts.question_requests(inp.get("questions"), sid):
                answer = prompts.question_answer(options, await self.hub.ask_request(req))
                if answer is None:
                    return {"answers": None}
                answers[str(q.get("question", ""))] = answer
            return {"answers": answers or None}
        if what == "plan":
            req, options = prompts.plan_request(inp.get("plan"), sid)
            return {"verdict": prompts.plan_verdict(options, await self.hub.ask_request(req))}
        raise ValueError(f"unknown ask {what!r}")


async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict]:
    line = (await reader.readline()).decode("latin-1").strip()
    parts = line.split(" ")
    if len(parts) < 2:
        raise ValueError("bad request line")
    method, target = parts[0].upper(), parts[1]
    length = 0
    while True:
        h = (await reader.readline()).decode("latin-1")
        if h in ("\r\n", "\n", ""):
            break
        name, _, value = h.partition(":")
        if name.strip().lower() == "content-length":
            length = int(value.strip())
    if length > MAX_BODY:
        raise ValueError("body too large")
    raw = await reader.readexactly(length) if length else b""
    body = json.loads(raw.decode("utf-8")) if raw else {}
    if not isinstance(body, dict):
        raise ValueError("body must be a JSON object")
    return method, target, body
