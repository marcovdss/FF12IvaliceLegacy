// Ivalice Legacy co-op module: state shared by the co-op-thread and game-thread halves of src/submit (internal).
#pragma once

#include <stdint.h>

#include "submit/submit.h"

namespace ilc::submit::detail {

extern counters g_counters;
extern volatile uintptr_t g_image_base;
extern volatile int32_t g_enabled;        // attach checked the game functions and registered the operations
extern volatile int32_t g_forced;         // host tests: -1 = attach decides
extern volatile uint64_t g_node[SEATS];   // the indicator node an operation returned, per seat
extern volatile uint32_t g_owner;         // the summon owner (party id, OWNER_NONE)
extern const game_fns* g_test_fns;

void bump(volatile uint64_t& c);
uintptr_t base();
bool is_enabled();
uint64_t pack_issuer(uint32_t handle, uint32_t member, uint32_t action);
uint64_t pack_target(const action_request& rq);

}  // namespace ilc::submit::detail
