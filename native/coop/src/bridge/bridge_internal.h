// Ivalice Legacy co-op module: bridge functions for the module's own code (not exported).
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"

namespace ilc {

// The settings safe point. The module's game-thread tick will call this once per tick: it moves staged
// settings into the applied slot. It never blocks: if a bridge call holds the lock it returns
// ILC_SAFE_POINT_BUSY and the next tick tries again. Returns an ilc_safe_point_outcome. When it returns
// ILC_SAFE_POINT_APPLIED and `applied_subsystems` is not null, the applied record's subsystems_armed mask is
// stored there (read under the same try-lock), for the patch layer's arm gates; when `applied_copy` is not null,
// the whole applied record is copied there too (for the input service).
uint32_t bridge_safe_point(uint32_t* applied_subsystems = nullptr, ilc_settings* applied_copy = nullptr);

// The applied settings record (generation 0 = native defaults), copied under the bridge lock. For the boot thread,
// never for the game thread (it waits for the lock).
void bridge_copy_applied(ilc_settings* out);

// Called once by the tick when it starts owning the safe point. From then on the debug op
// ILC_OP_TEST_SAFE_POINT answers ILC_E_NOT_AVAILABLE, so no script thread can apply settings.
void bridge_attach_tick();

}  // namespace ilc
