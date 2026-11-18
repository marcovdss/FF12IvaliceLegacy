// Ivalice Legacy co-op overlay: the signed-distance-field font of the active theme (Phase 8, E1).
//
// A theme's font is one R8 distance atlas with its glyph rows, kerning rows and line metrics (THEME-FORMAT.md 5/6):
// up to five text faces in the theme's `fonts` order (the built-in themes: 0 body, 1 title, 2 numerals) and the icon
// face last (index = text faces). Rows are sorted by (face, code point), kerning by (face, left, right): every lookup
// is a binary search. The atlas holds distances, not coverage, so one bake draws crisp text and the FF12 outline and
// glow at every size from 720p to 4K (the pixel shader turns distance into core, edge and glow).
//
// Faces are asked for by role: FACE_BODY (rows, help, log, names), FACE_TITLE (titles, headers, tags; small caps),
// FACE_NUMERALS (big HP figures; the body face when the theme has none) and FACE_ICONS. The set maps each role to its
// face. Icons are asked for by name (icon_name); the set maps each name to its index in the theme's icon list (a name
// the theme lacks maps to ICON_NONE: callers skip it).
//
// Text is UTF-8: measure, fit and the batch's glyph runs walk code points (`n` stays a byte count; a malformed byte is
// one '?'). A code point the face lacks is drawn as '?'. Kerning comes from the theme (both fonts' kern tables, baked);
// an accented Latin-1 letter (U+00C0..U+00FF) kerns like its base letter when it has no row of its own. Tracking: the
// caller's (text_style tracking in em and gap in units) plus the theme's small-size tracking (text.small: below
// `below_px` back-buffer pixels, `tracking_em` is added) on every glyph's advance; a space is at least 0.22 em.
//
// Units. Glyph metrics are atlas pixels; measure() and fit() take an em size in the caller's units (the HUD's 1920x1080
// authoring units) and answer in those units.
//
// Threads. The active set is a plain pointer: written by the render thread at a frame boundary (theme.h swap) and read
// by the composition on that same thread; host tests set it directly. Pure functions otherwise; no C runtime call.
#pragma once

#include <stdint.h>

namespace ilc::overlay::font {

enum face_id : uint8_t { FACE_BODY = 0, FACE_TITLE = 1, FACE_NUMERALS = 2, FACE_ICONS = 3, FACE_ROLES = 4 };

// The icons the engine draws, by name (THEME-FORMAT.md 4). The order is ours, not the theme's.
#define ILC_OVERLAY_ICON_NAMES(X)                                                                                     \
    X(GLOVE_BODY, "glove_body") X(GLOVE_CUFF, "glove_cuff") X(CHEVRON_UP, "chevron_up")                              \
    X(CHEVRON_DOWN, "chevron_down") X(CHEVRON_LEFT, "chevron_left") X(CHEVRON_RIGHT, "chevron_right")                \
    X(DIAMOND, "diamond") X(DOT, "dot") X(BADGE, "badge") X(BADGE_RING, "badge_ring")                                \
    X(PIC_POISON, "pic_poison") X(DET_POISON, "det_poison") X(PIC_SLOW, "pic_slow") X(DET_SLOW, "det_slow")          \
    X(PIC_PROTECT, "pic_protect") X(DET_PROTECT, "det_protect") X(PIC_HASTE, "pic_haste")                            \
    X(DET_HASTE, "det_haste") X(PIC_SLEEP, "pic_sleep") X(DET_SLEEP, "det_sleep") X(PIC_OIL, "pic_oil")              \
    X(DET_OIL, "det_oil")
#define ILC_ICON_ENUM(name, text) ICON_##name,
enum icon_name : uint8_t { ILC_OVERLAY_ICON_NAMES(ILC_ICON_ENUM) ICON_NAMED };
#undef ILC_ICON_ENUM
constexpr uint16_t ICON_NONE = 0xFFFF;
// The names a theme's icon set must have (the converter's rule).
constexpr uint32_t ICONS_REQUIRED = ICON_DOT + 1;
const char* icon_text(uint32_t name);   // "glove_body", ...; nullptr past the list

// Limits (THEME-FORMAT.md 7, the loader's refusal rules).
constexpr uint32_t ATLAS_BYTES_MAX = 1024u * 1024u;   // decoded atlas
constexpr uint32_t TEXT_FACES_MAX = 5;
constexpr uint32_t GLYPHS_MAX = 4096;
constexpr uint32_t KERNS_MAX = 20000;
constexpr uint32_t ICONS_MAX = 255;

struct glyph {
    uint16_t x, y, w, h;   // the cell in the atlas (w = 0: nothing to draw, e.g. the space)
    int16_t bx;            // cell left from the pen
    int16_t by;            // cell top from the baseline (icons: from the design box's top)
    int32_t advance64;     // pen advance, 1/64 atlas pixel
};
struct glyph_row {
    uint32_t code;         // code point (text faces), icon index (the icon face)
    uint8_t face;
    uint8_t reserved[3];
    glyph g;
};
struct kern_row {
    uint8_t face;
    uint8_t reserved[3];
    uint32_t left, right;  // code points
    int32_t k64;           // 1/64 atlas pixel
};
struct metric {
    int32_t ascent64, descent64;
};
static_assert(sizeof(glyph) == 16 && sizeof(glyph_row) == 24 && sizeof(kern_row) == 16 && sizeof(metric) == 8,
              "the row layouts (theme.atlas uses the same fields)");

// One theme's font. Tables and atlas are owned by whoever made the set (a built-in theme's constant tables, or a loaded
// theme's buffers); the set only points at them.
struct set {
    const glyph_row* rows;
    uint32_t row_count;
    const kern_row* kerns;
    uint32_t kern_count;
    const metric* metrics;   // faces + 1 entries (the icon face last)
    uint32_t faces;          // text faces (1..TEXT_FACES_MAX); the icon face is index `faces`
    uint32_t icons;
    uint32_t em, spread, atlas_w, atlas_h;
    const uint8_t* rle;      // the run-length coded atlas (built-ins; null for a loaded theme)
    uint32_t rle_size;
    const uint8_t* atlas;    // the decoded R8 atlas, atlas_w * atlas_h bytes (null until decoded)
    const char* digest;      // sha256 hex the converter recorded
    uint8_t role_face[3];    // FACE_BODY, FACE_TITLE, FACE_NUMERALS -> face index
    uint8_t reserved;
    uint16_t icon_id[ICON_NAMED];   // icon_name -> icon index (ICON_NONE: the theme has none)
    float small_below_px;    // text.small.below_px (0: off)
    float small_tracking_em; // text.small.tracking_em
};

// The set the composition uses (never null: the built-in default theme's tables until a theme is applied).
const set& active();
void set_active(const set* s);
// Back-buffer pixels per authoring unit (the small-size tracking rule); the render thread sets it every frame from the
// last back buffer, host tests leave it at 1.
void set_pixel_scale(float px_per_unit);
float pixel_scale();

// ---- lookups on the active set ----------------------------------------------------------------------------------------
// The glyph of `code` in face role `face` (for FACE_ICONS, code = icon index); nullptr when the face has none.
const glyph* find(uint32_t face, uint32_t code);
// The glyph drawn for `code`: '?' stands in for a character the face lacks (never null for a text face).
const glyph& resolve(uint32_t face, uint32_t code);
// The same lookup in a given set (the theme reader checks a set it has not applied).
const glyph* find_in(const set& s, uint32_t face, uint32_t code);
// An icon by name; nullptr when the theme has none.
const glyph* icon(uint32_t name);
float ascent(uint32_t face);    // atlas pixels above the baseline
float descent(uint32_t face);   // atlas pixels below it
int32_t kern64(uint32_t face, uint32_t left, uint32_t right);   // 1/64 atlas pixel (0: none; accents fall back)
uint32_t em();
uint32_t spread();
uint32_t atlas_w();
uint32_t atlas_h();

// The line box of `face` at em size `em`: ascent + descent, in the caller's units.
float line_height(uint32_t face, float em);
// Extra advance per glyph at em size `em` (units): the caller's `extra` (text_style: tracking * em + gap) plus the
// theme's small-size tracking.
float tracking(float em, float extra);
// The pen advance of `code`'s glyph in 1/64 atlas pixel: a space is never narrower than SPACE_MIN_EM (an outlined
// word needs room between its neighbours).
constexpr float SPACE_MIN_EM = 0.22f;
int32_t advance64(uint32_t face, uint32_t code, const glyph& g);
// Width of the first `n` bytes of UTF-8 `text` (stops at a NUL) at em size `em`, in the caller's units.
float measure(uint32_t face, float em, const char* text, uint32_t n, float extra = 0.0f);
// How many bytes of `text` fit in `max_w` (caller's units), cut at a code point boundary. If they do not all fit, the
// count leaves room for "..." and *cut is set; *width receives the drawn width (with the dots when cut).
uint32_t fit(uint32_t face, float em, const char* text, uint32_t n, float max_w, bool* cut, float* width,
             float extra = 0.0f);

// ---- UTF-8 ----------------------------------------------------------------------------------------------------------
// The code point at s[*at] (at most n bytes); *at moves past it. A malformed sequence yields '?' and moves one byte.
uint32_t next_code(const char* s, uint32_t n, uint32_t* at);
uint32_t length(const char* s, uint32_t n);   // bytes before the first NUL (at most n)
// The base letter of an accented Latin-1 letter (U+00C0..U+00FF), else 0.
uint32_t base_of(uint32_t code);

// ---- sets -----------------------------------------------------------------------------------------------------------
// Decode `s`'s run-length coded atlas into `out` (atlas_w * atlas_h bytes). False if `cap` is too small, the set has no
// coded atlas, or the coded data is damaged.
bool decode_atlas(const set& s, uint8_t* out, uint32_t cap);
// The rows are in (face, code) order, the kerning rows in (face, left, right) order, every cell inside the atlas, every
// icon index present once, the metrics sane: what the loader and the built-in check both require.
bool tables_valid(const set& s);
// The built-in default theme's set (tables only; its atlas pointer is null).
const set& builtin_default();

}  // namespace ilc::overlay::font
