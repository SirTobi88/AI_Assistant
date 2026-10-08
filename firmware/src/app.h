#pragma once
#include <stdint.h>
#include "data.h"

// The ring-menu layer above the usage/splash screens. Closed, the device
// behaves like before (tap toggles splash/usage, hold = push-to-talk). A ring
// turn opens the main ring; host prompts open their own ring on arrival.
//
// Gestures while open: ring = move, tap = select, double tap = back,
// hold = push-to-talk (or hold-to-confirm on options that need it).
void app_init(void);
void app_tick(void);

bool app_is_open(void);
// Ring detents; returns true when the app used them (always, once open).
bool app_encoder(int steps);

// After proto_handle() accepted a message.
void app_on_proto(void);
// Each new usage payload (for the Monitor ring's limits page).
void app_set_usage(const UsageData* usage);
// Link state, so the rings can say "not connected" instead of sending into nothing.
void app_set_connected(bool connected);
