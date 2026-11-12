// Ivalice Legacy co-op module: the UI-tick handler on UT-01 (the call to the UI manager tick, once per frame on
// the game thread). It is the only place game functions run, and the module's settings safe point.
//
// Per frame, when the dispatcher lets it run (gate open, liveness fresh; UT-01 is CORE, so it also runs in DRY):
//   1. the settings safe point (bridge): staged settings become the applied settings; a newly applied
//      subsystems_armed mask goes to the patch layer's arm gates (try-lock; retried next frame if busy);
//      the input service receives the applied record (try-lock; retried next frame if busy);
//   2. LIVE: drain the game-thread queue (game_thread.h); DRY: reject queued jobs as DISARMED without running
//      any of them, so no game function is called and no game memory is written;
//   3. RUN_ORIGINAL: the stock tick call follows exactly once, with every register as it was.
// Re-entry (a job whose game call reaches this site again, or a second thread) runs stock and is counted.
// With a stale, torn or disarmed liveness sample the dispatcher never calls this: the frame is stock, the queue
// waits, and a job that waits past its ttl is rejected later instead of running late.
#pragma once

#include <stdint.h>

#include "patch/patch_engine.h"

namespace ilc::tick {

struct counters {
    volatile uint64_t ticks;            // handler runs
    volatile uint64_t reentered;        // runs refused because the handler was already running
    volatile uint64_t settings_applied; // safe points that applied staged settings
    volatile uint64_t safe_point_busy;  // safe points that found the bridge lock taken
    volatile uint64_t mask_applied;     // subsystem masks handed to the arm gates
    volatile uint64_t mask_deferred;    // ... that found the patch-layer lock taken (retried next frame)
    volatile uint64_t jobs_drained;     // LIVE
    volatile uint64_t jobs_flushed;     // DRY
    volatile uint64_t last_tick_ms;     // module clock
};

// The UT-01 handler (patch::handler_fn).
int32_t ut01(patch::ctx* c, uint32_t epoch);

// Boot: registers ut01 on UT-01, arms that site and takes the safe point over from the bridge's debug op.
// False if the site table has no PATCH row named UT-01.
bool attach();

int32_t ut01_row();
const counters* stats();

// Host tests only.
void test_reset();

}  // namespace ilc::tick
