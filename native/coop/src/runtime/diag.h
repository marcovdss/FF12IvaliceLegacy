// Ivalice Legacy co-op module: diagnostics for the Mod Menu's developer options.
//
//  * Counters: fill() copies the module's counters into the bridge's GET_COUNTERS record (ilc_counters). Every field
//    is a 32-bit value read without a lock (a counter may move while the record is filled; each field is one
//    aligned load). Read-only: nothing here changes how the module behaves, and the op answers in any state.
//  * Timing: the UI tick measures itself in time-stamp counter ticks (no import on the game thread), the co-op thread
//    and the bridge with the performance counter. fill() converts the tick counts to microseconds once the
//    time-stamp counter has been calibrated against the performance counter (CALIBRATE_MIN_MS after the first call).
//  * Stall: the bridge's STALL_COOP_THREAD command (developer setting only) posts one sleep for the co-op thread.
//    The co-op thread takes it at the start of its next beat and waits that long on its stop event, so a shutdown
//    still ends it at once. While it sleeps nothing heartbeats: after patch::LIVENESS_STALE_MS every detour runs
//    stock and the UI tick runs its stale clean-up; the next beat after it heartbeats again and the module carries
//    on (a stale heartbeat is not a fault, nothing is latched).
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "patch/patch_engine.h"

namespace ilc::diag {

constexpr uint64_t CALIBRATE_MIN_MS = 50;

// ---- boot -------------------------------------------------------------------------------------------------------
void calibrate_once();                        // any thread: the time-stamp counter's first reference point
void note_init_thread(uint32_t thread_id);    // FF12Hinit's caller
void note_init_done_thread(uint32_t thread_id);
void note_coop_thread(uint32_t thread_id);
void note_install(const patch::report& r, uint32_t attempts);

// ---- co-op thread -----------------------------------------------------------------------------------------------
void loop_begin();                    // the beat loop starts: the first beat's gap is measured from here
uint64_t beat_begin();                // returns the beat's start (performance counter)
void beat_end(uint64_t started);
// The posted stall, taken once: its milliseconds (0 = none) and the command's request id.
uint32_t take_stall(uint32_t* request_id);
void note_stall_done(uint32_t slept_ms);

// ---- bridge (script threads) ------------------------------------------------------------------------------------
// One stall at a time: false while another one is posted or running (the bridge answers BUSY).
bool post_stall(uint32_t ms, uint32_t request_id);
uint64_t perf_now();
void note_bridge_call(uint32_t op, uint64_t started);
// Everything except the header and the fields the bridge owns (its own counters, read under its lock).
void fill(ilc_counters* out);

// ---- host tests -------------------------------------------------------------------------------------------------
void test_reset();

}  // namespace ilc::diag
