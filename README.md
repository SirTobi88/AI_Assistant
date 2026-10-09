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

## Bridge starten und Hooks einrichten

```bash
.venv/bin/python -m bridge.claude_usage_daemon        # BLE, Limits, Hub-Socket
.venv/bin/python bridge/knob_activity.py --install    # Status-Hooks (Sessions, Buddy)
.venv/bin/python bridge/knob_hook.py --install        # Freigaben/Fragen/Pläne am Knob
```

Die Hooks tragen sich in `~/.claude/settings.json` ein (fremde Einträge bleiben unberührt,
`--uninstall` entfernt nur die eigenen).

**Neben Clawdmeter / AI_Limitmeter:** Alles ist getrennt: Gerätename „AI Knob XXXX“, eigene
Service-UUID, Konfiguration und Hook-Daten unter `~/.config/ai-knob/`, Hook-Dateien
`knob_activity.py`/`knob_hook.py`. Beide Daemons und beide Hook-Sätze können parallel laufen.

Läuft die Bridge nicht oder ist der Knob nicht
verbunden, zeigt Claude Code sofort den normalen Dialog.

## Virtueller Knob (ohne Hardware)

```bash
tools/virtual_knob.sh              # Bridge im virtuellen Modus + Simulator-Fenster
tools/virtual_knob.sh --no-usage   # ohne Usage-Abfrage
```

Der Simulator verbindet sich über `~/.config/ai-knob/knob.sock` statt Bluetooth mit der Bridge.
Es sind dieselben Pakete wie über GATT, nur über einen anderen Transport. Mausrad oder Pfeil hoch/runter = Ring,
Klick = Tap, gedrückt halten = Halten. Mit installiertem `knob_hook.py` landen Freigaben und
Fragen echter Claude-Code-Sessions im Simulator. End-to-End-Test:
`.venv/bin/python -m pytest bridge/tests/test_e2e_virtual.py`.

## Sessions steuern (Modell, Reasoning, Modus, Fast, Stopp)

Weder die CLI noch die Desktop-App bieten dafür eine Schnittstelle. Die Bridge tippt deshalb
in die Session, abgeschaltet bis `control = keys` in `~/.config/ai-knob/config` steht:

| | CLI in Terminal.app | Desktop-App |
|---|---|---|
| Ziel | Tab mit dem TTY der Session (wird nach vorn geholt) | App nach vorn, **die gerade sichtbare Session** |
| Modell / Reasoning | Zeile leeren (Ctrl+E, Ctrl+U), `/model …` / `/effort …` + Enter | Menü Cmd+Shift+I / Cmd+Shift+E + Ziffer |
| Modus | Shift+Tab so oft wie nötig | Menü Cmd+Shift+M + Ziffer |
| Fast | `/fast` (nur wenn der Zustand abweicht) | nicht möglich |
| Stopp | Esc | Esc |
| Session wechseln | Tab nach vorn holen | App nach vorn |

- Nötig ist das Bedienungshilfen-Recht (Systemeinstellungen → Datenschutz & Sicherheit →
  Bedienungshilfen) für die App, die die Bridge startet. Fehlt es, steht das im Log.
- In der CLI geht ein halb getippter Entwurf beim Leeren nicht verloren: Ctrl+Y holt ihn zurück.
- Die Menü-Reihenfolge der Desktop-App ist aus der Doku übernommen und noch nicht am echten
  Menü geprüft; abweichende Reihenfolge in der Konfiguration eintragen (siehe `config.example`).
- Werte vom Knob werden gegen feste Listen geprüft, bevor etwas getippt wird.
- Klappt ein Umschalten nicht, nimmt der Knob die Anzeige zurück.

## Bedienung

| Geste | Wirkung |
|---|---|
| Ring drehen | Menü öffnen / Auswahl bewegen |
| Tap | auswählen |
| Doppeltap | zurück / schließen |
| Halten | Push-to-talk (Leertaste an Claude Code) bzw. Bestätigen bei roten Optionen |

Rote Optionen (z. B. „Erlauben“ bei `git push`, `rm -rf`, `sudo`) gelten nur, wenn man hält,
bis sich der innere Ring gefüllt hat. „Am Rechner“ gibt die Frage an den normalen Dialog zurück.
