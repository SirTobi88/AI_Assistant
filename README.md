# AI Assistant Knob

Ein Waveshare ESP32-S3 Knob-1.8 als physische Steuerzentrale für Claude Code (CLI und
Desktop-App): Limits anzeigen, Rückfragen und Freigaben per Drehring beantworten, Sessions
wechseln, Modell und Reasoning einstellen, Spracheingabe per Push-to-talk.

Basis ist [AI_Limitmeter](https://github.com/SirTobi88/AI_Limitmeter) (Fork von
[Clawdmeter](https://github.com/HermannBjorgvin/Clawdmeter) von Hermann Björgvin):
Knob-1.8-Port, BLE-Service, Simulator und Daemon stammen von dort.

## Aufbau

- `firmware/` — PlatformIO, Envs `waveshare_knob_18` (Hardware) und `sim` (SDL2-Simulator)
- `bridge/` — Python-Bridge auf dem Rechner (BLE, Limits, Hooks, Protokoll)
- `docs/protocol.md` — Protokoll v1 zwischen Knob und Bridge

## Bauen

```bash
pio run -d firmware -e waveshare_knob_18
pio run -d firmware -e sim && (cd firmware && .pio/build/sim/program)
python3 -m venv .venv && .venv/bin/pip install pytest bleak httpx
.venv/bin/python -m pytest bridge/tests -q
```

Simulator: Pfeil hoch/runter oder Mausrad = Drehring, Mausklick = Tap.
