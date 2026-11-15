// Ivalice Legacy co-op module, host tests only: the co-op menus in the native window style (ILCT_Hm*).
//
// The geometry, the layout and the drawing are pure and passed through; the view is built from the menu test seats
// (testsupport/menu_tests.cpp) with name doubles, or published as the test gives it.
#include <string.h>

#include "frontend/rules.h"
#include "hud/canvas.h"
#include "hud/menus_draw.h"
#include "menu/menus.h"

using namespace ilc;
using namespace ilc::hud;

menu::seat_menu* menu_test_seat(uint32_t seat);   // testsupport/menu_tests.cpp

namespace {

canvas g_cv;

const char* const MEMBER_NAMES[] = {"Vaan", "Ashe", "Fran", "Balthier", "Basch", "Penelo"};

void put(char* out, uint32_t cap, const char* text)
{
    uint32_t n = 0;
    for (; text[n] != '\0' && n + 1 < cap; ++n) {
        out[n] = text[n];
    }
    out[n] = '\0';
}

// Name doubles: an action is "Act" + its id in decimal, a character its name (or "M" + id), a foe the game text at
// the pointer the test gives (decoded as the live reader would).
bool test_action(void*, uint32_t action, char* out, uint32_t cap)
{
    if (action == 0xFFFE) {
        put(out, cap, "?");
        return false;
    }
    char digits[8];
    uint32_t n = 0;
    do {
        digits[n++] = static_cast<char>('0' + action % 10);
        action /= 10;
    } while (action != 0);
    char text[16] = {'A', 'c', 't'};
    uint32_t k = 3;
    while (n > 0) {
        text[k++] = digits[--n];
    }
    text[k] = '\0';
    put(out, cap, text);
    return true;
}

bool test_member(void*, uint32_t member, char* out, uint32_t cap)
{
    if (member < 6) {
        put(out, cap, MEMBER_NAMES[member]);
    } else {
        char text[4] = {'M', static_cast<char>('0' + member % 10), '\0', '\0'};
        put(out, cap, text);
    }
    return true;
}

bool test_foe(void*, uint64_t text, char* out, uint32_t cap)
{
    const uint8_t* raw = reinterpret_cast<const uint8_t*>(text);
    if (raw == nullptr) {
        put(out, cap, "?");
        return false;
    }
    uint32_t len = 0;
    while (len < 40 && raw[len] != 0) {
        ++len;
    }
    return menus_draw::decode_game_text(raw, len, out, cap) != 0;
}

}  // namespace

extern "C" {

uint32_t ILCT_HmSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(menus_draw::view_row),      sizeof(menus_draw::seat_view),
                              sizeof(menus_draw::menu_view),     sizeof(menus_draw::window_layout),
                              sizeof(menus_draw::frame_layout),  sizeof(menus_draw::box),
                              sizeof(menus_draw::p1_geometry),   sizeof(menus_draw::counters),
                              menus_draw::COMMAND_CAP,           menus_draw::TEXT_CAP};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

void ILCT_HmReset(void) { menus_draw::test_reset(); }

void ILCT_HmP1Native(menus_draw::p1_geometry* out) { *out = menus_draw::p1_native(); }

void ILCT_HmPartyRow(uint32_t index, uint32_t rows, int32_t reduced, menus_draw::box* out)
{
    *out = menus_draw::party_row(index, rows, reduced != 0);
}

void ILCT_HmPartyList(uint32_t rows, int32_t reduced, menus_draw::box* out)
{
    *out = menus_draw::party_list(rows, reduced != 0);
}

void ILCT_HmZones(menus_draw::box* target_info, menus_draw::box* log)
{
    *target_info = menus_draw::target_info_zone();
    *log = menus_draw::log_zone();
}

void ILCT_HmLayout(const menus_draw::menu_view* v, const hud_snapshot* s, uint32_t commands, uint32_t text,
                   menus_draw::frame_layout* out)
{
    menus_draw::layout(*v, *s, commands, text, out);
}

// Draws on a fresh canvas after `prefill` commands of another pass (one short text each); the frame comes back.
uint32_t ILCT_HmDraw(const menus_draw::menu_view* v, const hud_snapshot* s, uint64_t now_ms, uint32_t layout_mode,
                     uint32_t prefill, frame* out)
{
    g_cv.begin(1, now_ms);
    for (uint32_t k = 0; k < prefill; ++k) {
        g_cv.text(0, 0, "x", WHITE);
    }
    const uint32_t n = menus_draw::draw(g_cv, *v, *s, now_ms, static_cast<uint8_t>(layout_mode));
    memcpy(out, &g_cv.view(), sizeof(*out));
    return n;
}

// draw_seats() on a fresh canvas after `prefill` commands of another pass; returns the seats left out.
uint32_t ILCT_HmDrawDropped(const menus_draw::menu_view* v, const hud_snapshot* s, uint64_t now_ms, uint32_t prefill,
                            frame* out)
{
    g_cv.begin(1, now_ms);
    for (uint32_t k = 0; k < prefill; ++k) {
        g_cv.text(0, 0, "x", WHITE);
    }
    uint32_t dropped = 0xFFFFFFFFu;
    menus_draw::draw_seats(g_cv, *v, *s, now_ms, 0, &dropped);
    memcpy(out, &g_cv.view(), sizeof(*out));
    return dropped;
}

// The open windows' floor (commands, text bytes).
void ILCT_HmFloor(const menus_draw::menu_view* v, const hud_snapshot* s, uint32_t* commands, uint32_t* text)
{
    const menus_draw::footprint f = menus_draw::floor_cost(*v, *s);
    *commands = f.commands;
    *text = f.text;
}

uint32_t ILCT_HmLastDropped(void) { return menus_draw::last_dropped(); }

void ILCT_HmForceDropped(uint32_t mask) { menus_draw::test_force_dropped(mask); }

// The menu pass on a fresh canvas.
void ILCT_HmPass(const hud_snapshot* s, uint64_t now_ms, uint32_t layout_mode, frame* out)
{
    g_cv.begin(1, now_ms);
    const driver::pass_input in = {s, now_ms, static_cast<uint8_t>(layout_mode), {}};
    menus_draw::pass(g_cv, in);
    memcpy(out, &g_cv.view(), sizeof(*out));
}

void ILCT_HmPublish(const menus_draw::menu_view* v) { menus_draw::publish(*v); }

void ILCT_HmWithdraw(void) { menus_draw::withdraw(); }

int32_t ILCT_HmRead(menus_draw::menu_view* out) { return menus_draw::read(*out) ? 1 : 0; }

uint32_t ILCT_HmDecode(const uint8_t* in, uint32_t len, char* out, uint32_t cap)
{
    return menus_draw::decode_game_text(in, len, out, cap);
}

// One seat's window from a menu test seat's model (or a selector / candidates the test gives), with the name doubles.
void ILCT_HmBuildSeat(uint32_t test_seat, uint32_t seat, uint32_t member, const frontend::selector* sel,
                      const targets::candidate_list* cands, menus_draw::seat_view* out)
{
    const menu::seat_menu* sm = menu_test_seat(test_seat);
    const menus_draw::names n = {test_action, test_member, test_foe, nullptr, nullptr};
    const menus_draw::seat_source src = {sm != nullptr ? &sm->model : nullptr,
                                         sm != nullptr ? &sm->gambits : nullptr,
                                         cands,
                                         sel,
                                         static_cast<uint16_t>(member),
                                         static_cast<uint8_t>(seat),
                                         {}};
    memset(out, 0, sizeof(*out));
    menus_draw::build_seat(src, n, out);
}

uint32_t ILCT_HmSeatColour(uint32_t seat, uint32_t member, uint8_t* index)
{
    return menus_draw::seat_colour(seat, member, index);
}

void ILCT_HmConfigure(const ilc_settings* s) { menus_draw::configure_from_settings(*s); }

void ILCT_HmSetViewSource(const menus_draw::menu_view* v) { menus_draw::test_set_view_source(v); }

void ILCT_HmForceReduced(int32_t reduced) { menus_draw::test_force_reduced(reduced); }

void ILCT_HmSetImageBase(uint64_t base) { menus_draw::test_set_image_base(static_cast<uintptr_t>(base)); }

int32_t ILCT_HmBuild(uint64_t now_ms, menus_draw::menu_view* out) { return menus_draw::test_build(now_ms, out) ? 1 : 0; }

void ILCT_HmStart(uint64_t base, uint64_t now_ms) { menus_draw::start(static_cast<uintptr_t>(base), now_ms); }

void ILCT_HmStep(uint64_t now_ms) { menus_draw::step(now_ms); }

void ILCT_HmStop(void) { menus_draw::stop(); }

void ILCT_HmStats(menus_draw::counters* out) { memcpy(out, menus_draw::stats(), sizeof(*out)); }

int32_t ILCT_HmLiveName(uint32_t kind, uint32_t id, uint64_t pointer, char* out, uint32_t cap)
{
    return menus_draw::test_live_name(kind, id, pointer, out, cap) ? 1 : 0;
}

}  // extern "C"
