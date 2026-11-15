// Ivalice Legacy co-op module: proof that the co-op menus are on screen (see presenter.h).
#include "frontend/presenter.h"

namespace ilc::frontend::presenter {
namespace {

volatile uint64_t g_drawn_ms = 0;  // written only by the overlay (note_drawn)
volatile uint64_t g_dropped_since[SEATS] = {};  // first presented frame of the seat's current run left out (0 = shown)
volatile int32_t g_simulate = -1;  // host tests only

}  // namespace

void note_drawn(uint64_t now_ms)
{
    __atomic_store_n(&g_drawn_ms, now_ms, __ATOMIC_RELEASE);
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
}

}  // namespace ilc::frontend::presenter
