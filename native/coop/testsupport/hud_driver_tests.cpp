// Ivalice Legacy co-op module, host tests only: the HUD frame driver, the presenter stamp, the HudSnapshot sampler and
// the presenter policy while the menus are suspended (ILCT_HudDrv*, ILCT_HudSnap*, ILCT_FePresenterMode).
#include <windows.h>

#include <string.h>

#include "frontend/presenter.h"
#include "frontend/rules.h"
#include "hud/driver.h"
#include "hud/snapshot.h"

using namespace ilc;

namespace {

// ---- seqlock stress: a writer thread publishes snapshots whose every 32-bit word is the same number ----------------
volatile LONG g_stress_stop = 0;
hud::hud_snapshot g_stress_value;

DWORD WINAPI stress_writer(void*)
{
    uint32_t n = 1;
    while (InterlockedCompareExchange(&g_stress_stop, 0, 0) == 0) {
        uint32_t* words = reinterpret_cast<uint32_t*>(&g_stress_value);
        for (uint32_t k = 0; k < sizeof(g_stress_value) / 4; ++k) {
            words[k] = n;
        }
        hud::snapshot::publish(g_stress_value);
        ++n;
    }
    return 0;
}

hud::hud_snapshot g_stress_read;

}  // namespace

extern "C" {

uint32_t ILCT_HudDrvSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(hud::hud_snapshot),        sizeof(hud::hud_unit),
                              sizeof(hud::seat_target),         sizeof(hud::driver::counters),
                              sizeof(hud::snapshot::counters),  hud::driver::PASS_COUNT,
                              hud::SNAP_TARGETS,                sizeof(frontend::presenter_in)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

void ILCT_HudDrvReset(void)
{
    hud::driver::test_reset();
    hud::snapshot::test_reset();
}

void ILCT_HudDrvStats(hud::driver::counters* out) { memcpy(out, hud::driver::stats(), sizeof(*out)); }

uint64_t ILCT_HudDrvCountersAddress(void) { return reinterpret_cast<uint64_t>(hud::driver::stats()); }

void ILCT_HudDrvSetProbe(uint32_t mask) { hud::driver::test_set_probe(mask); }

void ILCT_HudDrvInjectFault(uint32_t pass) { hud::driver::test_inject_fault(pass); }

int32_t ILCT_HudDrvGate(uint32_t epoch, uint64_t now_ms) { return hud::driver::frame_gate(epoch, now_ms) ? 1 : 0; }

// The composed frame (copied out) and the result. test_frame may be null.
int32_t ILCT_HudDrvCompose(uint32_t epoch, uint64_t now_ms, const hud::frame* test_frame, hud::frame* out)
{
    const hud::frame* f = nullptr;
    const int32_t r = hud::driver::compose(epoch, now_ms, test_frame, &f);
    if (r == hud::driver::COMPOSE_OK && f != nullptr && out != nullptr) {
        memcpy(out, f, sizeof(*out));
    }
    return r;
}

void ILCT_HudDrvPresented(uint64_t now_ms) { hud::driver::presented(now_ms); }

void ILCT_HudDrvFaulted(void) { hud::driver::faulted(); }

const char* ILCT_HudDrvPassName(uint32_t pass) { return hud::driver::pass_name(pass); }

uint64_t ILCT_HudDrvStampAddress(void)
{
    return reinterpret_cast<uint64_t>(&hud::driver::stats()->last_stamp_ms);
}

// ---- the snapshot ----------------------------------------------------------------------------------------------
void ILCT_HudSnapPublish(const hud::hud_snapshot* s) { hud::snapshot::publish(*s); }

void ILCT_HudSnapWithdraw(void) { hud::snapshot::withdraw(); }

int32_t ILCT_HudSnapRead(hud::hud_snapshot* out) { return hud::snapshot::read(*out) ? 1 : 0; }

int32_t ILCT_HudSnapMeta(uint32_t* epoch, uint64_t* sampled_ms)
{
    return hud::snapshot::meta(epoch, sampled_ms) ? 1 : 0;
}

void ILCT_HudSnapStart(uint64_t base, uint64_t now_ms) { hud::snapshot::start(static_cast<uintptr_t>(base), now_ms); }

void ILCT_HudSnapStep(uint64_t now_ms) { hud::snapshot::step(now_ms); }

void ILCT_HudSnapStop(void) { hud::snapshot::stop(); }

int32_t ILCT_HudSnapSample(uint64_t now_ms, hud::hud_snapshot* out)
{
    return hud::snapshot::test_sample(now_ms, out) ? 1 : 0;
}

void ILCT_HudSnapSetImageBase(uint64_t base) { hud::snapshot::test_set_image_base(static_cast<uintptr_t>(base)); }

void ILCT_HudSnapSeatTarget(uint32_t seat, uint32_t handle, uint32_t group, uint32_t on_page)
{
    hud::snapshot::test_set_seat_target(seat, handle, static_cast<uint8_t>(group), static_cast<uint8_t>(on_page));
}

void ILCT_HudSnapUseSeatTargets(int32_t on) { hud::snapshot::test_use_seat_targets(on != 0); }

void ILCT_HudSnapSetBetween(hud::snapshot::between_fn fn) { hud::snapshot::test_set_between(fn); }

void ILCT_HudSnapStats(hud::snapshot::counters* out) { memcpy(out, hud::snapshot::stats(), sizeof(*out)); }

uint32_t ILCT_HudSnapLayout(void) { return hud::snapshot::layout_for_session(); }

// A writer thread publishes as fast as it can while this thread reads `reads` times. out[0] = coherent reads,
// out[1] = reads refused as torn, out[2] = reads that returned a mix (must be 0). Returns 1 if the thread ran.
int32_t ILCT_HudSnapStress(uint32_t reads, uint64_t* out)
{
    out[0] = out[1] = out[2] = 0;
    InterlockedExchange(&g_stress_stop, 0);
    HANDLE t = CreateThread(nullptr, 0, stress_writer, nullptr, 0, nullptr);
    if (t == nullptr) {
        return 0;
    }
    for (uint32_t k = 0; k < reads; ++k) {
        if (!hud::snapshot::read(g_stress_read)) {
            ++out[1];
            continue;
        }
        const uint32_t* words = reinterpret_cast<const uint32_t*>(&g_stress_read);
        bool same = true;
        for (uint32_t w = 1; w < sizeof(g_stress_read) / 4 && same; ++w) {
            same = words[w] == words[0];
        }
        ++out[same ? 0 : 2];
    }
    InterlockedExchange(&g_stress_stop, 1);
    WaitForSingleObject(t, INFINITE);
    CloseHandle(t);
    hud::snapshot::withdraw();
    return 1;
}

// ---- the presenter policy (pure) and the stamp -----------------------------------------------------------------------
uint32_t ILCT_FePresenterMode(const frontend::presenter_in* in) { return frontend::presenter_mode_of(*in); }

uint64_t ILCT_FePresenterDrawn(void) { return frontend::presenter::drawn_ms(); }

}  // extern "C"
