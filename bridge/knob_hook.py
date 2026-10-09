"""Blocking Claude Code hooks that let the knob answer permission requests,
questions (AskUserQuestion) and plans (ExitPlanMode).

    python3 knob_hook.py --perm      PermissionRequest hook (any tool)
    python3 knob_hook.py --pre       PreToolUse hook, matcher AskUserQuestion|ExitPlanMode
    python3 knob_hook.py --install / --uninstall

The hook hands the request to the bridge over a Unix socket and waits for the
knob. Whenever that doesn't work out — bridge not running, knob not connected,
timeout, or "Am Rechner" chosen on the knob — it prints nothing and exits 0, so
Claude Code shows its normal dialog. Stdlib only; never fails a session.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

SOCKET_PATH = Path.home() / ".config" / "ai-knob" / "bridge.sock"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
HOOK_MARK = "knob_hook.py"

try:
    from .prompts import (ALLOW, ALLOW_ALWAYS, ASK_TIMEOUT, AT_COMPUTER, DANGEROUS, DENY,  # noqa: F401
                          PLAN_DENY_MESSAGE, PLAN_NO, PLAN_OK, is_dangerous, tool_summary)
    from . import prompts
except ImportError:  # run as a script by Claude Code
    from prompts import (ALLOW, ALLOW_ALWAYS, ASK_TIMEOUT, AT_COMPUTER, DANGEROUS, DENY,  # noqa: F401
                         PLAN_DENY_MESSAGE, PLAN_NO, PLAN_OK, is_dangerous, tool_summary)
    import prompts

HOOK_TIMEOUT = ASK_TIMEOUT + 30
CONNECT_TIMEOUT = 0.5


# ---------------------------------------------------------------- bridge client

def ask_bridge(request: dict, socket_path: Path = SOCKET_PATH) -> list[int] | None:
    """Send one prompt to the bridge; the chosen option indices, or None."""
    timeout = int(request.get("timeout", ASK_TIMEOUT)) + 5
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(CONNECT_TIMEOUT)
            s.connect(str(socket_path))
            s.settimeout(timeout)
            s.sendall((json.dumps({"op": "ask", **request}) + "\n").encode())
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        reply = json.loads(buf.decode() or "null")
    except (OSError, ValueError):
        return None
    choices = reply.get("choices") if isinstance(reply, dict) else None
    if not isinstance(choices, list) or not all(isinstance(c, int) for c in choices):
        return None
    return choices


# ---------------------------------------------------------------- formatting
# The asking itself lives in prompts.py; here only the settings-hook wrapping.

def _sid(event: dict) -> str:
    return str(event.get("session_id") or "")


def permission_request(event: dict) -> tuple[dict, list[str]]:
    return prompts.permission_request(event.get("tool_name"), event.get("tool_input") or {},
                                      event.get("permission_suggestions"), _sid(event))


def permission_output(event: dict, options: list[str], choices: list[int] | None) -> dict | None:
    decision = prompts.permission_decision(options, choices, event.get("permission_suggestions"))
    if decision is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}


def question_requests(event: dict) -> list[tuple[dict, dict, list[str]]]:
    return prompts.question_requests((event.get("tool_input") or {}).get("questions"), _sid(event))


def question_output(event: dict, answers: dict) -> dict:
    inp = dict(event.get("tool_input") or {})
    inp["answers"] = answers
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "allow", "updatedInput": inp}}


def plan_request(event: dict) -> tuple[dict, list[str]]:
    return prompts.plan_request((event.get("tool_input") or {}).get("plan"), _sid(event))


def plan_output(event: dict, options: list[str], choices: list[int] | None) -> dict | None:
    verdict = prompts.plan_verdict(options, choices)
    if verdict is True:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                       "updatedInput": dict(event.get("tool_input") or {})}}
    if verdict is False:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": PLAN_DENY_MESSAGE}}
    return None


# ---------------------------------------------------------------- hook entry points

def run_perm(event: dict, ask=ask_bridge) -> dict | None:
    if event.get("tool_name") in ("AskUserQuestion", "ExitPlanMode"):
        return None          # the PreToolUse hook owns those
    req, options = permission_request(event)
    return permission_output(event, options, ask(req))


def run_pre(event: dict, ask=ask_bridge) -> dict | None:
    tool = event.get("tool_name")
    if tool == "ExitPlanMode":
        req, options = plan_request(event)
        return plan_output(event, options, ask(req))
    if tool != "AskUserQuestion":
        return None
    answers = {}
    for req, q, options in question_requests(event):
        answer = prompts.question_answer(options, ask(req))
        if answer is None:
            return None      # any question not answered on the knob -> normal dialog
        answers[str(q.get("question", ""))] = answer
    return question_output(event, answers) if answers else None


# ---------------------------------------------------------------- settings.json

def _hook_command(flag: str) -> str:
    return f'"{sys.executable}" "{Path(__file__).resolve()}" {flag}'


def _is_ours(h) -> bool:
    return isinstance(h, dict) and HOOK_MARK in str(h.get("command", ""))


def set_installed(on: bool, settings_path: Path = CLAUDE_SETTINGS) -> None:
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        settings = {}
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    wanted = {"PermissionRequest": ("*", "--perm"),
              "PreToolUse": ("AskUserQuestion|ExitPlanMode", "--pre")}
    for ev, (matcher, flag) in wanted.items():
        groups = []
        for g in hooks.get(ev) or []:
            if not isinstance(g, dict):
                groups.append(g)
                continue
            kept = [h for h in g.get("hooks") or [] if not _is_ours(h)]
            if kept:
                groups.append({**g, "hooks": kept})
        if on:
            groups.append({"matcher": matcher, "hooks": [
                {"type": "command", "command": _hook_command(flag), "timeout": HOOK_TIMEOUT}]})
        if groups:
            hooks[ev] = groups
        else:
            hooks.pop(ev, None)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, settings_path)


def main(argv: list[str]) -> int:
    arg = argv[1] if len(argv) > 1 else ""
    if arg in ("--perm", "--pre"):
        try:
            event = json.loads(sys.stdin.read())
            out = run_perm(event) if arg == "--perm" else run_pre(event)
            if out is not None:
                sys.stdout.write(json.dumps(out, ensure_ascii=False))
        except Exception:
            pass
        return 0
    if arg in ("--install", "--uninstall"):
        set_installed(arg == "--install")
        print(("Installed" if arg == "--install" else "Removed") + f" knob hooks in {CLAUDE_SETTINGS}")
        return 0
    print("usage: knob_hook.py --perm | --pre | --install | --uninstall")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
