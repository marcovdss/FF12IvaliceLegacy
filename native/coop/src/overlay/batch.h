// Ivalice Legacy co-op overlay: the quad batch and its primitives (pure; the scene composes into it, gpu.cpp uploads
// it as one vertex buffer and draws it with one call).
//
// Space. Everything is placed in the HUD's 1920x1080 authoring units (the co-op layout's units: hud/layout.h,
// hud/menus_draw.h). make_space() maps them onto the back buffer with one uniform scale, centred (letterboxed to
// 16:9), the mapping the HUD layout already assumes; expand() turns a quad into six clip-space vertices in that space.
//
// Quads. Five modes, one shader (overlay.hlsl): SOLID (vertex colours: gradients and end fades), SDF (text and icons
// from the font atlas, with core, edge, glow and shadow), GLASS (a rounded window body with a rim, computed per pixel:
// no texture), GLASS_CHAMFER (GLASS with the top-left and bottom-right corners cut) and HATCH (SOLID with diagonal
// stripes: the damage trail). SOLID and HATCH quads may be skewed (the top edge shifted right: slanted gauges). A GLASS
// quad may be one horizontal band of a taller window (glass_band: a multi-stop gradient as stacked bands, each computing
// the whole window's shape). Colours are packed r | g << 8 | b << 16 | a << 24 (straight alpha, the R8G8B8A8_UNORM
// byte order); batch::alpha fades everything pushed while it is set (the HUD's own fade, a window's slide-in).
//
// A full batch refuses further quads (counted); the caller can roll a half-drawn element back with truncate().
// Pure functions and plain memory: any thread (the render thread in the module, the host tests).
#pragma once

#include <stdint.h>

namespace ilc::overlay {

// Phase 7 capacity: four seat windows (~150 quads each), the party list, Target Info and the battle log within their
// budgets (hud_scene.h: 900 + 800 + 1100) and the test card (~1,300), with headroom: 6,144 quads
// (a 1.77 MB dynamic vertex buffer). Each element is composed within its own budget and rolled back whole.
constexpr uint32_t QUAD_CAP = 6144;
constexpr float AUTHOR_W = 1920.0f;
constexpr float AUTHOR_H = 1080.0f;

enum quad_mode : uint8_t { QM_SOLID = 0, QM_SDF = 1, QM_GLASS = 2, QM_GLASS_CHAMFER = 3, QM_HATCH = 4 };
constexpr uint32_t GLASS_PACK_MAX = 31;   // shine and rim-bias steps packed into the vertex mode (E2)

constexpr uint32_t rgba(uint32_t r, uint32_t g, uint32_t b, uint32_t a)
{
    return (r & 0xFFu) | (g & 0xFFu) << 8 | (b & 0xFFu) << 16 | (a & 0xFFu) << 24;
}
constexpr uint32_t alpha_of(uint32_t c) { return c >> 24; }
uint32_t with_alpha(uint32_t c, float k);          // alpha scaled by k (0..1)
uint32_t set_alpha(uint32_t c, uint32_t a);        // alpha replaced
uint32_t mix(uint32_t a, uint32_t b, float t);     // per channel, t = 0 gives a
// A seat colour as the menu view packs it (0x80 = 1.0 per channel, PS2 style) -> rgba with full alpha.
uint32_t from_ps2(uint32_t packed);

struct quad {
    float x0, y0, x1, y1;   // authoring units
    float u0, v0, u1, v1;   // SDF: atlas texture coordinates; GLASS: the whole window (units) when u1 > u0, else the quad
    uint32_t c[4];          // fill / core colour per corner: top-left, top-right, bottom-left, bottom-right
    uint32_t c1;            // SDF edge and glow colour; GLASS rim colour
    float p0, p1, p2;       // SDF: edge width, glow width, extra softness (distance units)
                            // GLASS: corner radius, cut (CHAMFER) or edge softness (GLASS: a halo), rim width (units)
    uint8_t mode;           // quad_mode
    uint8_t snap;           // SOLID: edges rounded to whole pixels (thin lines stay crisp)
    uint8_t shine;          // GLASS: the diagonal sheen's amount, 0..GLASS_PACK_MAX
    uint8_t bias;           // GLASS: the rim dimming toward the bottom, 0..GLASS_PACK_MAX
    float skew;             // SOLID / HATCH: the top edge shifted right by this many units
};

// A horizontal clip for the quads pushed while it is on (text and icons: SOLID and SDF; GLASS is never clipped): a quad
// outside [x0, x1) is dropped (not refused), one across an edge is trimmed (its texture coordinates with it), and its
// alpha fades to 0 over fade_l units inside the left edge and fade_r inside the right one (the carousel's soft edges).
struct clip_rect {
    float x0, x1;
    float fade_l, fade_r;
    uint8_t on;
    uint8_t reserved[3];
};

class batch {
public:
    void clear();
    bool push(const quad& q);            // false (and counted) when full
    uint32_t count() const { return n_; }
    void truncate(uint32_t n);           // drop everything pushed after the first n
    // The quads pushed from `first` on move in front of the earlier ones (both keep their order): something composed
    // later can claim the batch's room first and still be drawn underneath (the test card under the seat windows).
    void to_front(uint32_t first);
    const quad& at(uint32_t i) const { return q_[i]; }
    uint32_t refused() const { return refused_; }
    // A caller that rolled back the part that did not fit (truncate) takes the refusals since `count` back.
    void forgive(uint32_t count) { refused_ = count < refused_ ? count : refused_; }
    // Moves the quads from `first` on by dy units (a list that dropped its first rows closes the gap).
    void shift_y(uint32_t first, float dy);
    float alpha = 1.0f;                  // multiplies every alpha pushed (0..1)
    clip_rect clip = {};                 // off unless set (clear() turns it off)

private:
    quad q_[QUAD_CAP];
    uint32_t n_ = 0;
    uint32_t refused_ = 0;
};

// ---- primitives (authoring units) ------------------------------------------------------------------------------------
bool solid(batch& b, float x, float y, float w, float h, uint32_t c, bool snap = true);
bool gradient_v(batch& b, float x, float y, float w, float h, uint32_t top, uint32_t bottom, float skew = 0.0f);
// A skewed horizontal gradient (the top edge shifted right by `skew`): one part of a slanted gauge.
bool skewed(batch& b, float x, float y, float w, float h, uint32_t left, uint32_t right, float skew);
// The damage trail with diagonal stripes (QM_HATCH), optionally skewed.
bool hatch(batch& b, float x, float y, float w, float h, uint32_t c, float skew = 0.0f);
bool gradient_h(batch& b, float x, float y, float w, float h, uint32_t left, uint32_t right);
// A thin bar whose ends fade out over `fade` units each (the selection bar).
bool faded_bar(batch& b, float x, float y, float w, float h, uint32_t c, float fade);
// The window body: rounded rectangle, vertical gradient top -> bottom, rim colour, corner radius.
bool glass(batch& b, float x, float y, float w, float h, uint32_t top, uint32_t bottom, uint32_t rim, float radius);
// The themed window body (E2): rounded, or chamfered (the top-left / bottom-right cut); rim_w the rim width (units);
// soft > 0 (rounded only) fades the edge over that many units (a halo); shine / bias the diagonal sheen amount and the
// rim's dimming toward the bottom (0..1). One band [t0, t1] of the window (0 top .. 1 bottom) is drawn with `top` /
// `bottom` at the band's own top and bottom: stacked bands give a multi-stop gradient.
struct glass_shape {
    uint8_t chamfer;        // 1: the cut corners
    uint8_t reserved[3];
    float radius, cut, rim_w, soft, shine, bias;
};
bool glass_band(batch& b, float x, float y, float w, float h, const glass_shape& g, float t0, float t1, uint32_t top,
                uint32_t bottom, uint32_t rim);

struct text_style {
    uint32_t core;          // the letter
    uint32_t edge;          // outline and glow (alpha 0: none)
    float outline;          // outline width, authoring units
    float glow;             // glow beyond the outline, authoring units (0: none)
    uint32_t shadow;        // drop shadow colour (alpha 0: none)
    float shadow_dx, shadow_dy;   // shadow offset, authoring units
    float shadow_soft;      // shadow softness, authoring units
    float tracking = 0.0f;  // extra letter-spacing, em (a title-face label); the theme's tracking rules add to it
    float gap = 0.0f;       // extra advance per glyph, units (HUD text: keeps outlined glyphs clear of each other)
};
// The extra advance per glyph `st` asks for at em size `em` (units): what text(), fit() and measure must all use.
inline float extra_of(const text_style& st, float em) { return st.tracking * em + st.gap; }

// Draws UTF-8 `text` (first n bytes, stops at a NUL) with its line box's top at y; returns the width drawn.
// max_w > 0: cut to fit with "...". align: 0 left at x, 1 right edge at x, 2 centred on x.
float text(batch& b, uint32_t face, float x, float y, float em, const char* s, uint32_t n, const text_style& st,
           float max_w = 0.0f, uint32_t align = 0);
// An icon by name (font::icon_name) filling the box (x, y, size, size); false when the theme has none.
bool icon(batch& b, uint32_t name, float x, float y, float size, const text_style& st);
// One SDF quad of icon `index` (the theme's icon order) filling the box, grown by `grow` units on every side, with a
// vertical core gradient, an edge of `edge_px` units and a glow of `glow_px` (edge alpha = its colour's): a recipe
// layer (E4). False when the index has no glyph.
bool icon_layer(batch& b, uint32_t index, float x, float y, float size, float grow, uint32_t core_top,
                uint32_t core_bottom, uint32_t edge, float edge_px, float glow_px);

// ---- the back buffer --------------------------------------------------------------------------------------------------
struct space {
    uint32_t w, h;          // back buffer, pixels
    float scale;            // pixels per authoring unit
    float ox, oy;           // pixel position of the authoring origin
};
space make_space(uint32_t w, uint32_t h);

struct vertex {
    float x, y;             // clip space
    float u, v;             // SDF: atlas; GLASS: pixels from the window's centre
    float p[4];             // SDF: edge, glow, softness; GLASS: half width, half height, radius, cut or softness (pixels)
    uint32_t c0;            // fill / core
    uint32_t c1;            // edge / rim
    float mode;             // quad_mode; GLASS: + (shine * 32 + bias) / 1024 in the fraction
    float px;               // GLASS: the rim width in pixels; HATCH: pixels per authoring unit
};
static_assert(sizeof(vertex) == 48, "vertex layout (gpu.cpp input layout, overlay.hlsl vs_in)");

// Six vertices (two triangles: top-left, top-right, bottom-left; top-right, bottom-right, bottom-left).
uint32_t expand(const quad& q, const space& s, vertex* out);

}  // namespace ilc::overlay
