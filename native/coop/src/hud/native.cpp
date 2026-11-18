// Ivalice Legacy co-op module: the native HUD changes and who makes them (see native.h).
#include "hud/native.h"

#include <string.h>

#include "hud/circles.h"
#include "hud/driver.h"
#include "hud/relayout.h"
#include "patch/patch_engine.h"
#include "patch/site_table.h"
#include "session/session.h"

namespace ilc::hud::native {
namespace {

counters g_counters;
volatile uint32_t g_attached = 0;
volatile uint32_t g_enabled = 1;
volatile uint32_t g_parts = PART_ALL;
volatile uint32_t g_wanted_seen = 0;
volatile int32_t g_force_live = -1;
volatile int32_t g_last_job = 0;

// co-op thread only
bool g_in_flight = false;
uint32_t g_ticket = 0;
uint64_t g_pushed_ms = 0;
bool g_pushed_once = false;

void bump(volatile uint64_t& c)
{
    __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED);
}

bool render_live()
{
    const int32_t forced = g_force_live;
    if (forced >= 0) {
        return forced != 0;
    }
    return patch::subsystem_live(ILC_SITE_SUBSYS_RENDER);
}

int32_t run_job(uint64_t now_ms)
{
    const uint32_t p = __atomic_load_n(&g_parts, __ATOMIC_ACQUIRE);
    if ((p & PART_SHRINK) != 0) {
        relayout::ensure_reduced(now_ms);
    }
    if ((p & PART_CIRCLES) != 0) {
        circles::ensure_off();
    }
    bump(g_counters.jobs_ran);
    return gtq::RESULT_DONE;
}

int32_t op_native(const gtq::job& j)
{
    (void)j;
    // The drain has checked RENDER, the epoch, the generation and the world; the element switch may have changed
    // since the push.
    if (__atomic_load_n(&g_enabled, __ATOMIC_ACQUIRE) == 0) {
        return gtq::RESULT_DONE;
    }
    return run_job(patch::now_ms());
}

void forget_flight()
{
    g_in_flight = false;
    g_ticket = 0;
}

}  // namespace

bool attach()
{
    if (!gtq::register_op(OP_HUD_NATIVE, ILC_SITE_SUBSYS_RENDER, op_native)) {
        return false;
    }
    __atomic_store_n(&g_attached, 1u, __ATOMIC_RELEASE);
    return true;
}

void set_enabled(bool on)
{
    __atomic_store_n(&g_enabled, on ? 1u : 0u, __ATOMIC_RELEASE);
}

void set_parts(uint32_t p)
{
    __atomic_store_n(&g_parts, p & PART_ALL, __ATOMIC_RELEASE);
}

uint32_t parts()
{
    return __atomic_load_n(&g_parts, __ATOMIC_ACQUIRE);
}

bool wanted()
{
    // The reduced party list and circles off make room for, and stand in for, pieces of the co-op layer: with the layer
    // off (0.1.57 interim) the native list and circles stay stock (a held change is given back).
    return __atomic_load_n(&g_enabled, __ATOMIC_ACQUIRE) != 0 && parts() != 0 && render_live() &&
           driver::layer_enabled();
}

void step(uint64_t now_ms)
{
    if (__atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) == 0) {
        return;
    }
    if (g_in_flight) {
        const int32_t res = gtq::await_result(g_ticket, g_pushed_ms, JOB_TTL_MS, nullptr);
        if (res != gtq::RESULT_PENDING) {
            g_last_job = res;
            forget_flight();
        }
    }
    if (!wanted()) {
        forget_flight();
        return;
    }
    if (g_in_flight || (g_pushed_once && now_ms - g_pushed_ms < RETRY_MS)) {
        return;
    }
    session::session_record s;
    if (!session::read(&s) || s.head.epoch == 0 || (s.head.flags & session::GATE_WORLD) == 0) {
        return;
    }
    const uint64_t args[gtq::ARG_COUNT] = {0, 0, 0, 0};
    uint32_t ticket = 0;
    const int32_t status = gtq::push(OP_HUD_NATIVE, 0xFF, s.head.epoch, s.head.generation, JOB_TTL_MS, args, &ticket);
    g_pushed_ms = now_ms;
    g_pushed_once = true;
    if (status == gtq::PUSH_OK) {
        g_in_flight = true;
        g_ticket = ticket;
        bump(g_counters.pushed);
    } else {
        bump(g_counters.push_refused);
    }
}

void stop()
{
    forget_flight();
}

void tick(bool live)
{
    if (__atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) == 0) {
        return;
    }
    const bool want = live && wanted();
    const uint32_t p = parts();
    g_wanted_seen = want ? 1u : 0u;
    const bool give_layout = !(want && (p & PART_SHRINK) != 0) && relayout::owned();
    const bool give_circles = !(want && (p & PART_CIRCLES) != 0) && circles::owned();
    if (!give_layout && !give_circles) {
        return;
    }
    if (give_layout) {
        relayout::restore();
    }
    if (give_circles) {
        circles::restore();
    }
    bump(g_counters.give_backs);
}

void restore_at_exit()
{
    if (!relayout::owned() && !circles::owned()) {
        return;
    }
    relayout::restore_waiting(EXIT_WAIT_MS);
    circles::restore();
    bump(g_counters.exit_restores);
}

status_view status()
{
    status_view v;
    memset(&v, 0, sizeof(v));
    v.attached = g_attached != 0 ? 1 : 0;
    v.enabled = g_enabled != 0 ? 1 : 0;
    v.wanted = g_wanted_seen != 0 ? 1 : 0;
    v.layout_state = relayout::status().state;
    const circles::status_view c = circles::status();
    v.circles_state = c.state;
    v.circles_site = c.site;
    v.last_job = g_last_job;
    return v;
}

const counters* stats()
{
    return &g_counters;
}

void test_reset()
{
    memset(&g_counters, 0, sizeof(g_counters));
    g_enabled = 1;
    g_parts = PART_ALL;
    g_wanted_seen = 0;
    g_force_live = -1;
    g_last_job = 0;
    forget_flight();
    g_pushed_ms = 0;
    g_pushed_once = false;
}

void test_force_live(int32_t live)
{
    g_force_live = live;
}

void test_set_attached(bool attached)
{
    g_attached = attached ? 1u : 0u;
}

int32_t test_run_job(uint64_t now_ms)
{
    return run_job(now_ms);
}

}  // namespace ilc::hud::native
