#pragma once
#include <lvgl.h>
#include <stdint.h>

// Ring menu for the round 360x360 panel: one arc segment per item around the
// edge, the focused one wider and lit; the center names it (with its
// neighbours above and below), or shows a body text (prompts) with the
// focused option underneath. An inner arc shows hold-to-confirm progress.
#define RING_MAX_ITEMS 16
#define RING_LABEL_LEN 44

struct RingMenu;

RingMenu* ring_create(lv_obj_t* parent);
void ring_set_title(RingMenu* r, const char* title);
// nullptr = list mode (neighbours visible); text = body mode (prompts, previews).
void ring_set_body(RingMenu* r, const char* text);
// Resets the items; focus is clamped. Labels are set with ring_set_item().
void ring_set_count(RingMenu* r, int n, int focus);
// color 0 = default track colour.
void ring_set_item(RingMenu* r, int i, const char* label, const char* sub, uint32_t color);
void ring_set_sub(RingMenu* r, int i, const char* sub);
// Builders size the table generously with ring_set_count(), fill it, then cut
// it to what they used: keeps the items, clamps focus, refreshes.
void ring_trim(RingMenu* r, int n, int focus);
void ring_refresh(RingMenu* r);
int  ring_step(RingMenu* r, int delta);   // wraps; returns the new focus
int  ring_focus(const RingMenu* r);
int  ring_count(const RingMenu* r);
// 0 hides the hold arc, 1..1000 fills it.
void ring_set_hold(RingMenu* r, int permille);
