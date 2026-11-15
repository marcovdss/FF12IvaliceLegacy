// Ivalice Legacy co-op module, host tests only: the expanded Target Info (ILCT_Ti*).
#include <stddef.h>
#include <stdio.h>
#include <string.h>

#include "hud/canvas.h"
#include "hud/driver.h"
#include "hud/snapshot.h"
#include "hud/target_info.h"
#include "hud/target_info_native.h"
#include "patch/patch_engine.h"
#include "session/reader.h"

using namespace ilc;
namespace ti = ilc::hud::target_info;
namespace gb = ilc::hud::target_info::giveback;

namespace {

bool fake_names(void* ctx, uint16_t item, char* out, uint32_t cap)
{
    (void)ctx;
    if (item == 0x2FFF) {
        return false;   // a known item whose name cannot be read
    }
    snprintf(out, cap, "Item %04X", item);
    return true;
}

hud::canvas g_cv;

}  // namespace

extern "C" {

uint32_t ILCT_TiSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(ti::view),          sizeof(ti::drop_line),     sizeof(ti::counters),
                              sizeof(ti::status_view),   sizeof(hud::hud_snapshot), sizeof(hud::frame),
                              sizeof(ti::ghost_state),   offsetof(ti::view, handle), offsetof(ti::view, seat_colour),
                              offsetof(ti::view, status_id), offsetof(ti::view, name), offsetof(ti::view, drops)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

void ILCT_TiReset(void) { ti::test_reset(); }
void ILCT_TiSetImageBase(uint64_t base) { ti::test_set_image_base(static_cast<uintptr_t>(base)); }
void ILCT_TiSetEnabled(int32_t on) { ti::set_enabled(on != 0); }
void ILCT_TiSetAttached(int32_t on) { ti::test_set_attached(on != 0); }
void ILCT_TiSetLast(uint64_t drawn_ms, uint64_t stamp_ms) { ti::test_set_last(drawn_ms, stamp_ms); }
void ILCT_TiSetP1Owned(int32_t owned) { ti::test_set_p1_owned(owned); }
uint64_t ILCT_TiNow(void) { return patch::now_ms(); }

int32_t ILCT_TiSelect(const hud::hud_snapshot* snap, int32_t* target_index)
{
    return ti::select_seat(*snap, target_index);
}

uint32_t ILCT_TiGate(uint32_t party, uint32_t libra_known, uint32_t libra, uint32_t anti, uint32_t anti_stock)
{
    ti::gate_in in = {};
    in.party = static_cast<uint8_t>(party);
    in.libra_known = static_cast<uint8_t>(libra_known);
    in.libra = static_cast<uint8_t>(libra);
    in.anti_libra = static_cast<uint8_t>(anti);
    in.anti_libra_stock = static_cast<uint8_t>(anti_stock);
    return ti::gate(in);
}

uint32_t ILCT_TiGhost(ti::ghost_state* g, uint32_t handle, uint32_t hp, uint64_t now)
{
    return ti::ghost_step(g, handle, hp, now);
}

uint32_t ILCT_TiBuildDrops(const uint8_t* loot, const uint8_t* known, uint16_t steal_left, int32_t names,
                           ti::drop_line* out, uint32_t cap)
{
    return ti::build_drops(loot, known, steal_left, names != 0 ? fake_names : nullptr, nullptr, out, cap);
}

uint32_t ILCT_TiDecode(const uint8_t* text, uint32_t len, char* out, uint32_t cap)
{
    return ti::decode_text(text, len, out, cap);
}

uint32_t ILCT_TiStatuses(const hud::hud_unit* u, uint32_t block, int32_t show_immune, uint32_t tick_scale, uint8_t* ids,
                         uint8_t* flags, uint16_t* secs)
{
    return ti::build_statuses(*u, block, show_immune != 0, tick_scale, ids, flags, secs);
}

uint32_t ILCT_TiFade(const ti::view* v, uint64_t now) { return ti::fade_alpha(*v, now); }

uint32_t ILCT_TiCompose(const ti::view* v, uint64_t now, hud::frame* out)
{
    g_cv.begin(v->head.epoch, now);
    const uint32_t n = ti::compose(g_cv, *v, now);
    memcpy(out, &g_cv.view(), sizeof(hud::frame));
    return n;
}

int32_t ILCT_TiHideNative(uint32_t enabled, uint32_t coop_on, uint32_t attached, uint64_t now, uint64_t drawn,
                          uint64_t stamp, uint32_t p1_owned)
{
    ti::native_in in = {};
    in.enabled = static_cast<uint8_t>(enabled);
    in.coop_on = static_cast<uint8_t>(coop_on);
    in.attached = static_cast<uint8_t>(attached);
    in.p1_owned = static_cast<uint8_t>(p1_owned);
    in.now = now;
    in.last_drawn = drawn;
    in.last_stamp = stamp;
    return ti::hide_native(in) ? 1 : 0;
}

void ILCT_TiHeaderBox(uint32_t side, uint32_t w, uint32_t h, int32_t* out4)
{
    const auto s = side == 0 ? ti::corner::LEFT : ti::corner::RIGHT;
    const ti::corner::box b = w == 0 ? ti::corner::header_box(s) : ti::corner::header_box_on(s, w, h);
    out4[0] = b.x;
    out4[1] = b.y;
    out4[2] = b.w;
    out4[3] = b.h;
}

int32_t ILCT_TiBuild(const hud::hud_snapshot* snap, uint64_t now, ti::view* out)
{
    return ti::test_build(*snap, now, out) ? 1 : 0;
}

void ILCT_TiPublish(const ti::view* v) { ti::test_publish(*v); }

int32_t ILCT_TiLatest(ti::view* out) { return ti::latest(*out) ? 1 : 0; }

// The pass as the driver runs it: a canvas, the snapshot, now.
uint32_t ILCT_TiDraw(const hud::hud_snapshot* snap, uint64_t now, hud::frame* out)
{
    g_cv.begin(snap != nullptr ? snap->head.epoch : 0, now);
    hud::driver::pass_input in = {};
    in.snap = snap;
    in.now_ms = now;
    in.layout = hud::LAYOUT_COUCH;
    ti::draw(g_cv, in);
    memcpy(out, &g_cv.view(), sizeof(hud::frame));
    return g_cv.view().count;
}

int32_t ILCT_TiHandler(patch::ctx* c, uint32_t epoch) { return ti::ti01(c, epoch); }

int32_t ILCT_TiItemName(uint32_t item, char* out, uint32_t cap)
{
    return ti::test_item_name(static_cast<uint16_t>(item), out, cap) ? 1 : 0;
}

uint32_t ILCT_TiFindHi(uint64_t* save, uint64_t* settings) { return ti::test_find_hi(save, settings); }

uint32_t ILCT_TiHiCode(uint8_t* out, uint32_t cap, uint64_t save, int32_t flag_disp)
{
    return ti::test_hi_code(out, cap, save, flag_disp);
}

uint32_t ILCT_TiHiOffsets(void) { return ti::TEST_HI_SAVE_AT | ti::TEST_HI_FLAGS_AT << 16; }

void ILCT_TiStats(ti::counters* out)
{
    memcpy(out, const_cast<const ti::counters*>(ti::stats()), sizeof(*out));
}

void ILCT_TiStatus(ti::status_view* out) { *out = ti::status(); }

// ---- the native window's give-back and the target mode's aim ----------------------------------------------------
uint32_t ILCT_TiGbSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(gb::counters), sizeof(gb::status_view)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}
void ILCT_TiGbReset(void) { gb::test_reset(); }
void ILCT_TiGbSetImageBase(uint64_t base) { gb::test_set_image_base(static_cast<uintptr_t>(base)); }
void ILCT_TiGbForcePins(int32_t state) { gb::test_force_pins(state); }
void ILCT_TiGbForceLive(int32_t live) { gb::test_force_live(live); }
void ILCT_TiGbForceGate(int32_t open) { gb::test_force_gate(open); }
int32_t ILCT_TiGbGateOpen(void) { return gb::test_gate_open() ? 1 : 0; }
void ILCT_TiGbSample(uint64_t now) { gb::sample(now); }
void ILCT_TiGbTick(uint64_t now, uint32_t epoch) { gb::tick(now, epoch); }
int32_t ILCT_TiGbPinRow(uint32_t k) { return gb::test_pin_row(k); }
int32_t ILCT_TiGbLiveNow(uint64_t now) { return ti::native_hidden_now(now) ? 1 : 0; }
uint32_t ILCT_TiGbAim(uint64_t base)
{
    session::game_reader r(static_cast<uintptr_t>(base));
    return gb::aim(r);
}
void ILCT_TiGbStats(gb::counters* out)
{
    memcpy(out, const_cast<const gb::counters*>(gb::stats()), sizeof(*out));
}
void ILCT_TiGbStatus(gb::status_view* out) { *out = gb::status(); }

}  // extern "C"
