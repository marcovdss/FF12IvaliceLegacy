// Ivalice Legacy co-op module: the shared camera (subsystem CAMERA).
//
// Co-op thread, once per beat after the session step:
//   * camera roles: P1 if alive, plus every guest under manual control whose actor is present and not down. The
//     shared camera runs with two or more roles in a valid world where the session allows the camera (GATE_WORLD,
//     GATE_CAMERA); otherwise every hook is stock (the game's own camera). Native menus, modals and towns keep it;
//     loads, cutscenes and a summon (the other actors dematerialise) drop it.
//   * re-acquire without a fly-in: after it turns on it waits for three samples whose positions are finite and whose
//     native focus (camera master +0xA0) is within 16 units of the party target, then resets the controller (a new
//     reset epoch) and seeds the focus at the group centre. A seat toggle (drop-in/out) while it stays on resets it
//     in place: a fresh controller seeded at the new party centre on the same step, records still published, no
//     re-acquire (the three-sample wait is for off-to-on only).
//   * the controller (camera_math.h) gives the focus and the extra boom; the collision limiter freezes the extra
//     against a native wall and steps back under pressure, and is off for 0.35 s after P1 orbits the camera
//     (yaw change >= 0.012 rad); the corridor capsules (every pair >= 6 apart, boom gate 8 on / 6 off) and the focus
//     escape go to the collision wrapper.
//   * publishes two records: the pose (focus, its velocity, the extra boom, a time stamp) for the mid-function hooks,
//     and the collision record (capsules, focus escape) for the call-row wrapper.
//
// Game threads (fail-open, CAMERA arm bit, record for the dispatcher's epoch, pose no older than 250 ms):
//   CM-01  0x32B265 controller focus store: [rdi+0x60..0x68] = the shared focus (predicted by its velocity for the
//          record's age, at most 50 ms), [rdi+0x6C] = 1.0; the original store is skipped.
//   CM-02..05  the four calls of the collision query 0x230C00 (call rows, call islands): the query runs; a first
//          hit inside a corridor capsule (or, with the focus escape, within its distance of the ray origin) is
//          internal and the query is repeated from just past it (at most 24 times, never past 98% of the ray); an
//          external hit is returned as is.
//   CM-06/07  0x32CD65 / 0x32CD79, the two boom multiplications of the primary rig (rsi == 0x2098060), only while
//          the rig is not back on a wall: the boom used is rig boom + extra. The rig itself is never written.
//
// Not done here (later tasks): the per-Present resampler (render hook) and the latch guard's rig write. The guard
// decision is computed and counted.
#pragma once

#include <stdint.h>

#include "camera/camera_math.h"
#include "patch/detour.h"

namespace ilc::camera {

constexpr uint64_t POSE_STALE_MS = 250;
constexpr double PREDICT_MAX_S = 0.050;
constexpr uint32_t MAX_RETRIES = 24;

enum record_flag : uint32_t {
    CAM_ACTIVE = 0x01,
};

// For the mid-function hooks: small (a body copies it).
struct pose_record {
    patch::record_head head;  // epoch = session epoch, flags = record_flag
    uint64_t stamp_ms;        // module clock at publication
    float focus[3];
    float velocity[3];        // focus velocity (units/s)
    float extra;              // added to the rig boom at CM-06/07
    uint32_t reset_epoch;
    uint32_t reserved[2];
};
static_assert(sizeof(pose_record) == 64, "pose_record layout (host tests read it)");

struct collision_record {
    patch::record_head head;
    float escape_max2;        // 0 = no focus escape
    uint32_t capsule_count;
    capsule capsules[MAX_CAPSULES];
};
static_assert(sizeof(collision_record) == 24 + 32 * MAX_CAPSULES, "collision_record layout");

// ---- co-op thread ---------------------------------------------------------------------------------------------
void start(uintptr_t image_base);
void step(uint64_t now_ms);
void stop();  // withdraws both records

// ---- any thread -----------------------------------------------------------------------------------------------
void request_reset();  // a seat toggle: reset the controller in place at the next step (a new reset epoch, focus snaps)

bool attach();  // boot: registers CM-01..07 and arms them

struct counters {
    volatile uint64_t steps;
    volatile uint64_t active_steps;
    volatile uint64_t resets;
    volatile uint64_t focus_written;     // CM-01
    volatile uint64_t zoom_applied;      // CM-06/07
    volatile uint64_t zoom_yielded;      // CM-06/07: back on a wall or not the primary rig
    volatile uint64_t queries;           // CM-02..05: query calls made by the wrapper
    volatile uint32_t hits[4];           // [0] internal, [1] retries, [2] exhausted, [3] external
    volatile uint64_t focus_internal;    // internal through the focus escape
    volatile uint64_t latch_activations; // the latch guard would have suppressed (not applied)
    volatile uint64_t stale;             // a hook found the pose older than POSE_STALE_MS
};
const counters* stats();

struct debug_view {
    uint32_t active;
    uint32_t pending_samples;
    uint32_t roles;
    uint32_t capsules;
    double extra;            // published
    double native_boom;
    double orbit_hold;
    controller ctl;
};
void test_debug(debug_view* out);

// ---- host tests ------------------------------------------------------------------------------------------------
patch::record_slot<pose_record>& pose_slot();
patch::record_slot<collision_record>& collision_slot();
void test_reset();
int32_t cm01(patch::ctx* c, uint32_t epoch);
int32_t collision(patch::ctx* c, uint32_t epoch);
int32_t cm06(patch::ctx* c, uint32_t epoch);
int32_t cm07(patch::ctx* c, uint32_t epoch);
void* cm07_thunk();

}  // namespace ilc::camera
