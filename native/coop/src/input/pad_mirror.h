// Ivalice Legacy co-op module: a small per-seat copy of each input tick's pad, for detours.
//
// The input service's snapshot is several kilobytes; a detour on a game thread must not copy that. Every input
// tick (125 Hz) the service also writes, per seat, the few fields the input-isolation detours need: where the
// seat's pad came from (XInput slot or SDL), whether it is connected, the raw pad as an XINPUT_GAMEPAD image and
// the held action bits. Each seat has its own sequence lock; readers copy 32 bytes and never wait.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "ilc/seat_input.h"

namespace ilc::pads {

struct pad_view {
    uint32_t seq;          // input tick (0 = never written)
    uint32_t generation;   // source connection generation
    uint8_t source;        // ilc_frame_source
    uint8_t xinput_slot;   // 0..3 when source is XINPUT, else 0xFF
    uint8_t flags;         // ilc_frame_flag
    uint8_t reserved;
    uint32_t act_held;     // ilc_action bits held
    ilc_xinput_gamepad pad;  // raw pad; all zero when not connected
    uint8_t reserved2[4];
};
static_assert(sizeof(pad_view) == 32, "pad_view layout (host tests read it)");

// Writer: the input service thread (or the manual service), once per tick, ILC_SEATS frames and action frames.
void record(const ilc_seat_input_frame* frames, const ilc_seat_action_frame* actions);
void reset();
// Any thread, wait-free; false if never written or torn (seat 1..ILC_SEATS).
bool read(uint32_t seat, pad_view* out);
// Host tests: write one seat directly.
void test_write(uint32_t seat, const pad_view& v);
void test_hold(uint32_t seat, bool odd);  // leave the seat's sequence odd (a writer "inside"): reads fail

}  // namespace ilc::pads
