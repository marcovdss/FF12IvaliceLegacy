// Ivalice Legacy co-op module: town visibility, the co-op-thread step and the rebuild operation (see towns.h). The
// TW-01 handler is in hooks.cpp.
#include "towns/towns.h"

#include <string.h>

#include "patch/patch_engine.h"
#include "runtime/probe.h"
#include "session/reader.h"
#include "session/session.h"
#include "towns/towns_facts.h"

namespace ilc::towns {

namespace detail {
patch::record_slot<town_record> g_slot;
counters g_counters;
volatile uintptr_t g_image_base = facts::IMAGE_VA;  // set once at start, before the module arms
}  // namespace detail

namespace {

using detail::g_counters;
using detail::g_slot;

// The town arm of the layout builder as the exe ships it (sub eax,1; je <town collapse>).
constexpr uint8_t STOCK_TOWN_ARM[facts::TOWN_ARM_LEN] = {0x83, 0xE8, 0x01, 0x0F, 0x84, 0x82, 0x01, 0x00, 0x00};

// Co-op thread state.
planner_state g_planner;
planner_inputs g_last_inputs;
int32_t g_last_action = 0;
int32_t g_last_result = 0;
uint64_t g_last_allowed = 0;
uint64_t g_pushed_ms = 0;
bool g_started = false;
rematerialize_fn volatile g_test_rematerialize = nullptr;

void bump(volatile uint64_t& counter)
{
    __atomic_add_fetch(&counter, 1, __ATOMIC_RELAXED);
}

uintptr_t image_base()
{
    return __atomic_load_n(&detail::g_image_base, __ATOMIC_ACQUIRE);
}

uintptr_t at(uintptr_t base, uintptr_t va)
{
    return base + (va - facts::IMAGE_VA);
}

// ---- game-thread reads (fault-safe probe; one load per aligned scalar) ----------------------------------------
bool load(uintptr_t address, void* out, size_t width)
{
    const void* src = reinterpret_cast<const void*>(address);
    return address != 0 && (load_from_caller(out, src, width) || copy_from_caller(out, src, width));
}

}  // namespace

namespace detail {

// The engine's town mode, read on the game thread. False with *ok = false if a read failed.
bool town_mode_now(uintptr_t base, uintptr_t* block_out, bool* ok)
{
    *ok = false;
    uint64_t block = 0;
    uint32_t mode = 0;
    uint32_t location = 0;
    uint64_t table = 0;
    if (!load(at(base, facts::PARTY_BLOCK_PTR), &block, 8) || block == 0 ||
        !load(block + facts::MODE_BITS, &mode, 4) || !load(at(base, facts::LOCATION_ID), &location, 4) ||
        !load(at(base, facts::AREA_TABLE_PTR), &table, 8)) {
        return false;
    }
    *block_out = static_cast<uintptr_t>(block);
    uint32_t count = 0;
    uint8_t town_byte = 0;
    const uint32_t id = static_cast<int32_t>(location) < 0 ? 0u : location;
    if (table != 0) {
        if (!load(table + facts::AREA_COUNT, &count, 4)) {
            return false;
        }
        if (id < count && !load(table + facts::AREA_TOWN_BYTE + uint64_t{facts::AREA_ENTRY_SIZE} * id, &town_byte, 1)) {
            return false;
        }
    }
    *ok = true;
    return town_mode(table != 0, location, count, town_byte, mode);
}

}  // namespace detail

namespace {

// ---- co-op thread ---------------------------------------------------------------------------------------------
struct view {
    bool ok;
    bool in_town;
    bool task_busy;
    bool degraded;
    uint8_t leader;
    uint16_t roster[SLOTS];
    uint16_t current[SLOTS];
};

bool read_view(session::game_reader& r, view* v)
{
    memset(v, 0, sizeof(*v));
    uintptr_t block = 0;
    uintptr_t table = 0;
    uintptr_t task = 0;
    uint32_t mode = 0;
    uint32_t location = 0;
    uint16_t layouts[facts::L_UI / 2] = {};
    if (!r.ptr(r.global(facts::PARTY_BLOCK_PTR), &block) || block == 0 ||
        !r.u32(block + facts::MODE_BITS, &mode) || !r.u8(block + facts::LAYOUTS + facts::L_LEADER, &v->leader) ||
        !r.read(block + facts::LAYOUTS, layouts, sizeof(layouts)) ||
        !r.u32(r.global(facts::LOCATION_ID), &location) || !r.ptr(r.global(facts::AREA_TABLE_PTR), &table) ||
        !r.ptr(r.global(facts::PARTY_TASK_PTR), &task)) {
        return false;
    }
    for (uint32_t k = 0; k < SLOTS; ++k) {
        v->roster[k] = layouts[facts::L_DEFAULT / 2 + k];
        v->current[k] = layouts[facts::L_CURRENT / 2 + k];
    }
    uint32_t count = 0;
    uint8_t town_byte = 0;
    const uint32_t id = static_cast<int32_t>(location) < 0 ? 0u : location;
    if (table != 0) {
        if (!r.u32(table + facts::AREA_COUNT, &count)) {
            return false;
        }
        if (id < count && !r.u8(table + facts::AREA_TOWN_BYTE + uint64_t{facts::AREA_ENTRY_SIZE} * id, &town_byte)) {
            return false;
        }
    }
    uint8_t arm[facts::TOWN_ARM_LEN];
    if (!r.read(r.global(facts::TOWN_ARM), arm, sizeof(arm))) {
        return false;
    }
    v->in_town = town_mode(table != 0, location, count, town_byte, mode);
    v->task_busy = task != 0;
    v->degraded = memcmp(arm, STOCK_TOWN_ARM, sizeof(arm)) == 0;
    v->ok = true;
    return true;
}

void forget_flight()
{
    if (g_planner.in_flight) {
        planner_result(g_planner, gtq::REJECT_DISARMED);
    }
}

}  // namespace

void start(uintptr_t base, uint64_t now_ms)
{
    (void)now_ms;
    __atomic_store_n(&detail::g_image_base, base, __ATOMIC_RELEASE);
    planner_reset(g_planner);
    memset(&g_last_inputs, 0, sizeof(g_last_inputs));
    g_last_action = 0;
    g_last_result = 0;
    g_started = true;
}

void step(uint64_t now)
{
    if (!g_started) {
        return;
    }
    bump(g_counters.steps);
    session::session_record s;
    if (!session::read(&s) || s.head.epoch == 0) {
        g_slot.withdraw();
        bump(g_counters.withdrawn);
        forget_flight();
        planner_reset(g_planner);
        return;
    }
    town_record rec;
    memset(&rec, 0, sizeof(rec));
    rec.head.epoch = s.head.epoch;
    rec.head.flags = TOWNF_ACTIVE;
    rec.head.generation = s.head.generation;
    rec.allowed = controlled_members(s);
    g_slot.publish(rec);
    bump(g_counters.published);
    g_last_allowed = rec.allowed;

    // A job in flight: collect its result (await_result reaps it once past its ttl, so it never stays pending).
    if (g_planner.in_flight) {
        const int32_t res = gtq::await_result(g_planner.ticket, g_pushed_ms, REBUILD_TTL_MS, nullptr);
        if (res != gtq::RESULT_PENDING) {
            g_last_result = res;
            planner_result(g_planner, res);
        }
    }
    if (!patch::subsystem_live(ILC_SITE_SUBSYS_PARTY)) {
        forget_flight();
        planner_reset(g_planner);
        g_last_action = PLAN_NOTHING;
        return;
    }

    session::game_reader r(image_base());
    view v;
    planner_inputs in;
    memset(&in, 0, sizeof(in));
    in.now_ms = now;
    in.world_epoch = s.world_epoch;
    if (!read_view(r, &v)) {
        bump(g_counters.reads_failed);
        in.key = g_planner.key;  // nothing known: keep the key, ask for nothing
    } else {
        in.key = rec.allowed | (v.leader < 64 ? (1ull << v.leader) : 0);
        in.in_town = v.in_town ? 1 : 0;
        in.task_busy = v.task_busy ? 1 : 0;
        in.degraded = v.degraded ? 1 : 0;
        in.need = need_of(rec.allowed, v.leader, v.current, v.roster);
        in.world_ok = (s.head.flags & session::GATE_WORLD) != 0 && s.phase == session::PHASE_FIELD &&
                      (s.facts & session::FACT_CONTROL) != 0;
        if (v.in_town && v.degraded) {
            bump(g_counters.degraded);
        }
    }
    const int32_t action = plan(g_planner, in);
    g_last_inputs = in;
    g_last_action = action;
    if (action == PLAN_GAVE_UP && g_planner.applied + g_planner.refused != 0) {
        bump(g_counters.gave_up);
    }
    if (action != PLAN_PUSH) {
        return;
    }
    const uint64_t args[gtq::ARG_COUNT] = {in.key, in.need, 0, 0};
    uint32_t ticket = 0;
    const int32_t status = gtq::push(OP_TOWN_REBUILD, 0xFF, s.head.epoch, s.head.generation, REBUILD_TTL_MS, args,
                                     &ticket);
    g_pushed_ms = now;
    planner_pushed(g_planner, status == gtq::PUSH_OK, ticket, now);
    bump(status == gtq::PUSH_OK ? g_counters.pushed : g_counters.push_refused);
}

void stop()
{
    g_started = false;
    g_slot.withdraw();
    bump(g_counters.withdrawn);
    planner_reset(g_planner);
}

// ---- game thread: the rebuild operation -------------------------------------------------------------------------
int32_t op_rebuild(const gtq::job& j)
{
    int32_t result = REBUILD_APPLIED;
    session::session_record s;
    const uintptr_t base = image_base();
    uintptr_t block = 0;
    bool ok = false;
    uint64_t task = 0;
    if (!session::read(&s) || s.head.epoch != j.epoch || (s.head.flags & session::GATE_WORLD) == 0) {
        result = REBUILD_NO_WORLD;
    } else if (s.phase != session::PHASE_FIELD || (s.facts & session::FACT_CONTROL) == 0) {
        result = REBUILD_NOT_FIELD;
    } else if (!detail::town_mode_now(base, &block, &ok)) {
        result = ok ? REBUILD_NOT_TOWN : REBUILD_UNREADABLE;
    } else if (!load(at(base, facts::PARTY_TASK_PTR), &task, 8)) {
        result = REBUILD_UNREADABLE;
    } else if (task != 0) {
        result = REBUILD_BUSY;  // 0x37CA80 would overwrite the running task's pointer
    } else {
        const rematerialize_fn test = g_test_rematerialize;
        const rematerialize_fn game = reinterpret_cast<rematerialize_fn>(at(base, facts::REMATERIALIZE));
        (test != nullptr ? test : game)();
        task = 0;
        if (!load(at(base, facts::PARTY_TASK_PTR), &task, 8)) {
            result = REBUILD_UNREADABLE;
        } else if (task == 0) {
            result = REBUILD_POOL_FULL;
        }
    }
    bump(result >= 1 ? g_counters.applied : g_counters.refused);
    return result;
}

patch::record_slot<town_record>& slot()
{
    return g_slot;
}

const counters* stats()
{
    return &g_counters;
}

void test_set_rematerialize(rematerialize_fn fn)
{
    g_test_rematerialize = fn;
}

void test_set_image_base(uintptr_t base)
{
    __atomic_store_n(&detail::g_image_base, base, __ATOMIC_RELEASE);
}

void test_debug(debug_view* out)
{
    memset(out, 0, sizeof(*out));
    out->planner = g_planner;
    out->last_inputs = g_last_inputs;
    out->last_action = g_last_action;
    out->last_result = g_last_result;
    out->allowed = g_last_allowed;
}

void test_reset()
{
    g_slot.withdraw();
    memset(&g_counters, 0, sizeof(g_counters));
    planner_reset(g_planner);
    memset(&g_last_inputs, 0, sizeof(g_last_inputs));
    g_last_action = g_last_result = 0;
    g_last_allowed = 0;
    g_started = false;
    g_test_rematerialize = nullptr;
    __atomic_store_n(&detail::g_image_base, facts::IMAGE_VA, __ATOMIC_RELEASE);
}

}  // namespace ilc::towns
