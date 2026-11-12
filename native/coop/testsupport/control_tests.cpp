// Ivalice Legacy co-op module, host tests only: the co-op switch exports (ILCT_Ctl*) and a scripted keyboard.
#include <string.h>

#include "control/control.h"
#include "input/keyboard.h"

using namespace ilc;

namespace {

class scripted_keys final : public input::keyboard_api {
public:
    bool focus = true;
    uint8_t keys[256] = {};
    uint32_t reads = 0;

    bool focused() override { return focus; }
    bool down(uint8_t vk) override
    {
        ++reads;
        return keys[vk] != 0;
    }
};

scripted_keys g_keys;

}  // namespace

extern "C" {

// Resets the module and makes it read the scripted keyboard (all keys up, focused).
void ILCT_CtlReset(void)
{
    control::test_reset();
    g_keys = scripted_keys();
    control::test_keyboard(&g_keys);
}

void ILCT_CtlKey(uint32_t vk, int32_t down)
{
    if (vk < 256) {
        g_keys.keys[vk] = down != 0 ? 1 : 0;
    }
}

void ILCT_CtlFocus(int32_t focused)
{
    g_keys.focus = focused != 0;
}

uint32_t ILCT_CtlKeyReads(void)
{
    return g_keys.reads;
}

void ILCT_CtlConfigure(const ilc_settings* s)
{
    control::configure_from_settings(*s);
}

void ILCT_CtlStep(uint64_t now)
{
    control::step(now);
}

void ILCT_CtlKill(int32_t off)
{
    control::post_kill(off != 0);
}

uint32_t ILCT_CtlState(void)
{
    return control::current_state();
}

uint32_t ILCT_CtlParked(void)
{
    return control::parked_seats();
}

uint32_t ILCT_CtlMaskForSafePoint(uint32_t requested)
{
    return control::mask_for_safe_point(requested);
}

int32_t ILCT_CtlTakeReapply(uint32_t* mask)
{
    return control::take_reapply(mask) ? 1 : 0;
}

void ILCT_CtlStats(uint64_t* out, uint32_t count)
{
    const volatile uint64_t* s = &control::stats()->kill_presses;
    const uint32_t n = sizeof(control::counters) / sizeof(uint64_t);
    for (uint32_t k = 0; k < count && k < n; ++k) {
        out[k] = s[k];
    }
}

uint32_t ILCT_CtlMaskLog(uint32_t* out, uint32_t max)
{
    return control::test_mask_log(out, max);
}

}  // extern "C"
