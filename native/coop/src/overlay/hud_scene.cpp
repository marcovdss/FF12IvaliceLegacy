// Ivalice Legacy co-op overlay: the HUD elements (see hud_scene.h). Pure: reads only its arguments and the active theme,
// no game memory, no C runtime call; the module runs it on the render thread inside the fault guard, the host tests call
// it directly.
#include "overlay/hud_scene.h"

#include "hud/snapshot_facts.h"
#include "overlay/font.h"
#include "overlay/marks.h"
#include "overlay/marquee.h"
#include "overlay/theme.h"

namespace ilc::overlay::hud_scene {
namespace {

namespace bl = hud::battle_log;
namespace ti = hud::target_info;
namespace pr = hud::party_rows;
namespace md = hud::menus_draw;
using font::FACE_BODY;
using font::FACE_NUMERALS;
using font::FACE_TITLE;

// Element sizes the theme has no field for (authoring units). Labels are the title face (small caps) with the theme's
// label tracking.
constexpr float LABEL_EM = 15.0f;            // Lv, MP, Weak / Half / Null, Steal, tags
constexpr float TEXT_EM = 19.0f;             // Target Info's lines
constexpr float NUMERAL_EM = 14.0f;          // the numeral in a seat diamond
constexpr float DIAMOND = 22.0f;             // a seat diamond's box
constexpr float STATUS_BOX = 18.0f;          // a party row's status icon
constexpr float TI_STATUS_BOX = 22.0f;       // Target Info's status icon
constexpr float ELEMENT_BOX = 16.0f;
constexpr float LOG_DOT = 12.0f;
constexpr float TI_EDGE_W = 3.0f;
constexpr float GAP_K = 0.35f;               // HUD text: extra advance per glyph = GAP_K x the outline (review 4)
constexpr float LOW_HP_TROUGH = 0.6f;        // the critical glow's pulse floor
constexpr float WORD_STEP_DIM = 0.35f;
constexpr const char* DOT_SEP = "\xC2\xB7";  // U+00B7, between the parts of a line
constexpr const char* RARE_MARK = "\xE2\x97\x86";   // U+25C6, before a rare drop

// A status token: the stand-in for a status the theme has no icon for (a small rounded square in the status's colour
// with its short mark). Order = the status bit order.
struct status_token {
    const char* mark;
    uint32_t colour;
};
constexpr status_token STATUS_TOKENS[32] = {
    {"KO", rgba(120, 30, 30, 255)},  {"St", rgba(120, 120, 120, 255)}, {"Pt", rgba(150, 150, 140, 255)},
    {"Sp", rgba(110, 80, 160, 255)}, {"Sl", rgba(90, 90, 150, 255)},   {"Cf", rgba(190, 110, 190, 255)},
    {"Dm", rgba(70, 40, 70, 255)},   {"Bl", rgba(60, 60, 60, 255)},    {"Ps", rgba(110, 160, 60, 255)},
    {"Si", rgba(90, 120, 170, 255)}, {"Sa", rgba(150, 90, 60, 255)},   {"O", rgba(91, 74, 42, 255)},
    {"Rv", rgba(160, 60, 160, 255)}, {"Di", rgba(140, 100, 60, 255)},  {"Im", rgba(130, 110, 80, 255)},
    {"S", rgba(90, 111, 208, 255)},  {"Ds", rgba(100, 120, 60, 255)},  {"L", rgba(201, 162, 58, 255)},
    {"P", rgba(59, 120, 201, 255)},  {"Sh", rgba(140, 90, 200, 255)},  {"H", rgba(201, 162, 58, 255)},
    {"Bv", rgba(200, 80, 60, 255)},  {"Fa", rgba(90, 140, 220, 255)},  {"Rf", rgba(180, 200, 230, 255)},
    {"Iv", rgba(150, 170, 190, 255)},{"R", rgba(58, 168, 106, 255)},   {"Fl", rgba(150, 200, 230, 255)},
    {"Bk", rgba(210, 60, 50, 255)},  {"Bu", rgba(110, 190, 230, 255)}, {"HC", rgba(220, 70, 60, 255)},
    {"Lb", rgba(242, 220, 138, 255)},{"X", rgba(40, 40, 60, 255)},
};
// An element token (the element weakness row), bit order fire .. dark.
struct element_token {
    const char* name;
    uint32_t colour;
};
constexpr element_token ELEMENT_TOKENS[8] = {
    {"Fire", rgba(255, 122, 58, 255)},  {"Thunder", rgba(255, 228, 90, 255)}, {"Ice", rgba(143, 220, 255, 255)},
    {"Earth", rgba(196, 160, 90, 255)}, {"Water", rgba(80, 150, 255, 255)},   {"Wind", rgba(120, 230, 150, 255)},
    {"Holy", rgba(255, 250, 220, 255)}, {"Dark", rgba(150, 110, 200, 255)},
};

// A short text built in place (no snprintf).
struct line {
    char s[96];
    uint32_t n = 0;
    line() { s[0] = '\0'; }
    line& t(const char* text, uint32_t cap = 96)
    {
        for (uint32_t k = 0; text != nullptr && k < cap && text[k] != '\0' && n + 1 < sizeof(s); ++k) {
            s[n++] = text[k];
        }
        s[n] = '\0';
        return *this;
    }
    line& u(uint64_t v)
    {
        char d[24];
        uint32_t k = 0;
        do {
            d[k++] = static_cast<char>('0' + v % 10);
            v /= 10;
        } while (v != 0 && k < sizeof(d));
        while (k > 0 && n + 1 < sizeof(s)) {
            s[n++] = d[--k];
        }
        s[n] = '\0';
        return *this;
    }
};

// An element drawn whole or not at all: rolled back if the batch refused a quad or the budget was passed.
struct scope {
    batch& b;
    uint32_t start;
    uint32_t refused;
    uint32_t budget;
    scope(batch& bb, uint32_t limit) : b(bb), start(bb.count()), refused(bb.refused()), budget(limit) {}
    bool fits() const { return b.refused() == refused && b.count() - start <= budget; }
    bool close()
    {
        if (!fits()) {
            b.truncate(start);
            return false;
        }
        return b.count() > start;
    }
};

uint32_t light(uint32_t c, float t)
{
    return mix(c, rgba(255, 255, 255, alpha_of(c)), t);
}

// HUD text: the theme's style with the outline gap (glyphs drawn on the world keep their outlines apart).
text_style hud(const text_style& s)
{
    text_style h = s;
    h.gap = theme::active().outline * GAP_K;
    return h;
}
text_style hud(uint32_t core)
{
    text_style h = hud(theme::active().row);
    h.core = core;
    return h;
}
// A title-face label (small caps) with the theme's header tracking (text.tracking.header_em).
text_style label(const text_style& s)
{
    text_style h = hud(s);
    h.tracking = theme::active().header_track;
    return h;
}

// The seat diamond with its numeral centred in it.
void seat_mark(batch& b, float x, float y, float size, uint32_t colour, uint32_t seat, float numeral_em)
{
    marks::diamond(b, x, y, size, colour);
    char num[2] = {static_cast<char>('0' + seat), '\0'};
    const float lh = font::line_height(FACE_BODY, numeral_em);
    text(b, FACE_BODY, x + size * 0.5f, y + (size - lh) * 0.5f, numeral_em, num, 1, theme::active().numeral, 0.0f, 2);
}

// A status icon (the theme's own, else the stand-in token) and its seconds; returns the width used.
float draw_status(batch& b, float x, float y, uint32_t id, int32_t seconds, bool immune, float size)
{
    const theme::tokens& T = theme::active();
    const float a0 = b.alpha;
    if (immune) {
        b.alpha = a0 * 0.6f;
    }
    if (!marks::status_icon(b, x, y, size, id & 31u)) {
        const status_token& tok = STATUS_TOKENS[id & 31u];
        const uint32_t c = immune ? mix(tok.colour, rgba(110, 110, 110, 255), 0.7f) : tok.colour;
        glass(b, x, y, size, size, light(c, 0.15f), mix(c, rgba(0, 0, 0, 255), 0.25f), T.status_token_rim, 3.0f);
        const float lh = font::line_height(FACE_BODY, 12.0f);
        text(b, FACE_BODY, x + size * 0.5f, y + (size - lh) * 0.5f, 12.0f, tok.mark, 2, T.status_mark, size, 2);
    }
    float w = size;
    if (seconds >= 0) {
        line t;
        t.u(static_cast<uint32_t>(seconds));
        const float tlh = font::line_height(FACE_BODY, 17.0f);
        w += 2.0f + text(b, FACE_BODY, x + size + 2.0f, y + (size - tlh) * 0.5f, 17.0f, t.s, sizeof(t.s), hud(T.row));
    }
    if (immune) {
        solid(b, x - 1.0f, y + size * 0.5f, w + 2.0f, 1.5f, rgba(220, 220, 220, 230));
    }
    b.alpha = a0;
    return w;
}

// An element: a small diamond in the element's colour and its name; returns the width used.
float draw_element(batch& b, float x, float y, uint32_t element, float em)
{
    const element_token& tok = ELEMENT_TOKENS[element & 7u];
    const float lh = font::line_height(FACE_BODY, em);
    const float s = ELEMENT_BOX * 0.7f;
    text_style st = hud(theme::active().row);
    st.core = tok.colour;
    text(b, FACE_BODY, x + s * 0.5f, y + (lh - font::line_height(FACE_BODY, s * 1.3f)) * 0.5f, s * 1.3f, RARE_MARK, 3,
         st, 0.0f, 2);
    return s + 4.0f + text(b, FACE_BODY, x + s + 4.0f, y, em, tok.name, 16, hud(theme::active().row));
}

// A worn-steps counter: `total` diamonds, the first `worn` full, the rest dim (Guard, Tired).
float steps(batch& b, float x, float y, float em, uint32_t worn, uint32_t total, const text_style& st)
{
    const float size = em * 0.62f;
    const float lh = font::line_height(FACE_BODY, em);
    const float a0 = b.alpha;
    float w = 0.0f;
    for (uint32_t k = 0; k < total; ++k) {
        b.alpha = a0 * (k < worn ? 1.0f : WORD_STEP_DIM);
        icon(b, font::ICON_DIAMOND, x + w, y + (lh - size) * 0.5f, size, st);
        w += size + 1.0f;
    }
    b.alpha = a0;
    return w;
}

// The separator between the parts of a line (a middle dot); returns the width used.
float separator(batch& b, float x, float y, float em, const text_style& st)
{
    return text(b, FACE_BODY, x + 6.0f, y, em, DOT_SEP, 2, st) + 12.0f;
}

// The corner header: the title (title face, text.tracking.header_em) and a thin rule fading away from its side.
void corner_header(batch& b, float x, float y, float w, const char* title, bool right)
{
    const theme::tokens& T = theme::active();
    text_style st = hud(T.hdr);
    st.tracking = T.header_track;
    text(b, FACE_TITLE, right ? x + w : x, y, T.panel_head, title, 32, st, 0.0f, right ? 1u : 0u);
    const float ry = y + font::line_height(FACE_TITLE, T.panel_head) + 2.0f;
    if (right) {
        gradient_h(b, x, ry, w, 1.0f, set_alpha(T.rule2, 0), T.rule2);
    } else {
        gradient_h(b, x, ry, w, 1.0f, T.rule2, set_alpha(T.rule2, 0));
    }
}

const hud::hud_unit* member_of(const hud::hud_snapshot& s, uint32_t k) { return &s.members[k]; }

const feed::row* feed_row(const feed::record* f, uint16_t member)
{
    if (f == nullptr) {
        return nullptr;
    }
    for (const feed::row& r : f->rows) {
        if (r.member == member && member != hud::snapfacts::MEMBER_NONE) {
            return &r;
        }
    }
    return nullptr;
}

// Thousands grouped with a thin gap ("1 834", as the mockup); the numerals face has no comma rule of its own.
float grouped(batch& b, float x, float y, float em, uint32_t v, const text_style& st, uint32_t align)
{
    line digits;
    digits.u(v);
    if (digits.n <= 3) {
        return text(b, FACE_NUMERALS, x, y, em, digits.s, sizeof(digits.s), st, 0.0f, align);
    }
    const uint32_t head = digits.n - 3;
    const float gap = em * 0.18f;
    const float wh = font::measure(FACE_NUMERALS, em, digits.s, head, extra_of(st, em));
    const float wt = font::measure(FACE_NUMERALS, em, digits.s + head, 3, extra_of(st, em));
    const float total = wh + gap + wt;
    const float x0 = align == 1 ? x - total : (align == 2 ? x - total * 0.5f : x);
    text(b, FACE_NUMERALS, x0, y, em, digits.s, head, st);
    text(b, FACE_NUMERALS, x0 + wh + gap, y, em, digits.s + head, 3, st);
    return total;
}

// ---- the party list ----------------------------------------------------------------------------------------------------
void party_row(batch& b, const input& in, const hud::hud_unit& u, float top)
{
    const theme::tokens& T = theme::active();
    const float x0 = PARTY_X;
    const feed::row* fr = feed_row(in.feed, u.member);
    const bool seated = u.seat >= 1 && u.seat <= 4;
    const uint32_t colour = seated ? seat_rgba(in.rows, u.seat) : T.gambit_ally;
    if ((u.flags & hud::UNIT_LEADER) != 0) {
        gradient_h(b, x0 + ROW_EDGE_X, top + 1.0f, PARTY_W * 0.18f, 34.0f, set_alpha(T.leader_band, 0), T.leader_band);
        solid(b, x0 + ROW_EDGE_X + PARTY_W * 0.18f, top + 1.0f, PARTY_W * 0.64f, 34.0f, T.leader_band, false);
        gradient_h(b, x0 + ROW_EDGE_X + PARTY_W * 0.82f, top + 1.0f, PARTY_W * 0.18f, 34.0f, T.leader_band,
                   set_alpha(T.leader_band, 0));
    }
    if (seated) {
        solid(b, x0 + ROW_EDGE_X, top + 4.0f, 3.0f, 48.0f, colour);
        seat_mark(b, x0, top + 7.0f, DIAMOND, colour, u.seat, NUMERAL_EM);
    }
    if (fr != nullptr && (fr->flags & feed::RF_GAMBITS_ON) != 0) {
        text(b, FACE_TITLE, x0 + ROW_G_X, top + 6.0f, 21.0f, "G", 1, hud(T.gambit_g));
    }
    // HP big (right-aligned, the numerals face), the max small after it. Warning steps: hp_warn at half or less (number
    // and gauge), hp_critical with the pulsing glow at the game's critical state (and still at 0 HP / KO). One
    // effective max everywhere: doubled under Bubble (review 13).
    const uint32_t status = u.status_timed | u.status_lasting;
    const uint32_t max = effective_max(u.hp_max, status);
    const uint8_t step = hp_step_of(u.hp, u.hp_max, status);
    text_style hp_style = hud(T.row);
    hp_style.outline = T.outline * 1.15f;
    if (step == HP_WARNING) {
        hp_style.core = T.hp_warn;
    } else if (step == HP_DOWN) {
        hp_style.core = T.hp_critical;
    } else if (step == HP_CRIT) {
        hp_style = hud(T.low_hp);
        hp_style.outline = T.outline * 1.15f;
        const float k = static_cast<float>(alpha_of(pr::glow_rgba(in.now))) / 128.0f;   // 1 .. ~0.6
        const float pulse = LOW_HP_TROUGH + (1.0f - LOW_HP_TROUGH) * ((k - 0.6f) / 0.4f);
        hp_style.edge = with_alpha(set_alpha(T.hp_critical_glow, 255), pulse < 0.0f ? 0.0f : (pulse > 1.0f ? 1.0f : pulse));
    }
    const float hp_w = grouped(b, x0 + ROW_HP_RIGHT, top - 4.0f, T.hp_em, u.hp, hp_style, 1);
    line mx;
    mx.t("/").u(max);
    text(b, FACE_NUMERALS, x0 + ROW_MAX_X, top + 8.0f, T.max_em, mx.s, sizeof(mx.s), hud(T.party_max));
    // The name: what is left left of the HP number.
    const float name_room = (x0 + ROW_HP_RIGHT - hp_w - 10.0f) - (x0 + ROW_NAME_X);
    if (fr != nullptr && fr->name[0] != '\0') {
        marquee::text(b, FACE_BODY, x0 + ROW_NAME_X, top + 1.0f, T.party_name_em, fr->name, feed::NAME, hud(T.row),
                      name_room > 30.0f ? name_room : 30.0f, in.now, 0, true);   // an overflowing name glides
    }
    if (fr != nullptr && (fr->flags & feed::RF_MP) != 0) {
        text(b, FACE_TITLE, x0 + ROW_MP_LABEL_X, top + 12.0f, LABEL_EM, "MP", 2, label(T.mp_label));
        line mp;
        mp.u(fr->mp > 0 ? static_cast<uint32_t>(fr->mp) : 0u);
        text(b, FACE_NUMERALS, x0 + ROW_MP_RIGHT, top + 1.0f, T.mpv_em, mp.s, sizeof(mp.s), hud(T.row), 0.0f, 1);
    }
    const uint32_t ghost = fr != nullptr && fr->ghost > u.hp ? fr->ghost : u.hp;
    uint32_t fill[2] = {T.g_hp[0], T.g_hp[1]};
    if (step == HP_WARNING) {
        fill[0] = light(T.hp_warn, 0.25f);
        fill[1] = T.hp_warn;
    } else if (step >= HP_CRIT) {
        fill[0] = light(T.hp_critical, 0.25f);
        fill[1] = T.hp_critical;
    }
    const float gh = T.gauge_h < ROW_GAUGE_H_MAX ? T.gauge_h : ROW_GAUGE_H_MAX;
    marks::gauge(b, x0 + ROW_GAUGE_X, top + ROW_GAUGE_Y + (ROW_GAUGE_H_MAX - gh) * 0.5f - 2.0f, ROW_GAUGE_W, gh, u.hp,
                 ghost, max, fill, T.g_trail);
    // The action line: Choosing... (the seat's menu is open, or Player 1's stock menu for this member), else the action.
    float x = x0 + ROW_NAME_X;
    const float ly = top + ROW_LINE_Y;
    const float llh = font::line_height(FACE_BODY, T.act_em);
    const bool choosing = (seated && seat_choosing(in.menus, u.seat)) ||
                          (fr != nullptr && (fr->flags & feed::RF_CHOOSING) != 0);
    if (choosing) {
        x += text(b, FACE_BODY, x, ly, T.act_em, "Choosing...", 12, hud(light(colour, 0.45f))) + 10.0f;
    } else if (fr != nullptr && (fr->flags & feed::RF_ACTING) != 0) {
        const char* act = fr->action_name[0] != '\0' ? fr->action_name : "Attack";
        // The action only, never its target (Marco, 2026-11-16: the target lines show who targets what).
        x += text(b, FACE_BODY, x, ly, T.act_em, act, feed::NAME, hud(T.row), 150.0f);
        if (fr->gauge != feed::GAUGE_UNKNOWN) {
            const float by = ly + (llh - ACTION_BAR_H) * 0.5f;
            marks::gauge(b, x + 6.0f, by, ACTION_BAR_W, ACTION_BAR_H, fr->gauge > 100 ? 100u : fr->gauge, 0, 100,
                         T.g_act, 0);
            x += 6.0f + ACTION_BAR_W;
        }
        x += 10.0f;
    }
    // Status icons with seconds left (beneficial first, then the soonest to run out).
    pr::status_pick picks[ROW_STATUSES];
    const uint32_t tick = in.snap->tick_scale;
    const uint32_t n = pr::pick_statuses(u, tick, picks, ROW_STATUSES);
    const float limit = x0 + ROW_WORD_X - 4.0f;
    for (uint32_t k = 0; k < n; ++k) {
        if (x + STATUS_BOX > limit) {
            break;
        }
        x += draw_status(b, x, ly + (llh - STATUS_BOX) * 0.5f, picks[k].id, picks[k].seconds, false, STATUS_BOX) + 8.0f;
    }
    // The weapon-state word, only while live (the script channel).
    if (in.states != nullptr) {
        const states::word* w = states::word_for(*in.states, u.member);
        if (w != nullptr && states::word_live(*w)) {
            float wx = x0 + ROW_WORD_X;
            const text_style word = hud(T.word);
            wx += text(b, FACE_BODY, wx, ly, T.act_em, states::word_label(w->kind), 16, word) + 6.0f;
            if (w->kind == ILC_HUD_WORD_GUARD) {
                steps(b, wx, ly, T.act_em, w->value > 4 ? 4u : w->value, 4, word);
            } else {
                line v;
                if (w->kind == ILC_HUD_WORD_FLOW) {
                    v.t("+");
                }
                v.u(w->value);
                text(b, FACE_BODY, wx, ly, T.act_em, v.s, sizeof(v.s), word);
            }
        }
    }
}

// ---- Target Info -----------------------------------------------------------------------------------------------------------
void ti_states_line(batch& b, const input& in, const ti::view& v, float x, float y)
{
    const states::foe* f = in.states != nullptr ? states::foe_for(*in.states, v.handle) : nullptr;
    if (f == nullptr) {
        return;
    }
    const theme::tokens& T = theme::active();
    const text_style state = hud(T.state);
    bool first = true;
    const auto sep = [&]() {
        if (!first) {
            x += separator(b, x, y, TEXT_EM, state);
        }
        first = false;
    };
    if ((f->flags & ILC_HUD_FOE_BROKEN) != 0) {
        sep();
        line t;
        t.t("Broken ").u(f->broken_s);
        x += text(b, FACE_BODY, x, y, TEXT_EM, t.s, sizeof(t.s), hud(T.state_hot));
    }
    if ((f->flags & ILC_HUD_FOE_STAGGERED) != 0) {
        sep();
        x += text(b, FACE_BODY, x, y, TEXT_EM, "Staggered", 10, state);
    }
    if (f->dent_pct != 0) {
        sep();
        line t;
        t.t("Defense \xE2\x88\x92").u(f->dent_pct).t("%");   // U+2212 minus
        x += text(b, FACE_BODY, x, y, TEXT_EM, t.s, sizeof(t.s), state);
    }
    if (f->tired >= 1) {
        sep();
        x += text(b, FACE_BODY, x, y, TEXT_EM, "Tired ", 6, state);
        x += steps(b, x, y, TEXT_EM, f->tired > 4 ? 4u : f->tired, f->tired > 4 ? 4u : f->tired, state);
    }
    if ((f->flags & ILC_HUD_FOE_SPOTTED) != 0) {
        sep();
        const uint32_t c = f->spotter_seat >= 1 && f->spotter_seat <= 4 ? seat_rgba(in.rows, f->spotter_seat) : 0u;
        x += text(b, FACE_BODY, x, y, TEXT_EM, "Spotted", 8, c != 0 ? hud(light(c, 0.3f)) : state);
    }
    if ((f->flags & ILC_HUD_FOE_OIL) != 0 || (f->flags & ILC_HUD_FOE_KINDLED) != 0) {
        sep();
        const bool kindled = (f->flags & ILC_HUD_FOE_KINDLED) != 0;
        x += text(b, FACE_BODY, x, y, TEXT_EM, kindled ? "Oil kindled" : "Oil", 12, kindled ? hud(T.label) : state);
    }
}

bool ti_has_states(const input& in, const ti::view& v)
{
    const states::foe* f = in.states != nullptr ? states::foe_for(*in.states, v.handle) : nullptr;
    return f != nullptr && ((f->flags & (ILC_HUD_FOE_BROKEN | ILC_HUD_FOE_STAGGERED | ILC_HUD_FOE_SPOTTED |
                                         ILC_HUD_FOE_OIL | ILC_HUD_FOE_KINDLED)) != 0 ||
                            f->dent_pct != 0 || f->tired != 0);
}

// ---- the battle log ----------------------------------------------------------------------------------------------------------
bool is_tag_tone(uint8_t tone)
{
    return tone >= bl::TONE_CRIT && tone <= bl::TONE_HOLDING;
}

// One log line at row y; returns false if the batch refused a quad.
void log_line(batch& b, const input& in, const bl::view& v, const bl::line& l, float y)
{
    const theme::tokens& T = theme::active();
    const float lh = font::line_height(FACE_BODY, T.log_em);
    const uint32_t dc = dot_rgba(v, l.dot);
    marks::dot(b, LOG_X, y + (lh - LOG_DOT) * 0.5f, LOG_DOT, dc);
    float x = LOG_X + LOG_TEXT_DX;
    if (l.dot >= 1 && l.dot <= 4) {
        char num[2] = {static_cast<char>('0' + l.dot), '\0'};
        x += text(b, FACE_BODY, x, y, T.log_em, num, 1, hud(dc)) + 6.0f;
    }
    bl::runs r;
    bl::build_runs(l, r, true);
    for (uint32_t j = 0; j < r.n && x < LOG_RIGHT - 10.0f; ++j) {
        const bool tag = is_tag_tone(r.tone[j]);
        if (tag) {
            const float ty = y + (lh - font::line_height(FACE_TITLE, LABEL_EM)) * 0.7f;
            x += text(b, FACE_TITLE, x, ty, LABEL_EM, r.text + r.start[j], r.len[j], label(T.tag), LOG_RIGHT - x);
        } else {
            x += text(b, FACE_BODY, x, y, T.log_em, r.text + r.start[j], r.len[j], hud(tone_rgba(v, r.tone[j])),
                      LOG_RIGHT - x);
        }
    }
    // The proc tag from the script channel (Sunder, Detonate, ...), on its actor's line.
    if (in.states != nullptr) {
        const uint8_t tag = states::proc_for(*in.states, l.actor, l.born_ms, in.now);
        if (tag != 0 && x < LOG_RIGHT - 40.0f) {
            text(b, FACE_TITLE, x + 10.0f, y + (lh - font::line_height(FACE_TITLE, LABEL_EM)) * 0.7f, LABEL_EM,
                 states::tag_name(tag), 16, label(T.tag), LOG_RIGHT - x - 10.0f);
        }
    }
}

}  // namespace

// ---- pure helpers ---------------------------------------------------------------------------------------------------------------
float party_row_top(uint32_t index, uint32_t rows)
{
    if (rows == 0 || index >= rows) {
        return PARTY_BOTTOM - PARTY_ROW_H;
    }
    return PARTY_BOTTOM - PARTY_ROW_H - static_cast<float>(rows - 1 - index) * PARTY_PITCH;
}

bool seat_choosing(const md::menu_view* menus, uint8_t seat)
{
    if (menus == nullptr || seat < 1 || seat > md::SEATS) {
        return false;
    }
    const uint8_t kind = menus->seats[seat - 1].kind;
    return kind == md::WK_LIST || kind == md::WK_TARGET;
}

uint32_t seat_rgba(const pr::rows_record* rows, uint8_t seat)
{
    const theme::tokens& T = theme::active();
    if (seat < 1 || seat > 4) {
        return T.gambit_ally;
    }
    if (rows != nullptr && rows->seat_colour[seat - 1] != pr::COLOUR_NONE) {
        const hud::rgba c = pr::colour_rgba(rows->seat_colour[seat - 1]);
        return from_ps2(static_cast<uint32_t>(c.r) | static_cast<uint32_t>(c.g) << 8 |
                        static_cast<uint32_t>(c.b) << 16 | 0x80u << 24);
    }
    return T.seat[seat - 1];
}

uint32_t dot_rgba(const bl::view& v, uint8_t dot)
{
    const theme::tokens& T = theme::active();
    if (dot >= 1 && dot <= 4) {
        return alpha_of(v.seat_colour[dot - 1]) != 0 ? from_ps2(v.seat_colour[dot - 1]) : T.seat[dot - 1];
    }
    switch (dot) {
    case bl::DOT_GAMBIT: return T.gambit_ally;
    case bl::DOT_FOE: return T.foe_dot;
    case bl::DOT_REWARD: return T.heal;
    default: return T.log_dot_system;
    }
}

uint32_t tone_rgba(const bl::view& v, uint8_t tone)
{
    (void)v;
    const theme::tokens& T = theme::active();
    switch (tone) {
    case bl::TONE_HURT: return T.dmg;
    case bl::TONE_HEAL: return T.heal;
    case bl::TONE_REWARD: return T.heal;
    case bl::TONE_DIM: return T.dim;
    case bl::TONE_IMPORTANT: return T.log_important;
    case bl::TONE_CRIT:
    case bl::TONE_WEAK: return T.gold;
    case bl::TONE_MISS: return T.log_miss;
    case bl::TONE_HOLDING: return T.log_holding;
    default: return T.core;   // plain text, names (the numeral carries the seat colour), damage dealt (white)
    }
}

uint32_t effective_max(uint32_t hp_max, uint32_t status)
{
    if ((status & pr::STATUS_BUBBLE) == 0) {
        return hp_max;
    }
    return hp_max > 0x7FFFFFFFu ? 0xFFFFFFFFu : 2u * hp_max;
}

uint8_t hp_step_of(uint32_t hp, uint32_t hp_max, uint32_t status)
{
    if (hp == 0 || (status & pr::STATUS_KO) != 0) {
        return HP_DOWN;
    }
    if (pr::low_hp(hp, hp_max, status)) {
        return HP_CRIT;   // the game's own critical test (HP Critical status, or below a fifth of max, Bubble doubled)
    }
    const uint32_t max = effective_max(hp_max, status);
    if (max != 0 && static_cast<double>(hp) <= static_cast<double>(max) * theme::active().warn_at) {
        return HP_WARNING;   // gauge.hp_states.warn_at (0.5) of the same effective max
    }
    return HP_NORMAL;
}

bool drop_rare(const ti::drop_line& d)
{
    return (d.flags & ti::DROP_RARE) != 0 || d.kind == ti::SPECIAL;
}

uint32_t format_u(uint32_t v, char* out, uint32_t cap)
{
    line l;
    l.u(v);
    uint32_t n = 0;
    for (; n + 1 < cap && n < l.n; ++n) {
        out[n] = l.s[n];
    }
    if (cap != 0) {
        out[n] = '\0';
    }
    return n;
}

// ---- the elements -------------------------------------------------------------------------------------------------------------
bool party_list(batch& b, const input& in, uint32_t budget)
{
    // The party list element off (review 2): nothing drawn, so nothing is stamped and the vanilla panel stays.
    if (in.party_on == 0 || in.snap == nullptr || (in.snap->head.flags & hud::SNAP_PARTY) == 0 ||
        in.snap->member_count == 0) {
        return false;
    }
    scope sc(b, budget);
    const uint32_t rows = in.snap->member_count < hud::SNAP_MEMBERS ? in.snap->member_count : hud::SNAP_MEMBERS;
    for (uint32_t k = 0; k < rows; ++k) {
        const hud::hud_unit& u = *member_of(*in.snap, k);
        if ((u.flags & hud::UNIT_PRESENT) == 0 || u.member == hud::snapfacts::MEMBER_NONE) {
            continue;
        }
        party_row(b, in, u, party_row_top(k, rows));
    }
    return sc.close();
}

bool target_info(batch& b, const input& in, uint32_t budget)
{
    if (in.ti == nullptr || (in.ti->head.flags & ti::VIEW_SHOWN) == 0) {
        return false;
    }
    const ti::view& v = *in.ti;
    const uint8_t fade = ti::fade_alpha(v, in.now);
    if (fade == 0) {
        return false;
    }
    const theme::tokens& T = theme::active();
    scope sc(b, budget);
    const float a0 = b.alpha;
    b.alpha = a0 * (static_cast<float>(fade) / 128.0f);
    const float dy = in.boss_gauge != 0 ? BOSS_DY : 0.0f;
    const float x = TI_X;
    float y = TI_TOP + dy;
    const uint32_t seat_c = alpha_of(v.seat_colour) != 0 ? from_ps2(v.seat_colour) : 0u;
    const text_style row = hud(T.row);
    corner_header(b, x, y, TI_W, "Target Info", false);
    // The badge: Libra (revealed) or Anti-Libra (the gated rows hidden), right end of the header (title face).
    text_style badge = label(T.tag);
    badge.tracking = T.header_track;
    if ((v.head.flags & ti::VIEW_ANTI_LIBRA) != 0) {
        badge.core = T.low;
        text(b, FACE_TITLE, x + TI_W, y + 3.0f, T.panel_head - 4.0f, "Anti-Libra", 12, badge, 0.0f, 1);
    } else if ((v.head.flags & ti::VIEW_LIBRA) != 0) {
        text(b, FACE_TITLE, x + TI_W, y + 3.0f, T.panel_head - 4.0f, "Libra", 6, badge, 0.0f, 1);
    }
    y += 40.0f;
    // Lv and name (red when dangerous), the tag.
    const float name_lh = font::line_height(FACE_BODY, T.target_em);
    const float lab_y = y + name_lh - font::line_height(FACE_TITLE, LABEL_EM) - 4.0f;
    float nx = x;
    if (v.level != 0) {
        nx += text(b, FACE_TITLE, nx, lab_y, LABEL_EM + 3.0f, "Lv", 3, label(T.help_st)) + 6.0f;
        line lv;
        lv.u(v.level);
        nx += text(b, FACE_NUMERALS, nx, y, T.target_em, lv.s, sizeof(lv.s), row) + 12.0f;
    }
    if ((v.head.flags & ti::VIEW_NAME) != 0) {
        text_style ns = (v.head.flags & ti::VIEW_DANGER) != 0 ? hud(T.danger) : row;
        ns.outline = T.outline * 1.15f;
        nx += marquee::text(b, FACE_BODY, nx, y, T.target_em, v.name, ti::NAME_MAX, ns, 360.0f, in.now, in.ti_since,
                            true) + 14.0f;
    }
    const states::foe* sf = in.states != nullptr ? states::foe_for(*in.states, v.handle) : nullptr;
    if (sf != nullptr) {
        const text_style tag = label(T.tag);
        if ((sf->flags & ILC_HUD_FOE_KING) != 0) {
            text(b, FACE_TITLE, nx, lab_y, LABEL_EM, "King of the Zone", 17, tag);
        } else if ((sf->flags & ILC_HUD_FOE_CROWNED) != 0) {
            line t;
            t.t("Crowned");
            if (sf->crown_element < 8) {
                t.t(" ").t(DOT_SEP).t(" ").t(ELEMENT_TOKENS[sf->crown_element].name);
            }
            text(b, FACE_TITLE, nx, lab_y, LABEL_EM, t.s, sizeof(t.s), tag);
        } else if ((sf->flags & ILC_HUD_FOE_MARK) != 0) {
            text(b, FACE_TITLE, nx, lab_y, LABEL_EM, "Mark", 5, tag);
        }
    }
    y += 40.0f;
    // HP bar with its trail; numbers and percentage need Libra.
    if ((v.head.flags & ti::VIEW_HP) != 0) {
        const float gh = T.gauge_h < 8.0f ? (T.gauge_h > 6.0f ? T.gauge_h : 6.0f) : 8.0f;
        marks::gauge(b, x, y + 8.0f, TI_BAR_W, gh, v.hp, v.ghost, v.hp_max, T.g_hp, T.g_trail);
        if ((v.head.flags & ti::VIEW_REVEAL) != 0 && v.hp_max != 0) {
            float tx = x + TI_BAR_W + 16.0f;
            tx += grouped(b, tx, y - 2.0f, TEXT_EM + 1.0f, v.hp, row, 0);
            tx += text(b, FACE_NUMERALS, tx, y - 2.0f, TEXT_EM + 1.0f, " / ", 3, row);
            tx += grouped(b, tx, y - 2.0f, TEXT_EM + 1.0f, v.hp_max, row, 0) + 14.0f;
            line pct;
            pct.u((static_cast<uint64_t>(v.hp) * 100u + v.hp_max / 2) / v.hp_max).t("%");
            text(b, FACE_NUMERALS, tx, y - 2.0f, TEXT_EM + 1.0f, pct.s, sizeof(pct.s), row);
        }
        y += TI_LINE;
    }
    // Weak / Half / Null (Libra).
    if ((v.head.flags & ti::VIEW_REVEAL) != 0 && (v.weakness != 0 || (v.head.flags & ti::VIEW_ELEMENTS_EXT) != 0)) {
        float ex = x;
        const uint8_t masks[3] = {v.weakness, v.half, v.null_};
        const char* labels[3] = {"Weak", "Half", "Null"};
        const uint32_t kinds = (v.head.flags & ti::VIEW_ELEMENTS_EXT) != 0 ? 3u : 1u;
        const float ly = y + font::line_height(FACE_BODY, TEXT_EM) - font::line_height(FACE_TITLE, LABEL_EM) - 3.0f;
        for (uint32_t k = 0; k < kinds; ++k) {
            ex += text(b, FACE_TITLE, ex, ly, LABEL_EM, labels[k], 5, label(T.label)) + 8.0f;
            if (masks[k] == 0) {
                ex += text(b, FACE_BODY, ex, y, TEXT_EM, "\xE2\x80\x93", 3, row) + 18.0f;   // U+2013
                continue;
            }
            for (uint32_t e = 0; e < 8; ++e) {
                if ((masks[k] & (1u << e)) != 0 && ex < x + TI_W - 60.0f) {
                    ex += draw_element(b, ex, y, e, TEXT_EM) + 10.0f;
                }
            }
            ex += 10.0f;
        }
        y += TI_LINE;
    }
    // Statuses with seconds (immune ones greyed and struck through).
    if (v.status_count != 0) {
        float sx = x;
        const uint32_t n = v.status_count < ti::STATUS_SLOTS ? v.status_count : ti::STATUS_SLOTS;
        for (uint32_t k = 0; k < n && sx < x + TI_W - 30.0f; ++k) {
            const bool immune = (v.status_flags[k] & ti::STATUS_IMMUNE) != 0 && (v.status_flags[k] & ti::STATUS_ACTIVE) == 0;
            const int32_t secs = (v.status_flags[k] & ti::STATUS_TIMED) != 0 && (v.head.flags & ti::VIEW_TIMERS) != 0
                                     ? static_cast<int32_t>(v.status_secs[k])
                                     : -1;
            sx += draw_status(b, sx, y + 2.0f, v.status_id[k], secs, immune, TI_STATUS_BOX) + 12.0f;
        }
        y += TI_LINE;
    }
    // The Legacy states line (always shown while live: the states the party coordinates on).
    if (ti_has_states(in, v)) {
        ti_states_line(b, in, v, x, y);
        y += TI_LINE;
    }
    // Drops (Hunter's Insight; not Libra-gated).
    if ((v.head.flags & ti::VIEW_DROPS) != 0 && v.drop_count != 0) {
        y += 6.0f;
        const float dw = text(b, FACE_TITLE, x, y, LABEL_EM, "Drops", 6, label(T.hdr));
        const float ry = y + font::line_height(FACE_TITLE, LABEL_EM) * 0.55f;
        gradient_h(b, x + dw + 8.0f, ry, TI_BAR_W - dw - 8.0f, 1.0f, with_alpha(T.rule2, 0.6f), set_alpha(T.rule2, 0));
        y += font::line_height(FACE_TITLE, LABEL_EM) + 4.0f;
        float dx = x;
        bool steal_label = false;
        const uint32_t n = v.drop_count < ti::DROP_LINES ? v.drop_count : ti::DROP_LINES;
        const float ly = y + font::line_height(FACE_BODY, TEXT_EM) - font::line_height(FACE_TITLE, LABEL_EM) - 3.0f;
        for (uint32_t k = 0; k < n && dx < x + TI_W - 40.0f; ++k) {
            const ti::drop_line& d = v.drops[k];
            if (d.kind == ti::STEAL && !steal_label) {
                dx += 10.0f;
                dx += text(b, FACE_TITLE, dx, ly, LABEL_EM, "Steal", 6, label(T.label)) + 8.0f;
                steal_label = true;
            } else if (k != 0) {
                dx += separator(b, dx, y, TEXT_EM, row);
            }
            const bool dim = (d.flags & ti::DROP_TAKEN) != 0 || d.state == ti::ITEM_UNKNOWN;
            if (drop_rare(d)) {
                // A rare drop: the U+25C6 diamond and the name in drop_rare (known or not: the slot is rare either way).
                const float a1 = b.alpha;
                b.alpha = dim ? a1 * 0.6f : a1;
                const text_style rs = hud(T.drop_rare_st);
                dx += text(b, FACE_BODY, dx, y, TEXT_EM * 0.75f, RARE_MARK, 3, rs) + 3.0f;
                dx += text(b, FACE_BODY, dx, y, TEXT_EM, d.text, ti::ITEM_TEXT, rs, 200.0f);
                b.alpha = a1;
            } else {
                dx += text(b, FACE_BODY, dx, y, TEXT_EM, d.text, ti::ITEM_TEXT, dim ? hud(T.grey) : row, 200.0f);
            }
        }
        y += TI_LINE;
    }
    // The seat edge, down the whole block.
    if (seat_c != 0) {
        solid(b, TI_EDGE_X, TI_TOP + dy, TI_EDGE_W, y - (TI_TOP + dy), seat_c);
    }
    b.alpha = a0;
    return sc.close();
}

bool battle_log(batch& b, const input& in, uint32_t budget)
{
    // The chain header replaces the native chain counter (item 5, 0.1.60): drawn whenever the chain is above 0, with or
    // without log lines (0.1.59 drew it only while the log had lines, so the blocked native counter left a gap).
    const bool chain_on = in.feed != nullptr && in.feed->chain_known != 0 && in.feed->chain != 0;
    if (in.log == nullptr && !chain_on) {
        return false;
    }
    uint8_t index[bl::EXPANDED_LINES];
    uint8_t alpha[bl::EXPANDED_LINES];
    const uint32_t n = in.log != nullptr ? bl::select(*in.log, in.now, index, alpha, bl::EXPANDED_LINES) : 0u;
    if (n == 0 && !chain_on && !bl::header_shown(*in.log, in.now)) {
        return false;
    }
    const theme::tokens& T = theme::active();
    scope sc(b, budget);
    const float dy = in.boss_gauge != 0 ? BOSS_DY : 0.0f;
    const float w = LOG_RIGHT - LOG_X;
    // "Battle Log" on the left, the chain on the right ("Chain 12 . Lv 2"), as the mockup (review 5).
    corner_header(b, LOG_X, LOG_TOP + dy, w, "Battle Log", false);
    if (chain_on) {
        line t;
        t.t("Chain ").u(in.feed->chain).t(" ").t(DOT_SEP).t(" Lv ").u(static_cast<uint32_t>(in.feed->chain_level) + 1u);
        const float cy = LOG_TOP + dy + font::line_height(FACE_TITLE, T.panel_head) -
                         font::line_height(FACE_BODY, T.log_em) + 1.0f;
        text(b, FACE_BODY, LOG_RIGHT, cy, T.log_em, t.s, sizeof(t.s), hud(T.row), 0.0f, 1);
    }
    if (!sc.fits() || n == 0) {
        return sc.close();
    }
    const bl::view& v = *in.log;
    // The lines, newest first from the bottom row up, each kept only while the element stays within its budget: an
    // expanded log too long for the budget drops its oldest lines (review 3), never the whole element. The kept lines
    // then move up to start at the first row.
    const float a0 = b.alpha;
    const uint32_t lines_from = b.count();
    const uint32_t refused = b.refused();
    uint32_t kept = 0;
    uint32_t first_kept = n;
    for (uint32_t r = n; r > 0; --r) {
        const uint32_t k = r - 1;
        if (alpha[k] == 0) {
            continue;
        }
        const uint32_t before = b.count();
        b.alpha = a0 * (static_cast<float>(alpha[k]) / 128.0f);
        log_line(b, in, v, v.lines[index[k]], LOG_LINES_Y + dy + static_cast<float>(k) * LOG_PITCH);
        if (b.refused() != refused || b.count() - sc.start > budget) {
            b.truncate(before);
            b.forgive(refused);
            break;
        }
        ++kept;
        first_kept = k;
    }
    b.alpha = a0;
    if (kept != 0 && first_kept != 0) {
        b.shift_y(lines_from, -static_cast<float>(first_kept) * LOG_PITCH);
    }
    return sc.close();
}

output compose(batch& b, const input& in)
{
    output out = {};
    const auto run = [&](uint32_t el, bool (*fn)(batch&, const input&, uint32_t), uint32_t budget) {
        const uint32_t before = b.count();
        const uint32_t refused = b.refused();
        if (fn(b, in, budget)) {
            out.drawn |= 1u << el;
        }
        if (b.refused() != refused) {
            out.refused |= 1u << el;
        }
        out.quads[el] = b.count() - before;
    };
    run(EL_LOG, battle_log, BUDGET_LOG);
    run(EL_TARGET, target_info, BUDGET_TARGET);
    run(EL_PARTY, party_list, BUDGET_PARTY);
    return out;
}

}  // namespace ilc::overlay::hud_scene
