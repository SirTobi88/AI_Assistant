#include <Arduino.h>
#include <string.h>
#include <math.h>
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
static uint8_t       level = 0;

static float noise_db = 0.0f;           // background level, tracked per take

static void send_block(size_t n) {
    // RMS in dB, against a noise floor that follows the room: quick down,
    // slow up, so speech stands out and the dot rests while nobody talks.
    float sum = 0.0f;
    for (size_t i = 0; i < n; i++) sum += (float)block[i] * block[i];
    const float db = 10.0f * log10f(sum / (n ? n : 1) + 1.0f);
    if (noise_db == 0.0f || db < noise_db) noise_db = db;
    else                                   noise_db += 0.001f * (db - noise_db);   // ~16 s to climb
    // 4 dB above the floor = starts to move, 28 dB above = full.
    const float l = (db - noise_db - 4.0f) * (100.0f / 24.0f);
    level = l < 0 ? 0 : l > 100 ? 100 : (uint8_t)l;
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
    level = 0;
    noise_db = 0.0f;
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
uint32_t voice_elapsed_ms(void) { return active ? millis() - start_ms : 0; }
uint8_t voice_level(void) { return active ? level : 0; }
