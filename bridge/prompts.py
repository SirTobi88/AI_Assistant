"""What the knob asks for a permission request, a question or a plan, and how
its answer reads. Shared by the settings hook (knob_hook.py) and the mod
endpoint (mod_server.py), so both ask the same way. Stdlib only, no I/O.
"""
from __future__ import annotations

import json
import re

ASK_TIMEOUT = 30           # seconds the knob gets; the normal dialog waits that long

ALLOW, ALLOW_ALWAYS, DENY, AT_COMPUTER = "Erlauben", "Immer erlauben", "Ablehnen", "Am Rechner"
PLAN_OK, PLAN_NO = "Annehmen", "Ablehnen"
DENY_MESSAGE = "Am Knob abgelehnt."
PLAN_DENY_MESSAGE = "Plan am Knob abgelehnt."

# Commands that only go through with a hold on the knob (hardware confirm).
DANGEROUS = re.compile(
    r"\brm\s+-[a-zA-Z]*[rf]|\bgit\s+push\b|\bgit\s+reset\s+--hard|\bgit\s+clean\s+-[a-z]*f|"
    r"\bsudo\b|\bdeploy\b|\bdrop\s+(table|database)\b|\bmkfs\b|\bdd\s+if=|\bchmod\s+-R\b|"
    r"\bterraform\s+(apply|destroy)\b|\bkubectl\s+delete\b",
    re.IGNORECASE)


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


def _base(sid: str) -> dict:
    return {"sid": str(sid or ""), "timeout": ASK_TIMEOUT}


def _pick(options: list[str], choices: list[int] | None) -> str | None:
    if not choices or choices[0] >= len(options):
        return None
    return options[choices[0]]


# ---- permission request (any tool)

def permission_request(tool: str, tool_input: dict, suggestions: list | None,
                       sid: str = "") -> tuple[dict, list[str]]:
    tool = str(tool or "Tool")
    inp = tool_input if isinstance(tool_input, dict) else {}
    options = [ALLOW] + ([ALLOW_ALWAYS] if suggestions else []) + [DENY, AT_COMPUTER]
    confirm = [i for i, o in enumerate(options) if o in (ALLOW, ALLOW_ALWAYS)] \
        if is_dangerous(tool, inp) else []
    req = {**_base(sid), "kind": "perm", "title": tool, "text": tool_summary(tool, inp),
           "options": options, "confirm": confirm}
    return req, options


def permission_decision(options: list[str], choices: list[int] | None,
                        suggestions: list | None) -> dict | None:
    """The PermissionRequest `decision`, or None for the normal dialog."""
    pick = _pick(options, choices)
    if pick in (None, AT_COMPUTER):
        return None
    if pick == DENY:
        return {"behavior": "deny", "message": DENY_MESSAGE}
    decision = {"behavior": "allow"}
    if pick == ALLOW_ALWAYS:
        decision["updatedPermissions"] = list(suggestions or [])
    return decision


# ---- AskUserQuestion

def question_requests(questions: list, sid: str = "") -> list[tuple[dict, dict, list[str]]]:
    """One knob prompt per question: (request, question, option labels)."""
    out = []
    questions = [q for q in questions or [] if isinstance(q, dict)]
    for n, q in enumerate(questions):
        labels = [str(o.get("label", "")) for o in q.get("options") or [] if isinstance(o, dict)][:7]
        options = labels + [AT_COMPUTER]
        title = str(q.get("header") or "Frage")
        if len(questions) > 1:
            title = f"{title} ({n + 1}/{len(questions)})"
        req = {**_base(sid), "kind": "ask", "title": title, "text": str(q.get("question", "")),
               "options": options, "multi": bool(q.get("multiSelect")), "confirm": []}
        out.append((req, q, options))
    return out


def question_answer(options: list[str], choices: list[int] | None) -> str | None:
    """The answer text (multi-select comma-joined), or None: leave it to the dialog."""
    if not choices or any(c >= len(options) or options[c] == AT_COMPUTER for c in choices):
        return None
    return ", ".join(options[c] for c in choices)


# ---- ExitPlanMode

def plan_request(plan: str, sid: str = "") -> tuple[dict, list[str]]:
    plan = str(plan or "")
    first = next((ln.lstrip("# ").strip() for ln in plan.splitlines() if ln.strip()), "Plan")
    options = [PLAN_OK, PLAN_NO, AT_COMPUTER]
    req = {**_base(sid), "kind": "plan", "title": first[:40], "text": plan,
           "options": options, "confirm": []}
    return req, options


def plan_verdict(options: list[str], choices: list[int] | None) -> bool | None:
    """True = accepted, False = rejected, None = the dialog decides."""
    pick = _pick(options, choices)
    return True if pick == PLAN_OK else False if pick == PLAN_NO else None
