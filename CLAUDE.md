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
- Simulator-Eingabe per Skript: `SIM_INPUT="1000:d,1600:s,2000:t,3000:h1800"` (u/d = Ring,
  t = Tap, h<ms> = Halten, s = Screenshot nach `SIM_SHOT_PREFIX-N.bmp`); Ring-Menü-Szenario:
  `SIM_SCENARIO=sim/app_scenario.jsonl`. Ausgabe nur sichtbar mit `script -q /dev/null …`
  (stdout ist sonst gepuffert und geht bei Absturz verloren).
- LVGL nutzt `malloc` (`LV_USE_STDLIB_MALLOC=1`): der feste 64-KB-Pool reichte für Ring-Menü +
  Usage-Screens nicht, das äußerte sich als Segfault in `lv_draw_sw_arc`.
- Firmware-Module: `rxq` (Empfang + Zusammensetzen gestückelter Nachrichten, Host-Test in
  `firmware/test/test_rxq`), `proto` (typisierte Nachrichten → `AppState`, Befehle),
  `ringmenu` (LVGL-Ring), `app` (Ansichten, Gesten, Prompt-Popup).
- Sim-Build: nach Änderungen an Headern/Schriften stürzte ein inkrementell gebautes `program`
  mehrfach ab (Segfault ohne Ausgabe) — dann `rm -rf firmware/.pio/build/sim` und neu bauen.
- Schriften: `tools/gen_fonts.py` erzeugt Styrene 12–28 mit Latin-1 + „ “ – — … (Umlaute);
  Zeilenhöhe/Baseline bleiben wie in den alten ASCII-Dateien. Font-Quelle: AI_Limitmeter/assets.
- Bridge: `claude_usage_daemon.py` (aus Limitmeter) startet `Hub` (Unix-Socket
  `~/.config/ai-knob/bridge.sock`) und pro BLE-Verbindung einen `KnobLink` (TX-Notify abonniert,
  gestückelte Writes mit Response, gemeinsamer Lock mit den Usage-Writes). `hub_loop` schickt
  jede Sekunde Sessions (`sessions.py`: Hook-Dateien + Transcript-Ende) und alle 10 s Monitor-Werte.
- `knob_hook.py`: PermissionRequest (`--perm`) und PreToolUse für AskUserQuestion/ExitPlanMode
  (`--pre`), Antwort per `decision` bzw. `permissionDecision` + `updatedInput` (laut Hook-Doku
  reicht bei diesen Tools `allow` allein nicht). Kein Ergebnis → keine Ausgabe → normaler Dialog.
- Session-IDs gehen auf 12 Zeichen gekürzt an den Knob (`protocol.short_sid`), der Hub löst sie
  per Präfix auf. Adapter für switch/set/interrupt ist noch `LoggingAdapter` (Phase 3/4).
