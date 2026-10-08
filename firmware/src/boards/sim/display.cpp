#include "board.h"
#include "../../hal/display_hal.h"
#include "sim_platform.h"
#include <SDL.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static SDL_Window*   win = NULL;
static SDL_Renderer* ren = NULL;
static SDL_Texture*  tex = NULL;
static uint16_t*     shadow = NULL;   // full-frame RGB565 — what the panel "shows"
static uint8_t       brightness = 200;
static bool          dirty = false;
static SDL_Texture*  mask = NULL;     // black outside the inscribed circle, like the round panel

static void build_mask(void) {
    uint32_t* px = (uint32_t*)malloc((size_t)LCD_WIDTH * LCD_HEIGHT * 4);
    const float r = LCD_WIDTH / 2.0f, c = (LCD_WIDTH - 1) / 2.0f;
    for (int y = 0; y < LCD_HEIGHT; y++)
        for (int x = 0; x < LCD_WIDTH; x++) {
            float dx = x - c, dy = y - c;
            px[y * LCD_WIDTH + x] = (dx * dx + dy * dy > r * r) ? 0xFF000000u : 0;
        }
    mask = SDL_CreateTexture(ren, SDL_PIXELFORMAT_ARGB8888,
                             SDL_TEXTUREACCESS_STATIC, LCD_WIDTH, LCD_HEIGHT);
    SDL_UpdateTexture(mask, NULL, px, LCD_WIDTH * 4);
    SDL_SetTextureBlendMode(mask, SDL_BLENDMODE_BLEND);
    free(px);
}

static void present(void) {
    SDL_UpdateTexture(tex, NULL, shadow, LCD_WIDTH * 2);
    // AMOLED brightness → linear dim of the whole frame.
    SDL_SetTextureColorMod(tex, brightness, brightness, brightness);
    SDL_SetRenderDrawColor(ren, 0, 0, 0, 255);
    SDL_RenderClear(ren);
    SDL_RenderCopy(ren, tex, NULL, NULL);
    if (mask) SDL_RenderCopy(ren, mask, NULL, NULL);
    SDL_RenderPresent(ren);
    dirty = false;
}

void display_hal_init(void) {
    if (SDL_Init(SDL_INIT_VIDEO) != 0) {
        fprintf(stderr, "SDL_Init failed: %s\n", SDL_GetError());
        exit(1);
    }
    win = SDL_CreateWindow("AI Assistant sim",
                           SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
                           LCD_WIDTH, LCD_HEIGHT, 0);
    ren = SDL_CreateRenderer(win, -1, 0);
    if (!win || !ren) {
        fprintf(stderr, "SDL window/renderer failed: %s\n", SDL_GetError());
        exit(1);
    }
    tex = SDL_CreateTexture(ren, SDL_PIXELFORMAT_RGB565,
                            SDL_TEXTUREACCESS_STREAMING, LCD_WIDTH, LCD_HEIGHT);
    shadow = (uint16_t*)calloc((size_t)LCD_WIDTH * LCD_HEIGHT, 2);
    build_mask();
}

void display_hal_begin(void) { present(); }

void display_hal_set_brightness(uint8_t level) {
    if (level == brightness) return;
    brightness = level;
    // Present immediately so idle fades stay visible even while main.cpp
    // skips display_hal_tick() (asleep).
    present();
}

void display_hal_fill_screen(uint16_t color565) {
    for (int i = 0; i < LCD_WIDTH * LCD_HEIGHT; i++) shadow[i] = color565;
    dirty = true;
}

void display_hal_draw_bitmap(int32_t x, int32_t y, int32_t w, int32_t h,
                             const uint16_t* pixels) {
    for (int32_t row = 0; row < h; row++) {
        int32_t dy = y + row;
        if (dy < 0 || dy >= LCD_HEIGHT) continue;
        int32_t sx = x < 0 ? -x : 0;
        int32_t cw = w - sx;
        if (x + sx + cw > LCD_WIDTH) cw = LCD_WIDTH - (x + sx);
        if (cw <= 0) continue;
        memcpy(&shadow[dy * LCD_WIDTH + x + sx], &pixels[(size_t)row * w + sx],
               (size_t)cw * 2);
    }
    dirty = true;
}

void display_hal_tick(void) {
    if (dirty) present();
}

void display_hal_round_area(int32_t* x1, int32_t* y1, int32_t* x2, int32_t* y2) {
    (void)x1; (void)y1; (void)x2; (void)y2;  // no controller alignment quirks
}

void sim_display_set_title(const char* title) {
    if (win) SDL_SetWindowTitle(win, title);
}

void sim_display_screenshot(const char* path) {
    char buf[64];
    if (!path) {
        static int n = 0;
        snprintf(buf, sizeof(buf), "sim-shot-%d.bmp", n++);
        path = buf;
    }
    SDL_Surface* s = SDL_CreateRGBSurfaceWithFormatFrom(
        shadow, LCD_WIDTH, LCD_HEIGHT, 16, LCD_WIDTH * 2, SDL_PIXELFORMAT_RGB565);
    if (s && SDL_SaveBMP(s, path) == 0) printf("screenshot: %s\n", path);
    else printf("screenshot failed: %s\n", SDL_GetError());
    if (s) SDL_FreeSurface(s);
}
