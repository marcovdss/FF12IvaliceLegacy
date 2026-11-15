// Ivalice Legacy co-op module, host tests only: the party list row additions (ILCT_Rows*): anchors, rules, the pass,
// the co-op thread's record and the HP colour call rows.
#include <windows.h>

#include <string.h>

#include "hud/canvas.h"
#include "hud/driver.h"
#include "hud/party_rows.h"
#include "hud/snapshot.h"

using namespace ilc;
namespace pr = ilc::hud::party_rows;

namespace {

hud::canvas g_rows_canvas;

}  // namespace

extern "C" {

uint32_t ILCT_RowsSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(pr::anchor),      sizeof(pr::rows_record), sizeof(pr::row_extra),
                              sizeof(pr::ghost),       sizeof(pr::status_pick), sizeof(pr::counters),
                              sizeof(pr::status_view), pr::PASS_MAX_COMMANDS,   pr::MAX_STATUS_ICONS};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

int32_t ILCT_RowsAnchor(uint32_t geometry, uint32_t index, uint32_t rows, pr::anchor* out)
{
    return pr::row_anchor(static_cast<uint8_t>(geometry), index, rows, out) ? 1 : 0;
}

int32_t ILCT_RowsLayoutY(uint32_t geometry, uint32_t index, uint32_t rows)
{
    return pr::row_layout_y(static_cast<uint8_t>(geometry), index, rows);
}

int32_t ILCT_RowsCanvasY(uint32_t geometry, int32_t layout_y)
{
    return pr::canvas_y(static_cast<uint8_t>(geometry), layout_y);
}

uint32_t ILCT_RowsGeometryOf(uint32_t layout_state) { return pr::geometry_of(layout_state); }

int32_t ILCT_RowsLowHp(uint32_t hp, uint32_t hp_max, uint32_t status) { return pr::low_hp(hp, hp_max, status) ? 1 : 0; }

uint32_t ILCT_RowsGlow(uint64_t now_ms) { return pr::glow_rgba(now_ms); }

uint32_t ILCT_RowsTimer(int32_t ticks, uint32_t tick_scale, uint32_t status_id, char* out, uint32_t cap)
{
    return pr::format_timer(ticks, tick_scale, status_id, out, cap);
}

int32_t ILCT_RowsSeconds(int32_t ticks, uint32_t tick_scale) { return pr::seconds_left(ticks, tick_scale); }

int32_t ILCT_RowsBeneficial(uint32_t id) { return pr::beneficial(id) ? 1 : 0; }

int32_t ILCT_RowsGlyph(uint32_t seat, int32_t party) { return pr::seat_glyph(static_cast<uint8_t>(seat), party != 0); }

uint32_t ILCT_RowsRowColour(uint32_t mode, const uint8_t* seat_colours, const uint8_t* character_colours, uint32_t seat,
                            uint32_t member)
{
    return pr::row_colour(static_cast<uint8_t>(mode), seat_colours, character_colours, static_cast<uint8_t>(seat),
                          static_cast<uint16_t>(member));
}

uint32_t ILCT_RowsColourRgba(uint32_t index) { return hud::pack(pr::colour_rgba(static_cast<uint8_t>(index))); }

uint32_t ILCT_RowsGhostStep(pr::ghost* g, uint32_t member, uint32_t hp, uint32_t hp_max, uint64_t now_ms)
{
    return pr::ghost_step(*g, static_cast<uint16_t>(member), hp, hp_max, now_ms);
}

uint32_t ILCT_RowsPick(const hud::hud_unit* u, uint32_t tick_scale, pr::status_pick* out, uint32_t cap)
{
    return pr::pick_statuses(*u, tick_scale, out, cap);
}

void ILCT_RowsReset(void) { pr::test_reset(); }

void ILCT_RowsPublish(const pr::rows_record* r) { pr::test_publish(*r); }

int32_t ILCT_RowsRead(pr::rows_record* out) { return pr::test_read(out) ? 1 : 0; }

void ILCT_RowsSetEnabled(int32_t on) { pr::set_enabled(on != 0); }

void ILCT_RowsSetAttached(int32_t on) { pr::test_set_attached(on != 0); }

void ILCT_RowsSetImageBase(uint64_t base) { pr::test_set_image_base(base); }

void ILCT_RowsForceLayoutState(int32_t state) { pr::test_force_layout_state(state); }

void ILCT_RowsConfigure(const ilc_settings* s) { pr::configure_from_settings(*s); }

void ILCT_RowsStep(uint64_t now_ms) { pr::step(now_ms); }

void ILCT_RowsStop(void) { pr::stop(); }

void ILCT_RowsStats(pr::counters* out) { memcpy(out, pr::stats(), sizeof(*out)); }

void ILCT_RowsStatus(pr::status_view* out)
{
    const pr::status_view v = pr::status();
    memcpy(out, &v, sizeof(v));
}

int32_t ILCT_RowsAttach(uint64_t base) { return pr::attach(static_cast<uintptr_t>(base)) ? 1 : 0; }

// The pass on its own: a fresh canvas, the pass, the frame copied out. Returns the command count.
uint32_t ILCT_RowsDraw(const hud::hud_snapshot* s, uint64_t now_ms, hud::frame* out)
{
    g_rows_canvas.begin(s->head.epoch, now_ms);
    const hud::driver::pass_input in = {s, now_ms, s->layout, {}};
    pr::draw(g_rows_canvas, in);
    if (out != nullptr) {
        memcpy(out, &g_rows_canvas.view(), sizeof(*out));
    }
    return g_rows_canvas.view().count;
}

int32_t ILCT_RowsHpCall(patch::ctx* c, uint32_t epoch) { return pr::hp_call(c, epoch); }

void ILCT_RowsHpAfter(patch::ctx* c, uint32_t epoch) { pr::hp_after(c, epoch); }

int32_t ILCT_RowsDigitsCall(patch::ctx* c, uint32_t epoch) { return pr::digits_call(c, epoch); }

int32_t ILCT_RowsRow(uint32_t which) { return pr::test_row(which); }

int32_t ILCT_RowsGlowPending(uint64_t* row, uint32_t* rgba) { return pr::test_glow_pending(row, rgba) ? 1 : 0; }

uint32_t ILCT_RowsColoured(void) { return pr::test_coloured_rows(); }

uint64_t ILCT_RowsCountersAddress(void) { return reinterpret_cast<uint64_t>(pr::stats()); }

}  // extern "C"
