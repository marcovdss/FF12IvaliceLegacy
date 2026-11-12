// Ivalice Legacy co-op module: the co-op switches (kill switch, seat drop-in/out, ordered disarm).
//
// Runs on the co-op thread, once per beat, BEFORE the session step, so whatever it decides is in the session record,
// the isolation record and the steering record of the same beat ("stock on the same tick").
//
//  * Kill switch: the settings' kill-switch binding (default F8; keyboard or mouse, read while the game is in the
//    foreground, on a press edge whose previous sample was also in the foreground) or the bridge's KILL command
//    (the Mod Menu action). Off is an ordered disarm:
//      1. the same beat: the session is switched off (no seat is manual: movement goes stock, released characters
//         are asked to follow the leader) and isolation begins its release (no pad is masked or substituted; the
//         substitution host gets the neutral block once);
//      2. once isolation reports the release settled, or RELEASE_CAP_MS after step 1, the subsystem mask is applied
//         without INPUT_ISOLATION, then with nothing at all (CORE rows have no bit and keep running).
//    On restores the session and asks the UI tick to re-apply the settings' mask at its next safe point.
//  * Seat toggles: each guest seat's TOGGLE_CONTROL binding. Its keyboard keys (default F6 for P2, F7 for P3) are
//    read here for every guest seat that does not own the keyboard; pad bindings, and the keys of the seat that owns
//    the keyboard, arrive as action press edges through the input service (edge cursor). A press flips that seat
//    between playing and parked: the input service's live PARK (absorbs held input, reported to the Mod Menu) and, in
//    the same beat, the session's live park bits. A parked seat's character is released to follow the leader; a
//    seat that comes back claims its character by the session's rules (its preference, AUTO = first free regular,
//    another seat's character = swap). Toggles are ignored while co-op is off.
//  * P1 is never a toggle target: only seats 2..ILC_ACTIVE_SEATS_MAX are read, and P1's park bit is always 0.
//  * Settings (Mod Menu subsystem switches included) that turn INPUT_ISOLATION off go through the same ordered
//    release: the UI tick keeps the bit until isolation has released, then this module applies the new mask.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "input/keyboard.h"

namespace ilc::control {

constexpr uint64_t RELEASE_CAP_MS = 250;
constexpr uint64_t OVERRIDE_HOLD_MS = 500;  // a toggle's own park bit until the input service reports it

enum state : uint32_t {
    STATE_ON = 0,
    STATE_RELEASING = 1,  // kill switch pressed: waiting for isolation's release (at most RELEASE_CAP_MS)
    STATE_OFF = 2,
};

// ---- co-op thread ---------------------------------------------------------------------------------------------
void configure_from_settings(const ilc_settings& s);  // kill-switch keys, guest toggle keys, keyboard owner
void step(uint64_t now_ms);

// ---- any thread -----------------------------------------------------------------------------------------------
void post_kill(bool off);  // the bridge's KILL command: true = co-op off now, false = back on
uint32_t current_state();
uint32_t parked_seats();   // what the session was last given (bit s = seat s+1)

// ---- the UI tick (game thread), at the settings safe point --------------------------------------------------------
// The mask to apply for a newly applied settings record: while co-op is off, nothing; while releasing, what is
// applied now; a record that turns INPUT_ISOLATION off keeps the bit until the ordered release has run.
uint32_t mask_for_safe_point(uint32_t requested);
// After co-op comes back on: the settings' mask to re-apply at this safe point (once).
bool take_reapply(uint32_t* mask);

struct counters {
    volatile uint64_t kill_presses;   // kill-switch edges (keys and commands)
    volatile uint64_t kills;          // ordered disarms completed
    volatile uint64_t revives;
    volatile uint64_t released_by_settle;
    volatile uint64_t released_by_cap;
    volatile uint64_t toggles;        // seat toggles applied
    volatile uint64_t toggles_ignored;// while off, or P1, or a seat beyond the cap
    volatile uint64_t isolation_drops;// settings that turned INPUT_ISOLATION off (ordered)
    volatile uint64_t mask_busy;      // patch-layer lock busy: retried next beat
    volatile uint64_t last_release_ms;// duration of the last release wait
};
const counters* stats();

// ---- host tests ------------------------------------------------------------------------------------------------
void test_reset();
void test_keyboard(input::keyboard_api* keyboard);  // nullptr = the live keyboard
// The masks this module applied, oldest first (at most 16).
uint32_t test_mask_log(uint32_t* out, uint32_t max);

}  // namespace ilc::control
