// Ivalice Legacy co-op module, host tests only: action costs, Legacy's MP economy and help lines (ILCT_Cost*, ILCT_Help*).
//
// The cost functions run on the menu exports' tables (testsupport/menu_tests.cpp). A sweep re-costs one action row over
// every base cost 0..255 and returns 256 results, so a test compares whole rows with the game's code run in unicorn.
#include <string.h>

#include "hud/menus_draw.h"
#include "menu/cost.h"
#include "menu/help.h"
#include "menu/menus.h"
#include "session/reader.h"

using namespace ilc;
using namespace ilc::menu;

catalog_tables* menu_test_tables();               // testsupport/menu_tests.cpp
seat_menu* menu_test_seat(uint32_t seat);         // testsupport/menu_tests.cpp

namespace {

enum sweep_fn : uint32_t {
    SW_MP = 0,
    SW_HELPER = 1,
    SW_GIL = 2,
    SW_CHARGES = 3,
    SW_SHOWN = 4,
    SW_SURCHARGE = 5,
    SW_PAID = 6,
};

int32_t run(uint32_t fn, const catalog_tables& t, uint32_t action, const seat_state& s)
{
    switch (fn) {
    case SW_MP: return mp_cost(t, action, s);
    case SW_HELPER: return cost_helper(t, action, s);
    case SW_GIL: return gil_cost(t, action, s);
    case SW_CHARGES: return charge_cost(t, action);
    case SW_SHOWN: return shown_mp_cost(t, action, s, current_economy());
    case SW_SURCHARGE: return economy_surcharge(t, action, s, current_economy());
    case SW_PAID: return economy_paid(t, action, s);
    default: return -2;
    }
}

// The help double for the view tests: "help <action>" while on, nothing (the view's own line) while off.
uint32_t g_help_on = 0;
bool test_name(void*, uint32_t action, char* out, uint32_t cap)
{
    const char text[] = {'A', static_cast<char>('0' + action % 10), '\0'};
    memcpy(out, text, cap < sizeof(text) ? cap : sizeof(text));
    out[cap - 1] = '\0';
    return true;
}
bool test_member(void*, uint32_t, char* out, uint32_t cap) { return test_name(nullptr, 0, out, cap); }
bool test_foe(void*, uint64_t, char* out, uint32_t cap) { return test_name(nullptr, 0, out, cap); }
bool test_help(void*, uint32_t action, char* out, uint32_t cap)
{
    if (!g_help_on) {
        return false;
    }
    char text[16] = {'h', 'e', 'l', 'p', ' '};
    uint32_t n = 5;
    char digits[8];
    uint32_t d = 0;
    do {
        digits[d++] = static_cast<char>('0' + action % 10);
        action /= 10;
    } while (action != 0 && d < sizeof(digits));
    while (d > 0) {
        text[n++] = digits[--d];
    }
    text[n] = '\0';
    return fit_text(text, out, cap) != 0;
}

}  // namespace

extern "C" {

// Re-cost `action` (menu kind `kind`) with every base 0..255 and run one cost function for each: out[256].
void ILCT_CostSweep(uint32_t fn, uint32_t action, uint32_t kind, const seat_state* st, int32_t* out)
{
    catalog_tables& t = *menu_test_tables();
    if (action >= ACTION_CAP) {
        return;
    }
    const action_rule saved = t.actions[action];
    t.actions[action].present = 1;
    t.actions[action].kind = static_cast<uint8_t>(kind);
    for (uint32_t base = 0; base < 256; ++base) {
        t.actions[action].cost = static_cast<uint8_t>(base);
        out[base] = run(fn, t, action, *st);
    }
    t.actions[action] = saved;
}

// The same with the admission's cost verdict.
void ILCT_CostAdmissionSweep(uint32_t action, uint32_t kind, const seat_state* st, int32_t gil, int32_t* out)
{
    catalog_tables& t = *menu_test_tables();
    if (action >= ACTION_CAP) {
        return;
    }
    const action_rule saved = t.actions[action];
    t.actions[action].present = 1;
    t.actions[action].kind = static_cast<uint8_t>(kind);
    for (uint32_t base = 0; base < 256; ++base) {
        t.actions[action].cost = static_cast<uint8_t>(base);
        out[base] = cost_admission(t, action, *st, gil);
    }
    t.actions[action] = saved;
}

int32_t ILCT_CostOne(uint32_t fn, uint32_t action, const seat_state* st)
{
    return run(fn, *menu_test_tables(), action, *st);
}

int32_t ILCT_CostAdmission(uint32_t action, const seat_state* st, int32_t gil)
{
    return cost_admission(*menu_test_tables(), action, *st, gil);
}

void ILCT_CostSetEconomy(const ilc_economy* p) { set_economy(*p); }
void ILCT_CostEconomy(ilc_economy* out) { *out = current_economy(); }
void ILCT_CostConfigure(const ilc_settings* s) { configure_from_settings(*s); }

int32_t ILCT_CostEconomyTotal(int32_t base, uint32_t magick, const ilc_economy* p)
{
    return economy_total(base, magick, *p);
}

// Legacy's surcharge for every listed cost 0..255 of a Magick row (kind 1) at one Magick: out[256].
void ILCT_CostSurchargeSweep(uint32_t action, const seat_state* st, const ilc_economy* p, int32_t* out)
{
    catalog_tables& t = *menu_test_tables();
    if (action >= ACTION_CAP) {
        return;
    }
    const action_rule saved = t.actions[action];
    t.actions[action].present = 1;
    t.actions[action].kind = 1;
    for (uint32_t base = 0; base < 256; ++base) {
        t.actions[action].cost = static_cast<uint8_t>(base);
        out[base] = economy_surcharge(t, action, *st, *p);
    }
    t.actions[action] = saved;
}

void ILCT_CostPriceEntry(const shared_state* sh, const seat_state* st, entry* e)
{
    price_entry(*menu_test_tables(), *sh, *st, current_economy(), e);
}

uint32_t ILCT_HelpStrip(const uint8_t* in, uint32_t n, uint8_t* out, uint32_t cap)
{
    return strip_help_tokens(in, n, out, cap);
}

uint32_t ILCT_HelpFit(const char* in, char* out, uint32_t cap) { return fit_text(in, out, cap); }

// An action's help through the live reader on a synthetic image: the bytes (tokens removed), *where = the text.
uint32_t ILCT_HelpLive(uint64_t image_base, uint32_t action, uint8_t* out, uint32_t cap, uint64_t* where)
{
    session::game_reader r(static_cast<uintptr_t>(image_base));
    uintptr_t at = 0;
    const uint32_t n = action_help(r, action, out, cap, &at);
    if (where != nullptr) {
        *where = at;
    }
    return n;
}

int32_t ILCT_HelpId(uint64_t image_base, uint32_t action, uint32_t* id)
{
    session::game_reader r(static_cast<uintptr_t>(image_base));
    return action_help_id(r, action, id) ? 1 : 0;
}

uint64_t ILCT_HelpAt(uint64_t image_base, uint32_t id)
{
    session::game_reader r(static_cast<uintptr_t>(image_base));
    return help_text_at(r, id);
}

// A menu test seat's window help line with the help double on or off (the view's choice between the game's help and
// its own lines).
void ILCT_HelpSeatLine(uint32_t test_seat, uint32_t help_on, char* out, uint32_t cap)
{
    const seat_menu* sm = menu_test_seat(test_seat);
    g_help_on = help_on;
    const hud::menus_draw::names n = {test_name, test_member, test_foe, nullptr, test_help};
    const hud::menus_draw::seat_source src = {sm != nullptr ? &sm->model : nullptr,
                                              sm != nullptr ? &sm->gambits : nullptr,
                                              nullptr,
                                              nullptr,
                                              sm != nullptr ? sm->member : uint16_t{0},
                                              1,
                                              {}};
    static hud::menus_draw::seat_view view;
    memset(&view, 0, sizeof(view));
    hud::menus_draw::build_seat(src, n, &view);
    const uint32_t len = static_cast<uint32_t>(strlen(view.help));
    const uint32_t k = len < cap ? len : cap - 1;
    memcpy(out, view.help, k);
    out[k] = '\0';
}

// The view's live help line on a synthetic image (decoded and fitted), as the menus show it.
int32_t ILCT_HelpViewLine(uint64_t image_base, uint32_t action, char* out, uint32_t cap)
{
    hud::menus_draw::test_set_image_base(static_cast<uintptr_t>(image_base));
    const bool ok = hud::menus_draw::test_live_name(4, action, 0, out, cap);
    hud::menus_draw::test_set_image_base(0);
    return ok ? 1 : 0;
}

}  // extern "C"
