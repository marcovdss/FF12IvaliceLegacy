// Ivalice Legacy co-op module: the co-op battle menus in the native window style, the drawing half (see menus_draw.h).
//
// Everything here runs on the game thread inside the frame driver's guarded menu pass, or is a pure function the host
// tests call: no game memory, no game call, no C runtime call (bulk copies are `rep movsb`), no allocation, no wait.
#include "hud/menus_draw.h"
#include "runtime/loopback.h"

#include "hud/hud_internal.h"
#include "hud/layout.h"
#include "hud/party_rows.h"

namespace ilc::hud::menus_draw {
namespace {

using detail::copy_bytes;
using detail::zero_bytes;

// ---- the native layout (field MRP section 1 of the stock pack, 1920x1080 authoring units) ---------------------------
// Group 14: the battle command window's header and list origin. E0 is the first row's quad, E1 the character's name;
// E2 embeds group 15 ('LEADER' + the leader flag) at (0, 0).
constexpr box G14 = {80, 816, 704, 220};
constexpr box G14_E0 = {42, 132, 256, 68};
constexpr box G14_E1 = {122, 44, 256, 28};
constexpr box G15_E0 = {86, 92, 240, 28};
constexpr box G15_E1 = {18, 104, 50, 38};
// Group 11: the command menu's help strip (E0, a flat quad; its text at scale 75).
constexpr box G11 = {426, 764, 512, 256};
constexpr box G11_E0 = {108, 160, 360, 36};
constexpr int16_t HELP_SCALE = 75;
// Group 30: the target footer, with the shoulder-button icons of groups 31 and 32 (buttonicon page 0 sheet 4).
constexpr box G30 = {210, 978, 640, 80};
constexpr box G31 = {46, 30, 64, 40};
constexpr box G31_E0 = {4, 2, 50, 34};
constexpr box G32 = {485, 30, 60, 40};
constexpr box G32_E0 = {6, 2, 50, 34};
// ---- Player 1's window around the native pieces -----------------------------------------------------------------------
constexpr int16_t P1_PITCH = 44;          // row pitch (the runtime row-pitch global is not read here)
constexpr int16_t P1_SCALE = 100;
constexpr int16_t P1_MAX_ROWS = 8;
constexpr int16_t P1_MIN_ROWS = 4;
constexpr int16_t P1_FRAME_X = 60;        // the glass encloses the hand slot (E3, x 76) and the right slot (E4, x 422)
constexpr int16_t P1_FRAME_W = 384;
constexpr int16_t P1_HEADER = 142;        // from the frame's top to the rows (G14.y to G14 E0.y + 10 at one row)
constexpr int16_t P1_NAME_UP = 98;        // name, LEADER and flag above the rows: exactly G14's at one row
constexpr int16_t P1_LEADER_UP = 50;
constexpr int16_t P1_FLAG_UP = 38;
constexpr int16_t P1_HAND_X = 76;         // G14 E3
constexpr int16_t P1_ARROW_X = 386;       // G14 E4

// ---- a guest's window ---------------------------------------------------------------------------------------------
constexpr int16_t G_WIDTH = 280;
constexpr int16_t G_PITCH = 32;
constexpr int16_t G_SCALE = 80;
constexpr int16_t G_HELP_SCALE = 70;
constexpr int16_t G_MAX_ROWS = 6;
constexpr int16_t G_MIN_ROWS = 3;
constexpr int16_t G_PAD = 10;
constexpr int16_t G_HEADER = 32;
constexpr int16_t G_HELP = 26;
constexpr int16_t G_FOOTER = 30;
constexpr int16_t G_EDGE = 4;             // the seat-colour edge
constexpr int16_t G_ROW_GAP = 12;         // between a window and its party row's leftmost piece (the target hand)
constexpr int16_t G_STACK_GAP = 8;        // between two guests' windows
constexpr int16_t G_TOP = 40;             // nothing above this

// ---- assets (atlas, page, sheet, cell) ------------------------------------------------------------------------------
// bt_win_0_c 0.0: the battle glass 9-slice (corners 83x43, middle row 50 high, 2-unit stretches); 0.1: the 2x2 body
// fill the glass drawer tints (used here as a solid quad: seat edge, connector, help strip).
constexpr int16_t GLASS_CORNER_W = 83;
constexpr int16_t GLASS_CORNER_H = 43;
constexpr uint16_t FILL_CELL = 4;
// cursol_4_p 0.0 cell 0: the hand (64x54); 0.6: the gold scroll arrows (38x27), cell 0 down, cell 1 up.
// battle_4_c 14.2: the ON/OFF plate (88x40), cell 0 on, cell 1 off. buttonicon 0.4: L1 (cell 0) and R1 (cell 1).

constexpr rgba TEXT_WHITE = {0x80, 0x80, 0x80, 0x80};
constexpr rgba TEXT_GREY = {0x48, 0x48, 0x4C, 0x80};
constexpr rgba TEXT_VALUE = {0x70, 0x76, 0x80, 0x80};
constexpr rgba TEXT_HELP = {0x74, 0x78, 0x80, 0x80};
constexpr rgba TEXT_NAME = {0x80, 0x80, 0x80, 0x6E};    // G14 E1's colour
constexpr rgba TEXT_LEADER = {0x80, 0x80, 0x80, 0x66};  // G15 E0's colour
constexpr rgba TEXT_TITLE = {0x60, 0x70, 0x80, 0x80};
constexpr rgba HELP_FILL = {0x04, 0x0A, 0x1C, 0x50};
constexpr uint32_t WL_NO_HELP = 0x01;      // window_layout.reserved: the frame share dropped its help line

counters g_counters = {};

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

// ---- the published view (co-op thread writes; the menu pass reads) ---------------------------------------------------
struct slot_t {
    volatile uint32_t seq;
    volatile uint32_t valid;
    menu_view value;
};
slot_t g_slot = {};

// Game-thread copies: two buffers, the last good one kept for a torn read.
menu_view g_copy[2];
uint32_t g_good = 2;         // index of the last good copy (2 = none)
uint32_t g_prepared = 0;     // prepare() ran for the coming menu pass
uint32_t g_view_ok = 0;      // ... and found a usable view (g_copy[g_good])
uint32_t g_last_dropped = 0; // the seats the last menu pass left out
uint32_t g_test_dropped = 0; // host tests: seats reported left out whatever was drawn

uint32_t length(const char* s, uint32_t cap)
{
    uint32_t n = 0;
    while (n < cap && s[n] != '\0') {
        ++n;
    }
    return n;
}

int32_t clamp(int32_t v, int32_t lo, int32_t hi) { return v < lo ? lo : (v > hi ? hi : v); }

bool overlaps(const box& a, const box& b)
{
    return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

box from(const layout::zone& z) { return {z.x, z.y, z.w, z.h}; }

box inflate(const box& b, int16_t by)
{
    return {static_cast<int16_t>(b.x - by), static_cast<int16_t>(b.y - by), static_cast<int16_t>(b.w + 2 * by),
            static_cast<int16_t>(b.h + 2 * by)};
}

// Player 1's window at its tallest (the zone the guests keep clear of, whether or not it is open).
box p1_zone()
{
    const int16_t top = static_cast<int16_t>(p1_native().list_y - P1_MAX_ROWS * P1_PITCH - P1_HEADER);
    return {P1_FRAME_X, top, P1_FRAME_W, static_cast<int16_t>(G14.y + G14.h - top)};
}

int32_t text_estimate(const char* s, uint32_t cap, int32_t scale)
{
    // About 15 units per character at scale 100 (digits are the common case); only used to right-align values.
    return static_cast<int32_t>(length(s, cap)) * 15 * scale / 100;
}

// ---- per-window cost (canvas commands and encoded text bytes) -------------------------------------------------------
struct cost {
    uint32_t commands;
    uint32_t text;
};

void add_text(cost* c, const char* s, uint32_t cap)
{
    const uint32_t n = length(s, cap);
    if (n != 0) {
        c->commands += 1;
        c->text += n + 1;
    }
}

bool p1_leads(const seat_view& sv, const hud_snapshot& snap)
{
    return sv.member != targets::MEMBER_NONE && snap.leader == sv.member;
}

cost window_cost(const seat_view& sv, const window_layout& w, bool p1, bool leads)
{
    cost c = {9, 0};  // the glass
    if (p1) {
        add_text(&c, sv.name, ROW_TEXT);
        if (leads) {
            add_text(&c, "LEADER", 8);
            c.commands += 1;  // the leader flag
        }
        if (sv.kind != WK_LIST || sv.depth > 1) {
            add_text(&c, sv.title, TITLE_TEXT);
        }
    } else {
        c.commands += 1;  // the seat edge
        add_text(&c, sv.title, TITLE_TEXT);
    }
    for (uint32_t k = w.start; k < static_cast<uint32_t>(w.start) + w.visible && k < sv.row_count; ++k) {
        add_text(&c, sv.rows[k].text, ROW_TEXT);
        add_text(&c, sv.rows[k].value, ROW_VALUE);
        if ((sv.rows[k].flags & RF_TOGGLE) != 0 && (sv.rows[k].flags & RF_KNOWN) != 0) {
            c.commands += 1;
        }
    }
    if (sv.cursor != 0xFF && sv.cursor >= w.start && sv.cursor < w.start + w.visible) {
        c.commands += 2;  // hand and selection bar
    }
    if (sv.first + w.start > 0) {
        c.commands += 1;
    }
    if (sv.first + w.start + w.visible < sv.total) {
        c.commands += 1;
    }
    if (w.help.w != 0) {
        add_text(&c, sv.help, HELP_TEXT);
        if (w.help_glass != 0) {
            c.commands += 1;
        }
    }
    if (sv.kind == WK_TARGET) {
        c.commands += 2;  // the shoulder-button icons
        add_text(&c, group_name(sv.group) != nullptr ? group_name(sv.group) : "", TITLE_TEXT);
    }
    c.commands += w.connector;
    return c;
}

// The worst-case floor of one window in the cost model above: rows at the minimum, each with its name, value and
// ON/OFF plate; cursor, both arrows, the target footer; no help line, help glass or connector. Text: every string at
// its field's full length (length() stops at the field's end) plus its NUL.
constexpr uint32_t P1_FLOOR_COMMANDS = 9 + 1 + 2 + 1 + 3 * P1_MIN_ROWS + 2 + 2 + 3;   // glass, name, LEADER+flag, title
constexpr uint32_t G_FLOOR_COMMANDS = 9 + 1 + 1 + 3 * G_MIN_ROWS + 2 + 2 + 3;         // glass, edge, title
constexpr uint32_t P1_FLOOR_TEXT = (ROW_TEXT + 1) + 7 + (TITLE_TEXT + 1) +
                                   P1_MIN_ROWS * ((ROW_TEXT + 1) + (ROW_VALUE + 1)) + (TITLE_TEXT + 1);
constexpr uint32_t G_FLOOR_TEXT = (TITLE_TEXT + 1) + G_MIN_ROWS * ((ROW_TEXT + 1) + (ROW_VALUE + 1)) + (TITLE_TEXT + 1);
static_assert(P1_FLOOR_COMMANDS + (SEATS - 1) * G_FLOOR_COMMANDS <= FLOOR_COMMANDS_MAX &&
                  P1_FLOOR_TEXT + (SEATS - 1) * G_FLOOR_TEXT <= FLOOR_TEXT_MAX,
              "FLOOR_*_MAX bound four floor windows (four seats in the 192-command frame)");

int16_t guest_height(const seat_view& sv, int32_t rows)
{
    return static_cast<int16_t>(2 * G_PAD + G_HEADER + rows * G_PITCH + G_HELP + (sv.kind == WK_TARGET ? G_FOOTER : 0));
}

// The guest's party row: the snapshot row its seat holds (HUD order), 0xFF if none.
uint8_t row_of(const hud_snapshot& snap, uint32_t seat)
{
    const uint32_t rows = snap.member_count < SNAP_MEMBERS ? snap.member_count : SNAP_MEMBERS;
    for (uint32_t k = 0; k < rows; ++k) {
        if (snap.members[k].seat == seat + 1) {
            return static_cast<uint8_t>(k);
        }
    }
    return 0xFF;
}

uint32_t rows_of(const hud_snapshot& snap)
{
    const uint32_t rows = snap.member_count < SNAP_MEMBERS ? snap.member_count : SNAP_MEMBERS;
    return rows == 0 ? 1 : rows;
}

void set_start(const seat_view& sv, window_layout* w)
{
    const int32_t v = w->visible;
    int32_t start = 0;
    if (sv.cursor != 0xFF) {
        start = clamp(static_cast<int32_t>(sv.cursor) - v / 2, 0, sv.row_count > v ? sv.row_count - v : 0);
    }
    w->start = static_cast<uint8_t>(start);
}

void place_p1(const seat_view& sv, window_layout* w)
{
    const p1_geometry g = p1_native();
    const int16_t top = static_cast<int16_t>(g.list_y - w->visible * P1_PITCH);
    w->rows = {g.list_x, top, G14_E0.w, static_cast<int16_t>(w->visible * P1_PITCH)};
    const int16_t frame_top = static_cast<int16_t>(top - P1_HEADER);
    w->frame = {P1_FRAME_X, frame_top, P1_FRAME_W, static_cast<int16_t>(G14.y + G14.h - frame_top)};
    w->help = sv.help[0] != '\0' && (w->reserved & WL_NO_HELP) == 0 ? g.help : box{0, 0, 0, 0};
    w->pitch = P1_PITCH;
    w->text_scale = P1_SCALE;
    w->row_index = 0xFF;
}

// Guests' windows in one column, each sliding out of its own row: processed from the lowest row up, each starts with
// its bottom on its row's bottom and moves up past anything it would overlap.
bool place_guests(const menu_view& v, const hud_snapshot& snap, frame_layout* fl)
{
    const bool reduced = (v.head.flags & VIEW_REDUCED) != 0;
    const uint32_t rows = rows_of(snap);
    const bool boss = layout::boss_gauge(snap);
    const box ti_zone = from(layout::target_info_zone(snap.layout, boss));
    const box log_area = from(layout::log_zone(snap.layout, boss));
    const box forbidden[] = {inflate(p1_zone(), 8), inflate(p1_native().help, 12),
                             inflate(box{G30.x, G30.y, G30.w, G30.h}, 4), ti_zone, log_area, party_list(rows, reduced)};
    // Order: lowest row first (a guest without a row counts as below every row).
    uint8_t order[SEATS];
    uint32_t n = 0;
    for (uint32_t k = 0; k < fl->count; ++k) {
        if (fl->w[k].seat != 0 && fl->w[k].kind != WK_NONE) {
            order[n++] = static_cast<uint8_t>(k);
        }
    }
    for (uint32_t a = 1; a < n; ++a) {
        for (uint32_t b = a; b > 0; --b) {
            const window_layout& x = fl->w[order[b - 1]];
            const window_layout& y = fl->w[order[b]];
            const int32_t rx = x.row_index == 0xFF ? 99 : x.row_index;
            const int32_t ry = y.row_index == 0xFF ? 99 : y.row_index;
            if (rx > ry || (rx == ry && x.seat > y.seat)) {
                break;
            }
            const uint8_t t = order[b - 1];
            order[b - 1] = order[b];
            order[b] = t;
        }
    }
    box placed[SEATS];
    uint32_t placed_n = 0;
    bool fits = true;
    for (uint32_t i = 0; i < n; ++i) {
        window_layout& w = fl->w[order[i]];
        const seat_view& sv = v.seats[w.seat];
        const uint32_t ri = w.row_index == 0xFF ? rows - 1 : w.row_index;
        const box row = party_row(ri, rows, reduced);
        const int16_t h = guest_height(sv, w.visible);
        // Left of the row, but never into Player 1's or Target Info's column (the stock party list starts further
        // left than the reduced one: there the window rises above the list instead).
        const box p1 = p1_zone();
        const box ti = ti_zone;
        const int32_t left_min = (p1.x + p1.w > ti.x + ti.w ? p1.x + p1.w : ti.x + ti.w) + 2 * G_STACK_GAP;
        int32_t right_edge = row.x - G_ROW_GAP;
        const box lz = log_area;
        if (right_edge > lz.x - G_STACK_GAP) {
            right_edge = lz.x - G_STACK_GAP;   // the column stays clear of the expanded battle log above it
        }
        if (right_edge - G_WIDTH < left_min) {
            right_edge = left_min + G_WIDTH;
        }
        const int16_t right = static_cast<int16_t>(right_edge);
        int32_t bottom = row.y + row.h;
        bool moved = true;
        while (moved) {
            moved = false;
            const box b = {static_cast<int16_t>(right - G_WIDTH), static_cast<int16_t>(bottom - h), G_WIDTH, h};
            for (const box& f : forbidden) {
                if (overlaps(b, f)) {
                    bottom = f.y - G_STACK_GAP;
                    moved = true;
                }
            }
            for (uint32_t k = 0; k < placed_n; ++k) {
                if (overlaps(b, placed[k])) {
                    bottom = placed[k].y - G_STACK_GAP;
                    moved = true;
                }
            }
        }
        w.frame = {static_cast<int16_t>(right - G_WIDTH), static_cast<int16_t>(bottom - h), G_WIDTH, h};
        w.pushed = bottom != row.y + row.h ? 1 : 0;
        if (w.frame.y < G_TOP) {
            fits = false;
        }
        placed[placed_n++] = w.frame;
        const int16_t rows_top = static_cast<int16_t>(w.frame.y + G_PAD + G_HEADER);
        w.rows = {static_cast<int16_t>(w.frame.x + 36), rows_top, static_cast<int16_t>(G_WIDTH - 52),
                  static_cast<int16_t>(w.visible * G_PITCH)};
        w.help = sv.help[0] != '\0' && (w.reserved & WL_NO_HELP) == 0 ? box{static_cast<int16_t>(w.frame.x + 20),
                                          static_cast<int16_t>(rows_top + w.visible * G_PITCH + 2),
                                          static_cast<int16_t>(G_WIDTH - 40), G_HELP}
                                    : box{0, 0, 0, 0};
        w.pitch = G_PITCH;
        w.text_scale = G_SCALE;
        if (w.connector != 0) {
            // One line at the row's middle while the window spans it, else down from the window and across.
            const int32_t mid = row.y + row.h / 2;
            w.connector = (mid >= w.frame.y + 10 && mid <= w.frame.y + w.frame.h - 10) ? 1 : 2;
        }
    }
    return fits;
}

// The smoothstep of the slide-in at `now` (0..256).
int32_t ease(uint64_t opened, uint64_t now)
{
    if (now <= opened) {
        return opened == 0 ? 256 : 0;
    }
    const uint64_t t = now - opened;
    if (t >= SLIDE_MS) {
        return 256;
    }
    const int32_t p = static_cast<int32_t>(t * 256 / SLIDE_MS);     // 0..255
    return p * p * (768 - 2 * p) / (256 * 256);                       // 3p^2 - 2p^3, scaled
}

rgba faded(rgba c, int32_t e) { return {c.r, c.g, c.b, static_cast<uint8_t>(c.a * e / 256)}; }

rgba unpack(uint32_t v)
{
    return {static_cast<uint8_t>(v), static_cast<uint8_t>(v >> 8), static_cast<uint8_t>(v >> 16),
            static_cast<uint8_t>(v >> 24)};
}

void glass(canvas& cv, const box& b, int32_t e)
{
    const rgba tint = faded(TEXT_WHITE, e);
    const int16_t cw = b.w >= 2 * GLASS_CORNER_W ? GLASS_CORNER_W : static_cast<int16_t>(b.w / 2);
    const int16_t ch = b.h >= 2 * GLASS_CORNER_H ? GLASS_CORNER_H : static_cast<int16_t>(b.h / 2);
    const int16_t xs[3] = {b.x, static_cast<int16_t>(b.x + cw), static_cast<int16_t>(b.x + b.w - cw)};
    const int16_t ws[3] = {cw, static_cast<int16_t>(b.w - 2 * cw), cw};
    const int16_t ys[3] = {b.y, static_cast<int16_t>(b.y + ch), static_cast<int16_t>(b.y + b.h - ch)};
    const int16_t hs[3] = {ch, static_cast<int16_t>(b.h - 2 * ch), ch};
    for (uint32_t r = 0; r < 3; ++r) {
        for (uint32_t c = 0; c < 3; ++c) {
            const uint16_t w = static_cast<uint16_t>(ws[c] > 0 ? ws[c] : 1);
            const uint16_t h = static_cast<uint16_t>(hs[r] > 0 ? hs[r] : 1);
            cv.sprite(ATLAS_BT_WIN_0_C, 0, 0, static_cast<uint16_t>(r * 3 + c), xs[c], ys[r], tint, w, h);
        }
    }
}

void fill(canvas& cv, int32_t x, int32_t y, int32_t w, int32_t h, rgba c)
{
    if (w > 0 && h > 0) {
        cv.sprite(ATLAS_BT_WIN_0_C, 0, 1, FILL_CELL, static_cast<int16_t>(x), static_cast<int16_t>(y), c,
                  static_cast<uint16_t>(w), static_cast<uint16_t>(h));
    }
}

void text(canvas& cv, int32_t x, int32_t y, const char* s, uint32_t cap, rgba c, int32_t scale)
{
    if (length(s, cap) != 0) {
        cv.text(static_cast<int16_t>(x), static_cast<int16_t>(y), s, c, static_cast<uint16_t>(scale));
    }
}

// Rows, cursor, values, Gambits plate, scroll arrows: the same for every window.
void draw_rows(canvas& cv, const seat_view& sv, const window_layout& w, int32_t dx, int32_t e, bool p1)
{
    const int32_t scale = w.text_scale;
    const rgba bar = faded(unpack((sv.colour & 0x00FFFFFFu) | 0x40000000u), e);   // NOTADDR: alpha 0x40
    const int32_t hand_w = p1 ? 40 : 30;
    const int32_t hand_h = p1 ? 34 : 26;
    const int32_t right = w.rows.x + w.rows.w + dx;
    for (uint32_t i = 0; i < w.visible; ++i) {
        const uint32_t k = w.start + i;
        if (k >= sv.row_count) {
            break;
        }
        const view_row& r = sv.rows[k];
        const int32_t top = w.rows.y + static_cast<int32_t>(i) * w.pitch;
        if (k == sv.cursor) {
            cv.bar(static_cast<int16_t>(w.rows.x - 6 + dx), static_cast<int16_t>(top + 3),
                   static_cast<uint16_t>(w.rows.w + 12), static_cast<uint16_t>(w.pitch - 6), bar);
            const int32_t hx = p1 ? P1_HAND_X + dx : w.frame.x + 8 + dx;
            cv.sprite(ATLAS_CURSOL_4_P, 0, 0, 0, static_cast<int16_t>(hx),
                      static_cast<int16_t>(top + (w.pitch - hand_h) / 2), faded(TEXT_WHITE, e),
                      static_cast<uint16_t>(hand_w), static_cast<uint16_t>(hand_h));
        }
        const rgba c = faded((r.flags & RF_GREY) != 0 ? TEXT_GREY : TEXT_WHITE, e);
        const int32_t ty = top + (w.pitch - 28 * scale / 100) / 2;
        text(cv, w.rows.x + 4 + dx, ty, r.text, ROW_TEXT, c, scale);
        if (r.value[0] != '\0') {
            const rgba vc = faded((r.flags & RF_GREY) != 0 ? TEXT_GREY : TEXT_VALUE, e);
            text(cv, right - text_estimate(r.value, ROW_VALUE, scale), ty, r.value, ROW_VALUE, vc, scale);
        }
        if ((r.flags & RF_TOGGLE) != 0 && (r.flags & RF_KNOWN) != 0) {
            const int32_t pw = 88 * scale / 160;
            const int32_t ph = 40 * scale / 160;
            cv.sprite(ATLAS_BATTLE_4_C, 14, 2, (r.flags & RF_ON) != 0 ? 0 : 1, static_cast<int16_t>(right - pw),
                      static_cast<int16_t>(top + (w.pitch - ph) / 2), faded(TEXT_WHITE, e),
                      static_cast<uint16_t>(pw), static_cast<uint16_t>(ph));
        }
    }
    const int32_t ax = p1 ? P1_ARROW_X + dx : w.frame.x + w.frame.w - 34 + dx;
    const int32_t aw = p1 ? 38 : 28;
    const int32_t ah = p1 ? 27 : 20;
    if (sv.first + w.start > 0) {
        cv.sprite(ATLAS_CURSOL_4_P, 0, 6, 1, static_cast<int16_t>(ax), static_cast<int16_t>(w.rows.y - ah + 4),
                  faded(TEXT_WHITE, e), static_cast<uint16_t>(aw), static_cast<uint16_t>(ah));
    }
    if (sv.first + w.start + w.visible < sv.total) {
        cv.sprite(ATLAS_CURSOL_4_P, 0, 6, 0, static_cast<int16_t>(ax),
                  static_cast<int16_t>(w.rows.y + w.rows.h - 4), faded(TEXT_WHITE, e), static_cast<uint16_t>(aw),
                  static_cast<uint16_t>(ah));
    }
}

void draw_footer(canvas& cv, const seat_view& sv, int32_t x_l1, int32_t x_r1, int32_t y, int32_t size, int32_t dx,
                 int32_t e)
{
    const int32_t iw = 50 * size / 100;
    const int32_t ih = 34 * size / 100;
    cv.sprite(ATLAS_BUTTONICON, 0, 4, 0, static_cast<int16_t>(x_l1 + dx), static_cast<int16_t>(y),
              faded(TEXT_WHITE, e), static_cast<uint16_t>(iw), static_cast<uint16_t>(ih));
    const char* g = group_name(sv.group);
    if (g != nullptr) {
        text(cv, x_l1 + iw + 16 + dx, y + 2, g, TITLE_TEXT, faded(TEXT_WHITE, e), size * 80 / 100);
    }
    cv.sprite(ATLAS_BUTTONICON, 0, 4, 1, static_cast<int16_t>(x_r1 + dx), static_cast<int16_t>(y),
              faded(TEXT_WHITE, e), static_cast<uint16_t>(iw), static_cast<uint16_t>(ih));
}

void draw_p1(canvas& cv, const seat_view& sv, const window_layout& w, const hud_snapshot& snap, uint64_t now)
{
    const p1_geometry g = p1_native();
    const int32_t e = ease(sv.opened_ms, now);
    const int32_t dx = -(256 - e) * SLIDE_DISTANCE / 256;   // in from the left
    glass(cv, box{static_cast<int16_t>(w.frame.x + dx), w.frame.y, w.frame.w, w.frame.h}, e);
    const int32_t rows_top = w.rows.y;
    text(cv, g.name.x + dx, rows_top - P1_NAME_UP, sv.name, ROW_TEXT, faded(TEXT_NAME, e), 100);
    const bool leads = p1_leads(sv, snap);
    int32_t title_x = g.leader.x;
    if (leads) {
        text(cv, g.leader.x + dx, rows_top - P1_LEADER_UP, "LEADER", 8, faded(TEXT_LEADER, e), 100);
        cv.sprite(ATLAS_BATTLE_4_C, 14, 0, 0, static_cast<int16_t>(g.flag.x + dx),
                  static_cast<int16_t>(rows_top - P1_FLAG_UP), faded(TEXT_WHITE, e), static_cast<uint16_t>(g.flag.w),
                  static_cast<uint16_t>(g.flag.h));
        title_x = g.leader.x + 130;
    }
    if (sv.kind != WK_LIST || sv.depth > 1) {
        text(cv, title_x + dx, rows_top - P1_LEADER_UP + 4, sv.title, TITLE_TEXT, faded(TEXT_TITLE, e), HELP_SCALE);
    }
    draw_rows(cv, sv, w, dx, e, true);
    if (w.help.w != 0) {
        if (w.help_glass != 0) {
            fill(cv, w.help.x, w.help.y, w.help.w, w.help.h, faded(HELP_FILL, e));
        }
        text(cv, w.help.x + 14, w.help.y + 5, sv.help, HELP_TEXT, faded(TEXT_HELP, e), HELP_SCALE);
    }
    if (sv.kind == WK_TARGET) {
        draw_footer(cv, sv, g.footer_l1.x, g.footer_r1.x, g.footer_l1.y, 100, dx, e);
    }
}

void draw_guest(canvas& cv, const seat_view& sv, const window_layout& w, const hud_snapshot& snap, bool reduced,
                uint64_t now)
{
    const int32_t e = ease(sv.opened_ms, now);
    const int32_t dx = (256 - e) * SLIDE_DISTANCE / 256;    // out of the row: from the right, leftwards
    const rgba seat = faded(unpack(sv.colour | 0x80000000u), e);   // NOTADDR: full alpha
    const box f = {static_cast<int16_t>(w.frame.x + dx), w.frame.y, w.frame.w, w.frame.h};
    glass(cv, f, e);
    fill(cv, f.x + 6, f.y + 10, G_EDGE, f.h - 20, seat);
    text(cv, f.x + 20, f.y + G_PAD + 4, sv.title, TITLE_TEXT, seat, G_SCALE);
    draw_rows(cv, sv, w, dx, e, false);
    if (w.help.w != 0) {
        text(cv, w.help.x + dx, w.help.y + 4, sv.help, HELP_TEXT, faded(TEXT_HELP, e), G_HELP_SCALE);
    }
    if (sv.kind == WK_TARGET) {
        const int32_t y = f.y + f.h - G_PAD - G_FOOTER + 2;
        draw_footer(cv, sv, f.x + 18, f.x + f.w - 52, y, 70, 0, e);
    }
    if (w.connector != 0 && w.row_index != 0xFF) {
        // The link to its party row: a short seat-colour line at the row's middle (and up to the window if pushed).
        const box row = party_row(w.row_index, rows_of(snap), reduced);
        const int32_t mid = row.y + row.h / 2;
        const int32_t x0 = f.x + f.w;
        if (w.connector == 1) {
            fill(cv, x0, mid - 1, row.x - 6 - x0, 3, seat);
        } else {
            const int32_t from = f.y + f.h - 20;
            fill(cv, x0 + 10, from, 3, mid - from, seat);
            fill(cv, x0 + 10, mid - 1, row.x - 6 - (x0 + 10), 3, seat);
        }
    }
}

}  // namespace

// ---- geometry -----------------------------------------------------------------------------------------------------
p1_geometry p1_native()
{
    p1_geometry g;
    g.list_x = static_cast<int16_t>(G14.x + G14_E0.x);
    g.list_y = static_cast<int16_t>(G14.y + G14_E0.y + G14_E0.h - 14);
    g.help = {static_cast<int16_t>(G11.x + G11_E0.x), static_cast<int16_t>(G11.y + G11_E0.y), G11_E0.w, G11_E0.h};
    g.header = G14;
    g.name = {static_cast<int16_t>(G14.x + G14_E1.x), static_cast<int16_t>(G14.y + G14_E1.y), G14_E1.w, G14_E1.h};
    g.leader = {static_cast<int16_t>(G14.x + G15_E0.x), static_cast<int16_t>(G14.y + G15_E0.y), G15_E0.w, G15_E0.h};
    g.flag = {static_cast<int16_t>(G14.x + G15_E1.x), static_cast<int16_t>(G14.y + G15_E1.y), G15_E1.w, G15_E1.h};
    g.footer_l1 = {static_cast<int16_t>(G30.x + G31.x + G31_E0.x), static_cast<int16_t>(G30.y + G31.y + G31_E0.y),
                   G31_E0.w, G31_E0.h};
    g.footer_r1 = {static_cast<int16_t>(G30.x + G32.x + G32_E0.x), static_cast<int16_t>(G30.y + G32.y + G32_E0.y),
                   G32_E0.w, G32_E0.h};
    return g;
}

box party_row(uint32_t index, uint32_t rows, bool reduced)
{
    // The rows' own anchors (party_rows.h: the field layout's rows mapped into the canvas). The box starts at the
    // row's leftmost piece, the party-target hand, and runs to the screen's right edge.
    if (rows == 0) {
        rows = 1;
    }
    if (rows > SNAP_MEMBERS) {
        rows = SNAP_MEMBERS;
    }
    if (index >= rows) {
        index = rows - 1;
    }
    party_rows::anchor a;
    if (!party_rows::row_anchor(reduced ? party_rows::GEOMETRY_REDUCED : party_rows::GEOMETRY_STOCK, index, rows, &a)) {
        return {0, 0, 0, 0};
    }
    const int16_t left = a.cursor_x < a.numeral_x ? a.cursor_x : a.numeral_x;
    return {left, a.top, static_cast<int16_t>(1920 - left), a.pitch};
}

box party_list(uint32_t rows, bool reduced)
{
    const box top = party_row(0, rows, reduced);
    const box bottom = party_row(rows == 0 ? 0 : rows - 1, rows, reduced);
    return {top.x, top.y, top.w, static_cast<int16_t>(bottom.y + bottom.h - top.y)};
}

box target_info_zone() { return from(layout::target_info_zone(LAYOUT_COUCH, false)); }

box log_zone() { return from(layout::log_zone(LAYOUT_COUCH, false)); }

box p1_window_zone() { return p1_zone(); }

namespace {

// Every open window of `v` at its full size (rows capped), before placement and the frame share.
void init_windows(const menu_view& v, const hud_snapshot& snap, frame_layout* out)
{
    zero_bytes(out, sizeof(frame_layout));
    for (uint32_t s = 0; s < SEATS; ++s) {
        const seat_view& sv = v.seats[s];
        if (sv.kind == WK_NONE || sv.kind > WK_SELECTOR) {
            continue;
        }
        window_layout& w = out->w[out->count++];
        w.seat = static_cast<uint8_t>(s);
        w.kind = sv.kind;
        const int32_t cap = s == 0 ? P1_MAX_ROWS : G_MAX_ROWS;
        w.visible = static_cast<uint8_t>(sv.row_count < cap ? sv.row_count : cap);
        if (w.visible == 0) {
            w.visible = 1;  // an empty page still shows its window
        }
        w.row_index = s == 0 ? 0xFF : row_of(snap, s);
        w.connector = s == 0 || w.row_index == 0xFF ? 0 : 1;
        w.help_glass = s == 0 ? 1 : 0;
    }
}

}  // namespace

footprint floor_cost(const menu_view& v, const hud_snapshot& snap)
{
    // The state the frame share in layout() reaches just before it would leave a window out: connectors and P1's
    // help glass gone, every window at its minimum rows (or fewer if it has fewer), every help line gone. The cost
    // does not depend on where a window is placed.
    frame_layout fl;
    init_windows(v, snap, &fl);
    footprint total = {0, 0};
    for (uint32_t k = 0; k < fl.count; ++k) {
        window_layout& w = fl.w[k];
        const seat_view& sv = v.seats[w.seat];
        const int32_t floor = w.seat == 0 ? P1_MIN_ROWS : G_MIN_ROWS;
        if (w.visible > floor) {
            w.visible = static_cast<uint8_t>(floor);
        }
        w.connector = 0;
        w.help_glass = 0;
        w.help = {0, 0, 0, 0};
        w.reserved |= WL_NO_HELP;
        set_start(sv, &w);
        const cost c = window_cost(sv, w, w.seat == 0, p1_leads(sv, snap));
        total.commands += c.commands;
        total.text += c.text;
    }
    return total;
}

void layout(const menu_view& v, const hud_snapshot& snap, uint32_t commands, uint32_t text_room, frame_layout* out)
{
    init_windows(v, snap, out);
    // The column must fit on screen: fewer rows for the tallest guest window until it does.
    for (uint32_t guard = 0; guard < 32; ++guard) {
        if (place_guests(v, snap, out)) {
            break;
        }
        window_layout* tallest = nullptr;
        for (uint32_t k = 0; k < out->count; ++k) {
            window_layout& w = out->w[k];
            if (w.seat != 0 && w.visible > G_MIN_ROWS && (tallest == nullptr || w.visible > tallest->visible)) {
                tallest = &w;
            }
        }
        if (tallest == nullptr) {
            break;
        }
        --tallest->visible;
    }
    // The frame share: drop the connectors, then P1's help glass, then rows (guests first), then help lines, then
    // windows (the last seats first).
    for (uint32_t guard = 0; guard < 64; ++guard) {
        uint32_t cmds = 0;
        uint32_t txt = 0;
        for (uint32_t k = 0; k < out->count; ++k) {
            window_layout& w = out->w[k];
            if (w.kind == WK_NONE) {
                continue;
            }
            const seat_view& sv = v.seats[w.seat];
            set_start(sv, &w);
            if (w.seat == 0) {
                place_p1(sv, &w);
            }
            const cost c = window_cost(sv, w, w.seat == 0, p1_leads(sv, snap));
            w.commands = c.commands;
            w.text_bytes = c.text;
            cmds += c.commands;
            txt += c.text;
        }
        out->commands = cmds;
        out->text_bytes = txt;
        if (cmds <= commands && txt <= text_room) {
            break;
        }
        bool changed = false;
        for (uint32_t k = 0; k < out->count && !changed; ++k) {
            if (out->w[k].connector != 0) {
                for (uint32_t j = 0; j < out->count; ++j) {
                    out->w[j].connector = 0;
                }
                changed = true;
            }
        }
        for (uint32_t k = 0; k < out->count && !changed; ++k) {
            if (out->w[k].help_glass != 0 && out->w[k].kind != WK_NONE) {
                out->w[k].help_glass = 0;
                changed = true;
            }
        }
        if (!changed) {
            window_layout* most = nullptr;
            for (uint32_t pass_seat = 0; pass_seat < 2 && most == nullptr; ++pass_seat) {
                for (uint32_t k = 0; k < out->count; ++k) {
                    window_layout& w = out->w[k];
                    const bool guest = w.seat != 0;
                    const int32_t floor = guest ? G_MIN_ROWS : P1_MIN_ROWS;
                    if (w.kind == WK_NONE || guest != (pass_seat == 0) || w.visible <= floor) {
                        continue;
                    }
                    if (most == nullptr || w.visible > most->visible) {
                        most = &w;
                    }
                }
            }
            if (most != nullptr) {
                --most->visible;
                changed = true;
            }
        }
        for (uint32_t pass_seat = 0; pass_seat < 2 && !changed; ++pass_seat) {
            for (uint32_t k = out->count; k > 0 && !changed; --k) {
                window_layout& w = out->w[k - 1];
                if (w.kind != WK_NONE && (w.seat != 0) == (pass_seat == 0) && w.help.w != 0) {
                    w.help = {0, 0, 0, 0};
                    w.reserved |= WL_NO_HELP;
                    changed = true;
                }
            }
        }
        if (!changed) {
            for (uint32_t k = out->count; k > 0 && !changed; --k) {
                if (out->w[k - 1].kind != WK_NONE) {
                    out->w[k - 1].kind = WK_NONE;
                    ++out->dropped;
                    changed = true;
                }
            }
        }
        if (!changed) {
            break;
        }
        place_guests(v, snap, out);
    }
}

// ---- drawing ------------------------------------------------------------------------------------------------------------
uint32_t draw(canvas& cv, const menu_view& v, const hud_snapshot& snap, uint64_t now_ms, uint8_t layout_mode)
{
    return draw_seats(cv, v, snap, now_ms, layout_mode, nullptr);
}

uint32_t draw_seats(canvas& cv, const menu_view& v, const hud_snapshot& snap, uint64_t now_ms, uint8_t layout_mode,
                    uint32_t* dropped)
{
    (void)layout_mode;  // couch; the online layout (each screen its own seat) is a seam that draws the same way
    if (dropped != nullptr) {
        *dropped = 0;
    }
    if (v.open_mask == 0) {
        return 0;
    }
    const frame& f = cv.view();
    const uint32_t free_cmds = f.count < MAX_COMMANDS ? MAX_COMMANDS - f.count : 0;
    const uint32_t free_text = f.text_used < TEXT_POOL ? TEXT_POOL - f.text_used : 0;
    frame_layout fl;
    layout(v, snap, free_cmds < COMMAND_CAP ? free_cmds : COMMAND_CAP, free_text < TEXT_CAP ? free_text : TEXT_CAP,
           &fl);
    const bool reduced = (v.head.flags & VIEW_REDUCED) != 0;
    uint32_t drawn = 0;
    uint32_t lost = 0;
    for (uint32_t k = 0; k < fl.count; ++k) {
        const window_layout& w = fl.w[k];
        if (w.kind == WK_NONE) {
            lost |= 1u << w.seat;   // left out for room: that seat's menu is not on screen
            continue;
        }
        const seat_view& sv = v.seats[w.seat];
        if (w.visible < sv.row_count && w.visible < (w.seat == 0 ? P1_MAX_ROWS : G_MAX_ROWS)) {
            bump(g_counters.squeezed);
        }
        const uint32_t refused = cv.refused();
        if (w.seat == 0) {
            draw_p1(cv, sv, w, snap, now_ms);
        } else {
            draw_guest(cv, sv, w, snap, reduced, now_ms);
        }
        if (cv.refused() != refused) {
            lost |= 1u << w.seat;   // the canvas refused part of it (not expected: the layout fits the room)
        }
        ++drawn;
    }
    if (dropped != nullptr) {
        *dropped = lost;
    }
    if (fl.dropped != 0) {
        __atomic_add_fetch(&g_counters.dropped, static_cast<uint64_t>(fl.dropped), __ATOMIC_RELAXED);
    }
    if (drawn != 0) {
        bump(g_counters.drawn_frames);
        __atomic_add_fetch(&g_counters.drawn_windows, static_cast<uint64_t>(drawn), __ATOMIC_RELAXED);
    }
    return drawn;
}

footprint prepare(const driver::pass_input& in)
{
    g_prepared = 1;
    g_view_ok = 0;
    g_last_dropped = 0;
    if (in.snap == nullptr) {
        return {0, 0};
    }
    const uint32_t next = g_good == 0 ? 1u : 0u;
    bool got = read(g_copy[next]);
    if (!got) {
        bump(g_counters.torn);
        got = read(g_copy[next]);
    }
    if (got) {
        g_good = next;
    } else if (__atomic_load_n(&g_slot.valid, __ATOMIC_ACQUIRE) == 0) {
        g_good = 2;   // withdrawn (not torn): the last good copy is not shown either
    }
    if (g_good > 1) {
        return {0, 0};
    }
    const menu_view& v = g_copy[g_good];
    if (v.head.epoch != in.snap->head.epoch || in.now_ms > v.built_ms + VIEW_STALE_MS ||
        v.built_ms > in.now_ms + VIEW_STALE_MS) {
        return {0, 0};
    }
    g_view_ok = 1;
    return v.open_mask != 0 ? floor_cost(v, *in.snap) : footprint{0, 0};
}

bool pass(canvas& cv, const driver::pass_input& in)
{
    if (g_prepared == 0) {
        prepare(in);   // a direct call (not prepared by the frame driver this frame)
    }
    g_prepared = 0;
    g_last_dropped = g_test_dropped;
    if (in.snap == nullptr) {
        return false;
    }
    if (g_view_ok == 0) {
        bump(g_counters.stale);
        return false;
    }
    g_view_ok = 0;
    uint32_t dropped = 0;
    draw_seats(cv, g_copy[g_good], *in.snap, in.now_ms, in.layout, &dropped);
    g_last_dropped = dropped | g_test_dropped;
    return true;
}

uint32_t last_dropped()
{
    return g_last_dropped;
}

void test_force_dropped(uint32_t mask)
{
    g_test_dropped = mask;
}

// ---- publication -----------------------------------------------------------------------------------------------------------
void publish(const menu_view& v_in)
{
    ILC_LOOPBACK_COPY_STATIC(menu_view, FAM_MENU_VIEW, v_in, v);  // the seam (host tests: the loopback codec)
    const uint32_t odd = (g_slot.seq + 1u) | 1u;
    __atomic_store_n(&g_slot.seq, odd, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    copy_bytes(const_cast<menu_view*>(&g_slot.value), &v, sizeof(menu_view));
    g_slot.valid = 1;
    __atomic_store_n(&g_slot.seq, odd + 1u, __ATOMIC_RELEASE);
    bump(g_counters.built);
}

void withdraw()
{
    const uint32_t odd = (g_slot.seq + 1u) | 1u;
    __atomic_store_n(&g_slot.seq, odd, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    g_slot.valid = 0;
    __atomic_store_n(&g_slot.seq, odd + 1u, __ATOMIC_RELEASE);
    bump(g_counters.withdrawn);
}

bool read(menu_view& out)
{
    const uint32_t first = __atomic_load_n(&g_slot.seq, __ATOMIC_ACQUIRE);
    if ((first & 1u) != 0) {
        return false;
    }
    const uint32_t ok = g_slot.valid;
    copy_bytes(&out, const_cast<const menu_view*>(&g_slot.value), sizeof(menu_view));
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
    return __atomic_load_n(&g_slot.seq, __ATOMIC_RELAXED) == first && ok == 1;
}

const counters* stats()
{
    return &g_counters;
}

// Game-thread state reset (host tests; the rest of test_reset is in menus_draw_view.cpp).
void reset_draw_state()
{
    g_good = 2;
    g_prepared = 0;
    g_view_ok = 0;
    g_last_dropped = 0;
    g_test_dropped = 0;
    zero_bytes(const_cast<counters*>(&g_counters), sizeof(counters));
    withdraw();
    g_counters.withdrawn = 0;
}

}  // namespace ilc::hud::menus_draw
