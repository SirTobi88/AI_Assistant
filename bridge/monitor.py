"""Numbers for the knob's Monitor ring: burn rate and time-to-limit from the
usage readings, diff size and CI state of the active session's repository."""
from __future__ import annotations

import json
import subprocess
import time
from collections import deque

try:
    from . import protocol
except ImportError:  # run as a script
    import protocol

GIT_TTL = 15
GH_TTL = 90


class BurnTracker:
    """5-h window utilisation over time -> %/h and minutes until 100 %."""

    def __init__(self, window_s: float = 3600) -> None:
        self.window_s = window_s
        self.samples: deque[tuple[float, float]] = deque()

    def add(self, pct: float, now: float | None = None) -> None:
        now = time.time() if now is None else now
        if self.samples and pct + 1 < self.samples[-1][1]:
            self.samples.clear()            # window reset
        self.samples.append((now, pct))
        while self.samples and now - self.samples[0][0] > self.window_s:
            self.samples.popleft()

    def rate(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        (t0, p0), (t1, p1) = self.samples[0], self.samples[-1]
        return max(0.0, (p1 - p0) / ((t1 - t0) / 3600)) if t1 > t0 else 0.0

    def eta_minutes(self, reset_minutes: int | None) -> int:
        """Minutes until the limit at the current pace; -1 when the window resets first."""
        if not self.samples:
            return -1
        r = self.rate()
        left = 100 - self.samples[-1][1]
        if r <= 0.01:
            return -1
        eta = round(left / r * 60)
        if reset_minutes is not None and reset_minutes >= 0 and eta >= reset_minutes:
            return -1
        return max(0, eta)


_cache: dict[tuple[str, str], tuple[float, object]] = {}


def _cached(kind: str, cwd: str, ttl: float, fn):
    key = (kind, cwd)
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (now, val)
    return val


def _run(args: list[str], cwd: str, timeout: float = 8) -> str | None:
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def diff_stat(cwd: str) -> tuple[int, int]:
    """Lines added / removed in the working tree against HEAD."""
    def get():
        out = _run(["git", "diff", "HEAD", "--numstat"], cwd)
        add = dele = 0
        for line in (out or "").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                add += int(parts[0]); dele += int(parts[1])
        return add, dele
    return _cached("diff", cwd, GIT_TTL, get) if cwd else (0, 0)


def ci_state(cwd: str) -> tuple[int, int]:
    """(ci, pr) for the current branch's PR via gh: ci -1 unknown, 0 running, 1 green, 2 red."""
    def get():
        out = _run(["gh", "pr", "view", "--json", "number,statusCheckRollup"], cwd)
        if not out:
            return -1, 0
        try:
            d = json.loads(out)
        except ValueError:
            return -1, 0
        checks = d.get("statusCheckRollup") or []
        states = {str(c.get("conclusion") or c.get("state") or c.get("status") or "").upper() for c in checks}
        if not checks:
            ci = -1
        elif states & {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}:
            ci = 2
        elif states & {"PENDING", "IN_PROGRESS", "QUEUED", "EXPECTED", ""}:
            ci = 0
        else:
            ci = 1
        return ci, int(d.get("number") or 0)
    return _cached("ci", cwd, GH_TTL, get) if cwd else (-1, 0)


def monitor_msg(sid: str, burn: BurnTracker, reset_minutes: int | None, tok_k: int,
                cwd: str, agents: int = 0, tasks: tuple[int, int] = (0, 0)) -> dict:
    add, dele = diff_stat(cwd)
    ci, pr = ci_state(cwd)
    return {"t": "monitor", "sid": protocol.short_sid(sid), "burn": round(burn.rate(), 1),
            "eta": burn.eta_minutes(reset_minutes), "tok": tok_k, "ag": agents,
            "td": tasks[0], "tt": tasks[1], "add": add, "del": dele, "ci": ci, "pr": pr}
