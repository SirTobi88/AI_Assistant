#pragma once
#include <stdint.h>
#include <stddef.h>

enum ble_state_t {
    BLE_STATE_INIT,
    BLE_STATE_ADVERTISING,
    BLE_STATE_CONNECTED,
    BLE_STATE_DISCONNECTED,
};

void ble_init(void);
void ble_tick(void);
ble_state_t ble_get_state(void);
const char* ble_get_device_name(void);
const char* ble_get_mac_address(void);
void ble_clear_bonds(void);
bool ble_has_bonds(void);

// True while a central recently finished the security handshake WITHOUT
// bonding — the signature of a host that still holds a key this board no
// longer has. That host keeps listing the board as paired and keeps failing to
// connect, which from the board's side is indistinguishable from no host at
// all unless we say so. Clears on a successful bond, on ble_clear_bonds(), and
// on its own after a while.
bool ble_pairing_rejected(void);
bool ble_has_data(void);
const char* ble_get_data(void);
void ble_send_ack(void);
void ble_send_nack(void);
void ble_request_refresh(void);
// True once after the host subscribed to TX notifications: only then does a
// command (the hello) reach it.
bool ble_take_tx_subscribed(void);

// Device -> host command (docs/protocol.md), notified on the TX characteristic.
// Framed automatically when longer than one packet. No-op while disconnected.
void ble_send_cmd(const char* json);
// Device -> host voice frame (src/voice.h), notified on the AUDIO characteristic.
void ble_send_audio(const uint8_t* data, size_t len);

void ble_set_battery_level(int pct);

// BLE HID keyboard
void ble_keyboard_press(uint8_t key, uint8_t modifier);
void ble_keyboard_release(void);
