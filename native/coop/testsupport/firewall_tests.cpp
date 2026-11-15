// Ivalice Legacy co-op module, host tests only: the input firewall and frontend gate exports (ILCT_Fw*).
#include <string.h>

#include "firewall/firewall.h"
#include "firewall/policy.h"
#include "input/input_service.h"
#include "patch/patch_engine.h"

using namespace ilc;

namespace {
volatile uint64_t g_fake_host = 0;
volatile uint64_t g_fake_p1 = 0;
uint64_t fake_host()
{
    return g_fake_host;
}
uint64_t fake_p1()
{
    return g_fake_p1;
}

constexpr bool same_id(const char* a, const char* b)
{
    while (*a != '\0' && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}
}  // namespace

extern "C" {

// Sizes in this order: fw_record, menu_report, beat_inputs, counters, debug_view, policy_inputs, contract, policy,
// p1_inputs.
uint32_t ILCT_FwSizes(uint32_t* out, uint32_t n)
{
    const uint32_t sizes[] = {sizeof(firewall::fw_record),     sizeof(firewall::menu_report),
                              sizeof(firewall::beat_inputs),   sizeof(firewall::counters),
                              sizeof(firewall::debug_view),    sizeof(firewall::policy_inputs),
                              sizeof(firewall::contract),      sizeof(firewall::policy),
                              sizeof(firewall::p1_inputs)};
    const uint32_t count = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < count; ++k) {
        out[k] = sizes[k];
    }
    return count;
}

void ILCT_FwDerive(const firewall::policy_inputs* in, firewall::contract* out)
{
    *out = firewall::derive(*in);
}

void ILCT_FwMasks(const firewall::contract* c, uint32_t rule, firewall::policy* out)
{
    *out = firewall::masks(*c, rule);
}

void ILCT_FwCompose(const firewall::policy_inputs* in, firewall::policy* out)
{
    *out = firewall::compose(*in);
}

uint32_t ILCT_FwP1Pulse(const firewall::p1_inputs* in, uint32_t* kind)
{
    return firewall::p1_pulse(*in, kind);
}

uint32_t ILCT_FwGuestPulse(uint32_t pressed, uint32_t held, int32_t native_screen)
{
    return firewall::guest_pulse(pressed, held, native_screen != 0);
}

void ILCT_FwReset(void)
{
    firewall::test_reset();
    firewall::slot().withdraw();
    firewall::test_host_source(nullptr);
    firewall::test_p1_source(nullptr);
    firewall::test_set_image_base(0);
}

int32_t ILCT_FwAttach(void)
{
    return firewall::attach() ? 1 : 0;
}

void ILCT_FwPublish(const firewall::fw_record* r)
{
    firewall::test_publish(*r);
}

void ILCT_FwWithdraw(void)
{
    firewall::slot().withdraw();
}

int32_t ILCT_FwRecord(firewall::fw_record* out)
{
    return firewall::slot().read(*out) ? 1 : 0;
}

void ILCT_FwBeat(const firewall::beat_inputs* in, uint64_t now, firewall::fw_record* out)
{
    *out = firewall::beat(*in, now);
}

void ILCT_FwStats(firewall::counters* out)
{
    memcpy(out, const_cast<const firewall::counters*>(firewall::stats()), sizeof(*out));
}

void ILCT_FwDebug(firewall::debug_view* out)
{
    firewall::test_debug(out);
}

// Handler numbers: 0..4 = FW-01..05, 5..11 = FG-01..07.
int32_t ILCT_FwInvoke(uint32_t handler, patch::ctx* c, uint32_t epoch)
{
    using fn = int32_t (*)(patch::ctx*, uint32_t);
    static const fn handlers[] = {firewall::fw01, firewall::fw02, firewall::fw03, firewall::fw04,
                                  firewall::fw05, firewall::fg01, firewall::fg02, firewall::fg03,
                                  firewall::fg04, firewall::fg05, firewall::fg06, firewall::fg07};
    return handler < sizeof(handlers) / sizeof(handlers[0]) ? handlers[handler](c, epoch) : -1;
}

// Replace isolation's host object / P1 object with a fixed pointer (0 restores the real source).
void ILCT_FwFakeHost(uint64_t object)
{
    g_fake_host = object;
    firewall::test_host_source(object != 0 ? fake_host : nullptr);
}

void ILCT_FwFakeP1(uint64_t object)
{
    g_fake_p1 = object;
    firewall::test_p1_source(object != 0 ? fake_p1 : nullptr);
}

void ILCT_FwPostMenus(const firewall::menu_report* m)
{
    firewall::post_menus(*m);
}

void ILCT_FwConfigure(const ilc_settings* s)
{
    firewall::configure_from_settings(*s);
}

void ILCT_FwStart(uint64_t image_base, uint64_t now)
{
    firewall::start(static_cast<uintptr_t>(image_base), now);
}

void ILCT_FwStep(uint64_t now)
{
    firewall::step(now);
}

void ILCT_FwStop(void)
{
    firewall::stop();
}

void ILCT_FwSetImageBase(uint64_t base)
{
    firewall::test_set_image_base(static_cast<uintptr_t>(base));
}

uint32_t ILCT_FwDecide(void)
{
    return firewall::decide_button();
}

int32_t ILCT_FwDecideSwapKnob(void)
{
    return input::decide_swap_knob() ? 1 : 0;
}

int32_t ILCT_FwPromptReady(void)
{
    return firewall::p1_prompt_ready() ? 1 : 0;
}

uint64_t ILCT_FwFg07Last(void)
{
    return firewall::test_fg07_last();
}

int32_t ILCT_FwRow(const char* id)
{
    for (uint32_t row = 0; row < patch::SITE_COUNT; ++row) {
        if (same_id(patch::SITES[row].id, id)) {
            return static_cast<int32_t>(row);
        }
    }
    return -1;
}

uint64_t ILCT_FwSlotAddress(void)
{
    return reinterpret_cast<uint64_t>(&firewall::slot());
}

}  // extern "C"
