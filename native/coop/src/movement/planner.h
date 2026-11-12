// Ivalice Legacy co-op module: the per-seat movement planner and the steering record the movement detours read.
//
// Once per co-op tick the planner turns what the session published (who steers which actor this pass, the gates),
// each guest's logical input and a few facts about each steered actor into one steering record:
//
//   per guest seat that steers: actor, physics object and scene root (compared as pointers by the detours), the
//   commanded heading and physics command (what the steering function moves the body by), and the visual heading
//   (the body's facing, turned at the leader's rate toward the commanded heading).
//
// Rules, as Ivalice Together's follower movement (behaviour ported, written anew):
//  * No input keeps the last heading; the command drops to zero. The walk/run toggle is per seat and flips on a
//    fresh press only. Running with weapons drawn uses the slower armed step.
//  * An execution motion (an attack swing) zeroes the command and faces the motion's target when it has one.
//  * A newly steered actor (another actor, incarnation or world) starts from its own current headings, read from
//    its physics object, so taking control never snaps the body round. Without them the seat waits a tick.
//  * Movement is requested only while the session lets guests move; otherwise the seat keeps its actor (the AI
//    must not drag it back) with a zero command.
//  * Flee: a manual guest holding their flee action asks for the game's flee input while co-op is on.
//
// Pure: no memory reads, no clock, no thread. movement.cpp gathers the inputs on the co-op thread.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "movement/steering.h"
#include "patch/detour.h"
#include "session/session.h"

namespace ilc::movement {

constexpr uint32_t SEATS = ILC_SEATS;
constexpr uint32_t ACTIVE_SEATS = ILC_ACTIVE_SEATS_MAX;
constexpr uint32_t SLOTS = ACTIVE_SEATS - 1;  // guest seats 1..ACTIVE_SEATS-1 -> slot seat-1

// Record head flags.
enum steer_record_flag : uint32_t {
    STEERF_MOVE = 0x01,     // the follower movement hooks may steer (the session's guests-move gate)
    STEERF_LOGICAL = 0x02,  // co-op is logically on: controlled actors are kept from the native follower AI
};

// Entry flags.
enum steer_entry_flag : uint8_t {
    ENTRY_ACTIVE = 0x01,    // this seat steers this actor now
    ENTRY_MOVING = 0x02,    // the command is non-zero
};

struct steer_entry {
    uint64_t actor;            // compare only
    uint64_t phys;             // compare only
    uint64_t root;             // compare only (actor +0x10 when sampled)
    float heading;             // physics +0xA4 command
    float visual;              // the body's facing (actor +0x15C, physics +0xA8)
    float magnitude;           // physics +0xD0 command
    uint16_t member;
    uint8_t seat;              // 1..ACTIVE_SEATS-1
    uint8_t flags;             // steer_entry_flag
    uint32_t handle;
    uint32_t actor_generation;
};
static_assert(sizeof(steer_entry) == 48, "steer_entry layout (host tests read it)");

// The published steering record: head.epoch = the session epoch it was built for, head.flags = steer_record_flag,
// head.generation = the session generation.
struct steer_record {
    patch::record_head head;
    uint32_t active;           // bit k: slot k is ENTRY_ACTIVE
    uint32_t reserved;
    steer_entry slot[SLOTS];
};
static_assert(sizeof(steer_record) == 24 + 48 * SLOTS, "steer_record layout (host tests read it)");
static_assert(sizeof(steer_record) <= 128, "kept small: copied at mid-function detour sites");

// One seat's logical input this tick (from the action frame and the movement consumer's edge cursor).
struct seat_input {
    uint8_t present;           // an action frame exists for the seat
    uint8_t walk_toggle;       // WALK_RUN was pressed since the last tick (an edge, never an absorbed hold)
    uint8_t flee;              // FLEE is held
    uint8_t hold;              // something else owns the seat's character this tick (an interaction): no command
    uint16_t up, down, left, right;  // move action strengths, 0..65535
};
static_assert(sizeof(seat_input) == 12, "seat_input layout");

// Facts about a steered actor, read on the co-op thread for this tick.
struct actor_facts {
    uint8_t ok;                // the reads below succeeded and the actor's physics object is the session's
    uint8_t armed;             // fighting stance (actor +0x02 bit 7)
    uint8_t execution;         // an execution motion is playing (actor +0x00 bit 46)
    uint8_t has_target;        // target_heading is valid
    uint8_t headings_ok;       // phys_heading/phys_visual are finite (used when a seat starts steering)
    uint8_t reserved[3];
    uint64_t root;             // actor +0x10
    float target_heading;      // toward the execution target
    float phys_heading;        // physics +0xA4
    float phys_visual;         // physics +0xA8
    uint32_t reserved2;
};
static_assert(sizeof(actor_facts) == 32, "actor_facts layout");

struct plan_inputs {
    session::session_record session;
    seat_input input[SEATS];
    actor_facts actor[SEATS];
    uint8_t camera_ok;         // view[] was read
    uint8_t reserved[3];
    float view[16];
    uint64_t now_ms;
};

struct slot_state {
    uint64_t actor;            // the incarnation being steered (0 = none)
    uint64_t phys;
    uint32_t actor_generation;
    uint32_t world_epoch;
    double last_heading;
    double visual;
    uint8_t walk;              // walk mode (survives losing and regaining the actor, like the toggle it came from)
    uint8_t reserved[7];
};

struct planner_state {
    uint64_t last_ms;          // 0 = no tick yet
    basis camera;
    camera_filter filter;
    slot_state slot[SLOTS];
};

struct plan_outputs {
    steer_record record;
    uint32_t flee_seats;       // bit s: seat s asks for flee
    uint32_t started;          // bit k: slot k started steering a new incarnation this tick
    uint32_t waiting;          // bit k: slot k steers but its actor facts were missing (entry withheld)
    uint32_t camera_accepted;  // 1 when the camera basis came from this tick's matrix
};

void planner_reset(planner_state& st);
void plan(planner_state& st, const plan_inputs& in, plan_outputs& out);

// Lookups the detours use on their record copy. -1 = none.
int32_t slot_of_root(const steer_record& r, uint64_t root);
int32_t slot_of_phys(const steer_record& r, uint64_t phys);
int32_t slot_of_actor(const steer_record& r, uint64_t actor);

}  // namespace ilc::movement
