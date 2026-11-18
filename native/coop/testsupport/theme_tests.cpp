// Ivalice Legacy co-op module, host tests only: the HUD themes (ILCT_Th*): the built-ins, the player theme loader and its
// refusals, the theme list, the hand-over to the render thread, and font lookups (UTF-8, kerning, measure with extra).
#include <string.h>

#include "overlay/font.h"
#include "overlay/marks.h"
#include "overlay/theme.h"
#include "overlay/theme_builtin.h"
#include "overlay/theme_internal.h"

using namespace ilc;
using namespace ilc::overlay;

namespace {

theme::slot* g_test_slot = nullptr;
batch g_gauge_batch;   // namespace scope: no guarded local static (no thread runtime in the test DLL)

void put(char* dst, uint32_t cap, const char* src)
{
    uint32_t k = 0;
    while (src != nullptr && src[k] != '\0' && k + 1 < cap) {
        dst[k] = src[k];
        ++k;
    }
    if (cap != 0) {
        dst[k] = '\0';
    }
}

void hold(theme::slot* s)
{
    theme::test_activate(s);
    theme::free_slot(g_test_slot);
    g_test_slot = s;
}

}  // namespace

extern "C" {

// What the Python tests read of the active theme.
struct th_info {
    char id[33];
    char name[33];
    uint8_t win_shape, tab_kind, gauge_kind, focus_bar, status_style, status_count, shine, page_face;
    uint8_t marquee_on, trail_hatch, win_stops, faces;
    float row_em, help_em, outline, win_cut, win_radius, gauge_h, gauge_slant, marquee_speed, marquee_fade;
    uint32_t marquee_hold_ms, core, ink, hp_warn, hp_critical, drop_rare, seat0;
    uint32_t recipe_n[5];
    uint32_t icons, glyphs, kerns, atlas_w, atlas_h;
    uint8_t role_face[3];
    uint8_t reserved;
    float title_track, header_track;
};

uint32_t ILCT_ThInfoSize(void) { return sizeof(th_info); }

void ILCT_ThInfo(th_info* out)
{
    memset(out, 0, sizeof(*out));
    const theme::tokens& t = theme::active();
    const font::set& f = font::active();
    put(out->id, sizeof(out->id), t.id);
    put(out->name, sizeof(out->name), t.name);
    out->win_shape = t.win_shape;
    out->tab_kind = t.tab_kind;
    out->gauge_kind = t.gauge_kind;
    out->focus_bar = t.focus_bar;
    out->status_style = t.status_style;
    out->status_count = t.status_count;
    out->shine = t.shine;
    out->page_face = t.page_face;
    out->marquee_on = t.marquee_on;
    out->trail_hatch = t.trail_hatch;
    out->win_stops = t.win_stops;
    out->faces = static_cast<uint8_t>(f.faces);
    out->row_em = t.row_em;
    out->help_em = t.help_em;
    out->outline = t.outline;
    out->win_cut = t.win_cut;
    out->win_radius = t.win_radius;
    out->gauge_h = t.gauge_h;
    out->gauge_slant = t.gauge_slant;
    out->marquee_speed = t.marquee_speed;
    out->marquee_fade = t.marquee_fade;
    out->marquee_hold_ms = t.marquee_hold_ms;
    out->core = t.core;
    out->ink = t.ink;
    out->hp_warn = t.hp_warn;
    out->hp_critical = t.hp_critical;
    out->drop_rare = t.drop_rare;
    out->seat0 = t.seat[0];
    out->recipe_n[0] = t.glove_r.n;
    out->recipe_n[1] = t.chevron_r.n;
    out->recipe_n[2] = t.diamond_r.n;
    out->recipe_n[3] = t.dot_r.n;
    out->recipe_n[4] = t.status_r.n;
    out->icons = f.icons;
    out->glyphs = f.row_count;
    out->kerns = f.kern_count;
    out->atlas_w = f.atlas_w;
    out->atlas_h = f.atlas_h;
    out->role_face[0] = f.role_face[0];
    out->role_face[1] = f.role_face[1];
    out->role_face[2] = f.role_face[2];
    out->title_track = t.title_track;
    out->header_track = t.header_track;
}

// The built-in k made active (its own slot, as the module would hand it over). 1 = read.
int32_t ILCT_ThSelectBuiltin(uint32_t k, char* what, uint32_t cap)
{
    theme::report r{};
    theme::slot* s = theme::make_builtin(k, &r);
    put(what, cap, r.what);
    if (s == nullptr) {
        return 0;
    }
    hold(s);
    return 1;
}

// Back to the module's own active theme (the default after attach).
void ILCT_ThSelectDefault(void)
{
    theme::test_activate(nullptr);
    theme::free_slot(g_test_slot);
    g_test_slot = nullptr;
}

// A player theme folder read and, when it passes, made active. Returns the refusal code (0 = OK).
uint32_t ILCT_ThLoadPlayer(const wchar_t* dir, const char* id, char* what, uint32_t cap)
{
    theme::report r{};
    theme::slot* s = theme::make_player(dir, id, &r);
    put(what, cap, r.what);
    if (s == nullptr) {
        return r.code != 0 ? r.code : 255u;
    }
    hold(s);
    return 0;
}

// theme.json text read against the default theme's font (3 faces, the default icon order). The refusal code (0 = OK).
uint32_t ILCT_ThReadJson(const char* text, uint32_t len, const char* id, char* what, uint32_t cap)
{
    font::set f = *builtin::get(builtin::DEFAULT).font;
    theme::tokens t = theme::default_tokens();
    theme::report r{};
    const bool ok = theme::read_json(text, len, id, theme::DEFAULT_ICON_NAMES, theme::DEFAULT_ICON_COUNT, &f, &t, &r);
    put(what, cap, r.what);
    return ok ? 0u : (r.code != 0 ? r.code : 255u);
}

const char* ILCT_ThRefusalText(uint32_t code) { return theme::refusal_text(code); }

// theme.json text read as ILCT_ThReadJson does; then, per status icon k of the result, its status bit and the glyph of
// the status recipe's first {name} layer (review 9: an inherited recipe resolves by status name). Returns the status
// count, or 0xFFFFFFFF when the text is refused.
uint32_t ILCT_ThNameGlyphs(const char* text, uint32_t len, const char* id, uint8_t* bits, uint16_t* glyphs, uint32_t cap)
{
    font::set f = *builtin::get(builtin::DEFAULT).font;
    theme::tokens t = theme::default_tokens();
    theme::report r{};
    if (!theme::read_json(text, len, id, theme::DEFAULT_ICON_NAMES, theme::DEFAULT_ICON_COUNT, &f, &t, &r)) {
        return 0xFFFFFFFFu;
    }
    const theme::layer* named = nullptr;
    for (uint32_t k = 0; k < t.status_r.n && named == nullptr; ++k) {
        if (t.status_r.l[k].tmpl == theme::GT_NAME) {
            named = &t.status_r.l[k];
        }
    }
    for (uint32_t k = 0; k < t.status_count && k < cap; ++k) {
        bits[k] = t.status_bit[k];
        glyphs[k] = named != nullptr ? named->glyph[k] : font::ICON_NONE;
    }
    return t.status_count;
}

// The built-in crisp theme (square gauges, kind B) with gauge.ticks_every forced to `ticks` (the reader would refuse a
// tiny one: this is the drawing's own bound, review ★1), made active; one 200-unit gauge drawn. The quads it took.
uint32_t ILCT_ThGaugeTicks(float ticks)
{
    batch& b = g_gauge_batch;
    theme::report r{};
    theme::slot* s = theme::make_builtin(2, &r);
    if (s == nullptr) {
        return 0xFFFFFFFFu;
    }
    s->t.gauge_kind = theme::GAUGE_B;
    s->t.gauge_ticks = ticks;
    s->t.trail_hatch = 0;
    hold(s);
    b.clear();
    const uint32_t fill[2] = {0xFFFFFFFFu, 0xFFFFFFFFu};
    marks::gauge(b, 100.0f, 100.0f, 200.0f, 6.0f, 50, 50, 100, fill, 0xFF0000FFu);
    return b.count();
}

// The module: reset, a themes folder (scanned at once), a wanted id, one co-op beat, one render-frame hand-over.
void ILCT_ThReset(void) { theme::test_reset(); }
void ILCT_ThSetRoot(const wchar_t* dir) { theme::test_set_root(dir); }
void ILCT_ThConfigure(const char* id)
{
    static ilc_settings s;
    memset(&s, 0, sizeof(s));
    for (uint32_t k = 0; id != nullptr && id[k] != '\0' && k < ILC_THEME_ID_MAX; ++k) {
        s.hud_theme[k] = static_cast<uint8_t>(id[k]);
    }
    theme::configure_from_settings(s);
}
void ILCT_ThStep(uint64_t now) { theme::step(now); }
int32_t ILCT_ThTakePending(void) { return theme::take_pending() ? 1 : 0; }
uint32_t ILCT_ThAtlasGeneration(void) { return theme::atlas_generation(); }
int32_t ILCT_ThHasAtlas(void) { return theme::atlas_bytes() != nullptr ? 1 : 0; }
void ILCT_ThNoteAtlasFailed(void) { theme::note_atlas_failed(); }
void ILCT_ThList(ilc_themes_reply* out)
{
    memset(out, 0, sizeof(*out));
    theme::list(out);
}
void ILCT_ThStatus(theme::status_view* out) { *out = theme::status(); }
uint32_t ILCT_ThStatusSize(void) { return sizeof(theme::status_view); }

// ---- font ------------------------------------------------------------------------------------------------------------
int32_t ILCT_FtKern(uint32_t face, uint32_t left, uint32_t right) { return font::kern64(face, left, right); }
uint32_t ILCT_FtNextCode(const char* s, uint32_t n, uint32_t* at) { return font::next_code(s, n, at); }
uint32_t ILCT_FtBaseOf(uint32_t code) { return font::base_of(code); }
float ILCT_FtMeasureX(uint32_t face, float em, const char* s, uint32_t n, float extra)
{
    return font::measure(face, em, s, n, extra);
}
uint32_t ILCT_FtFitX(uint32_t face, float em, const char* s, uint32_t n, float max_w, float extra, int32_t* cut,
                     float* width)
{
    bool c = false;
    const uint32_t k = font::fit(face, em, s, n, max_w, &c, width, extra);
    *cut = c ? 1 : 0;
    return k;
}
int32_t ILCT_FtHasGlyph(uint32_t face, uint32_t code) { return font::find(face, code) != nullptr ? 1 : 0; }
int32_t ILCT_FtIconId(uint32_t name) { return font::active().icon_id[name < font::ICON_NAMED ? name : 0]; }
void ILCT_FtSetPixelScale(float s) { font::set_pixel_scale(s); }
int32_t ILCT_FtTablesValid(void) { return font::tables_valid(font::active()) ? 1 : 0; }

}  // extern "C"
