"""control.py: what gets typed where — checked as data / script text, nothing is sent."""
import pytest

from bridge import control as c, protocol

TERM = c.Target("terminal", "/dev/ttys004")
DESK = c.Target("desktop")


def test_cli_model_clears_line_then_types_command():
    plan = c.plan_set(TERM, "model", "sonnet", {})
    assert plan.focus == ("terminal", "/dev/ttys004")
    assert plan.keys[0] == ("char", "e", ("control",))
    assert ("text", "/model sonnet") in plan.keys
    assert plan.keys[-1] == ("code", c.KEY_RETURN, ())


@pytest.mark.parametrize("cur,want,presses", [
    ("default", "plan", 2), ("plan", "default", 2), ("auto", "default", 1), ("acceptEdits", "acceptEdits", 0)])
def test_cli_mode_by_shift_tab_cycle(cur, want, presses):
    plan = c.plan_set(TERM, "mode", want, {"mode": cur})
    assert plan.keys == [("code", c.KEY_TAB, ("shift",))] * presses


def test_cli_fast_only_toggles_when_needed():
    assert c.plan_set(TERM, "fast", True, {"fast": True}).keys == []
    assert ("text", "/fast") in c.plan_set(TERM, "fast", True, {"fast": False}).keys


@pytest.mark.parametrize("key,value", [
    ("model", "sonnet; rm -rf ~"), ("model", "/clear"), ("effort", "ultra"), ("mode", "bypassPermissions"),
    ("mode", "dontAsk")])
def test_values_outside_allowlist_are_refused(key, value):
    with pytest.raises(c.Unsupported):
        c.plan_set(TERM, key, value, {"mode": "default"})


def test_full_model_ids_allowed():
    assert c.valid_model("claude-fable-5-1") and c.valid_model("claude-opus-5-5[1m]")
    assert not c.valid_model("claude-opus 5")


def test_desktop_uses_menus_not_text():
    plan = c.plan_set(DESK, "effort", "high", {})
    assert plan.focus == ("desktop",)
    assert plan.keys[0] == ("char", "e", ("command", "shift"))
    assert plan.keys[-1] == ("char", "3", ())
    assert not any(k[0] == "text" for k in plan.keys)
    assert c.plan_set(DESK, "mode", "plan", {}).keys[-1] == ("char", "4", ())


def test_desktop_menu_order_from_config():
    cfg = {"desktop_models": "default, opus, sonnet, haiku"}
    assert c.plan_set(DESK, "model", "sonnet", {}, cfg).keys[-1] == ("char", "3", ())
    with pytest.raises(c.Unsupported):
        c.plan_set(DESK, "model", "fable", {}, cfg)


def test_desktop_fast_unsupported():
    with pytest.raises(c.Unsupported):
        c.plan_set(DESK, "fast", True, {})


def test_applescript_for_terminal():
    s = c.applescript(c.plan_set(TERM, "model", "opus", {}))
    assert 'if tty of t is "/dev/ttys004" then' in s
    assert 'keystroke "/model opus"' in s
    assert 'keystroke "u" using {control down}' in s
    assert "key code 36" in s
    assert 'tell process "Terminal"' in s


def test_applescript_for_desktop_interrupt():
    s = c.applescript(c.plan_interrupt(DESK))
    assert s.startswith('tell application "Claude" to activate')
    assert "key code 53" in s
    assert 'keystroke "m" using {command down, shift down}' in c.applescript(c.plan_set(DESK, "mode", "auto", {}))


def test_bad_tty_refused():
    with pytest.raises(c.Unsupported):
        c.applescript(c.plan_switch(c.Target("terminal", '/dev/ttys1" & do shell script "x')))


def test_target_for_session():
    assert c.target_for(protocol.Session("s", "x", entrypoint="claude-desktop")).kind == "desktop"
    with pytest.raises(c.Unsupported):
        c.target_for(protocol.Session("s", "x", entrypoint="cli", pid=0))


def test_adapter_runs_script_and_reports():
    scripts, logs = [], []
    a = c.KeystrokeAdapter(log=logs.append, run=scripts.append, cfg={})
    s = protocol.Session("s", "web", entrypoint="claude-desktop", mode="default")
    assert a.set(s, "mode", "plan") is True and "key code" not in scripts[0]
    assert a.set(s, "fast", True) is False and "fast mode" in logs[-1]

    def refuse(_):
        raise c.Unsupported(c.ASSISTIVE_HINT)
    b = c.KeystrokeAdapter(log=logs.append, run=refuse, cfg={})
    assert b.interrupt(s) is False and "Accessibility" in logs[-1]


def test_control_is_off_unless_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "CONFIG_FILE", tmp_path / "config")
    assert c.adapter_from_config(lambda m: None) is None
    (tmp_path / "config").write_text("control = keys\n")
    assert isinstance(c.adapter_from_config(lambda m: None), c.KeystrokeAdapter)


def test_desktop_never_selects_bypass_permissions():
    with pytest.raises(c.Unsupported):
        c.plan_set(DESK, "mode", "bypassPermissions", {})


def test_desktop_mode_digits_follow_the_app_menu():
    assert c.plan_set(DESK, "mode", "auto", {}).keys[-1] == ("char", "1", ())
    assert c.plan_set(DESK, "mode", "default", {}).keys[-1] == ("char", "2", ())
    assert c.plan_set(DESK, "mode", "plan", {}).keys[-1] == ("char", "4", ())


def test_desktop_mode_menu_opens_on_the_knob_and_then_takes_only_the_digit():
    sent = []
    a = c.KeystrokeAdapter(log=lambda m: None, run=sent.append, cfg={})
    s = protocol.Session("d", "x", entrypoint="claude-desktop")
    assert a.menu(s, "mode", True)
    assert 'keystroke "m" using {command down, shift down}' in sent[-1]
    assert a.set(s, "mode", "plan")
    assert 'keystroke "m"' not in sent[-1] and 'keystroke "4"' in sent[-1]
    # the choice closed the menu: leaving the picker now presses nothing
    n = len(sent)
    assert a.menu(s, "mode", False) and len(sent) == n


def test_desktop_mode_menu_closed_with_esc_when_the_knob_goes_back():
    sent = []
    a = c.KeystrokeAdapter(log=lambda m: None, run=sent.append, cfg={})
    s = protocol.Session("d", "x", entrypoint="claude-desktop")
    a.menu(s, "mode", True)
    assert a.menu(s, "mode", False)
    assert "key code 53" in sent[-1]
    # a later set opens the menu itself again
    a.set(s, "mode", "auto")
    assert 'keystroke "m" using {command down, shift down}' in sent[-1]


def test_menu_command_parses():
    cmd = protocol.parse_command({"t": "menu", "sid": "abc", "k": "mode", "open": 1})
    assert cmd.t == "menu" and cmd.args == {"sid": "abc", "key": "mode", "open": True}
    assert protocol.parse_command({"t": "menu", "sid": "abc", "k": "model", "open": 1}) is None
