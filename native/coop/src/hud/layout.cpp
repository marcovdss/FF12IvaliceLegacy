// Ivalice Legacy co-op module: the couch layout table (see layout.h).
#include "hud/layout.h"

#include "hud/menus_draw.h"
#include "hud/target_info.h"

namespace ilc::hud::layout {

bool boss_gauge(const hud_snapshot& s)
{
    return (s.head.flags & SNAP_BOSS_GAUGE) != 0;
}

int16_t header_dy(uint8_t mode, bool boss)
{
    (void)mode;   // online: the same corners (seam)
    return boss ? BOSS_SHIFT : 0;
}

zone target_info_zone(uint8_t mode, bool boss)
{
    zone z = TARGET_INFO_ZONE;
    z.h = static_cast<int16_t>(z.h + header_dy(mode, boss));
    return z;
}

zone log_zone(uint8_t mode, bool boss)
{
    (void)mode;
    (void)boss;   // the log keeps its zone and drops lines (log_lines)
    return LOG_ZONE;
}

uint32_t log_lines(int16_t dy, uint32_t pitch, int16_t lines_dy, uint32_t want)
{
    // From the shared header line down: the last line's bottom stays on or above the zone's bottom.
    const int32_t first = target_info::corner::HEADER_Y + dy + lines_dy;
    const int32_t room = LOG_ZONE.y + LOG_ZONE.h - first;
    if (room <= 0 || pitch == 0) {
        return 0;
    }
    const uint32_t fit = static_cast<uint32_t>(room) / pitch;
    return fit < want ? fit : want;
}

int32_t text_px(uint16_t scale, uint32_t screen_w, uint32_t screen_h)
{
    if (screen_w == 0 || screen_h == 0) {
        return 0;
    }
    // The uniform scale min(w / 1920, h / 1080) as an exact fraction m / (1920 * 1080), rounded down once at the end.
    const uint64_t by_w = static_cast<uint64_t>(screen_w) * AUTHOR_H;
    const uint64_t by_h = static_cast<uint64_t>(screen_h) * AUTHOR_W;
    const uint64_t m = by_w < by_h ? by_w : by_h;
    return static_cast<int32_t>(static_cast<uint64_t>(FONT_HEIGHT) * scale * m /
                                (100ull * static_cast<uint64_t>(AUTHOR_W) * static_cast<uint64_t>(AUTHOR_H)));
}

void corners(uint8_t mode, bool boss, uint32_t rows, bool reduced, zone out[CORNER_COUNT])
{
    out[CORNER_TARGET_INFO] = target_info_zone(mode, boss);
    out[CORNER_LOG] = log_zone(mode, boss);
    const menus_draw::box p1 = menus_draw::p1_window_zone();
    out[CORNER_P1_MENU] = {p1.x, p1.y, p1.w, p1.h};
    const menus_draw::box party = menus_draw::party_list(rows, reduced);
    out[CORNER_PARTY] = {party.x, party.y, party.w, party.h};
}

}  // namespace ilc::hud::layout
