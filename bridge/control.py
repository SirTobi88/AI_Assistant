"""Carry out knob commands (model, effort, mode, fast, interrupt, switch) on a
running Claude Code session — by typing into it, because neither the CLI nor
the desktop app has an outside API for this.

* CLI in Terminal.app: find the tab by the session's tty, bring it to the
  front, then keystrokes. Commands are typed as slash commands after clearing
  the input line (Ctrl+E, Ctrl+U — Claude Code keeps the cleared draft for
  Ctrl+Y); /model, /effort and /fast run immediately even mid-turn. Mode:
  Shift+Tab as often as the cycle needs. Interrupt: Esc.
* Desktop app: bring it to the front and use its menus (Cmd+Shift+I model,
  Cmd+Shift+E effort, Cmd+Shift+M mode, then a digit), Esc to stop. No text is
  typed, so a draft in the prompt box stays. It acts on the session that is
  showing — the app has no way to pick a session from outside.

Needs the Accessibility permission (System Settings > Privacy & Security >
Accessibility) for whatever runs the bridge. Off unless ``control = keys`` is
set in ~/.config/ai-knob/config.

Values are checked against allowlists before anything is typed: the knob
must never be able to type arbitrary text into a session.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILE = Path.home() / ".config" / "ai-knob" / "config"

MODEL_ALIASES = {"default", "best", "fable", "opus", "sonnet", "haiku", "opusplan",
                 "sonnet[1m]", "opus[1m]", "opusplan[1m]"}
MODEL_ID = re.compile(r"^claude-[a-z0-9-]+(\[1m\])?$")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Shift+Tab order in the CLI (bypassPermissions only when it is enabled; left out).
CLI_MODE_CYCLE = ("default", "acceptEdits", "plan", "auto")

# Desktop menus: item order -> digit. Defaults from the docs' order; check them
# in the app and override in the config (desktop_models / desktop_efforts /
# desktop_modes = comma-separated, in menu order).
# Checked in the app (2026-10-16): Opus, Sonnet, Fable, Haiku.
DESKTOP_MODELS = ("opus", "sonnet", "fable", "haiku")
DESKTOP_EFFORTS = EFFORTS
# Checked in the app (2026-10-16): Auto, Manual, Accept edits, Plan; bypass
# permissions is a separate switch, not an item.
DESKTOP_MODES = ("auto", "default", "acceptEdits", "plan")

KEY_ESC, KEY_RETURN, KEY_TAB = 53, 36, 48
# Number-row keys 1–9 (same on US and German layouts).
DIGIT_KEYCODES = {1: 18, 2: 19, 3: 20, 4: 21, 5: 23, 6: 22, 7: 26, 8: 28, 9: 25}


class Unsupported(Exception):
    """This command can't be carried out for this session."""


@dataclass
class Target:
    kind: str                 # "terminal" | "desktop"
    tty: str = ""             # /dev/ttysNNN for terminal sessions


@dataclass
class Plan:
    """What to do, as data (tested without touching the machine)."""
    focus: tuple = ()                         # ("terminal", tty) | ("desktop",)
    keys: list = field(default_factory=list)  # ("text", s) | ("code", n, mods) | ("char", c, mods)
    activate: bool = True                     # False: an open app menu would close


def valid_model(v: str) -> bool:
    return v in MODEL_ALIASES or bool(MODEL_ID.match(v))


def read_config(path: Path | None = None) -> dict:
    out = {}
    path = path or CONFIG_FILE
    try:
        for line in path.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                out[k.strip().lower()] = v.strip()
    except OSError:
        pass
    return out


def _order(cfg: dict, key: str, default: tuple) -> tuple:
    v = cfg.get(key)
    return tuple(x.strip() for x in v.split(",") if x.strip()) if v else default


def _clear_line() -> list:
    return [("char", "e", ("control",)), ("char", "u", ("control",)),
            ("char", "u", ("control",)), ("char", "u", ("control",))]


def _slash(cmd: str) -> list:
    return _clear_line() + [("text", cmd), ("code", KEY_RETURN, ())]


def _menu(letter: str, items: tuple, value: str) -> list:
    if value not in items:
        raise Unsupported(f"{value} is not in the desktop menu {items}")
    idx = items.index(value) + 1
    if idx > 9:
        raise Unsupported("menu item beyond 9")
    # The open menu ignores a typed character ("keystroke"); the physical key
    # (key code) selects, as a test against the app showed.
    return [("char", letter, ("command", "shift")), ("delay", 0.6), ("code", DIGIT_KEYCODES[idx], ())]


def plan_set(target: Target, key: str, value, current: dict, cfg: dict | None = None) -> Plan:
    """``current`` holds the session's mode/fast before the change."""
    cfg = cfg or {}
    if target.kind == "terminal":
        focus = ("terminal", target.tty)
        if key == "model":
            if not valid_model(str(value)):
                raise Unsupported(f"model {value!r} not allowed")
            return Plan(focus, _slash(f"/model {value}"))
        if key == "effort":
            if value not in EFFORTS:
                raise Unsupported(f"effort {value!r} not allowed")
            return Plan(focus, _slash(f"/effort {value}"))
        if key == "fast":
            if bool(value) == bool(current.get("fast")):
                return Plan(focus, [])
            return Plan(focus, _slash("/fast"))
        if key == "mode":
            cur = current.get("mode") or "default"
            if value not in CLI_MODE_CYCLE or cur not in CLI_MODE_CYCLE:
                raise Unsupported(f"mode {cur} -> {value} not in the Shift+Tab cycle")
            n = (CLI_MODE_CYCLE.index(value) - CLI_MODE_CYCLE.index(cur)) % len(CLI_MODE_CYCLE)
            return Plan(focus, [("code", KEY_TAB, ("shift",))] * n)
    elif target.kind == "desktop":
        focus = ("desktop",)
        if key == "model":
            keys = _menu("i", _order(cfg, "desktop_models", DESKTOP_MODELS), str(value))
            if current.get("menu_open"):
                return Plan(focus, keys[-1:], activate=False)
            return Plan(focus, keys)
        if key == "effort":
            return Plan(focus, _menu("e", _order(cfg, "desktop_efforts", DESKTOP_EFFORTS), str(value)))
        if key == "mode":
            # Never from the knob: it would stop every permission question.
            if value == "bypassPermissions":
                raise Unsupported("bypassPermissions is not set from the knob")
            keys = _menu("m", _order(cfg, "desktop_modes", DESKTOP_MODES), str(value))
            # The knob opened the menu already (plan_menu): only the digit is
            # left, and activating the app first would close that menu.
            if current.get("menu_open"):
                return Plan(focus, keys[-1:], activate=False)
            return Plan(focus, keys)
        if key == "fast":
            raise Unsupported("the desktop app has no shortcut for fast mode")
    raise Unsupported(f"{key} for {target.kind}")


MENU_LETTERS = {"mode": "m", "model": "i"}


def plan_menu(target: Target, key: str, open_: bool) -> Plan:
    """Show the desktop app's mode/model menu while the knob's picker is open
    (Cmd+Shift+M / I), close it with Esc when the knob leaves without a choice."""
    if target.kind != "desktop" or key not in MENU_LETTERS:
        raise Unsupported(f"no {key} menu for {target.kind}")
    if open_:
        return Plan(("desktop",), [("char", MENU_LETTERS[key], ("command", "shift"))])
    return Plan(("desktop",), [("code", KEY_ESC, ())], activate=False)


def plan_interrupt(target: Target) -> Plan:
    focus = ("terminal", target.tty) if target.kind == "terminal" else ("desktop",)
    return Plan(focus, [("code", KEY_ESC, ())])


def plan_switch(target: Target) -> Plan:
    return Plan(("terminal", target.tty) if target.kind == "terminal" else ("desktop",), [])


# ---------------------------------------------------------------- AppleScript

def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _mods(mods: tuple) -> str:
    return f" using {{{', '.join(m + ' down' for m in mods)}}}" if mods else ""


def applescript(plan: Plan) -> str:
    lines = []
    if plan.focus[:1] == ("terminal",):
        tty = plan.focus[1]
        if not re.fullmatch(r"/dev/ttys\d+", tty or ""):
            raise Unsupported(f"no usable tty ({tty!r})")
        lines += [
            'tell application "Terminal"',
            "  set found to false",
            "  repeat with w in windows",
            "    repeat with t in tabs of w",
            f"      if tty of t is {_q(tty)} then",
            "        set selected of t to true",
            "        set index of w to 1",
            "        set found to true",
            "        exit repeat",
            "      end if",
            "    end repeat",
            "    if found then exit repeat",
            "  end repeat",
            '  if not found then error "Terminal tab not found" number 1001',
            "  activate",
            "end tell",
        ]
        proc = "Terminal"
    elif plan.focus == ("desktop",):
        if plan.activate:
            lines += ['tell application "Claude" to activate']
        proc = "Claude"
    else:
        raise Unsupported("no focus target")
    if plan.keys:
        if proc == "Claude":
            # The app's menus track the global key stream only: keys sent to
            # the process (tell process) never reach an open menu.
            lines += ["delay 0.25" if plan.activate else "delay 0.1", 'tell application "System Events"']
        else:
            lines += ["delay 0.25", f'tell application "System Events" to tell process {_q(proc)}']
        for k in plan.keys:
            if k[0] == "text":
                lines.append(f"  keystroke {_q(k[1])}")
            elif k[0] == "char":
                lines.append(f"  keystroke {_q(k[1])}{_mods(k[2])}")
            elif k[0] == "code":
                lines.append(f"  key code {int(k[1])}{_mods(k[2])}")
            elif k[0] == "delay":
                lines.append(f"  delay {float(k[1])}")
            lines.append("  delay 0.05")
        lines.append("end tell")
    return "\n".join(lines) + "\n"


def tty_of(pid: int) -> str:
    try:
        out = subprocess.run(["ps", "-o", "tty=", "-p", str(int(pid))], capture_output=True,
                             text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    return f"/dev/{out}" if out and out not in ("??", "-") else ""


def target_for(session) -> Target:
    if getattr(session, "entrypoint", "") == "claude-desktop":
        return Target("desktop")
    if getattr(session, "pid", 0):
        tty = tty_of(session.pid)
        if tty:
            return Target("terminal", tty)
    raise Unsupported("session has no terminal to type into (not started from Terminal.app?)")


ASSISTIVE_HINT = ("macOS refused the keystrokes: give the app running the bridge Accessibility "
                  "access (System Settings > Privacy & Security > Accessibility)")


class KeystrokeAdapter:
    def __init__(self, log=print, run=None, cfg: dict | None = None) -> None:
        self.log = log
        self.cfg = cfg if cfg is not None else read_config()
        self._run = run or self._osascript
        self.menu_open: str | None = None    # the app menu the knob opened (plan_menu)

    def _osascript(self, script: str) -> None:
        r = subprocess.run(["osascript", "-"], input=script, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            err = r.stderr.strip()
            if "1002" in err or "1719" in err or "assistive" in err.lower() or "not allowed" in err.lower():
                raise Unsupported(ASSISTIVE_HINT)
            raise Unsupported(err or "osascript failed")

    def _do(self, what: str, plan: Plan) -> bool:
        try:
            self._run(applescript(plan))
            self.log(f"control: {what}")
            return True
        except (Unsupported, OSError, subprocess.SubprocessError) as e:
            self.log(f"control: {what} not done: {e}")
            return False

    def switch(self, session) -> bool:
        try:
            return self._do(f"show {session.label}", plan_switch(target_for(session)))
        except Unsupported as e:
            self.log(f"control: show {session.label} not done: {e}")
            return False

    def set(self, session, key: str, value) -> bool:
        menu_open, self.menu_open = self.menu_open == key, None
        try:
            plan = plan_set(target_for(session), key, value,
                            {"mode": session.mode, "fast": session.fast, "menu_open": menu_open}, self.cfg)
        except Unsupported as e:
            self.log(f"control: {key}={value} for {session.label} not done: {e}")
            return False
        return self._do(f"{key}={value} for {session.label}", plan)

    def menu(self, session, key: str, open_: bool) -> bool:
        if not open_ and self.menu_open != key:
            return True          # nothing of ours is open
        try:
            plan = plan_menu(target_for(session), key, open_)
        except Unsupported as e:
            self.log(f"control: {key} menu for {session.label} not shown: {e}")
            return False
        self.menu_open = key if open_ else None
        if not self._do(f"{key} menu {'open' if open_ else 'closed'}", plan):
            self.menu_open = None
            return False
        return True

    def interrupt(self, session) -> bool:
        try:
            return self._do(f"interrupt {session.label}", plan_interrupt(target_for(session)))
        except Unsupported as e:
            self.log(f"control: interrupt {session.label} not done: {e}")
            return False


# Modes the knob may ask the desktop app for; never bypassPermissions.
MOD_MODES = ("default", "plan", "acceptEdits", "auto")


# The desktop app's session ids, as CLAUDE_CODE_HOST_SESSION_ID carries them.
HOST_ID_RE = re.compile(r"local_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class ModAdapter:
    """Sessions running the ai-knob mod carry commands out themselves
    (mod_server.ModRegistry queues them); everything the mod can't do — the
    permission mode outside the desktop app, sessions without the mod — goes
    to ``fallback`` (the keystroke adapter when enabled), else it is reported
    as not possible. In the desktop app the mod sets the mode through the
    app's own session tool, which asks on screen before a mode that does more
    without asking."""

    def __init__(self, registry, fallback=None, log=print, run=None) -> None:
        self.registry, self.fallback, self.log = registry, fallback, log
        self.run = run or (lambda args: subprocess.run(args, check=False, timeout=10))

    def _fallback(self, what: str, session, *args) -> bool:
        if self.fallback is None:
            why = "the mod can't" if self.registry.has(session.sid) else "no mod for it"
            self.log(f"control: {what} for {session.label} not possible "
                     f"({why}; keystrokes off)")
            return False
        return getattr(self.fallback, what)(session, *args)

    def switch(self, session) -> bool:
        if not self.registry.has(session.sid):
            return self._fallback("switch", session)
        # Desktop app: open the session there too, by its own link, so the
        # app shows (and types into) the session the knob now follows.
        host_id = self.registry.sessions[session.sid].host_id
        if not HOST_ID_RE.fullmatch(host_id):
            return True
        self.log(f"control: show {session.label} in the desktop app")
        self.run(["open", f"claude://claude.ai/epitaxy/{host_id}"])
        return True

    def set(self, session, key: str, value) -> bool:
        if not self.registry.has(session.sid):
            return self._fallback("set", session, key, value)
        desktop = self.registry.sessions[session.sid].entrypoint == "claude-desktop"
        if key == "model" and desktop and self.fallback is not None:
            # The app's model menu: /model from the mod changes the engine
            # but not the picker under the input field.
            if not valid_model(str(value)):
                self.log(f"control: model={value!r} not allowed")
                return False
            return self._fallback("set", session, key, value)
        if key == "mode":
            # Keystrokes when enabled: the app's own tool refuses a call from
            # the mod in auto mode (docs/mod-api-findings.md).
            if self.fallback is not None or not desktop:
                done = self._fallback("set", session, key, value)
                if done:
                    self.registry.set_mode(session.sid, value)
                return done
            if value not in MOD_MODES:
                self.log(f"control: mode={value!r} not allowed")
                return False
        if key == "model" and not valid_model(str(value)) or key == "effort" and value not in EFFORTS:
            self.log(f"control: {key}={value!r} not allowed")
            return False
        return self.registry.queue(session.sid, {"t": "set", "key": key,
                                                 "value": bool(value) if key == "fast" else value})

    def prompt(self, session, text: str) -> bool:
        """A transcript from the knob's push-to-talk, as a prompt in the session."""
        if not self.registry.has(session.sid):
            self.log(f"control: voice prompt for {session.label} not possible (no mod for it)")
            return False
        self.log(f"control: voice prompt for {session.label} ({len(text)} chars)")
        return self.registry.queue(session.sid, {"t": "prompt", "text": text})

    def fill(self, session, text: str) -> bool:
        """A transcript into the session's prompt box, to edit before sending."""
        if not self.registry.has(session.sid):
            self.log(f"control: voice text for {session.label} not possible (no mod for it)")
            return False
        self.log(f"control: voice text into {session.label}'s prompt box ({len(text)} chars)")
        return self.registry.queue(session.sid, {"t": "fill", "text": text})

    def menu(self, session, key: str, open_: bool) -> bool:
        # Only the keystroke path has a menu to show.
        return self.fallback.menu(session, key, open_) if self.fallback is not None else False

    def interrupt(self, session) -> bool:
        if not self.registry.has(session.sid):
            return self._fallback("interrupt", session)
        return self.registry.queue(session.sid, {"t": "interrupt"})


def adapter_from_config(log=print, registry=None):
    cfg = read_config()
    keys = None
    if cfg.get("control", "off").lower() == "keys":
        log("Knob control fallback: keystrokes (Terminal.app / desktop app)")
        keys = KeystrokeAdapter(log, cfg=cfg)
    if registry is not None:
        return ModAdapter(registry, keys, log)
    if keys is None:
        log("Knob control off (set control = keys in ~/.config/ai-knob/config to enable)")
    return keys
