// Ivalice Legacy co-op module, host tests only: exports for town visibility (src/towns).
//
// The rules and the planner are pure and passed through unchanged. The handler, the operation and the co-op-thread
// step run natively on synthetic memory the test builds (and, in the emulator tests, on the image twin). The game's
// re-materialize function is never executed natively: the operation's call goes to a stub chosen here.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "patch/patch_engine.h"
#include "session/session.h"
#include "towns/filter.h"
#include "towns/towns.h"
#include "towns/towns_facts.h"

using namespace ilc;
using namespace ilc::towns;

namespace {

volatile uint32_t g_remat_calls = 0;
uint64_t g_task_value = 0;   // what the "task created" stub stores into the task pointer
uintptr_t g_task_address = 0;

void remat_creates_task()
{
    __atomic_add_fetch(&g_remat_calls, 1u, __ATOMIC_RELAXED);
    if (g_task_address != 0) {
        memcpy(reinterpret_cast<void*>(g_task_address), &g_task_value, sizeof(g_task_value));
    }
}

void remat_pool_full()
{
    __atomic_add_fetch(&g_remat_calls, 1u, __ATOMIC_RELAXED);
}

}  // namespace

extern "C" {

uint32_t ILCT_TownSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(town_record), sizeof(planner_state), sizeof(planner_inputs), sizeof(counters),
                              sizeof(debug_view)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

uint64_t ILCT_TownControlled(const session::session_record* r)
{
    return controlled_members(*r);
}

int32_t ILCT_TownKeeps(uint64_t allowed, uint32_t leader, uint32_t member)
{
    return keeps(allowed, static_cast<uint8_t>(leader), static_cast<uint16_t>(member)) ? 1 : 0;
}

int32_t ILCT_TownMode(int32_t table_ok, uint32_t location, uint32_t count, uint32_t town_byte, uint32_t mode)
{
    return town_mode(table_ok != 0, location, count, static_cast<uint8_t>(town_byte), mode) ? 1 : 0;
}

int32_t ILCT_TownFilter(uint64_t allowed, uint32_t leader, uint16_t* current, uint16_t* ui)
{
    return filter_slots(allowed, static_cast<uint8_t>(leader), current, ui);
}

uint32_t ILCT_TownNeed(uint64_t allowed, uint32_t leader, const uint16_t* current, const uint16_t* roster)
{
    return need_of(allowed, static_cast<uint8_t>(leader), current, roster);
}

void ILCT_TownPlanReset(planner_state* s)
{
    planner_reset(*s);
}

int32_t ILCT_TownPlan(planner_state* s, const planner_inputs* in)
{
    return plan(*s, *in);
}

void ILCT_TownPlanPushed(planner_state* s, int32_t accepted, uint32_t ticket, uint64_t now)
{
    planner_pushed(*s, accepted != 0, ticket, now);
}

void ILCT_TownPlanResult(planner_state* s, int32_t result)
{
    planner_result(*s, result);
}

void ILCT_TownConstants(uint64_t* out)
{
    const uint64_t v[] = {SHOW_SETTLE_MS, HIDE_SETTLE_MS, MIN_INTERVAL_MS, MAX_APPLIED, MAX_REFUSED,
                          OP_TOWN_REBUILD, REBUILD_TTL_MS};
    memcpy(out, v, sizeof(v));
}

int32_t ILCT_TownAttach(void)
{
    return attach() ? 1 : 0;
}

int32_t ILCT_TownRow(void)
{
    return row();
}

int32_t ILCT_TownInvoke(patch::ctx* c, uint32_t epoch)
{
    c->index = static_cast<uint64_t>(row());
    return tw01(c, epoch);
}

void ILCT_TownPublish(const town_record* r)
{
    slot().publish(*r);
}

void ILCT_TownWithdraw(void)
{
    slot().withdraw();
}

int32_t ILCT_TownRecord(town_record* out)
{
    return slot().read(*out) ? 1 : 0;
}

void ILCT_TownSessionPublish(const session::session_record* r)
{
    session::slot().publish(*r);
}

void ILCT_TownSessionWithdraw(void)
{
    session::slot().withdraw();
}

void ILCT_TownStart(uint64_t base, uint64_t now)
{
    start(static_cast<uintptr_t>(base), now);
}

void ILCT_TownStep(uint64_t now)
{
    step(now);
}

void ILCT_TownStop(void)
{
    stop();
}

void ILCT_TownReset(void)
{
    test_reset();
    g_remat_calls = 0;
    g_task_address = 0;
    g_task_value = 0;
}

void ILCT_TownSetImageBase(uint64_t base)
{
    test_set_image_base(static_cast<uintptr_t>(base));
}

// mode 0: the game's function (emulator tests only); 1: a stub that stores `task` at `task_address` (a task was
// created); 2: a stub that creates nothing (the game's task pool was full).
void ILCT_TownSetRematerialize(uint32_t mode, uint64_t task_address, uint64_t task)
{
    g_task_address = static_cast<uintptr_t>(task_address);
    g_task_value = task;
    test_set_rematerialize(mode == 1 ? remat_creates_task : mode == 2 ? remat_pool_full : nullptr);
}

uint32_t ILCT_TownRematerializeCalls(void)
{
    return g_remat_calls;
}

int32_t ILCT_TownOp(uint32_t epoch)
{
    gtq::job j;
    memset(&j, 0, sizeof(j));
    j.op = OP_TOWN_REBUILD;
    j.epoch = epoch;
    j.ttl_ms = REBUILD_TTL_MS;
    return op_rebuild(j);
}

void ILCT_TownStats(counters* out)
{
    memcpy(out, stats(), sizeof(*out));
}

void ILCT_TownDebug(debug_view* out)
{
    test_debug(out);
}

}  // extern "C"
