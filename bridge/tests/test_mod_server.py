"""The mod endpoint over a real Unix socket, with a fake mod speaking HTTP."""
import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from bridge import control, mod_server as ms, prompts, protocol
from bridge.hub import Hub


class FakeLink:
    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)
        return True


async def http(sock: Path, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    r, w = await asyncio.open_unix_connection(str(sock))
    data = json.dumps(body).encode() if body is not None else b""
    w.write(f"{method} {path} HTTP/1.1\r\nHost: bridge\r\nContent-Length: {len(data)}\r\n\r\n".encode() + data)
    await w.drain()
    raw = await r.read()
    w.close()
    head, _, payload = raw.partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), json.loads(payload)


@pytest.fixture
def sock():
    return Path(tempfile.mkdtemp(prefix="knob", dir="/tmp")) / "mod.sock"


async def setup(sock, knob=True):
    hub = Hub(log=lambda m: None, socket_path=sock.parent / "hub.sock")
    reg = ms.ModRegistry(log=lambda m: None)
    hub.adapter = control.ModAdapter(reg, None, log=lambda m: None)
    server = ms.ModServer(hub, reg, sock, log=lambda m: None)
    await server.start()
    link = FakeLink()
    if knob:
        await hub.attach(link)
    return hub, reg, server, link


SID = "0f6c3b2a-1111-2222-3333-444455556666"


def test_hello_state_and_session_view(sock):
    async def go():
        hub, reg, server, _ = await setup(sock)
        st, rep = await http(sock, "POST", "/v1/hello", {"sid": SID, "cwd": "/x/AI_Assistant",
                                                          "entrypoint": "claude-desktop"})
        assert st == 200 and rep["knob"] is True
        await http(sock, "POST", "/v1/state", {"sid": SID, "state": protocol.TOOL, "model": "claude-opus-5-5",
                                               "effort": "high", "mode": "auto", "ctx_pct": 51, "tokens": 512258})
        [s] = reg.views()
        assert (s.label, s.state, s.model, s.effort, s.mode, s.ctx_pct, s.entrypoint) == \
            ("AI_Assistant", protocol.TOOL, "opus", "high", "auto", 51, "claude-desktop")
        await http(sock, "POST", "/v1/bye", {"sid": SID})
        assert reg.views() == []
        await server.stop()
    asyncio.run(go())


def test_knob_command_reaches_the_mod_by_long_poll(sock):
    async def go():
        hub, reg, server, _ = await setup(sock)
        await http(sock, "POST", "/v1/hello", {"sid": SID, "cwd": "/r"})
        await hub.set_sessions(reg.views())
        poll = asyncio.create_task(http(sock, "GET", f"/v1/commands?sid={SID}&wait=5"))
        await asyncio.sleep(0.05)
        short = protocol.short_sid(SID)
        hub.on_command(protocol.Command("set", {"sid": short, "key": "model", "value": "sonnet"}))
        st, rep = await asyncio.wait_for(poll, 2)
        assert rep["commands"] == [{"t": "set", "key": "model", "value": "sonnet"}]
        # shown as pending until the mod reports the result
        assert reg.views()[0].model == "sonnet"
        await http(sock, "POST", "/v1/result", {"sid": SID, "ok": False, "error": "x",
                                                "cmd": {"t": "set", "key": "model", "value": "sonnet"}})
        assert reg.views()[0].model == ""
        hub.on_command(protocol.Command("interrupt", {"sid": short}))
        st, rep = await http(sock, "GET", f"/v1/commands?sid={SID}&wait=1")
        assert rep["commands"] == [{"t": "interrupt"}]
        await server.stop()
    asyncio.run(go())


def test_long_poll_times_out_empty(sock):
    async def go():
        hub, reg, server, _ = await setup(sock)
        st, rep = await http(sock, "GET", f"/v1/commands?sid={SID}&wait=0.2")
        assert st == 200 and rep["commands"] == []
        await server.stop()
    asyncio.run(go())


def test_mode_and_unknown_sessions_go_to_fallback():
    calls = []

    class Keys:
        def set(self, s, k, v): calls.append((s.sid, k, v)); return True
        def interrupt(self, s): calls.append((s.sid, "int")); return True
        def switch(self, s): return True

    reg = ms.ModRegistry(log=lambda m: None)
    reg.get("a")
    a = control.ModAdapter(reg, Keys(), log=lambda m: None)
    assert a.set(protocol.Session("a", "x"), "mode", "plan") and calls == [("a", "mode", "plan")]
    assert a.set(protocol.Session("a", "x"), "effort", "high") and reg.sessions["a"].commands[-1]["key"] == "effort"
    assert a.interrupt(protocol.Session("b", "y")) and calls[-1] == ("b", "int")
    assert not a.set(protocol.Session("a", "x"), "model", "rm -rf /")
    assert not control.ModAdapter(reg, None, log=lambda m: None).set(protocol.Session("a", "x"), "mode", "auto")


async def next_prompt(link, seen: int) -> dict:
    """Wait for the knob to receive prompt number ``seen`` (0-based)."""
    for _ in range(200):
        sent = [m for m in link.sent if m["t"] == "prompt"]
        if len(sent) > seen:
            return sent[seen]
        await asyncio.sleep(0.01)
    raise AssertionError("no prompt reached the knob")


def test_ask_permission_question_and_plan(sock):
    async def go():
        hub, reg, server, link = await setup(sock)

        async def ask(body, choices_list):
            base = len([m for m in link.sent if m["t"] == "prompt"])
            task = asyncio.create_task(http(sock, "POST", "/v1/ask", body))
            for n, choices in enumerate(choices_list):
                p = await next_prompt(link, base + n)
                hub.on_command(protocol.Command("answer", {"id": p["id"], "choices": choices}))
            return (await asyncio.wait_for(task, 3))[1]

        perm = {"sid": SID, "what": "permission", "tool": "Bash", "input": {"command": "git push"},
                "suggestions": [{"type": "addRules", "behavior": "allow", "rules": [], "destination": "session"}]}
        rep = await ask(perm, [[1]])
        assert rep == {"decision": {"behavior": "allow", "updatedPermissions": perm["suggestions"]}}
        prompt_msg = [m for m in link.sent if m["t"] == "prompt"][-1]
        assert prompt_msg["d"] == 3 and prompt_msg["o"][-1] == prompts.AT_COMPUTER

        q = {"sid": SID, "what": "question", "input": {"questions": [
            {"question": "Which?", "header": "H", "options": [{"label": "A"}, {"label": "B"}]}]}}
        assert await ask(q, [[1]]) == {"answers": {"Which?": "B"}}

        plan = {"sid": SID, "what": "plan", "input": {"plan": "# Do it"}}
        assert await ask(plan, [[1]]) == {"verdict": False}
        await server.stop()
    asyncio.run(go())


def test_ask_without_knob_returns_null_at_once(sock):
    async def go():
        hub, reg, server, _ = await setup(sock, knob=False)
        st, rep = await asyncio.wait_for(
            http(sock, "POST", "/v1/ask", {"sid": SID, "what": "permission", "tool": "Bash", "input": {}}), 2)
        assert rep == {"decision": None}
        await server.stop()
    asyncio.run(go())


def test_bad_requests(sock):
    async def go():
        hub, reg, server, _ = await setup(sock)
        assert (await http(sock, "POST", "/v1/state", {"state": 1}))[0] == 400
        assert (await http(sock, "POST", "/v1/nope", {"sid": "x"}))[0] == 404
        assert (await http(sock, "POST", "/v1/ask", {"sid": "x", "what": "rm"}))[0] == 400
        await server.stop()
    asyncio.run(go())


def test_desktop_mode_goes_to_the_mod():
    calls = []

    class Keys:
        def set(self, s, k, v): calls.append((s.sid, k, v)); return True

    reg = ms.ModRegistry(log=lambda m: None)
    reg.hello({"sid": "d", "entrypoint": "claude-desktop"})
    a = control.ModAdapter(reg, None, log=lambda m: None)
    assert a.set(protocol.Session("d", "x"), "mode", "plan")
    assert reg.sessions["d"].commands[-1] == {"t": "set", "key": "mode", "value": "plan"}
    assert not a.set(protocol.Session("d", "x"), "mode", "bypassPermissions")
    # with keystrokes enabled they win: the app refuses the mod's call in auto mode
    assert control.ModAdapter(reg, Keys(), log=lambda m: None).set(protocol.Session("d", "x"), "mode", "auto")
    assert calls == [("d", "mode", "auto")]


def test_stale_sessions_are_dropped():
    reg = ms.ModRegistry(log=lambda m: None)
    reg.get("a").seen = 0
    assert reg.views(now=ms.GONE_AFTER_S + 1) == []


def test_usage_payload_from_rate_limits():
    now = 1_800_000_000
    rl = [{"kind": "five_hour", "percentUsed": 17, "resetsAt": "2027-01-15T08:30:00.000Z"},
          {"kind": "seven_day", "percentUsed": 42, "resetsAt": "2027-01-20T08:00:00Z"}]
    p = ms.usage_payload(rl, now=now)
    assert (p["s"], p["w"], p["st"], p["ok"]) == (17.0, 42.0, "allowed", True)
    assert p["sr"] == round((1_800_001_800 - now) / 60) and p["sr"] == 30
    assert ms.usage_payload([], now=now) is None
    assert ms.usage_payload([{"kind": "five_hour", "percentUsed": 100, "resetsAt": "x"}], now)["st"] == "limited"


def test_freshest_rate_limits_win():
    reg = ms.ModRegistry(log=lambda m: None)
    reg.report({"sid": "a", "rate_limits": [{"kind": "five_hour", "percentUsed": 1}]})
    reg.report({"sid": "b", "rate_limits": [{"kind": "five_hour", "percentUsed": 2}]})
    assert reg.rate_limits()[0]["percentUsed"] == 2
