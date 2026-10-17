#include "ringmenu.h"
#include "theme.h"
#include <stdio.h>
#include <string.h>

LV_FONT_DECLARE(font_styrene_28);
LV_FONT_DECLARE(font_styrene_20);
LV_FONT_DECLARE(font_styrene_16);
LV_FONT_DECLARE(font_styrene_14);

#define SEG_WIDTH       8
#define SEG_WIDTH_FOCUS 16
#define SEG_GAP_DEG     4
#define HOLD_WIDTH      6

struct RingItem {
    char     label[RING_LABEL_LEN];
    char     sub[RING_LABEL_LEN];
    uint32_t color;
};

struct RingMenu {
    lv_obj_t* root;
    lv_obj_t* seg[RING_MAX_ITEMS];
    lv_obj_t* hold;
    lv_obj_t* title;
    lv_obj_t* prev;
    lv_obj_t* label;
    lv_obj_t* sub;
    lv_obj_t* next;
    lv_obj_t* body;
    RingItem  items[RING_MAX_ITEMS];
    int       n;
    int       focus;
    bool      body_mode;
};

static lv_obj_t* make_arc(lv_obj_t* parent, int size, int width) {
    lv_obj_t* a = lv_arc_create(parent);
    lv_obj_set_size(a, size, size);
    lv_obj_center(a);
    lv_obj_remove_style(a, NULL, LV_PART_KNOB);
    lv_obj_clear_flag(a, LV_OBJ_FLAG_CLICKABLE);
    lv_arc_set_rotation(a, 270);            // 0° = 12 o'clock
    lv_obj_set_style_arc_width(a, width, LV_PART_MAIN);
    lv_obj_set_style_arc_width(a, width, LV_PART_INDICATOR);
    lv_obj_set_style_arc_rounded(a, true, LV_PART_MAIN);
    lv_obj_set_style_arc_rounded(a, true, LV_PART_INDICATOR);
    lv_obj_set_style_bg_opa(a, LV_OPA_TRANSP, 0);
    lv_obj_set_style_pad_all(a, 0, 0);
    return a;
}

static lv_obj_t* make_label(lv_obj_t* parent, const lv_font_t* font, lv_color_t color, int y, int w) {
    lv_obj_t* l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, color, 0);
    lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_width(l, w);
    lv_label_set_long_mode(l, LV_LABEL_LONG_DOT);
    // One line, cut with "…": the rows sit at fixed heights, a wrapped long
    // session title ran into the next one.
    lv_obj_set_height(l, lv_font_get_line_height(font));
    lv_obj_align(l, LV_ALIGN_TOP_MID, 0, y);
    lv_label_set_text(l, "");
    return l;
}

RingMenu* ring_create(lv_obj_t* parent) {
    RingMenu* r = (RingMenu*)lv_malloc(sizeof(RingMenu));
    memset(r, 0, sizeof(*r));
    r->root = lv_obj_create(parent);
    lv_obj_remove_style_all(r->root);
    lv_obj_set_size(r->root, LV_PCT(100), LV_PCT(100));
    lv_obj_clear_flag(r->root, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_clear_flag(r->root, LV_OBJ_FLAG_CLICKABLE);   // the parent takes the touches

    const int d = lv_obj_get_width(parent) > 0 ? lv_obj_get_width(parent) : 360;
    for (int i = 0; i < RING_MAX_ITEMS; i++) {
        r->seg[i] = make_arc(r->root, d - 8, SEG_WIDTH);
        lv_obj_set_style_arc_opa(r->seg[i], LV_OPA_TRANSP, LV_PART_INDICATOR);
        lv_obj_add_flag(r->seg[i], LV_OBJ_FLAG_HIDDEN);
    }
    r->hold = make_arc(r->root, d - 52, HOLD_WIDTH);
    lv_obj_set_style_arc_opa(r->hold, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_arc_color(r->hold, THEME_ACCENT, LV_PART_INDICATOR);
    lv_arc_set_bg_angles(r->hold, 0, 360);
    lv_arc_set_range(r->hold, 0, 1000);
    lv_arc_set_value(r->hold, 0);
    lv_obj_add_flag(r->hold, LV_OBJ_FLAG_HIDDEN);

    r->title = make_label(r->root, &font_styrene_14, THEME_DIM, 58, 200);
    r->prev  = make_label(r->root, &font_styrene_16, THEME_RING_BG, 104, 220);
    r->label = make_label(r->root, &font_styrene_28, THEME_TEXT, 150, 260);
    r->sub   = make_label(r->root, &font_styrene_16, THEME_DIM, 190, 240);
    r->next  = make_label(r->root, &font_styrene_16, THEME_RING_BG, 236, 220);
    r->body  = make_label(r->root, &font_styrene_16, THEME_TEXT, 82, 250);
    lv_obj_set_height(r->body, 4 * 20);
    lv_obj_add_flag(r->body, LV_OBJ_FLAG_HIDDEN);
    return r;
}

void ring_set_title(RingMenu* r, const char* title) {
    lv_label_set_text(r->title, title ? title : "");
}

void ring_set_body(RingMenu* r, const char* text) {
    r->body_mode = text != nullptr;
    if (r->body_mode) {
        lv_label_set_text(r->body, text);
        lv_obj_clear_flag(r->body, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(r->prev, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(r->next, LV_OBJ_FLAG_HIDDEN);
        lv_obj_align(r->label, LV_ALIGN_TOP_MID, 0, 190);
        lv_obj_align(r->sub, LV_ALIGN_TOP_MID, 0, 230);
    } else {
        lv_obj_add_flag(r->body, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(r->prev, LV_OBJ_FLAG_HIDDEN);
        lv_obj_clear_flag(r->next, LV_OBJ_FLAG_HIDDEN);
        lv_obj_align(r->label, LV_ALIGN_TOP_MID, 0, 150);
        lv_obj_align(r->sub, LV_ALIGN_TOP_MID, 0, 190);
    }
}

void ring_set_count(RingMenu* r, int n, int focus) {
    if (n < 0) n = 0;
    if (n > RING_MAX_ITEMS) n = RING_MAX_ITEMS;
    r->n = n;
    r->focus = n ? (focus < 0 ? 0 : (focus >= n ? n - 1 : focus)) : 0;
    for (int i = 0; i < n; i++) r->items[i] = RingItem{};
}

void ring_set_item(RingMenu* r, int i, const char* label, const char* sub, uint32_t color) {
    if (i < 0 || i >= r->n) return;
    snprintf(r->items[i].label, RING_LABEL_LEN, "%s", label ? label : "");
    snprintf(r->items[i].sub, RING_LABEL_LEN, "%s", sub ? sub : "");
    r->items[i].color = color;
}

void ring_set_sub(RingMenu* r, int i, const char* sub) {
    if (i < 0 || i >= r->n) return;
    snprintf(r->items[i].sub, RING_LABEL_LEN, "%s", sub ? sub : "");
}

void ring_trim(RingMenu* r, int n, int focus) {
    if (n < 0) n = 0;
    if (n > r->n) n = r->n;
    r->n = n;
    r->focus = n ? (focus < 0 ? 0 : (focus >= n ? n - 1 : focus)) : 0;
    ring_refresh(r);
}

void ring_refresh(RingMenu* r) {
    const int n = r->n;
    const int span = n > 0 ? 360 / n : 360;
    const int gap = n > 1 ? SEG_GAP_DEG : 0;
    for (int i = 0; i < RING_MAX_ITEMS; i++) {
        lv_obj_t* a = r->seg[i];
        if (i >= n) { lv_obj_add_flag(a, LV_OBJ_FLAG_HIDDEN); continue; }
        lv_obj_clear_flag(a, LV_OBJ_FLAG_HIDDEN);
        // Centered on the item's slot so item 0 sits straight up.
        if (n == 1) {
            lv_arc_set_bg_angles(a, 0, 360);
        } else {
            const int mid = i * span;
            const int start = mid - span / 2 + gap / 2, end = mid + span / 2 - gap / 2;
            lv_arc_set_bg_angles(a, (start + 360) % 360, (end + 360) % 360);   // LVGL wraps past 0°
        }
        const bool focused = i == r->focus;
        lv_color_t c = r->items[i].color ? lv_color_hex(r->items[i].color) : THEME_RING_BG;
        if (focused && !r->items[i].color) c = THEME_ACCENT;
        lv_obj_set_style_arc_color(a, c, LV_PART_MAIN);
        lv_obj_set_style_arc_width(a, focused ? SEG_WIDTH_FOCUS : SEG_WIDTH, LV_PART_MAIN);
        lv_obj_set_style_arc_opa(a, focused || r->items[i].color ? LV_OPA_COVER : LV_OPA_70, LV_PART_MAIN);
    }
    if (n == 0) {
        lv_label_set_text(r->label, "");
        lv_label_set_text(r->sub, "");
        lv_label_set_text(r->prev, "");
        lv_label_set_text(r->next, "");
        return;
    }
    const RingItem& it = r->items[r->focus];
    lv_label_set_text(r->label, it.label);
    lv_label_set_text(r->sub, it.sub);
    if (!r->body_mode && n > 1) {
        lv_label_set_text(r->prev, r->items[(r->focus - 1 + n) % n].label);
        lv_label_set_text(r->next, n > 2 ? r->items[(r->focus + 1) % n].label : "");
    } else {
        lv_label_set_text(r->prev, "");
        lv_label_set_text(r->next, "");
    }
}

int ring_step(RingMenu* r, int delta) {
    if (r->n == 0) return 0;
    r->focus = ((r->focus + delta) % r->n + r->n) % r->n;
    ring_refresh(r);
    return r->focus;
}

int ring_focus(const RingMenu* r) { return r->focus; }
int ring_count(const RingMenu* r) { return r->n; }

void ring_set_hold(RingMenu* r, int permille) {
    if (permille <= 0) {
        lv_obj_add_flag(r->hold, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    lv_obj_clear_flag(r->hold, LV_OBJ_FLAG_HIDDEN);
    lv_arc_set_value(r->hold, permille > 1000 ? 1000 : permille);
}
