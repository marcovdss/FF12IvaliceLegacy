// Ivalice Legacy co-op module: guest movement (the MOVEMENT subsystem).
//
// Three parts, on three threads:
//
//  * Co-op thread, step() once per heartbeat after the session step: reads the published session record, each
//    guest's logical input (action frame strengths and the movement consumer's own edge cursor), the camera view
//    matrix and a few facts about each steered actor (all through the fault-safe reader), runs the planner
//    (planner.h) and publishes the steering record (record_slot, wait-free for the detours) and the flee request.
//    It also turns the session's "ask a released character to follow its leader" effects into game-thread jobs.
//
//  * Game thread, the movement detours (hooks.cpp), each built on the fail-open template (patch/detour.h): the
//    dispatcher has already checked the gate (site armed, MOVEMENT bit applied, LIVE mode, no fault) and a fresh
//    liveness sample; fail_open re-checks that the subsystem may act and that the record is a whole copy made for
//    the dispatcher's epoch. A detour compares the game's own registers with the record's pointers first and
//    touches game memory (through the fault-safe probe) only for an actor the record names. Anything missing,
//    stale or not ours runs the original instructions.
//      MV-01 0x2E4380 entry of the per-actor locomotion vector: writes the guest's heading and command into the
//            physics object and, while the stick moves and no execution motion plays, selects the steering path
//            instead of an all-zero root-motion vector.
//      MV-02 0x2E4797 steering heading store: the native steering keeps the guest's heading.
//      MV-03 0x236328 facing store: the actor keeps the guest's visual heading.
//      MV-04 0x236323 visual facing call (a call row, through a call island): for a guest's actor whose physics
//            facing differs from the guest's visual heading, calls the game's facing setter, then the original.
//      MV-05 0x305D02 follower list evaluator: a controlled actor evaluates the native follower lists as empty.
//      MV-06 0x325E07 approach gate: a controlled actor never gets the native approach (rbx restored first).
//    MV-07 (gateway trigger call) belongs to the gateway work and stays unarmed (stock) here.
//
//  * Game thread, the UI-tick drain: OP_FOLLOW, the follow request. It re-resolves the actor from its handle
//    through the actor manager, checks its physics object, that it is idle, untargeted and attached to a native
//    follower list, and then sets its action and the action shadow from idle to "follow the leader", or reports
//    that it already follows. Any mismatch is a rejection with its own code and changes nothing.
//
// The flee request is read by the input isolation detours, which OR it into the flee control of P1's pad.
#pragma once

#include <stdint.h>

#include "movement/planner.h"
#include "patch/detour.h"
#include "runtime/game_thread.h"

namespace ilc::movement {

constexpr uint16_t OP_FOLLOW = 8;          // game-thread operation number (runtime/game_thread.h table)
constexpr uint32_t FOLLOW_TTL_MS = 100;    // one request window of the session's release intents
constexpr uint32_t EFFECT_BATCH = 32;

// OP_FOLLOW results.
enum follow_result : int32_t {
    FOLLOW_APPLIED = 1,
    FOLLOW_ALREADY = 2,
    FOLLOW_STALE_IDENTITY = -16,  // the handle no longer resolves to that actor
    FOLLOW_STALE_PHYSICS = -17,   // the actor's physics object changed
    FOLLOW_NOT_READY = -18,       // not idle, targeted, not on a follower list, or no follow descriptor
    FOLLOW_ACTION_CHANGED = -19,  // action and its shadow are neither both idle nor both "follow"
    FOLLOW_UNREADABLE = -20,      // a read or the write failed
    FOLLOW_BAD_ARGS = -21,
    FOLLOW_NO_WORLD = -22,        // the session record does not show a valid world (load, cutscene, title)
};

struct counters {
    volatile uint64_t steps;
    volatile uint64_t published;
    volatile uint64_t withdrawn;
    volatile uint64_t camera_reads;
    volatile uint64_t facts_failed;      // a steered actor's facts could not be read this tick
    volatile uint64_t follow_pushed;
    volatile uint64_t follow_refused;    // push refused (not live, full, bad epoch)
    volatile uint64_t follow_stale;      // an effect from an older session epoch, dropped
    volatile uint64_t follow_applied;    // game thread: OP_FOLLOW changed the action
    volatile uint64_t follow_rejected;   // game thread: OP_FOLLOW refused
    volatile uint64_t list_rejected;     // MV-05: a follower list evaluated as empty for a controlled actor
    volatile uint64_t approach_blocked;  // MV-06: no-approach exit taken
    volatile uint64_t vectors_written;   // MV-01: physics command written
    volatile uint64_t root_motion_overridden;
    volatile uint64_t facings_set;       // MV-04: the facing setter ran for a guest's actor
};

// ---- boot (before gtq::seal) ----------------------------------------------------------------------------------
// Registers OP_FOLLOW (world-requiring) and the handlers of MV-01..MV-06, and arms those sites. The
// gates still open only when the MOVEMENT bit is applied and the layer is LIVE. False if a row is missing or the
// operation cannot be registered (the boot then leaves movement disarmed).
bool attach();

// ---- co-op thread ---------------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms);
void step(uint64_t now_ms);
void stop();  // withdraws the record and the flee request

// ---- any thread ----------------------------------------------------------------------------------------------
patch::record_slot<steer_record>& slot();
bool read(steer_record* out);
// Seats (bit s) whose guest asks for flee, if the request was made for `epoch`; 0 otherwise.
uint32_t flee_seats(uint32_t epoch);
// Host tests: force the "MOVEMENT may act" answer the flee word uses (-1 = the patch layer decides).
void test_force_flee_live(int32_t forced);
// The Berserk manual-command gate (MV-10, a MOVEMENT row) is in effect: its gate is open and MOVEMENT may act.
// The session's P1 lead rule treats Berserk as harmless for P1 only while this is true.
bool berserk_gate_live();
const counters* stats();

// ---- the detour handlers and the operation (exposed for host tests) -----------------------------------------
int32_t mv01(patch::ctx* c, uint32_t epoch);
int32_t mv02(patch::ctx* c, uint32_t epoch);
int32_t mv03(patch::ctx* c, uint32_t epoch);
int32_t mv04(patch::ctx* c, uint32_t epoch);
int32_t mv05(patch::ctx* c, uint32_t epoch);
int32_t mv06(patch::ctx* c, uint32_t epoch);
int32_t op_follow(const gtq::job& j);
int32_t row_of(const char* id);  // site table row of an MV id, -1 if absent

// ---- host tests ----------------------------------------------------------------------------------------------
using input_source_fn = bool (*)(uint32_t seat, seat_input* out);  // seat = session seat 0..SEATS-1
void test_set_input_source(input_source_fn fn);  // nullptr = the live input service
void test_reset();
void test_last(plan_outputs* out);
void test_set_image_base(uintptr_t image_base);

}  // namespace ilc::movement
