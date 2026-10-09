"""Claude Code sessions for the knob's session ring.

Built from the per-session files knob_activity.py's hook writes (state, cwd,
transcript path, permission mode) plus the tail of each session's transcript
(model, context fill). Read-only and cheap enough to run every second.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

try:
    from . import knob_activity, protocol
except ImportError:  # run as a script
    import knob_activity
    import protocol

TAIL_BYTES = 256 * 1024
STALE_TO_IDLE = {  # protocol state -> seconds before it decays to idle
    protocol.THINKING: knob_activity.WORK_STALE_S, protocol.TOOL: knob_activity.WORK_STALE_S,
    protocol.COMPACTING: knob_activity.WORK_STALE_S, protocol.DONE: knob_activity.DONE_S,
    protocol.NEEDS_PERMISSION: knob_activity.ALLOW_STALE_S, protocol.ASKING: knob_activity.ALLOW_STALE_S,
}
HIDE_AFTER_S = 12 * 3600

_tail_cache: dict[str, tuple[float, int, dict]] = {}


def short_model(model: str) -> str:
    m = (model or "").lower()
    for family in ("fable", "opus", "sonnet", "haiku"):
        if family in m:
            return family
    return m[:15]


def context_window(model: str) -> int:
    return 1_000_000 if "[1m]" in (model or "").lower() else 200_000


def transcript_info(path: str) -> dict:
    """Model and context tokens from the newest assistant message with usage."""
    try:
        st = os.stat(path)
    except OSError:
        return {}
    hit = _tail_cache.get(path)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    info: dict = {}
    try:
        with open(path, "rb") as f:
            f.seek(max(0, st.st_size - TAIL_BYTES))
            lines = f.read().splitlines()
    except OSError:
        return {}
    for raw in reversed(lines):
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        msg = rec.get("message") if isinstance(rec, dict) else None
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        usage = msg.get("usage") or {}
        if not usage:
            continue
        tokens = sum(int(usage.get(k) or 0) for k in
                     ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        info = {"model": str(msg.get("model") or ""), "tokens": tokens}
        break
    _tail_cache[path] = (st.st_mtime, st.st_size, info)
    return info


def read_sessions(activity_dir: Path = knob_activity.ACTIVITY_DIR,
                  now: float | None = None) -> list[protocol.Session]:
    now = time.time() if now is None else now
    out: list[tuple[float, protocol.Session]] = []
    try:
        files = list(activity_dir.glob("*.json"))
    except OSError:
        return []
    for f in files:
        try:
            d = json.loads(f.read_text())
            ts = float(d["ts"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        age = now - ts
        if age > HIDE_AFTER_S:
            continue
        state = int(d.get("ps", protocol.IDLE))
        if age > STALE_TO_IDLE.get(state, 10 ** 9):
            state = protocol.IDLE
        cwd = str(d.get("cwd") or "")
        info = transcript_info(str(d.get("tp") or "")) if d.get("tp") else {}
        model = info.get("model") or str(d.get("model") or "")
        tokens = info.get("tokens")
        ctx = min(100, round(100 * tokens / context_window(model))) if tokens else -1
        out.append((ts, protocol.Session(
            sid=f.stem, label=Path(cwd).name or f.stem[:8], state=state, ctx_pct=ctx,
            model=short_model(model), effort=str(d.get("effort") or ""),
            mode=str(d.get("mode") or "default"), cwd=cwd)))
    out.sort(key=lambda t: -t[0])
    return [s for _, s in out]


def context_tokens_k(activity_dir: Path, sid: str) -> int:
    try:
        d = json.loads((activity_dir / f"{sid}.json").read_text())
    except (OSError, ValueError):
        return -1
    info = transcript_info(str(d.get("tp") or "")) if d.get("tp") else {}
    return round(info["tokens"] / 1000) if info.get("tokens") else -1
