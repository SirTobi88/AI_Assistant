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
- Getrennt von Clawdmeter/AI_Limitmeter (die parallel laufen): Gerätename „AI Knob“, Service-UUID
  `41494b6e-6f62-4272-6964-6765000000xx`, Konfig/Adresse/Hook-Daten unter `~/.config/ai-knob/`,
  Status-Hook heißt `knob_activity.py` (deren Installer erkennt `clawd_activity.py` — gleicher Name
  würde die Hooks gegenseitig ersetzen). Knob-Prompts: Timeout 30 s, solange wartet der Dialog.
- Virtueller Knob: `bridge/virtual_knob.py` (Unix-Socket `~/.config/ai-knob/knob.sock`, Pakete
  Kanal R/T/Q + u16-Länge) liefert ein BleakClient-ähnliches `SocketClient`; dieselbe
  `run_session()` treibt Hardware und Simulator. Sim: `SIM_BRIDGE=<socket>` ersetzt die Szenarien
  (Empfang über die echte `rxq`, Senden gestückelt wie `ble.cpp`). Daemon-Optionen:
  `--virtual [pfad]`, `--no-usage` (keine API-Abfrage, für Tests), `--hub-socket`.
  Start: `tools/virtual_knob.sh`. E2E-Test: `bridge/tests/test_e2e_virtual.py` (braucht Sim-Build).
- Session-Steuerung: `bridge/control.py` (`KeystrokeAdapter`, an mit `control = keys`). Kein
  offizielles API → AppleScript/System Events: CLI = Terminal.app-Tab per TTY (aus `ps` der
  `pid`, die `knob_activity.py` aus `CLAUDE_PID` aufzeichnet) + Slash-Commands bzw. Shift+Tab/Esc;
  Desktop (`CLAUDE_CODE_ENTRYPOINT=claude-desktop`) = Menüs Cmd+Shift+I/E/M + Ziffer, wirkt auf die
  sichtbare Session. Pläne sind Daten (`plan_set` …) und werden als Text getestet; nie in Tests
  Tastendrücke senden. `osacompile` prüft die Syntax, ohne auszuführen.
- **Mod `ai-knob`** (`mod/ai-knob`, primäre Anbindung): TypeScript-Hooks-Modul, spricht HTTP über
  `~/.config/ai-knob/mod.sock` mit `bridge/mod_server.py` (Registry, Befehls-Queues, `/v1/ask`).
  Die Frage-/Freigabe-Logik bleibt in Python (`bridge/prompts.py`, geteilt mit `knob_hook.py`).
  Prüfen: `claude plugin validate mod/ai-knob`, `claude plugin test mod/ai-knob` (Test-Kit: `$`-Aufrufe
  per `on('http.fetch'|'session.id'|…)` mit `{ value }` beantworten, `tool.call` mit `{ result }`).
  Validator: Ereignisname als Literal, Hook als Funktionsliteral, `$` nur an oben deklarierte
  Funktionen. Gemessene API-Fakten: `docs/mod-api-findings.md`. Installation: `tools/install_mod.sh`.
  Installiert liegt eine Kopie im Cache (`~/.claude/plugins/cache/ai-knob-local/`): nach Änderungen
  `version` in `plugin.json` erhöhen, `claude plugin marketplace update ai-knob-local` und
  `claude plugin update ai-knob@ai-knob-local --scope user`, dann `/reload-plugins`. Sprach-Prompts
  gehen mit `asUser: true` rein (sonst rahmt die Engine sie als „The ai-knob plugin sent a message“).
- Modus vom Knob (Desktop): nur über das Menü der App (`control = keys`): Knob öffnet die
  Modus-Auswahl → Bridge drückt Cmd+Shift+M, Wahl → Ziffer als **Tastencode** (`key code 18–21`,
  ein getipptes Zeichen wirkt im Menü nicht), zurück → Esc. Reihenfolge laut App: Auto, Manual,
  Accept edits, Plan. `bypassPermissions` setzt der Knob nie. Der Knob folgt der Session mit dem
  letzten Prompt (Mod meldet `prompted`/`prompted_at`).
- Session wechseln vom Knob (Desktop): der Mod meldet `CLAUDE_CODE_HOST_SESSION_ID` (`local_…`, die
  ID der App, nicht die CLI-Session-ID) als `host_id`; die Bridge öffnet
  `claude://claude.ai/epitaxy/<host_id>`, damit die App die Session zeigt (sonst hat
  `$.prompt.fill` dort kein Eingabefeld: „prompt box not available“).
- Session-Namen am Knob: `bridge/desktop_sessions.py` liest die Dateien der App
  (`~/Library/Application Support/Claude/claude-code-sessions/*/*/local_*.json`: `cliSessionId`,
  `sessionId`, `title`) → Sidebar-Titel statt Ordnername, und `host_id` auch ohne neues Plugin.
- Modell und Reasoning vom Knob (Desktop, `control = keys`): Menü der App Cmd+Shift+I bzw. Cmd+Shift+E + Ziffer (Opus, Sonnet,
  Fable, Haiku), damit auch die Auswahl unter dem Eingabefeld wechselt; das Menü geht wie beim Modus schon beim Öffnen der Auswahl am Knob auf; `/model` aus dem Mod ändert
  nur die Engine. CLI und Tastenkürzel aus: weiter `/model` im Mod. Tasten an die Desktop-App gehen
  immer über den globalen Tastenstrom (nicht `tell process`).
- Spracheingabe: Halten am Knob (im Menü und auf der Übersicht, max. 2 min) nimmt über das PDM-Mikro auf (GPIO 45/46, I2S0,
  `boards/waveshare_knob_18/mic.cpp`), `voice.cpp` packt IMA-ADPCM-Rahmen (`adpcm.c`) aufs
  AUDIO-Merkmal `…0005`. Bridge: `bridge/voice.py` (Decoder, `Take`, whisper.cpp `whisper-cli`
  mit `~/.config/ai-knob/models/ggml-large-v3-turbo-q5_0.bin`, Konfig `whisper_cli`,
  `whisper_model`, `whisper_lang`). Sim: `SIM_MIC_WAV=<16-kHz-WAV>` statt Mikro, Kanal `A` im
  virtuellen Knob. Test-Stimme: `say -v Anna -o x.wav --data-format=LEI16@16000 "…"`.
  Halten drückt keine HID-Leertaste mehr (tippte in der Desktop-App Leerzeichen).
- GATT-Änderungen: macOS cached die Merkmale gekoppelter Geräte. Neues Merkmal → `GATT_LAYOUT` in
  `ble.cpp` erhöhen; die Firmware schickt dann einmal „Service Changed“. Die Bridge hängt dabei
  einmal (Verbindung mit altem Cache) → Bridge neu starten, danach ist das Merkmal da.
