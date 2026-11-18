// Ivalice Legacy co-op module: the steering remap that makes a seat change atomic.
//
// The steering record (movement/planner.h) is published by the co-op thread once per heartbeat. A seat change is
// made on the game thread, inside the UI tick, and the game applies a new leader at the end of that same frame.
// So that the guest's pad drives its new character from the very next frame, and never the character Player 1
// now drives, the game thread publishes a remap in the same callback: for each guest seat that changed hands, the
// steering entry it must use until the co-op thread has caught up (actor, physics, root of the new character,
// its own current heading and facing, no command: the body stands still for at most a heartbeat instead of
// snapping round or being dragged by the follower AI).
//
// The movement detours apply the remap on their record copy while it is pending. The co-op thread retires it once
// the session has adopted the change and the steering record it published names the new characters (or after
// REMAP_MAX_MS, or at an epoch change, which makes it inert anyway).
//
// Writers: one record_slot, written only on the game thread (the change operation and the leader watcher, both
// inside the UI tick); the retired sequence is written only by the co-op thread. Readers never wait.
#pragma once

#include <stdint.h>

#include "movement/planner.h"
#include "patch/detour.h"

namespace ilc::party {

constexpr uint64_t REMAP_MAX_MS = 250;  // a remap the co-op thread never confirmed is retired after this

struct remap_record {
    patch::record_head head;             // epoch = session epoch of the change, generation = session generation then
    uint32_t seq;                        // non-zero, grows with every publication
    uint32_t mask;                       // bit k: steering slot k is replaced by slot[k]
    uint64_t published_ms;
    movement::steer_entry slot[movement::SLOTS];
};
static_assert(sizeof(remap_record) == 32 + 48 * movement::SLOTS, "remap_record layout (host tests read it)");
static_assert(sizeof(remap_record) <= 256, "copied at mid-function detour sites (inline copy limit: movement/planner.h)");

// ---- game thread ----------------------------------------------------------------------------------------------
// Publishes a remap for the given slots (entries with ENTRY_ACTIVE steer; without it the slot steers nothing).
// seq: the change's sequence (the same number the session adoption carries), non-zero and growing.
void remap_publish(uint32_t seq, uint32_t epoch, uint64_t generation, uint32_t mask,
                   const movement::steer_entry* slots, uint64_t now_ms);

// ---- detours (any game thread, wait-free) ----------------------------------------------------------------------
// One atomic compare: is a remap waiting to be retired?
bool remap_pending();
// Applies a pending remap of the record's epoch to the copy; false when nothing applies. Any slot not in the remap
// that still names one of the remapped actors stops steering (one member, one seat).
bool remap_apply(movement::steer_record& r);

// ---- co-op thread ----------------------------------------------------------------------------------------------
bool remap_read(remap_record* out);
void remap_retire(uint32_t seq);
uint32_t remap_retired();

void remap_test_reset();

}  // namespace ilc::party
