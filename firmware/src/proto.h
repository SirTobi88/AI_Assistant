#pragma once
#include <stdint.h>
#include <stddef.h>

// Protocol v1 (docs/protocol.md) — the typed host messages ("t" set) and the
// commands the knob sends back. Messages without "t" are the legacy usage
// payload and stay with main.cpp's parse_json().

#define PROTO_MAX_SESS    12
#define PROTO_MAX_OPTS    8
#define PROTO_MAX_PROMPTS 4
#define PROTO_ID_LEN      16

enum sess_state_t {
    SS_STARTING, SS_IDLE, SS_THINKING, SS_RESPONDING, SS_TOOL, SS_COMPACTING,
    SS_NEEDS_PERMISSION, SS_ASKING, SS_NEEDS_INPUT, SS_ERROR, SS_DONE,
    SS_COUNT
};

struct SessRow {
    char    sid[PROTO_ID_LEN];
    char    label[28];
    uint8_t state;           // sess_state_t
    int8_t  ctx_pct;         // -1 = unknown
    char    model[16];
    char    effort[12];
    char    mode[16];
    bool    fast;
    bool    unread;
};

struct OptionList {
    char    v[PROTO_MAX_OPTS][16];
    uint8_t n;
};

enum prompt_kind_t { PROMPT_PERM, PROMPT_ASK, PROMPT_PLAN };

struct PromptMsg {
    char     id[PROTO_ID_LEN];
    char     sid[PROTO_ID_LEN];
    uint8_t  kind;           // prompt_kind_t
    char     title[44];
    char     text[320];
    char     opts[PROTO_MAX_OPTS][44];
    uint8_t  n_opts;
    bool     multi;
    uint8_t  confirm_mask;   // bit i = option i needs hold-to-confirm
    uint16_t timeout_s;
    uint32_t recv_ms;
};

struct MonitorData {
    bool  valid;
    char  sid[PROTO_ID_LEN];
    float burn;              // %/h in the 5-h window
    int   eta_min;           // minutes until the limit, -1 = lasts
    int   tok_k;             // context tokens, k
    int   agents, tasks_done, tasks_total;
    int   add, del;          // diff lines
    int   ci;                // -1 unknown, 0 running, 1 green, 2 red
    int   pr;                // 0 = none
};

struct SttData {
    bool valid;
    char id[PROTO_ID_LEN];
    char text[320];
    bool final;
};

struct AppState {
    SessRow     sess[PROTO_MAX_SESS];
    uint8_t     n_sess;
    char        active[PROTO_ID_LEN];
    OptionList  models, efforts, modes;
    PromptMsg   prompts[PROTO_MAX_PROMPTS];   // oldest first
    uint8_t     n_prompts;
    MonitorData mon;
    SttData     stt;
    uint32_t    changed;                      // bit per message type, cleared by the reader
};

#define PROTO_CHG_SESSIONS (1u << 0)
#define PROTO_CHG_OPTIONS  (1u << 1)
#define PROTO_CHG_PROMPTS  (1u << 2)
#define PROTO_CHG_MONITOR  (1u << 3)
#define PROTO_CHG_STT      (1u << 4)

enum proto_result_t {
    PROTO_LEGACY,            // no "t": the usage payload, not ours
    PROTO_OK,
    PROTO_BAD,               // malformed or unknown type
};

AppState* proto_state(void);
proto_result_t proto_handle(const char* json, uint32_t now_ms);

const SessRow* proto_active_session(void);
const SessRow* proto_find_session(const char* sid);
void proto_drop_prompt(const char* id);       // answered here, or gone

// ---- Commands (built here, sent via ble_send_cmd) ----
void proto_send_answer(const char* id, const uint8_t* choices, int n);
void proto_send_switch(const char* sid);
void proto_send_set(const char* sid, const char* key, const char* value);
void proto_send_set_fast(const char* sid, bool on);
void proto_send_interrupt(const char* sid);
void proto_send_ptt(bool on);
void proto_send_stt(const char* id, bool send);
void proto_send_hello(const char* fw);

// Pure builder used by the senders above and the host unit test.
size_t proto_build_answer(char* out, size_t cap, const char* id, const uint8_t* choices, int n);

const char* proto_state_name(uint8_t state);  // German UI label
bool proto_state_needs_you(uint8_t state);
