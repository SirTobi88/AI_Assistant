#pragma once
#include <stdint.h>
#include <stddef.h>

// Microphone for push-to-talk: 16 kHz, mono, 16-bit samples. The Knob-1.8
// reads its PDM MEMS mic (CLK 45, DATA 46) in a task of its own; the sim
// plays a WAV file (SIM_MIC_WAV) or silence. Boards without a mic keep the
// weak defaults (mic_hal_defaults.cpp): start() fails, so no voice.

#define MIC_SAMPLE_RATE 16000

// Claims the microphone's I2S port at boot, before sound_hal_init(): PDM
// input exists on I2S0 only, and the sound output would otherwise take it.
void   mic_hal_init(void);

bool   mic_hal_start(void);
void   mic_hal_stop(void);
// Samples recorded since the last call, up to max; never blocks.
size_t mic_hal_read(int16_t* out, size_t max);
