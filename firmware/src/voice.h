#pragma once
#include <stdint.h>

// Push-to-talk recording: microphone -> IMA-ADPCM frames -> AUDIO notify
// (docs/protocol.md). Frames: [seq u16 LE][predictor i16 LE][index u8] +
// 128 bytes = 256 samples (16 ms). Each frame carries the decoder state, so a
// lost frame costs 16 ms, not the rest of the take.

#define VOICE_MAX_MS 30000

bool voice_start(void);   // false: no microphone
void voice_stop(void);    // sends what is left
// Call every loop while recording; false once the take hit VOICE_MAX_MS
// (and has stopped itself).
bool voice_tick(void);
bool voice_active(void);
uint32_t voice_elapsed_ms(void);
// Loudness of the last frame, 0..100 (peak, log-ish), for the recording view.
uint8_t voice_level(void);
