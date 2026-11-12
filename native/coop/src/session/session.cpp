// Ivalice Legacy co-op module: the session core (see session.h).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "runtime/game_thread.h"
#include "movement/movement.h"
#include "session/session.h"

namespace ilc::session {
namespace {

using namespace facts;

constexpr uint32_t EFFECT_RING = 32;
constexpr uint64_t IDENTITY_SETTLE_MS = 200;  // a party-derived identity change must hold this long
constexpr uint64_t SCAN_RETRY_MS = 60;        // rescan sooner while a seat's character has no actor
constexpr uint8_t NO_SLOT = 0xFF;

// The kill switch (any thread; read at each step). Outside `core`, so a restart keeps it.
volatile uint32_t g_live_off = 0;

struct core {
    uint8_t started;
    uint8_t config_pending;
    uint8_t force_scan;
    uint8_t last_valid;
    uintptr_t image_base;
    seat_config staged;        // under g_config_lock
    seat_config cfg;           // co-op thread
    uint32_t live_parked;      // under g_config_lock (bit s)
    identity configured;
    identity live;
    world_epochs worlds;
    generation_tracker gens;
    ownership owners;
    party_actors party;
    uint16_t scanned_layout[4];
    uint64_t last_scan_ms;
    uint64_t sample_no;
    uint64_t sig_a;
    uint64_t sig_b;
    uint32_t session_epoch;
    uint32_t world_epoch;
    uint64_t generation;
    uint64_t layout_hash;
    identity pending;          // a party-derived identity waiting to settle
    uint8_t pending_valid;
    int8_t ko_seat;            // the last KO promotion classification (kept through an unknown P1 sample)
    uint64_t pending_since_ms;
    world_sample world;
    world_view view;
    lifecycle life;
    uint32_t phase;
    uint32_t effects_total;
};

core g;
SRWLOCK g_config_lock = SRWLOCK_INIT;
SRWLOCK g_effect_lock = SRWLOCK_INIT;
pending_effect g_ring[EFFECT_RING];
uint32_t g_ring_head = 0;
uint32_t g_ring_count = 0;
patch::record_slot<session_record> g_slot;
volatile uint32_t g_epoch = 0;
volatile uint64_t g_generation = 0;
volatile int32_t g_force_movement_live = -1;
counters g_counters;

void bump(volatile uint64_t& c, uint64_t n = 1)
{
    __atomic_add_fetch(&c, n, __ATOMIC_RELAXED);
}

bool seat_in_session(const identity& id, uint32_t s)
{
    return s < ACTIVE_SEATS && (s == 0 || (id.in_session & (1u << s)) != 0);
}

identity identity_of(const seat_config& c)
{
    identity id;
    memset(&id, 0, sizeof(id));
    for (uint32_t s = 0; s < SEATS; ++s) {
        id.member[s] = MEMBER_NONE;
    }
    // P1: a fixed character, or AUTO = whoever leads (resolved from the live party; Vaan only as a fallback).
    id.member[0] = c.character[0] < REGULARS ? c.character[0] : 0;
    id.in_session = 1;
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        const uint8_t ch = c.character[s];
        if ((c.has_input & (1u << s)) == 0 || ch == ILC_CHAR_NONE) {
            continue;  // no input, or a guest who sits out without a character
        }
        id.in_session |= static_cast<uint8_t>(1u << s);
        id.member[s] = ch < REGULARS ? ch : MEMBER_NONE;  // AUTO: filled from the party
    }
    return id;
}

uint32_t manual_seats(const core& c, uint32_t live_parked)
{
    uint32_t m = 0;
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        if (seat_in_session(c.live, s) && c.live.member[s] != MEMBER_NONE && (c.cfg.parked & (1u << s)) == 0 &&
            (live_parked & (1u << s)) == 0) {
            m |= 1u << s;
        }
    }
    return c.cfg.enabled ? m : 0;
}

uint8_t slot_of(const world_sample& w, uint16_t member)
{
    if (member == MEMBER_NONE) {
        return NO_SLOT;
    }
    for (uint32_t k = 0; k < 4; ++k) {
        if (w.current[k] == member) {
            return static_cast<uint8_t>(k);
        }
    }
    return NO_SLOT;
}

const party_actor* actor_for(const core& c, uint16_t member)
{
    const uint8_t k = slot_of(c.world, member);
    if (k == NO_SLOT || c.party.slot[k].actor == 0) {
        return nullptr;
    }
    return &c.party.slot[k];
}

void push_effect(uint32_t epoch, const effect& fx)
{
    AcquireSRWLockExclusive(&g_effect_lock);
    if (g_ring_count == EFFECT_RING) {
        g_ring_head = (g_ring_head + 1) % EFFECT_RING;
        --g_ring_count;
        bump(g_counters.effects_dropped);
    }
    pending_effect& p = g_ring[(g_ring_head + g_ring_count) % EFFECT_RING];
    p.epoch = epoch;
    p.reserved = 0;
    p.fx = fx;
    ++g_ring_count;
    ReleaseSRWLockExclusive(&g_effect_lock);
}

// What the follower release logic needs to know about one incarnation this pass.
follower_sample observe(game_reader& r, const core& c, const owner_token& token)
{
    follower_sample s;
    memset(&s, 0, sizeof(s));
    s.of = token;
    s.token = token;
    s.sample = c.sample_no;
    s.action_state = NONE_I32;
    s.target = NONE_I32;
    s.action = NONE_I32;
    s.descriptor_action = NONE_I32;
    const world_sample& w = c.world;
    s.layout_coherent = w.roots && member_in_both(token.member, w.current, w.ui);
    s.leader = w.leader;
    s.cutscene = c.view.cutscene || c.view.loading;
    const party_actor* entry = actor_for(c, token.member);
    const bool matches = entry != nullptr && entry->actor == token.actor && entry->phys == token.phys;
    // A coherent member without a scan entry is UNKNOWN; a different entry proves a new incarnation.
    s.materialized = matches || (entry == nullptr && s.layout_coherent);
    s.alive = s.materialized ? actor_alive(r, token.actor) : T_UNKNOWN;
    s.command_blocked = s.materialized ? actor_command_blocked(r, token.actor) : T_UNKNOWN;
    if (!matches || !s.layout_coherent || token.handle == 0) {
        return s;
    }
    uint32_t handle = 0;
    if (!r.u32(token.actor + ACTOR_HANDLE, &handle) || handle == 0) {
        return s;
    }
    if (handle != token.handle || actor_physics(r, token.actor) != token.phys) {
        s.materialized = 0;
        return s;
    }
    uint8_t state = 0;
    uintptr_t descriptor = 0;
    uint32_t target = 0;
    uint16_t action = 0;
    uintptr_t list_a = 0;
    uintptr_t list_b = 0;
    if (!r.u8(token.actor + ACTOR_ACTION_STATE, &state) || !r.ptr(token.actor + ACTOR_DESCRIPTOR, &descriptor) ||
        !r.u32(token.actor + ACTOR_TARGET, &target) || !r.u16(token.actor + ACTOR_ACTION, &action) ||
        !r.ptr(token.actor + ACTOR_FOLLOW_LIST_A, &list_a) || !r.ptr(token.actor + ACTOR_FOLLOW_LIST_B, &list_b)) {
        return s;
    }
    int32_t descriptor_action = NONE_I32;
    if (descriptor != 0) {
        uint16_t d = 0;
        if (!r.u16(descriptor, &d)) {
            return s;
        }
        descriptor_action = d;
    }
    const uintptr_t primary = r.global(FOLLOW_LIST_PRIMARY);
    const uintptr_t secondary = r.global(FOLLOW_LIST_SECONDARY);
    s.available = 1;
    s.action_state = state;
    s.target = target;
    s.action = action;
    s.descriptor_action = descriptor_action;
    s.follows_list = (list_a == primary || list_a == secondary || list_b == primary || list_b == secondary) ? 1 : 0;
    return s;
}

// The acquire boundary: the token must still be the live actor (same handle, same physics, same world).
bool still_current(game_reader& r, const owner_token& t, uint32_t world_epoch)
{
    uint32_t handle = 0;
    return t.world_epoch == world_epoch && t.handle != 0 && r.u32(t.actor + ACTOR_HANDLE, &handle) &&
           handle == t.handle && actor_physics(r, t.actor) == t.phys;
}

uint64_t fnv(uint64_t h, const void* data, size_t n)
{
    const uint8_t* p = static_cast<const uint8_t*>(data);
    for (size_t i = 0; i < n; ++i) {
        h ^= p[i];
        h *= 0x100000001B3ull;  // NOTADDR: FNV-1a prime
    }
    return h;
}

void refresh_party(game_reader& r, core& c, uint64_t now)
{
    bool scan = c.force_scan || !c.party.manager_ok || now - c.last_scan_ms >= SCAN_INTERVAL_MS || c.last_scan_ms == 0;
    for (uint32_t k = 0; k < 4 && !scan; ++k) {
        scan = c.scanned_layout[k] != c.world.current[k];
    }
    for (uint32_t k = 0; k < 4 && !scan; ++k) {
        if (c.party.slot[k].actor != 0 && !party_slot_holds(r, k, c.party.slot[k])) {
            scan = true;
        }
    }
    // A seat whose character is in both layouts but has no actor (a read gap or a rebuild): look again soon.
    for (uint32_t s = 0; s < ACTIVE_SEATS && !scan; ++s) {
        const uint16_t m = c.live.member[s];
        if (seat_in_session(c.live, s) && m != MEMBER_NONE && member_in_both(m, c.world.current, c.world.ui) &&
            actor_for(c, m) == nullptr && now - c.last_scan_ms >= SCAN_RETRY_MS) {
            scan = true;
        }
    }
    if (!scan) {
        return;
    }
    scan_party(r, &c.party);
    memcpy(c.scanned_layout, c.world.current, sizeof(c.scanned_layout));
    c.last_scan_ms = now != 0 ? now : 1;
    c.force_scan = 0;
    bump(g_counters.scans);
}

}  // namespace

void start(uintptr_t image_base, uint64_t now_ms)
{
    (void)now_ms;
    AcquireSRWLockExclusive(&g_config_lock);
    const seat_config staged = g.staged;
    const uint8_t pending = g.config_pending;
    const uint32_t parked = g.live_parked;
    ReleaseSRWLockExclusive(&g_config_lock);
    memset(&g, 0, sizeof(g));
    g.staged = staged;
    g.config_pending = pending;
    g.live_parked = parked;
    g.cfg = staged;
    g.configured = identity_of(g.cfg);
    g.live = g.configured;
    g.image_base = image_base;
    g.session_epoch = FIRST_EPOCH;
    g.ko_seat = NO_SEAT;
    g.phase = PHASE_NO_WORLD;
    g.force_scan = 1;
    session_record rec;
    memset(&rec, 0, sizeof(rec));
    rec.head.epoch = FIRST_EPOCH;
    rec.location = LOCATION_NONE;
    rec.leader = MEMBER_NONE;
    rec.phase = PHASE_NO_WORLD;
    rec.reason = WR_UNAVAILABLE;
    for (uint32_t s = 0; s < SEATS; ++s) {
        rec.seat[s].member = MEMBER_NONE;
        rec.seat[s].party_slot = NO_SLOT;
    }
    gtq::set_world(FIRST_EPOCH, false);
    g_slot.publish(rec);
    __atomic_store_n(&g_epoch, FIRST_EPOCH, __ATOMIC_RELEASE);
    __atomic_store_n(&g_generation, 0, __ATOMIC_RELEASE);
    g.started = 1;
}

void configure(const seat_config& cfg)
{
    seat_config c = cfg;
    // Seats at or above the cap never take part, whatever the record says.
    const uint8_t cap = static_cast<uint8_t>((1u << ACTIVE_SEATS) - 1u);
    c.has_input &= cap;
    c.parked &= cap;
    for (uint32_t s = ACTIVE_SEATS; s < SEATS; ++s) {
        c.character[s] = ILC_CHAR_NONE;
    }
    AcquireSRWLockExclusive(&g_config_lock);
    g.staged = c;
    g.config_pending = 1;
    ReleaseSRWLockExclusive(&g_config_lock);
}

void configure_from_settings(const ilc_settings& s)
{
    seat_config c;
    memset(&c, 0, sizeof(c));
    for (uint32_t i = 0; i < SEATS; ++i) {
        const ilc_seat_settings& seat = s.seats[i];
        c.character[i] = seat.character;
        if (i < ACTIVE_SEATS && (seat.device.kind != ILC_DEVICE_NONE || s.kbm_seat == i + 1)) {
            c.has_input |= static_cast<uint8_t>(1u << i);
        }
        if (seat.parked) {
            c.parked |= static_cast<uint8_t>(1u << i);
        }
    }
    c.enabled = 1;
    configure(c);
}

void set_live_enabled(bool enabled)
{
    __atomic_store_n(&g_live_off, enabled ? 0u : 1u, __ATOMIC_RELEASE);
}

void set_live_parked(uint32_t parked_seats)
{
    AcquireSRWLockExclusive(&g_config_lock);
    g.live_parked = parked_seats & ((1u << ACTIVE_SEATS) - 1u);
    ReleaseSRWLockExclusive(&g_config_lock);
}

void step(uint64_t now)
{
    core& c = g;
    if (!c.started) {
        return;
    }
    bump(g_counters.steps);
    ++c.sample_no;
    AcquireSRWLockExclusive(&g_config_lock);
    const bool reconfigure = c.config_pending != 0;
    const seat_config previous_cfg = c.cfg;
    if (reconfigure) {
        c.cfg = c.staged;
        c.config_pending = 0;
    }
    const uint32_t live_parked = c.live_parked;
    const uint8_t settings_enabled = c.staged.enabled;
    ReleaseSRWLockExclusive(&g_config_lock);
    // The kill switch: co-op off now, whatever the settings say; back on restores the settings' value. It is not a
    // seat change, so it never re-resolves who controls whom.
    c.cfg.enabled = __atomic_load_n(&g_live_off, __ATOMIC_ACQUIRE) != 0 ? 0 : settings_enabled;
    // A settings record changes seat identity only through a seat's character or input. Colours, sounds, park and
    // the switch never touch who controls whom; a seat whose own fields did not change keeps its live character
    // (an AUTO fill, an adopted swap). Changed seats are resolved against the party once it has been read.
    bool reresolve = false;
    identity base = c.live;
    if (reconfigure) {
        const identity next = identity_of(c.cfg);
        base = next;
        for (uint32_t s = 0; s < ACTIVE_SEATS; ++s) {
            const bool same_seat = previous_cfg.character[s] == c.cfg.character[s] &&
                                   ((previous_cfg.has_input ^ c.cfg.has_input) & (1u << s)) == 0;
            if (same_seat) {
                base.member[s] = c.live.member[s];
            } else {
                reresolve = true;
            }
        }
        c.configured = next;
    }

    game_reader r(c.image_base);
    sample_world(r, &c.world);
    const world_sample& w = c.world;
    if (reresolve) {
        identity resolved;
        c.live = (w.roots && resolve_identity(base, w.current, w.ui, w.leader, &resolved)) ? resolved : base;
        c.pending_valid = 0;
        c.force_scan = 1;
    }
    refresh_party(r, c, now);

    // P1's own character: is it available to lead (alive, no command-blocking status)? A KO promotion (the game
    // made a guest's character leader because P1 is down) is not a seat swap; an unknown sample keeps the last
    // classification instead of inventing one.
    const uint16_t p1_member = c.live.member[0];
    const party_actor* p1_actor = actor_for(c, p1_member);
    const bool p1_present = w.roots && member_in_both(p1_member, w.current, w.ui);
    const int32_t forced = __atomic_load_n(&g_force_movement_live, __ATOMIC_RELAXED);
    const bool berserk_steerable = forced >= 0 ? forced != 0 : movement::berserk_gate_live();
    const tri p1_available = p1_actor != nullptr ? actor_leads(r, p1_actor->actor, berserk_steerable) : T_UNKNOWN;
    int8_t ko_seat = NO_SEAT;
    if (p1_present) {
        ko_seat = p1_available == T_UNKNOWN ? c.ko_seat : ko_promoted_seat(c.live, w.leader, p1_available, true);
    }
    c.ko_seat = ko_seat;
    bool leader_is_guest = false;
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        leader_is_guest = leader_is_guest || (seat_in_session(c.live, s) && c.live.member[s] != MEMBER_NONE &&
                                              w.leader == c.live.member[s]);
    }
    const bool uncertain_leader = p1_available == T_UNKNOWN && p1_present && leader_is_guest;

    // Adopt a party or leader change (the game's, a story script's, another mod's) once it has held for
    // IDENTITY_SETTLE_MS: leader, status and layout words are published separately and must settle first.
    identity candidate;
    if (c.cfg.enabled && ko_seat == NO_SEAT && !uncertain_leader && identity_from_party(c.live, w, &candidate)) {
        if (!c.pending_valid || !same_identity(candidate, c.pending)) {
            c.pending = candidate;
            c.pending_valid = 1;
            c.pending_since_ms = now;
        } else if (now - c.pending_since_ms >= IDENTITY_SETTLE_MS) {
            c.live = candidate;
            c.pending_valid = 0;
            c.force_scan = 1;
            bump(g_counters.identity_adoptions);
        }
    } else {
        c.pending_valid = 0;
    }
    const int32_t materialized = static_cast<int32_t>(c.party.found);
    evaluate_world(c.live, w, materialized, &c.view);
    const world_view& v = c.view;

    // Epochs. The world epoch counts coherent worlds; the session epoch moves at every boundary either way.
    const bool valid = world_valid(v);
    c.world_epoch = c.worlds.observe(valid, v.battle_state, v.location);
    if (valid != (c.last_valid != 0) || (valid && (v.battle_state != c.sig_a || v.location != c.sig_b))) {
        if (c.session_epoch == 0xFFFFFFFFu) {  // NOTADDR: wrap
            c.session_epoch = FIRST_EPOCH;  // wrap without ever reaching 0
        }
        ++c.session_epoch;
        bump(g_counters.epoch_bumps);
        c.force_scan = 1;
    }
    c.last_valid = valid ? 1 : 0;
    if (valid) {
        c.sig_a = v.battle_state;
        c.sig_b = v.location;
    }

    // Per seat: actor, liveness, token.
    const uint32_t manual = manual_seats(c, live_parked);
    seat_entry seats[SEATS];
    owner_token token[SEATS];
    uint8_t has_token[SEATS] = {};
    tri alive[SEATS];
    memset(seats, 0, sizeof(seats));
    memset(token, 0, sizeof(token));
    for (uint32_t s = 0; s < SEATS; ++s) {
        alive[s] = T_UNKNOWN;
        seats[s].member = MEMBER_NONE;
        seats[s].party_slot = NO_SLOT;
        if (!seat_in_session(c.live, s)) {
            continue;
        }
        seat_entry& e = seats[s];
        e.flags |= SEATF_IN_SESSION;
        if (manual & (1u << s)) {
            e.flags |= SEATF_MANUAL;
        }
        e.member = c.live.member[s];
        if (e.member == MEMBER_NONE) {
            continue;
        }
        e.party_slot = slot_of(w, e.member);
        const bool coherent = w.roots && member_in_both(e.member, w.current, w.ui);
        if (coherent) {
            e.flags |= SEATF_COHERENT;
        }
        const party_actor* pa = actor_for(c, e.member);
        if (pa != nullptr) {
            e.flags |= SEATF_ACTOR;
            e.actor = pa->actor;
            e.phys = pa->phys;
            uint32_t handle = 0;
            e.handle = r.u32(pa->actor + ACTOR_HANDLE, &handle) ? handle : 0;
            alive[s] = actor_alive(r, pa->actor);
            if (alive[s] != T_UNKNOWN) {
                e.flags |= SEATF_ALIVE_KNOWN;
                if (alive[s] == T_YES) {
                    e.flags |= SEATF_ALIVE;
                }
            }
            if (actor_walks(r, pa->actor) == T_YES) {
                e.flags |= SEATF_WALKS;
            }
        }
        if (c.world_epoch != 0 &&
            c.gens.observe(static_cast<uint8_t>(s), e.member, e.actor, e.phys, e.handle, c.world_epoch, alive[s],
                           coherent, &token[s])) {
            has_token[s] = 1;
            e.actor_generation = token[s].actor_generation;
        }
    }

    // Lifecycle, KO promotion, session movement.
    tri guest_alive[SEATS - 1];
    bool guest_present[SEATS - 1];
    uint32_t present_mask = 0;
    for (uint32_t s = 1; s < SEATS; ++s) {
        guest_alive[s - 1] = alive[s];
        guest_present[s - 1] = (seats[s].flags & SEATF_ACTOR) != 0;
        if (guest_present[s - 1]) {
            present_mask |= 1u << s;
        }
    }
    const bool enabled = c.cfg.enabled != 0;
    c.life = lifecycle_gates(enabled, alive[0], guest_alive, guest_present, ACTIVE_SEATS - 1);
    const bool session_move = guest_may_move(v, c.live, w, present_mask);
    const movement_flags mf = movement_control(c.life.movement, session_move, false, false);

    // The whole active party down: the game's own recovery owns input.
    tri party_alive[4];
    uint32_t party_count = 0;
    for (uint32_t k = 0; k < 4; ++k) {
        if (c.party.slot[k].actor != 0) {
            party_alive[party_count++] = actor_alive(r, c.party.slot[k].actor);
        }
    }
    const bool down = w.roots && party_down(party_alive, party_count);

    // Desired owners (guests only; P1 is never steered by the module).
    owner_token desired[SEATS];
    uint8_t has_desired[SEATS] = {};
    uint8_t eligible[SEATS] = {};
    uint32_t candidate_steer = 0;
    memset(desired, 0, sizeof(desired));
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        seat_entry& e = seats[s];
        if ((e.flags & SEATF_IN_SESSION) == 0 || e.member == MEMBER_NONE) {
            continue;
        }
        const bool is_manual = (manual & (1u << s)) != 0;
        const bool allow_leader = ko_seat == static_cast<int8_t>(s);
        steer_facts f = {};
        f.enabled = mf.ownership && is_manual;
        f.alive = alive[s];
        f.actor_present = (e.flags & SEATF_ACTOR) != 0;
        f.identity_current = 1;  // the actor was resolved for this pass's character, never from an older one
        f.layout_coherent = (e.flags & SEATF_COHERENT) != 0;
        f.allow_leader = allow_leader;
        f.member = e.member;
        f.leader = w.leader;
        if (seat_steers(f) && !down) {
            candidate_steer |= 1u << s;
        }
        const bool want = enabled && is_manual && valid && has_token[s] && alive[s] == T_YES && !down &&
                          (w.leader != e.member || allow_leader);
        const bool have_owner = c.owners.has_owner[s] != 0;
        owner_ref current = {have_owner ? c.owners.owner[s].member : MEMBER_NONE, 0,
                             have_owner ? c.owners.owner[s].world_epoch : 0};
        const bool mapping_unknown = valid && (e.flags & SEATF_COHERENT) && (e.flags & SEATF_ACTOR) == 0;
        const bool keep_gap =
            keep_through_mapping_gap(mapping_unknown, enabled, is_manual, current, e.member, w.leader,
                                     c.world_epoch, allow_leader) ||
            keep_through_status_gap(alive[s], enabled, is_manual, current,
                                    have_owner && has_token[s] && same_token(c.owners.owner[s], token[s]), e.member,
                                    w.leader, c.world_epoch, allow_leader);
        if (keep_gap && have_owner) {
            desired[s] = c.owners.owner[s];
            has_desired[s] = 1;
        } else if (want) {
            desired[s] = token[s];
            has_desired[s] = 1;
        }
        eligible[s] = has_desired[s];
    }

    // Observations for every owned or releasing incarnation, then the plan.
    follower_sample samples[SEATS + INTENTS_MAX];
    uint32_t sample_count = 0;
    for (uint32_t s = 0; s < SEATS; ++s) {
        if (c.owners.has_owner[s]) {
            samples[sample_count++] = observe(r, c, c.owners.owner[s]);
        }
    }
    for (uint32_t i = 0; i < c.owners.intent_count && sample_count < SEATS + INTENTS_MAX; ++i) {
        const sample_set seen = {samples, sample_count};
        if (seen.find(c.owners.intent[i].token) == nullptr) {
            samples[sample_count++] = observe(r, c, c.owners.intent[i].token);
        }
    }
    const sample_set seen = {samples, sample_count};
    effect_list fx;
    fx.count = 0;
    fx.dropped = 0;
    plan_ownership(c.owners, desired, has_desired, eligible, seen, now, c.world_epoch, fx);
    advance_intents(c.owners, seen, now, c.world_epoch, fx);
    // Revalidate every acquire against the live actor before it counts; a failed transfer gets its intent back.
    for (uint32_t i = 0; i < fx.count; ++i) {
        const effect& e = fx.item[i];
        if (e.kind == FX_ACQUIRE && !still_current(r, e.token, c.world_epoch)) {
            refuse_acquire(c.owners, e.token, e.reason == WHY_TRANSFER, now);
            bump(g_counters.acquires_refused);
        } else if (e.kind == FX_ASK_FOLLOW) {
            push_effect(c.session_epoch, e);
        }
    }
    c.effects_total += fx.count;
    bump(g_counters.effects_dropped, fx.dropped);

    uint32_t steers = 0;
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        seat_entry& e = seats[s];
        if ((candidate_steer & (1u << s)) && c.owners.has_owner[s] && c.owners.owner[s].actor == e.actor &&
            c.owners.owner[s].phys == e.phys) {
            e.flags |= SEATF_STEERS;
            steers |= 1u << s;
        }
        if (ko_seat == static_cast<int8_t>(s)) {
            e.flags |= SEATF_KO_PROMOTED;
        }
        if (v.guest_menu[s] && (manual & (1u << s)) && !down) {
            e.flags |= SEATF_MENU;
        }
    }
    if (v.leader_seat >= 0) {
        seats[static_cast<uint32_t>(v.leader_seat)].flags |= SEATF_LEADER;
    }
    if (v.p1_menu && !down) {
        seats[0].flags |= SEATF_MENU;
    }

    // Phase and gates.
    uint32_t phase = PHASE_FIELD;
    if (!w.roots) {
        phase = PHASE_NO_WORLD;
    } else if (down) {
        phase = PHASE_PARTY_DOWN;
    } else if (v.loading) {
        phase = PHASE_LOADING;
    } else if (v.cutscene) {
        phase = PHASE_CUTSCENE;
    } else if (w.menu_open || w.modal_active) {
        phase = PHASE_MENU;
    }
    c.phase = phase;
    uint32_t gates = 0;
    bool any_guest_menu = false;
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        any_guest_menu = any_guest_menu || (seats[s].flags & SEATF_MENU) != 0;
    }
    gates |= valid ? GATE_WORLD : 0u;
    gates |= v.p1_gameplay ? GATE_P1_GAMEPLAY : 0u;
    gates |= v.gameplay ? GATE_GAMEPLAY : 0u;
    gates |= (v.movement && !down) ? GATE_MOVEMENT : 0u;
    gates |= v.camera ? GATE_CAMERA : 0u;
    gates |= (v.p1_menu && !down) ? GATE_P1_MENU : 0u;
    gates |= any_guest_menu ? GATE_GUEST_MENU : 0u;
    gates |= (c.life.logical && manual != 0) ? GATE_LOGICAL : 0u;
    gates |= c.life.shared_camera ? GATE_SHARED_CAMERA : 0u;
    gates |= guests_master(mf.ownership && !down, steers) ? GATE_GUESTS_MOVE : 0u;
    gates |= (valid && session_move && steers != 0 && !down) ? GATE_INTERACTION : 0u;
    gates |= down ? GATE_PARTY_DOWN : 0u;

    // The session generation: any change in who steers what (or a new epoch) makes older jobs stale.
    uint64_t h = 0xCBF29CE484222325ull;  // NOTADDR: FNV-1a offset basis
    h = fnv(h, &c.session_epoch, sizeof(c.session_epoch));
    for (uint32_t s = 0; s < SEATS; ++s) {
        const uint16_t f = seats[s].flags & (SEATF_IN_SESSION | SEATF_MANUAL | SEATF_STEERS);
        h = fnv(h, &f, sizeof(f));
        h = fnv(h, &seats[s].member, sizeof(seats[s].member));
        h = fnv(h, &seats[s].actor_generation, sizeof(seats[s].actor_generation));
        h = fnv(h, &seats[s].actor, sizeof(seats[s].actor));
        h = fnv(h, &seats[s].phys, sizeof(seats[s].phys));
    }
    if (h != c.layout_hash || c.generation == 0) {
        c.layout_hash = h;
        ++c.generation;
        bump(g_counters.generation_bumps);
    }

    session_record rec;
    memset(&rec, 0, sizeof(rec));
    rec.head.epoch = c.session_epoch;
    rec.head.flags = gates;
    rec.head.generation = c.generation;
    rec.world_epoch = c.world_epoch;
    rec.facts = (w.town ? FACT_TOWN : 0u) | (w.chocobo ? FACT_CHOCOBO : 0u) | (w.menu_open ? FACT_MENU : 0u) |
                (w.modal_active ? FACT_MODAL : 0u) | (w.control_enabled ? FACT_CONTROL : 0u) |
                (v.summon_seat != NO_SEAT ? FACT_SUMMON : 0u) | (c.party.manager_ok ? FACT_SCAN_OK : 0u);
    rec.location = v.location;
    rec.leader = w.leader <= 0xFFFFu ? static_cast<uint16_t>(w.leader) : MEMBER_NONE;
    rec.phase = static_cast<uint8_t>(phase);
    rec.reason = v.reason;
    memcpy(rec.seat, seats, sizeof(rec.seat));
    // The generation goes to the queue before the record names it, so a job made from this record is never older
    // than the queue's view.
    gtq::set_generation(c.generation);
    gtq::set_world(c.session_epoch, valid);
    __atomic_store_n(&g_generation, c.generation, __ATOMIC_RELEASE);
    __atomic_store_n(&g_epoch, c.session_epoch, __ATOMIC_RELEASE);
    g_slot.publish(rec);
    bump(g_counters.reads, r.reads());
    bump(g_counters.read_failures, r.failures());
}

void stop()
{
    g.started = 0;
    g.phase = PHASE_STOPPED;
    gtq::set_world(g.session_epoch != 0 ? g.session_epoch : FIRST_EPOCH, false);
    g_slot.withdraw();
}

uint32_t epoch()
{
    return __atomic_load_n(&g_epoch, __ATOMIC_ACQUIRE);
}

uint64_t generation()
{
    return __atomic_load_n(&g_generation, __ATOMIC_ACQUIRE);
}

bool read(session_record* out)
{
    return g_slot.read(*out);
}

patch::record_slot<session_record>& slot()
{
    return g_slot;
}

uint32_t take_effects(pending_effect* out, uint32_t max)
{
    AcquireSRWLockExclusive(&g_effect_lock);
    uint32_t n = 0;
    while (n < max && g_ring_count > 0) {
        out[n++] = g_ring[g_ring_head];
        g_ring_head = (g_ring_head + 1) % EFFECT_RING;
        --g_ring_count;
    }
    ReleaseSRWLockExclusive(&g_effect_lock);
    return n;
}

const counters* stats()
{
    return &g_counters;
}

void test_debug(debug_view* out)
{
    memset(out, 0, sizeof(*out));
    out->world = g.world;
    out->view = g.view;
    out->configured = g.configured;
    out->live = g.live;
    out->life = g.life;
    out->world_epoch = g.world_epoch;
    out->session_epoch = g.session_epoch;
    out->generation = g.generation;
    out->effects_total = g.effects_total;
    out->phase = g.phase;
    out->party = g.party;
    out->owners = g.owners;
}

void test_force_movement_live(int32_t value)
{
    __atomic_store_n(&g_force_movement_live, value, __ATOMIC_RELAXED);
}

void test_reset()
{
    AcquireSRWLockExclusive(&g_config_lock);
    memset(&g, 0, sizeof(g));
    ReleaseSRWLockExclusive(&g_config_lock);
    AcquireSRWLockExclusive(&g_effect_lock);
    memset(g_ring, 0, sizeof(g_ring));
    g_ring_head = 0;
    g_ring_count = 0;
    ReleaseSRWLockExclusive(&g_effect_lock);
    g_slot.withdraw();
    gtq::set_world(0, false);
    __atomic_store_n(&g_force_movement_live, -1, __ATOMIC_RELAXED);
    __atomic_store_n(&g_live_off, 0u, __ATOMIC_RELAXED);
    __atomic_store_n(&g_epoch, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_generation, 0ull, __ATOMIC_RELEASE);
    memset(&g_counters, 0, sizeof(g_counters));
}

}  // namespace ilc::session
