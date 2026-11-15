// Ivalice Legacy co-op module: target picker inputs from the running game (see live.h).
#include "targets/live.h"

#include <string.h>

#include "session/world.h"
#include "targets/targets_facts.h"

namespace ilc::targets {

namespace {

bool is_esper(uint16_t member) { return member >= facts::ESPER_FIRST && member <= facts::ESPER_LAST; }

bool read_layout(session::game_reader& r, uintptr_t battle, uint32_t which, uint16_t out[LAYOUT_ENTRIES])
{
    return r.read(battle + facts::LAYOUT_TABLE + which * facts::LAYOUT_STRIDE, out, LAYOUT_ENTRIES * 2);
}

// Status and HP of a record: the timed word read before and after the lasting word must agree (one coherent sample).
bool record_state(session::game_reader& r, uintptr_t actor, uint32_t* hp, uint32_t* status)
{
    uintptr_t battle = 0;
    return r.ptr(actor + facts::ACTOR_BATTLE, &battle) && battle != 0 && r.u32(battle + facts::BATTLE_HP, hp) &&
           session::battle_status(r, battle, status);
}

}  // namespace

bool reserve_from_layouts(const uint16_t* current, const uint16_t* def, const uint16_t* menu_layout,
                          uint16_t reserve_out[RESERVE_SLOTS], uint32_t* count_out)
{
    *count_out = 0;
    // Both complete layouts: unique members, the same set.
    uint32_t def_count = 0;
    for (uint32_t k = 0; k < LAYOUT_ENTRIES; ++k) {
        if (def[k] == facts::MEMBER_NONE) {
            continue;
        }
        ++def_count;
        for (uint32_t j = 0; j < k; ++j) {
            if (def[j] == def[k]) {
                return false;
            }
        }
        bool in_menu = false;
        for (uint32_t j = 0; j < LAYOUT_ENTRIES; ++j) {
            in_menu = in_menu || menu_layout[j] == def[k];
        }
        if (!in_menu) {
            return false;
        }
    }
    uint32_t menu_count = 0;
    for (uint32_t k = 0; k < LAYOUT_ENTRIES; ++k) {
        if (menu_layout[k] == facts::MEMBER_NONE) {
            continue;
        }
        ++menu_count;
        for (uint32_t j = 0; j < k; ++j) {
            if (menu_layout[j] == menu_layout[k]) {
                return false;
            }
        }
    }
    if (def_count == 0 || def_count != menu_count) {
        return false;
    }
    // Every active member (an Esper aside: it is never in the persistent roster) must be in it.
    for (uint32_t k = 0; k < 4; ++k) {
        const uint16_t m = current[k];
        if (m == facts::MEMBER_NONE || is_esper(m)) {
            continue;
        }
        bool found = false;
        for (uint32_t j = 0; j < LAYOUT_ENTRIES; ++j) {
            found = found || def[j] == m;
        }
        if (!found) {
            return false;
        }
    }
    for (uint32_t k = 4; k < LAYOUT_ENTRIES; ++k) {
        reserve_out[(*count_out)++] = menu_layout[k];
    }
    return true;
}

uintptr_t live_actor(session::game_reader& r, uint32_t handle, bool require_alive)
{
    if (handle == 0) {
        return 0;
    }
    uintptr_t manager = 0;
    uint32_t count = 0;
    uintptr_t entry = 0;
    uintptr_t actor = 0;
    uint32_t now = 0;
    const uint32_t index = handle & 0xFFFF;
    if (!r.ptr(r.global(facts::ACTOR_MANAGER_PTR), &manager) || manager == 0 ||
        !r.u32(manager + facts::MANAGER_COUNT, &count) || count > facts::MANAGER_COUNT_MAX || index >= count ||
        !r.ptr(manager + facts::MANAGER_ENTRIES + 8ull * index, &entry) || entry == 0 ||
        !r.ptr(entry + facts::ENTRY_ACTOR, &actor) || actor == 0 || !r.u32(actor + facts::ACTOR_HANDLE, &now) ||
        now != handle) {
        return 0;
    }
    if (require_alive) {
        uint32_t hp = 0;
        uint32_t status = 0;
        if (!record_state(r, actor, &hp, &status) || hp == 0 || (status & facts::ST_KO)) {
            return 0;
        }
    }
    return actor;
}

int32_t read_picker(session::game_reader& r, const session::session_record& rec, uint32_t seat, picker_input* out)
{
    memset(out, 0, sizeof(*out));
    if (seat >= session::SEATS) {
        return PICK_NO_ISSUER;
    }
    const session::seat_entry& me = rec.seat[seat];
    uint32_t handle_now = 0;
    float pos[3] = {};
    if ((me.flags & session::SEATF_ACTOR) == 0 || me.actor == 0 || me.handle == 0 ||
        !r.u32(me.actor + facts::ACTOR_HANDLE, &handle_now) || handle_now != me.handle ||
        !r.read(me.actor + facts::ACTOR_POSITION, pos, sizeof(pos))) {
        return PICK_NO_ISSUER;
    }
    out->issuer_x = pos[0];
    out->issuer_y = pos[1];
    out->issuer_z = pos[2];
    out->issuer_handle = me.handle;

    uintptr_t battle = 0;
    uint16_t current[LAYOUT_ENTRIES];
    uint16_t hud[LAYOUT_ENTRIES];
    if (!r.ptr(r.global(facts::BATTLE_STATE_PTR), &battle) || battle == 0 ||
        !read_layout(r, battle, facts::LAYOUT_CURRENT, current) || !read_layout(r, battle, facts::LAYOUT_HUD, hud)) {
        return PICK_NO_STATE;
    }

    // The active party, in HUD order (top row first); members the HUD does not list follow in slot order.
    session::party_actors bodies;
    session::scan_party(r, &bodies);
    bool used[PARTY_SLOTS] = {};
    uint32_t order[PARTY_SLOTS];
    uint32_t n = 0;
    for (uint32_t h = 0; h < PARTY_SLOTS; ++h) {
        for (uint32_t k = 0; k < PARTY_SLOTS; ++k) {
            if (!used[k] && hud[h] != facts::MEMBER_NONE && current[k] == hud[h]) {
                used[k] = true;
                order[n++] = k;
                break;
            }
        }
    }
    for (uint32_t k = 0; k < PARTY_SLOTS; ++k) {
        if (!used[k]) {
            order[n++] = k;
        }
    }
    for (uint32_t j = 0; j < n; ++j) {
        const uint32_t k = order[j];
        const session::party_actor& b = bodies.slot[k];
        if (b.actor == 0 || current[k] == facts::MEMBER_NONE) {
            continue;
        }
        party_member& p = out->party[out->party_count++];
        p.handle = b.handle;
        p.member = current[k];
        p.actor = b.actor;
        p.present = record_state(r, b.actor, &p.hp, &p.status) ? 1 : 0;
    }

    // The reserve, only while the native complete layouts agree.
    uint16_t def[LAYOUT_ENTRIES];
    uint16_t menu_layout[LAYOUT_ENTRIES];
    if (read_layout(r, battle, facts::LAYOUT_DEFAULT, def) && read_layout(r, battle, facts::LAYOUT_MENU, menu_layout)) {
        reserve_from_layouts(current, def, menu_layout, out->reserve, &out->reserve_count);
    }

    // World actors that could be Foes or Neutrals (the range is decided by the pure step).
    uintptr_t manager = 0;
    uint32_t count = 0;
    if (!r.ptr(r.global(facts::ACTOR_MANAGER_PTR), &manager) || manager == 0 ||
        !r.u32(manager + facts::MANAGER_COUNT, &count) || count > facts::MANAGER_COUNT_MAX) {
        return PICK_OK;  // the party is still useful without the world
    }
    for (uint32_t index = 0; index < count && out->actor_count < ACTOR_CAP; ++index) {
        uintptr_t entry = 0;
        uintptr_t actor = 0;
        if (!r.ptr(manager + facts::MANAGER_ENTRIES + 8ull * index, &entry) || entry == 0 ||
            !r.ptr(entry + facts::ENTRY_ACTOR, &actor) || actor == 0) {
            continue;
        }
        bool party = false;
        for (uint32_t k = 0; k < PARTY_SLOTS; ++k) {
            party = party || bodies.slot[k].actor == actor;
        }
        if (party || actor == me.actor) {
            continue;
        }
        uint64_t flags = 0;
        uintptr_t unit = 0;
        uint8_t type = 0;
        if (!r.u64(actor + facts::ACTOR_FLAGS, &flags) || !(flags & facts::ACTOR_ACTIVE) ||
            (flags & facts::ACTOR_HIDDEN) || !r.ptr(actor + facts::ACTOR_UNIT, &unit) || unit == 0 ||
            !r.u8(unit + facts::UNIT_TYPE, &type)) {
            continue;
        }
        type &= 0x0F;
        if (type != facts::UNIT_FOE && type != facts::UNIT_MIMIC && type != facts::UNIT_HUNT &&
            type != facts::UNIT_NEUTRAL) {
            continue;
        }
        actor_snapshot& a = out->actors[out->actor_count];
        memset(&a, 0, sizeof(a));
        a.actor = actor;
        a.unit_type = type;
        a.flags = SNAP_ACTIVE;
        uintptr_t ext = 0;
        uint8_t ext_flags = 0;
        uint32_t instance = 0;
        float where[3] = {};
        if (!r.u32(actor + facts::ACTOR_HANDLE, &a.handle) || !r.read(actor + facts::ACTOR_POSITION, where, 12) ||
            !r.ptr(actor + facts::ACTOR_BATTLE_EXT, &ext) || ext == 0 || !r.u8(ext, &ext_flags) ||
            !record_state(r, actor, &a.hp, &a.status)) {
            continue;
        }
        a.flags |= SNAP_RECORDS;
        if (ext_flags & facts::EXT_BLOCKED) {
            a.flags |= SNAP_BLOCKED;
        }
        a.x = where[0];
        a.y = where[1];
        a.z = where[2];
        uintptr_t name = 0;
        a.name = r.ptr(unit + facts::UNIT_NAME, &name) ? name : 0;
        a.instance = r.u32(unit + facts::UNIT_INSTANCE, &instance) ? static_cast<uint16_t>(instance & 0xFFFF) : 0;
        ++out->actor_count;
    }
    return PICK_OK;
}

}  // namespace ilc::targets
