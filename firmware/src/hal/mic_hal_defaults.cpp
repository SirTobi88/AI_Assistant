#include "mic_hal.h"

// Boards without a microphone: no recording, so push-to-talk sends nothing.
__attribute__((weak)) bool mic_hal_start(void) { return false; }
__attribute__((weak)) void mic_hal_stop(void) {}
__attribute__((weak)) size_t mic_hal_read(int16_t* out, size_t max) { (void)out; (void)max; return 0; }
