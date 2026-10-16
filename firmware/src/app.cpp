#include "app.h"
#include "ringmenu.h"
#include "proto.h"
#include "voice.h"
#include "ble.h"
#include "brightness.h"
#include "idle.h"
#include "theme.h"
#include <Arduino.h>
#include <lvgl.h>
#include <stdio.h>
#include <string.h>

#define DOUBLE_TAP_MS     300
#define AUTO_CLOSE_MS     30000
#define CONFIRM_HOLD_MS   1200     // after LVGL's long-press time
#define HINT_MS           1500

#define COL_NEEDS_YOU 0xd97757
#define COL_ERROR     0xc0392b
#define COL_DONE      0x788c5d
#define COL_DANGER    0xc0392b

enum view_t { V_CLOSED, V_MAIN, V_SESSIONS, V_PICK, V_MONITOR, V_PROMPT, V_BRIGHT, V_STT };

enum action_t {
    A_NONE, A_BACK, A_CLOSE,
    A_SESSIONS, A_MODEL, A_EFFORT, A_MODE, A_FAST, A_MONITOR, A_INTERRUPT, A_BRIGHT, A_PROMPTS,
    A_SESSION, A_OPTION, A_ANSWER, A_TOGGLE, A_SUBMIT, A_LATER, A_STT_SEND, A_STT_DROP,
};

static lv_obj_t* overlay = nullptr;
static RingMenu* ring = nullptr;
// Fast mode in the main menu: off for now (not available in desktop sessions).
static const bool SHOW_FAST = false;

static view_t    view = V_CLOSED;
static view_t    prompt_return = V_CLOSED;   // where a prompt / transcript goes back to
static uint8_t   act[RING_MAX_ITEMS];
static int16_t   arg[RING_MAX_ITEMS];

static const char* pick_key = "model";       // V_PICK: which setting
static char menu_sid[PROTO_ID_LEN] = "";               // mode picker open: the app's menu is shown for it
static char      prompt_id[PROTO_ID_LEN];     // V_PROMPT: the one on screen
static char      last_auto_prompt[PROTO_ID_LEN];
static uint8_t   multi_sel = 0;               // V_PROMPT multi-select bits

static UsageData usage = {};
static bool      connected = false;
static uint32_t  last_input_ms = 0;
static uint32_t  hint_until = 0;

// Gestures
static lv_timer_t* tap_timer = nullptr;
static uint32_t    press_ms = 0;
static bool        holding = false;
static bool        hold_is_confirm = false;
static bool        hold_fired = false;
static bool        ptt_on = false;

// ---------------------------------------------------------------- helpers

static const char* fallback_models[]  = {"opus", "sonnet", "haiku"};
static const char* fallback_efforts[] = {"low", "medium", "high", "xhigh", "max"};
static const char* fallback_modes[]   = {"default", "plan", "acceptEdits", "auto"};

static const char* mode_hint(const char* m) {
    if (!strcmp(m, "default"))           return "fragt bei Tools";
    if (!strcmp(m, "plan"))              return "nur planen";
    if (!strcmp(m, "acceptEdits"))       return "Edits ohne Nachfrage";
    if (!strcmp(m, "auto"))              return "entscheidet selbst";
    if (!strcmp(m, "bypassPermissions")) return "alles erlaubt";
    return "";
}

// Mode names as the desktop app shows them; the protocol keeps Claude Code's ids.
static const char* mode_label(const char* m) {
    if (!strcmp(m, "default"))           return "Manual";
    if (!strcmp(m, "plan"))              return "Plan";
    if (!strcmp(m, "acceptEdits"))       return "Accept edits";
    if (!strcmp(m, "auto"))              return "Auto";
    if (!strcmp(m, "bypassPermissions")) return "Bypass";
    return m;
}

static int list_for(const char* key, const char** out, int cap) {
    AppState* st = proto_state();
    const OptionList* l = !strcmp(key, "model") ? &st->models : !strcmp(key, "effort") ? &st->efforts : &st->modes;
    int n = 0;
    if (l->n) {
        for (int i = 0; i < l->n && n < cap; i++) out[n++] = l->v[i];
        return n;
    }
    const char** fb = !strcmp(key, "model") ? fallback_models : !strcmp(key, "effort") ? fallback_efforts : fallback_modes;
    int fn = !strcmp(key, "model") ? 3 : !strcmp(key, "effort") ? 5 : 4;
    for (int i = 0; i < fn && n < cap; i++) out[n++] = fb[i];
    return n;
}

static char* session_field(SessRow* s, const char* key) {
    if (!strcmp(key, "model"))  return s->model;
    if (!strcmp(key, "effort")) return s->effort;
    return s->mode;
}

static uint32_t state_color(uint8_t state) {
    if (proto_state_needs_you(state)) return COL_NEEDS_YOU;
    if (state == SS_ERROR) return COL_ERROR;
    if (state == SS_DONE)  return COL_DONE;
    return 0;
}

static void fmt_minutes(char* buf, size_t n, int mins) {
    if (mins < 0)         snprintf(buf, n, "?");
    else if (mins < 60)   snprintf(buf, n, "%d min", mins);
    else if (mins < 1440) snprintf(buf, n, "%dh %02dm", mins / 60, mins % 60);
    else                  snprintf(buf, n, "%dd %dh", mins / 1440, (mins % 1440) / 60);
}

static PromptMsg* current_prompt(void) {
    AppState* st = proto_state();
    for (int i = 0; i < st->n_prompts; i++)
        if (!strcmp(st->prompts[i].id, prompt_id)) return &st->prompts[i];
    return nullptr;
}

static void item(int i, const char* label, const char* sub, uint32_t color, action_t a, int16_t a_arg = 0) {
    ring_set_item(ring, i, label, sub, color);
    act[i] = a;
    arg[i] = a_arg;
}

// Briefly replaces the focused item's second line; the next rebuild restores it.
static void show_hint(const char* text) {
    ring_set_sub(ring, ring_focus(ring), text);
    ring_refresh(ring);
    hint_until = millis() + HINT_MS;
}

// ---------------------------------------------------------------- builders

static void build(view_t v, int focus);

static void build_main(int focus) {
    AppState* st = proto_state();
    const SessRow* s = proto_active_session();
    char buf[RING_LABEL_LEN];
    int n = 0;
    const int cap = 10;
    ring_set_count(ring, cap, 0);
    if (st->n_prompts) {
        snprintf(buf, sizeof(buf), "%d offen", st->n_prompts);
        item(n++, "Fragen", buf, COL_NEEDS_YOU, A_PROMPTS);
    }
    item(n++, "Sessions", s ? s->label : "keine aktiv", 0, A_SESSIONS);
    item(n++, "Modell", s && *s->model ? s->model : "-", 0, A_MODEL);
    item(n++, "Reasoning", s && *s->effort ? s->effort : "-", 0, A_EFFORT);
    item(n++, "Modus", s && *s->mode ? mode_label(s->mode) : "-", 0, A_MODE);
    if (SHOW_FAST) item(n++, "Fast", s ? (s->fast ? "an" : "aus") : "-", 0, A_FAST);
    if (s && s->ctx_pct >= 0) snprintf(buf, sizeof(buf), "Kontext %d %%", s->ctx_pct);
    else snprintf(buf, sizeof(buf), "Limits & Status");
    item(n++, "Monitor", buf, 0, A_MONITOR);
    if (s && s->state >= SS_THINKING && s->state <= SS_COMPACTING)
        item(n++, "Stopp", "Session unterbrechen", COL_DANGER, A_INTERRUPT);
    item(n++, "Helligkeit", "", 0, A_BRIGHT);
    item(n++, "Schließen", "", 0, A_CLOSE);
    ring_set_title(ring, connected ? "Claude" : "nicht verbunden");
    ring_set_body(ring, nullptr);
    ring_trim(ring, n, focus);
}

static void build_sessions(int focus) {
    AppState* st = proto_state();
    char sub[RING_LABEL_LEN];
    int n = 0;
    ring_set_count(ring, st->n_sess + 1, 0);
    int active_idx = 0;
    for (int i = 0; i < st->n_sess && n < RING_MAX_ITEMS - 1; i++) {
        const SessRow* s = &st->sess[i];
        const bool active = !strcmp(s->sid, st->active);
        if (active) active_idx = n;
        if (s->ctx_pct >= 0) snprintf(sub, sizeof(sub), "%s%s · %d %%", active ? "aktiv · " : "", proto_state_name(s->state), s->ctx_pct);
        else                 snprintf(sub, sizeof(sub), "%s%s", active ? "aktiv · " : "", proto_state_name(s->state));
        item(n++, s->label, sub, state_color(s->state), A_SESSION, (int16_t)i);
    }
    item(n++, "Zurück", "", 0, A_BACK);
    ring_set_title(ring, st->n_sess ? "Sessions" : "keine Sessions");
    ring_set_body(ring, nullptr);
    ring_trim(ring, n, focus >= 0 ? focus : active_idx);
}

static void build_pick(int focus) {
    const char* vals[PROTO_MAX_OPTS];
    const int nv = list_for(pick_key, vals, PROTO_MAX_OPTS);
    SessRow* s = (SessRow*)proto_active_session();
    const char* cur = s ? session_field(s, pick_key) : "";
    int cur_idx = 0;
    ring_set_count(ring, nv + 1, 0);
    for (int i = 0; i < nv; i++) {
        const bool is_cur = !strcmp(vals[i], cur);
        if (is_cur) cur_idx = i;
        const char* hint = !strcmp(pick_key, "mode") ? mode_hint(vals[i]) : "";
        char sub[RING_LABEL_LEN];
        snprintf(sub, sizeof(sub), "%s%s%s", is_cur ? "aktuell" : "", is_cur && *hint ? " · " : "", hint);
        item(i, !strcmp(pick_key, "mode") ? mode_label(vals[i]) : vals[i], sub, 0, A_OPTION, (int16_t)i);
    }
    item(nv, "Zurück", "", 0, A_BACK);
    ring_set_title(ring, !strcmp(pick_key, "model") ? "Modell" : !strcmp(pick_key, "effort") ? "Reasoning" : "Modus");
    ring_set_body(ring, nullptr);
    ring_trim(ring, nv + 1, focus >= 0 ? focus : cur_idx);
}

static void build_monitor(int focus) {
    const MonitorData* m = &proto_state()->mon;
    const SessRow* s = proto_active_session();
    char buf[RING_LABEL_LEN], t[24];
    int n = 0;
    ring_set_count(ring, 7, 0);
    if (usage.valid) {
        snprintf(buf, sizeof(buf), "5h %d %% · Woche %d %%", (int)(usage.session_pct + 0.5f), (int)(usage.weekly_pct + 0.5f));
    } else snprintf(buf, sizeof(buf), "keine Daten");
    item(n++, "Limits", buf, usage.valid && usage.session_pct >= 90 ? COL_NEEDS_YOU : 0, A_NONE);
    if (m->valid) {
        if (m->eta_min < 0) snprintf(buf, sizeof(buf), "%.1f %%/h · reicht", m->burn);
        else { fmt_minutes(t, sizeof(t), m->eta_min); snprintf(buf, sizeof(buf), "%.1f %%/h · leer in %s", m->burn, t); }
    } else snprintf(buf, sizeof(buf), "-");
    item(n++, "Prognose", buf, m->valid && m->eta_min >= 0 && m->eta_min < 60 ? COL_NEEDS_YOU : 0, A_NONE);
    if (s && s->ctx_pct >= 0 && m->valid && m->tok_k >= 0) snprintf(buf, sizeof(buf), "%d %% · %dk Token", s->ctx_pct, m->tok_k);
    else if (s && s->ctx_pct >= 0) snprintf(buf, sizeof(buf), "%d %%", s->ctx_pct);
    else snprintf(buf, sizeof(buf), "-");
    item(n++, "Kontext", buf, s && s->ctx_pct >= 80 ? COL_NEEDS_YOU : 0, A_NONE);
    if (m->valid) snprintf(buf, sizeof(buf), "%d Agents · %d/%d Tasks", m->agents, m->tasks_done, m->tasks_total);
    else snprintf(buf, sizeof(buf), "-");
    item(n++, "Agents", buf, 0, A_NONE);
    if (m->valid) snprintf(buf, sizeof(buf), "+%d  -%d Zeilen", m->add, m->del);
    else snprintf(buf, sizeof(buf), "-");
    item(n++, "Diff", buf, 0, A_NONE);
    static const char* ci_names[] = {"läuft", "grün", "rot"};
    uint32_t ci_col = 0;
    if (m->valid && m->ci >= 0 && m->ci <= 2) {
        if (m->pr) snprintf(buf, sizeof(buf), "%s · PR #%d", ci_names[m->ci], m->pr);
        else       snprintf(buf, sizeof(buf), "%s", ci_names[m->ci]);
        ci_col = m->ci == 1 ? COL_DONE : m->ci == 2 ? COL_ERROR : 0;
    } else snprintf(buf, sizeof(buf), "-");
    item(n++, "CI", buf, ci_col, A_NONE);
    item(n++, "Zurück", "", 0, A_BACK);
    ring_set_title(ring, s ? s->label : "Monitor");
    ring_set_body(ring, nullptr);
    ring_trim(ring, n, focus >= 0 ? focus : 0);
}

static void build_prompt(int focus) {
    PromptMsg* p = current_prompt();
    if (!p) return;
    const int no = p->n_opts;
    const int extra = p->multi ? 2 : 1;          // [Senden] + Später
    ring_set_count(ring, no + extra, 0);
    for (int i = 0; i < no; i++) {
        const bool needs_hold = (p->confirm_mask >> i) & 1;
        const char* sub = p->multi ? (((multi_sel >> i) & 1) ? "ausgewählt" : "")
                                   : (needs_hold ? "halten zum Bestätigen" : "");
        item(i, p->opts[i], sub, needs_hold ? COL_DANGER : 0,
             p->multi ? A_TOGGLE : A_ANSWER, (int16_t)i);
    }
    int n = no;
    if (p->multi) {
        const bool needs_hold = (multi_sel & p->confirm_mask) != 0;
        item(n++, "Senden", needs_hold ? "halten zum Bestätigen" : "", needs_hold ? COL_DANGER : 0, A_SUBMIT);
    }
    item(n++, "Später", "", 0, A_LATER);
    const SessRow* s = proto_find_session(p->sid);
    char title[64];
    const char* kind = p->kind == PROMPT_PERM ? "Freigabe" : p->kind == PROMPT_PLAN ? "Plan" : "Frage";
    if (s) snprintf(title, sizeof(title), "%s · %s", kind, s->label);
    else   snprintf(title, sizeof(title), "%s", kind);
    ring_set_title(ring, title);
    static char body[sizeof(p->title) + sizeof(p->text) + 2];
    if (*p->title) snprintf(body, sizeof(body), "%s\n%s", p->title, p->text);
    else           snprintf(body, sizeof(body), "%s", p->text);
    ring_set_body(ring, body);
    ring_trim(ring, n, focus >= 0 ? focus : 0);
}

static void build_bright(int) {
    ring_set_count(ring, 1, 0);
    char buf[16];
    snprintf(buf, sizeof(buf), "%d %%", (brightness_get() * 100 + 127) / 255);
    item(0, "Helligkeit", buf, 0, A_BACK);
    ring_set_title(ring, "Ring drehen");
    ring_set_body(ring, nullptr);
    ring_refresh(ring);
    ring_set_hold(ring, brightness_get() * 1000 / 255);
}

static void build_stt(int focus) {
    const SttData* t = &proto_state()->stt;
    ring_set_count(ring, 2, 0);
    item(0, "Senden", t->final ? "an aktive Session" : "hört noch zu", 0, A_STT_SEND);
    item(1, "Verwerfen", "", 0, A_STT_DROP);
    ring_set_title(ring, "Spracheingabe");
    ring_set_body(ring, *t->text ? t->text : "...");
    ring_trim(ring, 2, focus >= 0 ? focus : 0);
}

static void build(view_t v, int focus) {
    if (v != V_BRIGHT) ring_set_hold(ring, 0);
    // Leaving the mode picker without a choice closes the app's menu again.
    if (menu_sid[0] && v != V_PICK) {
        proto_send_menu(menu_sid, "mode", false);
        menu_sid[0] = 0;
    }
    view = v;
    switch (v) {
    case V_MAIN:     build_main(focus); break;
    case V_SESSIONS: build_sessions(focus); break;
    case V_PICK:     build_pick(focus); break;
    case V_MONITOR:  build_monitor(focus); break;
    case V_PROMPT:   build_prompt(focus); break;
    case V_BRIGHT:   build_bright(focus); break;
    case V_STT:      build_stt(focus); break;
    default: break;
    }
}

// ---------------------------------------------------------------- open / close

static void open_view(view_t v, int focus = -1) {
    if (!overlay) return;
    lv_obj_clear_flag(overlay, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_foreground(overlay);
    build(v, focus);
    last_input_ms = millis();
}

static void close_app(void) {
    if (menu_sid[0]) {
        proto_send_menu(menu_sid, "mode", false);
        menu_sid[0] = 0;
    }
    view = V_CLOSED;
    hint_until = 0;
    if (overlay) lv_obj_add_flag(overlay, LV_OBJ_FLAG_HIDDEN);
}

static bool open_next_prompt(void) {
    AppState* st = proto_state();
    if (!st->n_prompts) return false;
    snprintf(prompt_id, sizeof(prompt_id), "%s", st->prompts[0].id);
    multi_sel = 0;
    open_view(V_PROMPT);
    return true;
}

static void leave_prompt(void) {
    if (open_next_prompt()) return;
    if (prompt_return == V_CLOSED) close_app();
    else open_view(prompt_return);
}

static void back(void) {
    switch (view) {
    case V_MAIN:   close_app(); break;
    case V_PROMPT: prompt_return = V_CLOSED; close_app(); break;   // = Später, straight home
    case V_STT:    proto_send_stt(proto_state()->stt.id, false);
                   proto_state()->stt.valid = false;
                   if (prompt_return == V_CLOSED) close_app(); else open_view(prompt_return);
                   break;
    default:       open_view(V_MAIN); break;
    }
}

// ---------------------------------------------------------------- actions

static void send_answer(PromptMsg* p, uint8_t mask) {
    uint8_t choices[PROTO_MAX_OPTS];
    int n = 0;
    for (int i = 0; i < p->n_opts; i++) if ((mask >> i) & 1) choices[n++] = (uint8_t)i;
    if (!n) { show_hint("nichts ausgewählt"); return; }
    char id[PROTO_ID_LEN];
    snprintf(id, sizeof(id), "%s", p->id);
    proto_send_answer(id, choices, n);
    proto_drop_prompt(id);
    proto_state()->changed &= ~PROTO_CHG_PROMPTS;   // handled right here
    leave_prompt();
}

static bool needs_hold_now(void) {
    if (view != V_PROMPT) return false;
    PromptMsg* p = current_prompt();
    if (!p) return false;
    const int f = ring_focus(ring);
    if (act[f] == A_ANSWER) return (p->confirm_mask >> arg[f]) & 1;
    if (act[f] == A_SUBMIT) return (multi_sel & p->confirm_mask) != 0;
    return false;
}

static void activate(bool confirmed) {
    const int f = ring_focus(ring);
    SessRow* s = (SessRow*)proto_active_session();
    const char* need_sess = "keine aktive Session";
    switch ((action_t)act[f]) {
    case A_CLOSE:     close_app(); break;
    case A_BACK:      back(); break;
    case A_PROMPTS:   prompt_return = V_MAIN; open_next_prompt(); break;
    case A_SESSIONS:  open_view(V_SESSIONS); break;
    case A_MODEL:     if (!s) { show_hint(need_sess); break; } pick_key = "model";  open_view(V_PICK); break;
    case A_EFFORT:    if (!s) { show_hint(need_sess); break; } pick_key = "effort"; open_view(V_PICK); break;
    case A_MODE:      if (!s) { show_hint(need_sess); break; } pick_key = "mode";   open_view(V_PICK);
                      snprintf(menu_sid, sizeof(menu_sid), "%s", s->sid);
                      proto_send_menu(menu_sid, "mode", true);
                      break;
    case A_MONITOR:   open_view(V_MONITOR); break;
    case A_BRIGHT:    open_view(V_BRIGHT); break;
    case A_FAST:
        if (!s) { show_hint(need_sess); break; }
        s->fast = !s->fast;                       // optimistic; the next sessions message confirms
        proto_send_set_fast(s->sid, s->fast);
        build(V_MAIN, f);
        break;
    case A_INTERRUPT:
        if (!s) break;
        proto_send_interrupt(s->sid);
        show_hint("unterbrochen");
        break;
    case A_SESSION: {
        AppState* st = proto_state();
        const SessRow* row = &st->sess[arg[f]];
        snprintf(st->active, sizeof(st->active), "%s", row->sid);
        proto_send_switch(row->sid);
        open_view(V_MAIN);
        break;
    }
    case A_OPTION: {
        if (!s) { show_hint(need_sess); break; }
        const char* vals[PROTO_MAX_OPTS];
        const int nv = list_for(pick_key, vals, PROTO_MAX_OPTS);
        if (arg[f] >= nv) break;
        char* field = session_field(s, pick_key);
        snprintf(field, 16, "%s", vals[arg[f]]);
        menu_sid[0] = 0;   // the choice itself closes the app's menu
        proto_send_set(s->sid, pick_key, vals[arg[f]]);
        open_view(V_MAIN);
        break;
    }
    case A_ANSWER: {
        PromptMsg* p = current_prompt();
        if (!p) break;
        if (((p->confirm_mask >> arg[f]) & 1) && !confirmed) { show_hint("zum Bestätigen halten"); break; }
        send_answer(p, (uint8_t)(1u << arg[f]));
        break;
    }
    case A_TOGGLE:
        multi_sel ^= (uint8_t)(1u << arg[f]);
        build(V_PROMPT, f);
        break;
    case A_SUBMIT: {
        PromptMsg* p = current_prompt();
        if (!p) break;
        if ((multi_sel & p->confirm_mask) && !confirmed) { show_hint("zum Bestätigen halten"); break; }
        send_answer(p, multi_sel);
        break;
    }
    case A_LATER:
        if (prompt_return == V_CLOSED) close_app(); else open_view(prompt_return);
        break;
    case A_STT_SEND:
    case A_STT_DROP: {
        SttData* t = &proto_state()->stt;
        proto_send_stt(t->id, act[f] == A_STT_SEND);
        t->valid = false;
        if (prompt_return == V_CLOSED) close_app(); else open_view(prompt_return);
        break;
    }
    case A_NONE:
    default:
        break;
    }
}

// ---------------------------------------------------------------- gestures

// Push-to-talk: the knob records and streams to the bridge, which transcribes
// with Whisper and sends the text back for a look (V_STT) before it becomes a
// prompt. No HID Space any more: in the desktop app it would type spaces.
static void ptt(bool on) {
    if (on == ptt_on) return;
    if (on && !connected) { show_hint("nicht verbunden"); return; }
    if (on && !voice_start()) { show_hint("kein Mikrofon"); return; }
    ptt_on = on;
    if (!on) voice_stop();
    proto_send_ptt(on);
    if (on) {
        ring_set_title(ring, "Aufnahme ...");
    } else if (view != V_CLOSED) {
        build(view, ring_focus(ring));
        show_hint("transkribiere ...");
    }
}

static void tap_timer_cb(lv_timer_t*) {
    tap_timer = nullptr;
    activate(false);
}

static bool is_touch_event(lv_event_code_t c) {
    return c == LV_EVENT_PRESSED || c == LV_EVENT_PRESSING || c == LV_EVENT_RELEASED ||
           c == LV_EVENT_PRESS_LOST || c == LV_EVENT_SHORT_CLICKED ||
           c == LV_EVENT_LONG_PRESSED || c == LV_EVENT_LONG_PRESSED_REPEAT;
}

static void overlay_event_cb(lv_event_t* e) {
    const uint32_t now = lv_tick_get();
    // Registered for LV_EVENT_ALL: drawing and refresh events arrive here
    // too and must not count as input, or the menu never closes by itself.
    if (is_touch_event(lv_event_get_code(e))) {
        last_input_ms = millis();
        idle_note_activity();
    }
    switch (lv_event_get_code(e)) {
    case LV_EVENT_PRESSED:
        press_ms = now;
        hold_fired = false;
        break;
    case LV_EVENT_SHORT_CLICKED:
        if (tap_timer) {
            lv_timer_delete(tap_timer);
            tap_timer = nullptr;
            back();
        } else {
            tap_timer = lv_timer_create(tap_timer_cb, DOUBLE_TAP_MS, NULL);
            lv_timer_set_repeat_count(tap_timer, 1);
        }
        break;
    case LV_EVENT_LONG_PRESSED:
        holding = true;
        hold_is_confirm = needs_hold_now();
        if (!hold_is_confirm && view != V_PROMPT) ptt(true);
        break;
    case LV_EVENT_LONG_PRESSED_REPEAT:
        if (holding && hold_is_confirm && !hold_fired) {
            const uint32_t held = now - press_ms;
            const int lp = 400;   // LVGL's default long-press time
            const int pm = held > (uint32_t)lp ? (int)((held - lp) * 1000 / CONFIRM_HOLD_MS) : 0;
            ring_set_hold(ring, pm < 1 ? 1 : pm);
            if (pm >= 1000) {
                hold_fired = true;
                ring_set_hold(ring, 0);
                activate(true);
            }
        }
        break;
    case LV_EVENT_RELEASED:
    case LV_EVENT_PRESS_LOST:
        if (holding) {
            holding = false;
            if (hold_is_confirm) ring_set_hold(ring, 0);
            hold_is_confirm = false;
            ptt(false);
        }
        break;
    default:
        break;
    }
}

// ---------------------------------------------------------------- public

void app_init(void) {
    overlay = lv_obj_create(lv_layer_top());
    lv_obj_remove_style_all(overlay);
    lv_obj_set_size(overlay, LV_PCT(100), LV_PCT(100));
    lv_obj_set_style_bg_color(overlay, THEME_BG, 0);
    lv_obj_set_style_bg_opa(overlay, LV_OPA_COVER, 0);
    lv_obj_add_flag(overlay, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_clear_flag(overlay, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_clear_flag(overlay, LV_OBJ_FLAG_GESTURE_BUBBLE);
    lv_obj_add_event_cb(overlay, overlay_event_cb, LV_EVENT_ALL, NULL);
    ring = ring_create(overlay);
    lv_obj_add_flag(overlay, LV_OBJ_FLAG_HIDDEN);
}

bool app_is_open(void) { return view != V_CLOSED; }

bool app_encoder(int steps) {
    if (!steps) return false;
    last_input_ms = millis();
    if (view == V_CLOSED) {
        open_view(V_MAIN);
        return true;
    }
    if (view == V_BRIGHT) {
        for (int n = steps > 0 ? steps : -steps; n > 0; n--) brightness_step(steps > 0 ? 1 : -1);
        build(V_BRIGHT, 0);
        return true;
    }
    if (hint_until) { hint_until = 0; build(view, ring_focus(ring)); }
    ring_step(ring, steps);
    return true;
}

void app_on_proto(void) {
    AppState* st = proto_state();
    const uint32_t chg = st->changed;
    st->changed = 0;

    if (chg & PROTO_CHG_PROMPTS) {
        if (view == V_PROMPT) {
            if (current_prompt()) build(V_PROMPT, ring_focus(ring));
            else leave_prompt();
        } else if (st->n_prompts &&
                   strcmp(st->prompts[st->n_prompts - 1].id, last_auto_prompt) != 0) {
            // A new prompt: wake up and show it, from wherever we are.
            snprintf(last_auto_prompt, sizeof(last_auto_prompt), "%s", st->prompts[st->n_prompts - 1].id);
            prompt_return = (view == V_STT) ? V_CLOSED : view;
            idle_note_activity();
            open_next_prompt();
            return;
        }
    }
    if ((chg & PROTO_CHG_STT) && st->stt.valid && view != V_PROMPT) {
        if (view != V_STT) {
            prompt_return = view;
            idle_note_activity();
            open_view(V_STT);
        } else {
            build(V_STT, ring_focus(ring));
        }
        return;
    }
    // Live refresh of whatever list is up.
    if (view == V_MAIN && (chg & (PROTO_CHG_SESSIONS | PROTO_CHG_PROMPTS))) build(V_MAIN, ring_focus(ring));
    else if (view == V_SESSIONS && (chg & PROTO_CHG_SESSIONS)) build(V_SESSIONS, ring_focus(ring));
    else if (view == V_PICK && (chg & (PROTO_CHG_OPTIONS | PROTO_CHG_SESSIONS))) build(V_PICK, ring_focus(ring));
    else if (view == V_MONITOR && (chg & (PROTO_CHG_MONITOR | PROTO_CHG_SESSIONS))) build(V_MONITOR, ring_focus(ring));
}

void app_set_usage(const UsageData* u) {
    if (!u) return;
    usage = *u;
    if (view == V_MONITOR) build(V_MONITOR, ring_focus(ring));
}

void app_set_connected(bool c) {
    if (c == connected) return;
    connected = c;
    if (c) proto_send_hello("0.1.0");
    if (view == V_MAIN) build(V_MAIN, ring_focus(ring));
}

void app_tick(void) {
    if (ptt_on && !voice_tick()) ptt(false);   // the take hit its 30 s
    if (view == V_CLOSED) return;
    const uint32_t now = millis();
    if (hint_until && (int32_t)(now - hint_until) >= 0) {
        hint_until = 0;
        build(view, ring_focus(ring));
    }
    // Prompts the bridge has surely given up on.
    AppState* st = proto_state();
    for (int i = 0; i < st->n_prompts; i++) {
        const PromptMsg* p = &st->prompts[i];
        if (p->timeout_s && now - p->recv_ms > (uint32_t)p->timeout_s * 1000u + 5000u) {
            proto_drop_prompt(p->id);
            app_on_proto();
            return;
        }
    }
    if (view != V_PROMPT && view != V_STT && !holding && now - last_input_ms > AUTO_CLOSE_MS) close_app();
}
