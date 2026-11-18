// Ivalice Legacy co-op module: the Battle HUD settings and report (see battle_hud.h).
#include "hud/battle_hud.h"

#include "hud/battle_log.h"
#include "hud/canvas.h"
#include "hud/circles.h"
#include "hud/driver.h"
#include "hud/hud_facts.h"
#include "hud/native.h"
#include "hud/party_rows.h"
#include "hud/relayout.h"
#include "hud/rings.h"
#include "hud/target_info.h"
#include "hud/world_marks.h"
#include "input/edge_history.h"
#include "patch/site_table.h"
#include "runtime/probe.h"

namespace ilc::hud::battle_hud {
namespace {

static_assert(static_cast<uint32_t>(ILC_LOG_FILTER_ALL) == battle_log::FILTER_ALL &&
                  static_cast<uint32_t>(ILC_LOG_FILTER_PARTY) == battle_log::FILTER_PARTY &&
                  static_cast<uint32_t>(ILC_LOG_FILTER_FOES) == battle_log::FILTER_FOES &&
                  static_cast<uint32_t>(ILC_LOG_FILTER_STATUSES) == battle_log::FILTER_STATUSES &&
                  static_cast<uint32_t>(ILC_LOG_FILTER_MINE) == battle_log::FILTER_MINE &&
                  ILC_ENUM_COUNT(ILC_LOG_FILTERS) == battle_log::FILTER_COUNT,
              "the schema's log filters are the battle log's");

constexpr bool same_id(const char* a, const char* b)
{
    while (*a != '\0' && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

constexpr int32_t find_row(const char* id)
{
    for (uint32_t row = 0; row < patch::SITE_COUNT; ++row) {
        if (same_id(patch::SITES[row].id, id)) {
            return static_cast<int32_t>(row);
        }
    }
    return -1;
}

constexpr int32_t ROW_H0 = find_row("HD-01");
static_assert(ROW_H0 >= 0 && patch::SITES[ROW_H0].length >= 5, "the frame hook's call row");
constexpr uint8_t OP_CALL = 0xE8;   // NOTADDR: call rel32

counters g_counters;
volatile uint32_t g_elements = ALL_ELEMENTS;
volatile uint32_t g_report = 0;
volatile uintptr_t g_base = 0;
volatile uint32_t g_test_bytes_set = 0;
uint8_t g_test_bytes[5] = {};

// Co-op thread only.
edges::edge_cursor g_cursor = {};
uint64_t g_report_ms = 0;
bool g_reported_once = false;

void bump(volatile uint64_t& c)
{
    __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED);
}

bool on(uint32_t mask, uint32_t bit)
{
    return (mask & bit) != 0;
}

// One NOP form at p (n bytes left): its length, 0 when none fits.
uint32_t nop_length(const uint8_t* p, uint32_t n)
{
    if (n >= 1 && p[0] == 0x90) {
        return 1;
    }
    if (n >= 2 && p[0] == 0x66 && p[1] == 0x90) {
        return 2;
    }
    if (n >= 3 && p[0] == 0x0F && p[1] == 0x1F && p[2] == 0x00) {
        return 3;
    }
    if (n >= 4 && p[0] == 0x0F && p[1] == 0x1F && p[2] == 0x40 && p[3] == 0x00) {
        return 4;
    }
    if (n >= 5 && p[0] == 0x0F && p[1] == 0x1F && p[2] == 0x44 && p[3] == 0x00 && p[4] == 0x00) {
        return 5;
    }
    return 0;
}

void refresh_report()
{
    const uintptr_t base = g_base;
    if (base == 0) {
        __atomic_store_n(&g_report, 0u, __ATOMIC_RELEASE);
        return;
    }
    const uint64_t site = base + patch::SITES[ROW_H0].rva;
    uint8_t bytes[5] = {};
    bool read = false;
    if (__atomic_load_n(&g_test_bytes_set, __ATOMIC_ACQUIRE) != 0) {
        for (uint32_t k = 0; k < 5; ++k) {
            bytes[k] = g_test_bytes[k];
        }
        read = true;
    } else {
        read = copy_from_caller(bytes, reinterpret_cast<const void*>(site), sizeof(bytes));
    }
    const uint64_t log_draw = base + (facts::FN_LOG_DRAW - facts::PINNED_BASE);
    const uint8_t call = read ? classify_call(bytes, site, log_draw) : static_cast<uint8_t>(CALL_UNREADABLE);
    const native::status_view n = native::status();
    const hud::status_view h = hud::status();
    const bool drawing = h.attached != 0 && h.guard_ready != 0 && patch::subsystem_live(ILC_SUBSYS_RENDER);
    const uint8_t r = report_from(call, h.row_hooked != 0, n.layout_state, n.circles_site, drawing);
    __atomic_store_n(&g_report, static_cast<uint32_t>(r), __ATOMIC_RELEASE);
    bump(g_counters.reports);
}

}  // namespace

uint32_t effective_elements(const ilc_settings& s)
{
    return s.hud_enabled != 0 ? (s.hud_elements & ALL_ELEMENTS) : 0u;
}

uint8_t classify_call(const uint8_t bytes[5], uint64_t site, uint64_t log_draw)
{
    if (bytes[0] == OP_CALL) {
        const int32_t rel = static_cast<int32_t>(static_cast<uint32_t>(bytes[1]) | static_cast<uint32_t>(bytes[2]) << 8 |
                                                 static_cast<uint32_t>(bytes[3]) << 16 |
                                                 static_cast<uint32_t>(bytes[4]) << 24);
        const uint64_t target = site + 5 + static_cast<uint64_t>(static_cast<int64_t>(rel));
        return target == log_draw ? CALL_STOCK : CALL_OTHER;
    }
    uint32_t at = 0;
    while (at < 5) {
        const uint32_t n = nop_length(bytes + at, 5 - at);
        if (n == 0) {
            return CALL_FOREIGN;
        }
        at += n;
    }
    return CALL_NOPS;
}

uint8_t report_from(uint8_t call, bool row_hooked, uint32_t layout_state, uint32_t circles_site, bool drawing)
{
    uint8_t r = 0;
    if (call == CALL_NOPS) {
        r |= ILC_HUD_REPORT_VISION_NO_COMBAT_LOG;
    }
    if (call != CALL_UNREADABLE && !(row_hooked && call == CALL_OTHER)) {
        r |= ILC_HUD_REPORT_FRAME_HOOK_OFF;
    } else if (!drawing) {
        r |= ILC_HUD_REPORT_RENDER_OFF;   // hooked (or unreadable), but nothing of ours is drawn
    }
    if (layout_state == relayout::LAYOUT_FOREIGN) {
        r |= ILC_HUD_REPORT_PARTY_LAYOUT_FOREIGN;
    }
    if (circles_site == circles::SITE_VISION) {
        r |= ILC_HUD_REPORT_CIRCLES_BY_VISION;
    } else if (circles_site == circles::SITE_FOREIGN) {
        r |= ILC_HUD_REPORT_CIRCLES_FOREIGN;
    }
    return r;
}

void attach(uintptr_t image_base)
{
    g_base = image_base;
}

void configure_from_settings(const ilc_settings& s)
{
    const uint32_t e = effective_elements(s);
    const bool hud_on = s.hud_enabled != 0;
    // 0.1.57 interim (driver.h): the co-op layer, and everything that replaces a native HUD piece, only as an
    // experiment (Developer options and the HUD test draw on).
    // The old co-op layer (the replay through the game's HUD renderer) stays off: our own overlay draws the guests'
    // menus, and the Diagnostics test switch (hud_test_draw) now shows the overlay's test card (overlay.h).
    (void)s.hud_test_draw;
    driver::set_layer_enabled(false);
    native::set_enabled(hud_on);
    native::set_parts((on(e, ILC_HUD_PARTY_SHRINK) ? native::PART_SHRINK : 0u) |
                      (on(e, ILC_HUD_CIRCLES_OFF) ? native::PART_CIRCLES : 0u));
    party_rows::set_enabled(hud_on);
    party_rows::set_parts((on(e, ILC_HUD_SEAT_EDGE) ? party_rows::PART_EDGE : 0u) |
                          (on(e, ILC_HUD_LOW_HP_GLOW) ? party_rows::PART_GLOW : 0u) |
                          (on(e, ILC_HUD_DAMAGE_GHOST) ? party_rows::PART_GHOST : 0u) |
                          (on(e, ILC_HUD_ACTION_LINE) ? party_rows::PART_ACTION : 0u) |
                          (on(e, ILC_HUD_PARTY_TIMERS) ? party_rows::PART_TIMERS : 0u) |
                          (on(e, ILC_HUD_GUEST_CURSORS) ? party_rows::PART_CURSORS : 0u));
    target_info::set_enabled(on(e, ILC_HUD_TARGET_INFO));
    battle_log::settings b = battle_log::current();
    b.enabled = on(e, ILC_HUD_BATTLE_LOG) ? 1 : 0;
    b.expand_control = s.log_expand_control;
    b.filter = s.log_filter;
    b.filter_seat = s.log_filter_seat;
    b.compact_lines = s.log_lines_compact;
    b.expanded_lines = s.log_lines_expanded;
    b.fade_s = s.log_fade_s;
    battle_log::configure(b);   // clamps; `expanded` (the button's state) is kept
    world_marks::set_lines_enabled(on(e, ILC_HUD_SEAT_LINES));
    world_marks::set_diamonds_enabled(on(e, ILC_HUD_SEAT_DIAMONDS));
    rings::set_enabled(on(e, ILC_HUD_AOE_RING));
    __atomic_store_n(&g_elements, e, __ATOMIC_RELEASE);
    bump(g_counters.configured);
}

void step(uint64_t now_ms)
{
    // Identify: each seat's press since the last beat (the cursor's first use only seeds it).
    const bool diamonds = on(__atomic_load_n(&g_elements, __ATOMIC_ACQUIRE), ILC_HUD_SEAT_DIAMONDS);
    for (uint32_t seat = 1; seat <= ILC_SEATS; ++seat) {
        edges::edge_delta d;
        if (!edges::since(g_cursor, seat, &d) || d.overrun || d.reseeded ||
            (d.act_pressed & (1u << ILC_ACTION_IDENTIFY)) == 0) {
            continue;
        }
        if (diamonds) {
            world_marks::identify(seat - 1);
            bump(g_counters.identifies);
        } else {
            bump(g_counters.identify_off);
        }
    }
    if (!g_reported_once || now_ms - g_report_ms >= REPORT_MS) {
        g_reported_once = true;
        g_report_ms = now_ms;
        refresh_report();
    }
}

void stop()
{
    g_cursor = edges::edge_cursor{};
    g_reported_once = false;
}

uint32_t elements()
{
    return __atomic_load_n(&g_elements, __ATOMIC_ACQUIRE);
}

uint8_t report()
{
    return static_cast<uint8_t>(__atomic_load_n(&g_report, __ATOMIC_ACQUIRE));
}

const counters* stats()
{
    return &g_counters;
}

void test_reset()
{
    __atomic_store_n(&g_counters.configured, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&g_counters.identifies, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&g_counters.identify_off, 0, __ATOMIC_RELAXED);
    __atomic_store_n(&g_counters.reports, 0, __ATOMIC_RELAXED);
    g_elements = ALL_ELEMENTS;
    g_report = 0;
    g_base = 0;
    g_test_bytes_set = 0;
    g_cursor = edges::edge_cursor{};
    g_report_ms = 0;
    g_reported_once = false;
}

void test_set_call_bytes(const uint8_t* bytes)
{
    if (bytes == nullptr) {
        __atomic_store_n(&g_test_bytes_set, 0u, __ATOMIC_RELEASE);
        return;
    }
    for (uint32_t k = 0; k < 5; ++k) {
        g_test_bytes[k] = bytes[k];
    }
    __atomic_store_n(&g_test_bytes_set, 1u, __ATOMIC_RELEASE);
}

void test_report_now()
{
    refresh_report();
}

uint32_t test_identify_pending()
{
    return world_marks::test_identify_pending();
}

}  // namespace ilc::hud::battle_hud
