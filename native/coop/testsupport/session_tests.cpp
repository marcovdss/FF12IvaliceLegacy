// Ivalice Legacy co-op module, host tests only: exports for the session core, the edge cursors and the job reaper.
//
// The session rules and the ownership planner are pure functions of plain structs; these exports pass the structs
// through unchanged so the tests can state each rule as a table. The world readers take the image base of a
// synthetic memory image the test builds (real pages in this process, some of them deliberately unreadable).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "input/edge_history.h"
#include "runtime/game_thread.h"
#include "runtime/probe.h"
#include "session/handoff.h"
#include "session/policy.h"
#include "session/session.h"
#include "session/world.h"

using namespace ilc;
using namespace ilc::session;

namespace {

tri as_tri(int32_t v)
{
    return v < 0 ? T_UNKNOWN : (v != 0 ? T_YES : T_NO);
}

// Edge stress: one writer thread, several readers with their own cursors and their own pace. Each reader checks
// every delta against the writer's known pattern: a delta covering a press tick must report the press, a delta
// covering none must not, and consecutive deltas must leave no tick uncovered.
struct stress_reader {
    uint32_t seat;
    uint32_t sleep_every;   // the reader sleeps 1 ms after this many reads (slow readers)
    uint32_t period;
    uint32_t hold;
    uint32_t press_deltas;  // deltas that reported the press bit
    uint32_t mismatches;    // deltas whose press/release bits disagree with the ticks they cover
    uint32_t gaps;          // a delta that did not start right after the previous one
    uint32_t overruns;
    uint32_t reads;
    uint32_t covered;       // ticks covered
};
volatile uint32_t g_stress_done = 0;

bool covers(uint32_t from, uint32_t to, uint32_t period, uint32_t phase)
{
    for (uint32_t t = from; t <= to && t != 0; ++t) {
        if (t >= 2 && t % period == phase) {
            return true;
        }
    }
    return false;
}

DWORD WINAPI stress_read(void* arg)
{
    stress_reader* r = static_cast<stress_reader*>(arg);
    edges::edge_cursor cursor;
    memset(&cursor, 0, sizeof(cursor));
    edges::edge_delta d;
    while (!edges::since(cursor, r->seat, &d)) {
        YieldProcessor();
    }
    uint32_t last_to = d.to_seq;
    for (;;) {
        const bool done = __atomic_load_n(&g_stress_done, __ATOMIC_ACQUIRE) != 0;
        if (edges::since(cursor, r->seat, &d)) {
            ++r->reads;
            if (d.ticks != 0) {
                r->covered += d.to_seq - d.from_seq + 1;
                if (d.overrun) {
                    ++r->overruns;
                } else {
                    if (d.from_seq != last_to + 1) {
                        ++r->gaps;
                    }
                    const bool want_press = covers(d.from_seq, d.to_seq, r->period, 0);
                    const bool want_release = covers(d.from_seq, d.to_seq, r->period, r->hold);
                    if (want_press != ((d.act_pressed & 1u) != 0) || want_release != ((d.act_released & 1u) != 0)) {
                        ++r->mismatches;
                    }
                }
                r->press_deltas += (d.act_pressed & 1u) ? 1u : 0u;
                last_to = d.to_seq;
            }
        }
        if (done) {
            break;
        }
        if (r->sleep_every != 0 && r->reads % r->sleep_every == 0) {
            Sleep(1);
        }
    }
    return 0;
}

}  // namespace

extern "C" {

// ---- layouts ---------------------------------------------------------------------------------------------------
uint32_t ILCT_SesSizes(uint32_t* out, uint32_t max)
{
    const uint32_t sizes[] = {
        sizeof(world_sample), sizeof(party_actor), sizeof(party_actors), sizeof(identity), sizeof(world_view),
        sizeof(steer_facts), sizeof(owner_token), sizeof(follower_sample), sizeof(release_intent), sizeof(ownership),
        sizeof(effect), sizeof(effect_list), sizeof(world_epochs), sizeof(generation_tracker), sizeof(seat_entry),
        sizeof(session_record), sizeof(seat_config), sizeof(debug_view), sizeof(session::counters),
        sizeof(pending_effect), sizeof(edges::edge_cursor), sizeof(edges::edge_delta), sizeof(reacquire),
        sizeof(lifecycle),
    };
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t i = 0; i < n && i < max; ++i) {
        out[i] = sizes[i];
    }
    return n;
}

// ---- pure rules ------------------------------------------------------------------------------------------------
uint32_t ILCT_SesLifecycle(int32_t enabled, int32_t p1_alive, const int8_t* alive, const uint8_t* present,
                           uint32_t guests)
{
    tri a[SEATS];
    bool p[SEATS];
    for (uint32_t g = 0; g < guests && g < SEATS; ++g) {
        a[g] = as_tri(alive[g]);
        p[g] = present[g] != 0;
    }
    const lifecycle l = lifecycle_gates(enabled != 0, as_tri(p1_alive), a, p, guests);
    return l.logical | (l.movement << 1) | (l.shared_camera << 2);
}

int32_t ILCT_SesSteers(const steer_facts* f)
{
    return seat_steers(*f) ? 1 : 0;
}

int32_t ILCT_SesKeepMapGap(int32_t mapping_unknown, int32_t coop, int32_t manual, uint32_t owner_member,
                           uint32_t owner_epoch, uint32_t seat_member, uint32_t leader, uint32_t world_epoch,
                           int32_t allow_leader)
{
    const owner_ref cur = {static_cast<uint16_t>(owner_member), 0, owner_epoch};
    return keep_through_mapping_gap(mapping_unknown != 0, coop != 0, manual != 0, cur,
                                    static_cast<uint16_t>(seat_member), leader, world_epoch, allow_leader != 0);
}

int32_t ILCT_SesKeepStatusGap(int32_t alive, int32_t coop, int32_t manual, uint32_t owner_member,
                              uint32_t owner_epoch, int32_t candidate_is_current, uint32_t seat_member,
                              uint32_t leader, uint32_t world_epoch, int32_t allow_leader)
{
    const owner_ref cur = {static_cast<uint16_t>(owner_member), 0, owner_epoch};
    return keep_through_status_gap(as_tri(alive), coop != 0, manual != 0, cur, candidate_is_current != 0,
                                   static_cast<uint16_t>(seat_member), leader, world_epoch, allow_leader != 0);
}

int32_t ILCT_SesMemberInBoth(uint32_t member, const uint16_t* current, const uint16_t* ui)
{
    return member_in_both(static_cast<uint16_t>(member), current, ui);
}

int32_t ILCT_SesCacheSurvives(int32_t changed, uint32_t member, const uint16_t* current, const uint16_t* ui,
                              int32_t valid)
{
    return cache_survives(changed != 0, static_cast<uint16_t>(member), current, ui, as_tri(valid));
}

int32_t ILCT_SesGuestsMaster(int32_t base, uint32_t owned)
{
    return guests_master(base != 0, owned);
}

uint32_t ILCT_SesMovementControl(int32_t lifecycle_movement, int32_t session_movement, int32_t custom_wait,
                                 int32_t active_mode)
{
    const movement_flags f = movement_control(lifecycle_movement != 0, session_movement != 0, custom_wait != 0,
                                              active_mode != 0);
    return f.ownership | (f.input << 1);
}

int32_t ILCT_SesCameraGate(int32_t lifecycle_camera, int32_t session_camera, int32_t gameplay, int32_t loading,
                           int32_t interaction_owner)
{
    return camera_gate(lifecycle_camera != 0, session_camera != 0, gameplay != 0, loading != 0,
                       interaction_owner != 0);
}

void ILCT_SesCameraReacquire(int32_t requested, int32_t was_requested, reacquire* state, int32_t native_ready,
                             uint32_t required)
{
    *state = camera_reacquire(requested != 0, was_requested != 0, *state, native_ready != 0, required);
}

int32_t ILCT_SesPositionsReady(const float* xyz, uint32_t count)
{
    return camera_positions_ready(xyz, count);
}

int32_t ILCT_SesPartyDown(const int8_t* alive, uint32_t count)
{
    tri a[8];
    for (uint32_t i = 0; i < count && i < 8; ++i) {
        a[i] = as_tri(alive[i]);
    }
    return party_down(a, count < 8 ? count : 8);
}

void ILCT_SesEvaluate(const identity* id, const world_sample* w, int32_t materialized, world_view* out)
{
    evaluate_world(*id, *w, materialized, out);
}

int32_t ILCT_SesWorldValid(const world_view* v)
{
    return world_valid(*v);
}

int32_t ILCT_SesGuestMayMove(const world_view* v, const identity* id, const world_sample* w, uint32_t present)
{
    return guest_may_move(*v, *id, *w, present);
}

int32_t ILCT_SesP1MayMove(const world_view* v, const world_sample* w)
{
    return p1_may_move(*v, *w);
}

int32_t ILCT_SesResolveIdentity(const identity* configured, const uint16_t* current, const uint16_t* ui,
                                uint32_t leader, identity* out)
{
    return resolve_identity(*configured, current, ui, leader, out);
}

int32_t ILCT_SesIdentityFromParty(const identity* id, const world_sample* w, identity* out)
{
    return identity_from_party(*id, *w, out);
}

uint32_t ILCT_SesP1Member(const identity* id, const uint16_t* current, const uint16_t* ui, uint32_t leader)
{
    return p1_member_now(*id, current, ui, leader);
}

int32_t ILCT_SesKoPromoted(const identity* id, uint32_t leader, int32_t p1_alive, int32_t p1_present)
{
    return ko_promoted_seat(*id, leader, as_tri(p1_alive), p1_present != 0);
}

int32_t ILCT_SesSummonOwner(const identity* id, const uint16_t* current, const uint16_t* ui, uint32_t leader)
{
    return summon_owner_seat(*id, current, ui, leader);
}

uint32_t ILCT_SesSummonedEsper(const uint16_t* current, const uint16_t* ui)
{
    return summoned_esper(current, ui);
}

// ---- ownership planner -------------------------------------------------------------------------------------------
void ILCT_HoPlan(ownership* state, const owner_token* desired, const uint8_t* has_desired, const uint8_t* eligible,
                 const follower_sample* samples, uint32_t count, uint64_t now, uint32_t world_epoch, effect_list* out)
{
    out->count = 0;
    out->dropped = 0;
    const sample_set set = {samples, count};
    plan_ownership(*state, desired, has_desired, eligible, set, now, world_epoch, *out);
}

void ILCT_HoAdvance(ownership* state, const follower_sample* samples, uint32_t count, uint64_t now,
                    uint32_t world_epoch, effect_list* out)
{
    out->count = 0;
    out->dropped = 0;
    const sample_set set = {samples, count};
    advance_intents(*state, set, now, world_epoch, *out);
}

void ILCT_HoEnd(ownership* state, uint32_t reason, effect_list* out)
{
    out->count = 0;
    out->dropped = 0;
    end_intents(*state, static_cast<uint8_t>(reason), *out);
}

void ILCT_HoRefuse(ownership* state, const owner_token* token, int32_t restore, uint64_t now)
{
    refuse_acquire(*state, *token, restore != 0, now);
}

uint32_t ILCT_HoSampleFlags(const follower_sample* s)
{
    return (sample_confirms_follow(*s) ? 1u : 0u) | (sample_allows_request(*s) ? 2u : 0u) |
           (sample_cancels(*s) ? 4u : 0u);
}

uint32_t ILCT_WorldEpochs(world_epochs* state, int32_t valid, uint64_t a, uint64_t b)
{
    return state->observe(valid != 0, a, b);
}

int32_t ILCT_Generations(generation_tracker* state, uint32_t seat, uint32_t member, uint64_t actor, uint64_t phys,
                         uint32_t handle, uint32_t world_epoch, int32_t alive, int32_t present, owner_token* out)
{
    memset(out, 0, sizeof(*out));
    return state->observe(static_cast<uint8_t>(seat), static_cast<uint16_t>(member), actor, phys, handle,
                          world_epoch, as_tri(alive), present != 0, out);
}

// ---- world reads on a synthetic image ----------------------------------------------------------------------------
uint32_t ILCT_SesSampleWorld(uint64_t image_base, world_sample* out, uint32_t* reads_failed)
{
    game_reader r(static_cast<uintptr_t>(image_base));
    sample_world(r, out);
    if (reads_failed != nullptr) {
        *reads_failed = r.failures();
    }
    return r.reads();
}

uint32_t ILCT_SesScanParty(uint64_t image_base, party_actors* out, uint32_t* queries)
{
    game_reader r(static_cast<uintptr_t>(image_base));
    scan_party(r, out);
    if (queries != nullptr) {
        *queries = r.region_queries();
    }
    return r.reads();
}

int32_t ILCT_SesActorInParty(uint64_t image_base, uint64_t actor)
{
    game_reader r(static_cast<uintptr_t>(image_base));
    return actor_in_party(r, static_cast<uintptr_t>(actor));
}

int32_t ILCT_SesActorFacts(uint64_t image_base, uint64_t actor, int32_t which)
{
    game_reader r(static_cast<uintptr_t>(image_base));
    switch (which) {
    case 0:
        return actor_alive(r, static_cast<uintptr_t>(actor));
    case 1:
        return actor_walks(r, static_cast<uintptr_t>(actor));
    case 2:
        return actor_command_blocked(r, static_cast<uintptr_t>(actor));
    case 3:
        return actor_berserk(r, static_cast<uintptr_t>(actor));
    case 4:
        return actor_leads(r, static_cast<uintptr_t>(actor), false);
    case 5:
        return actor_leads(r, static_cast<uintptr_t>(actor), true);
    default:
        return -2;
    }
}

int32_t ILCT_SesSlotHolds(uint64_t image_base, uint32_t slot, const party_actor* cached)
{
    game_reader r(static_cast<uintptr_t>(image_base));
    return party_slot_holds(r, slot, *cached);
}

// One scalar through the session reader; *single = 1 if it was one load of its own width.
int32_t ILCT_SesReadScalar(uint64_t address, uint32_t width, uint64_t* out, uint32_t* single)
{
    game_reader r(0);
    uint64_t v = 0;
    bool ok = false;
    switch (width) {
    case 1: { uint8_t x = 0; ok = r.u8(static_cast<uintptr_t>(address), &x); v = x; break; }
    case 2: { uint16_t x = 0; ok = r.u16(static_cast<uintptr_t>(address), &x); v = x; break; }
    case 4: { uint32_t x = 0; ok = r.u32(static_cast<uintptr_t>(address), &x); v = x; break; }
    case 8: ok = r.u64(static_cast<uintptr_t>(address), &v); break;
    default: return -1;
    }
    *out = v;
    *single = r.single_loads();
    return ok ? 1 : 0;
}

// A writer thread flips a naturally aligned value between `a` and `b` as fast as it can while this thread reads it
// `iterations` times: through the session reader (mode 0: one load) or through the byte copy (mode 1, the
// instrument). Returns reads that were neither a nor b (torn).
struct tear_target {
    volatile uint64_t value;
    uint64_t a;
    uint64_t b;
    uint32_t width;
    volatile uint32_t stop;
};

DWORD WINAPI tear_writer(void* arg)
{
    tear_target* t = static_cast<tear_target*>(arg);
    while (__atomic_load_n(&t->stop, __ATOMIC_ACQUIRE) == 0) {
        if (t->width == 8) {
            __atomic_store_n(&t->value, t->a, __ATOMIC_RELAXED);
            __atomic_store_n(&t->value, t->b, __ATOMIC_RELAXED);
        } else {
            __atomic_store_n(reinterpret_cast<volatile uint32_t*>(&t->value), static_cast<uint32_t>(t->a), __ATOMIC_RELAXED);
            __atomic_store_n(reinterpret_cast<volatile uint32_t*>(&t->value), static_cast<uint32_t>(t->b), __ATOMIC_RELAXED);
        }
    }
    return 0;
}

uint32_t ILCT_ProbeTearDrill(uint32_t width, uint64_t a, uint64_t b, uint32_t iterations, uint32_t mode)
{
    if (width != 4 && width != 8) {
        return 0xFFFFFFFFu;  // NOTADDR: refused
    }
    static tear_target t;
    t.value = a;
    t.a = a;
    t.b = b;
    t.width = width;
    t.stop = 0;
    HANDLE th = CreateThread(nullptr, 0, tear_writer, &t, 0, nullptr);
    uint32_t torn = 0;
    game_reader r(0);
    for (uint32_t i = 0; i < iterations; ++i) {
        uint64_t v = 0;
        bool ok;
        if (mode == 0) {
            if (width == 8) {
                ok = r.u64(reinterpret_cast<uintptr_t>(&t.value), &v);
            } else {
                uint32_t x = 0;
                ok = r.u32(reinterpret_cast<uintptr_t>(&t.value), &x);
                v = x;
            }
        } else {
            ok = copy_from_caller(&v, const_cast<const uint64_t*>(&t.value), width);
        }
        if (ok && v != a && v != b) {
            ++torn;
        }
    }
    __atomic_store_n(&t.stop, 1u, __ATOMIC_RELEASE);
    WaitForSingleObject(th, INFINITE);
    CloseHandle(th);
    return torn;
}

// ---- the session core ----------------------------------------------------------------------------------------------
void ILCT_SesReset(void)
{
    session::test_reset();
}

void ILCT_SesStart(uint64_t image_base, uint64_t now)
{
    session::start(static_cast<uintptr_t>(image_base), now);
}

void ILCT_SesConfigure(const seat_config* cfg)
{
    session::configure(*cfg);
}

void ILCT_SesConfigureFromSettings(const ilc_settings* s)
{
    session::configure_from_settings(*s);
}

void ILCT_SesSetParked(uint32_t parked)
{
    session::set_live_parked(parked);
}

void ILCT_SesStep(uint64_t now)
{
    session::step(now);
}

void ILCT_SesStop(void)
{
    session::stop();
}

void ILCT_SesForceMovementLive(int32_t value)
{
    session::test_force_movement_live(value);
}

int32_t ILCT_SesRecord(session_record* out)
{
    return session::read(out) ? 1 : 0;
}

uint32_t ILCT_SesEpoch(void)
{
    return session::epoch();
}

uint64_t ILCT_SesGeneration(void)
{
    return session::generation();
}

uint64_t ILCT_GtqGeneration(void)
{
    return gtq::current_generation();
}

void ILCT_SesDebug(debug_view* out)
{
    session::test_debug(out);
}

uint32_t ILCT_SesTakeEffects(pending_effect* out, uint32_t max)
{
    return session::take_effects(out, max);
}

void ILCT_SesStats(session::counters* out)
{
    memcpy(out, const_cast<const session::counters*>(session::stats()), sizeof(*out));
}

// The record slot's sequence word (torn-read tests).
uint32_t ILCT_SesRecordSeq(void)
{
    return session::slot().seq;
}

// ---- edge cursors ----------------------------------------------------------------------------------------------
void ILCT_EdgeTestReset(void)
{
    edges::test_reset();
}

void ILCT_EdgeReset(uint32_t run)
{
    edges::reset(run);
}

// masks: per seat 7 words: pad pressed, released, held; action pressed, released, repeated, held.
void ILCT_EdgeRecord(uint32_t seq, const uint32_t* masks)
{
    ilc_seat_input_frame frames[ILC_SEATS];
    ilc_seat_action_frame actions[ILC_SEATS];
    memset(frames, 0, sizeof(frames));
    memset(actions, 0, sizeof(actions));
    for (uint32_t s = 0; s < ILC_SEATS; ++s) {
        const uint32_t* m = masks + s * 7;
        frames[s].pressed = m[0];
        frames[s].released = m[1];
        frames[s].held = m[2];
        actions[s].pressed = m[3];
        actions[s].released = m[4];
        actions[s].repeated = m[5];
        actions[s].held = m[6];
    }
    edges::record(seq, frames, actions);
}

int32_t ILCT_EdgeSince(edges::edge_cursor* cursor, uint32_t seat, edges::edge_delta* out)
{
    return edges::since(*cursor, seat, out) ? 1 : 0;
}

uint32_t ILCT_EdgeNewest(void)
{
    return edges::newest_seq();
}

// One writer thread records `ticks` ticks (a tap of action bit 0 on seat `seat`: pressed on one tick, released
// `hold` ticks later, every `period` ticks) at full speed; `readers` reader threads with their own cursors read at
// different paces. out[6 * i ..]: press deltas, mismatches, gaps, overruns, reads, ticks covered. Returns presses.
uint32_t ILCT_EdgeStress(uint32_t seat, uint32_t ticks, uint32_t period, uint32_t hold, uint32_t readers,
                         const uint32_t* sleep_every, uint32_t writer_pause_every, uint32_t* out)
{
    constexpr uint32_t MAX_READERS = 8;
    if (readers > MAX_READERS || seat < 1 || seat > ILC_SEATS || period <= hold || hold == 0) {
        return 0;
    }
    edges::test_reset();
    edges::reset(0x5EED);
    __atomic_store_n(&g_stress_done, 0u, __ATOMIC_RELEASE);
    stress_reader rs[MAX_READERS];
    HANDLE th[MAX_READERS];
    ilc_seat_input_frame frames[ILC_SEATS];
    ilc_seat_action_frame actions[ILC_SEATS];
    memset(frames, 0, sizeof(frames));
    memset(actions, 0, sizeof(actions));
    edges::record(1, frames, actions);  // the readers seed on tick 1
    for (uint32_t i = 0; i < readers; ++i) {
        memset(&rs[i], 0, sizeof(rs[i]));
        rs[i].seat = seat;
        rs[i].sleep_every = sleep_every[i];
        rs[i].period = period;
        rs[i].hold = hold;
        th[i] = CreateThread(nullptr, 0, stress_read, &rs[i], 0, nullptr);
    }
    Sleep(20);
    uint32_t presses = 0;
    for (uint32_t t = 2; t < ticks + 2; ++t) {
        memset(actions, 0, sizeof(actions));
        const uint32_t phase = t % period;
        if (phase == 0) {
            actions[seat - 1].pressed = 1;
            ++presses;
        } else if (phase == hold) {
            actions[seat - 1].released = 1;
        }
        actions[seat - 1].held = (phase < hold) ? 1 : 0;
        edges::record(t, frames, actions);
        if (writer_pause_every != 0 && t % writer_pause_every == 0) {
            Sleep(1);
        }
    }
    Sleep(20);
    __atomic_store_n(&g_stress_done, 1u, __ATOMIC_RELEASE);
    for (uint32_t i = 0; i < readers; ++i) {
        WaitForSingleObject(th[i], INFINITE);
        CloseHandle(th[i]);
        out[6 * i + 0] = rs[i].press_deltas;
        out[6 * i + 1] = rs[i].mismatches;
        out[6 * i + 2] = rs[i].gaps;
        out[6 * i + 3] = rs[i].overruns;
        out[6 * i + 4] = rs[i].reads;
        out[6 * i + 5] = rs[i].covered;
    }
    return presses;
}

// ---- the queue's world restriction ----
namespace {
volatile uint32_t g_any_world_runs = 0;
int32_t op_any_world(const gtq::job&)
{
    __atomic_add_fetch(&g_any_world_runs, 1u, __ATOMIC_RELAXED);
    return 1;
}
}  // namespace

int32_t ILCT_GtqRegisterAnyWorld(uint32_t op, uint32_t subsystem)
{
    return gtq::register_op(static_cast<uint16_t>(op), subsystem, op_any_world, gtq::OPF_ANY_WORLD) ? 1 : 0;
}

uint32_t ILCT_GtqAnyWorldRuns(void)
{
    return g_any_world_runs;
}

void ILCT_GtqSetWorld(uint32_t epoch, int32_t valid)
{
    gtq::set_world(epoch, valid != 0);
}

uint64_t ILCT_GtqWorld(void)
{
    return gtq::test_world();
}

// ---- job reaper ------------------------------------------------------------------------------------------------
uint32_t ILCT_GtqReap(void)
{
    return gtq::reap();
}

int32_t ILCT_GtqAwait(uint32_t ticket, uint64_t enqueued_ms, uint32_t ttl_ms, gtq::completion* out)
{
    return gtq::await_result(ticket, enqueued_ms, ttl_ms, out);
}

int32_t ILCT_GtqHoldConsumer(int32_t hold)
{
    return gtq::test_hold_consumer(hold != 0) ? 1 : 0;
}

}  // extern "C"
