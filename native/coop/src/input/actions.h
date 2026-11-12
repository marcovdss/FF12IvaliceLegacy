// Ivalice Legacy co-op module: the action layer (bindings, edges, repeat, hold absorption per action).
//
// Pure logic, like input_core: no thread, no clock, no Windows call. Each tick it turns the four seat input
// frames, the keyboard state and the applied binding tables into one ilc_seat_action_frame per seat.
//
// Bindings. Each seat has up to four bindings per action (bridge_abi.h: index = action * 4 + slot); an action is
// held while any of them is. A pad binding names a control on the seat's own pad: the seat's input frame, which
// already follows the seat's device through its aliases (XInput slot or SDL identity). Keyboard and mouse
// bindings count only for the seat that owns the keyboard and mouse (kbm_seat), and only while the game window
// is in the foreground. When the game confirms with Circle (the decide setting), every pad binding of CONFIRM
// and CANCEL swaps A and B; keyboard bindings never swap.
//
// Edges and repeat. pressed/released are per action. A held menu direction (MENU_UP/DOWN/LEFT/RIGHT) repeats
// after 0.250 s, then every 0.100 s; with fast repeat on, after 0.100 s, then every 0.035 s. Turning fast repeat
// on while a direction is held caps the pending timer to 0.035 s from now. Turning it off keeps the pending time.
// One pulse per tick at most (no bursts after a stall). A press is never also a repeat.
//
// Hold absorption. Absorption is per pad control and per key. A control or key held at a discontinuity is
// absorbed: it makes no edge (no press, no repeat) until it is released and pressed again. It still counts as
// held, with its strength, so a stick held through a reconnect keeps steering; only edges are suppressed.
//   pad controls and keys: the first tick, a change of the seat's binding table, an explicit request (seat
//               moved, pairing or capture ended), the end of a suppression;
//   pad controls only: anything the input frame already absorbed (device change, reconnect, pad settings
//               change), and A/B when the decide setting changes;
//   keys only:  the keyboard and mouse moving to another seat, and the game window regaining the foreground.
// So a keyboard reseed keeps a direction held on the pad pressing and repeating, and the other way round, and a
// second key of an action still presses while the first one stays absorbed.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "ilc/seat_input.h"
#include "input/input_core.h"

namespace ilc::input {

inline constexpr uint64_t REPEAT_FIRST_US = 250000;  // 0.250 s
inline constexpr uint64_t REPEAT_NEXT_US = 100000;   // 0.100 s
inline constexpr uint64_t FAST_FIRST_US = 100000;    // 0.100 s
inline constexpr uint64_t FAST_NEXT_US = 35000;      // 0.035 s
inline constexpr uint32_t ALL_ACTIONS = (1u << ILC_ACTIONS) - 1u;
inline constexpr uint32_t REPEAT_ACTIONS = (1u << ILC_ACTION_MENU_UP) | (1u << ILC_ACTION_MENU_DOWN) |
                                           (1u << ILC_ACTION_MENU_LEFT) | (1u << ILC_ACTION_MENU_RIGHT);
inline constexpr uint32_t DECIDE_ACTIONS = (1u << ILC_ACTION_CONFIRM) | (1u << ILC_ACTION_CANCEL);

// Virtual-key state: bit (vk & 31) of down[vk >> 5]. VK 1 and 2 are the left and right mouse buttons.
struct key_state {
    uint32_t down[8];
};

inline bool key_is_down(const key_state& k, uint32_t vk)
{
    return vk < 256 && (k.down[vk >> 5] & (1u << (vk & 31u))) != 0;
}

inline void key_set(key_state& k, uint32_t vk)
{
    if (vk < 256) {
        k.down[vk >> 5] |= 1u << (vk & 31u);
    }
}

struct action_inputs {
    const config* cfg;                    // applied settings (bindings, kbm_seat)
    const ilc_seat_input_frame* frames;   // [SEATS]
    key_state keys;                       // the kbm seat's keys; ignored while not focused
    uint8_t focused;                      // the game window is in the foreground
    uint8_t decide_swap;                  // the game confirms with Circle: pad A/B swap for CONFIRM/CANCEL
    uint8_t fast[SEATS];
    uint8_t suppress[SEATS];              // pairing/capture owns the seat: neutral frame
    uint8_t reseed[SEATS];                // explicit discontinuity this tick (both parts)
};

struct seat_action_state {
    uint32_t prev_held;     // actions held last tick (absorbed included)
    uint32_t prev_edge;     // actions held by a part that may make edges, last tick
    uint32_t prev_pad;      // the same, pad part only
    uint32_t prev_key;      // the same, keyboard part only
    uint32_t pad_absorbed;  // pad controls absorbed by this layer (bit per ilc_pad_control - 1)
    key_state key_absorbed; // keys absorbed by this layer
    uint8_t prompt_device;
    uint8_t suppressed;
    uint8_t fast;
    uint8_t reserved;
    uint64_t repeat_due_us[ILC_ACTIONS];
    uint8_t pulsed[ILC_ACTIONS];  // a repeat already came since the press
    uint8_t reserved2[1];
    ilc_binding bindings[ILC_SEAT_BINDINGS];  // the table the absorption state belongs to
};

struct action_layer {
    uint8_t started;
    uint8_t last_focused;
    uint8_t last_kbm;
    uint8_t last_swap;
    seat_action_state seats[SEATS];
};

void actions_reset(action_layer& st);
void actions_step(action_layer& st, const action_inputs& in, uint64_t now_us, uint32_t seq,
                  ilc_seat_action_frame out[SEATS]);

// The virtual keys the kbm seat's bindings name (keys and mouse buttons): what the service needs to read.
void actions_wanted_keys(const config& cfg, key_state* out);

// One binding of a seat's table, after the decide swap (exposed for tests and button prompts).
ilc_binding effective_binding(const ilc_binding& b, uint32_t action, bool decide_swap);

}  // namespace ilc::input
