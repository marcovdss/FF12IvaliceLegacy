// Ivalice Legacy co-op module, host tests only: the background setting's exports (ILCT_Focus*).
#include <string.h>

#include "focus/focus.h"

using namespace ilc;

extern "C" {

uint32_t ILCT_FocusSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(focus::counters), sizeof(focus::status_view)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

void ILCT_FocusReset(void) { focus::test_reset(); }

void ILCT_FocusSetKeep(int32_t on) { focus::test_set_keep_running(on != 0); }

void ILCT_FocusSetForeground(int32_t forced) { focus::test_set_foreground(forced); }

void ILCT_FocusSetReady(int32_t ready) { focus::test_set_neutral_ready(ready != 0); }

int32_t ILCT_FocusKeep(void) { return focus::keep_running() ? 1 : 0; }

int32_t ILCT_FocusReady(void) { return focus::neutral_ready() ? 1 : 0; }

void ILCT_FocusConfigure(const ilc_settings* s) { focus::configure_from_settings(*s); }

int32_t ILCT_FocusAttach(void) { return focus::attach() ? 1 : 0; }

// which: 1 FP-01, 3 FP-03, 4 FP-04, 6 IN-06 (the handlers, called directly)
int32_t ILCT_FocusCall(uint32_t which, patch::ctx* c, uint32_t epoch)
{
    switch (which) {
    case 1: return focus::fp01(c, epoch);
    case 3: return focus::fp03(c, epoch);
    case 4: return focus::fp04(c, epoch);
    case 6: return focus::in06(c, epoch);
    default: return -1;
    }
}

void ILCT_FocusStats(focus::counters* out)
{
    memcpy(out, const_cast<const focus::counters*>(focus::stats()), sizeof(*out));
}

void ILCT_FocusStatus(focus::status_view* out) { *out = focus::status(); }

}  // extern "C"
