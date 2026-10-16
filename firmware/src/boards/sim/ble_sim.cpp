// BLE stub + scenario playback. Implements ble.h without any transport: a
// JSONL scenario file stands in for the daemon, delivered through the same
// ble_has_data()/ble_get_data() path main.cpp uses on hardware — so JSON
// parsing, usage-rate tracking, and the chime trigger all run for real.
#include "../../ble.h"
#include "sim_platform.h"
#include <Arduino.h>
#include <ArduinoJson.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include "../../rxq.h"

#define MAX_STATES 64
#define MAX_LINE   4096

struct SimState {
    char json[MAX_LINE];
    char name[32];
    uint32_t hold_ms;
};

static SimState states[MAX_STATES];
static int      n_states = 0;
static int      cur = 0;
static bool     playing = true;
static bool     connected = true;
static bool     pending = false;      // a state is queued for main's next poll
static uint32_t delivered_ms = 0;

// ---- Virtual knob: SIM_BRIDGE=<unix socket> talks to the real bridge ----
// (bridge/virtual_knob.py). Packets: channel byte + u16 LE length + bytes;
// R = host write to RX (fed into the real rxq), T = notify on TX, Q = REQ.
// Replaces scenario playback entirely while set.
static const char* bridge_path = nullptr;
static int      bridge_fd = -1;
static bool     bridge_link_on = true;      // 'd' drops / restores the link
static uint32_t bridge_retry_ms = 0;
static uint8_t  bridge_in[8192];
static size_t   bridge_in_len = 0;
#define TX_CHUNK 180

static void bridge_close(void) {
    if (bridge_fd >= 0) {
        close(bridge_fd);
        printf("[sim] bridge link closed\n");
    }
    bridge_fd = -1;
    bridge_in_len = 0;
}

static void bridge_packet(char ch, const uint8_t* data, size_t len) {
    if (bridge_fd < 0 || len > 0xFFFF) return;
    uint8_t head[3] = { (uint8_t)ch, (uint8_t)(len & 0xFF), (uint8_t)(len >> 8) };
    if (write(bridge_fd, head, 3) != 3 || write(bridge_fd, data, len) != (ssize_t)len) bridge_close();
}

static void bridge_connect(void) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return;
    struct sockaddr_un addr = {};
    addr.sun_family = AF_UNIX;
    snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", bridge_path);
    if (connect(fd, (struct sockaddr*)&addr, sizeof(addr)) != 0) { close(fd); return; }
    fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
    bridge_fd = fd;
    rxq_reset();
    printf("[sim] bridge link up: %s\n", bridge_path);
    const uint8_t req = 0x01;                // like onSubscribe on hardware: "send me data"
    bridge_packet('Q', &req, 1);
}

static void bridge_tick(void) {
    if (!bridge_link_on) { bridge_close(); return; }
    if (bridge_fd < 0) {
        if (millis() - bridge_retry_ms >= 1000) { bridge_retry_ms = millis(); bridge_connect(); }
        return;
    }
    for (;;) {
        ssize_t n = read(bridge_fd, bridge_in + bridge_in_len, sizeof(bridge_in) - bridge_in_len);
        if (n == 0) { bridge_close(); return; }
        if (n < 0) { if (errno != EAGAIN && errno != EWOULDBLOCK) bridge_close(); break; }
        bridge_in_len += (size_t)n;
        if (bridge_in_len == sizeof(bridge_in)) break;
    }
    size_t off = 0;
    while (bridge_in_len - off >= 3) {
        const size_t len = bridge_in[off + 1] | (bridge_in[off + 2] << 8);
        if (bridge_in_len - off < 3 + len) break;
        if (bridge_in[off] == 'R') rxq_feed(bridge_in + off + 3, len);
        off += 3 + len;
    }
    memmove(bridge_in, bridge_in + off, bridge_in_len - off);
    bridge_in_len -= off;
}

static const char* FALLBACK[] = {
    "{\"name\":\"fresh\",\"s\":3.0,\"sr\":295,\"w\":12.0,\"wr\":9000,\"st\":\"allowed\",\"ok\":true}",
    "{\"name\":\"mid\",\"s\":48.0,\"sr\":150,\"w\":35.0,\"wr\":7200,\"st\":\"allowed\",\"ok\":true}",
    "{\"name\":\"high\",\"s\":92.0,\"sr\":30,\"w\":71.0,\"wr\":4600,\"st\":\"allowed\",\"ok\":true}",
    "{\"name\":\"reset+chime\",\"hold_ms\":4000,\"s\":2.0,\"sr\":298,\"w\":72.0,\"wr\":4500,\"st\":\"allowed\",\"c\":true,\"ok\":true}",
};

static void add_state(const char* line) {
    if (n_states >= MAX_STATES) return;
    size_t len = strlen(line);
    while (len && (line[len - 1] == '\n' || line[len - 1] == '\r')) len--;
    if (!len || line[0] == '#') return;   // blank lines / comments
    SimState* s = &states[n_states];
    if (len >= MAX_LINE) len = MAX_LINE - 1;
    memcpy(s->json, line, len);
    s->json[len] = 0;
    s->hold_ms = 3000;
    snprintf(s->name, sizeof(s->name), "state %d", n_states + 1);
    // "name" and "hold_ms" ride along in the payload; main's parse_json
    // ignores unknown keys so the line is delivered as-is.
    JsonDocument doc;
    if (deserializeJson(doc, s->json) == DeserializationError::Ok) {
        s->hold_ms = doc["hold_ms"] | 3000;
        const char* nm = doc["name"] | (const char*)NULL;
        if (nm) snprintf(s->name, sizeof(s->name), "%s", nm);
    }
    n_states++;
}

static void load_scenario(void) {
    const char* tries[] = { getenv("SIM_SCENARIO"), "sim/scenario.jsonl",
                            "firmware/sim/scenario.jsonl", "../sim/scenario.jsonl" };
    FILE* f = NULL;
    for (const char* t : tries) {
        if (!t) continue;
        f = fopen(t, "r");
        if (f) { printf("[sim] scenario: %s\n", t); break; }
    }
    if (f) {
        char line[MAX_LINE];
        while (fgets(line, sizeof(line), f)) add_state(line);
        fclose(f);
    }
    if (!n_states) {
        printf("[sim] no scenario file found — using built-in states\n");
        for (const char* l : FALLBACK) add_state(l);
    }
}

static void refresh_title(void) {
    char t[96];
    snprintf(t, sizeof(t), "AI Assistant sim — %s[%d/%d] %s %s",
             connected ? "" : "(disconnected) ",
             cur + 1, n_states, states[cur].name,
             playing ? "\xE2\x96\xB6" : "\xE2\x8F\xB8");
    sim_display_set_title(t);
}

void ble_init(void) {
    bridge_path = getenv("SIM_BRIDGE");
    if (bridge_path && *bridge_path) {
        printf("[sim] virtual knob: connecting to the bridge at %s\n", bridge_path);
        sim_display_set_title("AI Knob sim — bridge");
        return;
    }
    bridge_path = nullptr;
    load_scenario();
    pending = true;
    refresh_title();
}

void ble_tick(void) {
    if (bridge_path) { bridge_tick(); return; }
    if (!connected || pending || !playing || n_states == 0) return;
    if (millis() - delivered_ms >= states[cur].hold_ms) {
        cur = (cur + 1) % n_states;
        pending = true;
        refresh_title();
    }
}

ble_state_t ble_get_state(void) {
    if (bridge_path) return bridge_fd >= 0 ? BLE_STATE_CONNECTED : BLE_STATE_ADVERTISING;
    return connected ? BLE_STATE_CONNECTED : BLE_STATE_DISCONNECTED;
}
const char* ble_get_device_name(void) { return "AI Knob (sim)"; }
const char* ble_get_mac_address(void) { return "00:51:4D:00:00:01"; }

void ble_clear_bonds(void) { printf("[sim] pair gesture completed — bonds cleared\n"); }
bool ble_has_bonds(void)   { return true; }
bool ble_pairing_rejected(void) { return false; }

bool ble_has_data(void) { return bridge_path ? rxq_has() : connected && pending; }
const char* ble_get_data(void) {
    if (bridge_path) return rxq_pop();
    pending = false;
    delivered_ms = millis();
    return states[cur].json;
}
static void bridge_notify_str(const char* s) { bridge_packet('T', (const uint8_t*)s, strlen(s)); }

void ble_send_ack(void) { if (bridge_path) bridge_notify_str("{\"ack\":true}"); }

// Same framing as ble.cpp's ble_send_cmd, so the bridge's Reassembler is
// exercised exactly as on hardware.
void ble_send_cmd(const char* json) {
    if (bridge_path) {
        if (bridge_fd < 0) return;
        printf("[sim] -> %s\n", json);
        static uint8_t msg_id = 0;
        const size_t len = strlen(json);
        if (len <= TX_CHUNK) { bridge_notify_str(json); return; }
        const size_t body = TX_CHUNK - 4, count = (len + body - 1) / body;
        msg_id++;
        uint8_t pkt[TX_CHUNK];
        for (size_t i = 0; i < count; i++) {
            const size_t o = i * body, n = (len - o < body) ? len - o : body;
            pkt[0] = 0x01; pkt[1] = msg_id; pkt[2] = (uint8_t)i; pkt[3] = (uint8_t)count;
            memcpy(pkt + 4, json + o, n);
            bridge_packet('T', pkt, n + 4);
        }
        return;
    }
    if (connected) printf("[sim] -> %s\n", json);
}
void ble_send_nack(void) {
    if (bridge_path) { bridge_notify_str("{\"err\":true}"); printf("[sim] NACK\n"); return; }
    printf("[sim] payload NACKed — check the scenario JSON\n");
}
bool ble_take_tx_subscribed(void) { return false; }

void ble_send_audio(const uint8_t* data, size_t len) {
    if (bridge_path) bridge_packet('A', data, len);
}

void ble_request_refresh(void) {
    const uint8_t req = 0x01;
    if (bridge_path) bridge_packet('Q', &req, 1);
}
void ble_set_battery_level(int pct) { (void)pct; }

void ble_keyboard_press(uint8_t key, uint8_t modifier) {
    printf("[sim] HID press key=0x%02X mod=0x%02X\n", key, modifier);
}
void ble_keyboard_release(void) { printf("[sim] HID release\n"); }

// ---- Playback controls (called from the sim_platform event pump) ----
void sim_playback_toggle(void) {
    if (bridge_path) return;
    playing = !playing;
    delivered_ms = millis();   // restart the hold timer on resume
    refresh_title();
}
void sim_playback_step(int dir) {
    if (bridge_path) return;
    if (!n_states) return;
    playing = false;
    cur = (cur + dir + n_states) % n_states;
    pending = true;
    refresh_title();
}
void sim_playback_jump(int idx) {
    if (bridge_path) return;
    if (idx < 0 || idx >= n_states) return;
    playing = false;
    cur = idx;
    pending = true;
    refresh_title();
}
void sim_playback_toggle_link(void) {
    if (bridge_path) { bridge_link_on = !bridge_link_on; return; }
    connected = !connected;
    refresh_title();
}
