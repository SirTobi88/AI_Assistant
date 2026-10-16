#include "proto.h"
#include "ble.h"
#include <ArduinoJson.h>
#include <stdio.h>
#include <string.h>

static AppState st = {};

AppState* proto_state(void) { return &st; }

static void copy(char* dst, size_t cap, const char* src) {
    if (!cap) return;
    if (!src) src = "";
    strncpy(dst, src, cap - 1);
    dst[cap - 1] = '\0';
}

static void read_list(JsonVariantConst arr, OptionList* out) {
    out->n = 0;
    for (JsonVariantConst v : arr.as<JsonArrayConst>()) {
        if (out->n >= PROTO_MAX_OPTS) break;
        const char* s = v.as<const char*>();
        if (!s || !*s) continue;
        copy(out->v[out->n++], sizeof(out->v[0]), s);
    }
}

static bool handle_sessions(JsonDocument& doc) {
    JsonArrayConst rows = doc["ss"].as<JsonArrayConst>();
    if (rows.isNull()) return false;
    st.n_sess = 0;
    for (JsonArrayConst r : rows) {
        if (st.n_sess >= PROTO_MAX_SESS) break;
        const char* sid = r[0] | "";
        if (!*sid) continue;
        SessRow* s = &st.sess[st.n_sess++];
        copy(s->sid, sizeof(s->sid), sid);
        copy(s->label, sizeof(s->label), r[1] | "");
        int state = r[2] | (int)SS_IDLE;
        s->state = (state >= 0 && state < SS_COUNT) ? (uint8_t)state : (uint8_t)SS_IDLE;
        int ctx = r[3] | -1;
        s->ctx_pct = (int8_t)(ctx < -1 ? -1 : (ctx > 100 ? 100 : ctx));
        copy(s->model, sizeof(s->model), r[4] | "");
        copy(s->effort, sizeof(s->effort), r[5] | "");
        copy(s->mode, sizeof(s->mode), r[6] | "");
        s->fast = (r[7] | 0) != 0;
        s->unread = (r[8] | 0) != 0;
    }
    copy(st.active, sizeof(st.active), doc["act"] | "");
    st.changed |= PROTO_CHG_SESSIONS;
    return true;
}

static bool handle_options(JsonDocument& doc) {
    if (doc["model"].is<JsonArrayConst>())  read_list(doc["model"], &st.models);
    if (doc["effort"].is<JsonArrayConst>()) read_list(doc["effort"], &st.efforts);
    if (doc["mode"].is<JsonArrayConst>())   read_list(doc["mode"], &st.modes);
    st.changed |= PROTO_CHG_OPTIONS;
    return true;
}

static bool handle_prompt(JsonDocument& doc, uint32_t now_ms) {
    const char* id = doc["id"] | "";
    JsonArrayConst opts = doc["o"].as<JsonArrayConst>();
    if (!*id || opts.isNull() || opts.size() == 0) return false;

    // Same id again = an update (e.g. reworded); otherwise append.
    PromptMsg* p = nullptr;
    for (int i = 0; i < st.n_prompts; i++)
        if (strcmp(st.prompts[i].id, id) == 0) p = &st.prompts[i];
    if (!p) {
        if (st.n_prompts >= PROTO_MAX_PROMPTS) return false;   // bridge sends at most a few
        p = &st.prompts[st.n_prompts++];
        p->recv_ms = now_ms;
    }
    copy(p->id, sizeof(p->id), id);
    copy(p->sid, sizeof(p->sid), doc["sid"] | "");
    const char* k = doc["k"] | "ask";
    p->kind = strcmp(k, "perm") == 0 ? PROMPT_PERM : strcmp(k, "plan") == 0 ? PROMPT_PLAN : PROMPT_ASK;
    copy(p->title, sizeof(p->title), doc["ti"] | "");
    copy(p->text, sizeof(p->text), doc["tx"] | "");
    p->n_opts = 0;
    for (JsonVariantConst o : opts) {
        if (p->n_opts >= PROTO_MAX_OPTS) break;
        copy(p->opts[p->n_opts++], sizeof(p->opts[0]), o.as<const char*>());
    }
    p->multi = (doc["m"] | 0) != 0;
    p->confirm_mask = (uint8_t)(doc["d"] | 0);
    p->timeout_s = doc["to"] | 120;
    st.changed |= PROTO_CHG_PROMPTS;
    return true;
}

void proto_drop_prompt(const char* id) {
    for (int i = 0; i < st.n_prompts; i++) {
        if (strcmp(st.prompts[i].id, id) != 0) continue;
        for (int j = i; j + 1 < st.n_prompts; j++) st.prompts[j] = st.prompts[j + 1];
        st.n_prompts--;
        st.changed |= PROTO_CHG_PROMPTS;
        return;
    }
}

static bool handle_monitor(JsonDocument& doc) {
    MonitorData* m = &st.mon;
    copy(m->sid, sizeof(m->sid), doc["sid"] | "");
    m->burn = doc["burn"] | 0.0f;
    m->eta_min = doc["eta"] | -1;
    m->tok_k = doc["tok"] | -1;
    m->agents = doc["ag"] | 0;
    m->tasks_done = doc["td"] | 0;
    m->tasks_total = doc["tt"] | 0;
    m->add = doc["add"] | 0;
    m->del = doc["del"] | 0;
    m->ci = doc["ci"] | -1;
    m->pr = doc["pr"] | 0;
    m->valid = true;
    st.changed |= PROTO_CHG_MONITOR;
    return true;
}

static bool handle_stt(JsonDocument& doc) {
    const char* id = doc["id"] | "";
    if (!*id) return false;
    copy(st.stt.id, sizeof(st.stt.id), id);
    copy(st.stt.text, sizeof(st.stt.text), doc["tx"] | "");
    st.stt.final = (doc["fin"] | 0) != 0;
    st.stt.valid = true;
    st.changed |= PROTO_CHG_STT;
    return true;
}

proto_result_t proto_handle(const char* json, uint32_t now_ms) {
    if (!json) return PROTO_BAD;
    // Cheap pre-check: the legacy payload never carries "t", so skip the
    // second parse for it entirely.
    if (!strstr(json, "\"t\":")) return PROTO_LEGACY;
    JsonDocument doc;
    if (deserializeJson(doc, json) != DeserializationError::Ok) return PROTO_BAD;
    const char* t = doc["t"] | (const char*)nullptr;
    if (!t) return PROTO_LEGACY;
    bool ok = false;
    if      (strcmp(t, "sessions") == 0)    ok = handle_sessions(doc);
    else if (strcmp(t, "options") == 0)     ok = handle_options(doc);
    else if (strcmp(t, "prompt") == 0)      ok = handle_prompt(doc, now_ms);
    else if (strcmp(t, "prompt_done") == 0) { proto_drop_prompt(doc["id"] | ""); ok = true; }
    else if (strcmp(t, "monitor") == 0)     ok = handle_monitor(doc);
    else if (strcmp(t, "stt") == 0)         ok = handle_stt(doc);
    return ok ? PROTO_OK : PROTO_BAD;
}

const SessRow* proto_find_session(const char* sid) {
    if (!sid || !*sid) return nullptr;
    for (int i = 0; i < st.n_sess; i++)
        if (strcmp(st.sess[i].sid, sid) == 0) return &st.sess[i];
    return nullptr;
}

const SessRow* proto_active_session(void) { return proto_find_session(st.active); }

// ---- Commands ----

static void send_doc(JsonDocument& doc) {
    char buf[256];
    size_t n = serializeJson(doc, buf, sizeof(buf));
    if (n == 0 || n >= sizeof(buf)) return;
    ble_send_cmd(buf);
}

size_t proto_build_answer(char* out, size_t cap, const char* id, const uint8_t* choices, int n) {
    JsonDocument doc;
    doc["t"] = "answer";
    doc["id"] = id;
    JsonArray c = doc["c"].to<JsonArray>();
    for (int i = 0; i < n; i++) c.add(choices[i]);
    return serializeJson(doc, out, cap);
}

void proto_send_answer(const char* id, const uint8_t* choices, int n) {
    char buf[128];
    size_t len = proto_build_answer(buf, sizeof(buf), id, choices, n);
    if (len && len < sizeof(buf)) ble_send_cmd(buf);
}

void proto_send_switch(const char* sid) {
    JsonDocument doc;
    doc["t"] = "switch"; doc["sid"] = sid;
    send_doc(doc);
}

void proto_send_set(const char* sid, const char* key, const char* value) {
    JsonDocument doc;
    doc["t"] = "set"; doc["sid"] = sid; doc["k"] = key; doc["v"] = value;
    send_doc(doc);
}

void proto_send_set_fast(const char* sid, bool on) {
    JsonDocument doc;
    doc["t"] = "set"; doc["sid"] = sid; doc["k"] = "fast"; doc["v"] = on ? 1 : 0;
    send_doc(doc);
}

void proto_send_menu(const char* sid, const char* key, bool open) {
    JsonDocument doc;
    doc["t"] = "menu"; doc["sid"] = sid; doc["k"] = key; doc["open"] = open ? 1 : 0;
    send_doc(doc);
}

void proto_send_interrupt(const char* sid) {
    JsonDocument doc;
    doc["t"] = "interrupt"; doc["sid"] = sid;
    send_doc(doc);
}

void proto_send_ptt(bool on) {
    JsonDocument doc;
    doc["t"] = "ptt"; doc["on"] = on ? 1 : 0;
    send_doc(doc);
}

void proto_send_stt(const char* id, bool send) {
    JsonDocument doc;
    doc["t"] = send ? "stt_send" : "stt_drop"; doc["id"] = id;
    send_doc(doc);
}

void proto_send_hello(const char* fw) {
    JsonDocument doc;
    doc["t"] = "hello"; doc["v"] = 1; doc["fw"] = fw;
    send_doc(doc);
}

// ---- Labels ----

const char* proto_state_name(uint8_t state) {
    static const char* names[SS_COUNT] = {
        "startet", "bereit", "denkt nach", "antwortet", "Tool läuft", "kompaktiert",
        "Freigabe nötig", "fragt dich", "wartet auf dich", "Fehler", "fertig",
    };
    return state < SS_COUNT ? names[state] : "?";
}

bool proto_state_needs_you(uint8_t state) {
    return state == SS_NEEDS_PERMISSION || state == SS_ASKING || state == SS_NEEDS_INPUT;
}
