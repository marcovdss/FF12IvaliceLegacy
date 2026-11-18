// Ivalice Legacy co-op overlay: the themed composites (see marks.h). No C runtime calls.
#include "overlay/marks.h"

#include "overlay/font.h"

namespace ilc::overlay::marks {
namespace {

constexpr float CHAMFER_GROW = 0.5858f;   // 2 - sqrt(2): a 45-degree cut moved out by d keeps a d-wide border

uint32_t scale_rgb(uint32_t c, float k, float add)
{
    uint32_t out = c & 0xFF000000u;
    for (uint32_t shift = 0; shift < 24; shift += 8) {
        float v = static_cast<float>((c >> shift) & 0xFFu) * k + add;
        v = v < 0.0f ? 0.0f : (v > 255.0f ? 255.0f : v);
        out |= static_cast<uint32_t>(v + 0.5f) << shift;
    }
    return out;
}

uint32_t white(uint32_t a) { return rgba(255, 255, 255, a); }

glass_shape shape_of(uint8_t shape, float radius, float cut, float rim_w)
{
    glass_shape g = {};
    g.chamfer = shape == theme::SHAPE_CHAMFER ? 1 : 0;
    g.radius = radius;
    g.cut = g.chamfer != 0 ? cut : 0.0f;
    g.rim_w = rim_w;
    return g;
}

// The stops as stacked bands (a stop list that does not start at 0 or end at 1 is held flat to the edge).
void bands(batch& b, float x, float y, float w, float h, const glass_shape& g, const float* t, const uint32_t* c,
           uint32_t n, uint32_t rim)
{
    if (n == 0) {
        return;
    }
    if (n == 1 || t[0] > 0.0f) {
        glass_band(b, x, y, w, h, g, 0.0f, n == 1 ? 1.0f : t[0], c[0], c[0], rim);
    }
    for (uint32_t k = 0; k + 1 < n; ++k) {
        glass_band(b, x, y, w, h, g, t[k], t[k + 1], c[k], c[k + 1], rim);
    }
    if (n > 1 && t[n - 1] < 1.0f) {
        glass_band(b, x, y, w, h, g, t[n - 1], 1.0f, c[n - 1], c[n - 1], rim);
    }
}

}  // namespace

uint32_t resolve_colour(const theme::colour& c, const recipe_ctx& x, uint32_t fallback)
{
    const theme::tokens& t = theme::active();
    switch (c.ref) {
    case theme::CR_RGBA: return c.rgba;
    case theme::CR_SEAT: return set_alpha(x.seat, 255);
    case theme::CR_SEAT_LIGHT: return mix(set_alpha(x.seat, 255), white(255), 0.45f);
    case theme::CR_SEAT_DARK: return scale_rgb(set_alpha(x.seat, 255), 0.55f, 0.0f);
    case theme::CR_INK: return set_alpha(t.ink, 255);
    case theme::CR_WHITE: return white(255);
    case theme::CR_WHITE_SOFT: return white(230);
    case theme::CR_INK_SOFT: return set_alpha(t.ink, 150);
    case theme::CR_CHEVRON: return set_alpha(t.chev, 255);
    case theme::CR_DOT: return x.dot;
    case theme::CR_STATUS: return set_alpha(x.status, 255);
    case theme::CR_STATUS_LIGHT: return mix(set_alpha(x.status, 255), white(255), 0.5f);
    case theme::CR_STATUS_MID: return scale_rgb(set_alpha(x.status, 255), 0.55f, 18.0f);
    case theme::CR_STATUS_DARK: return scale_rgb(set_alpha(x.status, 255), 0.38f, 0.0f);
    default: return fallback;
    }
}

bool recipe(batch& b, const theme::recipe& r, float x, float y, float size, const recipe_ctx& ctx)
{
    bool ok = true;
    for (uint32_t k = 0; k < r.n && k < theme::RECIPE_MAX; ++k) {
        const theme::layer& l = r.l[k];
        const uint32_t slot = l.tmpl == theme::GT_PLAIN ? 0u : (ctx.index < theme::STATUS_ICONS_MAX ? ctx.index : 0u);
        const uint16_t glyph = l.glyph[slot];
        if (glyph == font::ICON_NONE) {
            continue;
        }
        const uint32_t top = resolve_colour(l.core_top, ctx, 0);
        const uint32_t bottom = resolve_colour(l.core_bottom, ctx, top);
        uint32_t edge = resolve_colour(l.edge, ctx, 0);
        if (l.glow_px > 0.0f) {
            // The glow uses the edge colour (or the core's when the layer has no edge) at glow_alpha.
            const uint32_t base = alpha_of(edge) != 0 ? edge : top;
            edge = with_alpha(set_alpha(base, 255), l.glow_alpha);
        }
        if (l.outline.ref != theme::CR_NONE) {
            const uint32_t oc = resolve_colour(l.outline, ctx, 0);
            ok = icon_layer(b, glyph, x, y, size, l.grow + 1.2f, oc, oc, oc, l.edge_px + 0.8f, 0.0f) && ok;
        }
        ok = icon_layer(b, glyph, x, y, size, l.grow, top, bottom, edge, l.edge_px, l.glow_px) && ok;
    }
    return ok;
}

void window(batch& b, float x, float y, float w, float h)
{
    const theme::tokens& t = theme::active();
    glass_shape g = shape_of(t.win_shape, t.win_radius, t.win_cut, t.win_rim_px);
    // Underneath: the halo (a soft, larger glass) and the outer ink keyline (a rim-only glass one keyline larger).
    if (alpha_of(t.halo) != 0 && t.halo_sigma > 0.0f) {
        const float s = t.halo_sigma;
        glass_shape hg = shape_of(theme::SHAPE_ROUND, t.win_radius + s, 0.0f, 1.0f);
        hg.soft = s * 1.5f;
        glass_band(b, x - s * 0.5f, y - s * 0.5f, w + s, h + s, hg, 0.0f, 1.0f, t.halo, set_alpha(t.halo, 0), 0);
    }
    if (alpha_of(t.outer_line) != 0 && t.outer_px > 0.0f) {
        const float d = t.outer_px;
        glass_shape og = shape_of(t.win_shape, t.win_radius + d, t.win_cut + d * CHAMFER_GROW, d);
        glass_band(b, x - d, y - d, w + 2.0f * d, h + 2.0f * d, og, 0.0f, 1.0f, 0, 0, t.outer_line);
    }
    if (t.shine == theme::SHINE_DIAG) {
        g.shine = t.shine_amount;
    }
    g.bias = t.win_rim_top_bias;
    bands(b, x, y, w, h, g, t.stop_t, t.stop_c, t.win_stops, t.win_rim);
    if (alpha_of(t.inner_line) != 0) {
        const float i = t.win_rim_px + 0.5f;
        glass_shape ig = shape_of(t.win_shape, t.win_radius > i ? t.win_radius - i : 0.0f,
                                  t.win_cut > i * CHAMFER_GROW ? t.win_cut - i * CHAMFER_GROW : 0.0f, 1.0f);
        glass_band(b, x + i, y + i, w - 2.0f * i, h - 2.0f * i, ig, 0.0f, 1.0f, 0, 0, t.inner_line);
    }
    if (t.shine == theme::SHINE_SOFT && t.shine_amount > 0.0f) {
        const uint32_t c = set_alpha(t.shine_rgba, static_cast<uint32_t>(t.shine_amount * 255.0f));
        const float inset = t.win_radius * 0.4f + 3.0f;
        gradient_v(b, x + inset, y + 3.0f, w - 2.0f * inset, h * (t.shine_extent > 0.0f ? t.shine_extent : 0.34f), c,
                   set_alpha(c, 0));
    } else if (t.shine == theme::SHINE_LINE && t.shine_amount > 0.0f) {
        const float inset = t.win_radius + 2.0f;
        solid(b, x + inset, y + t.win_rim_px + 1.0f, w - 2.0f * inset, 1.0f,
              white(static_cast<uint32_t>(t.shine_amount * 255.0f)), false);
    }
}

void tab(batch& b, float x, float y, float w, float h)
{
    const theme::tokens& t = theme::active();
    float cut = t.win_cut * 0.6f;
    if (cut > h * 0.45f) {
        cut = h * 0.45f;
    }
    glass_shape g = shape_of(t.tab_shape, t.tab_radius, cut, t.win_rim_px);
    g.bias = t.win_rim_top_bias;
    if (alpha_of(t.outer_line) != 0 && t.outer_px > 0.0f) {
        const float d = t.outer_px;
        glass_shape og = shape_of(t.tab_shape, t.tab_radius + d, cut + d * CHAMFER_GROW, d);
        glass_band(b, x - d, y - d, w + 2.0f * d, h + 2.0f * d, og, 0.0f, 1.0f, 0, 0, t.outer_line);
    }
    glass_band(b, x, y, w, h, g, 0.0f, 1.0f, t.tab_top, t.tab_bottom, t.win_rim);
}

void focus_bar(batch& b, float x, float y, float w, float h, uint32_t seat_rgba)
{
    const theme::tokens& t = theme::active();
    if (t.focus_bar == 0) {
        return;
    }
    const uint32_t c = scale_rgb(set_alpha(seat_rgba, 255), t.focus_darken, 0.0f);
    gradient_v(b, x, y, w, h, set_alpha(c, t.focus_alpha_top), set_alpha(c, t.focus_alpha_bottom));
    solid(b, x, y, w, 1.2f, set_alpha(seat_rgba, t.focus_edge_alpha), false);
    solid(b, x, y + h - 1.2f, w, 1.2f, set_alpha(seat_rgba, t.focus_edge_alpha / 2u), false);
}

void glove(batch& b, float x, float y, float size, uint32_t seat_rgba)
{
    recipe(b, theme::active().glove_r, x, y, size, recipe_ctx{seat_rgba, 0, 0, 0});
}

void chevron(batch& b, uint32_t d, float x, float y, float size)
{
    recipe(b, theme::active().chevron_r, x, y, size, recipe_ctx{0, 0, 0, d & 3u});
}

void diamond(batch& b, float x, float y, float size, uint32_t seat_rgba)
{
    recipe(b, theme::active().diamond_r, x, y, size, recipe_ctx{seat_rgba, 0, 0, 0});
}

void dot(batch& b, float x, float y, float size, uint32_t c)
{
    recipe(b, theme::active().dot_r, x, y, size, recipe_ctx{0, c, 0, 0});
}

bool has_status_icon(uint32_t bit)
{
    const theme::tokens& t = theme::active();
    for (uint32_t k = 0; k < t.status_count; ++k) {
        if (t.status_bit[k] == bit) {
            return t.status_r.n != 0;
        }
    }
    return false;
}

bool status_icon(batch& b, float x, float y, float size, uint32_t bit, float dim)
{
    const theme::tokens& t = theme::active();
    for (uint32_t k = 0; k < t.status_count; ++k) {
        if (t.status_bit[k] != bit || t.status_r.n == 0) {
            continue;
        }
        const float a0 = b.alpha;
        b.alpha = a0 * dim;
        const uint32_t c = t.status_rgb[k];
        recipe(b, t.status_r, x, y, size, recipe_ctx{0, 0, c, k});
        if (t.status_style == theme::STATUS_CHIP) {
            solid(b, x + size * 0.18f, y + size * 0.84f, size * 0.64f, 1.4f, set_alpha(c, 255), false);
        }
        b.alpha = a0;
        return true;
    }
    return false;
}

void gauge(batch& b, float x, float y, float w, float h, uint32_t value, uint32_t ghost, uint32_t max,
           const uint32_t fill[2], uint32_t trail)
{
    const theme::tokens& t = theme::active();
    const auto frac = [max](uint32_t v) {
        return max == 0 ? 0.0f : static_cast<float>(v > max ? max : v) / static_cast<float>(max);
    };
    const float fw = w * frac(value);
    const float gw = ghost > value ? w * frac(ghost) : 0.0f;
    switch (t.gauge_kind) {
    case theme::GAUGE_A: {   // rounded
        glass(b, x - 1.0f, y - 1.0f, w + 2.0f, h + 2.0f, t.g_track, t.g_track, 0, h * 0.5f + 1.0f);
        if (gw > 0.0f) {
            glass(b, x, y, gw > h ? gw : h, h, trail, trail, 0, h * 0.5f);
        }
        if (fw > 0.0f) {
            glass(b, x, y, fw > h ? fw : h, h, fill[0], fill[1], 0, h * 0.5f);
        }
        break;
    }
    case theme::GAUGE_B: {   // square, with ticks
        solid(b, x - 1.0f, y - 1.0f, w + 2.0f, h + 2.0f, t.g_track, false);
        if (gw > 0.0f) {
            if (t.trail_hatch != 0) {
                hatch(b, x, y, gw, h, trail);
            } else {
                solid(b, x, y, gw, h, trail, false);
            }
        }
        gradient_v(b, x, y, fw, h, fill[0], fill[1]);
        // Integer-bounded (review ★1): n = floor(1 / ticks) inner ticks at most, never more than GAUGE_TICKS_DRAWN,
        // whatever the tokens hold (the reader refuses 0 < ticks_every < 0.02; this holds without it).
        if (t.gauge_ticks > 0.0f) {
            const float per = 1.0f / t.gauge_ticks;
            const uint32_t n = per >= static_cast<float>(theme::GAUGE_TICKS_DRAWN) ? theme::GAUGE_TICKS_DRAWN
                                                                                   : static_cast<uint32_t>(per);
            for (uint32_t k = 1; k <= n; ++k) {
                const float f = t.gauge_ticks * static_cast<float>(k);
                if (f >= 0.999f) {
                    break;
                }
                solid(b, x + w * f, y, 1.0f, h, set_alpha(t.ink, 120), false);
            }
        }
        break;
    }
    default: {               // slanted: every part skewed by slant x height, parallel diagonal ends
        const float skew = t.gauge_slant * h;
        const float tskew = t.gauge_slant * (h + 2.0f);
        skewed(b, x - 1.0f - (tskew - skew) * 0.5f, y - 1.0f, w + 2.0f, h + 2.0f, t.g_track, t.g_track, tskew);
        if (gw > 0.0f) {
            if (t.trail_hatch != 0) {
                hatch(b, x, y, gw, h, trail, skew);
            } else {
                skewed(b, x, y, gw, h, trail, trail, skew);
            }
        }
        if (fw > 0.0f) {
            gradient_v(b, x, y, fw, h, fill[0], fill[1], skew);
            gradient_v(b, x + skew * 0.66f, y, fw, h * 0.34f, white(70), white(0), skew * 0.34f);
        }
        break;
    }
    }
}

}  // namespace ilc::overlay::marks
