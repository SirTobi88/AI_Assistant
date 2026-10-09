"""Knob hooks, hub and session reader (Phase 2/3 bridge side)."""
import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

from bridge import knob_activity, knob_hook as kh, monitor, protocol, sessions
from bridge.hub import Hub

BASH_PUSH = {"hook_event_name": "PermissionRequest", "session_id": "s1", "tool_name": "Bash",
             "tool_input": {"command": "git push origin main"},
             "permission_suggestions": [{"type": "addRules", "behavior": "allow",
                                         "rules": [{"toolName": "Bash", "ruleContent": "git push:*"}],
                                         "destination": "localSettings"}]}

QUESTION = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "AskUserQuestion",
            "tool_input": {"questions": [
                {"question": "Which framework?", "header": "Framework", "multiSelect": False,
                 "options": [{"label": "React"}, {"label": "Vue"}]},
                {"question": "Which extras?", "header": "Extras", "multiSelect": True,
                 "options": [{"label": "Router"}, {"label": "State"}, {"label": "Tests"}]}]}}


# ---- knob_hook

def test_dangerous_command_needs_hold_on_allow_options():
    req, options = kh.permission_request(BASH_PUSH)
    assert options == [kh.ALLOW, kh.ALLOW_ALWAYS, kh.DENY, kh.AT_COMPUTER]
    assert req["confirm"] == [0, 1]
    assert req["text"] == "git push origin main"


def test_harmless_command_is_a_tap():
    ev = {**BASH_PUSH, "tool_input": {"command": "ls -la"}, "permission_suggestions": []}
    req, options = kh.permission_request(ev)
    assert options == [kh.ALLOW, kh.DENY, kh.AT_COMPUTER]
    assert req["confirm"] == []


@pytest.mark.parametrize("choice,expected", [
    ([0], {"behavior": "allow"}),
    ([2], {"behavior": "deny", "message": "Am Knob abgelehnt."}),
])
def test_permission_output(choice, expected):
    out = kh.run_perm(BASH_PUSH, ask=lambda req: choice)
    assert out["hookSpecificOutput"]["decision"] == expected


def test_allow_always_carries_suggested_rules():
    out = kh.run_perm(BASH_PUSH, ask=lambda req: [1])
    d = out["hookSpecificOutput"]["decision"]
    assert d["behavior"] == "allow"
    assert d["updatedPermissions"] == BASH_PUSH["permission_suggestions"]


@pytest.mark.parametrize("answer", [None, [3]])
def test_no_answer_or_at_computer_falls_back_to_dialog(answer):
    assert kh.run_perm(BASH_PUSH, ask=lambda req: answer) is None


def test_question_tools_are_left_to_pretooluse():
    asked = []
    assert kh.run_perm({**BASH_PUSH, "tool_name": "AskUserQuestion"}, ask=asked.append) is None
    assert asked == []


def test_questions_answered_one_after_another():
    replies = iter([[1], [0, 2]])
    reqs = []
    out = kh.run_pre(QUESTION, ask=lambda r: (reqs.append(r), next(replies))[1])
    assert [r["multi"] for r in reqs] == [False, True]
    assert reqs[0]["title"] == "Framework (1/2)"
    assert reqs[1]["options"][-1] == kh.AT_COMPUTER
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "allow"
    assert hso["updatedInput"]["questions"] == QUESTION["tool_input"]["questions"]
    assert hso["updatedInput"]["answers"] == {"Which framework?": "Vue", "Which extras?": "Router, Tests"}


def test_question_at_computer_aborts_whole_tool():
    replies = iter([[0], [3]])     # second question: "Am Rechner"
    assert kh.run_pre(QUESTION, ask=lambda r: next(replies)) is None


def test_plan_accept_and_reject():
    ev = {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "ExitPlanMode",
          "tool_input": {"plan": "# Refactor auth\n1. Extract", "planFilePath": "/p.md"}}
    ok = kh.run_pre(ev, ask=lambda r: [0])["hookSpecificOutput"]
    assert ok["permissionDecision"] == "allow" and ok["updatedInput"]["planFilePath"] == "/p.md"
    no = kh.run_pre(ev, ask=lambda r: [1])["hookSpecificOutput"]
    assert no["permissionDecision"] == "deny"
    req, _ = kh.plan_request(ev)
    assert req["title"] == "Refactor auth" and req["kind"] == "plan"


def test_install_keeps_foreign_hooks(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "other.sh"}]}]}, "model": "opus"}))
    kh.set_installed(True, p)
    kh.set_installed(True, p)      # idempotent
    s = json.loads(p.read_text())
    pre = s["hooks"]["PreToolUse"]
    assert len(pre) == 2 and pre[0]["hooks"][0]["command"] == "other.sh"
    assert pre[1]["matcher"] == "AskUserQuestion|ExitPlanMode"
    assert s["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"] == kh.HOOK_TIMEOUT
    kh.set_installed(False, p)
    s = json.loads(p.read_text())
    assert "PermissionRequest" not in s["hooks"] and len(s["hooks"]["PreToolUse"]) == 1
    assert s["model"] == "opus"


# ---- hub over the real socket

class FakeLink:
    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)
        return True


@pytest.fixture
def sock_path():
    # AF_UNIX paths are short on macOS; tmp_path is too deep.
    d = tempfile.mkdtemp(prefix="knob", dir="/tmp")
    yield Path(d) / "b.sock"


def test_hook_to_knob_and_back(sock_path):
    async def go():
        hub = Hub(log=lambda m: None, socket_path=sock_path)
        await hub.start()
        link = FakeLink()
        await hub.attach(link)
        assert link.sent[0]["t"] == "options"

        req, options = kh.permission_request(BASH_PUSH)
        hook = asyncio.create_task(asyncio.to_thread(kh.ask_bridge, req, sock_path))
        for _ in range(100):
            prompts = [m for m in link.sent if m["t"] == "prompt"]
            if prompts:
                break
            await asyncio.sleep(0.01)
        assert prompts[0]["o"] == options and prompts[0]["d"] == 3
        hub.on_command(protocol.Command("answer", {"id": prompts[0]["id"], "choices": [2]}))
        assert await hook == [2]
        assert not any(m["t"] == "prompt_done" for m in link.sent)   # knob dropped it itself
        await hub.stop()
    asyncio.run(go())


def test_no_knob_means_immediate_fallback(sock_path):
    async def go():
        hub = Hub(log=lambda m: None, socket_path=sock_path)
        await hub.start()
        req, _ = kh.permission_request(BASH_PUSH)
        assert await asyncio.to_thread(kh.ask_bridge, req, sock_path) is None
        await hub.stop()
    asyncio.run(go())


def test_timeout_withdraws_prompt():
    async def go():
        hub = Hub(log=lambda m: None, socket_path=Path("/tmp/unused.sock"))
        link = FakeLink()
        await hub.attach(link)
        p = protocol.Prompt("p1", "s", "ask", "T", "x", ["a", "b"], timeout=0)
        assert await hub.ask(p) is None
        assert link.sent[-1] == {"t": "prompt_done", "id": "p1"}
    asyncio.run(go())


def test_bridge_not_running():
    assert kh.ask_bridge({"options": ["a"]}, Path("/tmp/does-not-exist.sock")) is None


def test_commands_resolve_short_session_ids():
    calls = []

    class A:
        def switch(self, s): calls.append(("switch", s.sid)); return True
        def set(self, s, k, v): calls.append(("set", s.sid, k, v, getattr(s, k))); return True
        def interrupt(self, s): calls.append(("int", s.sid)); return True

    async def go():
        hub = Hub(adapter=A(), log=lambda m: None, socket_path=Path("/tmp/unused.sock"))
        full = "0f6c3b2a-1111-2222-3333-444455556666"
        await hub.set_sessions([protocol.Session(full, "repo"), protocol.Session("other-id-000000", "x")])
        short = protocol.short_sid(full)
        hub.on_command(protocol.Command("switch", {"sid": short}))
        hub.on_command(protocol.Command("set", {"sid": short, "key": "model", "value": "sonnet"}))
        hub.on_command(protocol.Command("interrupt", {"sid": "nope"}))
        await asyncio.sleep(0)
        # the adapter saw the value before the change
        assert calls == [("switch", full), ("set", full, "model", "sonnet", "")]
        assert hub.active == full and hub.sessions[0].model == "sonnet"
    asyncio.run(go())


# ---- sessions from hook files + transcripts

def _hook(tmp, env=None, **ev):
    knob_activity.handle_hook(json.dumps({"session_id": "abc", **ev}), now=1000.0, activity_dir=tmp,
                              environ=env or {})


def test_sessions_from_hooks_and_transcript(tmp_path):
    tp = tmp_path / "t.jsonl"
    tp.write_text("\n".join(json.dumps(r) for r in [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-5-5",
                                          "usage": {"input_tokens": 1000, "cache_read_input_tokens": 99000,
                                                    "cache_creation_input_tokens": 24000}}},
    ]) + "\n")
    act = tmp_path / "act"
    _hook(act, hook_event_name="SessionStart", cwd="/x/AI_Assistant", transcript_path=str(tp),
          permission_mode="plan")
    _hook(act, hook_event_name="PermissionRequest", tool_name="Bash")
    [s] = sessions.read_sessions(act, now=1001.0)
    assert (s.sid, s.label, s.state, s.model, s.mode, s.ctx_pct) == \
        ("abc", "AI_Assistant", protocol.NEEDS_PERMISSION, "opus", "plan", 62)
    assert sessions.context_tokens_k(act, "abc") == 124
    # an old permission prompt decays to idle
    [s] = sessions.read_sessions(act, now=1000.0 + knob_activity.ALLOW_STALE_S + 1)
    assert s.state == protocol.IDLE


def test_question_marks_session_asking(tmp_path):
    _hook(tmp_path, hook_event_name="PreToolUse", tool_name="AskUserQuestion", cwd="/r")
    assert sessions.read_sessions(tmp_path, now=1000.0)[0].state == protocol.ASKING


# ---- monitor

def test_burn_rate_and_eta():
    b = monitor.BurnTracker()
    b.add(40, now=0)
    b.add(50, now=1800)          # 10 % in 30 min = 20 %/h
    assert b.rate() == pytest.approx(20)
    assert b.eta_minutes(reset_minutes=300) == 150
    assert b.eta_minutes(reset_minutes=60) == -1     # resets before it runs out
    b.add(2, now=1900)           # window reset clears history
    assert b.rate() == 0 and b.eta_minutes(100) == -1


# ---- side by side with Clawdmeter / AI_Limitmeter

CLAWDMETER_CMD = '"/Users/x/Clawdmeter/daemon/.venv/bin/python" "/Users/x/Clawdmeter/daemon/clawd_activity.py" --hook'


def test_installers_leave_clawdmeter_hooks_alone(tmp_path):
    p = tmp_path / "settings.json"
    theirs = {ev: [{"matcher": "*", "hooks": [{"type": "command", "command": CLAWDMETER_CMD}]}]
              for ev in ("PreToolUse", "PermissionRequest", "Stop")}
    p.write_text(json.dumps({"hooks": theirs}))
    knob_activity.set_installed(True, p)
    kh.set_installed(True, p)
    hooks = json.loads(p.read_text())["hooks"]
    for ev in theirs:
        cmds = [h["command"] for g in hooks[ev] for h in g["hooks"]]
        assert CLAWDMETER_CMD in cmds
    assert not knob_activity.is_installed(tmp_path / "missing.json")
    knob_activity.set_installed(False, p)
    kh.set_installed(False, p)
    assert json.loads(p.read_text())["hooks"] == theirs


def test_own_activity_dir():
    assert knob_activity.ACTIVITY_DIR.parts[-2:] == ("ai-knob", "activity")


def test_hook_records_where_the_session_runs(tmp_path):
    _hook(tmp_path, env={"CLAUDE_CODE_ENTRYPOINT": "cli", "CLAUDE_PID": "4242", "CLAUDE_EFFORT": "high"},
          hook_event_name="SessionStart", cwd="/r")
    [s] = sessions.read_sessions(tmp_path, now=1000.0)
    assert (s.entrypoint, s.pid, s.effort) == ("cli", 4242, "high")


def test_failed_control_reverts_the_knob():
    async def go():
        hub = Hub(log=lambda m: None, socket_path=Path("/tmp/unused.sock"))   # LoggingAdapter: fails
        link = FakeLink()
        await hub.attach(link)
        await hub.set_sessions([protocol.Session("abcdef0123456789", "r", fast=False)])
        n = len(link.sent)
        hub.on_command(protocol.Command("set", {"sid": "abcdef012345", "key": "fast", "value": True}))
        await asyncio.sleep(0.01)
        assert hub.sessions[0].fast is False
        assert link.sent[n]["t"] == "sessions" and link.sent[n]["ss"][0][7] == 0
    asyncio.run(go())
