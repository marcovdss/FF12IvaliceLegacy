// Ivalice Legacy co-op module: reading a guest's target picker inputs from the running game (co-op thread).
//
// Every read goes through the session's fault-safe reader; a failed read leaves the actor out (never invents one).
// Nothing here writes game memory.
#pragma once

#include <stdint.h>

#include "session/reader.h"
#include "session/session.h"
#include "targets/targets.h"

namespace ilc::targets {

enum picker_status : int32_t {
    PICK_OK = 0,
    PICK_NO_ISSUER = -1,     // the seat has no bound character, or its actor no longer carries its handle
    PICK_NO_STATE = -2,      // the battle state or the actor manager is not there
};

// The issuer (the seat's character), the active party in HUD order, the reserve (when the native layouts agree)
// and every world actor that could be a Foe or a Neutral.
int32_t read_picker(session::game_reader& r, const session::session_record& rec, uint32_t seat, picker_input* out);

// The actor a handle names right now (the manager entry at its index, whose actor still carries the full handle),
// or 0. With require_alive, also HP > 0 and no KO bit.
uintptr_t live_actor(session::game_reader& r, uint32_t handle, bool require_alive);

// The complete roster from the native default and menu layouts (slots 4..8 = reserve), or false when they disagree
// with each other or with the active members (pure, exposed for tests).
// Each layout is LAYOUT_ENTRIES party ids.
bool reserve_from_layouts(const uint16_t* current, const uint16_t* def, const uint16_t* menu_layout,
                          uint16_t reserve_out[RESERVE_SLOTS], uint32_t* count_out);

}  // namespace ilc::targets
