#pragma once

// Native desktop simulator "board" — an SDL2 window standing in for the
// round 360×360 Knob-1.8 so shared code (main.cpp, ui.cpp, splash.cpp) can be
// developed without hardware. No pins here, just geometry and the key map.
//
//   mouse / left-drag    touch (tap toggles splash <-> usage)
//   up / down, wheel     rotary ring: one detent per key press / wheel notch
//   space                play/pause scenario playback
//   left / right         step one scenario state (pauses playback)
//   1..9                 jump to scenario state N (pauses playback)
//   d                    toggle BLE connected/disconnected
//   b (hold)             PRIMARY button  (BOOT — HID Space PTT on hardware)
//   n (hold)             SECONDARY button (HID Shift+Tab on hardware)
//   p                    PWR button (short press; hold ~3s + release = pair)
//   c                    toggle charging       - / =   battery down / up 5%
//   s                    save screenshot BMP to the current directory
//   esc / window close   quit
//
// Scenario: sim/scenario.jsonl (relative to the firmware/ dir), overridable
// with SIM_SCENARIO=<path>. One JSON object per line — the daemon payload
// plus optional "name" and "hold_ms" (default 3000). Lines starting with #
// are comments. Missing file → a small built-in state list.
//
// Headless / CI: SDL_VIDEODRIVER=dummy SIM_AUTOSHOT_MS=<ms> saves a
// screenshot (SIM_AUTOSHOT_PATH, default sim-autoshot.bmp) after <ms> and
// exits.

#define BOARD_NAME  "Simulator Knob 360"
#define LCD_WIDTH   360
#define LCD_HEIGHT  360
