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
    assert ms.usage_payload([rl[1]], now=now) is None      # no 5-hour window yet: nothing, not 0 %
    assert ms.usage_payload([{"kind": "five_hour", "percentUsed": 100, "resetsAt": "x"}], now)["st"] == "limited"


def test_rate_limits_merge_to_the_current_window():
    reg = ms.ModRegistry(log=lambda m: None)
    now = 1_800_000_000
    soon, later = "2027-01-15T08:30:00Z", "2027-01-15T09:30:00Z"
    reg.report({"sid": "a", "rate_limits": [{"kind": "five_hour", "percentUsed": 60, "resetsAt": soon}]})
    reg.report({"sid": "b", "rate_limits": [{"kind": "five_hour", "percentUsed": 67, "resetsAt": soon},
                                            {"kind": "seven_day", "percentUsed": 71, "resetsAt": later}]})
    # a fresh session without a request yet, and one with an expired window
    reg.report({"sid": "c", "rate_limits": [{"kind": "five_hour", "percentUsed": 0, "resetsAt": None}]})
    # same window, reset a few seconds off, older reading
    reg.report({"sid": "e", "rate_limits": [{"kind": "five_hour", "percentUsed": 50, "resetsAt": "2027-01-15T08:30:04Z"}]})
    reg.report({"sid": "d", "rate_limits": [{"kind": "five_hour", "percentUsed": 3, "resetsAt": "2020-01-01T00:00:00Z"}]})
    for s in reg.sessions.values():
        s.rate_ts = now
    got = {r["kind"]: r["percentUsed"] for r in reg.rate_limits(now=now)}
    assert got == {"five_hour": 67, "seven_day": 71}
    reg.sessions["b"].rate_limits = []
    reg.sessions["a"].rate_limits = reg.sessions["e"].rate_limits = reg.sessions["d"].rate_limits = []
    assert reg.rate_limits(now=now) is None


def test_knob_follows_the_session_last_typed_in():
    reg = ms.ModRegistry(log=lambda m: None)
    reg.report({"sid": "a"})
    reg.report({"sid": "b"})
    assert reg.take_new_focus() is None
    reg.report({"sid": "b", "prompted": True})
    assert reg.take_new_focus() == "b"
    # once taken, a manual pick on the knob holds until the next prompt
    reg.report({"sid": "b"})
    assert reg.take_new_focus() is None
    reg.report({"sid": "a", "prompted": True})
    assert reg.take_new_focus() == "a"
    # after a bridge restart the heartbeats still say where the last prompt was
    fresh = ms.ModRegistry(log=lambda m: None)
    fresh.report({"sid": "a", "prompted_at": 1_000_000})
    fresh.report({"sid": "b", "prompted_at": 2_000_000})
    assert fresh.take_new_focus() == "b"


def test_mode_set_by_keystroke_survives_the_mods_heartbeat():
    reg = ms.ModRegistry(log=lambda m: None)
    reg.hello({"sid": "d", "entrypoint": "claude-desktop"})
    reg.report({"sid": "d", "mode": "auto"})

    class Keys:
        def set(self, s, k, v): return True

    assert control.ModAdapter(reg, Keys(), log=lambda m: None).set(protocol.Session("d", "x"), "mode", "plan")
    reg.report({"sid": "d", "mode": "auto"})          # heartbeat repeating the old reading
    assert reg.sessions["d"].mode == "plan"
    reg.report({"sid": "d", "mode": "acceptEdits"})   # a new reading from a prompt wins
    assert reg.sessions["d"].mode == "acceptEdits"
    reg.set_mode("d", "plan")
    reg.report({"sid": "d", "mode": "acceptEdits", "prompted": True})   # set back in the app, then typed
    assert reg.sessions["d"].mode == "acceptEdits"


def test_desktop_model_goes_to_the_app_menu_when_keys_are_on():
    calls = []

    class Keys:
        def set(self, s, k, v): calls.append((k, v)); return True

    reg = ms.ModRegistry(log=lambda m: None)
    reg.hello({"sid": "d", "entrypoint": "claude-desktop"})
    reg.hello({"sid": "t", "entrypoint": "cli"})
    a = control.ModAdapter(reg, Keys(), log=lambda m: None)
    assert a.set(protocol.Session("d", "x"), "model", "sonnet") and calls == [("model", "sonnet")]
    # CLI sessions and keys off: /model in the mod as before
    assert a.set(protocol.Session("t", "y"), "model", "sonnet") and reg.sessions["t"].commands[-1]["key"] == "model"
    assert control.ModAdapter(reg, None, log=lambda m: None).set(protocol.Session("d", "x"), "model", "opus")
    assert reg.sessions["d"].commands[-1] == {"t": "set", "key": "model", "value": "opus"}


def test_a_reset_window_reads_as_unused():
    reg = ms.ModRegistry(log=lambda m: None)
    now = 1_800_000_000
    reg.report({"sid": "a", "rate_limits": [{"kind": "five_hour", "percentUsed": 99, "resetsAt": "2020-01-01T00:00:00Z"},
                                            {"kind": "seven_day", "percentUsed": 44, "resetsAt": "2027-01-20T08:00:00Z"}]})
    reg.sessions["a"].rate_ts = now
    p = ms.usage_payload(reg.rate_limits(now=now), now=now)
    assert (p["s"], p["sr"], p["w"]) == (0.0, -1, 44.0)


def test_anim_from_mod_sessions():
    reg = ms.ModRegistry(log=lambda m: None)
    assert reg.anim() is None
    reg.report({"sid": "a", "state": protocol.THINKING})
    reg.report({"sid": "b", "state": protocol.IDLE})
    assert reg.anim() == "work think"
    reg.report({"sid": "b", "state": protocol.NEEDS_PERMISSION})
    assert reg.anim() == "allow"


def test_switch_shows_the_session_in_the_desktop_app():
    reg = ms.ModRegistry(log=lambda m: None)
    hid = "local_77d3da33-d266-432a-9855-91c08f67262f"
    reg.hello({"sid": "a", "entrypoint": "claude-desktop", "host_id": hid})
    reg.hello({"sid": "b", "entrypoint": "cli"})
    reg.hello({"sid": "c", "entrypoint": "claude-desktop", "host_id": "local_x; rm -rf /"})
    ran = []
    a = control.ModAdapter(reg, log=lambda m: None, run=ran.append)
    assert a.switch(protocol.Session("a", "hanse"))
    assert ran == [["open", f"claude://claude.ai/epitaxy/{hid}"]]
    # no desktop id, or not one: the knob follows, nothing is opened
    assert a.switch(protocol.Session("b", "cli")) and a.switch(protocol.Session("c", "x"))
    assert len(ran) == 1


def test_desktop_titles_name_the_sessions_and_give_the_app_id(tmp_path):
    from bridge.desktop_sessions import DesktopSessions
    d = tmp_path / "acct" / "org"
    d.mkdir(parents=True)
    hid = "local_77d3da33-d266-432a-9855-91c08f67262f"
    (d / f"{hid}.json").write_text(json.dumps(
        {"sessionId": hid, "cliSessionId": "a", "title": "Spielstart-Befehl", "isArchived": False}))
    (d / "local_old.json").write_text(json.dumps(
        {"sessionId": "local_old", "cliSessionId": "b", "title": "Alt", "isArchived": True}))
    (d / "local_bad.json").write_text("{")
    reg = ms.ModRegistry(log=lambda m: None, desktop=DesktopSessions(tmp_path))
    for sid in ("a", "b"):
        reg.hello({"sid": sid, "cwd": "/Users/t/hanse_merchant"})
    labels = {v.sid: v.label for v in reg.views()}
    assert labels == {"a": "Spielstart-Befehl", "b": "hanse_merchant"}
    # without the mod's own report, the app's id still comes from its files
    assert reg.host_id("a") == hid and reg.host_id("b") == ""
    ran = []
    assert control.ModAdapter(reg, log=lambda m: None, run=ran.append).switch(protocol.Session("a", "x"))
    assert ran == [["open", f"claude://claude.ai/epitaxy/{hid}"]]


def test_sessions_come_most_recently_active_first(tmp_path):
    from bridge.desktop_sessions import DesktopSessions
    d = tmp_path / "acct" / "org"
    d.mkdir(parents=True)
    for cli, last in (("old", 1_000_000), ("recent", 9_000_000)):
        (d / f"local_{cli}.json").write_text(json.dumps(
            {"sessionId": f"local_{cli}", "cliSessionId": cli, "title": cli, "lastActivityAt": last}))
    reg = ms.ModRegistry(log=lambda m: None, desktop=DesktopSessions(tmp_path))
    for sid in ("old", "recent", "cli"):
        reg.hello({"sid": sid})
    assert [v.sid for v in reg.views()] == ["recent", "old", "cli"]
    # a prompt typed now beats the app's older record
    reg.report({"sid": "old", "prompted": True})
    assert [v.sid for v in reg.views()][0] == "old"
