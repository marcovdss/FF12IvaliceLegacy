// Ivalice Legacy co-op module: the movement subsystem's co-op-thread step and its game-thread operation (see
// movement.h). The detour handlers are in hooks.cpp.
#include "movement/movement.h"

#include <math.h>
#include <string.h>

#include "input/edge_history.h"
#include "input/input_service.h"
#include "movement/movement_facts.h"
#include "runtime/probe.h"
#include "session/game_facts.h"
#include "session/reader.h"

namespace ilc::movement {

namespace detail {
patch::record_slot<steer_record> g_slot;
counters g_counters;
}  // namespace detail

namespace {

using detail::g_counters;
using detail::g_slot;
namespace sf = session::facts;

// Co-op thread state.
uintptr_t g_image_base = 0;
planner_state g_planner;
plan_outputs g_last;
edges::edge_cursor g_cursor;
input::snapshot g_snapshot;      // co-op thread only; too large for its stack
input_source_fn g_test_input = nullptr;
bool g_started = false;
uint32_t g_input_epoch = 0;     // the session epoch the edge cursor was last read in

// The flee request: (epoch << 32) | seats, one atomic word for the input detours.
volatile uint64_t g_flee = 0;
volatile int32_t g_test_flee_live = -1;  // host tests: force the MOVEMENT-live answer (-1 = patch layer)

void bump(volatile uint64_t& counter, uint64_t by = 1)
{
    __atomic_add_fetch(&counter, by, __ATOMIC_RELAXED);
}

bool flee_live()
{
    const int32_t forced = __atomic_load_n(&g_test_flee_live, __ATOMIC_RELAXED);
    return forced >= 0 ? forced != 0 : patch::subsystem_live(ILC_SITE_SUBSYS_MOVEMENT);
}

void publish_flee(uint32_t epoch, uint32_t seats)
{
    __atomic_store_n(&g_flee, (static_cast<uint64_t>(epoch) << 32) | seats, __ATOMIC_RELEASE);
}

// ---- inputs ------------------------------------------------------------------------------------------------------
// Edges are reseeded (no press replayed) when the cursor overran its history or the session epoch moved: presses
// mashed during a load or a cutscene must not act once play resumes.
void gather_input(plan_inputs& in)
{
    const bool new_epoch = in.session.head.epoch != g_input_epoch;
    g_input_epoch = in.session.head.epoch;
    if (g_test_input != nullptr) {
        (void)new_epoch;
        for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
            seat_input si = {};
            if (g_test_input(s, &si)) {
                in.input[s] = si;
            }
        }
        return;
    }
    const bool have = input::snapshot_read(&g_snapshot) && g_snapshot.state == input::RUN_RUNNING;
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        edges::edge_delta d;
        const bool edged = edges::since(g_cursor, s + 1, &d);  // the input seam numbers seats from 1
        if (!have) {
            continue;
        }
        const ilc_seat_action_frame& f = g_snapshot.actions[s];
        seat_input& si = in.input[s];
        si.present = 1;
        si.up = f.strength[ILC_ACTION_MOVE_UP];
        si.down = f.strength[ILC_ACTION_MOVE_DOWN];
        si.left = f.strength[ILC_ACTION_MOVE_LEFT];
        si.right = f.strength[ILC_ACTION_MOVE_RIGHT];
        si.flee = (f.held & (1u << ILC_ACTION_FLEE)) != 0 ? 1 : 0;
        si.walk_toggle = (edged && !d.reseeded && !d.overrun && !new_epoch &&
                          (d.act_pressed & (1u << ILC_ACTION_WALK_RUN)) != 0)
                             ? 1
                             : 0;
    }
}

bool read_f32(session::game_reader& r, uintptr_t address, float* out)
{
    return r.read(address, out, sizeof(float));
}

// Heading from the actor toward the target of its execution motion (the target is found by its handle).
bool target_heading(session::game_reader& r, uintptr_t actor, float* out)
{
    uint32_t handle = 0;
    uintptr_t manager = 0;
    uint32_t count = 0;
    uintptr_t entry = 0;
    uintptr_t target = 0;
    uint32_t target_handle = 0;
    if (!r.u32(actor + sf::ACTOR_TARGET, &handle) || handle == 0 ||
        !r.ptr(r.global(sf::ACTOR_MANAGER_PTR), &manager) || manager == 0 ||
        !r.u32(manager + sf::ACTOR_COUNT, &count) || count > sf::ACTOR_COUNT_MAX) {
        return false;
    }
    const uint32_t index = handle & sf::HANDLE_INDEX_MASK;
    if (index >= count || !r.ptr(manager + sf::ACTOR_ENTRIES + 8ull * index, &entry) || entry == 0 ||
        !r.ptr(entry + sf::ENTRY_ACTOR, &target) || target == 0 || target == actor ||
        !r.u32(target + sf::ACTOR_HANDLE, &target_handle) || target_handle != handle) {
        return false;
    }
    float a[3];
    float t[3];
    if (!r.read(actor + sf::ACTOR_POSITION, a, sizeof(a)) || !r.read(target + sf::ACTOR_POSITION, t, sizeof(t))) {
        return false;
    }
    const double dx = static_cast<double>(t[0]) - a[0];
    const double dz = static_cast<double>(t[2]) - a[2];
    if (!isfinite(dx) || !isfinite(dz) || dx * dx + dz * dz <= 1.0e-6) {
        return false;
    }
    *out = static_cast<float>(atan2(dx, dz));
    return true;
}

void gather_actor(session::game_reader& r, const session::seat_entry& e, actor_facts* out)
{
    memset(out, 0, sizeof(*out));
    uintptr_t root = 0;
    uintptr_t phys = 0;
    uint8_t stance = 0;
    uint64_t flags = 0;
    if (!r.ptr(e.actor + sf::ACTOR_ROOT, &root) || root == 0 || !r.ptr(root + facts::ROOT_PHYSICS, &phys) ||
        phys != e.phys || !r.u8(e.actor + facts::ACTOR_STANCE, &stance) ||
        !r.u64(e.actor + facts::ACTOR_FLAGS, &flags)) {
        return;
    }
    out->root = root;
    out->armed = (stance & facts::ACTOR_STANCE_ARMED) != 0 ? 1 : 0;
    // An execution motion counts only for a living actor (a fallen body keeps stale flags).
    out->execution = ((flags & facts::ACTOR_EXECUTION_BIT) != 0 && (e.flags & session::SEATF_ALIVE) != 0) ? 1 : 0;
    if (out->execution) {
        out->has_target = target_heading(r, e.actor, &out->target_heading) ? 1 : 0;
    }
    float h = 0.0f;
    float v = 0.0f;
    out->headings_ok = (read_f32(r, phys + facts::PHYS_HEADING, &h) && read_f32(r, phys + facts::PHYS_VISUAL, &v) &&
                        isfinite(h) && isfinite(v))
                           ? 1
                           : 0;
    out->phys_heading = h;
    out->phys_visual = v;
    out->ok = 1;
}

// ---- follow requests -----------------------------------------------------------------------------------------
void drain_follow_effects(uint32_t epoch)
{
    session::pending_effect fx[EFFECT_BATCH];
    const uint32_t n = session::take_effects(fx, EFFECT_BATCH);
    for (uint32_t i = 0; i < n; ++i) {
        if (fx[i].fx.kind != session::FX_ASK_FOLLOW) {
            continue;
        }
        const session::owner_token& t = fx[i].fx.token;
        if (fx[i].epoch != epoch || epoch == 0) {
            bump(g_counters.follow_stale);  // planned for an older world: the session plans again if needed
            continue;
        }
        const uint64_t args[gtq::ARG_COUNT] = {t.actor, t.phys,
                                               static_cast<uint64_t>(t.handle) | (static_cast<uint64_t>(t.member) << 32),
                                               t.seat};
        uint32_t ticket = 0;
        if (gtq::push(OP_FOLLOW, t.seat, epoch, gtq::current_generation(), FOLLOW_TTL_MS, args, &ticket) ==
            gtq::PUSH_OK) {
            bump(g_counters.follow_pushed);
        } else {
            bump(g_counters.follow_refused);
        }
    }
}

bool probe_u64(uintptr_t address, uint64_t* out)
{
    return address != 0 && copy_from_caller(out, reinterpret_cast<const void*>(address), sizeof(*out));
}

bool probe_u32(uintptr_t address, uint32_t* out)
{
    return address != 0 && copy_from_caller(out, reinterpret_cast<const void*>(address), sizeof(*out));
}

bool probe_u16(uintptr_t address, uint16_t* out)
{
    return address != 0 && copy_from_caller(out, reinterpret_cast<const void*>(address), sizeof(*out));
}

bool probe_u8(uintptr_t address, uint8_t* out)
{
    return address != 0 && copy_from_caller(out, reinterpret_cast<const void*>(address), sizeof(*out));
}

// The actor the handle names in the actor manager, and its physics object.
int32_t resolve_follower(uintptr_t actor, uintptr_t phys, uint32_t handle)
{
    const uintptr_t base = __atomic_load_n(&g_image_base, __ATOMIC_ACQUIRE);
    uint64_t manager = 0;
    uint32_t count = 0;
    uint64_t entry = 0;
    uint64_t live = 0;
    uint32_t live_handle = 0;
    const uint32_t index = handle & sf::HANDLE_INDEX_MASK;
    if (base == 0 || !probe_u64(base + (sf::ACTOR_MANAGER_PTR - sf::IMAGE_VA), &manager) || manager == 0 ||
        !probe_u32(manager + sf::ACTOR_COUNT, &count) || count > sf::ACTOR_COUNT_MAX || index >= count ||
        !probe_u64(manager + sf::ACTOR_ENTRIES + 8ull * index, &entry) || entry == 0 ||
        !probe_u64(entry + sf::ENTRY_ACTOR, &live)) {
        return FOLLOW_UNREADABLE;
    }
    if (live != actor || !probe_u32(actor + sf::ACTOR_HANDLE, &live_handle) || live_handle != handle) {
        return FOLLOW_STALE_IDENTITY;
    }
    uint64_t root = 0;
    uint64_t live_phys = 0;
    if (!probe_u64(actor + sf::ACTOR_ROOT, &root) || root == 0 || !probe_u64(root + facts::ROOT_PHYSICS, &live_phys)) {
        return FOLLOW_UNREADABLE;
    }
    return live_phys == phys ? 0 : FOLLOW_STALE_PHYSICS;
}

// Idle, untargeted (live and shadow), attached to a native follower list, with the follow descriptor.
int32_t follower_ready(uintptr_t actor)
{
    const uintptr_t base = __atomic_load_n(&g_image_base, __ATOMIC_ACQUIRE);
    uint8_t state = 0;
    uint32_t target = 0;
    uint32_t target_shadow = 0;
    uint64_t descriptor = 0;
    uint16_t descriptor_action = 0;
    uint64_t list_a = 0;
    uint64_t list_b = 0;
    if (!probe_u8(actor + sf::ACTOR_ACTION_STATE, &state) || !probe_u32(actor + sf::ACTOR_TARGET, &target) ||
        !probe_u32(actor + facts::ACTOR_TARGET_SHADOW, &target_shadow) ||
        !probe_u64(actor + sf::ACTOR_DESCRIPTOR, &descriptor) || !probe_u64(actor + sf::ACTOR_FOLLOW_LIST_A, &list_a) ||
        !probe_u64(actor + sf::ACTOR_FOLLOW_LIST_B, &list_b)) {
        return FOLLOW_UNREADABLE;
    }
    if (state != 1 || target != 0 || target_shadow != 0 || descriptor == 0) {
        return FOLLOW_NOT_READY;
    }
    if (!probe_u16(descriptor, &descriptor_action)) {
        return FOLLOW_UNREADABLE;
    }
    const uint64_t primary = base + (sf::FOLLOW_LIST_PRIMARY - sf::IMAGE_VA);
    const uint64_t secondary = base + (sf::FOLLOW_LIST_SECONDARY - sf::IMAGE_VA);
    const bool listed = list_a == primary || list_a == secondary || list_b == primary || list_b == secondary;
    if (descriptor_action != sf::ACTION_FOLLOW || !listed) {
        return FOLLOW_NOT_READY;
    }
    return 0;
}

}  // namespace

// ---- the game-thread operation -------------------------------------------------------------------------------
int32_t op_follow(const gtq::job& j)
{
    const uintptr_t actor = static_cast<uintptr_t>(j.arg[0]);
    const uintptr_t phys = static_cast<uintptr_t>(j.arg[1]);
    const uint32_t handle = static_cast<uint32_t>(j.arg[2]);
    int32_t result = FOLLOW_BAD_ARGS;
    // A world-requiring operation: an epoch match alone does not prove the world is valid (the session record
    // copy is fine here: the UI-tick drain runs at a call-type site).
    session::session_record rec;
    const bool world = session::read(&rec) && rec.head.epoch == j.epoch && (rec.head.flags & session::GATE_WORLD) != 0;
    if (!world) {
        result = FOLLOW_NO_WORLD;
    } else if (actor != 0 && phys != 0 && handle != 0) {
        result = resolve_follower(actor, phys, handle);
        if (result == 0) {
            result = follower_ready(actor);
        }
        uint16_t action = 0;
        uint16_t shadow = 0;
        if (result == 0 && (!probe_u16(actor + sf::ACTOR_ACTION, &action) ||
                            !probe_u16(actor + facts::ACTOR_ACTION_SHADOW, &shadow))) {
            result = FOLLOW_UNREADABLE;
        }
        if (result == 0) {
            if (action == sf::ACTION_FOLLOW && shadow == sf::ACTION_FOLLOW) {
                result = FOLLOW_ALREADY;
            } else if (action != sf::ACTION_IDLE || shadow != sf::ACTION_IDLE) {
                result = FOLLOW_ACTION_CHANGED;
            } else {
                // Both stores on the game thread, the live action first, then the setter's shadow.
                const uint16_t follow = sf::ACTION_FOLLOW;
                if (copy_to_caller(reinterpret_cast<void*>(actor + sf::ACTOR_ACTION), &follow, sizeof(follow)) &&
                    copy_to_caller(reinterpret_cast<void*>(actor + facts::ACTOR_ACTION_SHADOW), &follow,
                                   sizeof(follow))) {
                    result = FOLLOW_APPLIED;
                } else {
                    result = FOLLOW_UNREADABLE;
                }
            }
        }
    }
    bump(result >= 1 ? g_counters.follow_applied : g_counters.follow_rejected);
    return result;
}

// ---- co-op thread -------------------------------------------------------------------------------------------
void start(uintptr_t image_base, uint64_t now_ms)
{
    (void)now_ms;
    __atomic_store_n(&g_image_base, image_base, __ATOMIC_RELEASE);
    planner_reset(g_planner);
    memset(&g_last, 0, sizeof(g_last));
    memset(&g_cursor, 0, sizeof(g_cursor));
    publish_flee(0, 0);
    g_started = true;
}

void step(uint64_t now_ms)
{
    if (!g_started) {
        return;
    }
    bump(g_counters.steps);
    static plan_inputs in;  // co-op thread only
    memset(&in, 0, sizeof(in));
    in.now_ms = now_ms;
    if (!session::read(&in.session) || in.session.head.epoch == 0) {
        g_slot.withdraw();
        publish_flee(0, 0);
        bump(g_counters.withdrawn);
        planner_reset(g_planner);
        return;
    }
    gather_input(in);
    session::game_reader r(g_image_base);
    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        const session::seat_entry& e = in.session.seat[s];
        if ((e.flags & session::SEATF_STEERS) != 0 && e.actor != 0) {
            gather_actor(r, e, &in.actor[s]);
            if (!in.actor[s].ok) {
                bump(g_counters.facts_failed);
            }
        }
    }
    if ((in.session.head.flags & session::GATE_GUESTS_MOVE) != 0 &&
        r.read(r.global(facts::CAMERA_VIEW_MATRIX), in.view, sizeof(in.view))) {
        in.camera_ok = 1;
        bump(g_counters.camera_reads);
    }
    plan(g_planner, in, g_last);
    g_slot.publish(g_last.record);
    // The flee word is a movement-subsystem output: nothing while MOVEMENT may not act.
    publish_flee(g_last.record.head.epoch, flee_live() ? g_last.flee_seats : 0u);
    bump(g_counters.published);
    drain_follow_effects(in.session.head.epoch);
}

void stop()
{
    g_started = false;
    g_slot.withdraw();
    publish_flee(0, 0);
}

patch::record_slot<steer_record>& slot()
{
    return g_slot;
}

bool read(steer_record* out)
{
    return g_slot.read(*out);
}

void test_force_flee_live(int32_t forced)
{
    __atomic_store_n(&g_test_flee_live, forced, __ATOMIC_RELAXED);
}

uint32_t flee_seats(uint32_t epoch)
{
    const uint64_t v = __atomic_load_n(&g_flee, __ATOMIC_ACQUIRE);
    return (epoch != 0 && static_cast<uint32_t>(v >> 32) == epoch) ? static_cast<uint32_t>(v) : 0u;
}

bool berserk_gate_live()
{
    const int32_t row = row_of("MV-10");
    return row >= 0 && patch::gate(static_cast<uint32_t>(row)) != 0 && patch::subsystem_live(ILC_SITE_SUBSYS_MOVEMENT);
}

const counters* stats()
{
    return &g_counters;
}

void test_set_input_source(input_source_fn fn)
{
    g_test_input = fn;
}

void test_reset()
{
    g_started = false;
    g_slot.withdraw();
    publish_flee(0, 0);
    planner_reset(g_planner);
    memset(&g_last, 0, sizeof(g_last));
    memset(&g_cursor, 0, sizeof(g_cursor));
    memset(&g_counters, 0, sizeof(g_counters));
    g_test_input = nullptr;
    g_input_epoch = 0;
}

void test_last(plan_outputs* out)
{
    *out = g_last;
}

void test_set_image_base(uintptr_t image_base)
{
    __atomic_store_n(&g_image_base, image_base, __ATOMIC_RELEASE);
}

}  // namespace ilc::movement
