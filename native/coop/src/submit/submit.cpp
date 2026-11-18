// Ivalice Legacy co-op module: the co-op-thread side of battle-menu requests (see submit.h).
#include "submit/submit.h"

#include <string.h>

#include "menu/menu_facts.h"
#include "patch/patch_engine.h"
#include "patch/site_table.h"
#include "session/reader.h"
#include "session/session.h"
#include "session/world.h"
#include "submit/submit_detail.h"
#include "submit/submit_facts.h"
#include "targets/live.h"

namespace ilc::submit {

namespace detail {

counters g_counters;
volatile uintptr_t g_image_base = facts::IMAGE_VA;
volatile int32_t g_enabled = 0;
volatile int32_t g_forced = -1;
volatile uint64_t g_node[SEATS];          // indicator node per seat (game thread writes, co-op thread reads)
volatile uint32_t g_owner = OWNER_NONE;   // summon owner (co-op thread writes, game thread reads)
const game_fns* g_test_fns = nullptr;

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

uintptr_t base() { return __atomic_load_n(&g_image_base, __ATOMIC_ACQUIRE); }

bool is_enabled()
{
    const int32_t forced = __atomic_load_n(&g_forced, __ATOMIC_RELAXED);
    return forced >= 0 ? forced != 0 : __atomic_load_n(&g_enabled, __ATOMIC_ACQUIRE) != 0;
}

uint64_t pack_issuer(uint32_t handle, uint32_t member, uint32_t action)
{
    return uint64_t{handle} | (uint64_t{member & 0xFFFF} << 32) | (uint64_t{action & 0xFFFF} << 48);
}

uint64_t pack_target(const action_request& rq)
{
    return uint64_t{rq.target} | (uint64_t{rq.kind} << 32) | (uint64_t{rq.flags} << 40) |
           (uint64_t{rq.reserve_member} << 48);
}

}  // namespace detail

namespace {

using namespace detail;
using session::session_record;

struct slot {
    uint8_t state;
    uint8_t kind;
    uint8_t flags;
    uint8_t lost;
    uint16_t action;
    uint16_t member;
    uint32_t ticket;
    uint32_t epoch;
    uint32_t target;
    uint32_t handle;
    uint64_t generation;
    uint64_t enqueued_ms;
    uint64_t deadline_ms;
    uint32_t ttl_ms;
    int32_t last_result;
    uint32_t completed;
    uint32_t quarantined;
    uint32_t released;
    uint32_t late_result;
    menu::seat_menu* menu;    // a Gambits request: where its result goes
};

struct indicator {
    indicator_view v;
};

struct sound_queue {
    uint32_t pending;         // sound id waiting (0 none)
    uint32_t ticket;          // the sound job in flight (0 none)
    uint64_t enqueued_ms;
};

slot g_slot[SEATS];
indicator g_ind[SEATS];
sound_queue g_sound;
summon_view g_summon;
uint8_t g_seat_sounds[SEATS] = {1, 1, 1, 1};
bool g_started = false;

bool seat_in(const session_record& rec, uint32_t s)
{
    return s < session::ACTIVE_SEATS && (s == 0 || (rec.seat[s].flags & session::SEATF_IN_SESSION) != 0);
}

// The seat's bound character, as the session published it this beat.
bool issuer_of(const session_record& rec, uint32_t seat, const session::seat_entry** out)
{
    if (!seat_in(rec, seat)) {
        return false;
    }
    const session::seat_entry& e = rec.seat[seat];
    if ((e.flags & session::SEATF_ACTOR) == 0 || e.member == facts::MEMBER_NONE || e.handle == 0 || e.actor == 0 ||
        (seat != 0 && (e.flags & session::SEATF_MANUAL) == 0)) {
        return false;
    }
    *out = &e;
    return true;
}

int32_t map_push(int32_t status)
{
    switch (status) {
    case gtq::PUSH_OK: return SUB_QUEUED;
    case gtq::PUSH_FULL: return SUB_FULL;
    case gtq::PUSH_NOT_LIVE: return SUB_NOT_LIVE;
    case gtq::PUSH_NO_WORLD:
    case gtq::PUSH_BAD_EPOCH: return SUB_NO_WORLD;
    default: return SUB_REFUSED;
    }
}

int32_t enqueue(uint32_t seat, uint16_t op, uint8_t kind, uint8_t flags, uint16_t action, uint16_t member,
                uint32_t handle, uint32_t target, const session_record& rec, const uint64_t args[gtq::ARG_COUNT],
                uint64_t now)
{
    uint32_t ticket = 0;
    const int32_t status = gtq::push(op, static_cast<uint8_t>(seat), rec.head.epoch, rec.head.generation,
                                     ACTION_TTL_MS, args, &ticket);
    if (status != gtq::PUSH_OK) {
        bump(g_counters.refused);
        return map_push(status);
    }
    slot& s = g_slot[seat];
    s.state = SLOT_QUEUED;
    s.kind = kind;
    s.flags = flags;
    s.lost = 0;
    s.action = action;
    s.member = member;
    s.handle = handle;
    s.target = target;
    s.ticket = ticket;
    s.epoch = rec.head.epoch;
    s.generation = rec.head.generation;
    s.enqueued_ms = now;
    s.ttl_ms = ACTION_TTL_MS;
    s.deadline_ms = now + WAIT_MS;
    bump(g_counters.queued);
    return SUB_QUEUED;
}

int32_t precheck(uint32_t seat)
{
    bump(g_counters.requests);
    if (seat >= session::ACTIVE_SEATS || seat >= SEATS) {
        bump(g_counters.refused);
        return SUB_NOT_SEAT;
    }
    if (!is_enabled()) {
        bump(g_counters.refused);
        return SUB_NOT_LIVE;
    }
    if (g_slot[seat].state == SLOT_QUEUED) {
        bump(g_counters.refused);
        return SUB_BUSY;
    }
    if (g_slot[seat].state == SLOT_QUARANTINED) {
        bump(g_counters.refused);
        return SUB_QUARANTINED;
    }
    return 0;
}

// ---- the combat indicator scheduler ---------------------------------------------------------------------------------
void indicator_schedule(uint32_t seat, uint32_t source, uint32_t target, uint64_t now)
{
    indicator_view& v = g_ind[seat].v;
    // An attempt still in flight for an earlier pair finishes first; its node then fails the pair check and the new
    // pair is tried.
    const uint32_t ticket = v.in_flight ? v.ticket : 0;
    const uint8_t in_flight = v.in_flight;
    memset(&v, 0, sizeof(v));
    v.active = 1;
    v.in_flight = in_flight;
    v.ticket = ticket;
    v.source = source;
    v.target = target;
    v.next_try_ms = source == target ? now : now + INDICATOR_FIRST_MS;
    v.deadline_ms = now + INDICATOR_GIVE_UP_MS;
    __atomic_store_n(&g_node[seat], 0ull, __ATOMIC_RELEASE);
}

void indicator_finish(indicator_view& v, int32_t outcome)
{
    v.active = 0;
    v.node = 0;
    v.last = outcome;
}

void indicator_step(uint32_t seat, session::game_reader& r, const session_record& rec, uint64_t now)
{
    indicator_view& v = g_ind[seat].v;
    if (v.in_flight) {
        gtq::completion done;
        const int32_t result = gtq::await_result(v.ticket, v.attempt_ms, INDICATOR_TTL_MS, &done);
        if (result == gtq::RESULT_PENDING) {
            return;
        }
        v.in_flight = 0;
        v.ticket = 0;
        if (!v.active) {
            return;
        }
        if (result == OPR_DONE) {
            v.node = __atomic_load_n(&g_node[seat], __ATOMIC_ACQUIRE);
            ++v.attempts;
            v.attempt_ms = now;
            if (v.source == v.target) {
                bump(g_counters.indicators_shown);
                indicator_finish(v, 1);
            }
        } else {
            v.next_try_ms = now + INDICATOR_RETRY_MS;
        }
        return;
    }
    if (!v.active) {
        return;
    }
    if (targets::live_actor(r, v.source, false) == 0 || targets::live_actor(r, v.target, true) == 0) {
        bump(g_counters.indicators_cancelled);
        indicator_finish(v, 3);
        return;
    }
    if (now >= v.deadline_ms) {
        bump(g_counters.indicators_expired);
        indicator_finish(v, 2);
        return;
    }
    if (v.node != 0) {
        uint32_t source = 0;
        uint32_t target = 0;
        uint32_t flags = 0;
        uint16_t state = 0;
        const bool read = r.u32(v.node + facts::NODE_SOURCE, &source) && r.u32(v.node + facts::NODE_TARGET, &target) &&
                          r.u32(v.node + facts::NODE_FLAGS, &flags) && r.u16(v.node + facts::NODE_STATE, &state);
        if (read && source == v.source && target == v.target) {
            if (state == 2 && (flags & 1)) {
                bump(g_counters.indicators_shown);
                indicator_finish(v, 1);
                return;
            }
            if (state == 1 && now - v.attempt_ms < INDICATOR_GRACE_MS) {
                return;
            }
        }
        v.node = 0;
        v.next_try_ms = now + INDICATOR_RETRY_MS;
        return;
    }
    if (now < v.next_try_ms) {
        return;
    }
    uint64_t args[gtq::ARG_COUNT] = {uint64_t{v.source} | (uint64_t{v.target} << 32), seat, 0, 0};
    uint32_t ticket = 0;
    __atomic_store_n(&g_node[seat], 0ull, __ATOMIC_RELEASE);
    if (gtq::push(OP_MENU_INDICATOR, static_cast<uint8_t>(seat), rec.head.epoch, rec.head.generation,
                  INDICATOR_TTL_MS, args, &ticket) == gtq::PUSH_OK) {
        v.in_flight = 1;
        v.ticket = ticket;
        v.attempt_ms = now;
    } else {
        v.next_try_ms = now + INDICATOR_RETRY_MS;
    }
}

// ---- results ------------------------------------------------------------------------------------------------------
void deliver(uint32_t seat, slot& s, int32_t result, uint64_t now)
{
    s.last_result = result;
    ++s.completed;
    bump(g_counters.completed);
    if (s.kind == RK_GAMBITS) {
        int32_t code = 0;
        uint32_t final_enabled = 0xFF;
        gambit_outcome(result, &code, &final_enabled);
        if (s.menu != nullptr) {
            menu::gambit_complete(&s.menu->gambits, code, final_enabled);
            if (s.menu->gambits.sound != 0) {
                play(seat, s.menu->gambits.sound);
            }
        }
        s.menu = nullptr;
        return;
    }
    if (result < OPR_DONE || (s.kind != RK_ACTION && s.kind != RK_RESERVE)) {
        return;
    }
    if (s.action >= facts::ESPER_FIRST && s.action <= facts::ESPER_LAST && s.member < 6) {
        g_summon.pending = static_cast<uint8_t>(s.member);
        g_summon.pending_until_ms = now + SUMMON_OWNER_WINDOW_MS;
    }
    if (s.kind == RK_ACTION && (s.flags & RF_INDICATOR) && s.target != 0) {
        indicator_schedule(seat, s.handle, s.target, now);
    }
}

void release_late(slot& s, int32_t result)
{
    s.late_result = static_cast<uint32_t>(result);
    s.last_result = result;
    ++s.released;
    bump(g_counters.released);
    if (s.kind == RK_GAMBITS && s.menu != nullptr) {
        // Too late to trust: the record is read again at the next refresh.
        menu::gambit_complete(&s.menu->gambits, menu::GB_STALE_SEQUENCE, 0xFF);
    }
    s.menu = nullptr;
    s.state = SLOT_IDLE;
}

void advance(uint32_t seat, const session_record& rec, bool rec_ok, uint64_t now)
{
    slot& s = g_slot[seat];
    if (s.state == SLOT_QUEUED) {
        gtq::completion done;
        const int32_t result = gtq::result(s.ticket, &done);
        if (result == gtq::RESULT_PENDING) {
            if (now >= s.deadline_ms) {
                s.state = SLOT_QUARANTINED;
                ++s.quarantined;
                bump(g_counters.quarantined);
            }
            return;
        }
        if (result == gtq::RESULT_LOST) {
            s.state = SLOT_QUARANTINED;
            s.lost = 1;
            ++s.quarantined;
            bump(g_counters.quarantined);
            bump(g_counters.lost);
            return;
        }
        s.state = SLOT_IDLE;
        deliver(seat, s, result, now);
        return;
    }
    if (s.state != SLOT_QUARANTINED) {
        return;
    }
    if (s.lost) {
        // A lost result means a newer ticket's completion took its place. The queue is FIFO (drain and reap alike),
        // so this job had already finished: nothing can still act on it. The slot is released once its own deadline
        // (ttl + the queue's margin) has passed, as for any late result, or at once by a new epoch.
        if (now >= s.deadline_ms || (rec_ok && rec.head.epoch != s.epoch)) {
            release_late(s, gtq::RESULT_LOST);
        }
        return;
    }
    gtq::completion done;
    const int32_t result = gtq::await_result(s.ticket, s.enqueued_ms, s.ttl_ms, &done);
    if (result == gtq::RESULT_PENDING) {
        return;
    }
    if (result == gtq::RESULT_LOST) {
        s.lost = 1;
        bump(g_counters.lost);
        return;
    }
    release_late(s, result);
}

void sound_step(const session_record& rec, uint64_t now)
{
    if (g_sound.ticket != 0) {
        gtq::completion done;
        const int32_t result = gtq::await_result(g_sound.ticket, g_sound.enqueued_ms, SOUND_TTL_MS, &done);
        if (result == gtq::RESULT_PENDING) {
            return;
        }
        g_sound.ticket = 0;
        if (result >= OPR_DONE) {
            bump(g_counters.sounds);
        }
    }
    if (g_sound.pending == 0) {
        return;
    }
    const uint64_t args[gtq::ARG_COUNT] = {g_sound.pending, 0, 0, 0};
    g_sound.pending = 0;
    uint32_t ticket = 0;
    if (gtq::push(OP_MENU_SOUND, 0xFF, rec.head.epoch, rec.head.generation, SOUND_TTL_MS, args, &ticket) ==
        gtq::PUSH_OK) {
        g_sound.ticket = ticket;
        g_sound.enqueued_ms = now;
    } else {
        bump(g_counters.sounds_dropped);
    }
}

void summon_step(session::game_reader& r, const session_record& rec, uint64_t now)
{
    uintptr_t battle = 0;
    uint16_t current[4];
    if (!r.ptr(r.global(facts::BATTLE_STATE_PTR), &battle) || battle == 0 ||
        !r.read(battle + facts::LAYOUT_TABLE + facts::LAYOUT_CURRENT * facts::LAYOUT_STRIDE, current,
                sizeof(current))) {
        return;  // keep what we had until the layout reads again
    }
    bool active = false;
    for (uint16_t m : current) {
        active = active || (m >= facts::MEMBER_ESPER_FIRST && m <= facts::MEMBER_ESPER_LAST);
    }
    summon_update(&g_summon, active, current, rec.leader, now);
    __atomic_store_n(&g_owner, uint32_t{g_summon.owner}, __ATOMIC_RELEASE);
}

}  // namespace

// ---- pure --------------------------------------------------------------------------------------------------------
void summon_update(summon_view* s, bool active, const uint16_t current[4], uint32_t leader, uint64_t now)
{
    const bool pending_valid = s->pending < 6 && now <= s->pending_until_ms;
    if (active) {
        if (!s->active) {
            if (pending_valid) {
                s->owner = s->pending;
                s->source = 1;
            } else {
                s->owner = OWNER_NONE;
                s->source = 2;
            }
        }
        if (s->owner >= 6) {
            // The fallback: the native leader when it is a regular, else the first regular in the party.
            s->owner = OWNER_NONE;
            s->source = 2;
            if (leader < 6) {
                s->owner = static_cast<uint8_t>(leader);
            } else {
                for (uint32_t k = 0; k < 4; ++k) {
                    if (current[k] < 6) {
                        s->owner = static_cast<uint8_t>(current[k]);
                        break;
                    }
                }
            }
        }
        s->pending = OWNER_NONE;
        s->pending_until_ms = 0;
    } else {
        if (s->active || now > s->pending_until_ms) {
            s->pending = OWNER_NONE;
            s->pending_until_ms = 0;
        }
        s->owner = OWNER_NONE;
        s->source = 0;
    }
    s->active = active ? 1 : 0;
}

void gambit_outcome(int32_t result, int32_t* code, uint32_t* final_enabled)
{
    *final_enabled = 0xFF;
    if (result == OPR_GAMBITS_ON || result == OPR_GAMBITS_OFF) {
        *code = menu::GB_APPLIED;
        *final_enabled = result == OPR_GAMBITS_ON ? 1u : 0u;
        return;
    }
    const int32_t c = REJ_GAMBIT - result;  // result = REJ_GAMBIT - code
    if (c >= menu::GB_STALE_SEQUENCE && c <= menu::GB_BLOCKED_STATUS) {
        *code = c;
        return;
    }
    *code = menu::GB_STALE_SEQUENCE;  // the queue rejected it, or the world changed: nothing was decided
}

uint32_t sound_for(uint8_t event_kind)
{
    switch (event_kind) {
    case menu::EV_CURSOR:
    case menu::EV_TARGET_MOVED: return 1;
    case menu::EV_TARGET_GROUP: return 0x0A;
    case menu::EV_PAGE_OPENED:
    case menu::EV_TARGET_OPENED:
    case menu::EV_SUBMIT: return 2;
    case menu::EV_PAGE_CLOSED:
    case menu::EV_TARGET_CLOSED:
    case menu::EV_CLOSED: return 3;
    case menu::EV_OPENED: return 4;
    default: return 0;
    }
}

// ---- co-op thread ------------------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms)
{
    (void)now_ms;
    __atomic_store_n(&g_image_base, image_base, __ATOMIC_RELEASE);
    memset(g_slot, 0, sizeof(g_slot));
    memset(g_ind, 0, sizeof(g_ind));
    memset(&g_sound, 0, sizeof(g_sound));
    memset(&g_summon, 0, sizeof(g_summon));
    g_summon.owner = OWNER_NONE;
    g_summon.pending = OWNER_NONE;
    __atomic_store_n(&g_owner, uint32_t{OWNER_NONE}, __ATOMIC_RELEASE);
    g_started = true;
}

void configure_from_settings(const ilc_settings& s)
{
    for (uint32_t k = 0; k < SEATS && k < ILC_SEATS; ++k) {
        g_seat_sounds[k] = s.seats[k].seat_ui_sounds ? 1 : 0;
    }
}

void step(uint64_t now_ms)
{
    if (!g_started) {
        return;
    }
    session_record rec;
    const bool rec_ok = session::read(&rec);
    session::game_reader r(base());
    if (rec_ok) {
        summon_step(r, rec, now_ms);
    }
    for (uint32_t s = 0; s < SEATS; ++s) {
        advance(s, rec, rec_ok, now_ms);
        if (rec_ok) {
            indicator_step(s, r, rec, now_ms);
        }
    }
    if (rec_ok) {
        sound_step(rec, now_ms);
    }
}

void stop()
{
    g_started = false;
}

int32_t request(uint32_t seat, const action_request& rq, uint64_t now_ms)
{
    const int32_t pre = precheck(seat);
    if (pre != 0) {
        return pre;
    }
    if ((rq.kind != RK_ACTION && rq.kind != RK_RESERVE) || (rq.kind == RK_ACTION && rq.target == 0) ||
        (rq.kind == RK_RESERVE && rq.reserve_member >= facts::MEMBER_LIMIT) || rq.action == facts::ACTION_DISMISS) {
        bump(g_counters.refused);
        return SUB_BAD_REQUEST;
    }
    session_record rec;
    const session::seat_entry* me = nullptr;
    if (!session::read(&rec) || !issuer_of(rec, seat, &me)) {
        bump(g_counters.refused);
        return SUB_NO_ISSUER;
    }
    uint64_t args[gtq::ARG_COUNT];
    args[0] = pack_issuer(me->handle, me->member, rq.action);
    args[1] = me->actor;
    args[2] = pack_target(rq);
    args[3] = rq.kind == RK_ACTION && rq.target != me->handle ? rq.target_actor : 0;
    return enqueue(seat, OP_MENU_ACTION, rq.kind, rq.flags, rq.action, me->member, me->handle,
                   rq.kind == RK_ACTION ? rq.target : 0, rec, args, now_ms);
}

int32_t request_dismiss(uint32_t seat, uint64_t now_ms)
{
    const int32_t pre = precheck(seat);
    if (pre != 0) {
        return pre;
    }
    session_record rec;
    const session::seat_entry* me = nullptr;
    if (!session::read(&rec) || !issuer_of(rec, seat, &me)) {
        bump(g_counters.refused);
        return SUB_NO_ISSUER;
    }
    if (me->member != summon_owner()) {
        bump(g_counters.refused);
        return SUB_NOT_OWNER;
    }
    uint64_t args[gtq::ARG_COUNT] = {pack_issuer(me->handle, me->member, facts::ACTION_DISMISS), me->actor,
                                     uint64_t{RK_DISMISS} << 32, 0};
    return enqueue(seat, OP_MENU_DISMISS, RK_DISMISS, 0, facts::ACTION_DISMISS, me->member, me->handle, 0, rec, args,
                   now_ms);
}

int32_t request_gambits(uint32_t seat, menu::seat_menu* sm, uint64_t now_ms)
{
    const int32_t pre = precheck(seat);
    if (pre != 0) {
        return pre;
    }
    session_record rec;
    const session::seat_entry* me = nullptr;
    if (sm == nullptr || !session::read(&rec) || !issuer_of(rec, seat, &me)) {
        bump(g_counters.refused);
        return SUB_NO_ISSUER;
    }
    session::game_reader r(base());
    menu::gambit_identity id = {};
    id.member = me->member;
    id.handle = me->handle;
    id.actor = me->actor;
    id.physics = me->phys;
    uintptr_t record = 0;
    uintptr_t block = 0;
    uint32_t status = 0;
    r.ptr(me->actor + facts::ACTOR_BATTLE, &record);
    r.ptr(r.global(facts::BATTLE_STATE_PTR), &block);
    id.record = record;
    id.party_block = block;
    if (record != 0) {
        session::battle_status(r, record, &status);
    }
    const int32_t model = menu::gambit_request(&sm->gambits, id, status);
    if (model != menu::GB_PENDING) {
        bump(g_counters.refused);
        return SUB_GAMBITS_REFUSED;
    }
    uint64_t args[gtq::ARG_COUNT] = {pack_issuer(me->handle, me->member, 0), me->actor, me->phys, record};
    const int32_t status_push = enqueue(seat, OP_MENU_GAMBITS, RK_GAMBITS, 0, 0, me->member, me->handle, 0, rec,
                                        args, now_ms);
    if (status_push != SUB_QUEUED) {
        menu::gambit_complete(&sm->gambits, menu::GB_STALE_SEQUENCE, 0xFF);
        return status_push;
    }
    g_slot[seat].menu = sm;
    return SUB_QUEUED;
}

int32_t handle_event(uint32_t seat, menu::seat_menu* sm, const menu::menu_event& e,
                     const targets::candidate_list& shown, uint64_t now_ms)
{
    if (seat < SEATS) {
        const uint32_t sound = sound_for(e.kind);
        if (sound != 0) {
            play(seat, sound);
        }
    }
    if (e.kind == menu::EV_TOGGLE_GAMBITS) {
        return request_gambits(seat, sm, now_ms);
    }
    if (e.kind != menu::EV_SUBMIT || sm == nullptr) {
        return 0;
    }
    if (e.action == facts::ACTION_DISMISS) {
        return request_dismiss(seat, now_ms);
    }
    // The submitted row: its page says whether the game's availability check applies (magicks and technicks).
    const menu::catalog& c = sm->model.pages;
    uint8_t flags = 0;
    bool listed = false;
    for (uint32_t page = 0; page < menu::PG_COUNT && !listed; ++page) {
        for (uint32_t k = 0; k < menu::page_count(c, page); ++k) {
            const menu::entry& en = c.entries[c.pages[page].first + k];
            if (en.kind == menu::EK_ACTION && en.action == e.action && (en.flags & menu::EF_ENABLED)) {
                listed = true;
                if (page >= menu::PG_WHITE && page <= menu::PG_TECHNICKS) {
                    flags |= RF_LICENSED;
                }
                if ((en.flags & menu::EF_REVIVE) != 0) {
                    flags |= RF_REVIVE;
                }
                break;
            }
        }
    }
    if (!listed) {
        bump(g_counters.refused);
        return SUB_BAD_REQUEST;
    }
    action_request rq = {};
    rq.action = e.action;
    session_record rec;
    const session::seat_entry* me = nullptr;
    if (!session::read(&rec) || !issuer_of(rec, seat, &me)) {
        bump(g_counters.refused);
        return SUB_NO_ISSUER;
    }
    if (e.group >= menu::TG_COUNT) {
        rq.kind = RK_ACTION;  // the character itself
        rq.target = me->handle;
        rq.flags = static_cast<uint8_t>(flags | RF_INDICATOR);
    } else {
        const targets::candidate* t = targets::selectable_at(shown, e.group, e.index);
        if (t == nullptr) {
            bump(g_counters.refused);
            return SUB_BAD_REQUEST;
        }
        if (t->group == menu::TG_RESERVE) {
            rq.kind = RK_RESERVE;
            rq.reserve_member = t->member;
            rq.flags = flags;
        } else {
            rq.kind = RK_ACTION;
            rq.target = t->handle;
            rq.target_actor = t->actor;
            rq.flags = static_cast<uint8_t>(flags | RF_INDICATOR |
                                            (t->group == menu::TG_FOES || t->group == menu::TG_NEUTRALS ? RF_WORLD : 0));
        }
    }
    return request(seat, rq, now_ms);
}

void play(uint32_t seat, uint32_t sound_id)
{
    bool allowed = false;
    for (uint32_t id : facts::SOUND_IDS) {
        allowed = allowed || id == sound_id;
    }
    if (seat >= SEATS || !g_seat_sounds[seat] || !allowed || g_sound.pending != 0) {
        bump(g_counters.sounds_dropped);
        return;
    }
    g_sound.pending = sound_id;
}

bool play_system(uint32_t sound_id)
{
    bool allowed = false;
    for (uint32_t id : facts::SOUND_IDS) {
        allowed = allowed || id == sound_id;
    }
    if (!allowed || g_sound.pending != 0) {
        return false;
    }
    g_sound.pending = sound_id;
    return true;
}

uint32_t summon_owner()
{
    return __atomic_load_n(&g_owner, __ATOMIC_ACQUIRE);
}

const counters* stats()
{
    return &g_counters;
}

void read_slot(uint32_t seat, slot_view* out)
{
    memset(out, 0, sizeof(*out));
    if (seat >= SEATS) {
        return;
    }
    const slot& s = g_slot[seat];
    out->state = s.state;
    out->kind = s.kind;
    out->action = s.action;
    out->ticket = s.ticket;
    out->epoch = s.epoch;
    out->last_result = s.last_result;
    out->generation = s.generation;
    out->enqueued_ms = s.enqueued_ms;
    out->deadline_ms = s.deadline_ms;
    out->completed = s.completed;
    out->quarantined = s.quarantined;
    out->released = s.released;
    out->late_result = s.late_result;
}

void read_indicator(uint32_t seat, indicator_view* out)
{
    if (seat < SEATS) {
        *out = g_ind[seat].v;
    } else {
        memset(out, 0, sizeof(*out));
    }
}

void read_summon(summon_view* out)
{
    *out = g_summon;
}

// ---- host tests --------------------------------------------------------------------------------------------------------
void test_set_game_fns(const game_fns* fns)
{
    g_test_fns = fns;
}

void test_set_image_base(uintptr_t image_base)
{
    __atomic_store_n(&g_image_base, image_base, __ATOMIC_RELEASE);
}

void test_force_enabled(int32_t forced)
{
    __atomic_store_n(&g_forced, forced, __ATOMIC_RELAXED);
}

void test_reset()
{
    memset(&g_counters, 0, sizeof(g_counters));
    memset(g_slot, 0, sizeof(g_slot));
    memset(g_ind, 0, sizeof(g_ind));
    memset(&g_sound, 0, sizeof(g_sound));
    memset(&g_summon, 0, sizeof(g_summon));
    g_summon.owner = OWNER_NONE;
    g_summon.pending = OWNER_NONE;
    for (uint32_t k = 0; k < SEATS; ++k) {
        g_seat_sounds[k] = 1;
        __atomic_store_n(&g_node[k], 0ull, __ATOMIC_RELEASE);
    }
    __atomic_store_n(&g_owner, uint32_t{OWNER_NONE}, __ATOMIC_RELEASE);
    __atomic_store_n(&g_enabled, 0, __ATOMIC_RELEASE);
    __atomic_store_n(&g_forced, -1, __ATOMIC_RELAXED);
    __atomic_store_n(&g_image_base, facts::IMAGE_VA, __ATOMIC_RELEASE);
    g_test_fns = nullptr;
    g_started = false;
}

void test_encode(uint32_t seat, const action_request& rq, uint32_t member, uint32_t handle, uint64_t actor,
                 uint64_t args[gtq::ARG_COUNT])
{
    (void)seat;
    args[0] = pack_issuer(handle, member, rq.action);
    args[1] = actor;
    args[2] = pack_target(rq);
    args[3] = rq.kind == RK_ACTION && rq.target != handle ? rq.target_actor : 0;
}

}  // namespace ilc::submit
