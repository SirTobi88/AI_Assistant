#include "sim_platform.h"
#include <SDL.h>
#include <Arduino.h>
#include <stdlib.h>
#include <stdio.h>

static bool quit = false;

static bool     pwr_down = false;
static uint32_t pwr_down_ms = 0;
static bool     pwr_long_fired = false;
static bool     edge_pressed = false, edge_long = false, edge_released = false;

static int  battery = 87;
static int  encoder_steps = 0;

// SIM_INPUT="ms:action,..." replays input for headless runs, e.g.
//   SIM_INPUT="1000:d,1400:d,2000:t,3000:h1800" SIM_AUTOSHOT_MS=6000
// Actions: u / d = one ring detent back / forward, t = tap,
// h<ms> = hold the screen that long, s = save a screenshot (sim-shot-N.bmp,
// or SIM_SHOT_PREFIX-N.bmp).
#define SCRIPT_MAX 64
struct ScriptStep { uint32_t at; char what; uint32_t len; };
static ScriptStep script[SCRIPT_MAX];
static int script_n = -1, script_i = 0;
static uint32_t touch_until = 0;

static void script_load(void) {
    script_n = 0;
    const char* v = getenv("SIM_INPUT");
    if (!v) return;
    while (*v && script_n < SCRIPT_MAX) {
        char* end;
        uint32_t at = strtoul(v, &end, 10);
        if (*end != ':') break;
        ScriptStep st = { at, end[1], 0 };
        v = end + 2;
        if (st.what == 'h') { st.len = strtoul(v, &end, 10); v = end; }
        script[script_n++] = st;
        while (*v && *v != ',') v++;
        if (*v == ',') v++;
    }
}

static void script_tick(void) {
    if (script_n < 0) script_load();
    const uint32_t now = millis();
    while (script_i < script_n && now >= script[script_i].at) {
        const ScriptStep& st = script[script_i++];
        switch (st.what) {
        case 'u': encoder_steps--; break;
        case 'd': encoder_steps++; break;
        case 't': touch_until = now + 60; break;
        case 'h': touch_until = now + st.len; break;
        case 's': {
            static int shot = 0;
            const char* pre = getenv("SIM_SHOT_PREFIX");
            char path[256];
            snprintf(path, sizeof(path), "%s-%d.bmp", pre ? pre : "sim-shot", shot++);
            sim_display_screenshot(path);
            break;
        }
        default: break;
        }
    }
}

bool sim_script_touch(void) { return millis() < touch_until; }
static bool charging = false;

static bool take(bool* f) { bool v = *f; *f = false; return v; }
bool sim_take_pwr_pressed(void)  { return take(&edge_pressed); }
bool sim_take_pwr_long(void)     { return take(&edge_long); }
bool sim_take_pwr_released(void) { return take(&edge_released); }
int  sim_battery_pct(void) { return battery; }
int  sim_take_encoder_steps(void) { int v = encoder_steps; encoder_steps = 0; return v; }
bool sim_charging(void)    { return charging; }
bool sim_should_quit(void) { return quit; }

// Matches the AXP2101 long-press threshold main.cpp's pair gesture expects.
#define PWR_LONG_MS 1500

void sim_pump(void) {
    SDL_Event e;
    while (SDL_PollEvent(&e)) {
        if (e.type == SDL_QUIT) quit = true;
        if (e.type == SDL_KEYDOWN && !e.key.repeat) {
            SDL_Keycode k = e.key.keysym.sym;
            switch (k) {
            case SDLK_ESCAPE: quit = true; break;
            case SDLK_SPACE:  sim_playback_toggle(); break;
            case SDLK_LEFT:   sim_playback_step(-1); break;
            case SDLK_RIGHT:  sim_playback_step(+1); break;
            case SDLK_UP:     encoder_steps--; break;
            case SDLK_DOWN:   encoder_steps++; break;
            case SDLK_d:      sim_playback_toggle_link(); break;
            case SDLK_s:      sim_display_screenshot(NULL); break;
            case SDLK_c:      charging = !charging; break;
            case SDLK_MINUS:  battery = battery < 5 ? 0 : battery - 5; break;
            case SDLK_EQUALS: battery = battery > 95 ? 100 : battery + 5; break;
            case SDLK_p:
                pwr_down = true;
                pwr_down_ms = millis();
                pwr_long_fired = false;
                break;
            default:
                if (k >= SDLK_1 && k <= SDLK_9) sim_playback_jump(k - SDLK_1);
                break;
            }
        }
        if (e.type == SDL_MOUSEWHEEL) encoder_steps += e.wheel.y > 0 ? -1 : (e.wheel.y < 0 ? 1 : 0);
        if (e.type == SDL_KEYUP && e.key.keysym.sym == SDLK_p && pwr_down) {
            pwr_down = false;
            if (!pwr_long_fired) edge_pressed = true;
            edge_released = true;
        }
    }
    if (pwr_down && !pwr_long_fired && millis() - pwr_down_ms >= PWR_LONG_MS) {
        pwr_long_fired = true;
        edge_long = true;
    }

    script_tick();

    // Headless CI hook: SIM_AUTOSHOT_MS=<ms> → screenshot + exit.
    static long autoshot_ms = -2;
    if (autoshot_ms == -2) {
        const char* v = getenv("SIM_AUTOSHOT_MS");
        autoshot_ms = v ? atol(v) : -1;
    }
    if (autoshot_ms >= 0 && millis() >= (uint32_t)autoshot_ms) {
        const char* p = getenv("SIM_AUTOSHOT_PATH");
        sim_display_screenshot(p ? p : "sim-autoshot.bmp");
        quit = true;
        autoshot_ms = -1;
    }
}
