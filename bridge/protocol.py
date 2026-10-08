"""Protocol v1 between the knob and the bridge — see docs/protocol.md.

Pure functions only (no BLE): encoding host->device messages, splitting them into
write-sized frames, reassembling device->host notifies and validating commands.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

VERSION = 1
FRAME_MARK = 0x01
DEFAULT_CHUNK = 180          # conservative ATT payload (MTU 185 - 3, minus header slack)
MAX_MESSAGE = 4096

# Session states (docs/protocol.md, `sessions`)
STARTING, IDLE, THINKING, RESPONDING, TOOL, COMPACTING = range(6)
NEEDS_PERMISSION, ASKING, NEEDS_INPUT, ERROR, DONE = range(6, 11)
NEEDS_YOU = {NEEDS_PERMISSION, ASKING, NEEDS_INPUT}

MAX_SESSIONS = 12
MAX_OPTIONS = 8
MAX_OPTION_LEN = 40
MAX_PROMPT_TEXT = 300

SETTING_KEYS = ("model", "effort", "mode", "fast")
# Session ids go out shortened (Claude's are 36-char UUIDs, the knob keeps 15);
# the hub maps them back by prefix.
SID_LEN = 12


def short_sid(sid: str) -> str:
    return (sid or "")[:SID_LEN]


def _dumps(obj: dict) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ---------------------------------------------------------------- host -> device

@dataclass
class Session:
    sid: str
    label: str
    state: int = IDLE
    ctx_pct: int = -1
    model: str = ""
    effort: str = ""
    mode: str = "default"
    fast: bool = False
    unread: bool = False
    cwd: str = ""                   # bridge-side only (monitor), not sent

    def row(self) -> list:
        return [short_sid(self.sid), _clip(self.label, 24), self.state, self.ctx_pct, self.model,
                self.effort, self.mode, int(self.fast), int(self.unread)]


def sessions_msg(sessions: list[Session], active: str | None) -> dict:
    ordered = sorted(sessions, key=lambda s: (s.state not in NEEDS_YOU, not s.unread))
    return {"t": "sessions", "act": short_sid(active or ""),
            "ss": [s.row() for s in ordered[:MAX_SESSIONS]]}


def options_msg(model: list[str], effort: list[str], mode: list[str]) -> dict:
    return {"t": "options", "model": model, "effort": effort, "mode": mode}


@dataclass
class Prompt:
    id: str
    sid: str
    kind: str                       # perm | ask | plan
    title: str
    text: str
    options: list[str]
    multi: bool = False
    confirm: tuple[int, ...] = ()   # option indices that need hold-to-confirm
    timeout: int = 120

    def msg(self) -> dict:
        if self.kind not in ("perm", "ask", "plan"):
            raise ValueError(f"unknown prompt kind {self.kind!r}")
        if not self.options:
            raise ValueError("prompt needs at least one option")
        return {"t": "prompt", "id": self.id, "sid": short_sid(self.sid), "k": self.kind,
                "ti": _clip(self.title, 40), "tx": _clip(self.text, MAX_PROMPT_TEXT),
                "o": [_clip(o, MAX_OPTION_LEN) for o in self.options[:MAX_OPTIONS]],
                "m": int(self.multi), "d": sum(1 << i for i in set(self.confirm) if 0 <= i < MAX_OPTIONS),
                "to": int(self.timeout)}


def prompt_done_msg(prompt_id: str) -> dict:
    return {"t": "prompt_done", "id": prompt_id}


def stt_msg(stt_id: str, text: str, final: bool) -> dict:
    return {"t": "stt", "id": stt_id, "tx": _clip(text, MAX_PROMPT_TEXT), "fin": int(final)}


# ---------------------------------------------------------------- framing

def frame(msg: dict, msg_id: int, chunk: int = DEFAULT_CHUNK) -> list[bytes]:
    """One message -> the list of writes that carry it.

    Fits in one write -> the bare JSON. Otherwise 0x01-prefixed frames
    [mark, msg_id, index, count] + slice.
    """
    data = _dumps(msg)
    if len(data) > MAX_MESSAGE:
        raise ValueError(f"message is {len(data)} bytes, limit {MAX_MESSAGE}")
    if len(data) <= chunk:
        return [data]
    body = chunk - 4
    parts = [data[i:i + body] for i in range(0, len(data), body)]
    if len(parts) > 255:
        raise ValueError("too many frames")
    mid = msg_id & 0xFF
    return [bytes([FRAME_MARK, mid, i, len(parts)]) + p for i, p in enumerate(parts)]


class Reassembler:
    """Collects device->host notifies into complete JSON messages."""

    def __init__(self) -> None:
        self._id: int | None = None
        self._parts: dict[int, bytes] = {}
        self._count = 0

    def feed(self, data: bytes) -> dict | None:
        if not data:
            return None
        if data[0] != FRAME_MARK:
            self._reset()
            return _loads(data)
        if len(data) < 4:
            return None
        mid, idx, count = data[1], data[2], data[3]
        if mid != self._id:
            self._reset()
            self._id, self._count = mid, count
        if count != self._count or idx >= count:
            self._reset()
            return None
        self._parts[idx] = data[4:]
        if len(self._parts) < count:
            return None
        whole = b"".join(self._parts[i] for i in range(count))
        self._reset()
        return _loads(whole)

    def _reset(self) -> None:
        self._id, self._parts, self._count = None, {}, 0


def _loads(data: bytes) -> dict | None:
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


# ---------------------------------------------------------------- device -> host

@dataclass
class Command:
    t: str
    args: dict = field(default_factory=dict)


def parse_command(obj: dict | None) -> Command | None:
    """Validate a device notify; ack/nack and malformed commands -> None."""
    if not obj or "t" not in obj:
        return None
    t = obj["t"]
    if t == "answer":
        c = obj.get("c")
        if not isinstance(obj.get("id"), str) or not isinstance(c, list) \
                or not c or not all(isinstance(i, int) and 0 <= i < MAX_OPTIONS for i in c):
            return None
        return Command(t, {"id": obj["id"], "choices": sorted(set(c))})
    if t in ("switch", "interrupt"):
        return Command(t, {"sid": obj["sid"]}) if isinstance(obj.get("sid"), str) else None
    if t == "set":
        k, v = obj.get("k"), obj.get("v")
        if k not in SETTING_KEYS or not isinstance(obj.get("sid"), str):
            return None
        if k == "fast":
            v = bool(v)
        elif not isinstance(v, str) or not v:
            return None
        return Command(t, {"sid": obj["sid"], "key": k, "value": v})
    if t == "ptt":
        return Command(t, {"on": bool(obj.get("on"))})
    if t in ("stt_send", "stt_drop"):
        return Command(t, {"id": obj["id"]}) if isinstance(obj.get("id"), str) else None
    if t == "hello":
        return Command(t, {"v": obj.get("v"), "fw": obj.get("fw")})
    return None
