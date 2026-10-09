# Protokoll v1 — Knob ⇄ Bridge

Transport: BLE GATT, Service `41494b6e-6f62-4272-6964-676500000001`, Gerätename `AI Knob XXXX`.
Bewusst anders als Clawdmeter/AI_Limitmeter (`4c41555a-…`, „Clawdmeter“), damit deren Daemon
den Knob nicht findet und die Bridge den Clawdmeter nicht. Die Characteristics haben dieselbe
Rolle wie dort (Endziffer 2–4).

| Characteristic | Richtung | Inhalt |
|---|---|---|
| `…0002` RX | Bridge → Knob (write) | JSON-Nachrichten, ggf. gestückelt |
| `…0003` TX | Knob → Bridge (notify) | `{"ack":true}` / `{"err":true}` und Befehle (JSON) |
| `…0004` REQ | Knob → Bridge (notify) | `0x01` = „bitte alles neu senden“ |
| `…0005` AUDIO | Knob → Bridge (notify) | Push-to-talk-Audio (Phase 6, siehe unten) |

Alle Nachrichten sind UTF-8-JSON-Objekte. Das Feld `t` nennt den Typ. **Ohne `t`** ist es die
alte Limit-Payload aus AI_Limitmeter (`s`, `sr`, `w`, `wr`, `cx`, `a`, …) — sie bleibt unverändert.

## Stückelung

Eine Nachricht, die nicht in einen Write passt (Grenze: ausgehandelte MTU − 3, die Bridge
nimmt konservativ 180 Byte), geht als Folge von Rahmen:

```
Byte 0   0x01            Rahmenkennung (JSON beginnt immer mit '{' = 0x7B)
Byte 1   msg_id          0..255, pro Nachricht hochgezählt
Byte 2   index           0..count-1
Byte 3   count           1..255
Byte 4.. Nutzdaten       Teilstück der JSON-Nachricht
```

Der Knob setzt Rahmen gleicher `msg_id` in Reihenfolge zusammen; ein Rahmen mit neuer `msg_id`
verwirft eine unvollständige alte Nachricht. Maximale Nachrichtengröße: 4096 Byte.
Knob → Bridge nutzt dieselbe Stückelung auf TX, falls eine Nachricht die MTU übersteigt.

## Bridge → Knob

### `sessions` — Liste und aktive Session
```json
{"t":"sessions","act":"a1b2","ss":[
  ["a1b2","AI_Assistant",4,62,"opus","high","default",0,1],
  ["c3d4","AI_Limitmeter",7,18,"sonnet","medium","plan",1,0]
]}
```
Zeile: `[sid, label, state, ctx%, model, effort, mode, fast, unread]`

`state`: 0 startet · 1 idle · 2 denkt · 3 antwortet · 4 Tool läuft · 5 kompaktiert ·
6 braucht Freigabe · 7 fragt dich · 8 wartet auf Eingabe · 9 Fehler · 10 fertig
(0–9 wie die Session-Zeilen in AI_Limitmeters Simulator, 10 neu).

`ctx%` = Füllstand des Kontextfensters, −1 = unbekannt. `fast`/`unread` = 0/1.
Max. 12 Sessions; die Bridge sortiert „braucht dich“ nach vorn.

### `options` — wählbare Werte
```json
{"t":"options","model":["opus","sonnet","haiku"],"effort":["low","medium","high","xhigh","max"],
 "mode":["default","plan","acceptEdits","auto"]}
```
Wird beim Verbinden und bei Änderungen gesendet. Der Knob zeigt diese Listen im Ring.

### `prompt` — Rückfrage, Freigabe oder Plan
```json
{"t":"prompt","id":"p17","sid":"a1b2","k":"perm","ti":"Bash",
 "tx":"git push origin main","o":["Erlauben","Immer erlauben","Ablehnen"],
 "m":0,"d":3,"to":120}
```
- `k`: `perm` (Tool-Freigabe) · `ask` (Rückfrage mit Optionen) · `plan` (Plan annehmen)
- `ti` Titel, `tx` Text (Bridge kürzt auf ~300 Zeichen), `o` Optionen (max. 8, je ≤ 40 Zeichen)
- `m` = 1: Mehrfachauswahl; `d` = Bitmaske der Optionen, die nur per Hold-to-confirm gelten
  (Bit 0 = erste Option; im Beispiel 3 = „Erlauben“ und „Immer erlauben“, „Ablehnen“ ist ein Tap)
- `to` = Sekunden bis die Bridge aufgibt und auf den normalen Dialog zurückfällt

Mehrere offene Prompts stehen in einer Warteschlange; der Knob zeigt den ältesten.

### `prompt_done` — erledigt (anderswo beantwortet oder Timeout)
```json
{"t":"prompt_done","id":"p17"}
```

### `monitor` — Zusatzwerte zur aktiven Session
```json
{"t":"monitor","sid":"a1b2","burn":1.8,"eta":1040,"tok":124,"ag":2,"td":3,"tt":7,
 "add":120,"del":34,"ci":1,"pr":42}
```
`burn` = %/h im 5-h-Fenster, `eta` = Minuten bis Limit (−1 = reicht), `tok` = Kontext in k Token,
`ag` laufende Subagents, `td`/`tt` Tasks erledigt/gesamt, `add`/`del` Diff-Zeilen,
`ci`: −1 unbekannt · 0 läuft · 1 grün · 2 rot, `pr` PR-Nummer (0 = keiner).

### `stt` — Transkript zur Vorschau (Phase 6)
```json
{"t":"stt","id":"v3","tx":"Lass die Tests nochmal laufen","fin":1}
```

## Knob → Bridge (TX-Notify)

| Befehl | Beispiel |
|---|---|
| Antwort auf Prompt | `{"t":"answer","id":"p17","c":[0]}` (Indizes der gewählten Optionen) |
| Session wechseln | `{"t":"switch","sid":"c3d4"}` |
| Einstellung setzen | `{"t":"set","sid":"a1b2","k":"model","v":"sonnet"}` — `k`: `model`·`effort`·`mode`·`fast` |
| Unterbrechen | `{"t":"interrupt","sid":"a1b2"}` |
| Push-to-talk | `{"t":"ptt","on":1}` / `{"t":"ptt","on":0}` |
| Transkript senden/verwerfen | `{"t":"stt_send","id":"v3"}` / `{"t":"stt_drop","id":"v3"}` |
| Hallo (nach Verbindung) | `{"t":"hello","v":1,"fw":"0.1.0"}` |

## AUDIO (Phase 6)

Notify-Rahmen: `[seq u16 LE][IMA-ADPCM 4 bit, 16 kHz mono]`. Erster Rahmen nach `ptt on`
beginnt mit `seq = 0` und trägt im Kopf zusätzlich `predictor i16, index u8`.
~64 kbit/s, passt in BLE 2M PHY mit großer MTU.
