// Ivalice Legacy co-op module: the pure interaction rules (see rules.h).
#include "interaction/rules.h"

#include <string.h>

#include "interaction/interaction_facts.h"

namespace ilc::interaction {

bool press_allowed(uint64_t actor_flags, uint32_t actor_state, uint32_t actor_action)
{
    return (actor_flags & (facts::ACTOR_WEAPON_HANDLING | facts::ACTOR_EXECUTION)) == 0 && actor_state <= 1 &&
           (actor_action == facts::ACTION_NONE || actor_action == facts::ACTION_IDLE ||
            actor_action == facts::ACTION_FOLLOW);
}

bool leader_allows(uint64_t actor_flags)
{
    return (actor_flags & (facts::ACTOR_WEAPON_HANDLING | facts::ACTOR_EXECUTION)) == 0;
}

bool shop_open(uint32_t frontend_id, uint32_t frontend_active)
{
    return frontend_id == facts::FRONTEND_SHOP && frontend_active == 1;
}

void forced_descriptor(uint32_t handle, uint32_t state, uint32_t live_ready, uint32_t out[6])
{
    out[0] = facts::FORCED_DISTANCE_BITS;
    out[1] = (handle >> facts::HANDLE_REGISTRY_SHIFT) & 0x0F;
    out[2] = handle & facts::HANDLE_INDEX_MASK;
    out[3] = state;
    out[4] = handle;
    out[5] = live_ready | facts::READY_BITS;
}

void reset_descriptor(uint32_t out[6])
{
    out[0] = facts::RESET_DISTANCE_BITS;
    out[1] = 0xFFFFFFFFu;  // NOTADDR: no registry
    out[2] = 0xFFFFFFFFu;  // NOTADDR: no index
    out[3] = facts::TALK_STATE;
    out[4] = 0;
    out[5] = facts::READY_BITS;
}

uint32_t object_state(uint32_t published_state, bool sleeping_mimic)
{
    if (sleeping_mimic) {
        return facts::MIMIC_STATE;
    }
    return published_state >= facts::MINIMUM_STATE ? published_state : 0;
}

int32_t current_slot(const uint32_t state[8])
{
    for (int32_t k = 7; k >= 0; --k) {
        if (state[k] != 0) {
            return k;
        }
    }
    return -1;
}

int32_t target_ready(const target_view& t, uint32_t descriptor_state)
{
    if (!t.present) {
        return TARGET_GONE;
    }
    if (t.type == 1) {
        return TARGET_OK;
    }
    if (t.script == 0) {
        return TARGET_TORN;
    }
    const uint32_t flag = descriptor_state == facts::TALK_STATE ? facts::E_FLAG_TALK : facts::E_FLAG_OTHER;
    if ((t.flags & flag) == 0) {
        return TARGET_OK;  // no script request: nothing else is dereferenced
    }
    if (t.script_table == 0) {
        return TARGET_TORN;
    }
    if (t.script_context == 0xFF) {
        return TARGET_NO_CONTEXT;
    }
    return TARGET_OK;
}

void hold_begin(hold_state& h, uint32_t seat, uint32_t target_handle, uint32_t location, uint64_t now_ms)
{
    memset(&h, 0, sizeof(h));
    h.active = 1;
    h.seat = static_cast<uint8_t>(seat);
    h.location = location;
    h.target_handle = target_handle;
    h.start_ms = now_ms;
    h.last_signal_ms = now_ms;
}

uint32_t hold_step(hold_state& h, const hold_signals& s, uint64_t now_ms)
{
    if (!h.active) {
        return HOLD_DONE;
    }
    uint32_t end = HOLD_KEEP;
    const uint32_t none = 0xFFFFFFFFu;  // NOTADDR: unknown area
    if (!s.owner_ok) {
        end = HOLD_OWNER_GONE;
    } else if (!s.world_ok || (h.location != none && s.location != none && s.location != h.location)) {
        end = HOLD_WORLD_CHANGED;
    } else if (s.talking || s.menu_queued || s.frontend || s.modal) {
        h.opened = 1;
        h.last_signal_ms = now_ms;
        if (h.location == none) {
            h.location = s.location;
        }
    } else if (h.opened) {
        if (now_ms - h.last_signal_ms >= HOLD_RELEASE_MS) {
            end = HOLD_DONE;
        }
    } else if (now_ms - h.start_ms >= HOLD_OPEN_MS) {
        end = HOLD_NEVER_OPENED;
    }
    if (end != HOLD_KEEP) {
        memset(&h, 0, sizeof(h));
    }
    return end;
}

uint32_t hold_driver(const hold_state& h)
{
    return (h.active && h.opened) ? h.seat : 0u;
}

}  // namespace ilc::interaction
