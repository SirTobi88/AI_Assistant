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
import re
import socket
import sys
from pathlib import Path

SOCKET_PATH = Path.home() / ".config" / "ai-knob" / "bridge.sock"
CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
HOOK_MARK = "knob_hook.py"

ASK_TIMEOUT = 30           # seconds the knob gets; the normal dialog waits that long
HOOK_TIMEOUT = ASK_TIMEOUT + 30
CONNECT_TIMEOUT = 0.5

ALLOW, ALLOW_ALWAYS, DENY, AT_COMPUTER = "Erlauben", "Immer erlauben", "Ablehnen", "Am Rechner"

# Commands that only go through with a hold on the knob (hardware confirm).
DANGEROUS = re.compile(
    r"\brm\s+-[a-zA-Z]*[rf]|\bgit\s+push\b|\bgit\s+reset\s+--hard|\bgit\s+clean\s+-[a-z]*f|"
    r"\bsudo\b|\bdeploy\b|\bdrop\s+(table|database)\b|\bmkfs\b|\bdd\s+if=|\bchmod\s+-R\b|"
    r"\bterraform\s+(apply|destroy)\b|\bkubectl\s+delete\b",
    re.IGNORECASE)


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

def tool_summary(tool: str, inp: dict) -> str:
    if not isinstance(inp, dict):
        return ""
    if tool == "Bash":
        return str(inp.get("command", ""))
    for key in ("file_path", "notebook_path", "url", "pattern", "path", "query"):
        if inp.get(key):
            return str(inp[key])
    return json.dumps(inp, ensure_ascii=False)[:300]


def is_dangerous(tool: str, inp: dict) -> bool:
    return tool == "Bash" and bool(DANGEROUS.search(tool_summary(tool, inp)))


def _base(event: dict) -> dict:
    return {"sid": str(event.get("session_id") or ""), "timeout": ASK_TIMEOUT}


# ---- PermissionRequest

def permission_request(event: dict) -> tuple[dict, list[str]]:
    tool = str(event.get("tool_name") or "Tool")
    inp = event.get("tool_input") or {}
    options = [ALLOW]
    if event.get("permission_suggestions"):
        options.append(ALLOW_ALWAYS)
    options += [DENY, AT_COMPUTER]
    confirm = [i for i, o in enumerate(options) if o in (ALLOW, ALLOW_ALWAYS)] \
        if is_dangerous(tool, inp) else []
    req = {**_base(event), "kind": "perm", "title": tool, "text": tool_summary(tool, inp),
           "options": options, "confirm": confirm}
    return req, options


def permission_output(event: dict, options: list[str], choices: list[int] | None) -> dict | None:
    if not choices or choices[0] >= len(options):
        return None
    pick = options[choices[0]]
    if pick == AT_COMPUTER:
        return None
    if pick == DENY:
        decision = {"behavior": "deny", "message": "Am Knob abgelehnt."}
    else:
        decision = {"behavior": "allow"}
        if pick == ALLOW_ALWAYS:
            decision["updatedPermissions"] = event.get("permission_suggestions") or []
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}


# ---- AskUserQuestion (PreToolUse)

def question_requests(event: dict) -> list[tuple[dict, dict, list[str]]]:
    """One knob prompt per question: (request, question, option labels)."""
    out = []
    questions = (event.get("tool_input") or {}).get("questions") or []
    for n, q in enumerate(questions):
        labels = [str(o.get("label", "")) for o in q.get("options") or [] if isinstance(o, dict)][:7]
        options = labels + [AT_COMPUTER]
        title = str(q.get("header") or "Frage")
        if len(questions) > 1:
            title = f"{title} ({n + 1}/{len(questions)})"
        req = {**_base(event), "kind": "ask", "title": title, "text": str(q.get("question", "")),
               "options": options, "multi": bool(q.get("multiSelect")), "confirm": []}
        out.append((req, q, options))
    return out


def question_output(event: dict, answers: dict) -> dict:
    inp = dict(event.get("tool_input") or {})
    inp["answers"] = answers
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": "allow", "updatedInput": inp}}


# ---- ExitPlanMode (PreToolUse)

PLAN_OK, PLAN_NO = "Annehmen", "Ablehnen"


def plan_request(event: dict) -> tuple[dict, list[str]]:
    plan = str((event.get("tool_input") or {}).get("plan") or "")
    first = next((ln.lstrip("# ").strip() for ln in plan.splitlines() if ln.strip()), "Plan")
    options = [PLAN_OK, PLAN_NO, AT_COMPUTER]
    req = {**_base(event), "kind": "plan", "title": first[:40], "text": plan,
           "options": options, "confirm": []}
    return req, options


def plan_output(event: dict, options: list[str], choices: list[int] | None) -> dict | None:
    if not choices or choices[0] >= len(options):
        return None
    pick = options[choices[0]]
    if pick == PLAN_OK:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                       "updatedInput": dict(event.get("tool_input") or {})}}
    if pick == PLAN_NO:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": "Plan am Knob abgelehnt."}}
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
        choices = ask(req)
        if not choices or any(c >= len(options) or options[c] == AT_COMPUTER for c in choices):
            return None      # any question not answered on the knob -> normal dialog
        answers[str(q.get("question", ""))] = ", ".join(options[c] for c in choices)
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
