// Ivalice Legacy co-op module: one pass of world reads (see world.h).
#include "session/world.h"

#include <string.h>

namespace ilc::session {
namespace {

using namespace facts;

bool finite_coordinates(const float* v)
{
    for (int i = 0; i < 3; ++i) {
        const float x = v[i];
        // NaN fails both comparisons; infinities and absurd magnitudes fail the bound.
        if (!(x > -1.0e8f && x < 1.0e8f)) {
            return false;
        }
    }
    return true;
}

// The modal check never makes the pass fail: an unreadable UI manager just means "no modal".
bool modal_active(game_reader& r)
{
    uintptr_t manager = 0;
    if (!r.ptr(r.global(UI_MANAGER_PTR), &manager) || manager == 0) {
        return false;
    }
    uint32_t manager_flags = 0;
    uintptr_t object = 0;
    uint16_t object_flags = 0;
    if (!r.u32(manager + UI_MODAL_FLAGS, &manager_flags) || !r.ptr(manager + UI_MODAL_OBJECT, &object) ||
        object == 0 || !r.u16(object + UI_MODAL_OBJECT_FLAGS, &object_flags)) {
        return false;
    }
    return (manager_flags & UI_MODAL_MANAGER_BIT) != 0 && (object_flags & UI_MODAL_OBJECT_BIT) != 0;
}

// Area metadata: false when the table is absent or the id is out of range; a failed read fails the pass.
bool area_is_town(game_reader& r, uint32_t location, bool* ok)
{
    uintptr_t table = 0;
    if (!r.ptr(r.global(AREA_TABLE_PTR), &table)) {
        *ok = false;
        return false;
    }
    if (table == 0) {
        return false;
    }
    uint32_t count = 0;
    if (!r.u32(table + AREA_TABLE_COUNT, &count)) {
        *ok = false;
        return false;
    }
    if (location >= count) {
        return false;
    }
    uint8_t entry = 0;
    if (!r.u8(table + AREA_TABLE_ENTRIES + static_cast<uintptr_t>(location) * AREA_ENTRY_SIZE, &entry)) {
        *ok = false;
        return false;
    }
    return (entry & 1u) != 0;
}

void clear_world(world_sample* out)
{
    out->roots = 0;
    out->control_enabled = 0;
    out->town = 0;
    out->chocobo = 0;
    out->location_read = 0;
    out->leader = MEMBER_NONE;
    out->battle_state = 0;
    for (uint32_t k = 0; k < LAYOUT_ENTRIES; ++k) {
        out->current[k] = MEMBER_NONE;
        out->ui[k] = MEMBER_NONE;
    }
    out->location_current = LOCATION_NONE;
    out->location_ui = LOCATION_NONE;
}

}  // namespace

void sample_world(game_reader& r, world_sample* out)
{
    memset(out, 0, sizeof(*out));
    clear_world(out);

    // The command menu read decides whether the pass can go on at all; the modal check never fails it.
    uint32_t command_actor = 0;
    uint32_t command_flag = 0;
    if (!r.u32(r.global(COMMAND_ACTOR), &command_actor) || !r.u32(r.global(COMMAND_FLAG), &command_flag)) {
        return;
    }
    out->menu_open = (command_actor != 0 && command_flag == 1) ? 1 : 0;
    out->modal_active = modal_active(r) ? 1 : 0;

    uintptr_t battle = 0;
    uintptr_t location = 0;
    uintptr_t control = 0;
    if (!r.ptr(r.global(BATTLE_STATE_PTR), &battle) || !r.ptr(r.global(LOCATION_PTR), &location) ||
        !r.ptr(r.global(CONTROL_PTR), &control) || battle == 0 || location == 0 || control == 0) {
        return;
    }
    uint32_t mode = 0;
    const bool chocobo = r.u32(battle + MODE_BITS, &mode) && (mode & MODE_CHOCOBO) != 0;

    world_sample s = *out;
    bool ok = r.read(battle + LAYOUT_TABLE + LAYOUT_CURRENT * LAYOUT_STRIDE, s.current, sizeof(s.current)) &&
              r.read(battle + LAYOUT_TABLE + LAYOUT_UI * LAYOUT_STRIDE, s.ui, sizeof(s.ui)) &&
              r.u32(battle + LEADER_MEMBER, &s.leader) && r.u32(location + LOCATION_CURRENT, &s.location_current) &&
              r.u32(location + LOCATION_UI, &s.location_ui) && r.u8(control + CONTROL_ENABLED, &s.control_enabled);
    uint8_t flags = 0;
    ok = ok && r.u8(location + LOCATION_FLAGS, &flags);
    if (!ok) {
        return;
    }
    bool town = (flags & LOCATION_TOWN) != 0;
    if (!town) {
        town = area_is_town(r, s.location_current, &ok);
        if (!ok) {
            return;
        }
    }
    s.roots = 1;
    s.control_enabled = s.control_enabled != 0 ? 1 : 0;
    s.town = town ? 1 : 0;
    s.chocobo = chocobo ? 1 : 0;
    s.location_read = 1;
    s.battle_state = battle;
    *out = s;
}

void scan_party(game_reader& r, party_actors* out)
{
    memset(out, 0, sizeof(*out));
    uintptr_t manager = 0;
    uint32_t count = 0;
    if (!r.ptr(r.global(ACTOR_MANAGER_PTR), &manager) || manager == 0 || !r.u32(manager + ACTOR_COUNT, &count) ||
        count == 0 || count > ACTOR_COUNT_MAX) {
        return;
    }
    out->manager_ok = 1;
    uint32_t claims[4] = {};
    for (uint32_t index = 0; index < count; ++index) {
        ++out->scanned;
        uintptr_t entry = 0;
        uint8_t slot = 0xFF;
        // The party slot first: it rules out every non-party actor with one read.
        if (!r.ptr(manager + ACTOR_ENTRIES + static_cast<uintptr_t>(index) * 8, &entry) || entry == 0 ||
            !r.u8(entry + ENTRY_PARTY_SLOT, &slot) || slot > 3) {
            continue;
        }
        uintptr_t actor = 0;
        uintptr_t root = 0;
        uintptr_t phys = 0;
        uintptr_t battle = 0;
        uint32_t handle = 0;
        float actor_pos[3];
        float phys_pos[3];
        if (!r.ptr(entry + ENTRY_ACTOR, &actor) || actor == 0 || !r.ptr(actor + ACTOR_ROOT, &root) || root == 0 ||
            !r.ptr(root + ROOT_PHYSICS, &phys) || phys == 0 || !r.read(actor + ACTOR_POSITION, actor_pos, 12) ||
            !r.read(phys, phys_pos, 12) || !r.u32(actor + ACTOR_HANDLE, &handle) ||
            !r.ptr(actor + ACTOR_BATTLE, &battle)) {
            continue;
        }
        // The body and its physics object must be the same thing in the same place (a stale pair drifts apart).
        if (!finite_coordinates(actor_pos) || !finite_coordinates(phys_pos)) {
            continue;
        }
        float d2 = 0.0f;
        for (int k = 0; k < 3; ++k) {
            const float d = actor_pos[k] - phys_pos[k];
            d2 += d * d;
        }
        if (d2 > 1.0f || handle == 0 || (handle & HANDLE_INDEX_MASK) != index || battle == 0) {
            continue;
        }
        ++claims[slot];
        out->slot[slot] = {index, handle, actor, phys};
    }
    // Two actors claiming one slot is a rebuild in progress: neither is trusted.
    for (uint32_t slot = 0; slot < 4; ++slot) {
        if (claims[slot] != 1) {
            out->slot[slot] = {0, 0, 0, 0};
        } else {
            ++out->found;
        }
    }
}

bool party_slot_holds(game_reader& r, uint32_t slot, const party_actor& cached)
{
    uintptr_t manager = 0;
    uintptr_t entry = 0;
    uintptr_t actor = 0;
    uint32_t count = 0;
    uint32_t handle = 0;
    uint8_t entry_slot = 0xFF;
    if (cached.actor == 0 || !r.ptr(r.global(ACTOR_MANAGER_PTR), &manager) || manager == 0 ||
        !r.u32(manager + ACTOR_COUNT, &count) || cached.index >= count || count > ACTOR_COUNT_MAX ||
        !r.ptr(manager + ACTOR_ENTRIES + static_cast<uintptr_t>(cached.index) * 8, &entry) || entry == 0 ||
        !r.ptr(entry + ENTRY_ACTOR, &actor) || !r.u8(entry + ENTRY_PARTY_SLOT, &entry_slot) ||
        !r.u32(cached.actor + ACTOR_HANDLE, &handle)) {
        return false;
    }
    return actor == cached.actor && handle == cached.handle && entry_slot == slot &&
           actor_physics(r, cached.actor) == cached.phys;
}

tri actor_in_party(game_reader& r, uintptr_t actor)
{
    if (actor == 0) {
        return T_NO;
    }
    uintptr_t manager = 0;
    uint32_t count = 0;
    if (!r.ptr(r.global(ACTOR_MANAGER_PTR), &manager) || manager == 0 || !r.u32(manager + ACTOR_COUNT, &count) ||
        count == 0 || count > ACTOR_COUNT_MAX) {
        return T_UNKNOWN;
    }
    bool listed = false;
    bool gap = false;
    for (uint32_t index = 0; index < count; ++index) {
        uintptr_t entry = 0;
        uintptr_t listed_actor = 0;
        if (!r.ptr(manager + ACTOR_ENTRIES + static_cast<uintptr_t>(index) * 8, &entry)) {
            if (listed) {
                return T_UNKNOWN;
            }
            gap = true;
            continue;
        }
        if (entry == 0) {
            continue;
        }
        if (!r.ptr(entry + ENTRY_ACTOR, &listed_actor)) {
            if (listed) {
                return T_UNKNOWN;
            }
            gap = true;
            continue;
        }
        if (listed_actor != actor) {
            continue;
        }
        listed = true;
        uint32_t handle = 0;
        uint8_t slot = 0xFF;
        if (!r.u32(actor + ACTOR_HANDLE, &handle) || !r.u8(entry + ENTRY_PARTY_SLOT, &slot)) {
            return T_UNKNOWN;
        }
        if (handle == 0 || (handle & HANDLE_INDEX_MASK) != index) {
            continue;  // an alias left behind by a rebuild: this entry does not vouch for the actor
        }
        if (slot > 3) {
            return T_NO;
        }
        uintptr_t root = 0;
        uintptr_t phys = 0;
        uintptr_t battle = 0;
        if (!r.ptr(actor + ACTOR_ROOT, &root) || root == 0 || !r.ptr(root + ROOT_PHYSICS, &phys) || phys == 0 ||
            !r.ptr(actor + ACTOR_BATTLE, &battle) || battle == 0) {
            return T_UNKNOWN;
        }
        return T_YES;
    }
    return gap ? T_UNKNOWN : T_NO;
}

uintptr_t actor_physics(game_reader& r, uintptr_t actor)
{
    uintptr_t root = 0;
    uintptr_t phys = 0;
    if (actor == 0 || !r.ptr(actor + ACTOR_ROOT, &root) || root == 0 || !r.ptr(root + ROOT_PHYSICS, &phys)) {
        return 0;
    }
    return phys;
}

bool battle_status(game_reader& r, uintptr_t battle, uint32_t* status_out)
{
    if (battle == 0) {
        return false;
    }
    for (uint32_t attempt = 0; attempt < STATUS_SAMPLE_TRIES; ++attempt) {
        uint32_t before = 0;
        uint32_t lasting = 0;
        uint32_t after = 0;
        if (!r.u32(battle + BATTLE_STATUS_TIMED, &before) || !r.u32(battle + BATTLE_STATUS_LASTING, &lasting) ||
            !r.u32(battle + BATTLE_STATUS_TIMED, &after)) {
            continue;
        }
        if (before == after) {
            *status_out = before | lasting;
            return true;
        }
    }
    return false;
}

namespace {

bool status_of(game_reader& r, uintptr_t actor, uintptr_t* battle_out, uint32_t* status_out)
{
    uintptr_t battle = 0;
    if (actor == 0 || !r.ptr(actor + ACTOR_BATTLE, &battle) || battle == 0) {
        return false;
    }
    *battle_out = battle;
    return battle_status(r, battle, status_out);
}

}  // namespace

tri actor_alive(game_reader& r, uintptr_t actor)
{
    uintptr_t battle = 0;
    uint32_t hp = 0;
    if (actor == 0 || !r.ptr(actor + ACTOR_BATTLE, &battle) || battle == 0 || !r.u32(battle + BATTLE_HP, &hp)) {
        return T_UNKNOWN;
    }
    uint32_t status = 0;
    if (!battle_status(r, battle, &status)) {
        return T_UNKNOWN;
    }
    return tri_of(hp != 0 && (status & ST_KO) == 0);
}

tri actor_walks(game_reader& r, uintptr_t actor)
{
    uintptr_t battle = 0;
    uint32_t status = 0;
    if (!status_of(r, actor, &battle, &status)) {
        return T_UNKNOWN;
    }
    return tri_of((status & ST_NO_WALK) == 0);
}

tri actor_command_blocked(game_reader& r, uintptr_t actor)
{
    uintptr_t battle = 0;
    uint32_t status = 0;
    if (!status_of(r, actor, &battle, &status)) {
        return T_UNKNOWN;
    }
    return tri_of((status & ST_NO_COMMAND) != 0);
}

tri actor_berserk(game_reader& r, uintptr_t actor)
{
    uintptr_t battle = 0;
    uint32_t status = 0;
    if (!status_of(r, actor, &battle, &status)) {
        return T_UNKNOWN;
    }
    return tri_of((status & ST_BERSERK) != 0);
}

tri actor_leads(game_reader& r, uintptr_t actor, bool berserk_steerable)
{
    if (actor == 0) {
        return T_UNKNOWN;
    }
    const uint32_t blocking = berserk_steerable ? (ST_NO_COMMAND & ~ST_BERSERK) : ST_NO_COMMAND;
    // HP, status and the battle record pointer are published separately: an old alive sample next to a new
    // clear status would be a state that never existed. The status sample is bracketed by HP and the pointer,
    // both read before and after; any change retries, and an actor that never holds still stays UNKNOWN.
    for (uint32_t attempt = 0; attempt < STATUS_SAMPLE_TRIES; ++attempt) {
        uintptr_t battle_before = 0;
        uintptr_t battle_after = 0;
        uint32_t hp_before = 0;
        uint32_t hp_after = 0;
        uint32_t status = 0;
        if (!r.ptr(actor + ACTOR_BATTLE, &battle_before)) {
            continue;
        }
        if (battle_before == 0) {
            return T_UNKNOWN;
        }
        if (!r.u32(battle_before + BATTLE_HP, &hp_before) || !battle_status(r, battle_before, &status) ||
            !r.u32(battle_before + BATTLE_HP, &hp_after) || !r.ptr(actor + ACTOR_BATTLE, &battle_after)) {
            continue;
        }
        if (battle_before == battle_after && hp_before == hp_after) {
            return tri_of(hp_before != 0 && (status & blocking) == 0);
        }
    }
    return T_UNKNOWN;
}

}  // namespace ilc::session
