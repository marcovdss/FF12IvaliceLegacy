// Ivalice Legacy co-op module: diagnostics (see diag.h).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>
#include <x86intrin.h>

#include "runtime/diag.h"

#include "control/control.h"
#include "focus/focus.h"
#include "frontend/frontend.h"
#include "frontend/presenter.h"
#include "hud/canvas.h"
#include "hud/driver.h"
#include "hud/native.h"
#include "hud/target_info.h"
#include "hud/target_info_native.h"
#include "input/input_service.h"
#include "isolation/isolation.h"
#include "party/party.h"
#include "patch/call_island.h"
#include "runtime/game_thread.h"
#include "runtime/probe.h"
#include "runtime/tick.h"
#include "session/session.h"
#include "towns/towns.h"

namespace ilc::diag {
namespace {

constexpr uint32_t NONE = 0xFFFFFFFFu;

struct state {
    // calibration (written once by whoever calls calibrate_once first)
    volatile uint32_t calibrated;
    uint64_t tsc0;
    uint64_t perf0;
    // threads
    volatile uint32_t init_tid;
    volatile uint32_t init_done_tid;
    volatile uint32_t coop_tid;
    // install
    volatile uint32_t install_attempts;
    volatile uint32_t quiesce_retries;
    volatile uint32_t threads_probed;
    volatile uint32_t hooked;
    volatile uint32_t gated_off;
    volatile uint32_t info_mismatch;
    // co-op thread (one writer)
    volatile uint64_t beats;
    volatile uint64_t beat_max;     // performance-counter ticks
    volatile uint64_t gap_last;     // performance-counter ticks
    volatile uint64_t gap_max;
    uint64_t last_begin;
    // stall
    volatile uint64_t stall_post;   // ms << 32 | request id; 0 = nothing posted
    volatile uint32_t stall_running;
    volatile uint32_t stall_requests;
    volatile uint32_t stalls_done;
    volatile uint32_t stall_last_ms;
    // bridge (several script threads: compare-and-swap maxima)
    volatile uint64_t bridge_calls;
    volatile uint64_t bridge_max;
    volatile uint64_t poll_calls;
    volatile uint64_t poll_max;
};
state g;

uint64_t frequency()
{
    static volatile int64_t cached = 0;
    int64_t f = __atomic_load_n(&cached, __ATOMIC_RELAXED);
    if (f == 0) {
        LARGE_INTEGER q;
        QueryPerformanceFrequency(&q);
        f = q.QuadPart > 0 ? q.QuadPart : 1;
        __atomic_store_n(&cached, f, __ATOMIC_RELAXED);
    }
    return static_cast<uint64_t>(f);
}

uint32_t low(uint64_t v)
{
    return static_cast<uint32_t>(v);
}

uint32_t clamp32(uint64_t v)
{
    return v > 0xFFFFFFFFull ? 0xFFFFFFFFu : static_cast<uint32_t>(v);
}

uint32_t perf_us(uint64_t ticks)
{
    return clamp32(static_cast<uint64_t>(static_cast<unsigned __int128>(ticks) * 1000000u / frequency()));
}

uint32_t perf_ms(uint64_t ticks)
{
    return clamp32(static_cast<uint64_t>(static_cast<unsigned __int128>(ticks) * 1000u / frequency()));
}

// Time-stamp counter ticks -> microseconds, from the calibration span; 0 until it is long enough.
uint32_t tsc_us(uint64_t ticks)
{
    if (__atomic_load_n(&g.calibrated, __ATOMIC_ACQUIRE) != 1) {
        return 0;  // not started, or the reference point is being written
    }
    const uint64_t tsc_span = __rdtsc() - g.tsc0;
    const uint64_t perf_span = perf_now() - g.perf0;
    if (tsc_span == 0 || perf_span * 1000u < CALIBRATE_MIN_MS * frequency()) {
        return 0;
    }
    // us = ticks * (perf_span / frequency) / tsc_span * 1e6
    const unsigned __int128 num = static_cast<unsigned __int128>(ticks) * perf_span * 1000000u;
    const unsigned __int128 den = static_cast<unsigned __int128>(frequency()) * tsc_span;
    return clamp32(static_cast<uint64_t>(num / den));
}

void raise_max(volatile uint64_t& slot, uint64_t value)
{
    uint64_t seen = __atomic_load_n(&slot, __ATOMIC_RELAXED);
    while (value > seen && !__atomic_compare_exchange_n(&slot, &seen, value, true, __ATOMIC_RELAXED, __ATOMIC_RELAXED)) {
    }
}

bool query_op(uint32_t op)
{
    return op == ILC_OP_GET_STATUS || op == ILC_OP_GET_SEATS || op == ILC_OP_GET_DEVICES || op == ILC_OP_POP_EVENTS ||
           op == ILC_OP_GET_SETTINGS || op == ILC_OP_GET_COUNTERS;
}

}  // namespace

// ---- boot -------------------------------------------------------------------------------------------------------
void calibrate_once()
{
    uint32_t expected = 0;
    // 2 = being written; readers wait for 1 (they just report 0 meanwhile).
    if (!__atomic_compare_exchange_n(&g.calibrated, &expected, 2u, false, __ATOMIC_ACQ_REL, __ATOMIC_RELAXED)) {
        return;
    }
    g.perf0 = perf_now();
    g.tsc0 = __rdtsc();
    __atomic_store_n(&g.calibrated, 1u, __ATOMIC_RELEASE);
}

void note_init_thread(uint32_t thread_id)
{
    __atomic_store_n(&g.init_tid, thread_id, __ATOMIC_RELAXED);
}

void note_init_done_thread(uint32_t thread_id)
{
    __atomic_store_n(&g.init_done_tid, thread_id, __ATOMIC_RELAXED);
}

void note_coop_thread(uint32_t thread_id)
{
    __atomic_store_n(&g.coop_tid, thread_id, __ATOMIC_RELAXED);
}

void note_install(const patch::report& r, uint32_t attempts)
{
    g.install_attempts = attempts;
    g.quiesce_retries = r.quiesce_retries;
    g.threads_probed = r.threads_probed;
    g.hooked = r.hooked;
    g.gated_off = r.gated_off;
    g.info_mismatch = r.info_mismatch;
}

// ---- co-op thread -----------------------------------------------------------------------------------------------
void loop_begin()
{
    g.last_begin = perf_now();
}

uint64_t beat_begin()
{
    const uint64_t now = perf_now();
    if (g.last_begin != 0) {
        const uint64_t gap = now - g.last_begin;
        g.gap_last = gap;
        if (gap > g.gap_max) {
            g.gap_max = gap;
        }
    }
    g.last_begin = now;
    return now;
}

void beat_end(uint64_t started)
{
    const uint64_t spent = perf_now() - started;
    if (spent > g.beat_max) {
        g.beat_max = spent;
    }
    __atomic_add_fetch(&g.beats, 1, __ATOMIC_RELAXED);
}

uint32_t take_stall(uint32_t* request_id)
{
    const uint64_t post = __atomic_exchange_n(&g.stall_post, 0ull, __ATOMIC_ACQ_REL);
    *request_id = static_cast<uint32_t>(post);
    return static_cast<uint32_t>(post >> 32);
}

void note_stall_done(uint32_t slept_ms)
{
    g.stall_last_ms = slept_ms;
    __atomic_add_fetch(&g.stalls_done, 1u, __ATOMIC_RELAXED);
    __atomic_store_n(&g.stall_running, 0u, __ATOMIC_RELEASE);
}

// ---- bridge -------------------------------------------------------------------------------------------------------
bool post_stall(uint32_t ms, uint32_t request_id)
{
    uint32_t idle = 0;
    // stall_running covers the whole span from the post to the end of the sleep (note_stall_done clears it).
    if (ms == 0 || !__atomic_compare_exchange_n(&g.stall_running, &idle, 1u, false, __ATOMIC_ACQ_REL, __ATOMIC_RELAXED)) {
        return false;
    }
    __atomic_add_fetch(&g.stall_requests, 1u, __ATOMIC_RELAXED);
    __atomic_store_n(&g.stall_post, static_cast<uint64_t>(ms) << 32 | request_id, __ATOMIC_RELEASE);
    return true;
}

uint64_t perf_now()
{
    LARGE_INTEGER q;
    QueryPerformanceCounter(&q);
    return static_cast<uint64_t>(q.QuadPart);
}

void note_bridge_call(uint32_t op, uint64_t started)
{
    const uint64_t spent = perf_now() - started;
    __atomic_add_fetch(&g.bridge_calls, 1, __ATOMIC_RELAXED);
    raise_max(g.bridge_max, spent);
    if (query_op(op)) {
        __atomic_add_fetch(&g.poll_calls, 1, __ATOMIC_RELAXED);
        raise_max(g.poll_max, spent);
    }
}

void fill(ilc_counters* o)
{
    calibrate_once();
    const uint64_t now = patch::now_ms();
    o->layout = ILC_COUNTERS_LAYOUT;
    o->now_ms = low(now);
    o->caller_thread_id = GetCurrentThreadId();
    o->init_thread_id = g.init_tid;
    o->init_done_thread_id = g.init_done_tid;
    o->coop_thread_id = g.coop_tid;

    // The patch layer: mode, liveness, subsystems.
    const tick::counters* t = tick::stats();
    o->mode = patch::current_mode();
    o->live_at_ms = low(t->live_at_ms);
    const patch::liveness_view live = patch::sample_liveness();
    o->liveness_armed = live.armed;
    o->liveness_epoch = live.epoch;
    o->liveness_age_ms = live.heartbeat_ms == 0 ? NONE : clamp32(live.now_ms >= live.heartbeat_ms ? live.now_ms - live.heartbeat_ms : 0);
    o->liveness_fresh = live.fresh ? 1u : 0u;
    o->stale_fallbacks = low(patch::stale_fallbacks());
    o->stale_ticks = low(t->stale_ticks);
    o->stale_releases = low(t->stale_releases);
    o->subsystems_gates = patch::subsystems_mask();
    o->subsystems_faulted = patch::faulted_mask();
    uint32_t live_bits = 0;
    for (uint32_t bit = 1; bit <= 0x80u; bit <<= 1) {
        if (patch::subsystem_live(bit)) {
            live_bits |= bit;
        }
    }
    o->subsystems_live = live_bits;

    // The co-op thread.
    o->beats = low(g.beats);
    o->beat_max_us = perf_us(g.beat_max);
    o->beat_gap_last_ms = perf_ms(g.gap_last);
    o->beat_gap_max_ms = perf_ms(g.gap_max);
    o->stall_requests = g.stall_requests;
    o->stalls_done = g.stalls_done;
    o->stall_last_ms = g.stall_last_ms;

    // The UI tick.
    o->tick_count = low(t->ticks);
    o->tick_max_us = tsc_us(t->max_ticks);
    o->tick_reentered = low(t->reentered);
    o->tick_settings_applied = low(t->settings_applied);
    o->tick_safe_point_busy = low(t->safe_point_busy);
    o->tick_mask_applied = low(t->mask_applied);
    o->tick_mask_deferred = low(t->mask_deferred);
    o->tick_live_switches = low(t->live_switches);
    o->tick_live_deferred = low(t->live_deferred);
    o->tick_jobs_drained = low(t->jobs_drained);
    o->tick_jobs_flushed = low(t->jobs_flushed);

    // The bridge's own timing (its counters proper are filled by the bridge under its lock).
    o->bridge_calls = low(g.bridge_calls);
    o->bridge_max_us = perf_us(g.bridge_max);
    o->poll_calls = low(g.poll_calls);
    o->poll_max_us = perf_us(g.poll_max);

    // The hook transaction and the detours.
    uint32_t patch_rows = 0;
    uint64_t entered = 0, stock = 0, handled = 0, declined = 0;
    for (uint32_t row = 0; row < patch::SITE_COUNT; ++row) {
        if (patch::SITES[row].policy == ILC_SITE_POLICY_PATCH) {
            ++patch_rows;
        }
        const patch::site_stats* s = patch::stats(row);
        entered += s->entered;
        stock += s->stock;
        handled += s->handled;
        declined += s->declined;
    }
    o->patch_rows = patch_rows;
    o->hooks_installed = g.hooked;
    o->gated_off = g.gated_off;
    o->info_mismatch = g.info_mismatch;
    o->install_attempts = g.install_attempts;
    o->quiesce_retries = g.quiesce_retries;
    o->threads_probed = g.threads_probed;
    const patch::island::stats_view islands = patch::island::stats();
    o->island_commits = islands.commits;
    o->island_busy = islands.busy_attempts;
    o->detour_entered = low(entered);
    o->detour_stock = low(stock);
    o->detour_handled = low(handled);
    o->detour_declined = low(declined);

    // The game-thread queue.
    const gtq::counters* q = gtq::stats();
    o->jobs_pushed = low(q->pushed);
    o->jobs_push_full = low(q->push_full);
    o->jobs_push_refused = low(q->push_refused);
    o->jobs_ran = low(q->ran);
    o->jobs_rejected = low(q->rejected);
    o->jobs_reaped = low(q->reaped);

    // The co-op switches.
    const control::counters* c = control::stats();
    o->coop_switch = control::current_state();
    o->kill_presses = low(c->kill_presses);
    o->kills = low(c->kills);
    o->revives = low(c->revives);
    o->released_by_settle = low(c->released_by_settle);
    o->released_by_cap = low(c->released_by_cap);
    o->last_release_ms = low(c->last_release_ms);
    o->toggles = low(c->toggles);
    o->toggles_ignored = low(c->toggles_ignored);
    o->mask_busy = low(c->mask_busy);
    o->capture_held = low(c->capture_held);
    o->capture_capped = low(c->capture_capped);
    o->capture_word = input::kill_capture_word();

    // The session, the party and the towns.
    const session::counters* se = session::stats();
    o->session_epoch_bumps = low(se->epoch_bumps);
    o->session_read_failures = low(se->read_failures);
    const party::counters* p = party::stats();
    o->party_requests = low(p->requests);
    o->party_applied = low(p->applied);
    o->party_refused = low(p->refused);
    o->party_timed_out = low(p->requests_timed_out);
    o->party_watcher_changes = low(p->watcher_changes);
    o->party_external_swaps = low(p->external_swaps);
    o->party_ko_promotions = low(p->ko_promotions);
    o->party_town_rebuilds = low(p->town_rebuilds);
    o->party_town_takes = low(p->town_takes);
    const towns::counters* tw = towns::stats();
    o->towns_rebuilds = low(tw->applied);
    o->towns_gave_up = low(tw->gave_up);

    // The HUD: canvas (frame hook), frame driver, presenter, native HUD changes.
    const hud::counters* cv = hud::stats();
    o->canvas_reached = low(cv->reached);
    o->canvas_drawn = low(cv->drawn);
    o->canvas_published = low(cv->published);
    o->canvas_declined_off = low(cv->declined_off);
    o->canvas_declined_frame = low(cv->declined_frame);
    o->canvas_declined_context = low(cv->declined_context);
    o->canvas_declined_budget = low(cv->declined_budget);
    o->canvas_ring_gaps = low(cv->ring_gaps);
    o->canvas_faults = low(cv->faults);
    // A copy of the game thread's budget, read from the script thread without a lock: each field is untorn (aligned),
    // but the three can come from different frames.
    const hud::budget_view b = hud::budget();
    o->canvas_frame_use = clamp32(b.frame_use);
    o->canvas_peak = clamp32(b.peak);
    o->canvas_capacity = clamp32(b.capacity);
    const hud::driver::counters* dr = hud::driver::stats();
    o->driver_frames = low(dr->frames);
    o->driver_composed = low(dr->composed);
    o->driver_torn = low(dr->torn);
    o->driver_pass_faults = low(dr->pass_faults);
    o->driver_dropped_frames = low(dr->dropped_frames);
    o->driver_menus_no_view = low(dr->menus_no_view);
    const uint64_t drawn = frontend::presenter::drawn_ms();
    o->presenter_age_ms = drawn == 0 ? NONE : clamp32(now >= drawn ? now - drawn : 0);
    const hud::native::counters* nh = hud::native::stats();
    o->native_hud_jobs = low(nh->jobs_ran);
    o->native_hud_give_backs = low(nh->give_backs);

    // The Target Info: ours, and the native window's give-back.
    const hud::target_info::counters* ti = hud::target_info::stats();
    o->ti_native_closed = low(ti->native_closed);
    o->ti_native_stock = low(ti->native_stock);
    const hud::target_info::giveback::counters* gb = hud::target_info::giveback::stats();
    o->ti_give_opens = low(gb->opens);
    o->ti_give_opened = low(gb->opened);
    o->ti_give_cancels = low(gb->cancels);
    o->ti_give_giveups = low(gb->giveups);
    o->ti_give_rehides = low(gb->rehides);
    o->ti_pin_checks = low(gb->pin_checks);
    o->ti_pin_off = low(gb->pin_off);
    o->ti_aims = low(gb->aims);
    o->ti_gated = low(gb->gated);
    const hud::target_info::giveback::status_view gs = hud::target_info::giveback::status();
    o->ti_pins = gs.pins;
    o->ti_pin_row = gs.pin_row;

    // The co-op battle menus.
    const frontend::counters* fe = frontend::stats();
    o->menu_opens = low(fe->opens);
    o->menu_party_requests = low(fe->party_requests);
    o->menu_p1_givebacks = low(fe->p1_givebacks);
    // The co-op thread's state, copied without a lock: each field is untorn, but the two can come from different beats.
    frontend::state_view fs;
    frontend::read_state(&fs);
    o->menu_owns_p1 = fs.owns_p1;
    o->menu_presenter_live = fs.presenter_live;

    // Input isolation and the background setting.
    const isolation::counters* is = isolation::stats();
    o->iso_background = low(is->background);
    o->iso_masked = low(is->masked);
    o->iso_substituted = low(is->substituted);
    o->iso_probe_failed = low(is->probe_failed);
    const focus::counters* fo = focus::stats();
    o->focus_forced = low(fo->forced);
    o->focus_probe_failed = low(fo->probe_failed);
    o->frame_hook_us_last = tsc_us(cv->hook_ticks_last);
    o->frame_hook_us_max = tsc_us(cv->hook_ticks_max);
    const uint64_t hook_calls = cv->hook_calls;
    o->frame_hook_us_avg = hook_calls != 0 ? tsc_us(cv->hook_ticks_total / hook_calls) : 0;
    o->frame_hook_calls = low(hook_calls);
    o->canvas_atlas_lookups = low(cv->atlas_lookups);
    o->canvas_text_last = low(cv->text_last);
    o->canvas_chars_last = low(cv->chars_last);
    const probe_counters pc = probe_stats();
    o->probe_queries = low(pc.queries);
    o->probe_hot_hits = low(pc.hot_hits);
    o->probe_hot_misses = low(pc.hot_misses);
    o->canvas_ring_ours_last = clamp32(cv->ring_ours_last);
    o->canvas_zero_output = low(cv->zero_output);
    o->canvas_child_space = low(cv->child_space);
    o->canvas_declined_matrix = low(cv->declined_matrix);
    o->probe_hot_failed = low(pc.hot_failed);
}

void test_reset()
{
    memset(&g, 0, sizeof(g));
}

}  // namespace ilc::diag
