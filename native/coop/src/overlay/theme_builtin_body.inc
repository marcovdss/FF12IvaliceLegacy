// Ivalice Legacy co-op overlay: one built-in theme's tables from its font.inc and theme_json.inc (included by the
// theme_builtin_<id>.cpp files after both; ILC_BUILTIN_NAME names the theme_data it defines). Not a header.
//
// The font.inc rows become font::glyph_row / kern_row / metric constants in the converter's order; the checks below run
// at compile time: counts as the header says, rows in (face, code) order, kerning in (face, left, right) order, the atlas
// within the 1 MiB decode buffer, the icon face's metric the design box. The engine's icon names are looked up in the
// font.inc's ILC_OVERLAY_ICON_<NAME> ids (a name the set lacks: ICON_NONE).

#ifndef ILC_BUILTIN_NAME
#error "define ILC_BUILTIN_NAME before including theme_builtin_body.inc"
#endif

namespace ilc::overlay::builtin {
namespace {

#define ILC_FONT_ROW(f, c, x, y, w, h, bx, by, adv)                                                                   \
    font::glyph_row{static_cast<uint32_t>(c), static_cast<uint8_t>(f), {0, 0, 0},                                     \
                    font::glyph{static_cast<uint16_t>(x), static_cast<uint16_t>(y), static_cast<uint16_t>(w),          \
                                static_cast<uint16_t>(h), static_cast<int16_t>(bx), static_cast<int16_t>(by),          \
                                static_cast<int32_t>(adv)}},
constexpr font::glyph_row ROWS[] = {ILC_OVERLAY_FONT_GLYPH_ROWS(ILC_FONT_ROW)};
#undef ILC_FONT_ROW

#define ILC_FONT_KERN(f, a, b, k)                                                                                     \
    font::kern_row{static_cast<uint8_t>(f), {0, 0, 0}, static_cast<uint32_t>(a), static_cast<uint32_t>(b),             \
                   static_cast<int32_t>(k)},
constexpr font::kern_row KERNS[] = {ILC_OVERLAY_FONT_KERN_ROWS(ILC_FONT_KERN) font::kern_row{0xFF, {0, 0, 0}, 0, 0, 0}};
#undef ILC_FONT_KERN
constexpr uint32_t KERN_COUNT = sizeof(KERNS) / sizeof(KERNS[0]) - 1;   // the last row is a sentinel, never searched

#define ILC_FONT_METRIC(f, a, d) font::metric{static_cast<int32_t>(a), static_cast<int32_t>(d)},
constexpr font::metric METRICS[] = {ILC_OVERLAY_FONT_METRICS(ILC_FONT_METRIC)};
#undef ILC_FONT_METRIC

constexpr uint8_t RLE[] = {ILC_OVERLAY_FONT_ATLAS_BYTES};
constexpr uint8_t JSON[] = {ILC_THEME_JSON_BYTES};

constexpr bool rows_sorted()
{
    for (uint32_t k = 1; k < sizeof(ROWS) / sizeof(ROWS[0]); ++k) {
        const font::glyph_row& a = ROWS[k - 1];
        const font::glyph_row& b = ROWS[k];
        if (!(a.face < b.face || (a.face == b.face && a.code < b.code))) {
            return false;
        }
    }
    return true;
}
constexpr bool kerns_sorted()
{
    for (uint32_t k = 1; k < KERN_COUNT; ++k) {
        const font::kern_row& a = KERNS[k - 1];
        const font::kern_row& b = KERNS[k];
        const bool less = a.face != b.face ? a.face < b.face : (a.left != b.left ? a.left < b.left : a.right < b.right);
        if (!less) {
            return false;
        }
    }
    return true;
}
static_assert(sizeof(ROWS) / sizeof(ROWS[0]) == ILC_OVERLAY_FONT_GLYPHS, "font.inc glyph count");
static_assert(KERN_COUNT == ILC_OVERLAY_FONT_KERNS, "font.inc kerning count");
static_assert(sizeof(METRICS) / sizeof(METRICS[0]) == ILC_OVERLAY_FONT_FACES + 1, "a metric per face and the icons");
static_assert(sizeof(RLE) == ILC_OVERLAY_FONT_RLE_SIZE, "font.inc atlas size");
static_assert(sizeof(JSON) == ILC_THEME_JSON_SIZE, "theme_json.inc size");
static_assert(ILC_OVERLAY_FONT_ATLAS_W * ILC_OVERLAY_FONT_ATLAS_H <= font::ATLAS_BYTES_MAX, "the atlas fits 1 MiB");
static_assert(ILC_OVERLAY_FONT_FACES >= 2 && ILC_OVERLAY_FONT_FACES <= font::TEXT_FACES_MAX, "text faces");
static_assert(rows_sorted(), "font.inc rows: (face, code) order");
static_assert(kerns_sorted(), "font.inc kerning: (face, left, right) order");
static_assert(METRICS[ILC_OVERLAY_FONT_FACES].ascent64 == ILC_OVERLAY_FONT_EM * 64 &&
                  METRICS[ILC_OVERLAY_FONT_FACES].descent64 == 0,
              "the icon face's metric is the design box");

#define ILC_ICON_ID_OF(name, text) ILC_ICON_ID_##name,
#ifdef ILC_OVERLAY_ICON_GLOVE_BODY
#define ILC_ICON_ID_GLOVE_BODY ILC_OVERLAY_ICON_GLOVE_BODY
#else
#define ILC_ICON_ID_GLOVE_BODY font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_GLOVE_CUFF
#define ILC_ICON_ID_GLOVE_CUFF ILC_OVERLAY_ICON_GLOVE_CUFF
#else
#define ILC_ICON_ID_GLOVE_CUFF font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_CHEVRON_UP
#define ILC_ICON_ID_CHEVRON_UP ILC_OVERLAY_ICON_CHEVRON_UP
#else
#define ILC_ICON_ID_CHEVRON_UP font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_CHEVRON_DOWN
#define ILC_ICON_ID_CHEVRON_DOWN ILC_OVERLAY_ICON_CHEVRON_DOWN
#else
#define ILC_ICON_ID_CHEVRON_DOWN font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_CHEVRON_LEFT
#define ILC_ICON_ID_CHEVRON_LEFT ILC_OVERLAY_ICON_CHEVRON_LEFT
#else
#define ILC_ICON_ID_CHEVRON_LEFT font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_CHEVRON_RIGHT
#define ILC_ICON_ID_CHEVRON_RIGHT ILC_OVERLAY_ICON_CHEVRON_RIGHT
#else
#define ILC_ICON_ID_CHEVRON_RIGHT font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_DIAMOND
#define ILC_ICON_ID_DIAMOND ILC_OVERLAY_ICON_DIAMOND
#else
#define ILC_ICON_ID_DIAMOND font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_DOT
#define ILC_ICON_ID_DOT ILC_OVERLAY_ICON_DOT
#else
#define ILC_ICON_ID_DOT font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_BADGE
#define ILC_ICON_ID_BADGE ILC_OVERLAY_ICON_BADGE
#else
#define ILC_ICON_ID_BADGE font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_BADGE_RING
#define ILC_ICON_ID_BADGE_RING ILC_OVERLAY_ICON_BADGE_RING
#else
#define ILC_ICON_ID_BADGE_RING font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_PIC_POISON
#define ILC_ICON_ID_PIC_POISON ILC_OVERLAY_ICON_PIC_POISON
#else
#define ILC_ICON_ID_PIC_POISON font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_DET_POISON
#define ILC_ICON_ID_DET_POISON ILC_OVERLAY_ICON_DET_POISON
#else
#define ILC_ICON_ID_DET_POISON font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_PIC_SLOW
#define ILC_ICON_ID_PIC_SLOW ILC_OVERLAY_ICON_PIC_SLOW
#else
#define ILC_ICON_ID_PIC_SLOW font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_DET_SLOW
#define ILC_ICON_ID_DET_SLOW ILC_OVERLAY_ICON_DET_SLOW
#else
#define ILC_ICON_ID_DET_SLOW font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_PIC_PROTECT
#define ILC_ICON_ID_PIC_PROTECT ILC_OVERLAY_ICON_PIC_PROTECT
#else
#define ILC_ICON_ID_PIC_PROTECT font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_DET_PROTECT
#define ILC_ICON_ID_DET_PROTECT ILC_OVERLAY_ICON_DET_PROTECT
#else
#define ILC_ICON_ID_DET_PROTECT font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_PIC_HASTE
#define ILC_ICON_ID_PIC_HASTE ILC_OVERLAY_ICON_PIC_HASTE
#else
#define ILC_ICON_ID_PIC_HASTE font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_DET_HASTE
#define ILC_ICON_ID_DET_HASTE ILC_OVERLAY_ICON_DET_HASTE
#else
#define ILC_ICON_ID_DET_HASTE font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_PIC_SLEEP
#define ILC_ICON_ID_PIC_SLEEP ILC_OVERLAY_ICON_PIC_SLEEP
#else
#define ILC_ICON_ID_PIC_SLEEP font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_DET_SLEEP
#define ILC_ICON_ID_DET_SLEEP ILC_OVERLAY_ICON_DET_SLEEP
#else
#define ILC_ICON_ID_DET_SLEEP font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_PIC_OIL
#define ILC_ICON_ID_PIC_OIL ILC_OVERLAY_ICON_PIC_OIL
#else
#define ILC_ICON_ID_PIC_OIL font::ICON_NONE
#endif
#ifdef ILC_OVERLAY_ICON_DET_OIL
#define ILC_ICON_ID_DET_OIL ILC_OVERLAY_ICON_DET_OIL
#else
#define ILC_ICON_ID_DET_OIL font::ICON_NONE
#endif

constexpr font::set SET = {
    ROWS,
    ILC_OVERLAY_FONT_GLYPHS,
    KERNS,
    KERN_COUNT,
    METRICS,
    ILC_OVERLAY_FONT_FACES,
    ILC_OVERLAY_FONT_ICONS,
    ILC_OVERLAY_FONT_EM,
    ILC_OVERLAY_FONT_SPREAD,
    ILC_OVERLAY_FONT_ATLAS_W,
    ILC_OVERLAY_FONT_ATLAS_H,
    RLE,
    ILC_OVERLAY_FONT_RLE_SIZE,
    nullptr,
    ILC_OVERLAY_FONT_DIGEST,
    {0, 1, static_cast<uint8_t>(ILC_OVERLAY_FONT_FACES > 2 ? 2 : 0)},   // body, title, numerals (theme.json decides)
    0,
    {ILC_OVERLAY_ICON_NAMES(ILC_ICON_ID_OF)},
    0.0f,
    0.0f,
};
#undef ILC_ICON_ID_OF

}  // namespace

extern const theme_data ILC_BUILTIN_NAME;
const theme_data ILC_BUILTIN_NAME = {ILC_BUILTIN_ID, &SET, JSON, ILC_THEME_JSON_SIZE};

}  // namespace ilc::overlay::builtin
