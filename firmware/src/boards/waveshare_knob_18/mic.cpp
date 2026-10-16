#include <Arduino.h>
#include <ESP_I2S.h>
#include "../../hal/mic_hal.h"

// PDM MEMS microphone (MSM261D3526H1CPM) on I2S0: CLK GPIO 45, DATA GPIO 46,
// as in Waveshare's 07_Audio_Test. A task reads it into a ring buffer so the
// render loop never waits on I2S; mic_hal_read() drains that buffer.

#define MIC_PDM_CLK  45
#define MIC_PDM_DATA 46
#define RING_SAMPLES 8192            // 0.5 s at 16 kHz

static I2SClass i2s;
static int16_t ring_buf[RING_SAMPLES];
static volatile size_t head = 0, tail = 0;    // head: task writes, tail: loop reads
static volatile bool running = false;
static TaskHandle_t task = nullptr;

static void reader_task(void*) {
    int16_t chunk[256];
    for (;;) {
        const size_t got = i2s.readBytes((char*)chunk, sizeof(chunk)) / sizeof(int16_t);
        if (!running) continue;
        for (size_t i = 0; i < got; i++) {
            const size_t next = (head + 1) % RING_SAMPLES;
            if (next == tail) break;          // reader fell behind: drop the newest
            ring_buf[head] = chunk[i];
            head = next;
        }
    }
}

static bool ready = false;

// The port stays claimed from boot on (the sound output would take I2S0
// otherwise); between takes the task just drops what it reads.
void mic_hal_init(void) {
    i2s.setPinsPdmRx(MIC_PDM_CLK, MIC_PDM_DATA);
    if (!i2s.begin(I2S_MODE_PDM_RX, MIC_SAMPLE_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO)) {
        Serial.println("mic: I2S PDM init failed");
        return;
    }
    i2s.setTimeout(50);
    ready = true;
    xTaskCreatePinnedToCore(reader_task, "mic", 4096, nullptr, 5, &task, 0);
}

bool mic_hal_start(void) {
    if (!ready) return false;
    head = tail = 0;
    running = true;
    return true;
}

void mic_hal_stop(void) { running = false; }

size_t mic_hal_read(int16_t* out, size_t max) {
    size_t n = 0;
    while (n < max && tail != head) {
        out[n++] = ring_buf[tail];
        tail = (tail + 1) % RING_SAMPLES;
    }
    return n;
}
