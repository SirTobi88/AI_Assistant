#include <Arduino.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>
#include "../../hal/mic_hal.h"

// The sim's microphone: SIM_MIC_WAV (16 kHz mono 16-bit PCM WAV) played in
// real time from the start of each take, silence after its end or without a
// file. Lets bridge/tests drive push-to-talk end to end without hardware.

static std::vector<int16_t> wav;
static bool     loaded = false;
static bool     running = false;
static uint32_t start_ms = 0;
static size_t   given = 0;

static void load(void) {
    loaded = true;
    const char* path = getenv("SIM_MIC_WAV");
    if (!path || !*path) return;
    FILE* f = fopen(path, "rb");
    if (!f) { printf("[sim] mic: cannot open %s\n", path); return; }
    uint8_t head[12];
    if (fread(head, 1, 12, f) != 12 || memcmp(head, "RIFF", 4) || memcmp(head + 8, "WAVE", 4)) {
        fclose(f);
        return;
    }
    // Walk the chunks to "data" (fmt is assumed to be 16 kHz mono 16-bit).
    uint8_t ch[8];
    while (fread(ch, 1, 8, f) == 8) {
        const uint32_t n = ch[4] | ch[5] << 8 | ch[6] << 16 | (uint32_t)ch[7] << 24;
        if (!memcmp(ch, "data", 4)) {
            wav.resize(n / 2);
            const size_t got = fread(wav.data(), 2, wav.size(), f);
            wav.resize(got);
            break;
        }
        fseek(f, n + (n & 1), SEEK_CUR);
    }
    fclose(f);
    printf("[sim] mic: %zu samples from %s\n", wav.size(), path);
}

bool mic_hal_start(void) {
    if (!loaded) load();
    running = true;
    start_ms = millis();
    given = 0;
    return true;
}

void mic_hal_stop(void) { running = false; }

size_t mic_hal_read(int16_t* out, size_t max) {
    if (!running) return 0;
    const size_t due = (size_t)(millis() - start_ms) * (MIC_SAMPLE_RATE / 1000);
    size_t n = due > given ? due - given : 0;
    if (n > max) n = max;
    for (size_t i = 0; i < n; i++) {
        const size_t at = given + i;
        out[i] = at < wav.size() ? wav[at] : 0;
    }
    given += n;
    return n;
}
