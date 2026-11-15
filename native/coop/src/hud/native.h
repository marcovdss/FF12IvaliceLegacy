// Ivalice Legacy co-op module: the changes the co-op HUD makes to the stock HUD: the reduced party list (relayout.h)
// and target circles off (circles.h).
//
// Wanted while the Battle HUD may act: the RENDER subsystem is live (LIVE mode, armed, its bit set, not faulted) and
// the element switch is on (set_enabled; on by default). The kill switch (F8) clears every subsystem bit, the Mod
// Menu's RENDER switch clears this one, and a fault latch closes it: in each case the next UI tick gives both
// changes back. Who does what:
//
//  * co-op thread, step(): while wanted and the session reports a valid world, one game-thread job (OP_HUD_NATIVE,
//    RENDER, world-requiring) at most every RETRY_MS, never two in flight;
//  * game thread, the job: relayout::ensure_reduced() and circles::ensure_off(), each while its part is on;
//  * game thread, tick() (every UI tick, from runtime/tick.cpp, any mode): for each change that is in place while
//    not wanted (or while its part is off), its restore(). Giving back needs no gate: it only ever writes stock
//    bytes over bytes it recognises as its own;
//  * exit, restore_at_exit() (cleanup thread, after the co-op thread stopped; the module is disarmed, so the UI tick
//    no longer runs our code): the same restores, the layout under a bounded wait for its state.
// Nothing here reads or writes the TARGET INFO window: Legacy's Lua TI scaler keeps working untouched.
#pragma once

#include <stdint.h>

#include "runtime/game_thread.h"

namespace ilc::hud::native {

constexpr uint16_t OP_HUD_NATIVE = 24;  // game-thread operation number (runtime/game_thread.h table)
constexpr uint64_t RETRY_MS = 500;      // between jobs while wanted (a reloaded layout is noticed within this)
constexpr uint32_t JOB_TTL_MS = 500;
constexpr uint32_t EXIT_WAIT_MS = 50;   // restore_at_exit: how long it waits for the layout state

struct counters {
    volatile uint64_t pushed;
    volatile uint64_t push_refused;
    volatile uint64_t jobs_ran;
    volatile uint64_t give_backs;     // ticks that restored (something of ours was in place while not wanted)
    volatile uint64_t exit_restores;
};

struct status_view {
    uint8_t attached;
    uint8_t enabled;
    uint8_t wanted;        // as the game thread saw it last
    uint8_t reserved[5];
    uint32_t layout_state;   // relayout::layout_state
    uint32_t circles_state;  // circles::ensure_result
    uint32_t circles_site;   // circles::site_class
    int32_t last_job;        // gtq result of the last job (0 = none yet)
};

// ---- boot (before gtq::seal) ---------------------------------------------------------------------------------------
bool attach();

// ---- any thread ----------------------------------------------------------------------------------------------------
void set_enabled(bool on);   // the Battle HUD switch (the Mod Menu page wires it; on by default)
// The two changes one by one (battle_hud.h): a part that is off is given back on the next UI tick, the other kept.
enum part : uint32_t { PART_SHRINK = 0x01, PART_CIRCLES = 0x02, PART_ALL = 0x03 };
void set_parts(uint32_t parts);
uint32_t parts();
bool wanted();               // set_enabled, a part on and RENDER live
status_view status();
const counters* stats();

// ---- co-op thread --------------------------------------------------------------------------------------------------
void step(uint64_t now_ms);
void stop();

// ---- game thread (UI tick, every frame) ----------------------------------------------------------------------------
void tick(bool live);

// ---- cleanup thread ------------------------------------------------------------------------------------------------
void restore_at_exit();

// ---- host tests -----------------------------------------------------------------------------------------------------
void test_reset();
void test_force_live(int32_t live);   // -1 = the patch layer decides, 0 / 1 = forced
void test_set_attached(bool attached);  // as if attach() had (not) registered the operation
int32_t test_run_job(uint64_t now_ms);  // the job's body, called directly

}  // namespace ilc::hud::native
