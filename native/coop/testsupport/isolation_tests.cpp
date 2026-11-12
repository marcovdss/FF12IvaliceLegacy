// Ivalice Legacy co-op module, host tests only: the input-isolation exports (ILCT_Iso*).
#include <string.h>

#include "input/pad_mirror.h"
#include "isolation/isolation.h"
#include "patch/patch_engine.h"
#include "session/session.h"

using namespace ilc;

namespace {
volatile uint32_t g_fake_flee_word = 0;  // bits 63..32 epoch, 31..0 seats, as the movement subsystem publishes
volatile uint32_t g_fake_flee_epoch = 0;
uint32_t fake_flee(uint32_t epoch)
{
    return epoch != 0 && epoch == g_fake_flee_epoch ? g_fake_flee_word : 0u;
}
}  // namespace

extern "C" {

// Replace the movement subsystem's flee word with a fixed (epoch, seats); epoch 0 restores the real source.
void ILCT_IsoFakeFlee(uint32_t epoch, uint32_t seats)
{
    g_fake_flee_epoch = epoch;
    g_fake_flee_word = seats;
    isolation::test_flee_source(epoch != 0 ? fake_flee : nullptr);
}

void ILCT_IsoReset(void)
{
    isolation::test_reset();
    isolation::withdraw();
}

void ILCT_IsoPublish(const isolation::iso_record* r)
{
    isolation::test_publish(*r);
}

void ILCT_IsoWithdraw(void)
{
    isolation::withdraw();
}

int32_t ILCT_IsoBudget(void)
{
    return isolation::test_budget();
}

void ILCT_IsoSetBudget(int32_t value)
{
    isolation::test_set_budget(value);
}

void ILCT_IsoVariant(uint32_t v)
{
    isolation::test_block_variant(v);
}

void ILCT_IsoBlock(const ilc_xinput_gamepad* pad, uint32_t extra, int32_t flee, uint8_t* out)
{
    isolation::platform_block(*pad, extra, flee != 0, out);
}

// which: 1 IN-01, 2 IN-02, 3 IN-03, 5 IN-05, 6 IN-06, 7 IN-07 (the fail-open handlers, called directly)
int32_t ILCT_IsoCall(uint32_t which, patch::ctx* c, uint32_t epoch)
{
    switch (which) {
    case 1: return isolation::in01(c, epoch);
    case 2: return isolation::in02(c, epoch);
    case 3: return isolation::in03(c, epoch);
    case 5: return isolation::in05(c, epoch);
    case 6: return isolation::in06(c, epoch);
    case 7: return isolation::in07(c, epoch);
    default: return -1;
    }
}

void ILCT_IsoStats(uint64_t* out, uint32_t count)
{
    const volatile uint64_t* s = &isolation::stats()->passed;
    const uint32_t n = sizeof(isolation::counters) / sizeof(uint64_t);
    for (uint32_t k = 0; k < count && k < n; ++k) {
        out[k] = s[k];
    }
}

void ILCT_IsoStatus(isolation::status_view* out)
{
    *out = isolation::status();
}

int32_t ILCT_IsoAttach(void)
{
    return isolation::attach() ? 1 : 0;
}

void ILCT_IsoMirror(uint32_t seat, const pads::pad_view* v)
{
    pads::test_write(seat, *v);
}

int32_t ILCT_IsoMirrorRead(uint32_t seat, pads::pad_view* out)
{
    return pads::read(seat, out) ? 1 : 0;
}

void ILCT_IsoConfigure(const ilc_settings* s)
{
    isolation::configure_from_settings(*s);
}

// session: nullptr = no session record. Returns the record compose() builds (and advances the release counter).
void ILCT_IsoCompose(uint32_t epoch, uint64_t generation, const session::session_record* s, isolation::iso_record* out)
{
    *out = isolation::compose(epoch, generation, s);
}

uint32_t ILCT_IsoSessionRecordSize(void)
{
    return sizeof(session::session_record);
}

void ILCT_IsoPublishNow(void)
{
    isolation::publish(patch::now_ms());
}

void ILCT_IsoBeginRelease(void)
{
    isolation::begin_release();
}

void ILCT_IsoEndRelease(void)
{
    isolation::end_release();
}

int32_t ILCT_IsoReleaseSettled(void)
{
    return isolation::release_settled() ? 1 : 0;
}

void ILCT_IsoMirrorHold(uint32_t seat, int32_t odd)
{
    pads::test_hold(seat, odd != 0);
}

uint64_t ILCT_IsoSlotAddress(void)
{
    return reinterpret_cast<uint64_t>(&isolation::slot());
}

}  // extern "C"
