"""Push-to-talk: the knob's C encoder against the bridge's decoder, the take,
Whisper (real, when installed), and the hub's flow from ptt to prompt."""
import asyncio
import math
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

import pytest

from bridge import protocol, voice
from bridge.hub import Hub

ROOT = Path(__file__).resolve().parents[2]
FRAME = 256


@pytest.fixture(scope="module")
def encoder(tmp_path_factory):
    """firmware/src/adpcm.c built on the host, framed as voice.cpp frames it:
    raw int16 on stdin -> [seq][predictor][index] + nibbles per 256 samples."""
    cc = shutil.which("cc")
    if not cc:
        pytest.skip("no C compiler")
    d = tmp_path_factory.mktemp("adpcm")
    (d / "main.c").write_text(r'''
#include <stdio.h>
#include "adpcm.h"
int main(void) {
    adpcm_state_t st; adpcm_reset(&st);
    int16_t in[256]; uint8_t out[128]; unsigned short seq = 0; size_t n;
    while ((n = fread(in, 2, 256, stdin)) > 0) {
        if (n & 1) in[n++] = 0;
        unsigned char head[5] = {seq & 255, seq >> 8, (unsigned short)st.predictor & 255,
                                 (unsigned short)st.predictor >> 8, st.index};
        adpcm_encode(&st, in, n, out);
        unsigned short len = 5 + n / 2;
        fwrite(&len, 2, 1, stdout); fwrite(head, 1, 5, stdout); fwrite(out, 1, n / 2, stdout);
        seq++;
    }
    return 0;
}''')
    exe = d / "enc"
    subprocess.run([cc, "-O2", "-I", str(ROOT / "firmware/src"), str(d / "main.c"),
                    str(ROOT / "firmware/src/adpcm.c"), "-o", str(exe)], check=True)

    def encode(samples: list[int]) -> list[bytes]:
        raw = subprocess.run([str(exe)], input=struct.pack(f"<{len(samples)}h", *samples),
                             capture_output=True, check=True).stdout
        frames, i = [], 0
        while i < len(raw):
            n = struct.unpack_from("<H", raw, i)[0]
            frames.append(raw[i + 2:i + 2 + n])
            i += 2 + n
        return frames
    return encode


def sine(seconds=0.5, hz=440, amp=12000):
    return [int(amp * math.sin(2 * math.pi * hz * i / voice.SAMPLE_RATE))
            for i in range(int(seconds * voice.SAMPLE_RATE))]


def snr_db(ref, got):
    noise = sum((a - b) ** 2 for a, b in zip(ref, got)) or 1
    return 10 * math.log10(sum(a * a for a in ref) / noise)


def test_knob_encoder_and_bridge_decoder_agree(encoder):
    pcm = sine()
    take = voice.Take()
    for f in encoder(pcm):
        take.feed(f)
    got = take.samples()[:len(pcm)]
    assert len(got) == len(pcm)
    assert snr_db(pcm, got) > 20


def test_lost_and_reordered_frames(encoder):
    pcm = sine(seconds=0.2)
    frames = encoder(pcm)
    take = voice.Take()
    for f in reversed(frames):           # any order
        if f[:2] != struct.pack("<H", 3):  # frame 3 lost
            take.feed(f)
    got = take.samples()
    assert got[3 * FRAME:4 * FRAME] == [0] * FRAME
    # the frame after the gap decodes on its own (its header carries the state)
    assert snr_db(pcm[4 * FRAME:5 * FRAME], got[4 * FRAME:5 * FRAME]) > 15


def test_whisper_markers_for_silence_count_as_nothing():
    assert voice.clean(" [BLANK_AUDIO]\n") == ""
    assert voice.clean("(Musik)") == ""
    assert voice.clean("  Lass die\n Tests laufen. ") == "Lass die Tests laufen."


def test_transcribe_calls_whisper_cli(tmp_path):
    model = tmp_path / "m.bin"
    model.write_bytes(b"x")
    seen = {}

    def run(args, **kw):
        seen["args"] = args
        assert Path(args[args.index("-f") + 1]).read_bytes()[:4] == b"RIFF"
        return subprocess.CompletedProcess(args, 0, stdout=" Hallo Knob.\n", stderr="")

    text = voice.transcribe(sine(1.0), {"whisper_cli": "/bin/whisper", "whisper_model": str(model)}, run=run)
    assert text == "Hallo Knob."
    assert seen["args"][:3] == ["/bin/whisper", "-m", str(model)] and "-l" in seen["args"]
    # too short, no model: nothing, without calling Whisper
    assert voice.transcribe(sine(0.1), {"whisper_cli": "/bin/whisper", "whisper_model": str(model)}, run=run) == ""
    assert voice.transcribe(sine(1.0), {"whisper_cli": "/bin/whisper", "whisper_model": str(tmp_path / "no")}, run=run) == ""


@pytest.mark.skipif(not (voice.whisper_cli() and voice.DEFAULT_MODEL.exists() and shutil.which("say")),
                    reason="needs whisper-cli, the model and macOS say")
def test_spoken_sentence_through_the_knob_codec_and_whisper(encoder, tmp_path):
    wav = tmp_path / "say.wav"
    subprocess.run(["say", "-v", "Anna", "-o", str(wav), "--data-format=LEI16@16000",
                    "Lass die Tests noch einmal laufen"], check=True)
    with wave.open(str(wav)) as w:
        pcm = list(struct.unpack(f"<{w.getnframes()}h", w.readframes(w.getnframes())))
    take = voice.Take()
    for f in encoder(pcm):
        take.feed(f)
    text = voice.transcribe(take.samples()).lower()
    assert "tests" in text and "laufen" in text


class FakeLink:
    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)
        return True


def test_hub_from_ptt_to_prompt(encoder, monkeypatch):
    prompts = []

    class Adapter:
        def prompt(self, s, text): prompts.append((s.sid, text)); return True

    monkeypatch.setattr(voice, "transcribe", lambda samples, cfg=None: "Hallo Knob" if samples else "")

    async def go():
        hub = Hub(adapter=Adapter(), log=lambda m: None, socket_path=Path(tempfile.mkdtemp(dir="/tmp")) / "h.sock")
        link = FakeLink()
        await hub.attach(link)
        await hub.set_sessions([protocol.Session("s1", "AI_Assistant")])
        hub.on_command(protocol.Command("ptt", {"on": True}))
        for f in encoder(sine(1.0)):
            hub.on_audio(f)
        hub.on_command(protocol.Command("ptt", {"on": False}))
        for _ in range(100):
            stt = [m for m in link.sent if m.get("t") == "stt"]
            if stt:
                break
            await asyncio.sleep(0.02)
        assert stt and stt[0]["tx"] == "Hallo Knob" and stt[0]["fin"] == 1
        hub.on_command(protocol.Command("stt_send", {"id": stt[0]["id"]}))
        assert prompts == [("s1", "Hallo Knob")]
        hub.on_command(protocol.Command("stt_send", {"id": stt[0]["id"]}))   # only once
        assert len(prompts) == 1
    asyncio.run(go())


def test_hub_puts_a_transcript_in_the_prompt_box_to_edit(monkeypatch):
    fills, prompts = [], []

    class Adapter:
        def prompt(self, s, text): prompts.append(text); return True
        def fill(self, s, text): fills.append((s.sid, text)); return True

    long_text = "Wort " * 400
    monkeypatch.setattr(voice, "transcribe", lambda samples, cfg=None: long_text.strip())

    async def go():
        hub = Hub(adapter=Adapter(), log=lambda m: None, socket_path=Path(tempfile.mkdtemp(dir="/tmp")) / "h.sock")
        link = FakeLink()
        await hub.attach(link)
        await hub.set_sessions([protocol.Session("s1", "AI_Assistant")])
        hub.on_command(protocol.Command("ptt", {"on": True}))
        hub.on_audio(struct.pack("<HhB", 0, 0, 0) + bytes(128))
        hub.on_command(protocol.Command("ptt", {"on": False}))
        for _ in range(100):
            stt = [m for m in link.sent if m.get("t") == "stt"]
            if stt:
                break
            await asyncio.sleep(0.02)
        # the knob gets enough to read through (not cut at 300 like prompts)
        assert len(stt[0]["tx"]) > 1000
        cmd = protocol.parse_command({"t": "stt_edit", "id": stt[0]["id"]})
        hub.on_command(cmd)
        assert fills == [("s1", long_text.strip())] and prompts == []
        hub.on_command(protocol.Command("stt_send", {"id": stt[0]["id"]}))   # gone already
        assert prompts == []
    asyncio.run(go())


def test_mod_adapter_queues_a_fill():
    from bridge import control, mod_server
    reg = mod_server.ModRegistry()
    reg.hello({"sid": "s1", "entrypoint": "claude-desktop"})
    a = control.ModAdapter(reg, log=lambda m: None)
    assert a.fill(protocol.Session("s1", "x"), "Hallo")
    assert asyncio.run(reg.take("s1", 0)) == [{"t": "fill", "text": "Hallo"}]
