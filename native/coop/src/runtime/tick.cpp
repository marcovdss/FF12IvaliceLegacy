// Ivalice Legacy co-op module: the UT-01 UI-tick handler (see tick.h).
#include "runtime/tick.h"

#include <string.h>
#include <x86intrin.h>

#include "bridge/bridge_internal.h"
#include "control/control.h"
#include "ilc/bridge_abi.h"
#include "hud/native.h"
#include "hud/rings.h"
#include "hud/target_info_native.h"
#include "input/input_service.h"
#include "overlay/hide.h"
#include "overlay/overlay.h"
#include "party/party.h"
#include "runtime/game_thread.h"
#include "wait/wait.h"

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

// DRY mode lets CORE rows run. Only the tick and the background rows (focus.h: they act on the player's own setting
// alone, never on co-op state) may be CORE, or a dry-armed boot would run another subsystem's handler.
constexpr bool core_allowed(const char* id)
{
    return same_id(id, "UT-01") || same_id(id, "FP-01") || same_id(id, "FP-03") || same_id(id, "FP-04") ||
           same_id(id, "IN-06");
}
constexpr uint32_t core_patch_rows()
{
    uint32_t n = 0;
    for (uint32_t row = 0; row < patch::SITE_COUNT; ++row) {
        if (patch::SITES[row].policy == ILC_SITE_POLICY_PATCH && patch::SITES[row].subsystem == ILC_SITE_SUBSYS_CORE) {
            n += core_allowed(patch::SITES[row].id) ? 1u : 1000u;
        }
    }
    return n;
}
static_assert(core_patch_rows() == 5, "the CORE PATCH rows are exactly UT-01 and the four background rows");

counters g_counters;
volatile uint32_t g_in_tick = 0;
uint32_t g_pending_mask = 0;   // game thread only
bool g_mask_pending = false;   // game thread only
ilc_settings g_applied_copy;   // game thread only: the record the safe point just applied
bool g_input_pending = false;  // game thread only: g_applied_copy has not reached the input service yet
bool g_live_wanted = false;    // game thread only: a record (generation >= 1) was applied; LIVE once its mask is in
bool g_live_done = false;      // game thread only: the one DRY -> LIVE switch has happened (never undone)

void bump(volatile uint64_t& counter)
{
    __atomic_add_fetch(&counter, 1, __ATOMIC_RELAXED);
}

// The handler's own cost, in time-stamp counter ticks (no import: the tick also runs under the host tests'
// emulator). The diagnostics op converts it to microseconds.
void note_cost(uint64_t started)
{
    const uint64_t spent = __rdtsc() - started;
    if (spent > g_counters.max_ticks) {
        g_counters.max_ticks = spent;  // one writer: the game thread
    }
}

// The one DRY -> LIVE switch: after the first applied record's mask reached the arm gates (never before, so a
// LIVE layer never runs on the boot's closed mask by accident and never on the native defaults).
void go_live_if_due()
{
    if (g_live_done || !g_live_wanted || g_mask_pending) {
        return;
    }
    if (patch::current_mode() != patch::MODE_LIVE) {
        if (!patch::try_set_mode(patch::MODE_LIVE)) {
            bump(g_counters.live_deferred);
            return;
        }
        bump(g_counters.live_switches);
        g_counters.live_at_ms = patch::now_ms();
    }
    g_live_done = true;
    g_live_wanted = false;
}

}  // namespace

int32_t ut01(patch::ctx* c, uint32_t epoch)
{
    (void)c;  // the stock call's arguments stay exactly as they are; each job re-samples the liveness record itself
    if (__atomic_exchange_n(&g_in_tick, 1u, __ATOMIC_ACQ_REL) != 0) {
        bump(g_counters.reentered);
        return patch::RUN_ORIGINAL;
    }
    const uint64_t started = __rdtsc();
    bump(g_counters.ticks);
    g_counters.last_tick_ms = patch::now_ms();

    // Co-op back on: the settings' mask again, taken before the safe point so a record applied in this same frame
    // (newer) replaces it below instead of being overwritten by it.
    uint32_t restored = 0;
    if (control::take_reapply(&restored)) {
        g_pending_mask = control::mask_for_safe_point(restored);
        g_mask_pending = true;
    }
    uint32_t mask = 0;
    const uint32_t safe_point = bridge_safe_point(&mask, &g_applied_copy);
    if (safe_point == ILC_SAFE_POINT_APPLIED) {
        bump(g_counters.settings_applied);
        g_pending_mask = control::mask_for_safe_point(mask);  // kill switch and ordered isolation release
        g_mask_pending = true;
        g_input_pending = true;
        if (g_applied_copy.header.generation >= 1) {
            g_live_wanted = true;  // the native defaults (generation 0) are never applied here, and never go LIVE
        }
    } else if (safe_point == ILC_SAFE_POINT_BUSY) {
        bump(g_counters.safe_point_busy);
    }
    // The input service stages the newest applied record for its next tick; a busy lock is retried next frame.
    if (g_input_pending && input::try_configure_from_settings(g_applied_copy)) {
        g_input_pending = false;
    }
    if (g_mask_pending) {
        // A mask computed in an earlier frame (its apply was deferred) is passed through the switch again: while
        // co-op is off nothing is armed, whatever was computed before the kill.
        g_pending_mask = control::mask_for_safe_point_retry(g_pending_mask);
        if (patch::try_apply_subsystems(g_pending_mask)) {
            g_mask_pending = false;
            bump(g_counters.mask_applied);
        } else {
            bump(g_counters.mask_deferred);
        }
    }
    go_live_if_due();

    if (patch::current_mode() == patch::MODE_LIVE) {
        // The Wait engine's turn comes first: a pause taken or given back here is what this sub-step's stock UI tick
        // and the next field tick see (wait.h). It runs whatever the subsystem bits say: a pause it owns is released
        // as soon as nobody may want it.
        wait::step(epoch);
        party::tick_before();  // the leader watcher's first look: changes made earlier in this frame
        __atomic_add_fetch(&g_counters.jobs_drained, gtq::drain(), __ATOMIC_RELAXED);
    } else {
        wait::step_dry();
        __atomic_add_fetch(&g_counters.jobs_flushed, gtq::flush(gtq::REJECT_DISARMED), __ATOMIC_RELAXED);
    }
    // The native HUD changes are given back on the frame they stop being wanted (F8, RENDER off, DRY).
    hud::native::tick(patch::current_mode() == patch::MODE_LIVE);
    // The seats' area rings are released on the frame they stop being wanted (F8, TARGETS_CARDS off, DRY).
    hud::rings::tick(patch::current_mode() == patch::MODE_LIVE);
    // The native TARGET INFO: given back for the field target mode once ours has stopped, closed under ours once it
    // runs again (data writes into the window owner; any mode: it only hands back what TI-01 held).
    hud::target_info::giveback::tick(patch::now_ms(), epoch);
    // The overlay's HUD heartbeat while Target Info has faded the combat log out (HD-01 is silent then).
    if (patch::current_mode() == patch::MODE_LIVE) {
        overlay::note_ui_tick();
    }
    // The vanilla HUD pieces our overlay replaces: hidden by their draw bit while ours are presented, given back the
    // tick they are not (overlay/hide.h).
    overlay::hide::tick(patch::current_mode() == patch::MODE_LIVE);
    const int32_t decision = party::after_wanted() ? patch::CALL_AROUND : patch::RUN_ORIGINAL;
    note_cost(started);
    __atomic_store_n(&g_in_tick, 0u, __ATOMIC_RELEASE);
    // While the party subsystem may act, the stock tick runs from our frame so the leader watcher can look again
    // after it (a menu commit inside the stock tick changes the leader too). Otherwise the stock call as it is.
    return decision;
}

int32_t ut01_stale(patch::ctx* c, uint32_t epoch)
{
    (void)c;
    // The co-op thread stopped beating (liveness stale): the handler above does not run, so a Wait pause the engine
    // owns would stay held until the thread recovers. Give it back here, and the stock party list and target circles
    // too (a give-back only writes stock bytes over bytes it recognises as its own; the co-op thread applies them
    // again once it beats), and the native TARGET INFO for a target mode in progress (target_info_native.h).
    // Nothing else runs without a fresh beat.
    if (__atomic_exchange_n(&g_in_tick, 1u, __ATOMIC_ACQ_REL) != 0) {
        bump(g_counters.reentered);
        return patch::RUN_ORIGINAL;
    }
    bump(g_counters.stale_ticks);
    if (wait::release_if_owned()) {
        bump(g_counters.stale_releases);
    }
    hud::native::tick(false);
    overlay::hide::stale();   // the vanilla HUD pieces back: nothing of ours is fed without a beat
    // Ours stops drawing without a beat: the native TARGET INFO comes back for a target mode in progress.
    hud::target_info::giveback::tick(patch::now_ms(), epoch);
    __atomic_store_n(&g_in_tick, 0u, __ATOMIC_RELEASE);
    return patch::RUN_ORIGINAL;
}

bool attach()
{
    if (!patch::set_handler(static_cast<uint32_t>(UT01), ut01) ||
        !patch::set_stale_handler(static_cast<uint32_t>(UT01), ut01_stale) ||
        !patch::set_site_armed(static_cast<uint32_t>(UT01), true)) {
        return false;
    }
    bridge_attach_tick();
    return true;
}

int32_t test_run_stale(uint32_t epoch)
{
    patch::ctx c = {};
    c.index = static_cast<uint64_t>(UT01);
    return ut01_stale(&c, epoch);
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
    g_live_wanted = false;
    g_live_done = false;
}

}  // namespace ilc::tick
