#include "../../hal/input_hal.h"
#include <SDL.h>
#include "sim_platform.h"

void input_hal_init(void) {}

bool input_hal_is_held(InputButton btn) {
    const Uint8* keys = SDL_GetKeyboardState(NULL);
    switch (btn) {
    case INPUT_BTN_PRIMARY:   return keys[SDL_SCANCODE_B] != 0;
    case INPUT_BTN_SECONDARY: return keys[SDL_SCANCODE_N] != 0;
    }
    return false;
}

int input_hal_encoder_steps(void) { return sim_take_encoder_steps(); }
