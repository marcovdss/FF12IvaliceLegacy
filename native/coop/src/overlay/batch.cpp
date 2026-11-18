// Ivalice Legacy co-op overlay: the quad batch and its primitives (see batch.h).
//
// No C runtime calls (no roundf, no memcpy of size the compiler could turn into a call): the module runs this on the
// render thread inside the fault guard, and the host tests call it directly.
#include "overlay/batch.h"

#include "overlay/font.h"

namespace ilc::overlay {
namespace {

float clamp01(float v) { return v < 0.0f ? 0.0f : (v > 1.0f ? 1.0f : v); }

uint32_t channel(uint32_t c, uint32_t shift) { return (c >> shift) & 0xFFu; }

float round_px(float v)
{
    return static_cast<float>(static_cast<int32_t>(v >= 0.0f ? v + 0.5f : v - 0.5f));
}

uint8_t pack(float v)
{
    return static_cast<uint8_t>(clamp01(v) * static_cast<float>(GLASS_PACK_MAX) + 0.5f);
}

quad blank(float x, float y, float w, float h, uint8_t mode)
{
    quad q = {};
    q.x0 = x;
    q.y0 = y;
    q.x1 = x + w;
    q.y1 = y + h;
    q.mode = mode;
    return q;
}

void fill4(quad& q, uint32_t tl, uint32_t tr, uint32_t bl, uint32_t br)
{
    q.c[0] = tl;
    q.c[1] = tr;
    q.c[2] = bl;
    q.c[3] = br;
}

// The style's widths in distance units of the atlas at scale k (authoring units per atlas pixel). The edge and the
// glow together stay inside the spread the atlas holds (beyond it every distance reads 0).
struct sdf_params {
    float outline, glow;
};
sdf_params params_of(float outline_units, float glow_units, float k)
{
    const float to_d = 1.0f / (k * 2.0f * static_cast<float>(font::spread()));
    sdf_params p = {outline_units * to_d, glow_units * to_d};
    if (p.outline > 0.4f) {
        p.outline = 0.4f;
    }
    if (p.outline + p.glow > 0.46f) {
        p.glow = 0.46f - p.outline;
    }
    if (p.glow < 0.0f) {
        p.glow = 0.0f;
    }
    return p;
}

void cell_uv(quad& q, const font::glyph& g)
{
    const float aw = static_cast<float>(font::atlas_w());
    const float ah = static_cast<float>(font::atlas_h());
    q.u0 = static_cast<float>(g.x) / aw;
    q.v0 = static_cast<float>(g.y) / ah;
    q.u1 = static_cast<float>(g.x + g.w) / aw;
    q.v1 = static_cast<float>(g.y + g.h) / ah;
}

bool glyph_quad(batch& b, const font::glyph& g, float x0, float y0, float k, uint32_t core, uint32_t edge, float p0,
                float p1, float p2)
{
    if (g.w == 0 || g.h == 0) {
        return true;
    }
    quad q = blank(x0, y0, static_cast<float>(g.w) * k, static_cast<float>(g.h) * k, QM_SDF);
    cell_uv(q, g);
    fill4(q, core, core, core, core);
    q.c1 = edge;
    q.p0 = p0;
    q.p1 = p1;
    q.p2 = p2;
    return b.push(q);
}

// One run of glyphs on a baseline: the first `n` bytes of UTF-8 `s`, then "..." when `dots` (a cut string). The pen
// advances exactly as font::measure / font::fit count it (advance, kerning, the extra per glyph).
bool glyph_run(batch& b, uint32_t face, float x, float baseline, float k, const char* s, uint32_t n, bool dots,
               float track, uint32_t core, uint32_t edge, float p0, float p1, float p2)
{
    bool ok = true;
    float pen = x;
    int64_t pen64 = 0;
    float added = 0.0f;
    uint32_t at = 0;
    uint32_t prev = 0;
    uint32_t count = 0;
    const uint32_t extra = dots ? 3u : 0u;
    uint32_t dot = 0;
    while (at < n || dot < extra) {
        uint32_t c = '.';
        if (at < n) {
            c = font::next_code(s, n, &at);
        } else {
            ++dot;
        }
        if (count != 0) {
            pen64 += font::kern64(face, prev, c);
        }
        const font::glyph& g = font::resolve(face, c);
        pen = x + static_cast<float>(pen64) / 64.0f * k + added;
        ok = glyph_quad(b, g, pen + static_cast<float>(g.bx) * k, baseline + static_cast<float>(g.by) * k, k, core, edge,
                        p0, p1, p2) && ok;
        pen64 += font::advance64(face, c, g);
        added += track;
        prev = c;
        ++count;
    }
    return ok;
}

}  // namespace

uint32_t with_alpha(uint32_t c, float k)
{
    const float a = static_cast<float>(alpha_of(c)) * clamp01(k);
    return (c & 0x00FFFFFFu) | static_cast<uint32_t>(a + 0.5f) << 24;
}

uint32_t set_alpha(uint32_t c, uint32_t a)
{
    return (c & 0x00FFFFFFu) | (a & 0xFFu) << 24;
}

uint32_t mix(uint32_t a, uint32_t b, float t)
{
    t = clamp01(t);
    uint32_t out = 0;
    for (uint32_t shift = 0; shift < 32; shift += 8) {
        const float va = static_cast<float>(channel(a, shift));
        const float vb = static_cast<float>(channel(b, shift));
        out |= static_cast<uint32_t>(va + (vb - va) * t + 0.5f) << shift;
    }
    return out;
}

uint32_t from_ps2(uint32_t packed)
{
    uint32_t out = 0xFF000000u;
    for (uint32_t shift = 0; shift < 24; shift += 8) {
        uint32_t v = channel(packed, shift) * 2;
        out |= (v > 255 ? 255 : v) << shift;
    }
    return out;
}

// ---- batch -----------------------------------------------------------------------------------------------------------
void batch::clear()
{
    n_ = 0;
    refused_ = 0;
    alpha = 1.0f;
    clip = clip_rect{};
}

namespace {
float fade_at(const clip_rect& c, float x)
{
    float f = 1.0f;
    if (c.fade_l > 0.0f) {
        const float l = (x - c.x0) / c.fade_l;
        f = l < f ? l : f;
    }
    if (c.fade_r > 0.0f) {
        const float r = (c.x1 - x) / c.fade_r;
        f = r < f ? r : f;
    }
    return f < 0.0f ? 0.0f : (f > 1.0f ? 1.0f : f);
}

bool is_glass(uint8_t mode) { return mode == QM_GLASS || mode == QM_GLASS_CHAMFER; }
}  // namespace

bool batch::push(const quad& q)
{
    if (n_ >= QUAD_CAP) {
        ++refused_;
        return false;
    }
    const bool clipped = clip.on != 0 && !is_glass(q.mode);
    if (clipped && (q.x1 <= clip.x0 || q.x0 >= clip.x1)) {
        return true;   // wholly outside the clip: nothing to draw (not a refusal)
    }
    quad& d = q_[n_++];
    d = q;
    if (clipped) {
        const float w = q.x1 - q.x0;
        if (w > 0.0f && d.x0 < clip.x0) {
            d.u0 += (q.u1 - q.u0) * ((clip.x0 - q.x0) / w);
            d.x0 = clip.x0;
        }
        if (w > 0.0f && d.x1 > clip.x1) {
            d.u1 -= (q.u1 - q.u0) * ((q.x1 - clip.x1) / w);
            d.x1 = clip.x1;
        }
        const float fl = fade_at(clip, d.x0);
        const float fr = fade_at(clip, d.x1);
        if (fl < 1.0f || fr < 1.0f) {
            d.c[0] = with_alpha(d.c[0], fl);
            d.c[2] = with_alpha(d.c[2], fl);
            d.c[1] = with_alpha(d.c[1], fr);
            d.c[3] = with_alpha(d.c[3], fr);
            d.c1 = with_alpha(d.c1, fl < fr ? fl : fr);
        }
    }
    if (alpha < 1.0f) {
        for (uint32_t k = 0; k < 4; ++k) {
            d.c[k] = with_alpha(d.c[k], alpha);
        }
        d.c1 = with_alpha(d.c1, alpha);
    }
    return true;
}

void batch::truncate(uint32_t n)
{
    if (n < n_) {
        n_ = n;
    }
}

void batch::shift_y(uint32_t first, float dy)
{
    for (uint32_t k = first; k < n_; ++k) {
        q_[k].y0 += dy;
        q_[k].y1 += dy;
        if ((q_[k].mode == QM_GLASS || q_[k].mode == QM_GLASS_CHAMFER) && q_[k].u1 > q_[k].u0) {
            q_[k].v0 += dy;   // a glass band's whole-window rectangle moves with it
            q_[k].v1 += dy;
        }
    }
}

void batch::to_front(uint32_t first)
{
    if (first == 0 || first >= n_) {
        return;
    }
    // A rotation by three reversals: [0, first) and [first, n) each reversed, then the whole.
    const auto reverse = [this](uint32_t a, uint32_t b) {   // [a, b)
        while (a + 1 < b) {
            const quad t = q_[a];
            q_[a] = q_[b - 1];
            q_[b - 1] = t;
            ++a;
            --b;
        }
    };
    reverse(0, first);
    reverse(first, n_);
    reverse(0, n_);
}

// ---- primitives ------------------------------------------------------------------------------------------------------
bool solid(batch& b, float x, float y, float w, float h, uint32_t c, bool snap)
{
    if (w <= 0.0f || h <= 0.0f || alpha_of(c) == 0) {
        return true;
    }
    quad q = blank(x, y, w, h, QM_SOLID);
    fill4(q, c, c, c, c);
    q.snap = snap ? 1 : 0;
    return b.push(q);
}

bool gradient_v(batch& b, float x, float y, float w, float h, uint32_t top, uint32_t bottom, float skew)
{
    if (w <= 0.0f || h <= 0.0f) {
        return true;
    }
    quad q = blank(x, y, w, h, QM_SOLID);
    fill4(q, top, top, bottom, bottom);
    q.skew = skew;
    return b.push(q);
}

bool gradient_h(batch& b, float x, float y, float w, float h, uint32_t left, uint32_t right)
{
    if (w <= 0.0f || h <= 0.0f) {
        return true;
    }
    quad q = blank(x, y, w, h, QM_SOLID);
    fill4(q, left, right, left, right);
    return b.push(q);
}

bool skewed(batch& b, float x, float y, float w, float h, uint32_t left, uint32_t right, float skew)
{
    if (w <= 0.0f || h <= 0.0f) {
        return true;
    }
    quad q = blank(x, y, w, h, QM_SOLID);
    fill4(q, left, right, left, right);
    q.skew = skew;
    return b.push(q);
}

bool hatch(batch& b, float x, float y, float w, float h, uint32_t c, float skew)
{
    if (w <= 0.0f || h <= 0.0f || alpha_of(c) == 0) {
        return true;
    }
    quad q = blank(x, y, w, h, QM_HATCH);
    fill4(q, c, c, c, c);
    q.skew = skew;
    return b.push(q);
}

bool faded_bar(batch& b, float x, float y, float w, float h, uint32_t c, float fade)
{
    if (w <= 0.0f || h <= 0.0f) {
        return true;
    }
    if (fade * 2.0f > w) {
        fade = w / 2.0f;
    }
    const uint32_t clear = set_alpha(c, 0);
    bool ok = gradient_h(b, x, y, fade, h, clear, c);
    ok = solid(b, x + fade, y, w - 2.0f * fade, h, c, false) && ok;
    ok = gradient_h(b, x + w - fade, y, fade, h, c, clear) && ok;
    return ok;
}

bool glass(batch& b, float x, float y, float w, float h, uint32_t top, uint32_t bottom, uint32_t rim, float radius)
{
    if (w <= 0.0f || h <= 0.0f) {
        return true;
    }
    quad q = blank(x, y, w, h, QM_GLASS);
    fill4(q, top, top, bottom, bottom);
    q.c1 = rim;
    q.p0 = radius;
    return b.push(q);
}

bool glass_band(batch& b, float x, float y, float w, float h, const glass_shape& g, float t0, float t1, uint32_t top,
                uint32_t bottom, uint32_t rim)
{
    if (w <= 0.0f || h <= 0.0f || t1 <= t0) {
        return true;
    }
    quad q = blank(x, y + h * t0, w, h * (t1 - t0), g.chamfer != 0 ? QM_GLASS_CHAMFER : QM_GLASS);
    q.u0 = x;
    q.v0 = y;
    q.u1 = x + w;
    q.v1 = y + h;
    fill4(q, top, top, bottom, bottom);
    q.c1 = rim;
    q.p0 = g.radius;
    q.p1 = g.chamfer != 0 ? g.cut : g.soft;
    q.p2 = g.rim_w;
    q.shine = pack(g.shine);
    q.bias = pack(g.bias);
    return b.push(q);
}

float text(batch& b, uint32_t face, float x, float y, float em, const char* s, uint32_t n, const text_style& st,
           float max_w, uint32_t align)
{
    if (face >= font::FACE_ICONS || em <= 0.0f) {
        return 0.0f;
    }
    n = font::length(s, n);
    if (n == 0) {
        return 0.0f;
    }
    const float extra = extra_of(st, em);
    bool cut = false;
    float width = 0.0f;
    uint32_t keep = n;
    if (max_w > 0.0f) {
        keep = font::fit(face, em, s, n, max_w, &cut, &width, extra);
    } else {
        width = font::measure(face, em, s, n, extra);
    }
    const float x0 = align == 1 ? x - width : (align == 2 ? x - width * 0.5f : x);
    const float k = em / static_cast<float>(font::em());
    const float baseline = y + font::ascent(face) * k;
    const float track = font::tracking(em, extra);
    const sdf_params p = params_of(st.outline, st.glow, k);
    if (alpha_of(st.shadow) != 0) {
        const float soft = st.shadow_soft / (k * 2.0f * static_cast<float>(font::spread()));
        glyph_run(b, face, x0 + st.shadow_dx, baseline + st.shadow_dy, k, s, keep, cut, track, st.shadow,
                  with_alpha(st.shadow, 0.6f), p.outline, 0.0f, soft);
    }
    glyph_run(b, face, x0, baseline, k, s, keep, cut, track, st.core, st.edge, p.outline, p.glow, 0.0f);
    return width;
}

bool icon(batch& b, uint32_t name, float x, float y, float size, const text_style& st)
{
    const font::glyph* g = font::icon(name);
    if (g == nullptr || size <= 0.0f) {
        return false;
    }
    const float k = size / static_cast<float>(font::em());
    const sdf_params p = params_of(st.outline, st.glow, k);
    bool ok = true;
    if (alpha_of(st.shadow) != 0) {
        const float soft = st.shadow_soft / (k * 2.0f * static_cast<float>(font::spread()));
        ok = glyph_quad(b, *g, x + st.shadow_dx + static_cast<float>(g->bx) * k,
                        y + st.shadow_dy + static_cast<float>(g->by) * k, k, st.shadow, with_alpha(st.shadow, 0.6f),
                        p.outline, 0.0f, soft);
    }
    return glyph_quad(b, *g, x + static_cast<float>(g->bx) * k, y + static_cast<float>(g->by) * k, k, st.core, st.edge,
                      p.outline, p.glow, 0.0f) && ok;
}

bool icon_layer(batch& b, uint32_t index, float x, float y, float size, float grow, uint32_t core_top,
                uint32_t core_bottom, uint32_t edge, float edge_px, float glow_px)
{
    const font::glyph* g = font::find(font::FACE_ICONS, index);
    if (g == nullptr || g->w == 0 || size <= 0.0f) {
        return false;
    }
    const float k = size / static_cast<float>(font::em());
    const float x0 = x + static_cast<float>(g->bx) * k;
    const float y0 = y + static_cast<float>(g->by) * k;
    const float w = static_cast<float>(g->w) * k;
    const float h = static_cast<float>(g->h) * k;
    // Grown on every side, same centre: the glyph is drawn that much bigger (a keyline under the next layer).
    quad q = blank(x0 - grow, y0 - grow, w + 2.0f * grow, h + 2.0f * grow, QM_SDF);
    cell_uv(q, *g);
    fill4(q, core_top, core_top, core_bottom, core_bottom);
    q.c1 = edge;
    const float kk = k * (w + 2.0f * grow) / w;   // the distance scale of the grown quad
    const sdf_params p = params_of(edge_px, glow_px, kk);
    q.p0 = alpha_of(edge) != 0 && edge_px > 0.0f ? p.outline : 0.0f;
    q.p1 = glow_px > 0.0f ? p.glow : 0.0f;
    q.p2 = 0.0f;
    return b.push(q);
}

// ---- the back buffer --------------------------------------------------------------------------------------------------
space make_space(uint32_t w, uint32_t h)
{
    space s = {w, h, 0.0f, 0.0f, 0.0f};
    if (w == 0 || h == 0) {
        return s;
    }
    const float sx = static_cast<float>(w) / AUTHOR_W;
    const float sy = static_cast<float>(h) / AUTHOR_H;
    s.scale = sx < sy ? sx : sy;
    s.ox = (static_cast<float>(w) - AUTHOR_W * s.scale) * 0.5f;
    s.oy = (static_cast<float>(h) - AUTHOR_H * s.scale) * 0.5f;
    return s;
}

uint32_t expand(const quad& q, const space& s, vertex* out)
{
    float X0 = s.ox + q.x0 * s.scale;
    float Y0 = s.oy + q.y0 * s.scale;
    float X1 = s.ox + q.x1 * s.scale;
    float Y1 = s.oy + q.y1 * s.scale;
    const bool glassy = q.mode == QM_GLASS || q.mode == QM_GLASS_CHAMFER;
    if (q.mode == QM_SOLID && q.snap != 0 && q.skew == 0.0f) {
        X0 = round_px(X0);
        Y0 = round_px(Y0);
        X1 = round_px(X1);
        Y1 = round_px(Y1);
        if (X1 <= X0) {
            X1 = X0 + 1.0f;
        }
        if (Y1 <= Y0) {
            Y1 = Y0 + 1.0f;
        }
    }
    const float W = s.w != 0 ? static_cast<float>(s.w) : 1.0f;
    const float H = s.h != 0 ? static_cast<float>(s.h) : 1.0f;
    // A glass band computes the whole window's shape: its centre and half size, not the band's.
    float FX0 = X0, FY0 = Y0, FX1 = X1, FY1 = Y1;
    if (glassy && q.u1 > q.u0 && q.v1 > q.v0) {
        FX0 = s.ox + q.u0 * s.scale;
        FY0 = s.oy + q.v0 * s.scale;
        FX1 = s.ox + q.u1 * s.scale;
        FY1 = s.oy + q.v1 * s.scale;
    }
    const float hw = (FX1 - FX0) * 0.5f;
    const float hh = (FY1 - FY0) * 0.5f;
    const float cx = (FX0 + FX1) * 0.5f;
    const float cy = (FY0 + FY1) * 0.5f;
    float radius = q.p0 * s.scale;
    if (radius > hw) {
        radius = hw;
    }
    if (radius > hh) {
        radius = hh;
    }
    const float skew_px = (q.mode == QM_SOLID || q.mode == QM_HATCH) ? q.skew * s.scale : 0.0f;
    // The fraction stays below 1 so floor(mode) is the mode (overlay.hlsl selects by floor; review ★2).
    static_assert(GLASS_PACK_MAX * 32u + GLASS_PACK_MAX < 1024u, "the packed glass fraction stays below 1");
    float mode = static_cast<float>(q.mode);
    if (glassy) {
        const uint32_t sh = q.shine > GLASS_PACK_MAX ? GLASS_PACK_MAX : q.shine;
        const uint32_t bi = q.bias > GLASS_PACK_MAX ? GLASS_PACK_MAX : q.bias;
        mode += static_cast<float>(sh * 32u + bi) / 1024.0f;
    }
    const float rim_units = q.p2 > 0.0f ? q.p2 : 1.0f;
    vertex corner[4];
    for (uint32_t k = 0; k < 4; ++k) {
        const uint32_t ix = k & 1u;
        const uint32_t iy = k >> 1;
        vertex& v = corner[k];
        float px = ix != 0 ? X1 : X0;
        const float py = iy != 0 ? Y1 : Y0;
        if (iy == 0) {
            px += skew_px;   // the top edge shifted right (a slanted gauge part)
        }
        v.x = px / W * 2.0f - 1.0f;
        v.y = 1.0f - py / H * 2.0f;
        if (glassy) {
            v.u = px - cx;
            v.v = py - cy;
            v.p[0] = hw;
            v.p[1] = hh;
            v.p[2] = radius;
            v.p[3] = q.p1 * s.scale;   // the cut (chamfer) or the edge softness (a halo), pixels
            v.px = rim_units * s.scale;
        } else {
            v.u = ix != 0 ? q.u1 : q.u0;
            v.v = iy != 0 ? q.v1 : q.v0;
            v.p[0] = q.p0;
            v.p[1] = q.p1;
            v.p[2] = q.p2;
            v.p[3] = 0.0f;
            v.px = s.scale;
        }
        v.c0 = q.c[k];
        v.c1 = q.c1;
        v.mode = mode;
    }
    out[0] = corner[0];
    out[1] = corner[1];
    out[2] = corner[2];
    out[3] = corner[1];
    out[4] = corner[3];
    out[5] = corner[2];
    return 6;
}

}  // namespace ilc::overlay
