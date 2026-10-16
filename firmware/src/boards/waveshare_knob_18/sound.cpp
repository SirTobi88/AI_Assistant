#include <Arduino.h>
#include "../../hal/sound_hal.h"
#include "../../chime.h"
#include "board.h"

// The PCM5100A DAC drives the 3.5 mm line-out (no amp or speaker on the
// board): headphones or an active speaker there play the shared chime
// engine's bell and cues. I2S1 BCLK 39, WS 40, DOUT 41 as in Waveshare's
// 07_Audio_Test; the DAC needs no setup over I2C. GPIO 0 (also BOOT, an input
// with pull-up) high selects the S3 as the DAC's source on the CH445P mux, so
// nothing has to drive it. The DAC's soft mute (XSMT) belongs to the second
// MCU and is left alone.
//
// The DRV2605 was tried as a substitute for the pairing cues and dropped: on
// this kit the vibration is too weak to notice, whatever it is fed. The driver
// is not the problem — it reports DEVICE_ID 7, auto-calibration passes
// (DIAG_RESULT clear) and measures a real back-EMF, so the motor does turn.
// Every ROM effect from Strong Click to Long Buzz went unnoticed, and so did
// constant full-amplitude drive (RTP) both at the stock ~3 V clamp and at the
// 5.4 V maximum. The motor is simply too small for the mass of the knob it has
// to shake. Pairing feedback on this board is the on-screen overlay alone; the
// ring's detent click stays, since there the fingertips are already on the
// ring that moves.

void sound_hal_init(void) {
    const ChimeConfig cfg = {
        -1, SND_I2S_BCLK, SND_I2S_WS, SND_I2S_DOUT, -1,
        44100, 0 /* no codec */, 100, nullptr
    };
    chime_init(cfg);
}

void sound_hal_tick(void)            { chime_tick(); }
void sound_hal_play_reset(void)      { chime_play(); }
void sound_hal_play_pair_armed(void) { chime_play_cue(CHIME_CUE_PAIR_ARMED); }
void sound_hal_play_paired(void)     { chime_play_cue(CHIME_CUE_PAIRED); }
void sound_hal_set_volume(uint8_t pct) { chime_set_gain(pct); }

void sound_hal_play_state(sound_state_t state) {
    switch (state) {
        case SOUND_STATE_NEEDS_YOU: chime_play_cue(CHIME_CUE_NEEDS_YOU); break;
        case SOUND_STATE_YOUR_TURN: chime_play_cue(CHIME_CUE_YOUR_TURN); break;
        case SOUND_STATE_LIMIT:     chime_play_cue(CHIME_CUE_LIMIT);     break;
    }
}
