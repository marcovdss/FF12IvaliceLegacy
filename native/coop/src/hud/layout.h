// Ivalice Legacy co-op module: the couch layout table (one shared TV), in the HUD's 1920x1080 authoring units.
//
// The corners (the approved battle HUD design):
//
//     Target Info, top-left  ---- one header line ----  battle log, top-right
//
//     guest menus slide out of their own party rows (left of the party list)
//     Player 1's menu, bottom-left (the native command window)          party list, bottom-right (reduced)
//
//   * Target Info and the battle log share one header line (target_info::corner::HEADER_Y). While the native boss gauge
//     shows (HudSnapshot flag SNAP_BOSS_GAUGE), both headers move down by BOSS_SHIFT together, so they stay on one line.
//   * Nothing shrinks to make room: the battle log keeps its zone's bottom and drops its oldest expanded lines instead
//     (log_lines), and text is never drawn below its authored scale (the game's 2D pipeline scales the whole HUD with
//     the screen; TEXT_FLOOR_PX is the smallest physical text height the table allows at 1280x720).
//   * Online is a seam: the mode exists and every function takes it, and it returns the couch table today. The passes
//     draw from the snapshot and the event ring only, so an online screen can be fed the same way later.
//
// Pure functions of their arguments; any thread.
#pragma once

#include <stdint.h>

#include "hud/snapshot.h"

namespace ilc::hud::layout {

struct zone {
    int16_t x, y, w, h;
};

enum corner_id : uint8_t {
    CORNER_TARGET_INFO = 0,   // top-left
    CORNER_LOG = 1,           // top-right
    CORNER_P1_MENU = 2,       // bottom-left
    CORNER_PARTY = 3,         // bottom-right
    CORNER_COUNT
};

constexpr int16_t AUTHOR_W = 1920;
constexpr int16_t AUTHOR_H = 1080;
// The headers' drop while the boss gauge shows: 54 units, the drop measured for the same flag in earlier co-op HUD work
// [U: the in-game look is still to be checked].
constexpr int16_t BOSS_SHIFT = 54;
constexpr int16_t TEXT_FLOOR_PX = 13;      // smallest physical text height, at the smallest screen
constexpr int16_t FONT_HEIGHT = 26;        // [U] the native font's line height at scale 100, authoring units

constexpr zone TARGET_INFO_ZONE = {16, 16, 480, 360};   // the expanded Target Info, everything it can show
constexpr zone LOG_ZONE = {1360, 16, 544, 680};        // the battle log expanded to 20 lines

bool boss_gauge(const hud_snapshot& s);                 // the snapshot's SNAP_BOSS_GAUGE
int16_t header_dy(uint8_t mode, bool boss);             // 0, or BOSS_SHIFT under the boss gauge
// The zones the menus keep clear of, for the mode and the boss gauge (the Target Info zone grows down with its header;
// the log zone keeps its place: the log drops lines instead).
zone target_info_zone(uint8_t mode, bool boss);
zone log_zone(uint8_t mode, bool boss);
// Expanded log lines that fit between the (moved) header and the log zone's bottom: 20 without the boss gauge.
uint32_t log_lines(int16_t dy, uint32_t pitch, int16_t lines_dy, uint32_t want);
// The physical text height (pixels, rounded down) of text at `scale` (x100) on a screen `screen_h` high, with the
// HUD's uniform scale (letterboxed to 16:9).
int32_t text_px(uint16_t scale, uint32_t screen_w, uint32_t screen_h);

// Every corner as it stands: Target Info and log zones, Player 1's menu at its tallest, the party list for `rows`.
void corners(uint8_t mode, bool boss, uint32_t rows, bool reduced, zone out[CORNER_COUNT]);

}  // namespace ilc::hud::layout
