"""Push-to-talk on the bridge: AUDIO frames from the knob -> WAV -> Whisper.

Frame (firmware/src/voice.cpp, docs/protocol.md): [seq u16 LE][predictor i16 LE]
[index u8] + IMA-ADPCM nibbles (low first), 256 samples at 16 kHz mono. Each
frame carries its decoder state, so frames decode on their own; a missing one
becomes 16 ms of silence.

Transcription: whisper.cpp (`whisper-cli`, Homebrew) with a local model, by
default ~/.config/ai-knob/models/ggml-large-v3-turbo-q5_0.bin. Config keys in
~/.config/ai-knob/config: whisper_cli, whisper_model, whisper_lang (de).
"""
from __future__ import annotations

import io
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path

SAMPLE_RATE = 16000
FRAME_SAMPLES = 256
HEADER = struct.Struct("<HhB")
MAX_SECONDS = 35                      # the knob stops at 30 s

STEPS = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
    32767]
INDEX_ADJ = [-1, -1, -1, -1, 2, 4, 6, 8]

DEFAULT_MODEL = Path.home() / ".config" / "ai-knob" / "models" / "ggml-large-v3-turbo-q5_0.bin"


def adpcm_decode(data: bytes, predictor: int = 0, index: int = 0) -> list[int]:
    out = []
    for byte in data:
        for code in (byte & 0x0F, byte >> 4):
            step = STEPS[index]
            delta = step >> 3
            if code & 4:
                delta += step
            if code & 2:
                delta += step >> 1
            if code & 1:
                delta += step >> 2
            predictor += -delta if code & 8 else delta
            predictor = max(-32768, min(32767, predictor))
            index = max(0, min(88, index + INDEX_ADJ[code & 7]))
            out.append(predictor)
    return out


class Take:
    """One push-to-talk recording, assembled from frames in seq order."""

    def __init__(self) -> None:
        self.frames: dict[int, list[int]] = {}

    def feed(self, frame: bytes) -> None:
        if len(frame) < HEADER.size:
            return
        seq, pred, idx = HEADER.unpack_from(frame)
        if seq >= MAX_SECONDS * SAMPLE_RATE // FRAME_SAMPLES or idx > 88:
            return
        self.frames[seq] = adpcm_decode(frame[HEADER.size:], pred, idx)

    def samples(self) -> list[int]:
        if not self.frames:
            return []
        out: list[int] = []
        for seq in range(max(self.frames) + 1):
            out += self.frames.get(seq, [0] * FRAME_SAMPLES)
        return out

    def seconds(self) -> float:
        return len(self.samples()) / SAMPLE_RATE


def wav_bytes(samples: list[int]) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


def whisper_cli(cfg: dict | None = None) -> str | None:
    cfg = cfg or {}
    return cfg.get("whisper_cli") or shutil.which("whisper-cli") or (
        "/opt/homebrew/bin/whisper-cli" if Path("/opt/homebrew/bin/whisper-cli").exists() else None)


def transcribe(samples: list[int], cfg: dict | None = None, run=subprocess.run) -> str:
    """Text Whisper hears in ``samples``; '' for nothing (or no Whisper)."""
    cfg = cfg or {}
    cli = whisper_cli(cfg)
    model = Path(cfg.get("whisper_model") or DEFAULT_MODEL).expanduser()
    if not cli or not model.exists() or len(samples) < SAMPLE_RATE // 4:
        return ""
    with tempfile.TemporaryDirectory(prefix="knob-voice") as d:
        path = Path(d) / "take.wav"
        path.write_bytes(wav_bytes(samples))
        r = run([cli, "-m", str(model), "-f", str(path), "-l", cfg.get("whisper_lang", "de"),
                 "-nt", "-np"], capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "whisper-cli failed").strip()[-300:])
    return clean(r.stdout)


def clean(text: str) -> str:
    """Whisper's output as one line; its markers for silence count as nothing."""
    text = " ".join(text.split())
    if text.strip("[]() .").lower() in ("", "blank_audio", "silence", "music", "musik", "stille"):
        return ""
    return text
