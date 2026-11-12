// Ivalice Legacy co-op module: session rules (see policy.h).
#include "session/policy.h"

#include <string.h>

namespace ilc::session {
namespace {

using namespace facts;

constexpr uint32_t LEADER_NONE = MEMBER_NONE;

bool in_session(const identity& id, uint32_t seat)
{
    return seat < ACTIVE_SEATS && (seat == 0 || (id.in_session & (1u << seat)) != 0);
}

// Up to four entries; `n` is how many are filled.
struct member_list {
    uint16_t m[4];
    uint32_t n;
    bool has(uint32_t member) const
    {
        for (uint32_t i = 0; i < n; ++i) {
            if (m[i] == member) {
                return true;
            }
        }
        return false;
    }
    void add(uint16_t member) { m[n++] = member; }
};

member_list occupied_of(const uint16_t* layout)
{
    member_list out = {};
    for (uint32_t k = 0; k < 4; ++k) {
        if (layout[k] != MEMBER_NONE) {
            out.add(layout[k]);
        }
    }
    return out;
}

bool contains(const uint16_t* layout, uint32_t member)
{
    if (member == MEMBER_NONE) {
        return false;
    }
    for (uint32_t k = 0; k < 4; ++k) {
        if (layout[k] == member) {
            return true;
        }
    }
    return false;
}

bool has_duplicates(const member_list& l)
{
    for (uint32_t i = 0; i < l.n; ++i) {
        for (uint32_t j = i + 1; j < l.n; ++j) {
            if (l.m[i] == l.m[j]) {
                return true;
            }
        }
    }
    return false;
}

bool same_set(const member_list& a, const member_list& b)
{
    for (uint32_t i = 0; i < a.n; ++i) {
        if (!b.has(a.m[i])) {
            return false;
        }
    }
    for (uint32_t i = 0; i < b.n; ++i) {
        if (!a.has(b.m[i])) {
            return false;
        }
    }
    return true;
}

}  // namespace

bool same_identity(const identity& a, const identity& b)
{
    if (a.in_session != b.in_session) {
        return false;
    }
    for (uint32_t s = 0; s < SEATS; ++s) {
        if (a.member[s] != b.member[s]) {
            return false;
        }
    }
    return true;
}

identity make_identity(uint16_t p1, const uint16_t* guests, uint32_t guest_count, uint8_t in_session_bits)
{
    identity id;
    memset(&id, 0, sizeof(id));
    for (uint32_t s = 0; s < SEATS; ++s) {
        id.member[s] = MEMBER_NONE;
    }
    id.member[0] = p1;
    id.in_session = static_cast<uint8_t>((in_session_bits | 1u) & ((1u << ACTIVE_SEATS) - 1u));
    for (uint32_t g = 0; g < guest_count && g + 1 < ACTIVE_SEATS; ++g) {
        if (id.in_session & (1u << (g + 1))) {
            id.member[g + 1] = guests[g];
        }
    }
    return id;
}

bool member_in_layout(uint16_t member, const uint16_t* layout)
{
    return contains(layout, member);
}

bool member_in_both(uint16_t member, const uint16_t* current, const uint16_t* ui)
{
    return contains(current, member) && contains(ui, member);
}

uint16_t p1_member_now(const identity& id, const uint16_t* current, const uint16_t* ui, uint32_t leader)
{
    if (is_named(leader) && !is_esper(leader) && contains(current, leader) && contains(ui, leader)) {
        if (leader == id.member[0]) {
            return static_cast<uint16_t>(leader);
        }
        bool guest_owned = false;
        for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
            guest_owned = guest_owned || (in_session(id, s) && id.member[s] == leader);
        }
        if (!guest_owned) {
            return static_cast<uint16_t>(leader);
        }
    }
    return id.member[0];
}

bool resolve_identity(const identity& configured, const uint16_t* current, const uint16_t* ui, uint32_t leader,
                      identity* out)
{
    member_list party = {};
    for (uint32_t k = 0; k < 4; ++k) {
        const uint16_t m = current[k];
        if (is_regular(m) && contains(ui, m) && !party.has(m)) {
            party.add(m);
        }
    }
    if (party.n == 0) {
        return false;
    }
    uint16_t p1 = party.has(leader) ? static_cast<uint16_t>(leader) : configured.member[0];
    if (!party.has(p1)) {
        p1 = party.m[0];
    }
    identity next = configured;
    for (uint32_t s = 0; s < SEATS; ++s) {
        next.member[s] = MEMBER_NONE;
    }
    next.member[0] = p1;
    member_list taken = {};
    taken.add(p1);

    // Seats are kept where they were: a leader change onto a guest's character swaps exactly those two seats and
    // never rotates the others.
    int32_t displaced = -1;
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        if (in_session(configured, s) && configured.member[s] == p1 && displaced < 0) {
            displaced = static_cast<int32_t>(s);
        }
    }
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        const uint16_t m = configured.member[s];
        if (in_session(configured, s) && party.has(m) && !taken.has(m) && taken.n < 4) {
            next.member[s] = m;
            taken.add(m);
        }
    }
    if (displaced > 0) {
        const uint16_t former = configured.member[0];
        if (party.has(former) && !taken.has(former) && taken.n < 4) {
            next.member[displaced] = former;
            taken.add(former);
        }
    }
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        if (!in_session(configured, s) || next.member[s] != MEMBER_NONE) {
            continue;
        }
        for (uint32_t k = 0; k < party.n; ++k) {
            if (!taken.has(party.m[k]) && taken.n < 4) {
                next.member[s] = party.m[k];
                taken.add(party.m[k]);
                break;
            }
        }
    }
    *out = next;
    return true;
}

bool identity_from_party(const identity& current_identity, const world_sample& w, identity* out)
{
    // A summon dematerializes the other characters for a while: never a party edit.
    if (summoned_esper(w.current, w.ui) != MEMBER_NONE) {
        return false;
    }
    const member_list cur = occupied_of(w.current);
    const member_list ui = occupied_of(w.ui);
    // Story scripts publish temporary rosters; only a settled party where both layouts agree becomes identity.
    if (!w.roots || w.battle_state == 0 || !w.control_enabled || w.menu_open || w.modal_active || !w.location_read ||
        w.location_current == LOCATION_NONE || w.location_current != w.location_ui || cur.n == 0 ||
        has_duplicates(cur) || has_duplicates(ui) || !same_set(cur, ui) ||
        (w.leader != LEADER_NONE && !is_regular(w.leader))) {
        return false;
    }
    identity next;
    if (!resolve_identity(current_identity, w.current, w.ui, w.leader, &next) ||
        same_identity(next, current_identity)) {
        return false;
    }
    *out = next;
    return true;
}

uint16_t summoned_esper(const uint16_t* current, const uint16_t* ui)
{
    member_list a = {};
    member_list b = {};
    for (uint32_t k = 0; k < 4; ++k) {
        if (is_esper(current[k]) && !a.has(current[k])) {
            a.add(current[k]);
        }
        if (is_esper(ui[k]) && !b.has(ui[k])) {
            b.add(ui[k]);
        }
    }
    if (a.n != 1 || !same_set(a, b)) {
        return MEMBER_NONE;
    }
    return a.m[0];
}

int8_t summon_owner_seat(const identity& id, const uint16_t* current, const uint16_t* ui, uint32_t leader)
{
    if (summoned_esper(current, ui) == MEMBER_NONE) {
        return NO_SEAT;
    }
    // Candidates: regulars in both layouts that a seat controls.
    uint32_t candidates = 0;
    uint16_t only = MEMBER_NONE;
    bool leader_candidate = false;
    member_list seen = {};
    for (uint32_t s = 0; s < ACTIVE_SEATS; ++s) {
        const uint16_t m = id.member[s];
        if (!in_session(id, s) || !is_regular(m) || !member_in_both(m, current, ui) || seen.has(m)) {
            continue;
        }
        seen.add(m);
        ++candidates;
        only = m;
        leader_candidate = leader_candidate || m == leader;
    }
    uint16_t owner = MEMBER_NONE;
    if (leader_candidate) {
        owner = static_cast<uint16_t>(leader);
    } else if (candidates == 1) {
        owner = only;
    }
    if (owner == MEMBER_NONE) {
        return NO_SEAT;
    }
    for (uint32_t s = 0; s < ACTIVE_SEATS; ++s) {
        if (in_session(id, s) && id.member[s] == owner) {
            return static_cast<int8_t>(s);
        }
    }
    return NO_SEAT;
}

int8_t ko_promoted_seat(const identity& id, uint32_t leader, tri p1_alive, bool p1_present)
{
    if (!p1_present || p1_alive == T_YES) {
        return NO_SEAT;
    }
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        if (in_session(id, s) && id.member[s] != MEMBER_NONE && leader == id.member[s]) {
            return static_cast<int8_t>(s);
        }
    }
    return NO_SEAT;
}

void evaluate_world(const identity& id, const world_sample& w, int32_t materialized, world_view* out)
{
    memset(out, 0, sizeof(*out));
    out->summon_seat = NO_SEAT;
    out->leader_seat = NO_SEAT;
    out->location = LOCATION_NONE;
    uint8_t reason = WR_UNAVAILABLE;
    bool p1_structural = false;
    bool structural = false;
    bool summon_structural = false;
    int8_t summon_seat = NO_SEAT;
    const uint16_t p1_now = p1_member_now(id, w.current, w.ui, w.leader);

    if (w.roots) {
        const bool p1_present = member_in_both(p1_now, w.current, w.ui);
        uint32_t guests = 0;
        bool guests_present = true;
        for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
            const uint16_t m = id.member[s];
            if (!in_session(id, s) || m == MEMBER_NONE) {
                continue;
            }
            ++guests;
            guests_present = guests_present && member_in_both(m, w.current, w.ui);
        }
        const bool roles_present = guests > 0 && p1_present && guests_present;
        const bool p1_actor = materialized < 0 || materialized >= 1;
        const bool actors_present = materialized < 0 || materialized >= static_cast<int32_t>(1 + guests);
        const bool location_coherent = w.location_read && w.location_current != LOCATION_NONE &&
                                       w.location_ui != LOCATION_NONE && w.location_current == w.location_ui;
        summon_seat = summon_owner_seat(id, w.current, w.ui, w.leader);
        if (summon_seat != NO_SEAT) {
            const uint16_t owner = id.member[static_cast<uint32_t>(summon_seat)];
            summon_structural = member_in_both(owner, w.current, w.ui) && p1_actor && location_coherent;
        }
        p1_structural = p1_present && p1_actor && location_coherent;
        structural = (p1_structural && roles_present && actors_present) || summon_structural;
        if (summon_structural) {
            reason = WR_SUMMON;
        } else if (!p1_present) {
            reason = WR_P1_ABSENT;
        } else if (!p1_actor) {
            reason = WR_P1_NOT_MATERIALIZED;
        } else if (!location_coherent) {
            reason = WR_LOCATION_SETTLING;
        } else if (!roles_present || !actors_present) {
            reason = WR_NO_GUEST;
        } else {
            reason = WR_FIELD;
        }
    }

    const bool loading = !(p1_structural || summon_structural);
    const bool cutscene = p1_structural && !w.control_enabled && !w.menu_open && !w.modal_active;
    const bool p1_gameplay = (p1_structural || summon_seat == 0) && !loading;
    const bool gameplay = structural && !loading;
    const bool p1_is_leader = w.leader != LEADER_NONE && w.leader == p1_now;
    if (!loading) {
        if (w.menu_open) {
            reason = WR_MENU;
        } else if (w.modal_active) {
            reason = WR_MODAL;
        } else if (w.town) {
            reason = WR_TOWN;
        } else if (w.chocobo) {
            reason = WR_CHOCOBO;
        } else if (cutscene) {
            reason = WR_CUTSCENE;
        } else if (summon_seat != NO_SEAT) {
            reason = WR_SUMMON;
        } else if (!p1_is_leader) {
            reason = WR_P1_NOT_LEADER;
        }
    }
    const bool menus_open = w.control_enabled && !w.menu_open && !w.modal_active && !w.town && !w.chocobo;
    out->loading = loading;
    out->cutscene = cutscene;
    out->p1_gameplay = p1_gameplay;
    out->gameplay = gameplay;
    out->p1_is_leader = p1_is_leader;
    out->p1_menu = p1_gameplay && menus_open && p1_is_leader && (summon_seat == NO_SEAT || summon_seat == 0);
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        out->guest_menu[s] = gameplay && in_session(id, s) && id.member[s] != MEMBER_NONE && menus_open &&
                             (summon_seat == NO_SEAT || summon_seat == static_cast<int8_t>(s));
    }
    out->movement = gameplay && w.control_enabled && !w.menu_open && !w.modal_active;
    out->camera = gameplay && !cutscene;
    out->summon_seat = summon_seat;
    if (p1_is_leader) {
        out->leader_seat = 0;
    } else {
        for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
            if (in_session(id, s) && id.member[s] != MEMBER_NONE && w.leader == id.member[s]) {
                out->leader_seat = static_cast<int8_t>(s);
                break;
            }
        }
    }
    out->reason = reason;
    out->p1_member = p1_now;
    // The world signature: the battle state and a settled area. It feeds the world epoch (session.h).
    if (w.roots && w.battle_state != 0 && w.location_read && w.location_current != LOCATION_NONE &&
        w.location_current == w.location_ui) {
        out->signature_valid = 1;
        out->location = w.location_current;
        out->battle_state = w.battle_state;
    }
}

bool world_valid(const world_view& v)
{
    return v.signature_valid && !v.loading && !v.cutscene;
}

bool guest_may_move(const world_view& v, const identity& id, const world_sample& w, uint32_t present_seats)
{
    if (v.movement) {
        return true;
    }
    bool ready = false;
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        if ((present_seats & (1u << s)) && member_in_both(id.member[s], w.current, w.ui)) {
            ready = true;
        }
    }
    return v.p1_gameplay && w.control_enabled && !w.menu_open && !w.modal_active && !v.loading && !v.cutscene &&
           ready;
}

bool p1_may_move(const world_view& v, const world_sample& w)
{
    return v.p1_gameplay && w.control_enabled && !w.menu_open && !w.modal_active && !v.loading && !v.cutscene;
}

lifecycle lifecycle_gates(bool enabled, tri p1_alive, const tri* guest_alive, const bool* guest_present,
                          uint32_t guests)
{
    uint32_t available = 0;
    for (uint32_t g = 0; g < guests && g + 1 < ACTIVE_SEATS; ++g) {
        if (guest_present[g] && guest_alive[g] != T_NO) {
            ++available;
        }
    }
    const uint32_t roles = (p1_alive == T_YES ? 1u : 0u) + available;
    lifecycle out = {};
    out.logical = enabled;
    out.movement = enabled && available > 0;
    out.shared_camera = enabled && roles >= 2;
    return out;
}

bool seat_steers(const steer_facts& f)
{
    return f.enabled && f.actor_present && f.identity_current && f.layout_coherent && f.alive != T_NO &&
           f.member != MEMBER_NONE && (f.leader != f.member || f.allow_leader);
}

bool keep_through_mapping_gap(bool mapping_unknown, bool coop_active, bool manual, owner_ref current,
                              uint16_t seat_member, uint32_t leader, uint32_t world_epoch, bool allow_leader)
{
    return mapping_unknown && coop_active && manual && current.member != MEMBER_NONE && seat_member != MEMBER_NONE &&
           current.member == seat_member && current.world_epoch == world_epoch &&
           (leader != seat_member || allow_leader);
}

bool keep_through_status_gap(tri alive, bool coop_active, bool manual, owner_ref current, bool candidate_is_current,
                             uint16_t seat_member, uint32_t leader, uint32_t world_epoch, bool allow_leader)
{
    return alive == T_UNKNOWN && coop_active && manual && current.member != MEMBER_NONE && candidate_is_current &&
           seat_member != MEMBER_NONE && current.member == seat_member && current.world_epoch == world_epoch &&
           (leader != seat_member || allow_leader);
}

bool cache_survives(bool member_changed, uint16_t member, const uint16_t* current, const uint16_t* ui,
                    tri actor_valid)
{
    return !member_changed && member != MEMBER_NONE && member_in_both(member, current, ui) && actor_valid != T_NO;
}

bool guests_master(bool base, uint32_t owned_seats)
{
    return base && (owned_seats & ~1u) != 0;
}

movement_flags movement_control(bool lifecycle_movement, bool session_movement, bool custom_wait, bool active_mode)
{
    movement_flags out = {};
    out.input = lifecycle_movement && (session_movement || (custom_wait && active_mode));
    out.ownership = lifecycle_movement && (session_movement || custom_wait);
    return out;
}

bool camera_gate(bool lifecycle_camera, bool session_camera, bool gameplay, bool loading, bool interaction_owner)
{
    const bool hold = interaction_owner && gameplay && !loading;
    return lifecycle_camera && (session_camera || hold);
}

reacquire camera_reacquire(bool requested, bool was_requested, reacquire previous, bool native_ready,
                           uint32_t required)
{
    reacquire out = {};
    if (!requested) {
        return out;
    }
    bool pending = previous.pending != 0;
    uint32_t samples = previous.ready_samples;
    if (!was_requested) {
        pending = true;
        samples = 0;
    }
    if (!pending) {
        out.active = 1;
        out.ready_samples = samples;
        return out;
    }
    samples = native_ready ? samples + 1 : 0;
    if (samples >= (required > 0 ? required : 1)) {
        out.active = 1;
        out.ready_samples = samples;
        return out;
    }
    out.pending = 1;
    out.ready_samples = samples;
    return out;
}

bool camera_positions_ready(const float* xyz, uint32_t count)
{
    if (count < 2) {
        return false;
    }
    for (uint32_t i = 0; i < count; ++i) {
        float n2 = 0.0f;
        for (uint32_t k = 0; k < 3; ++k) {
            const float v = xyz[i * 3 + k];
            if (!(v == v) || v > 3.4e38f || v < -3.4e38f) {
                return false;
            }
            n2 += v * v;
        }
        if (!(n2 > 1.0e-8f)) {
            return false;
        }
    }
    return true;
}

bool party_down(const tri* alive, uint32_t count)
{
    if (count == 0) {
        return false;
    }
    for (uint32_t i = 0; i < count; ++i) {
        if (alive[i] != T_NO) {
            return false;
        }
    }
    return true;
}

}  // namespace ilc::session
