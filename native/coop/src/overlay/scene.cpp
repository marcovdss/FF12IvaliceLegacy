// Ivalice Legacy co-op overlay: the frame's content (see scene.h). Pure: reads only its arguments, no game memory, no
// C runtime call; the module runs it on the render thread inside the fault guard, the host tests call it directly.
#include "overlay/scene.h"

#include "overlay/font.h"
#include "overlay/marks.h"
#include "overlay/marquee.h"
#include "overlay/theme.h"

namespace ilc::overlay::scene {
namespace {

namespace md = hud::menus_draw;
using font::FACE_BODY;
using font::FACE_TITLE;
using font::FACE_NUMERALS;

// The test card (engine geometry; its glass is the theme's card colours).
constexpr float CARD_W = 340.0f;
constexpr float CARD_H = 92.0f;
constexpr float CARD_MARGIN = 16.0f;
// The glove: its design box is this many times sizes.glove (the glove is about 26 of the box's 64 tall), and the box's
// empty left margin (the cuff starts 3.6 of 64 in) is taken off so glove_out measures the visible glove.
constexpr float GLOVE_BOX = 64.0f / 26.0f * 0.9f;
constexpr float GLOVE_INSET = 3.6f / 64.0f;
constexpr float GLOVE_KEYLINE = 4.0f;   // the ink layer (grow + edge) and the soft edge, kept off the slot on its left

constexpr uint32_t SEATS = md::SEATS;

// A short line built in place (no snprintf).
struct line {
    char s[112];
    uint32_t n = 0;
    line() { s[0] = '\0'; }
    line& t(const char* text)
    {
        while (text != nullptr && *text != '\0' && n + 1 < sizeof(s)) {
            s[n++] = *text++;
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
    // A value in tenths, shown with one decimal ("16.7").
    line& tenths(uint64_t v10)
    {
        u(v10 / 10);
        t(".");
        return u(v10 % 10);
    }
};

uint32_t length(const char* s, uint32_t cap)
{
    uint32_t n = 0;
    while (n < cap && s[n] != '\0') {
        ++n;
    }
    return n;
}

// A triangle wave 0..1..0 over `period_ms` (the hand's bob).
float wave(uint64_t now, uint32_t period_ms)
{
    const float p = static_cast<float>(now % period_ms) / static_cast<float>(period_ms);
    return 1.0f - (p < 0.5f ? 1.0f - 2.0f * p : 2.0f * p - 1.0f);
}

// ---- the test card ----------------------------------------------------------------------------------------------------
bool corner_card(batch& b, float x, float y, const char* label, const char* first, const char* second)
{
    const theme::tokens& T = theme::active();
    bool ok = glass(b, x, y, CARD_W, CARD_H, T.card_top, T.card_bottom, T.card_rim, 6.0f);
    ok = gradient_v(b, x + 4.0f, y + 3.0f, CARD_W - 8.0f, 12.0f, T.card_shine, set_alpha(T.card_shine, 0)) && ok;
    text(b, FACE_TITLE, x + 16.0f, y + 8.0f, 22.0f, label, 64, T.card_label, CARD_W - 32.0f);
    text(b, FACE_BODY, x + 16.0f, y + 36.0f, 20.0f, first, 96, T.row, CARD_W - 32.0f);
    text(b, FACE_BODY, x + 16.0f, y + 60.0f, 18.0f, second, 96, T.help_st, CARD_W - 32.0f);
    return ok && b.refused() == 0;
}

// ---- a seat window ----------------------------------------------------------------------------------------------------
bool same_text(const char* a, uint32_t a_cap, const char* b, uint32_t b_cap)
{
    const uint32_t n = length(a, a_cap);
    if (n == 0 || n != length(b, b_cap)) {
        return false;
    }
    for (uint32_t k = 0; k < n; ++k) {
        if (a[k] != b[k]) {
            return false;
        }
    }
    return true;
}

// The window's page title. The view's guest titles start "P2 - " (the tab names the seat now, so it is left out); the
// root page's title is the character's name (on the tab too): "Commands" there.
const char* header_of(const md::seat_view& sv, uint32_t* cap)
{
    const char* t = sv.title;
    *cap = md::TITLE_TEXT;
    if (t[0] == 'P' && t[1] >= '1' && t[1] <= '4' && t[2] == ' ' && t[3] == '-' && t[4] == ' ') {
        t += 5;
        *cap = md::TITLE_TEXT - 5;
    }
    if (sv.kind == md::WK_LIST && (sv.depth <= 1 || same_text(t, *cap, sv.name, md::ROW_TEXT))) {
        *cap = 9;
        return "Commands";
    }
    return t;
}

// A style in a colour (the theme's outline and shadow).
text_style coloured(const text_style& base, uint32_t core)
{
    text_style s = base;
    s.core = core;
    return s;
}

// The seat tab above the window's left end (theme tab.kind): A "P2 - Fran" with the seat number in the seat colour
// under a thin seat-colour accent; B the seat diamond with its number, the name and a seat-colour underline; C a
// numbered block in the seat colour and the name. The tab's height and offset are the theme's; its slot is the engine's.
void draw_tab(batch& b, uint32_t seat, const md::seat_view& sv, const rect& slot_tab, const rect& frame, float dy,
              uint32_t seat_rgba)
{
    const theme::tokens& T = theme::active();
    line num;
    num.u(seat + 1);
    line pnum;
    pnum.t("P").u(seat + 1);
    const float h = T.tab_h;
    const float y = frame.y - h + 2.0f + dy;
    const float x = slot_tab.x + T.tab_dx;
    const float pad = 10.0f;
    const bool named = sv.name[0] != '\0';
    const float em = T.tab_em;
    const float lh = font::line_height(FACE_BODY, em);
    const float ty = y + (h - lh) * 0.5f + 1.0f;
    float lead = 0.0f;   // what comes before the name
    if (T.tab_kind == theme::TAB_A) {
        lead = font::measure(FACE_BODY, em, pnum.s, sizeof(pnum.s), extra_of(T.row, em)) +
               (named ? font::measure(FACE_BODY, em, " - ", 3, extra_of(T.row, em)) : 0.0f);
    } else {
        lead = h - 6.0f + 8.0f;   // the mark (a square of the tab's height less 6) and a gap
    }
    const float room = slot_tab.w - 2.0f * pad - lead;
    float name_w = named ? font::measure(FACE_BODY, em, sv.name, md::ROW_TEXT, extra_of(T.row, em)) : 0.0f;
    if (name_w > room) {
        name_w = room > 0.0f ? room : 0.0f;
    }
    float w = 2.0f * pad + lead + name_w;
    w = w < 64.0f ? 64.0f : w;
    marks::tab(b, x, y, w, h);
    float tx = x + pad;
    if (T.tab_kind == theme::TAB_A) {
        solid(b, x + T.tab_radius * 0.75f, y + 2.0f, w - T.tab_radius * 1.5f, 3.0f, seat_rgba);
        tx += text(b, FACE_BODY, tx, ty, em, pnum.s, sizeof(pnum.s),
                   coloured(T.row, mix(seat_rgba, rgba(255, 255, 255, 255), 0.45f)));
        if (named) {
            tx += text(b, FACE_BODY, tx, ty, em, " - ", 3, T.row);
        }
    } else {
        const float m = h - 6.0f;
        const float my = y + 3.0f;
        if (T.tab_kind == theme::TAB_B) {
            marks::diamond(b, tx - 2.0f, my - 1.0f, m + 2.0f, seat_rgba);
            text(b, FACE_BODY, tx - 2.0f + (m + 2.0f) * 0.5f, my + (m - font::line_height(FACE_BODY, m * 0.62f)) * 0.5f,
                 m * 0.62f, num.s, sizeof(num.s), T.numeral, 0.0f, 2);
            solid(b, x + 6.0f, y + h - 4.0f, w - 12.0f, 2.0f, seat_rgba, false);
        } else {
            solid(b, tx, my, m, m, seat_rgba, false);
            solid(b, tx, my + m - 2.0f, m, 2.0f, set_alpha(T.ink, 90), false);
            text(b, FACE_NUMERALS, tx + m * 0.5f, my + (m - font::line_height(FACE_NUMERALS, m * 0.8f)) * 0.5f,
                 m * 0.8f, num.s, sizeof(num.s), T.numeral, 0.0f, 2);
        }
        tx += m + 8.0f;
    }
    if (named) {
        text(b, FACE_BODY, tx, ty, em, sv.name, md::ROW_TEXT, T.row, name_w > 20.0f ? name_w : 20.0f);
    }
}

void draw_rows(batch& b, const md::seat_view& sv, const slot& sl, float dy, uint32_t seat_rgba, uint64_t now,
               uint64_t focus)
{
    const theme::tokens& T = theme::active();
    const float pitch = SLOT_PITCH;
    const float rx = sl.rows.x;
    const float ry = sl.rows.y + dy;
    const float right = rx + sl.rows.w;   // values and ON end here; the chevrons sit right of it
    const float lh = font::line_height(FACE_BODY, T.row_em);
    const float vlh = font::line_height(FACE_BODY, T.value_em);
    const float arrow = 14.0f;
    for (uint32_t i = 0; i < sl.visible; ++i) {
        const uint32_t k = sl.start + i;
        if (k >= sv.row_count) {
            break;
        }
        const md::view_row& r = sv.rows[k];
        const float top = ry + static_cast<float>(i) * pitch;
        const bool grey = (r.flags & md::RF_GREY) != 0;
        if (k == sv.cursor) {
            // The focused row's bar in the seat colour (theme focus_row; off: the glove alone marks it).
            marks::focus_bar(b, sl.frame.x + 10.0f, top + 1.0f, sl.frame.w - 20.0f, pitch - 2.0f, seat_rgba);
        }
        // Right side first: the Gambits word or the value, then the name gets what is left.
        float reserved = 0.0f;
        const float vy = top + (pitch - vlh) * 0.5f;
        if ((r.flags & md::RF_TOGGLE) != 0 && (r.flags & md::RF_KNOWN) != 0) {
            const bool on = (r.flags & md::RF_ON) != 0;
            reserved = text(b, FACE_BODY, right, vy, T.value_em, on ? "ON" : "OFF", 4, on ? T.on : T.grey, 0.0f, 1) +
                       10.0f;
        } else if (r.value[0] != '\0') {
            reserved = text(b, FACE_BODY, right, vy, T.value_em, r.value, md::ROW_VALUE, grey ? T.grey : T.value, 0.0f,
                            1) + 10.0f;
        }
        // A name wider than its column: the carousel (clipped left of the value column, focused row moving).
        const float name_w = sl.rows.w - reserved;
        marquee::text(b, FACE_BODY, rx, top + (pitch - lh) * 0.5f, T.row_em, r.text, md::ROW_TEXT, grey ? T.grey : T.row,
                      name_w > 20.0f ? name_w : 20.0f, now, focus, k == sv.cursor);
    }
    // The glove over the rows (pokes out of the window's left edge, as FF12's glove does), bobbing toward the row.
    if (sv.cursor >= sl.start && sv.cursor < sl.start + sl.visible && sv.cursor < sv.row_count) {
        const float top = ry + static_cast<float>(sv.cursor - sl.start) * pitch;
        const float bob = T.glove_bob * wave(now, 800);
        // sizes.glove is the glove's height; the glove (46:26) sits in its 64-unit design box: the box is GLOVE_BOX x it.
        const float box = T.glove * GLOVE_BOX;
        // The slot geometry: the glove's ink (its keyline included) stays inside the 16-unit gap, never in the slot left.
        const float out = T.glove_out < SLOT_GAP - GLOVE_KEYLINE ? T.glove_out : SLOT_GAP - GLOVE_KEYLINE;
        marks::glove(b, sl.frame.x - out + bob - box * GLOVE_INSET, top + (pitch - box) * 0.5f, box, seat_rgba);
    }
    // Scroll chevrons in the margin right of the rows, beside the first and the last row shown.
    const float ax = right + 3.0f;
    if (sv.first + sl.start > 0) {
        marks::chevron(b, marks::DIR_UP, ax, ry + (pitch - arrow) * 0.5f, arrow);
    }
    if (static_cast<uint32_t>(sv.first) + sl.start + sl.visible < sv.total) {
        marks::chevron(b, marks::DIR_DOWN, ax, ry + static_cast<float>(sl.visible - 1) * pitch + (pitch - arrow) * 0.5f,
                       arrow);
    }
}

void draw_window(batch& b, uint32_t seat, const md::seat_view& sv, const slot& sl, uint64_t now, uint64_t focus)
{
    const theme::tokens& T = theme::active();
    const float e = ease(sv.opened_ms, now);
    const float dy = (1.0f - e) * SLOT_RISE;   // rises into its slot while it fades in
    const float base_alpha = b.alpha;
    b.alpha = base_alpha * e;
    const uint32_t seat_rgba = from_ps2(sv.colour);
    const rect& f = sl.frame;
    const float fy = f.y + dy;
    draw_tab(b, seat, sv, sl.tab, f, dy, seat_rgba);   // first: the window's rim covers its foot
    marks::window(b, f.x, fy, f.w, f.h);
    // Header: the page title on the left (the theme's page face and tracking); on a target page the group on the right,
    // with chevrons for LB/RB when another group has a candidate.
    const uint32_t pface = T.page_face;
    text_style hst = T.header_st;
    hst.tracking = T.title_track;
    const float arrow = 14.0f;
    const float hy = sl.header.y + dy + (sl.header.h - font::line_height(pface, T.header_em)) * 0.5f;
    const float hr = sl.header.x + sl.header.w;
    float right_w = 0.0f;
    if (sv.kind == md::WK_TARGET) {
        const char* g = md::group_name(sv.group);
        const bool more = (sv.groups & ~(1u << sv.group)) != 0;
        const float ay = sl.header.y + dy + (sl.header.h - arrow) * 0.5f;
        const float gy = sl.header.y + dy + (sl.header.h - font::line_height(FACE_BODY, T.header_em)) * 0.5f;
        float x = hr;
        if (more) {
            x -= arrow;
            marks::chevron(b, marks::DIR_RIGHT, x, ay, arrow);
            x -= 4.0f;
        }
        if (g != nullptr) {
            x -= text(b, FACE_BODY, x, gy, T.header_em, g, md::TITLE_TEXT, T.header_st, 0.0f, 1);
        }
        if (more) {
            x -= 4.0f + arrow;
            marks::chevron(b, marks::DIR_LEFT, x, ay, arrow);
        }
        right_w = hr - x + 10.0f;
    }
    uint32_t cap = 0;
    const char* title = header_of(sv, &cap);
    const float title_w = sl.header.w - right_w;
    text(b, pface, sl.header.x, hy, T.header_em, title, cap, hst, title_w > 40.0f ? title_w : 40.0f);
    draw_rows(b, sv, sl, dy, seat_rgba, now, focus);
    // Footer: a thin rule, then the help line.
    const float fty = sl.footer.y + dy;
    solid(b, sl.footer.x, fty, sl.footer.w, 1.0f, T.rule);
    if (sv.help[0] != '\0') {
        // The help line in the carousel (item 8, 0.1.60: "Cause magicks to be refle..." was cut): a line wider than the
        // footer glides side to side as the focused row's name does, restarting when the cursor arrives on a row.
        marquee::text(b, FACE_BODY, sl.footer.x + 2.0f,
                      fty + (sl.footer.h - font::line_height(FACE_BODY, T.help_em)) * 0.5f + 1.0f, T.help_em, sv.help,
                      md::HELP_TEXT, T.help_st, sl.footer.w - 4.0f, now, focus, true);
    }
    b.alpha = base_alpha;
}

}  // namespace

bool slot_layout(uint32_t seat, const md::seat_view& sv, slot* out)
{
    *out = slot{};
    if (seat >= SEATS) {
        return false;
    }
    const float x = SLOT_LEFT + static_cast<float>(seat) * (SLOT_W + SLOT_GAP);
    const float y = SLOT_BOTTOM - SLOT_H;
    const float rows_h = static_cast<float>(SLOT_ROWS) * SLOT_PITCH;
    out->frame = {x, y, SLOT_W, SLOT_H};
    out->tab = {x + 8.0f, y - SLOT_TAB_H + 2.0f, SLOT_W - 16.0f, SLOT_TAB_H};
    out->header = {x + 18.0f, y + 9.0f, SLOT_W - 36.0f, 28.0f};
    out->rows = {x + 40.0f, y + 40.0f, SLOT_W - 62.0f, rows_h};
    out->footer = {x + 16.0f, y + 40.0f + rows_h + 6.0f, SLOT_W - 32.0f, SLOT_H - (40.0f + rows_h + 6.0f) - 4.0f};
    const uint32_t count = sv.row_count < md::VIEW_ROWS ? sv.row_count : md::VIEW_ROWS;
    out->visible = count < SLOT_ROWS ? count : SLOT_ROWS;
    if (out->visible == 0) {
        out->visible = 1;   // an empty page still shows its window
    }
    // The cursor kept in view: centred where the rows allow it (the view already carries a window around it).
    if (sv.cursor != 0xFF && count > out->visible) {
        const int32_t half = static_cast<int32_t>(out->visible) / 2;
        int32_t start = static_cast<int32_t>(sv.cursor) - half;
        const int32_t last = static_cast<int32_t>(count - out->visible);
        start = start < 0 ? 0 : (start > last ? last : start);
        out->start = static_cast<uint32_t>(start);
    }
    return true;
}

float ease(uint64_t opened, uint64_t now)
{
    if (opened == 0) {
        return 1.0f;   // no open time known: in place
    }
    if (now <= opened) {
        return 0.0f;
    }
    const uint64_t t = now - opened;
    if (t >= md::SLIDE_MS) {
        return 1.0f;
    }
    const float p = static_cast<float>(t) / static_cast<float>(md::SLIDE_MS);
    return p * p * (3.0f - 2.0f * p);
}

bool test_card(batch& b, const card_info& in)
{
    const theme::tokens& T = theme::active();
    const uint32_t refused = b.refused();
    // Tick marks along the edges of the authoring space: every 160 units across, every 90 down (12 each way).
    const uint32_t tick = set_alpha(T.card_label.core, 200);
    for (uint32_t k = 0; k <= 12; ++k) {
        const float x = static_cast<float>(k) * 160.0f - (k == 12 ? 2.0f : (k == 0 ? 0.0f : 1.0f));
        const float len = k % 3 == 0 ? 18.0f : 10.0f;
        solid(b, x, 0.0f, 2.0f, len, tick);
        solid(b, x, AUTHOR_H - len, 2.0f, len, tick);
        const float y = static_cast<float>(k) * 90.0f - (k == 12 ? 2.0f : (k == 0 ? 0.0f : 1.0f));
        solid(b, 0.0f, y, len, 2.0f, tick);
        solid(b, AUTHOR_W - len, y, len, 2.0f, tick);
    }
    line size;
    size.t("1920 x 1080 units; back buffer ").u(in.bb_w).t(" x ").u(in.bb_h);
    const float right = AUTHOR_W - CARD_MARGIN - CARD_W;
    const float bottom = AUTHOR_H - CARD_MARGIN - CARD_H;
    corner_card(b, CARD_MARGIN + 20.0f, CARD_MARGIN + 20.0f, "Top left", "Ivalice Legacy overlay", size.s);
    corner_card(b, right - 20.0f, CARD_MARGIN + 20.0f, "Top right", "Target Info and the log sit here",
                "Glass, outline and shadow");
    corner_card(b, CARD_MARGIN + 20.0f, bottom - 20.0f, "Bottom left", "Command windows: a slot per seat",
                "P1 leftmost, then P2, P3, P4");
    corner_card(b, right - 20.0f, bottom - 20.0f, "Bottom right", "Party list",
                "abcdefghijklmnopqrstuvwxyz 0123456789");
    // The status line, top centre: frame time, the overlay's own cost, the HUD fade and whether guests' menus can open.
    line status;
    status.t("Overlay test card   frame ").tenths(in.frame_us / 100).t(" ms");
    if (in.frame_us != 0) {
        status.t(" (").u((1000000u + in.frame_us / 2) / in.frame_us).t(" fps)");
    }
    status.t("   overlay ").u(in.draw_us).t(" us   HUD ").u(in.hud_alpha_pct).t("%   guest menus ")
        .t(in.menus_live != 0 ? "live" : "idle").t("   theme ").t(T.id);
    text(b, FACE_BODY, AUTHOR_W * 0.5f, 150.0f, 22.0f, status.s, sizeof(status.s), T.row, 1100.0f, 2);
    // The marks in the seat colours: seat diamonds, glove, chevrons, dot.
    float x = AUTHOR_W * 0.5f - 4.0f * 34.0f;
    for (uint32_t s = 0; s < 4; ++s) {
        const uint32_t c = in.seat_colour[s] != 0 ? in.seat_colour[s] : T.seat[s];
        marks::diamond(b, x, 186.0f, 24.0f, c);
        line p;
        p.t("P").u(s + 1);
        text(b, FACE_BODY, x + 28.0f, 184.0f, 22.0f, p.s, 4, coloured(T.row, c));
        x += 68.0f;
    }
    marks::glove(b, AUTHOR_W * 0.5f - 60.0f, 220.0f, 28.0f, T.seat[0]);
    marks::chevron(b, marks::DIR_UP, AUTHOR_W * 0.5f - 20.0f, 224.0f, 18.0f);
    marks::chevron(b, marks::DIR_DOWN, AUTHOR_W * 0.5f + 4.0f, 224.0f, 18.0f);
    marks::dot(b, AUTHOR_W * 0.5f + 30.0f, 226.0f, 14.0f, T.heal);
    return b.refused() == refused;
}

menus_out seat_windows(batch& b, const md::menu_view& v, const hud::hud_snapshot& snap, uint64_t now_ms,
                       bool p1_ours, const uint64_t* focus_ms)
{
    (void)snap;   // fixed slots: nothing follows the party list (the caller still requires a fresh snapshot)
    menus_out out = {0, 0, 0};
    for (uint32_t s = 0; s < SEATS; ++s) {
        const md::seat_view& sv = v.seats[s];
        if (sv.kind == md::WK_NONE) {
            continue;
        }
        out.open |= 1u << s;
        slot sl;
        if (sv.kind > md::WK_SELECTOR || (s == 0 && !p1_ours) || !slot_layout(s, sv, &sl)) {
            out.dropped |= 1u << s;   // Player 1 on the game's own menu (never drawn here), or a kind we cannot draw
            continue;
        }
        const uint32_t before = b.count();
        const uint32_t refused = b.refused();
        draw_window(b, s, sv, sl, now_ms, focus_ms != nullptr ? focus_ms[s] : sv.opened_ms);
        if (b.refused() != refused) {
            b.truncate(before);   // never half a window: it is left out and reported
            out.dropped |= 1u << s;
            continue;
        }
        ++out.drawn;
    }
    return out;
}

}  // namespace ilc::overlay::scene
