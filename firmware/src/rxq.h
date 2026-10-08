#pragma once
#include <stddef.h>
#include <stdint.h>

// Incoming host messages (docs/protocol.md). The BLE task feeds raw writes —
// bare JSON or 0x01-framed pieces of a larger message — and the main loop pops
// whole messages. Single producer, single consumer; no locks needed.
#define RXQ_MSG_MAX 4096
#define RXQ_SLOTS   6

void rxq_feed(const uint8_t* data, size_t len);   // producer (BLE host task)
bool rxq_has(void);                               // consumer (loop)
// Next whole message, NUL-terminated. Stays valid until the next rxq_pop().
const char* rxq_pop(void);
uint32_t rxq_dropped(void);                       // messages lost to a full queue / bad frames
void rxq_reset(void);                             // test helper / on disconnect
