// Ivalice Legacy co-op module: the HUD snapshot sampler and its publication (see snapshot.h).
#include "hud/snapshot.h"
#include "runtime/loopback.h"

#include "control/control.h"
#include "frontend/frontend.h"
#include "hud/hud_internal.h"
#include "hud/snapshot_facts.h"
#include "hud/target_info_native.h"
#include "ilc/bridge_abi.h"
#include "menu/model.h"
#include "patch/patch_engine.h"
#include "session/reader.h"
#include "session/session.h"
#include "session/world.h"
#include "targets/live.h"
#include "targets/targets.h"

namespace ilc::hud::snapshot {
namespace {

namespace sf = snapfacts;
using detail::copy_bytes;
using detail::zero_bytes;
using session::game_reader;
using session::session_record;

// The published snapshot: one writer (the co-op thread); a reader copies and discards a torn copy (seq only grows).
struct slot_t {
    volatile uint32_t seq;
    volatile uint32_t valid;
    hud_snapshot value;
};
slot_t g_slot = {};
volatile uint32_t g_meta_epoch = 0;
volatile uint64_t g_meta_ms = 0;   // 0 = nothing published
counters g_counters = {};

uintptr_t g_base = 0;              // 0 = the pinned base
uint32_t g_published = 0;
uint64_t g_generation = 0;
hud_snapshot g_next;               // built here, then published (co-op thread only)

// Each seat's menu target as the sampler saw it last (for since_ms and the last target after the page closes).
struct seat_track {
    uint32_t handle;
    uint8_t group;
    uint8_t on_page;
    uint16_t reserved;
    uint64_t since;
};
seat_track g_track[SNAP_SEATS];
uint32_t g_track_epoch = 0;

// Player 1's aim in the field target mode (the native Target button), kept apart from the seat track: while the mode
// holds focus on a target that resolves, that target is P1's seat target (so the shared Target Info, and a party row
// cursor, follow the aimed foe); when it ends the seat's own track comes back exactly as it was.
struct aim_track {
    uint32_t handle;
    uint64_t since;
};
aim_track g_aim = {};

// Host-test doubles.
struct seat_double {
    uint32_t handle;
    uint8_t group;
    uint8_t on_page;
};
seat_double g_seat_double[SNAP_SEATS];
bool g_use_seat_doubles = false;
between_fn g_between = nullptr;

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

uintptr_t image_base() { return g_base != 0 ? g_base : static_cast<uintptr_t>(sf::PINNED_BASE); }

void between(uint32_t stage, uint64_t address)
{
    if (g_between != nullptr) {
        g_between(stage, address);
    }
}

// ---- one battle record, bracketed ----------------------------------------------------------------------------------
bool read_record(game_reader& r, uintptr_t rec, hud_unit* u)
{
    for (uint32_t attempt = 0; attempt < sf::SAMPLE_TRIES; ++attempt) {
        uint32_t timed0 = 0;
        uint32_t lasting0 = 0;
        uint32_t hp0 = 0;
        if (!r.u32(rec + sf::REC_STATUS_TIMED, &timed0) || !r.u32(rec + sf::REC_STATUS_LASTING, &lasting0) ||
            !r.u32(rec + sf::REC_HP, &hp0)) {
            return false;
        }
        between(2, rec);
        int32_t ticks[SNAP_STATUS_IDS];
        uint32_t hp_max = 0;
        uint8_t level = 0;
        uint8_t weakness = 0;
        uint8_t aug_timed = 0;
        uint8_t aug_lasting = 0;
        if (!r.read(rec + sf::REC_DURATIONS, ticks, sizeof(ticks)) || !r.u32(rec + sf::REC_HP_MAX, &hp_max) ||
            !r.u8(rec + sf::REC_LEVEL, &level) || !r.u8(rec + sf::REC_WEAKNESS, &weakness) ||
            !r.u8(rec + sf::REC_AUGMENTS_TIMED + sf::ANTI_LIBRA_BYTE, &aug_timed) ||
            !r.u8(rec + sf::REC_AUGMENTS_LASTING + sf::ANTI_LIBRA_BYTE, &aug_lasting)) {
            return false;
        }
        uint32_t timed1 = 0;
        uint32_t lasting1 = 0;
        uint32_t hp1 = 0;
        if (!r.u32(rec + sf::REC_STATUS_TIMED, &timed1) || !r.u32(rec + sf::REC_STATUS_LASTING, &lasting1) ||
            !r.u32(rec + sf::REC_HP, &hp1)) {
            return false;
        }
        if (timed0 != timed1 || lasting0 != lasting1 || hp0 != hp1) {
            bump(g_counters.record_retries);
            continue;
        }
        u->hp = hp0;
        u->hp_max = hp_max;
        u->status_timed = timed0;
        u->status_lasting = lasting0;
        u->level = level;
        u->weakness = weakness;
        // Only temporary statuses (timed and not lasting) count down; every other slot reads 0.
        const uint32_t counting = timed0 & ~lasting0;
        for (uint32_t id = 0; id < SNAP_STATUS_IDS; ++id) {
            u->duration[id] = (counting >> id & 1u) != 0 ? ticks[id] : 0;
        }
        if (((aug_timed | aug_lasting) & sf::ANTI_LIBRA_MASK) != 0) {
            u->flags |= UNIT_ANTI_LIBRA;
        }
        u->flags |= UNIT_PRESENT;
        return true;
    }
    bump(g_counters.records_torn);
    return false;
}

void clear_unit(hud_unit* u)
{
    zero_bytes(u, sizeof(*u));
    u->member = sf::MEMBER_NONE;
}

uint8_t seat_of(const session_record& rec, uint16_t member)
{
    if (member == sf::MEMBER_NONE) {
        return 0;
    }
    for (uint32_t s = 0; s < SNAP_SEATS && s < session::SEATS; ++s) {
        const session::seat_entry& e = rec.seat[s];
        if ((e.flags & session::SEATF_IN_SESSION) != 0 && e.member == member) {
            return static_cast<uint8_t>(s + 1);
        }
    }
    return 0;
}

// ---- the party: HUD layout + leader, bracketed around the rows -------------------------------------------------------
struct party_header {
    uint16_t rows[SNAP_MEMBERS];
    uint16_t leader;
};

bool read_header(game_reader& r, uintptr_t state, party_header* h)
{
    uint16_t layout[9];
    uint32_t leader = 0;
    if (!r.read(state + sf::LAYOUT_TABLE + sf::LAYOUT_HUD * sf::LAYOUT_STRIDE, layout, sizeof(layout)) ||
        !r.u32(state + sf::LEADER_MEMBER, &leader)) {
        return false;
    }
    for (uint32_t k = 0; k < SNAP_MEMBERS; ++k) {
        h->rows[k] = layout[k];
    }
    h->leader = leader <= 0xFFFFu ? static_cast<uint16_t>(leader) : sf::MEMBER_NONE;
    return true;
}

bool same_header(const party_header& a, const party_header& b)
{
    for (uint32_t k = 0; k < SNAP_MEMBERS; ++k) {
        if (a.rows[k] != b.rows[k]) {
            return false;
        }
    }
    return a.leader == b.leader;
}

void sample_party(game_reader& r, const session_record& rec, hud_snapshot* s)
{
    uintptr_t state = 0;
    if (!r.ptr(r.global(sf::BATTLE_STATE_PTR), &state) || state == 0) {
        return;
    }
    session::party_actors pa;
    session::scan_party(r, &pa);
    for (uint32_t attempt = 0; attempt < sf::SAMPLE_TRIES; ++attempt) {
        party_header before;
        if (!read_header(r, state, &before)) {
            return;
        }
        between(1, state);
        uint8_t used = 0;
        for (uint32_t k = 0; k < SNAP_MEMBERS; ++k) {
            hud_unit& u = s->members[k];
            clear_unit(&u);
            const uint16_t m = before.rows[k];
            if (m == sf::MEMBER_NONE || m >= sf::MEMBER_IDS) {
                continue;
            }
            used = static_cast<uint8_t>(k + 1);
            u.member = m;
            u.flags = UNIT_PARTY;
            u.seat = seat_of(rec, m);
            if (m == before.leader) {
                u.flags |= UNIT_LEADER;
            }
            const uintptr_t record = state + sf::MEMBER_RECORDS + static_cast<uintptr_t>(m) * sf::MEMBER_RECORD_SIZE;
            read_record(r, record, &u);
            for (const session::party_actor& a : pa.slot) {
                uintptr_t battle = 0;
                if (a.actor == 0 || !r.ptr(a.actor + sf::ACTOR_BATTLE, &battle) || battle != record) {
                    continue;
                }
                uint32_t handle = 0;
                uint32_t target = 0;
                if (r.u32(a.actor + sf::ACTOR_HANDLE, &handle) && handle == a.handle &&
                    r.u32(a.actor + sf::ACTOR_TARGET, &target)) {
                    u.handle = handle;
                    u.target = target;
                    u.flags |= UNIT_ACTOR;
                }
                break;
            }
        }
        party_header after;
        if (!read_header(r, state, &after)) {
            break;
        }
        if (same_header(before, after)) {
            s->member_count = used;
            s->leader = before.leader;
            s->head.flags |= SNAP_PARTY;
            bool known = false;
            for (uint32_t k = 0; k < used; ++k) {
                const hud_unit& u = s->members[k];
                if ((u.flags & UNIT_PRESENT) == 0) {
                    continue;
                }
                known = true;
                if (((u.status_timed | u.status_lasting) & sf::STATUS_LIBRA) != 0) {
                    s->head.flags |= SNAP_LIBRA;
                }
            }
            if (known) {
                s->head.flags |= SNAP_LIBRA_KNOWN;
            }
            return;
        }
    }
    // The party moved under every try: no rows rather than rows from two different parties.
    bump(g_counters.party_torn);
    for (hud_unit& u : s->members) {
        clear_unit(&u);
    }
    s->member_count = 0;
    s->leader = sf::MEMBER_NONE;
}

// ---- seat targets ---------------------------------------------------------------------------------------------------
uint32_t seat_target_now(uint32_t seat, uint8_t* group)
{
    if (g_use_seat_doubles) {
        const seat_double& d = g_seat_double[seat];
        *group = d.group;
        return d.on_page != 0 ? d.handle : 0;
    }
    frontend::seat_view v;
    frontend::read_seat(seat, &v);
    if (v.mode != menu::MODE_TARGET) {
        return 0;
    }
    const targets::candidate_list* l = frontend::candidates(seat);
    if (l == nullptr) {
        return 0;
    }
    const targets::candidate* c = targets::selectable_at(*l, v.group, v.target_index);
    if (c == nullptr) {
        return 0;
    }
    *group = v.group;
    return c->handle;
}

void sample_seats(game_reader& r, uint32_t epoch, uint64_t now, hud_snapshot* s)
{
    if (epoch != g_track_epoch) {
        zero_bytes(g_track, sizeof(g_track));
        g_aim = {};
        g_track_epoch = epoch;
    }
    for (uint32_t k = 0; k < SNAP_SEATS; ++k) {
        seat_track& t = g_track[k];
        uint8_t group = 0;
        const uint32_t handle = seat_target_now(k, &group);
        if (handle != 0) {
            if (handle != t.handle || t.on_page == 0) {
                t.since = now;  // moved onto a target (or came back to the page): this seat targeted just now
            }
            t.handle = handle;
            t.group = group;
            t.on_page = 1;
        } else {
            t.on_page = 0;  // the last target stays, with the time it was aimed
        }
        seat_target& out = s->seats[k];
        out.handle = t.handle;
        out.group = t.group;
        out.on_page = t.on_page;
        out.since_ms = t.since;
        if (k != 0) {
            continue;
        }
        // P1's menu on a target page wins (it is the more specific aim); otherwise the target mode's.
        const uint32_t aimed = handle == 0 ? target_info::giveback::aim(r) : 0;
        if (aimed == 0) {
            g_aim = {};
            continue;
        }
        if (aimed != g_aim.handle) {
            g_aim = {aimed, now};   // aimed at a new target: P1 targeted just now
        }
        out.handle = aimed;
        out.group = 0;
        out.on_page = 1;
        out.since_ms = g_aim.since;
    }
}

// ---- targets: seat targets first, then each party member's current target ------------------------------------------
void add_target(uint32_t handle, uint32_t* list, uint32_t* count)
{
    if (handle == 0) {
        return;
    }
    for (uint32_t k = 0; k < *count; ++k) {
        if (list[k] == handle) {
            return;
        }
    }
    if (*count >= SNAP_TARGETS) {
        bump(g_counters.targets_dropped);
        return;
    }
    list[(*count)++] = handle;
}

void sample_targets(game_reader& r, const session_record& rec, hud_snapshot* s)
{
    uint32_t handles[SNAP_TARGETS];
    uint32_t count = 0;
    for (uint32_t k = 0; k < SNAP_SEATS; ++k) {
        add_target(s->seats[k].handle, handles, &count);
    }
    for (uint32_t k = 0; k < s->member_count; ++k) {
        if ((s->members[k].flags & UNIT_ACTOR) != 0) {
            add_target(s->members[k].target, handles, &count);
        }
    }
    for (uint32_t k = 0; k < count; ++k) {
        hud_unit& u = s->targets[k];
        clear_unit(&u);
        u.handle = handles[k];
        for (uint32_t m = 0; m < s->member_count; ++m) {
            const hud_unit& row = s->members[m];
            if ((row.flags & UNIT_ACTOR) != 0 && row.handle == u.handle) {
                u.member = row.member;
                u.seat = seat_of(rec, row.member);
                u.flags |= UNIT_PARTY;
            }
        }
        const uintptr_t actor = targets::live_actor(r, u.handle, false);
        uintptr_t battle = 0;
        if (actor == 0 || !r.ptr(actor + sf::ACTOR_BATTLE, &battle) || battle == 0) {
            continue;  // the handle stays, nothing read: the passes show nothing they cannot back
        }
        u.flags |= UNIT_ACTOR;
        uint32_t target = 0;
        if (r.u32(actor + sf::ACTOR_TARGET, &target)) {
            u.target = target;
        }
        uintptr_t unit = 0;
        uint8_t type = 0;
        if (r.ptr(actor + sf::ACTOR_UNIT, &unit) && unit != 0 && r.u8(unit + sf::UNIT_TYPE, &type)) {
            u.unit_type = static_cast<uint8_t>(type & 0x0F);
        }
        read_record(r, battle, &u);
    }
    s->target_count = static_cast<uint8_t>(count);
}

bool build(uint64_t now, hud_snapshot* s)
{
    session_record rec;
    if (!session::read(&rec) || rec.head.epoch == 0 || (rec.head.flags & session::GATE_WORLD) == 0 ||
        control::current_state() != control::STATE_ON || !patch::subsystem_live(ILC_SUBSYS_RENDER)) {
        return false;
    }
    zero_bytes(s, sizeof(*s));
    s->head.epoch = rec.head.epoch;
    s->head.flags = SNAP_WORLD;
    s->head.generation = ++g_generation;
    s->sampled_ms = now;
    s->layout = layout_for_session();
    s->leader = sf::MEMBER_NONE;
    for (hud_unit& u : s->members) {
        clear_unit(&u);
    }
    for (hud_unit& u : s->targets) {
        clear_unit(&u);
    }
    game_reader r(image_base());
    sample_party(r, rec, s);
    sample_seats(r, rec.head.epoch, now, s);
    sample_targets(r, rec, s);
    uint32_t scale = 0;
    if (r.u32(r.global(sf::TICK_SCALE), &scale)) {
        s->tick_scale = scale;
        s->head.flags |= SNAP_TICKS;
    }
    if (boss_gauge_shown(image_base())) {
        s->head.flags |= SNAP_BOSS_GAUGE;
    }
    return true;
}

}  // namespace

bool boss_gauge_shown(uintptr_t base)
{
    game_reader r(base);
    uintptr_t manager = 0;
    uintptr_t owner = 0;
    if (!r.ptr(r.global(sf::BOSS_GAUGE_MANAGER), &manager) || manager == 0 ||
        !r.ptr(manager + sf::BOSS_GAUGE_OWNER, &owner) || owner == 0) {
        return false;
    }
    const uint32_t objects[] = {sf::BOSS_GAUGE_OBJECT_A, sf::BOSS_GAUGE_OBJECT_B};
    for (const uint32_t at : objects) {
        uintptr_t object = 0;
        uint32_t flags = 0;
        if (r.ptr(owner + at, &object) && object != 0 && r.u32(object + sf::BOSS_GAUGE_FLAGS, &flags) &&
            (flags & sf::BOSS_GAUGE_SHOWN) != 0) {
            return true;
        }
    }
    return false;
}

uint8_t layout_for_session()
{
    return LAYOUT_COUCH;  // phase 1 is couch co-op; the online layout is a seam
}

// ---- publication -----------------------------------------------------------------------------------------------
void publish(const hud_snapshot& s_in)
{
    ILC_LOOPBACK_COPY_STATIC(hud_snapshot, FAM_SNAPSHOT, s_in, s);  // the seam (host tests: the loopback codec)
    const uint32_t odd = (g_slot.seq + 1u) | 1u;
    __atomic_store_n(&g_slot.seq, odd, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    copy_bytes(const_cast<hud_snapshot*>(&g_slot.value), &s, sizeof(hud_snapshot));
    g_slot.valid = 1;
    __atomic_store_n(&g_slot.seq, odd + 1u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_meta_epoch, s.head.epoch, __ATOMIC_RELAXED);
    __atomic_store_n(&g_meta_ms, s.sampled_ms != 0 ? s.sampled_ms : uint64_t{1}, __ATOMIC_RELEASE);
    bump(g_counters.samples);
}

void withdraw()
{
    const uint32_t odd = (g_slot.seq + 1u) | 1u;
    __atomic_store_n(&g_slot.seq, odd, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    g_slot.valid = 0;
    __atomic_store_n(&g_slot.seq, odd + 1u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_meta_ms, uint64_t{0}, __ATOMIC_RELEASE);
    bump(g_counters.withdrawn);
}

bool read(hud_snapshot& out)
{
    const uint32_t first = __atomic_load_n(&g_slot.seq, __ATOMIC_ACQUIRE);
    if ((first & 1u) != 0) {
        return false;
    }
    const uint32_t ok = g_slot.valid;
    copy_bytes(&out, const_cast<const hud_snapshot*>(&g_slot.value), sizeof(hud_snapshot));
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
    return __atomic_load_n(&g_slot.seq, __ATOMIC_RELAXED) == first && ok == 1;
}

bool meta(uint32_t* epoch, uint64_t* sampled_ms)
{
    const uint64_t ms = __atomic_load_n(&g_meta_ms, __ATOMIC_ACQUIRE);
    *epoch = __atomic_load_n(&g_meta_epoch, __ATOMIC_RELAXED);
    *sampled_ms = ms;
    return ms != 0;
}

const counters* stats()
{
    return &g_counters;
}

// ---- co-op thread ---------------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms)
{
    (void)now_ms;
    g_base = image_base;
    zero_bytes(g_track, sizeof(g_track));
    g_aim = {};
    g_track_epoch = 0;
}

void step(uint64_t now_ms)
{
    if (!build(now_ms, &g_next)) {
        if (g_published != 0) {
            withdraw();
            g_published = 0;
        }
        return;
    }
    publish(g_next);
    g_published = 1;
}

void stop()
{
    withdraw();
    g_published = 0;
}

// ---- host tests ------------------------------------------------------------------------------------------------
void test_reset()
{
    withdraw();
    g_published = 0;
    zero_bytes(const_cast<counters*>(&g_counters), sizeof(counters));
    zero_bytes(g_track, sizeof(g_track));
    g_aim = {};
    zero_bytes(g_seat_double, sizeof(g_seat_double));
    g_track_epoch = 0;
    g_use_seat_doubles = false;
    g_between = nullptr;
    g_base = 0;
}

void test_set_image_base(uintptr_t image_base)
{
    g_base = image_base;
}

void test_set_seat_target(uint32_t seat, uint32_t handle, uint8_t group, uint8_t on_page)
{
    if (seat < SNAP_SEATS) {
        g_seat_double[seat] = {handle, group, on_page};
    }
}

void test_use_seat_targets(bool on)
{
    g_use_seat_doubles = on;
}

void test_set_between(between_fn fn)
{
    g_between = fn;
}

bool test_sample(uint64_t now_ms, hud_snapshot* out)
{
    return build(now_ms, out);
}

}  // namespace ilc::hud::snapshot
