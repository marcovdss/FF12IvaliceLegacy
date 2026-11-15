// Ivalice Legacy co-op module, host tests only: the native HUD relayout, the target-circle switch and their owner
// (ILCT_Relayout*, ILCT_Circles*, ILCT_HudNative*).
#include <string.h>

#include "hud/circles.h"
#include "hud/native.h"
#include "hud/relayout.h"
#include "runtime/tick.h"

using namespace ilc;

extern "C" {

uint32_t ILCT_RelayoutSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(hud::relayout::counters), sizeof(hud::relayout::status_view),
                              sizeof(hud::circles::counters), sizeof(hud::circles::status_view),
                              sizeof(hud::native::counters), sizeof(hud::native::status_view)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

// ---- relayout ------------------------------------------------------------------------------------------------------
void ILCT_RelayoutReset(void) { hud::relayout::test_reset(); }
void ILCT_RelayoutSetSlot(uint64_t va) { hud::relayout::test_set_slot(va); }
uint32_t ILCT_RelayoutEnsure(uint64_t now_ms) { return hud::relayout::ensure_reduced(now_ms); }
int32_t ILCT_RelayoutRestore(void) { return hud::relayout::restore(); }
int32_t ILCT_RelayoutRestoreWaiting(uint32_t wait_ms) { return hud::relayout::restore_waiting(wait_ms); }
int32_t ILCT_RelayoutOwned(void) { return hud::relayout::owned() ? 1 : 0; }
int32_t ILCT_RelayoutHold(int32_t hold) { return hud::relayout::test_hold(hold != 0) ? 1 : 0; }
void ILCT_RelayoutStatus(hud::relayout::status_view* out) { *out = hud::relayout::status(); }
void ILCT_RelayoutStats(hud::relayout::counters* out)
{
    memcpy(out, const_cast<const hud::relayout::counters*>(hud::relayout::stats()), sizeof(*out));
}

// ---- circles -------------------------------------------------------------------------------------------------------
void ILCT_CirclesReset(void) { hud::circles::test_reset(); }
void ILCT_CirclesSetSite(uint64_t va) { hud::circles::test_set_site(va); }
void ILCT_CirclesFailCas(int32_t fail) { hud::circles::test_fail_cas(fail != 0); }
uint32_t ILCT_CirclesClassify(const uint8_t* window) { return hud::circles::classify(window); }
uint32_t ILCT_CirclesEnsure(void) { return hud::circles::ensure_off(); }
int32_t ILCT_CirclesRestore(void) { return hud::circles::restore(); }
int32_t ILCT_CirclesOwned(void) { return hud::circles::owned() ? 1 : 0; }
void ILCT_CirclesStatus(hud::circles::status_view* out) { *out = hud::circles::status(); }
void ILCT_CirclesStats(hud::circles::counters* out)
{
    memcpy(out, const_cast<const hud::circles::counters*>(hud::circles::stats()), sizeof(*out));
}

// ---- the owner -----------------------------------------------------------------------------------------------------
void ILCT_HudNativeReset(void) { hud::native::test_reset(); }
int32_t ILCT_HudNativeAttach(void) { return hud::native::attach() ? 1 : 0; }
void ILCT_HudNativeSetAttached(int32_t on) { hud::native::test_set_attached(on != 0); }
void ILCT_HudNativeForceLive(int32_t live) { hud::native::test_force_live(live); }
void ILCT_HudNativeSetEnabled(int32_t on) { hud::native::set_enabled(on != 0); }
int32_t ILCT_HudNativeWanted(void) { return hud::native::wanted() ? 1 : 0; }
int32_t ILCT_HudNativeRunJob(uint64_t now_ms) { return hud::native::test_run_job(now_ms); }
void ILCT_HudNativeTick(int32_t live) { hud::native::tick(live != 0); }

// UT-01's stale-liveness handler (the co-op thread stopped beating).
int32_t ILCT_TickRunStale(uint32_t epoch) { return tick::test_run_stale(epoch); }
void ILCT_HudNativeStep(uint64_t now_ms) { hud::native::step(now_ms); }
void ILCT_HudNativeRestoreAtExit(void) { hud::native::restore_at_exit(); }
void ILCT_HudNativeStatus(hud::native::status_view* out) { *out = hud::native::status(); }
void ILCT_HudNativeStats(hud::native::counters* out)
{
    memcpy(out, const_cast<const hud::native::counters*>(hud::native::stats()), sizeof(*out));
}

}  // extern "C"
