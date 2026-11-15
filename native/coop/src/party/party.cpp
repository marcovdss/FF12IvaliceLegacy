// Ivalice Legacy co-op module: seat changes, the leader watcher and the change operation (see party.h).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "input/input_service.h"
#include "movement/movement.h"
#include "party/party.h"
#include "party/party_facts.h"
#include "runtime/tick.h"
#include "session/reader.h"
#include "session/session.h"
#include "session/world.h"
#include "towns/towns.h"

namespace ilc::party {
namespace {

namespace pf = facts;
using session::game_reader;
using session::session_record;
using session::seat_entry;

counters g_counters;
volatile uintptr_t g_image_base = pf::IMAGE_VA;
volatile int32_t g_force_live = -1;
setter_fn g_test_setter = nullptr;
ready_fn g_test_ready = nullptr;
volatile uint32_t g_attached = 0;  // the after-handler is registered on the UI-tick row

void bump(volatile uint64_t& counter)
{
    __atomic_add_fetch(&counter, 1, __ATOMIC_RELAXED);
}

uintptr_t base()
{
    return __atomic_load_n(&g_image_base, __ATOMIC_ACQUIRE);
}

uintptr_t at(uintptr_t va)
{
    return base() + (va - pf::IMAGE_VA);
}

bool party_live()
{
    const int32_t forced = __atomic_load_n(&g_force_live, __ATOMIC_RELAXED);
    return forced >= 0 ? forced != 0 : patch::subsystem_live(ILC_SITE_SUBSYS_PARTY);
}

bool seat_in(const session_record& rec, uint32_t s)
{
    return s < ACTIVE_SEATS && (s == 0 || (rec.seat[s].flags & session::SEATF_IN_SESSION) != 0);
}

seats_view seats_of(const session_record& rec)
{
    seats_view v;
    for (uint32_t s = 0; s < SEATS; ++s) {
        v.member[s] = seat_in(rec, s) ? rec.seat[s].member : MEMBER_NONE;
        v.flags[s] = rec.seat[s].flags;
    }
    return v;
}

// ---- reads (either thread, through the fault-safe reader) --------------------------------------------------------
struct party_read {
    party_view view;
    uintptr_t block;
    uint32_t mode_bits;
    uint16_t current9[9];
};

// Job flags (the job's arg0 top byte).
constexpr uint8_t JOB_RELOAD = 0x01;  // the location was being reloaded when the job was planned
constexpr uint8_t JOB_TOWN = 0x02;    // the target has no body in this town: the operation rebuilds for it

bool read_party(game_reader& r, party_read* out)
{
    memset(out, 0, sizeof(*out));
    uint32_t leader = 0;
    if (!r.ptr(r.global(pf::PARTY_BLOCK_PTR), &out->block) || out->block == 0 ||
        !r.read(out->block + pf::LAYOUT_CURRENT, out->current9, sizeof(out->current9)) ||
        !r.read(out->block + pf::LAYOUT_UI, out->view.ui, sizeof(out->view.ui)) ||
        !r.u32(out->block + pf::LEADER, &leader) || !r.u32(out->block + pf::MODE_BITS, &out->mode_bits)) {
        return false;
    }
    memcpy(out->view.current, out->current9, sizeof(out->view.current));
    out->view.leader = leader;
    for (uint32_t k = 0; k < SLOTS; ++k) {
        out->view.able[k] = 0;
        out->view.busy[k] = 1;
    }
    return true;
}

// May this actor lead or be handed to a player: HP > 0 and none of KO, Stone, X-Zone, from one coherent sample.
session::tri can_lead(game_reader& r, uintptr_t actor)
{
    uintptr_t battle = 0;
    uint32_t hp = 0;
    uint32_t status = 0;
    if (actor == 0 || !r.ptr(actor + pf::ACTOR_BATTLE, &battle) || battle == 0 ||
        !session::battle_status(r, battle, &status) || !r.u32(battle + pf::BATTLE_HP, &hp)) {
        return session::T_UNKNOWN;
    }
    return session::tri_of(hp > 0 && (status & pf::STATUS_CANNOT_LEAD) == 0);
}

// The same test on a member's own record, for a member without a body (towns).
session::tri member_can_lead(game_reader& r, uintptr_t block, uint32_t member)
{
    const uintptr_t record = block + pf::MEMBER_RECORDS + uintptr_t{pf::MEMBER_RECORD_SIZE} * member;
    uint32_t hp = 0;
    uint32_t status = 0;
    if (block == 0 || !is_regular(member) || !session::battle_status(r, record, &status) ||
        !r.u32(record + pf::BATTLE_HP, &hp)) {
        return session::T_UNKNOWN;
    }
    return session::tri_of(hp > 0 && (status & pf::STATUS_CANNOT_LEAD) == 0);
}

// In a town where members can have bodies (party.h, "Towns"), an unseated roster member has empty current and HUD
// slots: plan against the roster there. The member keeps its roster slot (TW-01 only ever blanks a slot), able
// comes from its member record, and a member without a body is never in an attack motion. Returns the slots
// filled this way (bit k).
uint8_t extend_town(game_reader& r, party_read& p, towns::town_state* t)
{
    if (!towns::read_town_state(r, t) || t->in_town == 0 || t->degraded != 0) {
        return 0;
    }
    uint8_t hidden = 0;
    for (uint32_t k = 0; k < SLOTS; ++k) {
        const uint16_t m = t->roster[k];
        if (!is_regular(m) || p.view.current[k] != pf::EMPTY || p.view.ui[k] != pf::EMPTY) {
            continue;
        }
        bool elsewhere = false;
        for (uint32_t u = 0; u < SLOTS; ++u) {
            elsewhere = elsewhere || p.view.current[u] == m || p.view.ui[u] == m;
        }
        if (elsewhere) {
            continue;
        }
        p.view.current[k] = m;
        p.view.ui[k] = m;
        p.view.able[k] = member_can_lead(r, p.block, m) == session::T_YES ? 1 : 0;
        p.view.busy[k] = 0;
        hidden = static_cast<uint8_t>(hidden | (1u << k));
    }
    return hidden;
}

session::tri in_motion(game_reader& r, uintptr_t actor)
{
    uint64_t flags = 0;
    if (actor == 0 || !r.u64(actor + pf::ACTOR_FLAGS, &flags)) {
        return session::T_UNKNOWN;
    }
    return session::tri_of((flags & pf::ACTOR_EXECUTION_BIT) != 0);
}

// The actor is still the body in that party slot: manager entry, handle, slot and physics object agree.
bool body_holds(game_reader& r, uint32_t slot, uint64_t actor, uint32_t handle, uint64_t phys)
{
    if (slot >= SLOTS || actor == 0) {
        return false;
    }
    session::party_actor pa;
    pa.index = handle & 0xFFFFu;
    pa.handle = handle;
    pa.actor = actor;
    pa.phys = phys;
    return session::party_slot_holds(r, slot, pa);
}

void note_body(game_reader& r, party_view& v, uint32_t slot, uint64_t actor)
{
    if (slot >= SLOTS) {
        return;
    }
    v.able[slot] = can_lead(r, actor) == session::T_YES ? 1 : 0;
    v.busy[slot] = in_motion(r, actor) == session::T_NO ? 0 : 1;
}

// Facts about the seats' bodies from the session record, re-checked live.
void note_seat_bodies(game_reader& r, const session_record& rec, party_view& v)
{
    for (uint32_t s = 0; s < ACTIVE_SEATS; ++s) {
        const seat_entry& e = rec.seat[s];
        if (!seat_in(rec, s) || e.member == MEMBER_NONE || (e.flags & session::SEATF_ACTOR) == 0) {
            continue;
        }
        const int32_t k = slot_in_party(v, e.member);
        if (k >= 0 && body_holds(r, static_cast<uint32_t>(k), e.actor, e.handle, e.phys)) {
            note_body(r, v, static_cast<uint32_t>(k), e.actor);
        }
    }
}

// The steering entry a guest uses for its new character until the co-op thread catches up: the body's own
// heading and facing, no command.
movement::steer_entry entry_for(game_reader& r, uint32_t seat, uint16_t member, uint64_t actor, uint32_t handle,
                                uint64_t phys)
{
    movement::steer_entry e;
    memset(&e, 0, sizeof(e));
    e.seat = static_cast<uint8_t>(seat);
    e.member = member;
    uintptr_t root = 0;
    uintptr_t root_actor = 0;
    uintptr_t root_phys = 0;
    float headings[2] = {0.0f, 0.0f};
    if (actor == 0 || !r.ptr(actor + pf::ACTOR_ROOT, &root) || root == 0 ||
        !r.ptr(root + pf::ROOT_ACTOR, &root_actor) || root_actor != actor ||
        !r.ptr(root + pf::ROOT_PHYSICS, &root_phys) || root_phys == 0 || (phys != 0 && root_phys != phys) ||
        !r.read(root_phys + pf::PHYS_HEADING, headings, sizeof(headings)) ||
        !__builtin_isfinite(headings[0]) || !__builtin_isfinite(headings[1])) {
        return e;  // not ENTRY_ACTIVE: the slot steers nothing until the co-op thread has it
    }
    e.actor = actor;
    e.phys = root_phys;
    e.root = root;
    e.heading = headings[0];
    e.visual = headings[1];
    e.magnitude = 0.0f;
    e.handle = handle;
    e.flags = movement::ENTRY_ACTIVE;
    return e;
}

// ---- game thread: change sequence, watcher, session post ---------------------------------------------------------
uint32_t g_change_seq = 0;          // game thread only
volatile uint32_t g_posted_seq = 0;

struct watch_state {
    uint32_t leader;
    uint32_t epoch;
    uint8_t known;
};
watch_state g_watch = {0xFFFFu, 0, 0};  // game thread only

uint32_t next_seq()
{
    if (++g_change_seq == 0) {
        g_change_seq = 1;
    }
    return g_change_seq;
}

// One change of hands, in one callback: the steering remap for every guest seat that steers and changed hands,
// then the post to the session. `after` is every seat's member after the change; `body` gives, per party member
// a guest now takes, the body to steer.
struct body_ref {
    uint16_t member;
    uint64_t actor;
    uint32_t handle;
    uint64_t phys;
};

void commit(game_reader& r, const session_record& rec, uint32_t mask, const uint16_t* after, uint32_t leader,
            const body_ref* bodies, uint32_t body_count)
{
    const uint32_t seq = next_seq();
    movement::steer_entry slots[movement::SLOTS];
    memset(slots, 0, sizeof(slots));
    uint32_t slot_mask = 0;
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        if ((mask & (1u << s)) == 0 || (rec.seat[s].flags & session::SEATF_STEERS) == 0) {
            continue;  // a seat that does not steer now has no steering entry to replace
        }
        const uint32_t k = s - 1;
        slot_mask |= 1u << k;
        for (uint32_t b = 0; b < body_count; ++b) {
            if (bodies[b].member == after[s]) {
                slots[k] = entry_for(r, s, after[s], bodies[b].actor, bodies[b].handle, bodies[b].phys);
            }
        }
    }
    if (slot_mask != 0) {
        remap_publish(seq, rec.head.epoch, rec.head.generation, slot_mask, slots, patch::now_ms());
        bump(g_counters.remaps);
    }
    session::adoption a;
    memset(&a, 0, sizeof(a));
    a.seq = seq;
    a.epoch = rec.head.epoch;
    a.mask = mask;
    for (uint32_t s = 0; s < SEATS; ++s) {
        a.member[s] = after[s];
    }
    a.leader = static_cast<uint16_t>(leader <= 0xFFFFu ? leader : MEMBER_NONE);
    session::post_adoption(a);
    __atomic_store_n(&g_posted_seq, seq, __ATOMIC_RELEASE);
}

bool esper_out(const party_read& p)
{
    for (uint32_t k = 0; k < SLOTS; ++k) {
        if ((p.view.current[k] >= 27 && p.view.current[k] <= 39) || (p.view.ui[k] >= 27 && p.view.ui[k] <= 39)) {
            return true;
        }
    }
    return false;
}

// The leader watcher. `before`: the look before the drain (the town rebuild may run only there, where the drain's
// own game calls run too).
void watch(bool before)
{
    session_record rec;
    if (!party_live() || !session::read(&rec) || (rec.head.flags & session::GATE_WORLD) == 0) {
        g_watch.known = 0;  // follow again from scratch once it may act
        return;
    }
    game_reader r(base());
    party_read p;
    if (!read_party(r, &p)) {
        return;
    }
    const uint32_t leader = p.view.leader;
    if (!g_watch.known || g_watch.epoch != rec.head.epoch) {
        g_watch = {leader, rec.head.epoch, 1};
        return;
    }
    if (leader == g_watch.leader) {
        return;
    }
    const uint32_t previous = g_watch.leader;
    g_watch.leader = leader;
    bump(g_counters.watcher_changes);
    // Summons, the chocobo and scripted modes move the leader on their own; the summon lifecycle decides there.
    if ((p.mode_bits & pf::MODE_BLOCKING) != 0 || esper_out(p) || rec.seat[0].member != previous) {
        bump(g_counters.external_left);
        return;
    }
    uint32_t guest = NO_SEAT;
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        if (seat_in(rec, s) && rec.seat[s].member != MEMBER_NONE && rec.seat[s].member == leader) {
            guest = s;
        }
    }
    if (guest == NO_SEAT) {
        bump(g_counters.external_left);
        // A town cycle onto a member without a body: rebuild now, so the game's fallback (the first bound slot,
        // maybe a guest's character) never reaches the end of this frame.
        if (before && is_regular(leader) && slot_in_party(p.view, leader) < 0) {
            bool listed = false;
            for (uint32_t k = 0; k < SLOTS; ++k) {
                listed = listed || p.view.current[k] == leader;
            }
            if (!listed) {
                gtq::job j;
                memset(&j, 0, sizeof(j));
                j.op = towns::OP_TOWN_REBUILD;
                j.epoch = rec.head.epoch;
                j.generation = rec.head.generation;
                j.ttl_ms = towns::REBUILD_TTL_MS;
                if (towns::op_rebuild(j) >= 1) {
                    bump(g_counters.town_rebuilds);
                }
            }
        }
        return;
    }
    // Player 1's character must still be able to lead, or this is the game's KO promotion, not a swap.
    const seat_entry& p1 = rec.seat[0];
    const int32_t p1_slot = slot_in_party(p.view, p1.member);
    if (p1_slot < 0 || (p1.flags & session::SEATF_ACTOR) == 0 ||
        !body_holds(r, static_cast<uint32_t>(p1_slot), p1.actor, p1.handle, p1.phys) ||
        can_lead(r, p1.actor) != session::T_YES) {
        bump(g_counters.ko_promotions);
        return;
    }
    uint16_t after[SEATS];
    for (uint32_t s = 0; s < SEATS; ++s) {
        after[s] = seat_in(rec, s) ? rec.seat[s].member : MEMBER_NONE;
    }
    after[0] = static_cast<uint16_t>(leader);
    after[guest] = p1.member;
    const body_ref body = {p1.member, p1.actor, p1.handle, p1.phys};
    commit(r, rec, 1u | (1u << guest), after, leader, &body, 1);
    bump(g_counters.external_swaps);
}

// ---- job arguments ----------------------------------------------------------------------------------------------
// arg0: kind | seat << 8 | target << 16 | released << 32 | target_slot << 48 | job flags (JOB_*) << 56
// arg1: location id | target handle << 32;  arg2: target actor;  arg3: target physics object
struct job_args {
    uint8_t kind;
    uint8_t seat;
    uint16_t target;
    uint16_t released;
    uint8_t target_slot;
    uint8_t flags;
    uint32_t location;
    uint32_t handle;
    uint64_t actor;
    uint64_t phys;
};

void encode(const job_args& a, uint64_t* out)
{
    out[0] = static_cast<uint64_t>(a.kind) | (static_cast<uint64_t>(a.seat) << 8) |
             (static_cast<uint64_t>(a.target) << 16) | (static_cast<uint64_t>(a.released) << 32) |
             (static_cast<uint64_t>(a.target_slot) << 48) | (static_cast<uint64_t>(a.flags) << 56);
    out[1] = static_cast<uint64_t>(a.location) | (static_cast<uint64_t>(a.handle) << 32);
    out[2] = a.actor;
    out[3] = a.phys;
}

job_args decode(const gtq::job& j)
{
    job_args a;
    a.kind = static_cast<uint8_t>(j.arg[0]);
    a.seat = static_cast<uint8_t>(j.arg[0] >> 8);
    a.target = static_cast<uint16_t>(j.arg[0] >> 16);
    a.released = static_cast<uint16_t>(j.arg[0] >> 32);
    a.target_slot = static_cast<uint8_t>(j.arg[0] >> 48);
    a.flags = static_cast<uint8_t>(j.arg[0] >> 56);
    a.location = static_cast<uint32_t>(j.arg[1]);
    a.handle = static_cast<uint32_t>(j.arg[1] >> 32);
    a.actor = j.arg[2];
    a.phys = j.arg[3];
    return a;
}

// ---- co-op thread state -----------------------------------------------------------------------------------------
struct request {
    uint8_t state;             // request_state
    uint8_t target;
    uint8_t attempts;
    uint8_t next_target;       // a newer request taken while a job was in flight (0xFF none)
    int32_t last_result;
    uint32_t ticket;
    uint8_t town_fails;        // OP_TOWN_REBUILD results so far
    uint8_t reserved[3];
    uint64_t since_ms;
    uint64_t enqueued_ms;
    uint64_t retry_ms;         // not planned again before this (0 = no wait)
};

bool g_started = false;
request g_req[SEATS];
uint32_t g_seen_move[SEATS];
bool g_seen_init = false;
input::snapshot g_snapshot;  // co-op thread only; too large for its stack
SRWLOCK g_api_lock = SRWLOCK_INIT;
uint32_t g_api_seq[SEATS];   // under g_api_lock
uint8_t g_api_target[SEATS];
uint32_t g_api_taken[SEATS]; // co-op thread
uint32_t g_remap_seen = 0;   // co-op thread: the remap sequence first seen at g_remap_seen_ms
uint64_t g_remap_seen_ms = 0;

constexpr uint8_t NO_TARGET = 0xFF;

void take(uint32_t seat, uint8_t target, uint64_t now)
{
    request& q = g_req[seat];
    bump(g_counters.requests);
    if (q.state == REQ_QUEUED) {
        q.next_target = target;  // decided after the job in flight
        return;
    }
    q = {};
    q.state = REQ_WAITING;
    q.target = target;
    q.next_target = NO_TARGET;
    q.since_ms = now;
}

void collect(uint64_t now)
{
    AcquireSRWLockExclusive(&g_api_lock);
    uint32_t seq[SEATS];
    uint8_t target[SEATS];
    memcpy(seq, g_api_seq, sizeof(seq));
    memcpy(target, g_api_target, sizeof(target));
    ReleaseSRWLockExclusive(&g_api_lock);
    for (uint32_t s = 0; s < ACTIVE_SEATS; ++s) {
        if (seq[s] != g_api_taken[s]) {
            g_api_taken[s] = seq[s];
            take(s, target[s], now);
        }
    }
    // The Mod Menu's move-seat command, as the input service last saw it (it absorbed the seat's held input).
    if (!input::snapshot_read(&g_snapshot)) {
        return;
    }
    for (uint32_t s = 0; s < ACTIVE_SEATS; ++s) {
        const uint32_t id = g_snapshot.control.move_request[s];
        if (!g_seen_init) {
            g_seen_move[s] = id;  // a command from before this session is not replayed
        } else if (id != 0 && id != g_seen_move[s]) {
            g_seen_move[s] = id;
            take(s, g_snapshot.control.move_character[s], now);
        }
    }
    g_seen_init = true;
}

// Refusals a wait can fix: the world, the frame, a stale record, Player 1 not yet the leader again.
bool transient(int32_t code)
{
    switch (code) {
    case gtq::REJECT_STALE_EPOCH:
    case gtq::REJECT_STALE_GENERATION:
    case gtq::REJECT_EXPIRED:
    case gtq::REJECT_DISARMED:
    case gtq::REJECT_NO_WORLD:
    case gtq::RESULT_LOST:
    case REFUSE_P1_UNSTABLE:
    case REFUSE_BUSY:
    case REFUSE_DUPLICATE:
    case OP_NO_WORLD:
    case OP_AREA_PENDING:
    case OP_ROSTER_EDIT:
    case OP_NOT_FIELD:
    case OP_APPLY_PENDING:
    case OP_MODE:
    case OP_LOCATION_MOVED:
    case OP_STALE:
    case OP_UNREADABLE:
    case OP_TOWN_BUSY:
    case OP_TOWN_REBUILD:
        return true;
    default:
        return false;
    }
}

void finish(request& q, uint8_t state, int32_t result)
{
    q.state = state;
    q.last_result = result;
    if (state == REQ_REFUSED) {
        bump(g_counters.requests_refused);
    } else if (state == REQ_TIMED_OUT) {
        bump(g_counters.requests_timed_out);
    }
}

// Plans the seat's request against the latest record and queues it, or says why it waits or ends.
void attempt(uint32_t seat, request& q, uint64_t now)
{
    session_record rec;
    if (!session::read(&rec) || rec.head.epoch != session::epoch() || (rec.head.flags & session::GATE_WORLD) == 0 ||
        rec.phase != session::PHASE_FIELD || (rec.facts & session::FACT_CONTROL) == 0 || !party_live()) {
        q.last_result = OP_NO_WORLD;
        return;  // a load, a cutscene, a menu, co-op not armed: wait
    }
    game_reader r(base());
    party_read p;
    uint32_t location = 0;
    uint8_t flags = 0;
    if (!read_party(r, &p) || !r.u32(r.global(pf::LOCATION_ID), &location) ||
        !r.u8(r.global(pf::LOCATION_FLAGS), &flags)) {
        q.last_result = OP_UNREADABLE;
        return;
    }
    session::party_actors bodies;
    session::scan_party(r, &bodies);
    for (uint32_t k = 0; k < SLOTS; ++k) {
        if (bodies.slot[k].actor != 0) {
            note_body(r, p.view, k, bodies.slot[k].actor);
        }
    }
    towns::town_state town;
    const uint8_t hidden = extend_town(r, p, &town);
    change_plan plan;
    int32_t refusal = plan_change(seats_of(rec), p.view, seat, q.target, &plan);
    const bool town_take = refusal == CHANGE_OK && (hidden & (1u << plan.target_slot)) != 0;
    if (town_take && plan.kind != KIND_LEADER && plan.kind != KIND_TAKE_FREE && plan.kind != KIND_ALREADY) {
        refusal = REFUSE_NOT_IN_PARTY;  // a seat "holds" a member without a body: nothing to swap with
    }
    if (refusal != CHANGE_OK) {
        if (!transient(refusal)) {
            finish(q, REQ_REFUSED, refusal);
        } else {
            q.last_result = refusal;
        }
        return;
    }
    if (plan.kind == KIND_ALREADY) {
        finish(q, REQ_DONE, CHANGE_ALREADY);
        return;
    }
    job_args a = {};
    a.kind = plan.kind;
    a.seat = static_cast<uint8_t>(seat);
    a.target = plan.target;
    a.released = plan.released;
    a.target_slot = plan.target_slot;
    a.flags = static_cast<uint8_t>(((flags & pf::LOCATION_RELOAD) != 0 ? JOB_RELOAD : 0) | (town_take ? JOB_TOWN : 0));
    a.location = location;
    if (!town_take) {  // a town take has no body yet: the operation steers the one its rebuild binds
        const session::party_actor& body = bodies.slot[plan.target_slot];
        a.handle = body.handle;
        a.actor = body.actor;
        a.phys = body.phys;
    }
    uint64_t args[gtq::ARG_COUNT];
    encode(a, args);
    uint32_t ticket = 0;
    const int32_t status = gtq::push(OP_PARTY_CHANGE, static_cast<uint8_t>(seat), rec.head.epoch,
                                     rec.head.generation, CHANGE_TTL_MS, args, &ticket);
    ++q.attempts;
    if (status == gtq::PUSH_OK) {
        q.state = REQ_QUEUED;
        q.ticket = ticket;
        q.enqueued_ms = now;
        q.last_result = 0;
        bump(g_counters.pushed);
        return;
    }
    bump(g_counters.push_refused);
    q.last_result = -status;
    if (status != gtq::PUSH_FULL && status != gtq::PUSH_NO_WORLD && status != gtq::PUSH_NOT_LIVE &&
        status != gtq::PUSH_BAD_EPOCH) {
        finish(q, REQ_REFUSED, -status);
    }
}

void advance(uint32_t seat, uint64_t now)
{
    request& q = g_req[seat];
    if (q.state == REQ_QUEUED) {
        gtq::completion done;
        const int32_t result = gtq::await_result(q.ticket, q.enqueued_ms, CHANGE_TTL_MS, &done);
        if (result == gtq::RESULT_PENDING) {
            return;
        }
        if (result >= 1) {
            finish(q, REQ_DONE, result);
        } else if (result == OP_TOWN_REBUILD) {
            // The setter and its undo ran: wait before the next try, and stop after TOWN_TRIES.
            if (++q.town_fails >= TOWN_TRIES) {
                finish(q, REQ_REFUSED, result);
            } else {
                q.state = REQ_WAITING;
                q.last_result = result;
                q.retry_ms = now + TOWN_RETRY_MS;
            }
        } else if (transient(result)) {
            q.state = REQ_WAITING;
            q.last_result = result;
        } else {
            finish(q, REQ_REFUSED, result);
        }
        if (q.next_target != NO_TARGET) {
            const uint8_t next = q.next_target;
            q = {};
            q.state = REQ_WAITING;
            q.target = next;
            q.next_target = NO_TARGET;
            q.since_ms = now;
        }
    }
    if (q.state != REQ_WAITING) {
        return;
    }
    if (now - q.since_ms >= REQUEST_WAIT_MS) {
        finish(q, REQ_TIMED_OUT, q.last_result);
        return;
    }
    if (q.retry_ms != 0 && now < q.retry_ms) {
        return;  // backing off after a town rebuild that did not run
    }
    attempt(seat, q, now);
}

// Retire the steering remap once the session adopted its change and the steering record names the new bodies.
void retire(uint64_t now)
{
    if (!remap_pending()) {
        return;
    }
    remap_record m;
    if (!remap_read(&m)) {
        return;
    }
    if (m.seq != g_remap_seen) {
        g_remap_seen = m.seq;
        g_remap_seen_ms = now;
    }
    if (m.head.epoch != session::epoch()) {
        remap_retire(m.seq);  // inert anyway: the detours compare the epoch
        bump(g_counters.remaps_retired);
        return;
    }
    if (static_cast<int32_t>(session::adopted_seq() - m.seq) >= 0) {
        movement::steer_record steer;
        session_record rec;
        bool confirmed = movement::read(&steer) && session::read(&rec) && steer.head.epoch == m.head.epoch;
        for (uint32_t k = 0; k < movement::SLOTS && confirmed; ++k) {
            if ((m.mask & (1u << k)) == 0) {
                continue;
            }
            const bool steers = (rec.seat[k + 1].flags & session::SEATF_STEERS) != 0;
            const bool named = (steer.active & (1u << k)) != 0 && steer.slot[k].actor == m.slot[k].actor &&
                               (steer.slot[k].flags & movement::ENTRY_ACTIVE) != 0;
            confirmed = named || !steers;
        }
        if (confirmed) {
            remap_retire(m.seq);
            bump(g_counters.remaps_retired);
            return;
        }
    }
    if (now - g_remap_seen_ms >= REMAP_MAX_MS) {
        remap_retire(m.seq);
        bump(g_counters.remaps_expired);
    }
}

}  // namespace

// ---- boot -------------------------------------------------------------------------------------------------------
bool attach()
{
    const int32_t row = tick::ut01_row();
    const bool ok = row >= 0 && gtq::register_op(OP_PARTY_CHANGE, ILC_SITE_SUBSYS_PARTY, op_change) &&
                    patch::set_after(static_cast<uint32_t>(row), tick_after);
    __atomic_store_n(&g_attached, ok ? 1u : 0u, __ATOMIC_RELEASE);
    return ok;
}

// ---- co-op thread -----------------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms)
{
    (void)now_ms;
    __atomic_store_n(&g_image_base, image_base, __ATOMIC_RELEASE);
    memset(g_req, 0, sizeof(g_req));
    for (uint32_t s = 0; s < SEATS; ++s) {
        g_req[s].next_target = NO_TARGET;
    }
    AcquireSRWLockExclusive(&g_api_lock);
    memcpy(g_api_taken, g_api_seq, sizeof(g_api_taken));  // requests from before the start are not replayed
    ReleaseSRWLockExclusive(&g_api_lock);
    g_seen_init = false;
    g_remap_seen = 0;
    g_remap_seen_ms = 0;
    g_started = true;
}

void step(uint64_t now_ms)
{
    if (!g_started) {
        return;
    }
    bump(g_counters.steps);
    collect(now_ms);
    for (uint32_t s = 0; s < ACTIVE_SEATS; ++s) {
        advance(s, now_ms);
    }
    retire(now_ms);
}

void stop()
{
    g_started = false;
    remap_retire(__atomic_load_n(&g_posted_seq, __ATOMIC_ACQUIRE));
    remap_record m;
    if (remap_read(&m)) {
        remap_retire(m.seq);
    }
}

// ---- any thread -------------------------------------------------------------------------------------------------
void request_change(uint32_t seat, uint32_t member)
{
    if (seat >= ACTIVE_SEATS) {
        return;
    }
    AcquireSRWLockExclusive(&g_api_lock);
    g_api_target[seat] = static_cast<uint8_t>(member <= 0xFF ? member : NO_TARGET);
    if (++g_api_seq[seat] == 0) {
        g_api_seq[seat] = 1;
    }
    ReleaseSRWLockExclusive(&g_api_lock);
}

const counters* stats()
{
    return &g_counters;
}

void test_api_request(uint32_t seat, uint32_t* seq, uint32_t* member)
{
    *seq = 0;
    *member = NO_TARGET;
    if (seat >= ACTIVE_SEATS) {
        return;
    }
    AcquireSRWLockShared(&g_api_lock);
    *seq = g_api_seq[seat];
    *member = g_api_target[seat];
    ReleaseSRWLockShared(&g_api_lock);
}

// ---- game thread ------------------------------------------------------------------------------------------------
void tick_before()
{
    watch(true);
}

bool after_wanted()
{
    return __atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) != 0 && patch::current_mode() == patch::MODE_LIVE &&
           party_live();
}

void tick_after(patch::ctx* c, uint32_t epoch)
{
    (void)c;      // the stock tick's results stay as they are
    (void)epoch;
    bump(g_counters.watcher_after);
    watch(false);
}

int32_t op_change(const gtq::job& j)
{
    const job_args a = decode(j);
    int32_t result = CHANGE_APPLIED;
    session_record rec;
    game_reader r(base());
    party_read p;
    int32_t pending_area = 0;
    uint32_t lock = 0;
    uint32_t frontend = 0;
    uint8_t pending_handle = 0;
    uint32_t location = 0;
    uint8_t location_flags = 0;
    change_plan plan;
    towns::town_state town;
    memset(&town, 0, sizeof(town));
    uint8_t hidden = 0;
    bool town_take = false;
    if (a.seat >= ACTIVE_SEATS || a.kind < KIND_LEADER || a.kind > KIND_TAKE_FREE || a.target_slot >= SLOTS) {
        result = OP_BAD_ARGS;
    } else if (!session::read(&rec) || rec.head.epoch != j.epoch || (rec.head.flags & session::GATE_WORLD) == 0) {
        result = OP_NO_WORLD;
    } else if (!r.u32(r.global(pf::PENDING_AREA), reinterpret_cast<uint32_t*>(&pending_area)) ||
               !r.u32(r.global(pf::LOCK_MASK), &lock) || !r.u32(r.global(pf::FRONTEND_OPEN), &frontend) ||
               !r.u8(r.global(pf::PENDING_HANDLE_FLAG), &pending_handle) ||
               !r.u32(r.global(pf::LOCATION_ID), &location) || !r.u8(r.global(pf::LOCATION_FLAGS), &location_flags) ||
               !read_party(r, &p)) {
        result = OP_UNREADABLE;
    } else if (pending_area != -1 || (lock & pf::LOCK_TRANSITION) != 0) {
        result = OP_AREA_PENDING;
    } else if ((lock & pf::LOCK_ROSTER_EDIT) != 0) {
        result = OP_ROSTER_EDIT;
    } else if (frontend != 0) {
        result = OP_NOT_FIELD;
    } else if (pending_handle != 0) {
        result = OP_APPLY_PENDING;
    } else if ((p.mode_bits & pf::MODE_BLOCKING) != 0 || esper_out(p)) {
        result = OP_MODE;
    } else if (location != a.location ||
               ((location_flags & pf::LOCATION_RELOAD) != 0) != ((a.flags & JOB_RELOAD) != 0)) {
        result = OP_LOCATION_MOVED;
    } else {
        // The same change, planned again on what the game shows now.
        note_seat_bodies(r, rec, p.view);
        hidden = extend_town(r, p, &town);
        town_take = (hidden & (1u << a.target_slot)) != 0;
        if ((a.kind == KIND_LEADER || a.kind == KIND_TAKE_FREE) && !town_take) {
            if (p.view.current[a.target_slot] == a.target &&
                body_holds(r, a.target_slot, a.actor, a.handle, a.phys)) {
                note_body(r, p.view, a.target_slot, a.actor);
            }
        }
        const int32_t refusal = plan_change(seats_of(rec), p.view, a.seat, a.target, &plan);
        if (refusal != CHANGE_OK) {
            result = refusal;
        } else if (plan.kind == KIND_ALREADY) {
            result = CHANGE_ALREADY;
        } else if (plan.kind != a.kind || plan.released != a.released || plan.target_slot != a.target_slot ||
                   town_take != ((a.flags & JOB_TOWN) != 0)) {
            result = OP_STALE;  // includes a body that appeared (or went) since the plan: plan again
        } else if (town_take && plan.kind != KIND_LEADER && plan.kind != KIND_TAKE_FREE) {
            result = REFUSE_NOT_IN_PARTY;
        } else if (town_take && (town.task_busy != 0 || rec.phase != session::PHASE_FIELD ||
                                 (rec.facts & session::FACT_CONTROL) == 0)) {
            result = OP_TOWN_BUSY;  // the rebuild below would be refused: change nothing
        } else {
            const ready_fn ready = g_test_ready != nullptr ? g_test_ready
                                                           : reinterpret_cast<ready_fn>(at(pf::FIELD_READY));
            if (ready() == 0) {
                result = OP_NOT_FIELD;
            }
        }
    }
    if (result != CHANGE_APPLIED) {
        bump(result == CHANGE_ALREADY ? g_counters.already : g_counters.refused);
        return result;
    }

    // Apply, all in this callback.
    uint32_t leader = p.view.leader;
    const uint32_t previous_leader = leader;
    const setter_fn setter = g_test_setter != nullptr ? g_test_setter
                                                      : reinterpret_cast<setter_fn>(at(pf::LEADER_SETTER));
    if (plan.new_leader != MEMBER_NONE) {
        setter(static_cast<uint8_t>(plan.new_leader));
        bump(g_counters.leader_writes);
        uint32_t written = 0;
        if (!r.u32(p.block + pf::LEADER, &written) || written != plan.new_leader) {
            g_watch.known = 0;  // whatever happened, the watcher starts over from what it reads next
            bump(g_counters.refused);
            return OP_SETTER_FAILED;
        }
        leader = written;
        g_watch = {leader, rec.head.epoch, 1};  // our own write: never seen as an external change
    }
    body_ref bodies[SEATS + 1];
    uint32_t n = 0;
    for (uint32_t s = 0; s < ACTIVE_SEATS; ++s) {
        const seat_entry& e = rec.seat[s];
        if (seat_in(rec, s) && e.member != MEMBER_NONE && (e.flags & session::SEATF_ACTOR) != 0) {
            bodies[n++] = {e.member, e.actor, e.handle, e.phys};
        }
    }
    if (town_take) {
        // The member has no body yet. TW-01 keeps the leader on its own; a guest's new member is kept through the
        // pending word until the session names it. The rebuild binds the body now (0x37CA80 -> layout builder,
        // whose tail refreshes the controlled actor before the frame ends).
        if (plan.kind == KIND_TAKE_FREE) {
            towns::allow_pending(rec.head.epoch, a.target, patch::now_ms());
        }
        gtq::job rebuild;
        memset(&rebuild, 0, sizeof(rebuild));
        rebuild.op = towns::OP_TOWN_REBUILD;
        rebuild.epoch = rec.head.epoch;
        rebuild.generation = rec.head.generation;
        rebuild.ttl_ms = towns::REBUILD_TTL_MS;
        if (towns::op_rebuild(rebuild) < 1) {
            if (plan.kind == KIND_TAKE_FREE) {
                towns::clear_pending();
            }
            if (plan.new_leader != MEMBER_NONE) {
                setter(static_cast<uint8_t>(previous_leader));  // back to the leader whose body exists
                uint32_t back = 0;
                if (r.u32(p.block + pf::LEADER, &back) && back == previous_leader) {
                    g_watch = {previous_leader, rec.head.epoch, 1};
                } else {
                    g_watch.known = 0;  // the undo did not read back: the watcher starts over from what it reads
                }
            }
            bump(g_counters.refused);
            return OP_TOWN_REBUILD;
        }
        bump(g_counters.town_takes);
        session::party_actors bound;
        session::scan_party(r, &bound);
        const session::party_actor& b = bound.slot[a.target_slot];
        if (b.actor != 0 && body_holds(r, a.target_slot, b.actor, b.handle, b.phys)) {
            bodies[n++] = {a.target, b.actor, b.handle, b.phys};
        }  // else: the guest's slot steers nothing until the co-op thread has the body (entry_for)
    } else {
        bodies[n++] = {a.target, a.actor, a.handle, a.phys};
    }
    commit(r, rec, plan.mask, plan.after, leader, bodies, n);
    bump(g_counters.applied);
    return CHANGE_APPLIED;
}

// ---- host tests -------------------------------------------------------------------------------------------------
const volatile uint32_t* test_posted_seq()
{
    return &g_posted_seq;
}

void test_set_game_fns(setter_fn setter, ready_fn field_ready)
{
    g_test_setter = setter;
    g_test_ready = field_ready;
}

void test_set_image_base(uintptr_t image_base)
{
    __atomic_store_n(&g_image_base, image_base, __ATOMIC_RELEASE);
}

void test_force_live(int32_t forced)
{
    __atomic_store_n(&g_force_live, forced, __ATOMIC_RELAXED);
}

void test_debug(debug_view* out)
{
    memset(out, 0, sizeof(*out));
    for (uint32_t s = 0; s < SEATS; ++s) {
        out->seat[s].state = g_req[s].state;
        out->seat[s].target = g_req[s].target;
        out->seat[s].attempts = g_req[s].attempts;
        out->seat[s].town_fails = g_req[s].town_fails;
        out->seat[s].last_result = g_req[s].last_result;
        out->seat[s].ticket = g_req[s].ticket;
    }
    out->watcher_leader = g_watch.known ? g_watch.leader : 0xFFFFu;
    out->watcher_epoch = g_watch.epoch;
    remap_record m;
    out->remap_seq = remap_read(&m) ? m.seq : 0;
    out->remap_retired = remap_retired();
    out->adopted_seq = session::adopted_seq();
    out->posted_seq = __atomic_load_n(&g_posted_seq, __ATOMIC_ACQUIRE);
}

void test_reset()
{
    memset(&g_counters, 0, sizeof(g_counters));
    memset(g_req, 0, sizeof(g_req));
    for (uint32_t s = 0; s < SEATS; ++s) {
        g_req[s].next_target = NO_TARGET;
    }
    memset(g_seen_move, 0, sizeof(g_seen_move));
    g_seen_init = false;
    AcquireSRWLockExclusive(&g_api_lock);
    memset(g_api_seq, 0, sizeof(g_api_seq));
    memset(g_api_target, 0, sizeof(g_api_target));
    ReleaseSRWLockExclusive(&g_api_lock);
    memset(g_api_taken, 0, sizeof(g_api_taken));
    g_remap_seen = 0;
    g_remap_seen_ms = 0;
    g_started = false;
    g_change_seq = 0;
    __atomic_store_n(&g_posted_seq, 0u, __ATOMIC_RELEASE);
    g_watch = {0xFFFFu, 0, 0};
    g_test_setter = nullptr;
    g_test_ready = nullptr;
    __atomic_store_n(&g_attached, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_force_live, -1, __ATOMIC_RELAXED);
    __atomic_store_n(&g_image_base, pf::IMAGE_VA, __ATOMIC_RELEASE);
    remap_test_reset();
}

}  // namespace ilc::party
