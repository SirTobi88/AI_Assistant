# AI Assistant Knob — Projektnotizen

- Ziel-Hardware: nur Waveshare ESP32-S3-Knob-Touch-LCD-1.8 (`waveshare_knob_18`). Board-Details,
  Pin-Map und Hardware-Erkenntnisse: siehe `firmware/src/boards/waveshare_knob_18/board.h` und
  das CLAUDE.md von AI_Limitmeter (`/Users/Tobi/Documents/AI_Limitmeter/CLAUDE.md`).
- Bedienung: Ring = Auswahl, Tap = OK, Doppeltap = zurück, Halten = Kontext / Push-to-talk.
  Keine erreichbare Taste, Haptik nicht spürbar, kein Lautsprecher (nur Line-out), Mikro vorhanden.
- Protokoll: `docs/protocol.md` ist die Quelle; `bridge/protocol.py` und `firmware/src/proto.*`
  müssen dazu passen. Nachrichten ohne `t` = alte Limit-Payload aus AI_Limitmeter.
- Simulator (`-e sim`) emuliert den runden 360×360-Knob; Ring = Pfeil hoch/runter / Mausrad.
  Headless-Screenshot: `SDL_VIDEODRIVER=dummy SIM_AUTOSHOT_MS=5000 .pio/build/sim/program`
- Tests: `.venv/bin/python -m pytest bridge/tests -q`
