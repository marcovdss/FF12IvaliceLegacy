// Ivalice Legacy co-op module, host tests only: the Battle HUD settings and report (ILCT_Bh*): which element switch
// each record field sets, the Identify presses, and the frame-hook call classification behind the page's status line.
#include <windows.h>

#include <string.h>

#include "hud/battle_hud.h"
#include "hud/battle_log.h"
#include "hud/native.h"
#include "hud/party_rows.h"
#include "hud/rings.h"
#include "hud/target_info.h"
#include "hud/world_marks.h"
#include "ilc/bridge_defaults.h"

using namespace ilc;
namespace bh = ilc::hud::battle_hud;

extern "C" {

void ILCT_BhDefaults(ilc_settings* out)
{
    constexpr ilc_settings d = default_settings();
    memcpy(out, &d, sizeof(d));
}

uint32_t ILCT_BhEffective(const ilc_settings* s) { return bh::effective_elements(*s); }
void ILCT_BhConfigure(const ilc_settings* s) { bh::configure_from_settings(*s); }
uint32_t ILCT_BhElements(void) { return bh::elements(); }

// Every switch the record sets, as words: [0] native enabled, [1] native parts, [2] party rows enabled,
// [3] party rows parts, [4] Target Info, [5] log enabled, [6] log expand control, [7] log filter, [8] log seat,
// [9] lines, [10] diamonds, [11] rings, [12] log expanded.
uint32_t ILCT_BhSwitches(uint32_t* out, uint32_t cap)
{
    if (cap < 13) {
        return 0;
    }
    const hud::battle_log::settings b = hud::battle_log::current();
    out[0] = hud::native::status().enabled;
    out[1] = hud::native::parts();
    out[2] = hud::party_rows::enabled() ? 1 : 0;
    out[3] = hud::party_rows::parts();
    out[4] = hud::target_info::enabled() ? 1 : 0;
    out[5] = b.enabled;
    out[6] = b.expand_control;
    out[7] = b.filter;
    out[8] = b.filter_seat;
    out[9] = hud::world_marks::lines_enabled() ? 1 : 0;
    out[10] = hud::world_marks::diamonds_enabled() ? 1 : 0;
    out[11] = hud::rings::enabled() ? 1 : 0;
    out[12] = b.expanded;
    return 13;
}

// Every element's own state back to its defaults (switches on), and the Battle HUD's.
void ILCT_BhReset(void)
{
    bh::test_reset();
    hud::native::test_reset();
    hud::party_rows::test_reset();
    hud::target_info::test_reset();
    hud::battle_log::test_reset();
    hud::battle_log::configure(hud::battle_log::defaults());
    hud::world_marks::test_reset();
    hud::rings::test_reset();
}

void ILCT_BhToggleExpanded(void) { hud::battle_log::toggle_expanded(); }
uint32_t ILCT_BhClassify(const uint8_t* bytes, uint64_t site, uint64_t log_draw)
{
    return bh::classify_call(bytes, site, log_draw);
}
uint32_t ILCT_BhReportFrom(uint32_t call, int32_t hooked, uint32_t layout, uint32_t circles, int32_t drawing)
{
    return bh::report_from(static_cast<uint8_t>(call), hooked != 0, layout, circles, drawing != 0);
}
void ILCT_BhAttach(uint64_t base) { bh::attach(static_cast<uintptr_t>(base)); }
void ILCT_BhSetCallBytes(const uint8_t* bytes) { bh::test_set_call_bytes(bytes); }
void ILCT_BhReportNow(void) { bh::test_report_now(); }
uint32_t ILCT_BhReport(void) { return bh::report(); }
void ILCT_BhStep(uint64_t now_ms) { bh::step(now_ms); }
void ILCT_BhStop(void) { bh::stop(); }
uint32_t ILCT_BhIdentifyPending(void) { return bh::test_identify_pending(); }
void ILCT_BhStats(bh::counters* out) { memcpy(out, bh::stats(), sizeof(*out)); }

}  // extern "C"
