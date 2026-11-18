// Ivalice Legacy co-op overlay: reading a theme (theme.json, atlas/theme.atlas, icons/icons.json) into a slot, with the
// loader's rules (theme.h). Runs on the boot and co-op threads (and in host tests); never on the render thread.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "input/sha256.h"
#include "overlay/theme_builtin.h"
#include "overlay/theme_internal.h"
#include "overlay/theme_json.h"

namespace ilc::overlay::theme {

const char* const DEFAULT_ICON_NAMES[] = {
    "glove_body", "glove_cuff", "chevron_up", "chevron_down", "chevron_left", "chevron_right", "diamond", "dot",
    "badge", "badge_ring", "pic_poison", "det_poison", "pic_slow", "det_slow", "pic_protect", "det_protect",
    "pic_haste", "det_haste", "pic_sleep", "det_sleep", "pic_oil", "det_oil",
};
const uint32_t DEFAULT_ICON_COUNT = sizeof(DEFAULT_ICON_NAMES) / sizeof(DEFAULT_ICON_NAMES[0]);

namespace {

// sRGB -> linear, per 8-bit channel (the converter's lum(): WCAG relative luminance).
constexpr float SRGB_LINEAR[256] = {
    0.0f, 0.000303526984f, 0.000607053967f, 0.000910580951f, 0.00121410793f, 0.00151763492f, 0.0018211619f,
    0.00212468888f, 0.00242821587f, 0.00273174285f, 0.00303526984f, 0.00334653576f, 0.00367650732f, 0.00402471702f,
    0.00439144204f, 0.00477695348f, 0.0051815167f, 0.00560539162f, 0.00604883302f, 0.00651209079f, 0.00699541019f,
    0.00749903204f, 0.00802319299f, 0.00856812562f, 0.0091340587f, 0.00972121732f, 0.010329823f, 0.010960094f,
    0.0116122452f, 0.0122864884f, 0.0129830323f, 0.013702083f, 0.0144438436f, 0.0152085144f, 0.0159962934f,
    0.0168073758f, 0.0176419545f, 0.0185002201f, 0.019382361f, 0.0202885631f, 0.0212190104f, 0.0221738848f,
    0.0231533662f, 0.0241576324f, 0.0251868596f, 0.0262412219f, 0.0273208916f, 0.0284260395f, 0.0295568344f,
    0.0307134437f, 0.0318960331f, 0.0331047666f, 0.0343398068f, 0.0356013149f, 0.0368894504f, 0.0382043716f,
    0.0395462353f, 0.0409151969f, 0.0423114106f, 0.0437350293f, 0.0451862044f, 0.0466650863f, 0.0481718242f,
    0.049706566f, 0.0512694584f, 0.052860647f, 0.0544802764f, 0.05612849f, 0.0578054302f, 0.0595112382f, 0.0612460542f,
    0.0630100177f, 0.0648032667f, 0.0666259386f, 0.0684781698f, 0.0703600957f, 0.0722718507f, 0.0742135684f,
    0.0761853815f, 0.0781874218f, 0.0802198203f, 0.0822827071f, 0.0843762115f, 0.086500462f, 0.0886555863f,
    0.0908417112f, 0.0930589628f, 0.0953074666f, 0.0975873471f, 0.0998987282f, 0.102241733f, 0.104616484f, 0.107023103f,
    0.109461711f, 0.111932428f, 0.114435374f, 0.116970668f, 0.119538428f, 0.122138772f, 0.124771818f, 0.12743768f,
    0.130136477f, 0.132868322f, 0.13563333f, 0.138431615f, 0.141263291f, 0.144128471f, 0.147027266f, 0.14995979f,
    0.152926152f, 0.155926464f, 0.158960835f, 0.162029376f, 0.165132195f, 0.1682694f, 0.171441101f, 0.174647404f,
    0.177888416f, 0.181164244f, 0.184474995f, 0.187820772f, 0.191201683f, 0.19461783f, 0.19806932f, 0.201556254f,
    0.205078736f, 0.20863687f, 0.212230757f, 0.2158605f, 0.2195262f, 0.223227957f, 0.226965874f, 0.230740049f,
    0.234550582f, 0.238397574f, 0.242281122f, 0.246201327f, 0.250158285f, 0.254152094f, 0.258182853f, 0.262250658f,
    0.266355605f, 0.270497791f, 0.274677312f, 0.278894263f, 0.28314874f, 0.287440838f, 0.29177065f, 0.296138271f,
    0.300543794f, 0.304987314f, 0.309468923f, 0.313988713f, 0.318546778f, 0.323143209f, 0.327778098f, 0.332451536f,
    0.337163615f, 0.341914425f, 0.346704056f, 0.3515326f, 0.356400144f, 0.36130678f, 0.366252596f, 0.37123768f,
    0.376262123f, 0.381326011f, 0.386429434f, 0.391572478f, 0.396755231f, 0.40197778f, 0.407240212f, 0.412542613f,
    0.417885071f, 0.42326767f, 0.428690497f, 0.434153636f, 0.439657174f, 0.445201195f, 0.450785783f, 0.456411023f,
    0.462077f, 0.467783796f, 0.473531496f, 0.479320183f, 0.48514994f, 0.49102085f, 0.496932995f, 0.502886458f,
    0.508881321f, 0.514917665f, 0.520995573f, 0.527115126f, 0.533276404f, 0.539479489f, 0.545724461f, 0.552011402f,
    0.55834039f, 0.564711506f, 0.571124829f, 0.57758044f, 0.584078418f, 0.590618841f, 0.597201788f, 0.603827339f,
    0.610495571f, 0.617206562f, 0.623960392f, 0.630757136f, 0.637596874f, 0.644479682f, 0.651405637f, 0.658374817f,
    0.665387298f, 0.672443157f, 0.67954247f, 0.686685312f, 0.693871761f, 0.701101892f, 0.70837578f, 0.715693501f,
    0.723055129f, 0.73046074f, 0.737910409f, 0.74540421f, 0.752942217f, 0.760524505f, 0.768151147f, 0.775822218f,
    0.783537792f, 0.79129794f, 0.799102738f, 0.806952258f, 0.814846572f, 0.822785754f, 0.830769877f, 0.838799012f,
    0.846873232f, 0.854992608f, 0.863157213f, 0.871367119f, 0.879622397f, 0.887923118f, 0.896269353f, 0.904661174f,
    0.913098652f, 0.921581856f, 0.930110858f, 0.938685728f, 0.947306537f, 0.955973353f, 0.964686248f, 0.97344529f,
    0.98225055f, 0.991102097f, 1.0f,
};

// The 32 statuses in status-bit order, lower case (status_icons.names are matched against these, any case).
constexpr const char* STATUS_NAMES[32] = {
    "ko", "stone", "petrify", "stop", "sleep", "confuse", "doom", "blind", "poison", "silence", "sap", "oil", "reverse",
    "disable", "immobilize", "slow", "disease", "lure", "protect", "shell", "haste", "bravery", "faith", "reflect",
    "invisible", "regen", "float", "berserk", "bubble", "hp critical", "libra", "x-zone",
};

float luminance(uint32_t c)
{
    return 0.2126f * SRGB_LINEAR[c & 0xFFu] + 0.7152f * SRGB_LINEAR[(c >> 8) & 0xFFu] +
           0.0722f * SRGB_LINEAR[(c >> 16) & 0xFFu];
}

float contrast(uint32_t a, uint32_t b)
{
    const float la = luminance(a);
    const float lb = luminance(b);
    const float hi = la > lb ? la : lb;
    const float lo = la > lb ? lb : la;
    return (hi + 0.05f) / (lo + 0.05f);
}

// ---- reports ------------------------------------------------------------------------------------------------------------
struct what_line {
    char* s;
    uint32_t n;
    uint32_t cap;
    what_line& t(const char* text)
    {
        while (text != nullptr && *text != '\0' && n + 1 < cap) {
            s[n++] = *text++;
        }
        s[n] = '\0';
        return *this;
    }
    what_line& f(double v)   // one decimal place
    {
        if (v < 0) {
            t("-");
            v = -v;
        }
        const uint64_t tenths = static_cast<uint64_t>(v * 10.0 + 0.5);
        char d[24];
        uint32_t k = 0;
        uint64_t w = tenths / 10;
        do {
            d[k++] = static_cast<char>('0' + w % 10);
            w /= 10;
        } while (w != 0 && k < 20);
        while (k > 0 && n + 1 < cap) {
            s[n++] = d[--k];
        }
        s[n] = '\0';
        if (tenths % 10 != 0) {
            char frac[3] = {'.', static_cast<char>('0' + tenths % 10), '\0'};
            t(frac);
        }
        return *this;
    }
};

bool refuse(report* r, uint32_t code, const char* a, const char* b = nullptr, const char* c = nullptr)
{
    if (r != nullptr && r->code == OK) {
        r->code = code;
        what_line w{r->what, 0, sizeof(r->what)};
        w.t(a).t(b).t(c);
    }
    return false;
}

bool refuse_range(report* r, const char* field, double v, double lo, double hi)
{
    if (r != nullptr && r->code == OK) {
        r->code = BAD_VALUE;
        what_line w{r->what, 0, sizeof(r->what)};
        w.t(field).t(" ").f(v).t(": ").f(lo).t("..").f(hi);
    }
    return false;
}

// ---- the reader's helpers -------------------------------------------------------------------------------------------------
struct ctx {
    const json::doc& d;
    report* r;
};

bool is_num(const json::node* n) { return n != nullptr && n->type == json::T_NUM; }

bool get_num(ctx& c, const json::node* obj, const char* key, const char* field, double lo, double hi, float* out,
             bool required)
{
    const json::node* n = json::member(c.d, obj, key);
    if (n == nullptr) {
        return required ? refuse(c.r, MISSING, field) : true;
    }
    if (!is_num(n)) {
        return refuse(c.r, BAD_VALUE, field, " is not a number");
    }
    if (!(n->num >= lo && n->num <= hi)) {
        return refuse_range(c.r, field, n->num, lo, hi);
    }
    *out = static_cast<float>(n->num);
    return true;
}

bool get_bool(ctx& c, const json::node* obj, const char* key, const char* field, uint8_t* out)
{
    const json::node* n = json::member(c.d, obj, key);
    if (n == nullptr) {
        return true;
    }
    if (n->type != json::T_TRUE && n->type != json::T_FALSE) {
        return refuse(c.r, BAD_VALUE, field, " is not true or false");
    }
    *out = n->type == json::T_TRUE ? 1 : 0;
    return true;
}

// [r, g, b] or [r, g, b, a], whole numbers 0..255 (alpha 255 when left out).
bool rgba_of(ctx& c, const json::node* n, const char* field, bool alpha_ok, uint32_t* out)
{
    if (n == nullptr || n->type != json::T_ARR || (n->count != 3 && !(alpha_ok && n->count == 4))) {
        return refuse(c.r, BAD_VALUE, field, alpha_ok ? " must be [r, g, b] or [r, g, b, a]" : " must be [r, g, b]");
    }
    uint32_t v[4] = {0, 0, 0, 255};
    for (uint32_t k = 0; k < n->count; ++k) {
        const json::node* e = json::element(c.d, n, k);
        if (!is_num(e) || e->num < 0.0 || e->num > 255.0 || e->num != static_cast<double>(static_cast<int32_t>(e->num))) {
            return refuse(c.r, BAD_VALUE, field, " needs whole numbers 0 to 255");
        }
        v[k] = static_cast<uint32_t>(e->num);
    }
    *out = rgba(v[0], v[1], v[2], v[3]);
    return true;
}

bool get_rgba(ctx& c, const json::node* obj, const char* key, const char* field, bool alpha_ok, uint32_t* out,
              bool required)
{
    const json::node* n = json::member(c.d, obj, key);
    if (n == nullptr) {
        return required ? refuse(c.r, MISSING, field) : true;
    }
    return rgba_of(c, n, field, alpha_ok, out);
}

// rgba, or null (alpha 0: none); absent keeps *out.
bool get_rgba_or_null(ctx& c, const json::node* obj, const char* key, const char* field, uint32_t* out)
{
    const json::node* n = json::member(c.d, obj, key);
    if (n == nullptr) {
        return true;
    }
    if (n->type == json::T_NULL) {
        *out = 0;
        return true;
    }
    return rgba_of(c, n, field, true, out);
}

// A string member that must be one of `names`; *out = its index. Absent keeps *out (required: refused).
bool get_enum(ctx& c, const json::node* obj, const char* key, const char* field, const char* const* names,
              uint32_t count, uint8_t* out, bool required)
{
    const json::node* n = json::member(c.d, obj, key);
    if (n == nullptr) {
        return required ? refuse(c.r, MISSING, field) : true;
    }
    for (uint32_t k = 0; k < count; ++k) {
        if (json::str_is(c.d, n, names[k])) {
            *out = static_cast<uint8_t>(k);
            return true;
        }
    }
    return refuse(c.r, BAD_VALUE, field, " is not a known value");
}

const json::node* section(ctx& c, const json::node* root, const char* key, bool required, bool* ok)
{
    const json::node* n = json::member(c.d, root, key);
    if (n == nullptr) {
        if (required) {
            *ok = refuse(c.r, MISSING, key, " (section)");
        }
        return nullptr;
    }
    if (n->type != json::T_OBJ) {
        *ok = refuse(c.r, BAD_VALUE, key, " must be an object");
        return nullptr;
    }
    return n;
}

bool same(const char* a, uint32_t alen, const char* b);

// An object whose members must all be among `keys` (text.tracking, motion): an unknown key is refused by name, so a
// misspelt or renamed key is never silently ignored.
bool only_keys(ctx& c, const json::node* obj, const char* field, const char* const* keys, uint32_t n, const char* hint)
{
    for (const json::node* e = json::element(c.d, obj, 0); e != nullptr; e = json::next(c.d, e)) {
        bool known = false;
        for (uint32_t k = 0; k < n && !known; ++k) {
            known = same(json::key_of(c.d, e), e->key_len, keys[k]);
        }
        if (!known) {
            if (c.r != nullptr && c.r->code == OK) {
                c.r->code = BAD_VALUE;
                what_line w{c.r->what, 0, sizeof(c.r->what)};
                w.t(field).t(": unknown key \"").t(json::key_of(c.d, e)).t("\" (").t(hint).t(")");
            }
            return false;
        }
    }
    return true;
}

// THEME-FORMAT.md 2's own defaults for text.tracking and motion: what a theme gets for a key it leaves out, whatever
// theme it starts from.
void tracking_defaults(tokens* t)
{
    t->page_face = font::FACE_BODY;
    t->title_track = 0.0f;
    t->header_track = 0.06f;
}

void motion_defaults(tokens* t)
{
    t->marquee_speed = 40.0f;
    t->marquee_hold_ms = 1000;
    t->marquee_fade = 12.0f;
    t->marquee_on = 1;
}

bool id_char(uint8_t ch)
{
    return (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '-' || ch == '_';
}

bool same(const char* a, uint32_t alen, const char* b)
{
    uint32_t k = 0;
    while (k < alen && b[k] != '\0' && a[k] == b[k]) {
        ++k;
    }
    return k == alen && b[k] == '\0';
}

uint8_t lower(uint8_t ch) { return ch >= 'A' && ch <= 'Z' ? static_cast<uint8_t>(ch + 32) : ch; }

// A status name (any case) -> its bit, or 0xFF.
uint8_t status_bit_of(const char* name, uint32_t len)
{
    for (uint32_t b = 0; b < 32; ++b) {
        const char* s = STATUS_NAMES[b];
        uint32_t k = 0;
        while (k < len && s[k] != '\0' && lower(static_cast<uint8_t>(name[k])) == static_cast<uint8_t>(s[k])) {
            ++k;
        }
        if (k == len && s[k] == '\0') {
            return static_cast<uint8_t>(b);
        }
    }
    return 0xFF;
}

// The status's icon suffix: its name lower case with spaces as '_' ("pic_poison").
uint32_t status_suffix(uint8_t bit, char* out, uint32_t cap)
{
    const char* s = STATUS_NAMES[bit & 31u];
    uint32_t n = 0;
    while (s[n] != '\0' && n + 1 < cap) {
        out[n] = s[n] == ' ' ? '_' : s[n];
        ++n;
    }
    out[n] = '\0';
    return n;
}

// A {name} layer's glyphs for this theme's status list, by status name: glyph[s] = the icon "<prefix><name of status
// s>" in `names` (ICON_NONE when the theme has none: that status skips the layer).
void resolve_names(layer& l, const char* const* names, uint32_t icons, const tokens& t)
{
    for (uint16_t& g : l.glyph) {
        g = font::ICON_NONE;
    }
    uint32_t pre = 0;
    char name[GLYPH_NAME_MAX + 24];
    while (pre < GLYPH_PREFIX_MAX && l.prefix[pre] != '\0') {
        name[pre] = l.prefix[pre];
        ++pre;
    }
    for (uint32_t s = 0; s < t.status_count && s < STATUS_ICONS_MAX; ++s) {
        char suffix[24];
        const uint32_t sl = status_suffix(t.status_bit[s], suffix, sizeof(suffix));
        uint32_t n = pre;
        for (uint32_t q = 0; q < sl && n < sizeof(name); ++q) {
            name[n++] = suffix[q];
        }
        l.glyph[s] = icon_index(names, icons, name, n);
    }
}

// ---- recipes -----------------------------------------------------------------------------------------------------------
constexpr const char* COLOUR_REFS[] = {"", "", "seat", "seat_light", "seat_dark", "ink", "white", "white_soft",
                                       "ink_soft", "chevron", "dot_colour", "status", "status_light", "status_mid",
                                       "status_dark"};

bool colour_of(ctx& c, const json::node* n, const char* field, colour* out)
{
    *out = colour{};
    if (n == nullptr || n->type == json::T_NULL) {
        out->ref = CR_NONE;
        return true;
    }
    if (n->type == json::T_STR) {
        for (uint32_t k = CR_SEAT; k <= CR_STATUS_DARK; ++k) {
            if (json::str_is(c.d, n, COLOUR_REFS[k])) {
                out->ref = static_cast<uint8_t>(k);
                return true;
            }
        }
        return refuse(c.r, BAD_VALUE, field, " names an unknown colour");
    }
    out->ref = CR_RGBA;
    return rgba_of(c, n, field, true, &out->rgba);
}

bool read_recipe(ctx& c, const json::node* arr, const char* field, const char* const* icon_names, uint32_t icons,
                 const tokens& t, recipe* out)
{
    if (arr->type != json::T_ARR || arr->count == 0 || arr->count > RECIPE_MAX) {
        return refuse(c.r, BAD_VALUE, field, " must list 1 to 6 layers");
    }
    recipe rc = {};
    for (uint32_t k = 0; k < arr->count; ++k) {
        const json::node* e = json::element(c.d, arr, k);
        if (e->type != json::T_OBJ) {
            return refuse(c.r, BAD_VALUE, field, " layers must be objects");
        }
        layer& l = rc.l[k];
        for (uint16_t& g : l.glyph) {
            g = font::ICON_NONE;
        }
        const json::node* g = json::member(c.d, e, "glyph");
        const char* gs = json::str_of(c.d, g);
        if (gs == nullptr || g->str_len == 0 || g->str_len > GLYPH_NAME_MAX) {
            return refuse(c.r, BAD_VALUE, field, " layer without a glyph name");
        }
        // Templates: "{dir}" and "{name}" (the whole suffix after the prefix).
        char name[64];
        uint32_t pre = 0;
        while (pre < g->str_len && gs[pre] != '{') {
            name[pre] = gs[pre];
            ++pre;
        }
        const char* rest = gs + pre;
        const uint32_t rest_len = g->str_len - pre;
        if (rest_len == 0) {
            l.tmpl = GT_PLAIN;
            l.glyph[0] = icon_index(icon_names, icons, gs, g->str_len);
            if (l.glyph[0] == font::ICON_NONE) {
                return refuse(c.r, BAD_ICONS, field, " names an icon the theme does not have: ", gs);
            }
        } else if (same(rest, rest_len, "{dir}")) {
            l.tmpl = GT_DIR;
            const char* dirs[4] = {"up", "down", "left", "right"};
            for (uint32_t d = 0; d < 4; ++d) {
                uint32_t n = pre;
                for (const char* p = dirs[d]; *p != '\0'; ++p) {
                    name[n++] = *p;
                }
                l.glyph[d] = icon_index(icon_names, icons, name, n);
                if (l.glyph[d] == font::ICON_NONE) {
                    return refuse(c.r, BAD_ICONS, field, " names an icon the theme does not have: ", gs);
                }
            }
        } else if (same(rest, rest_len, "{name}")) {
            l.tmpl = GT_NAME;
            for (uint32_t q = 0; q < pre && q < GLYPH_PREFIX_MAX; ++q) {
                l.prefix[q] = gs[q];
            }
            resolve_names(l, icon_names, icons, t);   // a status without this icon skips the layer
        } else {
            return refuse(c.r, BAD_VALUE, field, " glyph template must be {dir} or {name}");
        }
        const json::node* core = json::member(c.d, e, "core");
        const json::node* top = json::member(c.d, e, "core_top");
        const json::node* bottom = json::member(c.d, e, "core_bottom");
        if (core != nullptr) {
            if (!colour_of(c, core, field, &l.core_top)) {
                return false;
            }
            l.core_bottom = l.core_top;
        } else if (top != nullptr && bottom != nullptr) {
            if (!colour_of(c, top, field, &l.core_top) || !colour_of(c, bottom, field, &l.core_bottom)) {
                return false;
            }
        } else {
            return refuse(c.r, MISSING, field, " layer needs core or core_top + core_bottom");
        }
        if (!colour_of(c, json::member(c.d, e, "edge"), field, &l.edge) ||
            !colour_of(c, json::member(c.d, e, "outline"), field, &l.outline)) {
            return false;
        }
        if (!get_num(c, e, "edge_px", field, 0.0, 6.0, &l.edge_px, false) ||
            !get_num(c, e, "grow", field, 0.0, 8.0, &l.grow, false) ||
            !get_num(c, e, "glow_px", field, 0.0, 12.0, &l.glow_px, false)) {
            return false;
        }
        l.glow_alpha = l.glow_px > 0.0f ? 0.55f : 0.0f;
        if (!get_num(c, e, "glow_alpha", field, 0.0, 1.0, &l.glow_alpha, false)) {
            return false;
        }
    }
    rc.n = arr->count;
    *out = rc;
    return true;
}

// Recipes inherited from the default theme name icons in the default's order (make_builtin checks that order is
// DEFAULT_ICON_NAMES): map their plain and {dir} glyphs into this theme's order by name. {name} layers are resolved
// again by status name once this theme's status list is known (resolve_names), never by slot index.
void remap(recipe& rc, const char* const* names, uint32_t icons)
{
    for (uint32_t k = 0; k < rc.n; ++k) {
        if (rc.l[k].tmpl == GT_NAME) {
            continue;
        }
        for (uint16_t& g : rc.l[k].glyph) {
            if (g == font::ICON_NONE || g >= DEFAULT_ICON_COUNT) {
                continue;
            }
            const char* nm = DEFAULT_ICON_NAMES[g];
            uint32_t len = 0;
            while (nm[len] != '\0') {
                ++len;
            }
            g = icon_index(names, icons, nm, len);
        }
    }
}

bool recipe_complete(const recipe& rc, uint32_t tmpl_slots)
{
    for (uint32_t k = 0; k < rc.n; ++k) {
        const layer& l = rc.l[k];
        const uint32_t need = l.tmpl == GT_PLAIN ? 1 : (l.tmpl == GT_DIR ? 4 : 0);
        for (uint32_t i = 0; i < need && i < tmpl_slots; ++i) {
            if (l.glyph[i] == font::ICON_NONE) {
                return false;
            }
        }
    }
    return true;
}

// ---- python-style text of the digest's tables (theme_convert.py: f'{row}\n', f'{kern}\n', f'{metrics}\n') -------------
struct text_out {
    char s[256];
    uint32_t n = 0;
    void c(char ch)
    {
        if (n + 1 < sizeof(s)) {
            s[n++] = ch;
        }
    }
    void t(const char* p)
    {
        while (*p != '\0') {
            c(*p++);
        }
    }
    void i(int64_t v)
    {
        if (v < 0) {
            c('-');
            v = -v;
        }
        char d[24];
        uint32_t k = 0;
        uint64_t w = static_cast<uint64_t>(v);
        do {
            d[k++] = static_cast<char>('0' + w % 10);
            w /= 10;
        } while (w != 0);
        while (k > 0) {
            c(d[--k]);
        }
    }
    // Python's repr of v / 64 (exact: a dyadic fraction with at most six decimals).
    void q64(int32_t v)
    {
        int64_t x = v;
        if (x < 0) {
            c('-');
            x = -x;
        }
        i(x / 64);
        c('.');
        int64_t frac = (x % 64) * 15625;   // millionths
        if (frac == 0) {
            c('0');
            return;
        }
        char d[6];
        for (int k = 5; k >= 0; --k) {
            d[k] = static_cast<char>('0' + frac % 10);
            frac /= 10;
        }
        int last = 5;
        while (last > 0 && d[last] == '0') {
            --last;
        }
        for (int k = 0; k <= last; ++k) {
            c(d[k]);
        }
    }
};

void digest_hex(const uint8_t d[32], char out[65])
{
    const char* hex = "0123456789abcdef";
    for (uint32_t k = 0; k < 32; ++k) {
        out[2 * k] = hex[d[k] >> 4];
        out[2 * k + 1] = hex[d[k] & 15];
    }
    out[64] = '\0';
}

void compute_digest(const font::set& f, const uint8_t* atlas, uint8_t out[32])
{
    sha256_ctx h;
    sha256_init(h);
    sha256_update(h, atlas, static_cast<size_t>(f.atlas_w) * f.atlas_h);
    for (uint32_t k = 0; k < f.row_count; ++k) {
        const font::glyph_row& r = f.rows[k];
        text_out t;
        t.c('(');
        t.i(r.face);
        t.t(", ");
        t.i(r.code);
        t.t(", ");
        t.i(r.g.x);
        t.t(", ");
        t.i(r.g.y);
        t.t(", ");
        t.i(r.g.w);
        t.t(", ");
        t.i(r.g.h);
        t.t(", ");
        t.i(r.g.bx);
        t.t(", ");
        t.i(r.g.by);
        t.t(", ");
        t.i(r.g.advance64);
        t.t(")\n");
        sha256_update(h, t.s, t.n);
    }
    for (uint32_t k = 0; k < f.kern_count; ++k) {
        const font::kern_row& r = f.kerns[k];
        text_out t;
        t.c('(');
        t.i(r.face);
        t.t(", ");
        t.i(r.left);
        t.t(", ");
        t.i(r.right);
        t.t(", ");
        t.i(r.k64);
        t.t(")\n");
        sha256_update(h, t.s, t.n);
    }
    text_out t;
    t.c('[');
    for (uint32_t face = 0; face < f.faces; ++face) {
        if (face != 0) {
            t.t(", ");
        }
        t.c('(');
        t.q64(f.metrics[face].ascent64);
        t.t(", ");
        t.q64(f.metrics[face].descent64);
        t.c(')');
    }
    t.t("]\n");
    sha256_update(h, t.s, t.n);
    sha256_final(h, out);
}

// ---- little-endian reads ------------------------------------------------------------------------------------------------
uint16_t u16_at(const uint8_t* p) { return static_cast<uint16_t>(p[0] | p[1] << 8); }
uint32_t u32_at(const uint8_t* p)
{
    return static_cast<uint32_t>(p[0]) | static_cast<uint32_t>(p[1]) << 8 | static_cast<uint32_t>(p[2]) << 16 |
           static_cast<uint32_t>(p[3]) << 24;
}

constexpr uint32_t ATLAS_HEADER = 62;
constexpr uint32_t ATLAS_FILE_MAX = ATLAS_HEADER + 8 * (font::TEXT_FACES_MAX + 1) + 24 * font::GLYPHS_MAX +
                                    16 * font::KERNS_MAX + 2 * font::ATLAS_BYTES_MAX;

// JSON arenas: theme.json and icons.json.
struct arena {
    json::node* nodes;
    char* strings;
    uint32_t node_cap, string_cap;
    bool make(uint32_t nodes_n, uint32_t strings_n)
    {
        nodes = static_cast<json::node*>(alloc(static_cast<uint64_t>(nodes_n) * sizeof(json::node)));
        strings = static_cast<char*>(alloc(strings_n));
        node_cap = nodes_n;
        string_cap = strings_n;
        return nodes != nullptr && strings != nullptr;
    }
    void drop()
    {
        release(nodes);
        release(strings);
        nodes = nullptr;
        strings = nullptr;
    }
    json::doc doc() const { return json::doc{nodes, node_cap, strings, string_cap, 0, 0, 0, nullptr}; }
};

// A path component (len bytes) that Windows opens as a DOS device whatever follows its stem ("nul.json", "COM1.x"):
// CON, PRN, AUX, NUL, COM0..COM9, LPT0..LPT9, any case.
bool dos_device(const char* part, uint32_t len)
{
    uint32_t stem = 0;
    while (stem < len && part[stem] != '.') {
        ++stem;
    }
    if (stem != 3 && stem != 4) {
        return false;
    }
    char u[4];
    for (uint32_t k = 0; k < stem; ++k) {
        const char ch = part[k];
        u[k] = ch >= 'a' && ch <= 'z' ? static_cast<char>(ch - 32) : ch;
    }
    const auto is3 = [&](const char* w) { return u[0] == w[0] && u[1] == w[1] && u[2] == w[2]; };
    if (stem == 3) {
        return is3("CON") || is3("PRN") || is3("AUX") || is3("NUL");
    }
    return (is3("COM") || is3("LPT")) && u[3] >= '0' && u[3] <= '9';
}

// Path join: dir + "\" + relative UTF-8 path (ASCII letters, digits, '.', '-', '_', '/' only; no "..", no root, no
// component that starts or ends with '.', no DOS device name as a component).
bool join(const wchar_t* dir, const char* rel, wchar_t* out, uint32_t cap)
{
    uint32_t n = 0;
    while (dir[n] != L'\0') {
        if (n + 1 >= cap) {
            return false;
        }
        out[n] = dir[n];
        ++n;
    }
    if (rel[0] == '\0' || rel[0] == '/' || rel[0] == '.') {
        return false;
    }
    out[n++] = L'\\';
    char prev = '/';
    const char* part = rel;
    for (const char* p = rel;; ++p) {
        const char ch = *p;
        if (ch == '/' || ch == '\0') {
            const uint32_t len = static_cast<uint32_t>(p - part);
            if (len == 0 || prev == '.' || dos_device(part, len)) {
                return false;
            }
            if (ch == '\0') {
                break;
            }
            part = p + 1;
        }
        const bool ok = (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '.' ||
                        ch == '-' || ch == '_' || ch == '/';
        if (!ok || (ch == '.' && prev == '.') || (ch == '/' && prev == '/') || (ch == '.' && prev == '/') ||
            n + 1 >= cap) {
            return false;
        }
        out[n++] = ch == '/' ? L'\\' : static_cast<wchar_t>(ch);
        prev = ch;
    }
    out[n] = L'\0';
    return true;
}

// icons.file names a .json file (lower-case suffix, something before it).
bool json_suffix(const char* rel)
{
    uint32_t n = 0;
    while (rel[n] != '\0') {
        ++n;
    }
    return n > 5 && rel[n - 5] == '.' && rel[n - 4] == 'j' && rel[n - 3] == 's' && rel[n - 2] == 'o' &&
           rel[n - 1] == 'n' && rel[n - 6] != '/';
}

}  // namespace

// ---- memory and files -----------------------------------------------------------------------------------------------------
void* alloc(uint64_t bytes)
{
    if (bytes == 0 || bytes > (64ull << 20)) {
        return nullptr;
    }
    return VirtualAlloc(nullptr, static_cast<SIZE_T>(bytes), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
}

void release(void* p)
{
    if (p != nullptr) {
        VirtualFree(p, 0, MEM_RELEASE);
    }
}

uint32_t read_file(const wchar_t* path, uint32_t cap, uint8_t** out, uint32_t* len)
{
    *out = nullptr;
    *len = 0;
    HANDLE f = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) {
        const DWORD e = GetLastError();
        return e == ERROR_FILE_NOT_FOUND || e == ERROR_PATH_NOT_FOUND ? NO_FILE : READ_FAILED;
    }
    LARGE_INTEGER size;
    if (!GetFileSizeEx(f, &size) || size.QuadPart < 0) {
        CloseHandle(f);
        return READ_FAILED;
    }
    if (size.QuadPart > cap) {
        CloseHandle(f);
        return TOO_LARGE;
    }
    const uint32_t n = static_cast<uint32_t>(size.QuadPart);
    uint8_t* buf = static_cast<uint8_t*>(alloc(n + 1));
    if (buf == nullptr) {
        CloseHandle(f);
        return NO_MEMORY;
    }
    DWORD got = 0;
    const BOOL ok = n == 0 || ReadFile(f, buf, n, &got, nullptr);
    CloseHandle(f);
    if (!ok || got != n) {
        release(buf);
        return READ_FAILED;
    }
    *out = buf;
    *len = n;
    return OK;
}

uint16_t icon_index(const char* const* names, uint32_t count, const char* name, uint32_t len)
{
    for (uint32_t k = 0; k < count; ++k) {
        if (names[k] != nullptr && same(name, len, names[k])) {
            return static_cast<uint16_t>(k);
        }
    }
    return font::ICON_NONE;
}

const char* refusal_text(uint32_t r)
{
    switch (r) {
    case OK: return "ok";
    case NO_FILE: return "a file is missing";
    case TOO_LARGE: return "a file is too large";
    case READ_FAILED: return "a file could not be read";
    case BAD_JSON: return "not valid JSON";
    case BAD_FORMAT: return "not an ilc-theme file";
    case BAD_VERSION: return "an unknown theme format version";
    case MISSING: return "a required value is missing";
    case BAD_VALUE: return "a value is out of range";
    case BAD_ID: return "a bad id";
    case BUILTIN_ID: return "the id of a built-in theme";
    case BAD_ATLAS: return "the atlas is damaged or too large";
    case BAD_DIGEST: return "the atlas digest does not match";
    case BAD_ICONS: return "the icon set is incomplete";
    case NO_MEMORY: return "out of memory";
    default: return "refused";
    }
}

// ---- tokens --------------------------------------------------------------------------------------------------------------
void base_tokens(tokens* t)
{
    *t = tokens{};
    t->outline = 1.4f;
    t->ink = rgba(10, 14, 34, 255);
    t->shadow_dx = 1.0f;
    t->shadow_dy = 2.0f;
    t->shadow_soft = 1.0f;
    t->shadow = rgba(0, 0, 0, 150);
    t->small_below_px = 19.0f;
    t->small_tracking_em = 0.02f;
    tracking_defaults(t);
    t->row_em = 23.0f;
    t->value_em = 21.0f;
    t->header_em = 18.0f;
    t->tab_em = 19.0f;
    t->help_em = 17.0f;
    t->glove = 26.0f;
    t->glove_out = 12.0f;
    t->panel_head = 21.0f;
    t->target_em = 27.0f;
    t->party_name_em = 26.0f;
    t->hp_em = 31.0f;
    t->max_em = 20.0f;
    t->mpv_em = 24.0f;
    t->act_em = 18.0f;
    t->log_em = 20.0f;
    t->core = rgba(228, 228, 228, 255);
    t->dim = rgba(142, 150, 170, 255);
    t->header = rgba(207, 224, 255, 255);
    t->help = rgba(196, 204, 222, 255);
    t->gold = rgba(242, 220, 138, 255);
    t->foe_dot = rgba(200, 121, 74, 255);
    t->heal = rgba(109, 255, 142, 255);
    t->dmg = rgba(255, 106, 88, 255);
    t->low = rgba(255, 106, 90, 255);
    t->mpl = rgba(156, 198, 255, 255);
    t->chev = rgba(208, 228, 255, 255);
    t->rule = rgba(170, 206, 255, 76);
    t->rule2 = rgba(200, 220, 255, 217);
    t->hp_warn = rgba(255, 214, 92, 255);
    t->hp_critical = rgba(255, 104, 88, 255);
    t->hp_critical_glow = rgba(255, 70, 50, 255);
    t->drop_rare = rgba(236, 152, 190, 255);
    t->seat[0] = rgba(80, 140, 255, 255);
    t->seat[1] = rgba(235, 80, 90, 255);
    t->seat[2] = rgba(80, 210, 120, 255);
    t->seat[3] = rgba(240, 200, 80, 255);
    t->gambit_ally = rgba(201, 206, 216, 255);
    t->win_shape = SHAPE_ROUND;
    t->win_radius = 10.0f;
    t->win_stops = 2;
    t->stop_t[0] = 0.0f;
    t->stop_t[1] = 1.0f;
    t->stop_c[0] = rgba(64, 104, 196, 208);
    t->stop_c[1] = rgba(7, 16, 56, 232);
    t->win_rim = rgba(170, 206, 255, 190);
    t->win_rim_px = 1.0f;
    t->shine = SHINE_SOFT;
    t->shine_amount = 0.2f;
    t->shine_extent = 0.34f;
    t->shine_rgba = rgba(200, 224, 255, 255);
    t->tab_kind = TAB_A;
    t->tab_shape = SHAPE_ROUND;
    t->tab_h = 32.0f;
    t->tab_radius = 8.0f;
    t->tab_top = rgba(92, 134, 214, 226);
    t->tab_bottom = rgba(36, 64, 146, 232);
    t->focus_darken = 0.66f;
    t->focus_alpha_top = 200;
    t->focus_alpha_bottom = 110;
    t->focus_edge_alpha = 235;
    t->gauge_kind = GAUGE_A;
    t->gauge_h = 4.0f;
    t->g_hp[0] = t->g_hp[1] = rgba(223, 240, 255, 255);
    t->g_low[0] = t->g_low[1] = rgba(255, 106, 90, 255);
    t->g_mp[0] = t->g_mp[1] = rgba(124, 196, 255, 255);
    t->g_act[0] = t->g_act[1] = rgba(233, 240, 255, 255);
    t->g_trail = rgba(255, 190, 180, 204);
    t->g_track = rgba(0, 0, 0, 140);
    t->warn_at = 0.5f;
    t->critical_at = 0.25f;
    t->critical_glow_px = 5.0f;
    t->status_style = STATUS_MEDALLION;
    motion_defaults(t);
    t->leader_band = rgba(60, 120, 235, 140);
    t->log_mp = rgba(124, 196, 255, 255);
    t->log_important = rgba(255, 228, 184, 255);
    t->log_miss = rgba(196, 200, 210, 255);
    t->log_holding = rgba(160, 228, 192, 255);
    t->log_dot_system = rgba(150, 154, 166, 255);
    t->status_token_rim = rgba(255, 255, 255, 115);
    t->ti_fill_left = rgba(216, 236, 255, 255);
    t->ti_fill_right = rgba(159, 208, 255, 255);
    t->card_top = rgba(30, 50, 104, 222);
    t->card_bottom = rgba(8, 15, 42, 222);
    t->card_rim = rgba(170, 186, 228, 200);
    t->card_shine = rgba(255, 255, 255, 30);
    t->glove_bob = 3.0f;
    const char id[] = "legacy-bold";
    for (uint32_t k = 0; k < sizeof(id); ++k) {
        t->id[k] = id[k];
        t->name[k] = id[k];
    }
}

void resolve(tokens* t)
{
    const uint32_t ink = set_alpha(t->ink, 235);
    const auto st = [&](uint32_t core) {
        return text_style{core, ink, t->outline, 0.0f, t->shadow, t->shadow_dx, t->shadow_dy, t->shadow_soft, 0.0f};
    };
    t->row = st(t->core);
    t->grey = st(t->dim);
    t->help_st = st(t->help);
    t->header_st = st(t->header);
    t->value = t->row;
    t->on = st(t->gold);
    t->hdr = st(t->header);
    t->tag = st(t->gold);
    t->label = st(t->mpl);
    t->numeral = text_style{rgba(255, 255, 255, 255), set_alpha(t->ink, 220), t->outline * 0.7f, 0.0f, 0, 0, 0, 0, 0};
    t->low_hp = st(t->hp_critical);
    t->low_hp.edge = set_alpha(t->hp_critical_glow, 255);
    t->low_hp.glow = t->critical_glow_px;
    t->word = st(rgba(232, 226, 204, 255));
    t->word_cost = st(rgba(255, 143, 122, 255));
    t->party_max = st(mix(t->core, t->dim, 0.2f));
    t->mp_label = st(t->mpl);
    t->gambit_g = st(t->gold);
    t->danger = st(t->low);
    t->anti_libra = st(t->low);
    t->state_hot = st(rgba(255, 201, 138, 255));
    t->state = st(t->core);
    t->drop_rare_st = st(t->drop_rare);
    t->status_mark = text_style{rgba(255, 255, 255, 255), rgba(0, 0, 0, 230), 1.0f, 0.0f, 0, 0, 0, 0, 0};
    t->icon_st = st(t->chev);
    t->card_label = st(rgba(232, 204, 128, 255));
}

// ---- theme.json ---------------------------------------------------------------------------------------------------------
bool read_json(const char* text, uint32_t len, const char* id, const char* const* icon_names, uint32_t icons,
               font::set* font, tokens* out, report* r)
{
    if (len > JSON_BYTES_MAX) {
        return refuse(r, TOO_LARGE, "theme.json");
    }
    arena a = {};
    if (!a.make(len / 2 + 64, len + 64)) {
        a.drop();
        return refuse(r, NO_MEMORY, "theme.json");
    }
    json::doc d = a.doc();
    if (!json::parse(d, text, len, 16)) {
        refuse(r, BAD_JSON, "theme.json: ", d.error);
        a.drop();
        return false;
    }
    tokens t = *out;
    ctx c{d, r};
    bool ok = true;
    const json::node* root = json::root(d);
    // Recipes the theme does not give are the default's, in the default's icon order: map them into this theme's.
    remap(t.glove_r, icon_names, icons);
    remap(t.chevron_r, icon_names, icons);
    remap(t.diamond_r, icon_names, icons);
    remap(t.dot_r, icon_names, icons);
    remap(t.status_r, icon_names, icons);
    do {
        if (root == nullptr || root->type != json::T_OBJ) {
            ok = refuse(r, BAD_JSON, "theme.json is not an object");
            break;
        }
        static const char* const required[] = {"format", "version", "id", "name", "fonts", "text", "sizes",
                                               "colors", "seats", "window"};
        for (const char* k : required) {
            if (json::member(d, root, k) == nullptr) {
                ok = refuse(r, MISSING, k, " (section)");
                break;
            }
        }
        if (!ok) {
            break;
        }
        if (!json::str_is(d, json::member(d, root, "format"), "ilc-theme")) {
            ok = refuse(r, BAD_FORMAT, "format");
            break;
        }
        const json::node* ver = json::member(d, root, "version");
        if (!is_num(ver) || ver->num != 1.0) {
            ok = refuse(r, BAD_VERSION, "version");
            break;
        }
        // id: lower-case letters, digits, '-', '_' (1..32), the folder's name.
        const json::node* idn = json::member(d, root, "id");
        const char* ids = json::str_of(d, idn);
        if (ids == nullptr || idn->str_len == 0 || idn->str_len > ID_MAX) {
            ok = refuse(r, BAD_ID, "id");
            break;
        }
        for (uint32_t k = 0; k < idn->str_len; ++k) {
            if (!id_char(static_cast<uint8_t>(ids[k]))) {
                ok = refuse(r, BAD_ID, "id: lower-case letters, digits, '-' or '_' only");
                break;
            }
        }
        if (!ok) {
            break;
        }
        if (id != nullptr && !same(ids, idn->str_len, id)) {
            ok = refuse(r, BAD_ID, "id differs from the folder name");
            break;
        }
        for (uint32_t k = 0; k <= ID_MAX; ++k) {
            t.id[k] = k < idn->str_len ? ids[k] : '\0';
        }
        // name: shown in the Mod Menu as printable ASCII (anything else as '?'), cut to NAME_MAX bytes.
        const json::node* nm = json::member(d, root, "name");
        const char* nms = json::str_of(d, nm);
        if (nms == nullptr) {
            ok = refuse(r, BAD_VALUE, "name");
            break;
        }
        {
            uint32_t o = 0;
            uint32_t at = 0;
            while (at < nm->str_len && o < NAME_MAX) {
                const uint32_t cp = font::next_code(nms, nm->str_len, &at);
                t.name[o++] = cp >= 0x20 && cp < 0x7F ? static_cast<char>(cp) : '?';
            }
            while (o > 0 && t.name[o - 1] == ' ') {
                --o;
            }
            if (o == 0) {
                for (; o < NAME_MAX && t.id[o] != '\0'; ++o) {
                    t.name[o] = t.id[o];
                }
            }
            for (uint32_t k = o; k <= NAME_MAX; ++k) {
                t.name[k] = '\0';
            }
        }
        // fonts: the faces in order (body and title required, at most five): must match the atlas.
        const json::node* fonts = section(c, root, "fonts", true, &ok);
        if (!ok) {
            break;
        }
        if (json::member(d, fonts, "body") == nullptr || json::member(d, fonts, "title") == nullptr) {
            ok = refuse(r, MISSING, "fonts.body and fonts.title");
            break;
        }
        if (fonts->count > font::TEXT_FACES_MAX || fonts->count != font->faces) {
            ok = refuse(r, BAD_ATLAS, "fonts: the theme.json fonts and the atlas faces differ");
            break;
        }
        {
            uint32_t k = 0;
            uint8_t numerals = 0xFF;
            for (const json::node* e = json::element(d, fonts, 0); e != nullptr; e = json::next(d, e), ++k) {
                if (e->type != json::T_OBJ || json::str_of(d, json::member(d, e, "file")) == nullptr) {
                    ok = refuse(r, BAD_VALUE, "fonts.", json::key_of(d, e), " needs a file");
                    break;
                }
                const char* key = json::key_of(d, e);
                if (same(key, e->key_len, "body")) {
                    font->role_face[font::FACE_BODY] = static_cast<uint8_t>(k);
                } else if (same(key, e->key_len, "title")) {
                    font->role_face[font::FACE_TITLE] = static_cast<uint8_t>(k);
                } else if (same(key, e->key_len, "numerals")) {
                    numerals = static_cast<uint8_t>(k);
                }
            }
            if (!ok) {
                break;
            }
            font->role_face[font::FACE_NUMERALS] = numerals != 0xFF ? numerals : font->role_face[font::FACE_BODY];
        }
        // text
        const json::node* tx = section(c, root, "text", true, &ok);
        if (!ok || !get_num(c, tx, "outline", "text.outline", 0.8, 3.0, &t.outline, true)) {
            ok = false;
            break;
        }
        {
            const json::node* sh = json::member(d, tx, "shadow");
            if (sh != nullptr) {
                if (sh->type != json::T_ARR || sh->count != 4) {
                    ok = refuse(r, BAD_VALUE, "text.shadow must be [dx, dy, soft, alpha]");
                    break;
                }
                const double lo[4] = {-10, -10, 0, 0};
                const double hi[4] = {10, 10, 10, 1};
                float v[4] = {0, 0, 0, 0};
                for (uint32_t k = 0; k < 4 && ok; ++k) {
                    const json::node* e = json::element(d, sh, k);
                    if (!is_num(e) || !(e->num >= lo[k] && e->num <= hi[k])) {
                        ok = refuse_range(r, "text.shadow", is_num(e) ? e->num : 0.0, lo[k], hi[k]);
                    } else {
                        v[k] = static_cast<float>(e->num);
                    }
                }
                if (!ok) {
                    break;
                }
                t.shadow_dx = v[0];
                t.shadow_dy = v[1];
                t.shadow_soft = v[2];
                t.shadow = set_alpha(0, static_cast<uint32_t>(v[3] * 255.0f + 0.5f));   // filled with the ink below
            }
            float soft = 0.0f;
            if (!get_num(c, tx, "soft", "text.soft", 0.0, 5.0, &soft, false)) {
                ok = false;
                break;
            }
            const json::node* small = json::member(d, tx, "small");
            if (small != nullptr) {
                if (small->type != json::T_OBJ ||
                    !get_num(c, small, "below_px", "text.small.below_px", 0.0, 64.0, &t.small_below_px, false) ||
                    !get_num(c, small, "tracking_em", "text.small.tracking_em", 0.0, 0.2, &t.small_tracking_em,
                             false)) {
                    ok = refuse(r, BAD_VALUE, "text.small");
                    break;
                }
            }
            float min_help = 17.0f;
            if (!get_num(c, tx, "min_help_em", "text.min_help_em", 17.0, 40.0, &min_help, false)) {
                ok = false;
                break;
            }
            // text.tracking {page_face, title_em, header_em} (THEME-FORMAT.md 2): a key left out (or the whole object)
            // takes the spec's default body / 0 / 0.06; an unknown key is refused.
            tracking_defaults(&t);
            const json::node* tr = json::member(d, tx, "tracking");
            if (tr != nullptr) {
                static const char* const faces[] = {"body", "title"};
                static const char* const keys[] = {"page_face", "title_em", "header_em"};
                uint8_t pf = 0;
                if (tr->type != json::T_OBJ) {
                    ok = refuse(r, BAD_VALUE, "text.tracking must be an object");
                    break;
                }
                if (!only_keys(c, tr, "text.tracking", keys, 3, "page_face, title_em, header_em") ||
                    !get_enum(c, tr, "page_face", "text.tracking.page_face", faces, 2, &pf, false) ||
                    !get_num(c, tr, "title_em", "text.tracking.title_em", 0.0, 0.3, &t.title_track, false) ||
                    !get_num(c, tr, "header_em", "text.tracking.header_em", 0.0, 0.3, &t.header_track, false)) {
                    ok = false;
                    break;
                }
                t.page_face = pf == 0 ? font::FACE_BODY : font::FACE_TITLE;
            }
        }
        // sizes
        const json::node* sz = section(c, root, "sizes", true, &ok);
        if (!ok) {
            break;
        }
        float pitch = 30.0f;
        if (!get_num(c, sz, "row", "sizes.row", 18.0, 30.0, &t.row_em, true) ||
            !get_num(c, sz, "help", "sizes.help", 17.0, 40.0, &t.help_em, true) ||
            !get_num(c, sz, "header", "sizes.header", 8.0, 60.0, &t.header_em, true) ||
            !get_num(c, sz, "tab", "sizes.tab", 8.0, 60.0, &t.tab_em, true) ||
            !get_num(c, sz, "pitch", "sizes.pitch", 8.0, 60.0, &pitch, true)) {
            ok = false;
            break;
        }
        if (pitch < t.row_em * 1.15f - 0.001f) {
            ok = refuse(r, BAD_VALUE, "sizes.pitch below 1.15 x sizes.row");
            break;
        }
        {
            struct opt {
                const char* key;
                const char* field;
                float* v;
            };
            const opt sizes[] = {{"value", "sizes.value", &t.value_em},       {"glove", "sizes.glove", &t.glove},
                                 {"glove_out", "sizes.glove_out", &t.glove_out}, {"panel_head", "sizes.panel_head", &t.panel_head},
                                 {"target", "sizes.target", &t.target_em},       {"party_name", "sizes.party_name", &t.party_name_em},
                                 {"hp", "sizes.hp", &t.hp_em},                   {"max", "sizes.max", &t.max_em},
                                 {"mpv", "sizes.mpv", &t.mpv_em},                {"act", "sizes.act", &t.act_em},
                                 {"log", "sizes.log", &t.log_em}};
            for (const opt& o : sizes) {
                if (!get_num(c, sz, o.key, o.field, 8.0, 60.0, o.v, false)) {
                    ok = false;
                    break;
                }
            }
            if (!ok) {
                break;
            }
            if (t.glove_out > 16.0f) {
                t.glove_out = 16.0f;   // the slot geometry: the glove never reaches into the window on its left
            }
        }
        // colors: every value a colour; the required ones present; the contrast rules.
        const json::node* co = section(c, root, "colors", true, &ok);
        if (!ok) {
            break;
        }
        for (const json::node* e = json::element(d, co, 0); e != nullptr && ok; e = json::next(d, e)) {
            uint32_t dummy = 0;
            ok = rgba_of(c, e, json::key_of(d, e), true, &dummy);
        }
        if (!ok) {
            break;
        }
        {
            struct col {
                const char* key;
                uint32_t* v;
                bool required;
            };
            uint32_t ink = t.ink;
            const col cols[] = {{"core", &t.core, true},      {"dim", &t.dim, true},          {"header", &t.header, true},
                                {"help", &t.help, true},      {"gold", &t.gold, true},        {"ink", &ink, true},
                                {"heal", &t.heal, true},      {"dmg", &t.dmg, true},          {"low", &t.low, true},
                                {"foe_dot", &t.foe_dot, false}, {"mpl", &t.mpl, false},       {"chev", &t.chev, false},
                                {"rule", &t.rule, false},     {"rule2", &t.rule2, false},     {"hp_warn", &t.hp_warn, false},
                                {"hp_critical", &t.hp_critical, false},
                                {"hp_critical_glow", &t.hp_critical_glow, false},
                                {"drop_rare", &t.drop_rare, false}};
            for (const col& k : cols) {
                if (!get_rgba(c, co, k.key, k.key, true, k.v, k.required)) {
                    ok = false;
                    break;
                }
            }
            if (!ok) {
                break;
            }
            t.ink = ink;
            if (!get_rgba(c, tx, "ink", "text.ink", true, &t.ink, false)) {
                ok = false;
                break;
            }
            // The contrast rules against the outline colour the text is really drawn with: text.ink when the theme
            // gives one (it overrides colors.ink for every text style, resolve()), else colors.ink.
            const char* const outline_name = json::member(d, tx, "ink") != nullptr ? "text.ink" : "colors.ink";
            if (contrast(t.core, t.ink) < 7.0f) {
                ok = refuse(r, BAD_VALUE, "colors.core vs ", outline_name, " (the text outline) below 7:1");
                break;
            }
            const struct {
                const char* name;
                uint32_t v;
            } states[] = {{"colors.hp_warn", t.hp_warn}, {"colors.hp_critical", t.hp_critical},
                          {"colors.drop_rare", t.drop_rare}};
            for (const auto& s : states) {
                if (contrast(s.v, t.ink) < 4.5f) {
                    if (r != nullptr && r->code == OK) {
                        r->code = BAD_VALUE;
                        what_line w{r->what, 0, sizeof(r->what)};
                        w.t(s.name).t(" vs ").t(outline_name).t(" (the text outline) below 4.5:1");
                    }
                    ok = false;
                    break;
                }
            }
            if (!ok) {
                break;
            }
            t.shadow = (t.ink & 0x00FFFFFFu) | (t.shadow & 0xFF000000u);
        }
        // seats: exactly four {n, rgb}, numbered 1..4.
        {
            const json::node* seats = json::member(d, root, "seats");
            if (seats->type != json::T_ARR || seats->count != 4) {
                ok = refuse(r, BAD_VALUE, "seats must list exactly four seats");
                break;
            }
            for (uint32_t k = 0; k < 4 && ok; ++k) {
                const json::node* s = json::element(d, seats, k);
                const json::node* num = json::member(d, s, "n");
                if (s->type != json::T_OBJ || !is_num(num) || num->num != static_cast<double>(k + 1)) {
                    ok = refuse(r, BAD_VALUE, "seats: numbers 1 to 4 in order");
                    break;
                }
                ok = get_rgba(c, s, "rgb", "seats.rgb", false, &t.seat[k], true);
            }
            if (!ok) {
                break;
            }
            const json::node* ga = section(c, root, "gambit_ally", false, &ok);
            if (!ok || (ga != nullptr && !get_rgba(c, ga, "rgb", "gambit_ally.rgb", false, &t.gambit_ally, true))) {
                ok = false;
                break;
            }
        }
        // window
        const json::node* w = section(c, root, "window", true, &ok);
        if (!ok) {
            break;
        }
        {
            static const char* const shapes[] = {"round", "crisp", "chamfer"};
            if (!get_enum(c, w, "shape", "window.shape", shapes, 3, &t.win_shape, true) ||
                !get_num(c, w, "radius", "window.radius", 0.0, 30.0, &t.win_radius, false)) {
                ok = false;
                break;
            }
            if (t.win_shape == SHAPE_CHAMFER) {
                if (!get_num(c, w, "cut", "window.cut", 0.0, 60.0, &t.win_cut, true)) {
                    ok = false;
                    break;
                }
                const json::node* cc = json::member(d, w, "cut_corners");
                if (cc != nullptr) {
                    // The engine cuts the top-left and the bottom-right corner (one shader mode, E2).
                    const json::node* a0 = json::element(d, cc, 0);
                    const json::node* a1 = json::element(d, cc, 1);
                    if (cc->type != json::T_ARR || cc->count != 2 || !json::str_is(d, a0, "top_left") ||
                        !json::str_is(d, a1, "bottom_right")) {
                        ok = refuse(r, BAD_VALUE, "window.cut_corners: only [\"top_left\", \"bottom_right\"]");
                        break;
                    }
                }
            } else {
                t.win_cut = 0.0f;
            }
            if (t.win_shape == SHAPE_CRISP && json::member(d, w, "radius") == nullptr) {
                t.win_radius = 4.0f;
            }
            const json::node* stops = json::member(d, w, "stops");
            if (stops == nullptr || stops->type != json::T_ARR || stops->count < 2 || stops->count > STOPS_MAX) {
                ok = refuse(r, BAD_VALUE, "window.stops needs 2 to 4 stops");
                break;
            }
            float last = -1.0f;
            for (uint32_t k = 0; k < stops->count && ok; ++k) {
                const json::node* s = json::element(d, stops, k);
                const json::node* at = json::element(d, s, 0);
                if (s->type != json::T_ARR || s->count != 2 || !is_num(at) || at->num < 0.0 || at->num > 1.0 ||
                    at->num < last) {
                    ok = refuse(r, BAD_VALUE, "window.stops: [t, [r,g,b,a]] with t 0..1 in order");
                    break;
                }
                last = static_cast<float>(at->num);
                t.stop_t[k] = last;
                ok = rgba_of(c, json::element(d, s, 1), "window.stops", true, &t.stop_c[k]);
            }
            if (!ok) {
                break;
            }
            t.win_stops = static_cast<uint8_t>(stops->count);
            if (!get_rgba(c, w, "rim", "window.rim", true, &t.win_rim, false) ||
                !get_num(c, w, "rim_px", "window.rim_px", 0.5, 4.0, &t.win_rim_px, false) ||
                !get_num(c, w, "rim_top_bias", "window.rim_top_bias", 0.0, 1.0, &t.win_rim_top_bias, false) ||
                !get_rgba_or_null(c, w, "inner_line", "window.inner_line", &t.inner_line)) {
                ok = false;
                break;
            }
            const json::node* sh = json::member(d, w, "shine");
            if (sh != nullptr) {
                if (sh->type == json::T_NULL) {
                    t.shine = SHINE_NONE;
                } else {
                    static const char* const kinds[] = {"", "soft", "line", "diag"};
                    if (sh->type != json::T_OBJ || !get_enum(c, sh, "kind", "window.shine.kind", kinds, 4, &t.shine, true) ||
                        t.shine == SHINE_NONE ||
                        !get_num(c, sh, "amount", "window.shine.amount", 0.0, 1.0, &t.shine_amount, false) ||
                        !get_num(c, sh, "extent", "window.shine.extent", 0.0, 1.0, &t.shine_extent, false)) {
                        ok = refuse(r, BAD_VALUE, "window.shine");
                        break;
                    }
                }
            }
            const json::node* ol = json::member(d, w, "outer_line");
            if (ol != nullptr) {
                if (ol->type == json::T_NULL) {
                    t.outer_line = 0;
                } else if (ol->type != json::T_OBJ || !get_rgba(c, ol, "rgba", "window.outer_line.rgba", true, &t.outer_line, true) ||
                           !get_num(c, ol, "px", "window.outer_line.px", 0.5, 6.0, &t.outer_px, true)) {
                    ok = refuse(r, BAD_VALUE, "window.outer_line");
                    break;
                }
            }
            const json::node* ha = json::member(d, w, "halo");
            if (ha != nullptr) {
                if (ha->type == json::T_NULL) {
                    t.halo = 0;
                } else {
                    float alpha = 0.0f;
                    uint32_t rgb = 0;
                    if (ha->type != json::T_OBJ || !get_rgba(c, ha, "rgb", "window.halo.rgb", false, &rgb, true) ||
                        !get_num(c, ha, "sigma_px", "window.halo.sigma_px", 0.0, 20.0, &t.halo_sigma, true) ||
                        !get_num(c, ha, "alpha", "window.halo.alpha", 0.0, 1.0, &alpha, true)) {
                        ok = refuse(r, BAD_VALUE, "window.halo");
                        break;
                    }
                    t.halo = set_alpha(rgb, static_cast<uint32_t>(alpha * 255.0f + 0.5f));
                }
            }
        }
        // tab
        const json::node* tb = section(c, root, "tab", false, &ok);
        if (!ok) {
            break;
        }
        if (tb != nullptr) {
            static const char* const kinds[] = {"A", "B", "C"};
            static const char* const shapes[] = {"round", "crisp", "chamfer"};
            if (!get_enum(c, tb, "kind", "tab.kind", kinds, 3, &t.tab_kind, true) ||
                !get_num(c, tb, "height", "tab.height", 20.0, 40.0, &t.tab_h, false) ||
                !get_num(c, tb, "dx", "tab.dx", -20.0, 20.0, &t.tab_dx, false) ||
                !get_enum(c, tb, "shape", "tab.shape", shapes, 3, &t.tab_shape, false) ||
                !get_num(c, tb, "radius", "tab.radius", 0.0, 20.0, &t.tab_radius, false)) {
                ok = false;
                break;
            }
            const json::node* stops = json::member(d, tb, "stops");
            if (stops != nullptr) {
                if (stops->type != json::T_ARR || stops->count < 2 || stops->count > STOPS_MAX) {
                    ok = refuse(r, BAD_VALUE, "tab.stops needs 2 to 4 stops");
                    break;
                }
                const json::node* first = json::element(d, stops, 0);
                const json::node* lastn = json::element(d, stops, stops->count - 1);
                if (first->type != json::T_ARR || lastn->type != json::T_ARR || first->count != 2 || lastn->count != 2 ||
                    !rgba_of(c, json::element(d, first, 1), "tab.stops", true, &t.tab_top) ||
                    !rgba_of(c, json::element(d, lastn, 1), "tab.stops", true, &t.tab_bottom)) {
                    ok = refuse(r, BAD_VALUE, "tab.stops");
                    break;
                }
            }
        }
        // focus_row
        const json::node* fr = section(c, root, "focus_row", false, &ok);
        if (!ok) {
            break;
        }
        if (fr != nullptr) {
            float at = t.focus_alpha_top, ab = t.focus_alpha_bottom, ea = t.focus_edge_alpha;
            if (!get_bool(c, fr, "bar", "focus_row.bar", &t.focus_bar) ||
                !get_num(c, fr, "seat_darken", "focus_row.seat_darken", 0.0, 1.0, &t.focus_darken, false) ||
                !get_num(c, fr, "alpha_top", "focus_row.alpha_top", 0.0, 255.0, &at, false) ||
                !get_num(c, fr, "alpha_bottom", "focus_row.alpha_bottom", 0.0, 255.0, &ab, false) ||
                !get_num(c, fr, "edge_line_alpha", "focus_row.edge_line_alpha", 0.0, 255.0, &ea, false)) {
                ok = false;
                break;
            }
            t.focus_alpha_top = static_cast<uint8_t>(at);
            t.focus_alpha_bottom = static_cast<uint8_t>(ab);
            t.focus_edge_alpha = static_cast<uint8_t>(ea);
        }
        // gauge
        const json::node* g = section(c, root, "gauge", false, &ok);
        if (!ok) {
            break;
        }
        if (g != nullptr) {
            static const char* const kinds[] = {"A", "B", "C"};
            if (!get_enum(c, g, "kind", "gauge.kind", kinds, 3, &t.gauge_kind, true) ||
                !get_num(c, g, "height", "gauge.height", 3.0, 14.0, &t.gauge_h, true) ||
                !get_num(c, g, "slant", "gauge.slant", 0.0, 2.0, &t.gauge_slant, false) ||
                !get_num(c, g, "ticks_every", "gauge.ticks_every", 0.0, 0.5, &t.gauge_ticks, false) ||
                !get_rgba(c, g, "trail", "gauge.trail", true, &t.g_trail, false) ||
                !get_bool(c, g, "trail_hatch", "gauge.trail_hatch", &t.trail_hatch)) {
                ok = false;
                break;
            }
            // 0 = no ticks; anything finer than GAUGE_TICKS_MIN would draw a tick per pixel or less (review ★1).
            if (t.gauge_ticks > 0.0f && t.gauge_ticks < GAUGE_TICKS_MIN) {
                ok = refuse(r, BAD_VALUE, "gauge.ticks_every must be 0 (no ticks) or 0.02 to 0.5");
                break;
            }
            const json::node* cols = json::member(d, g, "cols");
            if (cols != nullptr) {
                if (cols->type != json::T_OBJ) {
                    ok = refuse(r, BAD_VALUE, "gauge.cols");
                    break;
                }
                struct gc {
                    const char* key;
                    uint32_t* v;
                };
                const gc list[] = {{"hp", t.g_hp}, {"low", t.g_low}, {"mp", t.g_mp}, {"act", t.g_act}};
                for (const gc& e : list) {
                    const json::node* pair = json::member(d, cols, e.key);
                    if (pair == nullptr) {
                        continue;
                    }
                    if (pair->type != json::T_ARR || pair->count != 2 ||
                        !rgba_of(c, json::element(d, pair, 0), "gauge.cols", true, &e.v[0]) ||
                        !rgba_of(c, json::element(d, pair, 1), "gauge.cols", true, &e.v[1])) {
                        ok = refuse(r, BAD_VALUE, "gauge.cols.", e.key, " must be [top, bottom]");
                        break;
                    }
                }
                if (!ok) {
                    break;
                }
            }
            const json::node* hs = json::member(d, g, "hp_states");
            if (hs != nullptr) {
                if (hs->type != json::T_OBJ ||
                    !get_num(c, hs, "warn_at", "gauge.hp_states.warn_at", 0.0, 1.0, &t.warn_at, false) ||
                    !get_num(c, hs, "critical_at", "gauge.hp_states.critical_at", 0.0, 1.0, &t.critical_at, false) ||
                    !get_num(c, hs, "critical_glow_px", "gauge.hp_states.critical_glow_px", 0.0, 12.0,
                             &t.critical_glow_px, false)) {
                    ok = refuse(r, BAD_VALUE, "gauge.hp_states");
                    break;
                }
            }
        }
        // marks (informational: the shapes are in the icon set) and party_row (always action only): shape-checked only.
        const json::node* mk = section(c, root, "marks", false, &ok);
        if (!ok) {
            break;
        }
        if (mk != nullptr) {
            static const char* const fam[] = {"A", "B", "C"};
            uint8_t dummy = 0;
            static const char* const mark_keys[] = {"glove", "chevron", "diamond", "dot"};
            for (const char* k : mark_keys) {
                if (!get_enum(c, mk, k, "marks", fam, 3, &dummy, false)) {
                    ok = false;
                    break;
                }
            }
            if (!ok) {
                break;
            }
        }
        // status_icons
        const json::node* si = section(c, root, "status_icons", false, &ok);
        if (!ok) {
            break;
        }
        if (si != nullptr) {
            static const char* const styles[] = {"medallion", "chip", "hex_badge"};
            if (!get_enum(c, si, "style", "status_icons.style", styles, 3, &t.status_style, false)) {
                ok = false;
                break;
            }
            const json::node* names = json::member(d, si, "names");
            const json::node* colors = json::member(d, si, "colors");
            if (names != nullptr) {
                if (names->type != json::T_ARR || names->count > STATUS_ICONS_MAX) {
                    ok = refuse(r, BAD_VALUE, "status_icons.names lists at most 8 statuses");
                    break;
                }
                t.status_count = 0;
                for (const json::node* e = json::element(d, names, 0); e != nullptr && ok; e = json::next(d, e)) {
                    const char* s = json::str_of(d, e);
                    const uint8_t bit = s != nullptr ? status_bit_of(s, e->str_len) : 0xFF;
                    if (bit == 0xFF) {
                        ok = refuse(r, BAD_VALUE, "status_icons.names: not a status: ", s != nullptr ? s : "?");
                        break;
                    }
                    for (uint32_t q = 0; q < t.status_count; ++q) {
                        if (t.status_bit[q] == bit) {
                            ok = refuse(r, BAD_VALUE, "status_icons.names: a status twice");
                        }
                    }
                    if (!ok) {
                        break;
                    }
                    uint32_t rgb = rgba(200, 200, 200, 255);
                    if (colors != nullptr && !get_rgba(c, colors, s, "status_icons.colors", false, &rgb, false)) {
                        ok = false;
                        break;
                    }
                    t.status_bit[t.status_count] = bit;
                    t.status_rgb[t.status_count] = rgb;
                    ++t.status_count;
                }
                if (!ok) {
                    break;
                }
            }
        }
        // recipes (after status_icons: a {name} layer resolves per named status)
        const json::node* rc = section(c, root, "recipes", false, &ok);
        if (!ok) {
            break;
        }
        if (rc != nullptr) {
            struct rk {
                const char* key;
                const char* field;
                recipe* v;
            };
            const rk list[] = {{"glove", "recipes.glove", &t.glove_r},
                               {"chevron", "recipes.chevron", &t.chevron_r},
                               {"seat_diamond", "recipes.seat_diamond", &t.diamond_r},
                               {"dot", "recipes.dot", &t.dot_r},
                               {"status_icon", "recipes.status_icon", &t.status_r}};
            for (const rk& e : list) {
                const json::node* arr = json::member(d, rc, e.key);
                if (arr != nullptr && !read_recipe(c, arr, e.field, icon_names, icons, t, e.v)) {
                    ok = false;
                    break;
                }
            }
            if (!ok) {
                break;
            }
        }
        // {name} layers (given or inherited) for this theme's status list, by status name.
        recipe* const all[] = {&t.glove_r, &t.chevron_r, &t.diamond_r, &t.dot_r, &t.status_r};
        for (recipe* rp : all) {
            for (uint32_t k = 0; k < rp->n; ++k) {
                if (rp->l[k].tmpl == GT_NAME) {
                    resolve_names(rp->l[k], icon_names, icons, t);
                }
            }
        }
        // Every recipe (given or inherited) names icons this theme has.
        if (!recipe_complete(t.glove_r, 1) || !recipe_complete(t.chevron_r, 4) || !recipe_complete(t.diamond_r, 1) ||
            !recipe_complete(t.dot_r, 1) || !recipe_complete(t.status_r, 1)) {
            ok = refuse(r, BAD_ICONS, "a mark recipe names an icon the theme does not have");
            break;
        }
        // motion (the carousel, THEME-FORMAT.md 2): speed 10..120 units/s, hold 0..5000 ms, fade 0..40 units, marquee_on
        // true/false; a key left out (or the whole section) takes the engine default 40 / 1000 / 12 / true; an unknown
        // key is refused.
        motion_defaults(&t);
        const json::node* mo = section(c, root, "motion", false, &ok);
        if (!ok) {
            break;
        }
        if (mo != nullptr) {
            static const char* const keys[] = {"marquee_speed", "marquee_hold_ms", "marquee_fade", "marquee_on"};
            float hold = static_cast<float>(t.marquee_hold_ms);
            if (!only_keys(c, mo, "motion", keys, 4, "marquee_speed, marquee_hold_ms, marquee_fade, marquee_on") ||
                !get_num(c, mo, "marquee_speed", "motion.marquee_speed", MARQUEE_SPEED_MIN, MARQUEE_SPEED_MAX,
                         &t.marquee_speed, false) ||
                !get_num(c, mo, "marquee_hold_ms", "motion.marquee_hold_ms", 0.0, MARQUEE_HOLD_MAX_MS, &hold, false) ||
                !get_num(c, mo, "marquee_fade", "motion.marquee_fade", 0.0, MARQUEE_FADE_MAX, &t.marquee_fade, false) ||
                !get_bool(c, mo, "marquee_on", "motion.marquee_on", &t.marquee_on)) {
                ok = false;
                break;
            }
            t.marquee_hold_ms = static_cast<uint32_t>(hold + 0.5f);
        }
    } while (false);
    a.drop();
    if (!ok) {
        return false;
    }
    // The font's icon names and small-size tracking come with the tokens.
    for (uint32_t k = 0; k < font::ICON_NAMED; ++k) {
        const char* nm = font::icon_text(k);
        uint32_t nl = 0;
        while (nm[nl] != '\0') {
            ++nl;
        }
        font->icon_id[k] = icon_index(icon_names, icons, nm, nl);
    }
    for (uint32_t k = 0; k < font::ICONS_REQUIRED; ++k) {
        if (font->icon_id[k] == font::ICON_NONE) {
            return refuse(r, BAD_ICONS, "the icon set lacks ", font::icon_text(k));
        }
    }
    font->small_below_px = t.small_below_px;
    font->small_tracking_em = t.small_tracking_em;
    resolve(&t);
    *out = t;
    return true;
}

// ---- slots ---------------------------------------------------------------------------------------------------------------
slot* make_builtin(uint32_t k, report* r)
{
    if (r != nullptr) {
        *r = report{};
    }
    const builtin::theme_data& b = builtin::get(k);
    slot* s = static_cast<slot*>(alloc(sizeof(slot)));
    if (s == nullptr) {
        refuse(r, NO_MEMORY, b.id);
        return nullptr;
    }
    s->f = *b.font;
    s->builtin = static_cast<uint8_t>(1 + (k < builtin::COUNT ? k : 0));
    const uint64_t size = static_cast<uint64_t>(s->f.atlas_w) * s->f.atlas_h;
    s->block = static_cast<uint8_t*>(alloc(size));
    s->block_size = size;
    // The built-in's icon names from its font.inc ids (the engine's names it has).
    const char* names[font::ICONS_MAX] = {};
    for (uint32_t n = 0; n < font::ICON_NAMED; ++n) {
        if (s->f.icon_id[n] != font::ICON_NONE && s->f.icon_id[n] < font::ICONS_MAX) {
            names[s->f.icon_id[n]] = font::icon_text(n);
        }
    }
    tokens t;
    if (k == builtin::DEFAULT || k >= builtin::COUNT) {
        // Every other theme inherits the default's recipes and remap() reads their glyph indices as DEFAULT_ICON_NAMES
        // positions: the default's atlas must have exactly that icon order (only a broken build differs).
        bool order = s->f.icons == DEFAULT_ICON_COUNT;
        for (uint32_t n = 0; n < DEFAULT_ICON_COUNT && order; ++n) {
            const char* want = DEFAULT_ICON_NAMES[n];
            order = icon_index(names, s->f.icons, want, font::length(want, GLYPH_NAME_MAX + 1)) == n;
        }
        if (!order) {
            refuse(r, BAD_ICONS, b.id, ": the default's icon order is not DEFAULT_ICON_NAMES");
            free_slot(s);
            return nullptr;
        }
        base_tokens(&t);
    } else {
        t = default_tokens();
    }
    if (s->block == nullptr || !font::decode_atlas(s->f, s->block, static_cast<uint32_t>(size))) {
        refuse(r, s->block == nullptr ? NO_MEMORY : BAD_ATLAS, b.id);
        free_slot(s);
        return nullptr;
    }
    if (!read_json(reinterpret_cast<const char*>(b.json), b.json_len, b.id, names, s->f.icons, &s->f, &t, r) ||
        !font::tables_valid(s->f)) {
        refuse(r, BAD_ATLAS, b.id);
        free_slot(s);
        return nullptr;
    }
    s->f.atlas = s->block;
    s->t = t;
    uint32_t n = 0;
    for (; n < 64 && s->f.digest[n] != '\0'; ++n) {
        s->digest[n] = s->f.digest[n];
    }
    s->digest[n] = '\0';
    s->f.digest = s->digest;
    return s;
}

slot* make_player(const wchar_t* dir, const char* id, report* r)
{
    if (r != nullptr) {
        *r = report{};
    }
    if (id == nullptr || builtin::find(id, font::length(id, ID_MAX + 1)) != builtin::COUNT) {
        refuse(r, BUILTIN_ID, "the folder has a built-in theme's id");
        return nullptr;
    }
    wchar_t path[1024];
    uint8_t* json_text = nullptr;
    uint32_t json_len = 0;
    uint8_t* atlas_file = nullptr;
    uint32_t atlas_len = 0;
    uint8_t* icons_text = nullptr;
    uint32_t icons_len = 0;
    slot* s = nullptr;
    arena a = {};
    const char* names[font::ICONS_MAX] = {};
    uint32_t icon_count = 0;
    bool ok = false;
    do {
        // theme.json
        if (!join(dir, "theme.json", path, 1024)) {
            refuse(r, READ_FAILED, "theme.json: path too long");
            break;
        }
        uint32_t e = read_file(path, JSON_BYTES_MAX, &json_text, &json_len);
        if (e != OK) {
            refuse(r, e, "theme.json");
            break;
        }
        // The icon set: icons.json when theme.json names one (inside the folder), else the converter's built-in order.
        if (!a.make(json_len / 2 + 64, json_len + 64)) {
            refuse(r, NO_MEMORY, "theme.json");
            break;
        }
        json::doc d = a.doc();
        if (!json::parse(d, reinterpret_cast<const char*>(json_text), json_len, 16)) {
            refuse(r, BAD_JSON, "theme.json: ", d.error);
            break;
        }
        const json::node* ic = json::member(d, json::root(d), "icons");
        if (ic != nullptr) {
            const char* file = json::str_of(d, json::member(d, ic, "file"));
            if (ic->type != json::T_OBJ || file == nullptr) {
                refuse(r, BAD_VALUE, "icons.file");
                break;
            }
            if (!json_suffix(file) || !join(dir, file, path, 1024)) {
                refuse(r, BAD_VALUE, "icons.file must be a plain .json path inside the theme folder");
                break;
            }
            e = read_file(path, ICONS_JSON_BYTES_MAX, &icons_text, &icons_len);
            if (e != OK) {
                refuse(r, e, "icons.json");
                break;
            }
            arena ia = {};
            if (!ia.make(icons_len / 2 + 64, icons_len + 64)) {
                ia.drop();
                refuse(r, NO_MEMORY, "icons.json");
                break;
            }
            json::doc idoc = ia.doc();
            bool iok = json::parse(idoc, reinterpret_cast<const char*>(icons_text), icons_len, 16);
            const json::node* list = iok ? json::member(idoc, json::root(idoc), "icons") : nullptr;
            if (!iok) {
                refuse(r, BAD_JSON, "icons.json: ", idoc.error);
            } else if (!json::str_is(idoc, json::member(idoc, json::root(idoc), "format"), "ilc-icons") || list == nullptr ||
                       list->type != json::T_ARR || list->count == 0 || list->count > font::ICONS_MAX) {
                iok = refuse(r, BAD_ICONS, "icons.json: not an ilc-icons list");
            } else {
                // Names copied into the tail of the icons text buffer's own memory is not possible; keep them in a block.
                static_assert(font::ICONS_MAX * 41 < 16384, "name block");
                char* block = static_cast<char*>(alloc(16384));
                if (block == nullptr) {
                    iok = refuse(r, NO_MEMORY, "icons.json");
                } else {
                    uint32_t used = 0;
                    for (const json::node* it = json::element(idoc, list, 0); it != nullptr && iok; it = json::next(idoc, it)) {
                        const json::node* nn = json::member(idoc, it, "name");
                        const char* nm = json::str_of(idoc, nn);
                        if (nm == nullptr || nn->str_len == 0 || nn->str_len > 40) {
                            iok = refuse(r, BAD_ICONS, "icons.json: an icon without a name");
                            break;
                        }
                        for (uint32_t q = 0; q < nn->str_len; ++q) {
                            const uint8_t ch = static_cast<uint8_t>(nm[q]);
                            if (!((ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '_')) {
                                iok = refuse(r, BAD_ICONS, "icons.json: names are lower-case letters, digits and '_'");
                                break;
                            }
                        }
                        if (!iok) {
                            break;
                        }
                        if (icon_index(names, icon_count, nm, nn->str_len) != font::ICON_NONE) {
                            iok = refuse(r, BAD_ICONS, "icons.json: a name twice: ", nm);
                            break;
                        }
                        char* dst = block + used;
                        for (uint32_t q = 0; q < nn->str_len; ++q) {
                            dst[q] = nm[q];
                        }
                        dst[nn->str_len] = '\0';
                        used += nn->str_len + 1;
                        names[icon_count++] = dst;
                    }
                    if (!iok) {
                        release(block);
                    } else {
                        // The block lives until the slot is built (released below with the other buffers).
                        icons_len = 0;
                        release(icons_text);
                        icons_text = reinterpret_cast<uint8_t*>(block);
                    }
                }
            }
            ia.drop();
            if (!iok) {
                break;
            }
        } else {
            for (uint32_t k = 0; k < DEFAULT_ICON_COUNT; ++k) {
                names[k] = DEFAULT_ICON_NAMES[k];
            }
            icon_count = DEFAULT_ICON_COUNT;
        }
        // theme.atlas
        if (!join(dir, "atlas/theme.atlas", path, 1024)) {
            refuse(r, READ_FAILED, "theme.atlas: path too long");
            break;
        }
        e = read_file(path, ATLAS_FILE_MAX, &atlas_file, &atlas_len);
        if (e != OK) {
            refuse(r, e, "atlas/theme.atlas");
            break;
        }
        const uint8_t* p = atlas_file;
        if (atlas_len < ATLAS_HEADER || p[0] != 'I' || p[1] != 'L' || p[2] != 'C' || p[3] != 'A') {
            refuse(r, BAD_ATLAS, "theme.atlas: not an ILCA file");
            break;
        }
        const uint32_t version = u16_at(p + 4);
        const uint32_t faces = u16_at(p + 6);
        const uint32_t icons = u16_at(p + 8);
        const uint32_t em = u16_at(p + 10);
        const uint32_t spread = u16_at(p + 12);
        const uint32_t aw = u16_at(p + 14);
        const uint32_t ah = u16_at(p + 16);
        const uint32_t glyphs = u32_at(p + 18);
        const uint32_t kerns = u32_at(p + 22);
        const uint32_t rle = u32_at(p + 26);
        if (version != 2) {
            refuse(r, BAD_ATLAS, "theme.atlas: version is not 2");
            break;
        }
        if (faces == 0 || faces > font::TEXT_FACES_MAX || icons == 0 || icons > font::ICONS_MAX ||
            glyphs > font::GLYPHS_MAX || kerns > font::KERNS_MAX || aw == 0 || ah == 0 ||
            static_cast<uint64_t>(aw) * ah > font::ATLAS_BYTES_MAX || rle > 2ull * aw * ah) {
            refuse(r, BAD_ATLAS, "theme.atlas: a count or size past the limits");
            break;
        }
        const uint64_t want = ATLAS_HEADER + 8ull * (faces + 1) + 24ull * glyphs + 16ull * kerns + rle;
        if (want != atlas_len) {
            refuse(r, BAD_ATLAS, "theme.atlas: the file size does not match its header");
            break;
        }
        if (icons != icon_count) {
            refuse(r, BAD_ICONS, "the atlas and the icon set differ in their icon count");
            break;
        }
        // One block: the decoded atlas, then the rows, kerning and metrics (aligned copies of the file's).
        const uint64_t raw = static_cast<uint64_t>(aw) * ah;
        const uint64_t off_rows = (raw + 15) & ~15ull;
        const uint64_t off_kerns = off_rows + 24ull * glyphs;
        const uint64_t off_metrics = off_kerns + 16ull * kerns;
        const uint64_t total = off_metrics + 8ull * (faces + 1);
        s = static_cast<slot*>(alloc(sizeof(slot)));
        if (s == nullptr) {
            refuse(r, NO_MEMORY, "slot");
            break;
        }
        s->block = static_cast<uint8_t*>(alloc(total));
        s->block_size = total;
        if (s->block == nullptr) {
            refuse(r, NO_MEMORY, "atlas");
            break;
        }
        font::metric* metrics = reinterpret_cast<font::metric*>(s->block + off_metrics);
        font::glyph_row* rows = reinterpret_cast<font::glyph_row*>(s->block + off_rows);
        font::kern_row* kr = reinterpret_cast<font::kern_row*>(s->block + off_kerns);
        const uint8_t* q = p + ATLAS_HEADER;
        for (uint32_t k = 0; k <= faces; ++k, q += 8) {
            metrics[k].ascent64 = static_cast<int32_t>(u32_at(q));
            metrics[k].descent64 = static_cast<int32_t>(u32_at(q + 4));
        }
        bool pads = true;
        for (uint32_t k = 0; k < glyphs; ++k, q += 24) {
            font::glyph_row& g = rows[k];
            g.code = u32_at(q);
            g.face = q[4];
            pads = pads && q[5] == 0 && q[6] == 0 && q[7] == 0;
            g.g.x = u16_at(q + 8);
            g.g.y = u16_at(q + 10);
            g.g.w = u16_at(q + 12);
            g.g.h = u16_at(q + 14);
            g.g.bx = static_cast<int16_t>(u16_at(q + 16));
            g.g.by = static_cast<int16_t>(u16_at(q + 18));
            g.g.advance64 = static_cast<int32_t>(u32_at(q + 20));
        }
        for (uint32_t k = 0; k < kerns; ++k, q += 16) {
            font::kern_row& g = kr[k];
            g.face = q[0];
            pads = pads && q[1] == 0 && q[2] == 0 && q[3] == 0;
            g.left = u32_at(q + 4);
            g.right = u32_at(q + 8);
            g.k64 = static_cast<int32_t>(u32_at(q + 12));
        }
        if (!pads) {
            refuse(r, BAD_ATLAS, "theme.atlas: reserved bytes are not zero");
            break;
        }
        font::set f = {};
        f.rows = rows;
        f.row_count = glyphs;
        f.kerns = kr;
        f.kern_count = kerns;
        f.metrics = metrics;
        f.faces = faces;
        f.icons = icons;
        f.em = em;
        f.spread = spread;
        f.atlas_w = aw;
        f.atlas_h = ah;
        f.rle = q;
        f.rle_size = rle;
        f.role_face[0] = 0;
        f.role_face[1] = faces > 1 ? 1 : 0;
        f.role_face[2] = 0;
        for (uint16_t& v : f.icon_id) {
            v = font::ICON_NONE;
        }
        tokens t = default_tokens();
        if (!read_json(reinterpret_cast<const char*>(json_text), json_len, id, names, icon_count, &f, &t, r)) {
            break;
        }
        if (!font::tables_valid(f)) {
            refuse(r, BAD_ATLAS, "theme.atlas: a glyph or kerning row is out of order or outside the atlas");
            break;
        }
        // The body and title faces carry every printable ASCII character (the converter's rule).
        bool ascii = true;
        for (uint32_t role = font::FACE_BODY; role <= font::FACE_TITLE && ascii; ++role) {
            for (uint32_t ch = 32; ch < 127 && ascii; ++ch) {
                ascii = font::find_in(f, role, ch) != nullptr;   // never the active set: the render thread uses it
            }
        }
        if (!ascii) {
            refuse(r, BAD_ATLAS, "the body or title face lacks a printable ASCII character");
            break;
        }
        if (!font::decode_atlas(f, s->block, static_cast<uint32_t>(raw))) {
            refuse(r, BAD_ATLAS, "theme.atlas: the run-length data does not decode to the atlas size");
            break;
        }
        uint8_t got[32];
        compute_digest(f, s->block, got);
        bool same_digest = true;
        for (uint32_t k = 0; k < 32; ++k) {
            same_digest = same_digest && got[k] == p[30 + k];
        }
        if (!same_digest) {
            refuse(r, BAD_DIGEST, "theme.atlas");
            break;
        }
        digest_hex(got, s->digest);
        f.rle = nullptr;   // the file buffer is released below
        f.rle_size = 0;
        f.atlas = s->block;
        f.digest = s->digest;
        s->f = f;
        s->t = t;
        s->builtin = 0;
        ok = true;
    } while (false);
    a.drop();
    release(json_text);
    release(atlas_file);
    release(icons_text);
    if (!ok) {
        free_slot(s);
        return nullptr;
    }
    return s;
}

void free_slot(slot* s)
{
    if (s == nullptr) {
        return;
    }
    release(s->block);
    release(s);
}

const tokens* tokens_of(const slot* s) { return s != nullptr ? &s->t : nullptr; }
const font::set* font_of(const slot* s) { return s != nullptr ? &s->f : nullptr; }

}  // namespace ilc::overlay::theme
