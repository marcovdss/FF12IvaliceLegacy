// Ivalice Legacy co-op module, host tests only: the HUD elements on the overlay (hud_scene), the vanilla pieces they
// hide (hide), their feed (feed), the Legacy script states (states), the seat colour rules (world_marks) and the Wait
// line's freeze view (wait).
#include <string.h>

#include "hud/menus_draw.h"
#include "hud/world_marks.h"
#include "overlay/batch.h"
#include "overlay/feed.h"
#include "overlay/font.h"
#include "overlay/hide.h"
#include "overlay/hud_scene.h"
#include "overlay/marquee.h"
#include "overlay/overlay.h"
#include "overlay/states.h"
#include "runtime/guard.h"
#include "wait/wait.h"

using namespace ilc;
using namespace ilc::overlay;

namespace {

batch g_hud_batch;

uint32_t copy_out(quad* out, uint32_t cap)
{
    const uint32_t n = g_hud_batch.count();
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = g_hud_batch.at(k);
    }
    return n;
}

hud_scene::input make_input(const hud::hud_snapshot* s, const hud::party_rows::rows_record* rows,
                            const feed::record* f, const hud::menus_draw::menu_view* v, const hud::target_info::view* ti,
                            const hud::battle_log::view* log, const states::record* st, uint64_t now, uint32_t boss)
{
    hud_scene::input in = {};
    in.snap = s;
    in.rows = rows;
    in.feed = f;
    in.menus = v;
    in.ti = ti;
    in.log = log;
    in.states = st;
    in.now = now;
    in.boss_gauge = (boss & 1u) != 0 ? 1 : 0;
    in.party_on = (boss & 2u) != 0 ? 0 : 1;   // bit 1: the party list element switched off (review 2)
    return in;
}

// states::test_resolve's lookup: pairs of (record address, handle).
uint64_t g_map_records[16];
uint32_t g_map_handles[16];
uint32_t g_map_count = 0;
uint32_t map_handle(void*, uint64_t record)
{
    for (uint32_t k = 0; k < g_map_count; ++k) {
        if (g_map_records[k] == record) {
            return g_map_handles[k];
        }
    }
    return 0;
}

}  // namespace

extern "C" {

// ---- layouts ------------------------------------------------------------------------------------------------------------
uint32_t ILCT_OhSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t v[] = {sizeof(feed::row), sizeof(feed::record), sizeof(states::foe), sizeof(states::word),
                          sizeof(states::proc), sizeof(states::record), sizeof(ilc_hud_states), sizeof(ilc_hud_foe),
                          sizeof(ilc_hud_word), sizeof(ilc_hud_proc), sizeof(hud_scene::output),
                          sizeof(hide::want_in), sizeof(hide::status_view), sizeof(wait::freeze_view),
                          sizeof(hud::world_marks::diamond), QUAD_CAP};
    const uint32_t n = sizeof(v) / sizeof(v[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = v[k];
    }
    return n;
}

// ---- hud_scene (pure) ---------------------------------------------------------------------------------------------------
// which: 0..2 one element (hud_scene::element) within `budget`, 3 or more compose(). Returns the quads in the batch.
uint32_t ILCT_HsCompose(uint32_t which, uint32_t budget, const hud::hud_snapshot* s,
                        const hud::party_rows::rows_record* rows, const feed::record* f,
                        const hud::menus_draw::menu_view* v, const hud::target_info::view* ti,
                        const hud::battle_log::view* log, const states::record* st, uint64_t now, uint32_t boss,
                        quad* out, uint32_t cap, hud_scene::output* o, uint32_t batch_room)
{
    g_hud_batch.clear();
    // Fill the batch to leave only `batch_room` quads (0 = all of it): the full-batch drill.
    if (batch_room != 0 && batch_room < QUAD_CAP) {
        for (uint32_t k = 0; k < QUAD_CAP - batch_room; ++k) {
            solid(g_hud_batch, 0, 0, 1, 1, rgba(1, 1, 1, 255));
        }
    }
    const uint32_t start = g_hud_batch.count();
    const hud_scene::input in = make_input(s, rows, f, v, ti, log, st, now, boss);
    hud_scene::output res = {};
    bool drawn = false;
    switch (which) {
    case hud_scene::EL_PARTY: drawn = hud_scene::party_list(g_hud_batch, in, budget); break;
    case hud_scene::EL_TARGET: drawn = hud_scene::target_info(g_hud_batch, in, budget); break;
    case hud_scene::EL_LOG: drawn = hud_scene::battle_log(g_hud_batch, in, budget); break;
    default: res = hud_scene::compose(g_hud_batch, in); break;
    }
    if (which < hud_scene::EL_COUNT) {
        res.drawn = drawn ? 1u << which : 0u;
        res.quads[which] = g_hud_batch.count() - start;
    }
    if (o != nullptr) {
        *o = res;
    }
    // Hand back only the element's quads (the filler stays out).
    uint32_t n = 0;
    for (uint32_t k = start; k < g_hud_batch.count() && n < cap; ++k) {
        out[n++] = g_hud_batch.at(k);
    }
    (void)copy_out;
    return g_hud_batch.count() - start;
}

float ILCT_HsRowTop(uint32_t index, uint32_t rows) { return hud_scene::party_row_top(index, rows); }

void ILCT_HsConstants(float* out)
{
    const float v[] = {hud_scene::PARTY_X, hud_scene::PARTY_W, hud_scene::PARTY_BOTTOM, hud_scene::PARTY_PITCH,
                       hud_scene::PARTY_ROW_H, hud_scene::TI_X, hud_scene::TI_TOP, hud_scene::TI_W, hud_scene::LOG_X,
                       hud_scene::LOG_RIGHT, hud_scene::LOG_TOP,
                       hud_scene::BOSS_DY, static_cast<float>(hud_scene::BUDGET_PARTY),
                       static_cast<float>(hud_scene::BUDGET_TARGET), static_cast<float>(hud_scene::BUDGET_LOG)};
    memcpy(out, v, sizeof(v));
}

uint32_t ILCT_HsSeatRgba(const hud::party_rows::rows_record* rows, uint32_t seat)
{
    return hud_scene::seat_rgba(rows, static_cast<uint8_t>(seat));
}

// ---- hide -------------------------------------------------------------------------------------------------------------
uint32_t ILCT_HideWanted(const hide::want_in* in) { return hide::wanted(*in); }
int32_t ILCT_HideMayHide(uint64_t handler, uint64_t expected, uint32_t flags)
{
    return hide::may_hide(handler, expected, flags) ? 1 : 0;
}
int32_t ILCT_HideMayGiveBack(uint64_t handler, uint64_t held, uint32_t flags)
{
    return hide::may_give_back(handler, held, flags) ? 1 : 0;
}
int32_t ILCT_HideFieldUi(uint32_t flag, uint32_t actor) { return hide::field_ui(flag, actor) ? 1 : 0; }
void ILCT_HideReset(void) { hide::test_reset(); }
void ILCT_HideSetBase(uint64_t base) { hide::test_set_image_base(static_cast<uintptr_t>(base)); }
void ILCT_HideForcePins(int32_t s) { hide::test_force_pins(s); }
void ILCT_HideForceGate(int32_t s) { hide::test_force_gate(s); }
void ILCT_HideForceP1(int32_t s) { hide::test_force_p1_ours(s); }
void ILCT_HideForceLive(int32_t s) { hide::test_force_live(s); }
void ILCT_HideForceElements(int32_t mask) { hide::test_force_elements(mask); }
void ILCT_HideNote(uint32_t element, uint64_t now) { hide::note_presented(element, now); }
void ILCT_HideTick(int32_t live) { hide::tick(live != 0); }
void ILCT_HideStale(void) { hide::stale(); }
void ILCT_HideExit(void) { hide::restore_at_exit(); }
// The co-op beat's watch (review 9): the UI tick stopped with pieces hidden. The fault guard is installed first (the
// module installs it at the overlay's attach).
void ILCT_HideWatch(uint64_t now)
{
    guard::attach();
    hide::watch(now);
}
void ILCT_HideSetTickMs(uint64_t ms) { hide::test_set_tick_ms(ms); }
void ILCT_HideSetSignals(int32_t forced, uint64_t core_ms, uint64_t frame_ms)
{
    hide::test_set_game_signals(forced, core_ms, frame_ms);
}
uint64_t ILCT_OvLastFrameMs(void) { return overlay::last_frame_ms(); }
void ILCT_HideSample(uint64_t now) { hide::sample(now); }
void ILCT_HideStatus(hide::status_view* out) { *out = hide::status(); }
void ILCT_HideStats(hide::counters* out) { memcpy(out, const_cast<hide::counters*>(hide::stats()), sizeof(*out)); }

// ---- feed ---------------------------------------------------------------------------------------------------------------
uint32_t ILCT_FeedGauge(int32_t set, uint32_t action, uint32_t state, int32_t charge, int32_t need)
{
    return feed::gauge_percent(set != 0, static_cast<uint16_t>(action), static_cast<uint8_t>(state), charge, need);
}
void ILCT_FeedReset(void) { feed::test_reset(); }
void ILCT_FeedPublish(const feed::record* r) { feed::test_publish(*r); }
int32_t ILCT_FeedRead(feed::record* out) { return feed::read(*out) ? 1 : 0; }
void ILCT_FeedForcePins(int32_t ok) { feed::test_force_pins(ok); }
int32_t ILCT_FeedBuild(const hud::hud_snapshot* s, uint64_t now, feed::record* out)
{
    return feed::test_build(*s, now, out) ? 1 : 0;
}

// ---- states -------------------------------------------------------------------------------------------------------------
const char* ILCT_StTagName(uint32_t tag) { return states::tag_name(tag); }
const char* ILCT_StWordLabel(uint32_t kind) { return states::word_label(kind); }
int32_t ILCT_StWordLive(uint32_t kind, uint32_t value)
{
    states::word w = {1, static_cast<uint8_t>(kind), static_cast<uint8_t>(value)};
    return states::word_live(w) ? 1 : 0;
}
uint32_t ILCT_StProcFor(const states::record* r, uint32_t actor, uint64_t since, uint64_t now)
{
    return states::proc_for(*r, actor, since, now);
}
void ILCT_StMap(const uint64_t* records, const uint32_t* handles, uint32_t n)
{
    g_map_count = n < 16 ? n : 16;
    for (uint32_t k = 0; k < g_map_count; ++k) {
        g_map_records[k] = records[k];
        g_map_handles[k] = handles[k];
    }
}
int32_t ILCT_StResolve(const ilc_hud_states* pushed, uint64_t now, states::record* out)
{
    return states::test_resolve(*pushed, now, map_handle, nullptr, out) ? 1 : 0;
}
void ILCT_StReset(void) { states::test_reset(); }
void ILCT_StPublish(const states::record* r) { states::test_publish(*r); }
int32_t ILCT_StRead(states::record* out) { return states::read(*out) ? 1 : 0; }
uint64_t ILCT_StPushes(void) { return states::stats()->pushes; }

// ---- world marks: the seat colours ------------------------------------------------------------------------------------
void ILCT_WmLineCluts(const uint8_t* colours, uint8_t* out) { hud::world_marks::line_cluts(colours, out); }
int32_t ILCT_WmGemTint(uint32_t colour, uint32_t* rgb)
{
    return hud::world_marks::gem_tint(static_cast<uint8_t>(colour), rgb) ? 1 : 0;
}

// ---- HP warning steps and rare drops ------------------------------------------------------------------------------------
uint32_t ILCT_HsHpStep(uint32_t hp, uint32_t hp_max, uint32_t status) { return hud_scene::hp_step_of(hp, hp_max, status); }
int32_t ILCT_HsDropRare(uint32_t kind, uint32_t slot_flags)
{
    hud::target_info::drop_line d = {};
    d.kind = static_cast<uint8_t>(kind);
    d.flags = static_cast<uint8_t>(slot_flags);
    return hud_scene::drop_rare(d) ? 1 : 0;
}

// ---- the carousel -----------------------------------------------------------------------------------------------------
float ILCT_MqOffset(uint64_t now, uint64_t since, float text_w, float col_w, float speed, uint32_t hold_ms)
{
    return marquee::offset(now, since, text_w, col_w, speed, hold_ms);
}
// Draws one carousel text into a fresh batch; returns the quads, hands them back.
uint32_t ILCT_MqText(const char* s, float x, float col_w, uint64_t now, uint64_t since, int32_t focused, quad* out,
                     uint32_t cap)
{
    g_hud_batch.clear();
    const text_style st = {0xFFFFFFFFu, 0xE6000000u, 1.4f, 0.0f, 0x96000000u, 1.0f, 2.0f, 1.0f};
    marquee::text(g_hud_batch, font::FACE_BODY, x, 100.0f, 23.0f, s, 200, st, col_w, now, since, focused != 0);
    const uint32_t n = g_hud_batch.count();
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = g_hud_batch.at(k);
    }
    return n;
}
int32_t ILCT_MqClipOn(void) { return g_hud_batch.clip.on; }

// ---- the windows' built-in help lines ---------------------------------------------------------------------------------
const char* ILCT_HmBuiltinHelp(uint32_t index) { return hud::menus_draw::builtin_help(index); }

// ---- wait: the freeze view ---------------------------------------------------------------------------------------------
int32_t ILCT_WaitFrozenAfter(const wait::engine_in* in, const wait::engine_out* out)
{
    return wait::frozen_after(*in, *out) ? 1 : 0;
}
void ILCT_WaitFreeze(wait::freeze_view* out) { *out = wait::freeze(); }

}  // extern "C"
