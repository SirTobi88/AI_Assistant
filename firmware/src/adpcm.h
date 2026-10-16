#pragma once
#include <stdint.h>
#include <stddef.h>

// IMA-ADPCM (4 bit per 16-bit sample) for the voice stream to the bridge
// (docs/protocol.md, AUDIO). Plain C, no Arduino: bridge/tests builds it on
// the host and checks it against bridge/voice.py's decoder.

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int16_t predictor;
    uint8_t index;
} adpcm_state_t;

void adpcm_reset(adpcm_state_t* st);

// Encodes n samples (n even) into n/2 bytes, low nibble first.
void adpcm_encode(adpcm_state_t* st, const int16_t* in, size_t n, uint8_t* out);

#ifdef __cplusplus
}
#endif
