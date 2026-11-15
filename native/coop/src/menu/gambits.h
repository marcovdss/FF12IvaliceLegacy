// Ivalice Legacy co-op module: a guest's Gambits on/off state (pure).
//
// The Gambits row toggles the guest character's gambits through the game's own query and setter (a game-thread
// operation built later; this file is its contract and the model's state). The model:
//  * mirrors the game: member record +0 bit 2 is read with every catalog refresh (`observe`), unless a toggle is in
//    flight;
//  * checks a request before it is queued: the identity the operation will prove again on the game thread
//    (a regular character, a full non-zero handle, live actor/physics/record pointers, the record being exactly the
//    party block's record for that member) and the statuses that make the game refuse (KO, Stone);
//  * records the operation's result with the codes it uses, and the sound to play (on 0x12, off 0x13; none when
//    refused).
#pragma once

#include <stdint.h>

namespace ilc::menu {

// The operation's result codes (kept from the native toggle it replaces, so logs read the same).
enum gambit_result : int32_t {
    GB_PENDING = 0,
    GB_APPLIED = 1,
    GB_STALE_SEQUENCE = 0x10,
    GB_STALE_IDENTITY = 0x11,     // the handle no longer resolves to this actor
    GB_STALE_PHYSICS = 0x12,
    GB_INVALID_MEMBER = 0x13,     // not a regular character
    GB_STALE_RECORD = 0x14,       // the actor's record is not the party block's record for the member
    GB_VERIFY_FAILED = 0x15,      // the setter ran but the query disagrees
    GB_BLOCKED_STATUS = 0x16,     // KO or Stone
    GB_BUSY = 0x20,               // a toggle is already in flight (model-side refusal)
    GB_UNKNOWN = 0x21,            // the current state has not been read yet (model-side refusal)
};

struct gambit_identity {
    uint32_t member;              // party id 0..5
    uint32_t handle;              // full 32-bit handle (registry, slot, generation)
    uint64_t actor;
    uint64_t physics;
    uint64_t record;              // actor +0x698
    uint64_t party_block;         // [0x2EBF190] when sampled
};
static_assert(sizeof(gambit_identity) == 40, "gambit_identity layout (host tests build it)");

struct gambit_state {
    uint8_t known;                // the game's value has been read
    uint8_t enabled;
    uint8_t in_flight;
    uint8_t desired;              // what the in-flight toggle asks for
    int32_t last_result;          // gambit_result
    uint32_t sound;               // 0, or the sound id to play for the last completed toggle
    uint32_t requests;
    uint32_t applied;
    uint32_t refused;
};
static_assert(sizeof(gambit_state) == 24, "gambit_state layout");

// Static identity proof (the game thread repeats it with live reads before calling the setter). GB_APPLIED = ok.
int32_t gambit_check_identity(const gambit_identity& id);

void gambit_observe(gambit_state* g, uint8_t record_state);
// Ask for the opposite of the current state. GB_PENDING = queued; anything else is the refusal.
int32_t gambit_request(gambit_state* g, const gambit_identity& id, uint32_t status);
// The operation finished: result code and the state the game reports afterwards (0/1).
void gambit_complete(gambit_state* g, int32_t result, uint32_t final_enabled);

}  // namespace ilc::menu
