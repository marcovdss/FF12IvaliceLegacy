// Ivalice Legacy co-op module, host tests only: chests to the combat log (ILCT_Tl*). The handlers run on the image twin
// (mapped at the pinned base, so the treasure record and the sites are the twin's); log lines go through the overlay
// tests' capture (ILCT_OvLogCapture / ILCT_OvLogText).
#include <string.h>

#include "interaction/treasure_log.h"
#include "patch/call_island.h"
#include "patch/site_table.h"
#include "runtime/log.h"

using namespace ilc;
namespace tl = ilc::interaction::treasure_log;

extern "C" uint64_t ilc_patch_dispatch(ilc::patch::ctx* c);  // patch_engine.cpp: a MinHook row's slow path
extern "C" uint64_t ilc_call_dispatch(ilc::patch::ctx* c);   // patch_engine.cpp: a call row's slow path

namespace {

int32_t row_of(const char* id)
{
    for (uint32_t row = 0; row < patch::SITE_COUNT; ++row) {
        if (strcmp(patch::SITES[row].id, id) == 0) {
            return static_cast<int32_t>(row);
        }
    }
    return -1;
}

}  // namespace

extern "C" {

void ILCT_TlReset(void) { tl::test_reset(); }
int32_t ILCT_TlAttach(void) { return tl::attach() ? 1 : 0; }
void ILCT_TlStatus(tl::status_view* out) { *out = tl::status(); }
void ILCT_TlStats(tl::counters* out) { memcpy(out, const_cast<const tl::counters*>(tl::stats()), sizeof(*out)); }
int32_t ILCT_TlCall01(patch::ctx* c, uint32_t epoch) { return tl::tl01(c, epoch); }
int32_t ILCT_TlCall02(patch::ctx* c, uint32_t epoch) { return tl::tl02(c, epoch); }
void ILCT_TlRecordFor(uint64_t actor, uint64_t chest, uint64_t data, tl::record_image* out)
{
    *out = tl::record_for(actor, chest, data);
}
// Rows of TL-01, TL-02, TL-03 (-1 when missing).
void ILCT_TlRows(int32_t* out)
{
    out[0] = row_of("TL-01");
    out[1] = row_of("TL-02");
    out[2] = row_of("TL-03");
}
// TL-01 through the patch layer's slow path (what its MinHook stub calls), rsi = the notice: returns where execution
// continues (the trampoline = the stock store, else our resume address); *stock = 1 when that is the trampoline.
uint64_t ILCT_TlDispatch01(uint64_t rsi, int32_t* stock)
{
    const int32_t row = row_of("TL-01");
    patch::ctx c;
    memset(&c, 0, sizeof(c));
    c.index = static_cast<uint64_t>(row);
    c.rsi = rsi;
    const uint64_t next = ilc_patch_dispatch(&c);
    *stock = next == reinterpret_cast<uint64_t>(patch::trampoline(static_cast<uint32_t>(row))) ? 1 : 0;
    return next;
}

// TL-02 through the call-island dispatcher (what its call stub runs): the begin call's registers; returns where execution
// continues, *exit_mode = the stub's exit (0 tail-jump to the callee, 1 RETURN: skip it and continue there).
uint64_t ILCT_TlDispatch02(uint64_t actor, uint64_t chest, uint64_t data, int32_t* exit_mode)
{
    const int32_t row = row_of("TL-02");
    patch::ctx c;
    memset(&c, 0, sizeof(c));
    c.index = static_cast<uint64_t>(patch::island::index_of(static_cast<uint32_t>(row)));
    c.rcx = actor;
    c.rdx = chest;
    c.r8 = data;
    const uint64_t next = ilc_call_dispatch(&c);
    *exit_mode = static_cast<int32_t>(c.reserved);
    return next;
}

// treasure_log::step, then the queued lines drained into the capture.
void ILCT_TlStep(uint64_t now)
{
    tl::step(now);
    log::drain();
}

}  // extern "C"
