// Ivalice Legacy co-op module: town visibility (part of the PARTY subsystem).
//
// In a town only player-controlled characters have a body (filter.h has the rule). Three parts, on three threads:
//
//  * Co-op thread, step() once per heartbeat after the session step: turns the session record into the set of
//    controlled party ids and publishes the town record (record_slot, wait-free for the detour). It then reads the
//    party layouts and the engine's town state through the fault-safe reader and, when the bodies in the current
//    layout no longer match the controlled set (a guest joined, left or changed character; the leader changed),
//    asks for one rebuild through the game-thread queue, debounced (filter.h, planner).
//
//  * Game thread, TW-01 at 0x326A34, the join point of the layout builder 0x3266C0 after its mode switch and
//    before its bind loop (fail-open template, patch/detour.h). In town mode it blanks the current/HUD slots of
//    uncontrolled members; the stock bind loop then unbinds their slot actors. It touches only the layout the
//    interrupted builder is writing (rdi, checked against the party block pointer) and reads the town state
//    itself through the probe, so it also runs during the area-entry rebuild (no GATE_WORLD needed: no world
//    object is dereferenced beyond the builder's own block and the static area table). Menus keep the full party:
//    they read the default and menu layouts, which the filter never writes. Outside town mode it is stock.
//
//  * Game thread, the UI-tick drain: OP_TOWN_REBUILD (world-requiring). Re-checks the session (valid world in the
//    job's epoch, field phase, player control), the engine's town mode and that no re-materialize task is in
//    flight, then calls the stock party re-materialize 0x37CA80, whose immediate rebuild runs TW-01.
//
// The arm bit is ILC_SUBSYS_PARTY: with it clear (or the module DRY, disarmed or faulted) the detour is stock and
// the job is refused; the record is still published (it costs nothing).
#pragma once

#include <stdint.h>

#include "patch/detour.h"
#include "runtime/game_thread.h"
#include "towns/filter.h"

namespace ilc::towns {

constexpr uint16_t OP_TOWN_REBUILD = 9;   // game-thread operation number (runtime/game_thread.h table)
constexpr uint32_t REBUILD_TTL_MS = 200;

enum record_flag : uint32_t {
    TOWNF_ACTIVE = 0x1,  // a session runs: the filter applies in town mode
};

struct town_record {
    patch::record_head head;  // epoch = session epoch, flags = record_flag, generation = session generation
    uint64_t allowed;         // bit m: party id m is controlled (P1's character, manual guests' characters)
};
static_assert(sizeof(town_record) == 24, "town_record layout");

// OP_TOWN_REBUILD results.
enum rebuild_result : int32_t {
    REBUILD_APPLIED = 1,
    REBUILD_NO_WORLD = -16,     // session record missing, of another epoch, or not a valid world
    REBUILD_NOT_FIELD = -17,    // menu, modal, cutscene, load, or player control off
    REBUILD_NOT_TOWN = -18,     // the engine's party mode is not town mode
    REBUILD_UNREADABLE = -19,   // a read failed
    REBUILD_BUSY = -20,         // a re-materialize task is already in flight
    REBUILD_POOL_FULL = -21,    // the call created no task (the game's task pool was full): nothing happened
};

struct counters {
    volatile uint64_t steps;
    volatile uint64_t published;
    volatile uint64_t withdrawn;
    volatile uint64_t reads_failed;     // co-op thread: a layout/town read failed this step
    volatile uint64_t pushed;
    volatile uint64_t push_refused;
    volatile uint64_t applied;          // game thread: a rebuild ran
    volatile uint64_t refused;          // game thread: the operation refused
    volatile uint64_t gave_up;          // co-op thread: steps spent at a key/world's cap (after an attempt)
    volatile uint64_t degraded;         // co-op thread steps in town with a stock town arm
    volatile uint64_t filtered;         // TW-01: slots hidden at least once in that call
    volatile uint64_t hidden_slots;     // TW-01: slots hidden in total
    volatile uint64_t no_leader;        // TW-01: town mode, but the leader is not in current (nothing written)
    volatile uint64_t contract_failed;  // TW-01: registers or block pointer not as the builder leaves them
};

// ---- boot (before gtq::seal) ----------------------------------------------------------------------------------
// Registers OP_TOWN_REBUILD (PARTY, world-requiring) and the TW-01 handler, and arms the site. False if the row is
// missing or the operation cannot be registered (the boot then latches PARTY as faulted).
bool attach();

// ---- co-op thread ---------------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms);
void step(uint64_t now_ms);
void stop();  // withdraws the record

// ---- any thread -----------------------------------------------------------------------------------------------
patch::record_slot<town_record>& slot();
const counters* stats();

// ---- the detour handler and the operation (exposed for host tests) ------------------------------------------
int32_t tw01(patch::ctx* c, uint32_t epoch);
int32_t op_rebuild(const gtq::job& j);
int32_t row();

// ---- host tests -----------------------------------------------------------------------------------------------
struct debug_view {
    planner_state planner;
    planner_inputs last_inputs;
    int32_t last_action;
    int32_t last_result;
    uint64_t allowed;
};
using rematerialize_fn = void (*)();
void test_set_rematerialize(rematerialize_fn fn);  // nullptr = the game's 0x37CA80
void test_set_image_base(uintptr_t image_base);
void test_debug(debug_view* out);
void test_reset();

}  // namespace ilc::towns
