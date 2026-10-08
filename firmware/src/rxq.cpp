#include "rxq.h"
#include <string.h>

#define FRAME_MARK 0x01

static char     slots[RXQ_SLOTS][RXQ_MSG_MAX];
static volatile uint8_t head = 0;      // next slot the producer fills
static volatile uint8_t tail = 0;      // oldest slot not yet released
static bool     holding = false;       // consumer still reads slots[tail]
static volatile uint32_t dropped = 0;

// Reassembly of a framed message (producer side only).
static char     asm_buf[RXQ_MSG_MAX];
static size_t   asm_len = 0;
static int      asm_id = -1;
static uint8_t  asm_next = 0;
static uint8_t  asm_count = 0;

static void push(const char* data, size_t len) {
    if (len == 0) return;
    if (len >= RXQ_MSG_MAX) { dropped++; return; }
    uint8_t next = (uint8_t)((head + 1) % RXQ_SLOTS);
    if (next == tail) { dropped++; return; }          // full: keep the older messages
    memcpy(slots[head], data, len);
    slots[head][len] = '\0';
    __sync_synchronize();                             // slot contents before the index
    head = next;
}

static void asm_drop(void) { asm_id = -1; asm_len = 0; asm_next = 0; asm_count = 0; }

void rxq_feed(const uint8_t* data, size_t len) {
    if (!data || len == 0) return;
    if (data[0] != FRAME_MARK) {
        if (asm_id >= 0) { dropped++; asm_drop(); }   // a bare message ends any half-built one
        push((const char*)data, len);
        return;
    }
    if (len < 4) { dropped++; return; }
    const uint8_t id = data[1], idx = data[2], count = data[3];
    if (count == 0 || idx >= count) { dropped++; asm_drop(); return; }
    if ((int)id != asm_id) {
        if (asm_id >= 0) dropped++;                    // new message replaces an unfinished one
        asm_drop();
        if (idx != 0) { dropped++; return; }           // joined mid-message
        asm_id = id;
        asm_count = count;
    }
    if (idx != asm_next || count != asm_count) { dropped++; asm_drop(); return; }
    const size_t body = len - 4;
    if (asm_len + body >= RXQ_MSG_MAX) { dropped++; asm_drop(); return; }
    memcpy(asm_buf + asm_len, data + 4, body);
    asm_len += body;
    asm_next++;
    if (asm_next == asm_count) {
        push(asm_buf, asm_len);
        asm_drop();
    }
}

bool rxq_has(void) {
    uint8_t t = holding ? (uint8_t)((tail + 1) % RXQ_SLOTS) : tail;
    return t != head;
}

const char* rxq_pop(void) {
    if (holding) {                                    // release the one handed out last time
        tail = (uint8_t)((tail + 1) % RXQ_SLOTS);
        holding = false;
    }
    if (tail == head) return nullptr;
    __sync_synchronize();
    holding = true;
    return slots[tail];
}

uint32_t rxq_dropped(void) { return dropped; }

void rxq_reset(void) {
    head = tail = 0;
    holding = false;
    dropped = 0;
    asm_drop();
}
