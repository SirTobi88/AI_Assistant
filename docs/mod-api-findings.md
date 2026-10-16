# Mod-API: geprüfte Fakten (Claude Code 2.1.293, Desktop-Session, 2026-10-09)

Gemessen mit einem reinen Beobachtungs-Mod (`ai-knob-spike`, nichts blockiert) in einer Code-Tab-
Session der Desktop-App. Typen: `plugin-authoring/types/claude-code.d.ts` des Builds.

| Frage | Ergebnis |
|---|---|
| Lädt ein Mod in der Desktop-App? | Ja (Hot Reload der Session). `session.start`: `surface: null`, `isInteractive: false` |
| `$.session.id()/cwd()/model()/surfaces()/version()` | liefern **Promises** → immer `await` |
| `$.session.usage()` | `context {tokens, window, percent}`, `rateLimits [{kind: "five_hour"\|"seven_day", percentUsed, resetsAt}]`, `cost {usd}` → ersetzt Transcript-Schätzung und Usage-API-Poll |
| Long-Poll `$.http.fetch(…, {socketPath})` | 20-s-Antwort kam nach 20 026 ms an, Status 200 |
| `$.command.run({command, args})` | `effort status` im Idle in 51 ms, Ausgabe als `text`, erscheint im Verlauf wie ein getippter Befehl |
| Befehlsliste (`$.command.list()`) | enthält `model`, `effort`, `fast`, `compact`, `clear`, `rename` …; **kein `plan`, kein `permissions`** in der Desktop-Session |
| `$.config.list()` | `permissionMode` = „Default permission mode“ (nur für neue Sessions), `model` (Standardmodell), `thinking` … – keine Zeile für den Modus der laufenden Session |
| `classic.UserPromptSubmit` | Felder u. a. `permission_mode` (hier `auto`), `effort`, `session_id`, `transcript_path`, `prompt`, `session_title` |
| `classic.PreToolUse` im Mod | Felder `tool`, `tool_use_id` + Tool-Eingabe (nicht die Settings-Hook-Form) |
| `turn.start` | `{ text, turnId }` → für `$.turn.abort` merken |
| `$.env.get('CLAUDE_EFFORT')` | im Mod nicht gesetzt (Effort aus `classic.UserPromptSubmit.effort`) |
| `$.env.get('CLAUDE_CODE_ENTRYPOINT')` | `claude-desktop` |

## Live-Test mit dem Mod `ai-knob` (Desktop-Session, virtueller Knob, 2026-10-09)

| Prüfpunkt | Ergebnis |
|---|---|
| Anmeldung | `hello` 17 s nach dem Laden, Heartbeat läuft |
| Limits aus dem Mod | Bridge mit `--no-usage` schickt 5h/7d aus `rateLimits` (43 % / 45 %) an den Knob |
| AskUserQuestion über `classic.PreToolUse` → `{allow, updatedInput.answers}` | Antwort am Simulator („Orange“, 25 s) kam in der Session an, **kein Dialog** am Rechner |
| Reasoning vom Knob → `$.command.run({command:'effort', args:'high'})` | ausgeführt nach Ende des Turns, Ausgabe „Set effort level to high (this session only)“: `/effort` wirkt nur auf die Session |
| Freigabe (`classic.PermissionRequest`) | noch nicht live (Session im Auto-Modus) |
| Modell vom Knob → `/model sonnet`, `/model opus` | „Set model to … for this session only“: in der Desktop-Session **kein** neuer Standard |
| Fast vom Knob → `/fast on` | „Fast mode is not available in the Agent SDK“: in Desktop-Sessions nicht verfügbar, `/fast off` geht |
| Effort `max`, `medium` | ausgeführt, jeweils nur für die Session |
| Modell-Auswahl der Desktop-App | bleibt nach `/model` auf dem alten Modell stehen (nur Anzeige); `set_session_model` der App lehnt die eigene Session ab |
| Modus vom Knob → `$.mcp.call('ccd_session_mgmt', 'set_session_permission_mode', {session_id:'self'})` (2026-10-16, echte Hardware) | im Auto-Modus abgelehnt: „auto mode classifier gave no verdict … the request that produced this action did not ask for one“. Aufruf aus dem Mod hat keine auslösende Anfrage; kein Umgehen per Wiederholung. In anderen Modi ungetestet |

## Folgerungen
- Status, Kontext, Limits, Modell/Effort/Fast, Stopp und Prompts: direkt im Mod.
- **Modus der laufenden Session**: kein direkter Weg. Möglich sind `setMode` (Ziel `session`) als Teil
  einer Freigabe-Antwort (`classic.PermissionRequest` → `decision.updatedPermissions`) oder der
  Tastendruck-Rückfall (`bridge/control.py`, Desktop: Cmd+Shift+M + Ziffer).
- Validator-Regeln für Hooks-Module: Ereignisnamen als String-Literal, Hook als Funktionsliteral,
  `$` nur an Funktionen übergeben, die oben in der Datei deklariert sind.
