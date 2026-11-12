// Ivalice Legacy co-op module: per-consumer edge cursors at the input seam.
//
// The input service ticks at 125 Hz; its snapshot keeps each tick's edges (pressed, released, repeat pulses) for
// that tick only. A reader that runs slower (the game thread, the device tick thread, a future network transport)
// would miss a tap that starts and ends between two of its reads. So every tick also lands in a short history,
// and each consumer keeps its own cursor:
//
//   edge_cursor cur;               // one per consumer, owned by that consumer's thread
//   edge_delta d;
//   if (edges::since(cur, seat, &d)) { ... d.pressed ... }
//
// since() ORs the edges of every tick after the cursor up to the newest one, returns the newest held state, and
// moves the cursor. A press is therefore seen by every consumer exactly once (and a press plus release that both
// happened since the last read are both reported), however slowly it reads. Consumers never affect each other.
//
// The history holds HISTORY ticks (about 2 s). A cursor that fell further behind gets every tick still held,
// ORed, with `overrun` set. A new service run (the input service restarted) resets the history; a cursor from an
// older run is re-seeded with no edges (`reseeded`). The first call on a fresh cursor seeds it at the newest tick
// and reports no edges: a consumer never replays presses from before it existed.
//
// One writer (the input service thread, or the manual service in host tests); any number of readers on any thread,
// wait-free: a reader copies an entry under that entry's own sequence word and skips nothing silently.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "ilc/seat_input.h"

namespace ilc::edges {

constexpr uint32_t HISTORY = 256;  // ticks (power of two)
constexpr uint32_t SEATS = ILC_SEATS;

struct edge_cursor {
    uint32_t run;      // the service run the cursor belongs to (0 = never used)
    uint32_t seq[SEATS];  // last tick consumed, per seat
};

struct edge_delta {
    uint32_t from_seq;       // first tick included (0 when none)
    uint32_t to_seq;         // newest tick included (= the cursor afterwards)
    uint32_t ticks;          // ticks ORed
    uint32_t pad_pressed;    // ilc_pad_control bits
    uint32_t pad_released;
    uint32_t pad_held;       // newest tick
    uint32_t act_pressed;    // ilc_action bits
    uint32_t act_released;
    uint32_t act_repeated;
    uint32_t act_held;       // newest tick
    uint8_t overrun;         // the cursor was more than HISTORY ticks behind
    uint8_t reseeded;        // first use, or a new service run: no edges reported
    uint8_t reserved[2];
};
static_assert(sizeof(edge_delta) == 44, "edge_delta layout (host tests read it)");

// ---- writer ----
void reset(uint32_t run);  // a new service run (run != 0)
// One tick: the seat frames and action frames the service is about to publish (SEATS of each).
void record(uint32_t seq, const ilc_seat_input_frame* frames, const ilc_seat_action_frame* actions);

// ---- readers (any thread) ----
// False when the service has not recorded anything yet or the seat is out of range (cursor unchanged).
bool since(edge_cursor& cursor, uint32_t seat, edge_delta* out);
uint32_t newest_seq();
uint32_t current_run();

// Host tests.
void test_reset();

}  // namespace ilc::edges
