// Ivalice Legacy co-op module: proof that the co-op menus are on screen (see presenter.h).
#include "frontend/presenter.h"

namespace ilc::frontend::presenter {
namespace {

volatile uint64_t g_drawn_ms = 0;  // written only by the overlay (note_drawn)
volatile uint64_t g_dropped_since[SEATS] = {};  // first presented frame of the seat's current run left out (0 = shown)
volatile int32_t g_simulate = -1;  // host tests only
// The overlay's run (one writer: the render thread; note_drawn(0) may also come from the HUD frame driver's fault).
volatile uint32_t g_run_frames = 0;
volatile uint32_t g_run_missed = 0;
volatile uint64_t g_run_since = 0;
volatile uint64_t g_run_last = 0;
volatile uint64_t g_failed_ms = 0;
volatile uint64_t g_gpu_drawn_ms = 0;

}  // namespace

void note_drawn(uint64_t now_ms)
{
    __atomic_store_n(&g_drawn_ms, now_ms, __ATOMIC_RELEASE);
    if (now_ms == 0) {
        break_run();
    }
}

void note_run(uint64_t now_ms)
{
    const uint64_t now = now_ms != 0 ? now_ms : 1;
    const uint64_t last = __atomic_load_n(&g_run_last, __ATOMIC_ACQUIRE);
    const uint32_t frames = __atomic_load_n(&g_run_frames, __ATOMIC_ACQUIRE);
    const bool continues = frames != 0 && last != 0 && now >= last && now - last <= RUN_GAP_MS;
    if (!continues) {
        // A new run: the count goes to zero first, so a reader never pairs the old count with the new since/last.
        __atomic_store_n(&g_run_frames, 0u, __ATOMIC_RELEASE);
        __atomic_store_n(&g_run_since, now, __ATOMIC_RELEASE);
    }
    __atomic_store_n(&g_run_last, now, __ATOMIC_RELEASE);
    __atomic_store_n(&g_run_missed, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_run_frames, continues ? (frames < 0xFFFFFFFFu ? frames + 1 : frames) : 1u, __ATOMIC_RELEASE);
}

void note_gpu_drawn(uint64_t now_ms)
{
    __atomic_store_n(&g_gpu_drawn_ms, now_ms != 0 ? now_ms : 1, __ATOMIC_RELEASE);
}

void break_run()
{
    __atomic_store_n(&g_run_frames, 0u, __ATOMIC_RELEASE);
    const uint32_t missed = __atomic_load_n(&g_run_missed, __ATOMIC_ACQUIRE);
    __atomic_store_n(&g_run_missed, missed < 0xFFFFFFFFu ? missed + 1 : missed, __ATOMIC_RELEASE);
}

void note_failed(uint64_t now_ms)
{
    break_run();
    __atomic_store_n(&g_failed_ms, now_ms != 0 ? now_ms : 1, __ATOMIC_RELEASE);
}

run_view run()
{
    run_view v = {};
    bool coherent = false;
    for (uint32_t k = 0; k < 3 && !coherent; ++k) {
        v.frames = __atomic_load_n(&g_run_frames, __ATOMIC_ACQUIRE);
        v.since_ms = __atomic_load_n(&g_run_since, __ATOMIC_ACQUIRE);
        v.last_ms = __atomic_load_n(&g_run_last, __ATOMIC_ACQUIRE);
        coherent = __atomic_load_n(&g_run_frames, __ATOMIC_ACQUIRE) == v.frames;
    }
    if (!coherent) {
        v.frames = 0;   // the writer kept moving: report a broken run, never a torn one
    }
    if (v.frames == 0) {
        v.since_ms = 0;
    }
    v.missed = __atomic_load_n(&g_run_missed, __ATOMIC_ACQUIRE);
    v.failed_ms = __atomic_load_n(&g_failed_ms, __ATOMIC_ACQUIRE);
    v.gpu_drawn_ms = __atomic_load_n(&g_gpu_drawn_ms, __ATOMIC_ACQUIRE);
    return v;
}

uint64_t drawn_ms()
{
    return __atomic_load_n(&g_drawn_ms, __ATOMIC_ACQUIRE);
}

bool live(uint64_t now_ms)
{
    const int32_t sim = __atomic_load_n(&g_simulate, __ATOMIC_RELAXED);
    if (sim >= 0) {
        return sim != 0;
    }
    const uint64_t drawn = drawn_ms();
    if (drawn == 0) {
        return false;
    }
    return now_ms >= drawn ? now_ms - drawn <= LIVE_MS : drawn - now_ms <= LIVE_MS;
}

void note_dropped(uint32_t mask, uint64_t now_ms)
{
    for (uint32_t s = 0; s < SEATS; ++s) {
        if ((mask >> s & 1u) == 0) {
            __atomic_store_n(&g_dropped_since[s], uint64_t{0}, __ATOMIC_RELEASE);
        } else if (__atomic_load_n(&g_dropped_since[s], __ATOMIC_ACQUIRE) == 0) {
            __atomic_store_n(&g_dropped_since[s], now_ms != 0 ? now_ms : 1, __ATOMIC_RELEASE);
        }
    }
}

bool seat_shown(uint32_t seat)
{
    return seat >= SEATS || __atomic_load_n(&g_dropped_since[seat], __ATOMIC_ACQUIRE) == 0;
}

bool seat_lost(uint32_t seat, uint64_t now_ms)
{
    if (seat >= SEATS) {
        return false;
    }
    const uint64_t since = __atomic_load_n(&g_dropped_since[seat], __ATOMIC_ACQUIRE);
    return since != 0 && now_ms > since && now_ms - since > LIVE_MS;
}

void test_simulate(int32_t mode)
{
    __atomic_store_n(&g_simulate, mode < 0 ? -1 : (mode != 0 ? 1 : 0), __ATOMIC_RELAXED);
}

void test_reset()
{
    __atomic_store_n(&g_drawn_ms, uint64_t{0}, __ATOMIC_RELEASE);
    for (uint32_t s = 0; s < SEATS; ++s) {
        __atomic_store_n(&g_dropped_since[s], uint64_t{0}, __ATOMIC_RELEASE);
    }
    __atomic_store_n(&g_simulate, -1, __ATOMIC_RELAXED);
    __atomic_store_n(&g_run_frames, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_run_missed, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_run_since, uint64_t{0}, __ATOMIC_RELEASE);
    __atomic_store_n(&g_run_last, uint64_t{0}, __ATOMIC_RELEASE);
    __atomic_store_n(&g_failed_ms, uint64_t{0}, __ATOMIC_RELEASE);
    __atomic_store_n(&g_gpu_drawn_ms, uint64_t{0}, __ATOMIC_RELEASE);
}

}  // namespace ilc::frontend::presenter
