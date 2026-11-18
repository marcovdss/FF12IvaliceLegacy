// Ivalice Legacy co-op overlay: the themed composites (Phase 8, E2-E4): the command window and its tab, the focused-row
// bar, the gauges, and the layered marks (glove, chevrons, seat diamond, dot, status icons) drawn from the active
// theme's tokens and recipes. Pure: reads the active theme (theme.h) and its arguments, writes the batch; no state, no
// C runtime call; the render thread and the host tests call it.
//
//  * window: the theme's shape (rounded, crisp = small radius, chamfered: the top-left and bottom-right corners cut),
//    the vertical gradient of 2..4 stops (stacked glass bands, each computing the whole window's shape), the rim with
//    its dimming toward the bottom, the sheen (soft: a band over the top; line: a one-unit highlight under the top edge;
//    diag: the shader's diagonal sheen), an inner line, an outer ink keyline and a halo as extra quads underneath.
//  * gauges: A rounded (glass, radius = height / 2), B square with ticks, C slanted (every part skewed by slant x
//    height, the trail hatched); the fill is clamped to the trough (no stencil).
//  * recipes: one SDF quad per layer of the icon glyph (THEME-FORMAT.md 3), colours resolved here.
#pragma once

#include <stdint.h>

#include "overlay/batch.h"
#include "overlay/theme.h"

namespace ilc::overlay::marks {

enum dir : uint32_t { DIR_UP = 0, DIR_DOWN = 1, DIR_LEFT = 2, DIR_RIGHT = 3 };

// What a recipe's colour references resolve to.
struct recipe_ctx {
    uint32_t seat;          // rgba of the seat (seat, seat_light, seat_dark)
    uint32_t dot;           // dot_colour
    uint32_t status;        // status icon colour
    uint32_t index;         // GT_DIR: the direction; GT_NAME: the theme's status index
};
uint32_t resolve_colour(const theme::colour& c, const recipe_ctx& x, uint32_t fallback);
// Draws `r` in the box (x, y, size, size); false when a layer could not be pushed.
bool recipe(batch& b, const theme::recipe& r, float x, float y, float size, const recipe_ctx& x_ctx);

void window(batch& b, float x, float y, float w, float h);
void tab(batch& b, float x, float y, float w, float h);
void focus_bar(batch& b, float x, float y, float w, float h, uint32_t seat_rgba);

void glove(batch& b, float x, float y, float size, uint32_t seat_rgba);
void chevron(batch& b, uint32_t d, float x, float y, float size);
void diamond(batch& b, float x, float y, float size, uint32_t seat_rgba);
void dot(batch& b, float x, float y, float size, uint32_t rgba);
// The theme's own icon for status `bit`; false when the theme has none (the caller draws the stand-in token).
bool status_icon(batch& b, float x, float y, float size, uint32_t bit, float dim = 1.0f);
bool has_status_icon(uint32_t bit);

// A gauge: the trough, the trail up to `ghost`, the fill up to `value` (fill = {top, bottom}); h = the gauge's height.
void gauge(batch& b, float x, float y, float w, float h, uint32_t value, uint32_t ghost, uint32_t max,
           const uint32_t fill[2], uint32_t trail);

}  // namespace ilc::overlay::marks
