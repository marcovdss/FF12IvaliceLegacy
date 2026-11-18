// Ivalice Legacy co-op overlay: the active theme's SDF font (see font.h). No C runtime calls.
#include "overlay/font.h"

#include "overlay/theme_builtin.h"

namespace ilc::overlay::font {
namespace {

const set* g_active = nullptr;
float g_pixel_scale = 1.0f;

#define ILC_ICON_TEXT(name, text) text,
constexpr const char* ICON_TEXT[ICON_NAMED] = {ILC_OVERLAY_ICON_NAMES(ILC_ICON_TEXT)};
#undef ILC_ICON_TEXT

// Latin-1 accented letters U+00C0..U+00FF -> base letter (0: none: x, division, eth, thorn, sharp s, AE/ae).
constexpr char BASE_LATIN1[64] = {
    'A', 'A', 'A', 'A', 'A', 'A', 0, 'C', 'E', 'E', 'E', 'E', 'I', 'I', 'I', 'I',   // U+00C0..
    0, 'N', 'O', 'O', 'O', 'O', 'O', 0, 'O', 'U', 'U', 'U', 'U', 'Y', 0, 0,         // U+00D0..
    'a', 'a', 'a', 'a', 'a', 'a', 0, 'c', 'e', 'e', 'e', 'e', 'i', 'i', 'i', 'i',   // U+00E0..
    0, 'n', 'o', 'o', 'o', 'o', 'o', 0, 'o', 'u', 'u', 'u', 'u', 'y', 0, 'y',       // U+00F0..
};

uint32_t face_index(const set& s, uint32_t role)
{
    if (role < FACE_ICONS) {
        const uint32_t f = s.role_face[role];
        return f < s.faces ? f : 0u;
    }
    return role == FACE_ICONS ? s.faces : 0xFFFFFFFFu;
}

bool row_less(const glyph_row& r, uint32_t face, uint32_t code)
{
    return r.face < face || (r.face == face && r.code < code);
}

const glyph* lookup_index(const set& s, uint32_t face, uint32_t code)
{
    uint32_t lo = 0;
    uint32_t hi = s.row_count;
    while (lo < hi) {
        const uint32_t mid = lo + (hi - lo) / 2;
        if (row_less(s.rows[mid], face, code)) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    if (lo < s.row_count && s.rows[lo].face == face && s.rows[lo].code == code) {
        return &s.rows[lo].g;
    }
    return nullptr;
}

bool kern_less(const kern_row& k, uint32_t face, uint32_t left, uint32_t right)
{
    if (k.face != face) {
        return k.face < face;
    }
    if (k.left != left) {
        return k.left < left;
    }
    return k.right < right;
}

const kern_row* kern_lookup(const set& s, uint32_t face, uint32_t left, uint32_t right)
{
    uint32_t lo = 0;
    uint32_t hi = s.kern_count;
    while (lo < hi) {
        const uint32_t mid = lo + (hi - lo) / 2;
        if (kern_less(s.kerns[mid], face, left, right)) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    if (lo < s.kern_count && s.kerns[lo].face == face && s.kerns[lo].left == left && s.kerns[lo].right == right) {
        return &s.kerns[lo];
    }
    return nullptr;
}

float scale_of(const set& s, float em_size)
{
    return em_size / static_cast<float>(s.em != 0 ? s.em : 40u);
}

// One pass over the code points: the pen advance (units) of the first `n` bytes.
float run_width(const set& s, uint32_t face, float em_size, const char* text, uint32_t n, float extra)
{
    const float k = scale_of(s, em_size);
    const float track = tracking(em_size, extra);
    int64_t pen64 = 0;
    float added = 0.0f;
    uint32_t at = 0;
    uint32_t prev = 0;
    bool first = true;
    while (at < n) {
        const uint32_t c = next_code(text, n, &at);
        if (!first) {
            pen64 += kern64(face, prev, c);
        }
        pen64 += advance64(face, c, resolve(face, c));
        added += track;
        prev = c;
        first = false;
    }
    return static_cast<float>(pen64) / 64.0f * k + added;
}

}  // namespace

const char* icon_text(uint32_t name)
{
    return name < ICON_NAMED ? ICON_TEXT[name] : nullptr;
}

const set& active()
{
    return g_active != nullptr ? *g_active : builtin_default();
}

void set_active(const set* s)
{
    g_active = s;
}

void set_pixel_scale(float px_per_unit)
{
    g_pixel_scale = px_per_unit > 0.01f ? px_per_unit : 1.0f;
}

float pixel_scale()
{
    return g_pixel_scale;
}

const set& builtin_default()
{
    return *builtin::get(0).font;
}

const glyph* find_in(const set& s, uint32_t face, uint32_t code)
{
    const uint32_t f = face_index(s, face);
    if (f > s.faces) {
        return nullptr;
    }
    return lookup_index(s, f, code);
}

const glyph* find(uint32_t face, uint32_t code)
{
    return find_in(active(), face, code);
}

const glyph& resolve(uint32_t face, uint32_t code)
{
    const glyph* g = find(face, code);
    if (g == nullptr) {
        g = find(face < FACE_ICONS ? face : static_cast<uint32_t>(FACE_BODY), '?');
    }
    if (g == nullptr) {
        g = find(FACE_BODY, '?');
    }
    static const glyph blank = {0, 0, 0, 0, 0, 0, 0};
    return g != nullptr ? *g : blank;
}

const glyph* icon(uint32_t name)
{
    const set& s = active();
    if (name >= ICON_NAMED || s.icon_id[name] == ICON_NONE) {
        return nullptr;
    }
    return lookup_index(s, s.faces, s.icon_id[name]);
}

float ascent(uint32_t face)
{
    const set& s = active();
    const uint32_t f = face_index(s, face);
    return f <= s.faces ? static_cast<float>(s.metrics[f].ascent64) / 64.0f : 0.0f;
}

float descent(uint32_t face)
{
    const set& s = active();
    const uint32_t f = face_index(s, face);
    return f <= s.faces ? static_cast<float>(s.metrics[f].descent64) / 64.0f : 0.0f;
}

int32_t kern64(uint32_t face, uint32_t left, uint32_t right)
{
    const set& s = active();
    const uint32_t f = face_index(s, face);
    if (f >= s.faces || s.kern_count == 0) {
        return 0;
    }
    const kern_row* k = kern_lookup(s, f, left, right);
    if (k == nullptr) {
        // An accented letter kerns like its base letter (the theme bakes no rows of its own for them).
        const uint32_t bl = base_of(left);
        const uint32_t br = base_of(right);
        if (bl != 0 || br != 0) {
            k = kern_lookup(s, f, bl != 0 ? bl : left, br != 0 ? br : right);
        }
    }
    return k != nullptr ? k->k64 : 0;
}

uint32_t em() { return active().em; }
uint32_t spread() { return active().spread; }
uint32_t atlas_w() { return active().atlas_w; }
uint32_t atlas_h() { return active().atlas_h; }

float line_height(uint32_t face, float em_size)
{
    return (ascent(face) + descent(face)) * scale_of(active(), em_size);
}

float tracking(float em_size, float extra)
{
    const set& s = active();
    float t = extra;
    if (s.small_below_px > 0.0f && em_size * g_pixel_scale < s.small_below_px) {
        t += s.small_tracking_em * em_size;
    }
    return t;
}

int32_t advance64(uint32_t face, uint32_t code, const glyph& g)
{
    if (face != FACE_ICONS && (code == 0x20 || code == 0xA0)) {
        const int32_t floor64 = static_cast<int32_t>(SPACE_MIN_EM * static_cast<float>(active().em) * 64.0f + 0.5f);
        return g.advance64 > floor64 ? g.advance64 : floor64;
    }
    return g.advance64;
}

float measure(uint32_t face, float em_size, const char* text, uint32_t n, float extra)
{
    if (text == nullptr) {
        return 0.0f;
    }
    n = length(text, n);
    return run_width(active(), face, em_size, text, n, extra);
}

uint32_t fit(uint32_t face, float em_size, const char* text, uint32_t n, float max_w, bool* cut, float* width,
             float extra)
{
    if (cut != nullptr) {
        *cut = false;
    }
    if (text == nullptr) {
        if (width != nullptr) {
            *width = 0.0f;
        }
        return 0;
    }
    n = length(text, n);
    const float full = measure(face, em_size, text, n, extra);
    if (full <= max_w) {
        if (width != nullptr) {
            *width = full;
        }
        return n;
    }
    const float dots = measure(face, em_size, "...", 3, extra);
    // One walk over the code points: the prefix ending at each boundary is as measure() would give it (the kerning to
    // the next character is not part of it). Keep the longest prefix that leaves room for the dots, counting the
    // kerning between its last letter and the first '.' as batch.cpp's glyph_run draws it (the prefix + "..." is then
    // exactly measure(prefix + "...")).
    const float k = scale_of(active(), em_size);
    const float track = tracking(em_size, extra);
    uint32_t keep = 0;
    float kept_w = 0.0f;
    uint32_t at = 0;
    int64_t pen64 = 0;
    uint32_t prev = 0;
    uint32_t count = 0;
    while (at < n) {
        const uint32_t c = next_code(text, n, &at);
        if (count != 0) {
            pen64 += kern64(face, prev, c);
        }
        pen64 += advance64(face, c, resolve(face, c));
        ++count;
        prev = c;
        const float w = static_cast<float>(pen64 + kern64(face, c, '.')) / 64.0f * k + track * static_cast<float>(count);
        if (w + dots > max_w) {
            break;
        }
        keep = at;
        kept_w = w;
    }
    if (cut != nullptr) {
        *cut = true;
    }
    if (width != nullptr) {
        *width = kept_w + dots;
    }
    return keep;
}

// ---- UTF-8 ---------------------------------------------------------------------------------------------------------
uint32_t next_code(const char* s, uint32_t n, uint32_t* at)
{
    const uint32_t i = *at;
    const uint32_t b0 = static_cast<uint8_t>(s[i]);
    if (b0 < 0x80) {
        *at = i + 1;
        return b0;
    }
    uint32_t need = 0;
    uint32_t code = 0;
    uint32_t min = 0;
    if ((b0 & 0xE0) == 0xC0) {
        need = 1;
        code = b0 & 0x1F;
        min = 0x80;
    } else if ((b0 & 0xF0) == 0xE0) {
        need = 2;
        code = b0 & 0x0F;
        min = 0x800;
    } else if ((b0 & 0xF8) == 0xF0) {
        need = 3;
        code = b0 & 0x07;
        min = 0x10000;
    } else {
        *at = i + 1;
        return '?';
    }
    if (i + need >= n) {   // the sequence runs past the text
        *at = i + 1;
        return '?';
    }
    for (uint32_t k = 1; k <= need; ++k) {
        const uint32_t b = static_cast<uint8_t>(s[i + k]);
        if ((b & 0xC0) != 0x80) {
            *at = i + 1;
            return '?';
        }
        code = code << 6 | (b & 0x3F);
    }
    if (code < min || code > 0x10FFFF || (code >= 0xD800 && code <= 0xDFFF)) {
        *at = i + 1;
        return '?';
    }
    *at = i + 1 + need;
    return code;
}

uint32_t length(const char* s, uint32_t n)
{
    uint32_t k = 0;
    while (s != nullptr && k < n && s[k] != '\0') {
        ++k;
    }
    return k;
}

uint32_t base_of(uint32_t code)
{
    if (code < 0xC0 || code > 0xFF) {
        return 0;
    }
    return static_cast<uint8_t>(BASE_LATIN1[code - 0xC0]);
}

// ---- sets -------------------------------------------------------------------------------------------------------------
bool decode_atlas(const set& s, uint8_t* out, uint32_t cap)
{
    const uint64_t size = static_cast<uint64_t>(s.atlas_w) * s.atlas_h;
    if (out == nullptr || s.rle == nullptr || size == 0 || size > ATLAS_BYTES_MAX || cap < size) {
        return false;
    }
    uint32_t o = 0;
    for (uint32_t i = 0; i < s.rle_size; ++i) {
        const uint8_t b = s.rle[i];
        if (b != 0) {
            if (o >= size) {
                return false;
            }
            out[o++] = b;
            continue;
        }
        if (i + 1 >= s.rle_size) {
            return false;
        }
        const uint32_t run = s.rle[++i];
        if (run == 0 || o + run > size) {
            return false;
        }
        for (uint32_t k = 0; k < run; ++k) {
            out[o++] = 0;
        }
    }
    return o == size;
}

bool tables_valid(const set& s)
{
    if (s.faces == 0 || s.faces > TEXT_FACES_MAX || s.icons > ICONS_MAX || s.row_count > GLYPHS_MAX ||
        s.kern_count > KERNS_MAX || s.em < 16 || s.em > 128 || s.spread == 0 || s.spread > 16 || s.atlas_w == 0 ||
        s.atlas_h == 0 || static_cast<uint64_t>(s.atlas_w) * s.atlas_h > ATLAS_BYTES_MAX || s.rows == nullptr ||
        s.metrics == nullptr || (s.kern_count != 0 && s.kerns == nullptr)) {
        return false;
    }
    const int32_t lim = static_cast<int32_t>(s.em) * 64 * 4;
    for (uint32_t f = 0; f <= s.faces; ++f) {
        if (s.metrics[f].ascent64 < 0 || s.metrics[f].ascent64 > lim || s.metrics[f].descent64 < 0 ||
            s.metrics[f].descent64 > lim) {
            return false;
        }
    }
    if (s.metrics[s.faces].ascent64 != static_cast<int32_t>(s.em) * 64 || s.metrics[s.faces].descent64 != 0) {
        return false;   // the icon face's metric is the design box
    }
    uint32_t icons_seen = 0;
    const int32_t span = static_cast<int32_t>(s.em) * 4;
    for (uint32_t k = 0; k < s.row_count; ++k) {
        const glyph_row& r = s.rows[k];
        if (r.face > s.faces || (k > 0 && !row_less(s.rows[k - 1], r.face, r.code))) {
            return false;   // out of order, or a face past the icon face
        }
        const glyph& g = r.g;
        if ((g.w == 0) != (g.h == 0) || static_cast<uint32_t>(g.x) + g.w > s.atlas_w ||
            static_cast<uint32_t>(g.y) + g.h > s.atlas_h || g.bx < -span || g.bx > span || g.by < -span ||
            g.by > span || g.advance64 < -span * 64 || g.advance64 > span * 64) {
            return false;
        }
        if (r.face == s.faces) {
            if (r.code != icons_seen) {
                return false;   // icon indices 0..icons-1, each once, in order
            }
            ++icons_seen;
        } else if (r.code > 0x10FFFF) {
            return false;
        }
    }
    if (icons_seen != s.icons) {
        return false;
    }
    for (uint32_t k = 0; k < s.kern_count; ++k) {
        const kern_row& r = s.kerns[k];
        if (r.face >= s.faces || r.k64 < -span * 64 || r.k64 > span * 64 ||
            (k > 0 && !kern_less(s.kerns[k - 1], r.face, r.left, r.right))) {
            return false;
        }
    }
    for (uint32_t role = 0; role < 3; ++role) {
        if (s.role_face[role] >= s.faces) {
            return false;
        }
    }
    for (uint32_t i = 0; i < ICON_NAMED; ++i) {
        if (s.icon_id[i] != ICON_NONE && s.icon_id[i] >= s.icons) {
            return false;
        }
    }
    return true;
}

}  // namespace ilc::overlay::font
