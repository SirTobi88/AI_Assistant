"""What the Claude desktop app knows about its Code sessions: the title shown
in its sidebar and its own session id (local_…), by the CLI session id the
mod reports. Read from the app's per-session files; nothing is written.

    ~/Library/Application Support/Claude/claude-code-sessions/<account>/<org>/local_<uuid>.json
    {"sessionId": "local_…", "cliSessionId": "<uuid>", "title": "…", "isArchived": false, …}
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path.home() / "Library/Application Support/Claude/claude-code-sessions"
REFRESH_S = 5.0


@dataclass(frozen=True)
class DesktopSession:
    host_id: str
    title: str
    last_activity: float = 0.0      # s since the epoch, the app's lastActivityAt


class DesktopSessions:
    def __init__(self, root: Path = ROOT, clock=time.monotonic) -> None:
        self.root, self.clock = root, clock
        self._by_cli: dict[str, DesktopSession] = {}
        self._mtimes: dict[Path, float] = {}
        self._read_at = float("-inf")

    def get(self, cli_sid: str) -> DesktopSession | None:
        self._refresh()
        return self._by_cli.get(cli_sid)

    def _refresh(self) -> None:
        now = self.clock()
        if now - self._read_at < REFRESH_S:
            return
        self._read_at = now
        try:
            files = list(self.root.glob("*/*/local_*.json"))
        except OSError:
            return
        changed = False
        for f in files:
            try:
                m = f.stat().st_mtime
            except OSError:
                continue
            if self._mtimes.get(f) != m:
                self._mtimes[f] = m
                changed = True
        for f in set(self._mtimes) - set(files):
            del self._mtimes[f]
            changed = True
        if not changed:
            return
        by_cli: dict[str, DesktopSession] = {}
        for f in self._mtimes:
            try:
                d = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            cli, host = d.get("cliSessionId"), d.get("sessionId")
            if isinstance(cli, str) and isinstance(host, str) and not d.get("isArchived"):
                last = d.get("lastActivityAt")
                by_cli[cli] = DesktopSession(host, str(d.get("title") or "").strip(),
                                             last / 1000 if isinstance(last, (int, float)) else 0.0)
        self._by_cli = by_cli
