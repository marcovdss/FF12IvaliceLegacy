// Ivalice Legacy co-op module: the UT-01 UI-tick handler (see tick.h).
#include "runtime/tick.h"

#include <string.h>

#include "bridge/bridge_internal.h"
#include "control/control.h"
#include "ilc/bridge_abi.h"
#include "input/input_service.h"
#include "runtime/game_thread.h"

namespace ilc::tick {
namespace {

constexpr bool same_id(const char* a, const char* b)
{
    while (*a != '\0' && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

constexpr int32_t find_row(const char* id)
{
    for (uint32_t row = 0; row < patch::SITE_COUNT; ++row) {
        if (same_id(patch::SITES[row].id, id)) {
            return static_cast<int32_t>(row);
        }
    }
    return -1;
}

constexpr int32_t UT01 = find_row("UT-01");
static_assert(UT01 >= 0 && patch::SITES[UT01].policy == ILC_SITE_POLICY_PATCH &&
                  patch::SITES[UT01].subsystem == ILC_SITE_SUBSYS_CORE && patch::SITES[UT01].kind == ILC_SITE_KIND_CALL,
              "UT-01 is the CORE call site the tick runs on");

constexpr uint32_t core_patch_rows()
{
    uint32_t n = 0;
    for (uint32_t row = 0; row < patch::SITE_COUNT; ++row) {
        n += patch::SITES[row].policy == ILC_SITE_POLICY_PATCH && patch::SITES[row].subsystem == ILC_SITE_SUBSYS_CORE;
    }
    return n;
}
// DRY mode lets CORE rows run; only the tick may be one, or a dry-armed boot would run another handler.
static_assert(core_patch_rows() == 1, "UT-01 must be the only CORE PATCH row");

counters g_counters;
volatile uint32_t g_in_tick = 0;
uint32_t g_pending_mask = 0;   // game thread only
bool g_mask_pending = false;   // game thread only
ilc_settings g_applied_copy;   // game thread only: the record the safe point just applied
bool g_input_pending = false;  // game thread only: g_applied_copy has not reached the input service yet

void bump(volatile uint64_t& counter)
{
    __atomic_add_fetch(&counter, 1, __ATOMIC_RELAXED);
}

}  // namespace

int32_t ut01(patch::ctx* c, uint32_t epoch)
{
    (void)c;      // the stock call's arguments stay exactly as they are
    (void)epoch;  // each job re-samples the liveness record itself, right before it runs
    if (__atomic_exchange_n(&g_in_tick, 1u, __ATOMIC_ACQ_REL) != 0) {
        bump(g_counters.reentered);
        return patch::RUN_ORIGINAL;
    }
    bump(g_counters.ticks);
    g_counters.last_tick_ms = patch::now_ms();

    uint32_t mask = 0;
    const uint32_t safe_point = bridge_safe_point(&mask, &g_applied_copy);
    if (safe_point == ILC_SAFE_POINT_APPLIED) {
        bump(g_counters.settings_applied);
        g_pending_mask = control::mask_for_safe_point(mask);  // kill switch and ordered isolation release
        g_mask_pending = true;
        g_input_pending = true;
    } else if (safe_point == ILC_SAFE_POINT_BUSY) {
        bump(g_counters.safe_point_busy);
    }
    // The input service stages the newest applied record for its next tick; a busy lock is retried next frame.
    if (g_input_pending && input::try_configure_from_settings(g_applied_copy)) {
        g_input_pending = false;
    }
    uint32_t restored = 0;
    if (control::take_reapply(&restored)) {  // co-op back on: the settings' mask again, at this safe point
        g_pending_mask = control::mask_for_safe_point(restored);
        g_mask_pending = true;
    }
    if (g_mask_pending) {
        if (patch::try_apply_subsystems(g_pending_mask)) {
            g_mask_pending = false;
            bump(g_counters.mask_applied);
        } else {
            bump(g_counters.mask_deferred);
        }
    }

    if (patch::current_mode() == patch::MODE_LIVE) {
        __atomic_add_fetch(&g_counters.jobs_drained, gtq::drain(), __ATOMIC_RELAXED);
    } else {
        __atomic_add_fetch(&g_counters.jobs_flushed, gtq::flush(gtq::REJECT_DISARMED), __ATOMIC_RELAXED);
    }
    __atomic_store_n(&g_in_tick, 0u, __ATOMIC_RELEASE);
    return patch::RUN_ORIGINAL;
}

bool attach()
{
    if (!patch::set_handler(static_cast<uint32_t>(UT01), ut01) ||
        !patch::set_site_armed(static_cast<uint32_t>(UT01), true)) {
        return false;
    }
    bridge_attach_tick();
    return true;
}

int32_t ut01_row()
{
    return UT01;
}

const counters* stats()
{
    return &g_counters;
}

void test_reset()
{
    memset(&g_counters, 0, sizeof(g_counters));
    g_in_tick = 0;
    g_pending_mask = 0;
    g_mask_pending = false;
    g_input_pending = false;
}

}  // namespace ilc::tick
