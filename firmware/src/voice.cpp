#include <Arduino.h>
#include <string.h>
#include "voice.h"
#include "adpcm.h"
#include "ble.h"
#include "hal/mic_hal.h"

#define FRAME_SAMPLES 256
#define HEADER_BYTES  5

static bool          active = false;
static uint32_t      start_ms = 0;
static uint16_t      seq = 0;
static adpcm_state_t enc;
static int16_t       block[FRAME_SAMPLES];
static size_t        filled = 0;

static void send_block(size_t n) {
    if (n & 1) block[n++] = 0;
    uint8_t frame[HEADER_BYTES + FRAME_SAMPLES / 2];
    frame[0] = seq & 0xFF;
    frame[1] = seq >> 8;
    frame[2] = (uint16_t)enc.predictor & 0xFF;
    frame[3] = (uint16_t)enc.predictor >> 8;
    frame[4] = enc.index;
    adpcm_encode(&enc, block, n, frame + HEADER_BYTES);
    ble_send_audio(frame, HEADER_BYTES + n / 2);
    seq++;
}

bool voice_start(void) {
    if (active) return true;
    if (!mic_hal_start()) return false;
    adpcm_reset(&enc);
    seq = 0;
    filled = 0;
    start_ms = millis();
    active = true;
    return true;
}

void voice_stop(void) {
    if (!active) return;
    voice_tick();
    if (filled) send_block(filled);
    filled = 0;
    mic_hal_stop();
    active = false;
}

bool voice_tick(void) {
    if (!active) return false;
    for (;;) {
        const size_t got = mic_hal_read(block + filled, FRAME_SAMPLES - filled);
        filled += got;
        if (filled < FRAME_SAMPLES) break;
        send_block(FRAME_SAMPLES);
        filled = 0;
    }
    if (millis() - start_ms >= VOICE_MAX_MS) {
        voice_stop();
        return false;
    }
    return true;
}

bool voice_active(void) { return active; }
