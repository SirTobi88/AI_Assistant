"""End to end without hardware: knob_hook -> bridge (virtual mode) -> simulator
binary -> scripted ring/touch input -> answer back to the hook.

Needs the simulator build (`pio run -d firmware -e sim`); skipped otherwise.
No BLE and no usage API calls (--no-usage).
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from bridge import knob_hook as kh

ROOT = Path(__file__).resolve().parents[2]
SIM = ROOT / "firmware" / ".pio" / "build" / "sim" / "program"

pytestmark = pytest.mark.skipif(not SIM.exists(), reason="simulator not built")


def _wait_for(path: Path, timeout: float = 10) -> None:
    t0 = time.time()
    while not path.exists():
        if time.time() - t0 > timeout:
            raise TimeoutError(path)
        time.sleep(0.05)


def _run(sim_input: str, request: dict, ask_at: float):
    d = Path(tempfile.mkdtemp(prefix="knob", dir="/tmp"))
    hub_sock, knob_sock = d / "hub.sock", d / "knob.sock"
    bridge = subprocess.Popen(
        [sys.executable, "-m", "bridge.claude_usage_daemon", "--virtual", str(knob_sock),
         "--no-usage", "--hub-socket", str(hub_sock),
         "--mod-socket", str(hub_sock.parent / "mod.sock")],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    sim = None
    try:
        _wait_for(knob_sock)
        env = {**os.environ, "SDL_VIDEODRIVER": "dummy", "SIM_BRIDGE": str(knob_sock),
               "SIM_INPUT": sim_input, "SIM_AUTOSHOT_MS": "9000", "SIM_AUTOSHOT_PATH": "/dev/null"}
        # `script` gives the sim a tty so its stdout is line-buffered and readable here.
        sim = subprocess.Popen(["script", "-q", "/dev/null", str(SIM)], cwd=ROOT / "firmware", env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True)
        lines, linked = [], threading.Event()
        def read_sim():
            for line in sim.stdout:
                lines.append(line)
                if '"t":"hello"' in line:     # the knob is attached to the hub
                    linked.set()
        reader = threading.Thread(target=read_sim, daemon=True)
        reader.start()
        assert linked.wait(15), "".join(lines)
        time.sleep(ask_at)
        choices = kh.ask_bridge(request, hub_sock)
        sim.wait(timeout=20)
        reader.join(timeout=5)
        return choices, "".join(lines)
    finally:
        if sim and sim.poll() is None:
            sim.kill()
        bridge.terminate()
        bridge_out, _ = bridge.communicate(timeout=10)
        print(bridge_out)


PUSH = {"sid": "", "kind": "perm", "title": "Bash", "text": "git push origin main",
        "options": [kh.ALLOW, kh.DENY, kh.AT_COMPUTER], "confirm": [0], "timeout": 15}


def test_hold_confirms_dangerous_allow():
    # The prompt goes out once the sim said hello (well before 4 s); hold from 4 s for 2 s.
    choices, out = _run("4000:h2000", PUSH, ask_at=0.3)
    assert choices == [0], out
    assert '"t":"answer"' in out and '"c":[0]' in out


def test_tap_on_dangerous_allow_does_not_answer_but_deny_does():
    # Tap on "Erlauben" only shows the hint; one detent on, tap "Ablehnen".
    choices, out = _run("4000:t,4800:d,5200:t", PUSH, ask_at=0.3)
    assert choices == [1], out


def test_double_tap_leaves_prompt_unanswered_until_timeout():
    req = {**PUSH, "timeout": 4}
    choices, _ = _run("4000:t,4150:t", req, ask_at=0.3)
    assert choices is None
