// Ivalice Legacy co-op module: the party list row additions (see party_rows.h).
#include "hud/party_rows.h"
#include "runtime/loopback.h"

#include "control/control.h"
#include "frontend/frontend.h"
#include "hud/hud_internal.h"
#include "hud/relayout.h"
#include "hud/snapshot_facts.h"
#include "menu/model.h"
#include "patch/site_table.h"
#include "runtime/probe.h"
#include "session/reader.h"
#include "session/session.h"
#include "targets/live.h"

namespace ilc::hud::party_rows {
namespace {

namespace sf = snapfacts;
using detail::copy_bytes;
using detail::zero_bytes;

// ---- game facts (FFXII_TZA.exe 1.0.4.0, VAs at the pinned base 0x120000) --------------------------------------------
// The party row update 0x29a0a0 calls the HP digits function 0x297d10(row, hp, status) at 0x29a550 with rbx = the
// party-HUD record (+0x24 maximum HP). 0x297d10 returns early while hp == row+0xe0 (the HP cache) and row+0x108 (the
// status colour entry) is null; otherwise it computes the colour (white, or a pulsing palette colour for a status
// colour class) and calls the digit setter 0x29f6e0(first digit node, digits, 0, rgba, hp) at 0x297df1, after
// `mov rcx, rsi` at 0x297dee (rdi = row). The cache itself is written by the caller after the call returns.
constexpr uint64_t SITE_HP_CALL = 0x29A550;      // VA: `call 0x297d10` (HD-02)
constexpr uint64_t SITE_DIGITS_CALL = 0x297DF1;  // VA: `call 0x29f6e0` after `mov rcx, rsi` (HD-03)
constexpr uint32_t ROW_HP_CACHE = 0xE0;          // u32 in the row window: the HP the digits show
constexpr uint32_t HUD_RECORD_HP_MAX = 0x24;     // u32 in the party-HUD record
constexpr uint32_t HP_CACHE_FORCE = 0xFFFFFFFFu; // NOTADDR: no HP equals it: the digits are set again
// Actors and member records (read on the co-op thread only).
constexpr uint32_t ACTOR_ACTION = 0x714;         // u16: the action the actor performs (0xffff none)
constexpr uint16_t ACTION_LIMIT = 0x21F;         // battle action ids are below the action table's bound
constexpr uint32_t REC_STATE = 0x00;             // u8 of the member record: bit 2 = gambits on
constexpr uint8_t REC_GAMBITS_ON = 0x04;
constexpr uint16_t REGULARS = 6;                 // party ids 0..5: the six regulars (ilc_character order)
constexpr uint16_t ESPER_FIRST = 27;
constexpr uint16_t ESPER_LAST = 39;

// ---- the anchor table ------------------------------------------------------------------------------------------------
// Layout units (the field MRP's authoring units) map to canvas units with one scale, 15/13: the stock row pitch on
// screen (60) over the stock gap (52) [U: the root space is assumed to be the 1920x1080 authoring space]. y: the stock bottom row's top (G40.y = 228) sits at canvas 970
// (the measured party-target geometry: rows at 790 + 60 * visual row, four rows). x: each geometry has its
// member-name origin in canvas units (the party-target hand's tip: the measured hand at x 1184, 64 wide, and its reduced
// form scaled 3/4 about (1972, 1027)), and every other entry is placed by its layout distance from the name entry.
constexpr int32_t SCALE_NUM = 15;
constexpr int32_t SCALE_DEN = 13;
constexpr int32_t Y_LAYOUT_REF = 228;   // stock G40.y
constexpr int32_t Y_CANVAS_REF = 970;   // its canvas y

struct geometry_facts {
    // From the field MRP (stock values and the reduced transform's values, native_facts.h).
    int16_t g40_y;          // G40 rect y
    int16_t gap;            // G40 gap (row pitch)
    int16_t name_ex, name_ey;                       // G40 E19 (member name), row-relative
    int16_t gauge_ex, gauge_ey, gauge_ew, gauge_eh; // G40 E6 (HP gauge)
    int16_t mp_end;                                 // right end of the MP number (G40 E17 x + w)
    int16_t action_dx, action_dy;                   // G50 root minus G40 root
    int16_t label_ex, label_ey;                     // G50 E1 (ACTION label)
    uint16_t label_scale;                           // its text scale
    int16_t again_ex, again_ey, again_ew;           // G50 E0 (action gauge)
    // Canvas units.
    int16_t cursor_left, cursor_dy;                 // the party-target hand's sprite: x, and y below the row top
    int16_t cursor_w, cursor_h;                     // its size (native glove 64x54, reduced 48x40)
    int16_t name_x;                                 // the member name's left edge (the hand's tip: left + w - 2)
    int16_t edge_dx, edge_w, edge_dy, edge_inset;   // seat edge: x = name_x + edge_dx; h = pitch - 2 * inset
    int16_t numeral_dx;                             // numeral x = edge x + numeral_dx
    uint16_t numeral_scale;
    int16_t icon_gap, icon_dy, icon_w, icon_h, icon_stride;   // status icons, right of the MP number
    int16_t timer_dy;                                         // time left, under its icon
    uint16_t timer_scale;
    uint16_t suffix_scale;
    int16_t suffix_dx;
};

constexpr geometry_facts GEOMETRY[GEOMETRY_COUNT] = {
    // stock: G40 (202, 228, 1200, 58) gap 52; E19 (540, 8, 168, 32); E6 (680, 36, 200, 22); E17 (1014, 10, 26, 34);
    //        G50 (170, 230) E1 (236, 0) scale 80, E0 (292, 29, 200, 24)
    {228, 52, 540, 8, 680, 36, 200, 22, 1040, -32, 2, 236, 0, 80, 292, 29, 200,
     1184, 8, 64, 54, 1246, -12, 4, 0, 5, -18, 80, 8, 6, 24, 30, 30, 32, 45, 60, 6},
    // reduced: G40 (122, 242, 900, 44) gap 42; E19 (405, 3, 126, 24); E6 (510, 27, 150, 17); E17 ends at 778
    //          (x 761, w 20 at 85 %); G50 (78, 244) E1 (165, 0) scale 56, E0 (183, 20, 140, 17)
    {242, 42, 405, 3, 510, 27, 150, 17, 778, -44, 2, 165, 0, 56, 183, 20, 140,
     1381, 5, 48, 40, 1427, -10, 4, 0, 4, -15, 64, 6, 5, 18, 22, 24, 25, 38, 48, 5},
};
constexpr bool hand_tips_at_names()
{
    for (const geometry_facts& g : GEOMETRY) {
        if (g.name_x != g.cursor_left + g.cursor_w - 2) {
            return false;
        }
    }
    return true;
}
static_assert(hand_tips_at_names(), "the party-target hand's tip touches the member name");

int32_t scale(int32_t v)
{
    const int32_t p = v * SCALE_NUM;
    return p >= 0 ? (p + SCALE_DEN / 2) / SCALE_DEN : -((-p + SCALE_DEN / 2) / SCALE_DEN);
}

int16_t s16(int32_t v)
{
    return static_cast<int16_t>(v < -32768 ? -32768 : (v > 32767 ? 32767 : v));
}

// ---- colours (0x80 = 1.0) ---------------------------------------------------------------------------------------------
constexpr rgba half(uint8_t r, uint8_t g, uint8_t b, uint8_t a = 0x80)
{
    return {static_cast<uint8_t>((r + 1) / 2), static_cast<uint8_t>((g + 1) / 2), static_cast<uint8_t>((b + 1) / 2), a};
}
// The seat palette in ilc_colour order (the same RGB as the canvas test draw), and the gambit silver.
const rgba PALETTE[ILC_ENUM_COUNT(ILC_COLOURS)] = {
    half(70, 130, 240),   // sapphire
    half(230, 60, 70),    // ruby
    half(245, 120, 190),  // pink
    half(60, 210, 110),   // emerald
    half(240, 190, 50),   // gold
    half(170, 100, 235),  // amethyst
    half(205, 127, 50),   // bronze
};
constexpr rgba SILVER = half(192, 200, 216);
constexpr rgba GHOST = half(244, 212, 139);          // pale gold: the HP just lost
constexpr rgba TEXT_WHITE = {0x80, 0x80, 0x80, 0x80};
constexpr rgba GLOW_RED = {0x9C, 0x2C, 0x2C, 0x80};  // a little over 1.0 in red: the number glows
constexpr uint8_t DIM_ALPHA = 0x48;

// ---- module state ------------------------------------------------------------------------------------------------------
uintptr_t g_base = 0x120000;  // NOTADDR: the pinned image base until attach
counters g_counters = {};
volatile uint32_t g_enabled = 1;
volatile uint32_t g_parts = PART_ALL;
volatile uint32_t g_attached = 0;
patch::record_slot<rows_record> g_slot = {};
uint32_t g_published = 0;
uint64_t g_generation = 0;

// Co-op thread: settings.
uint8_t g_colour_mode = ILC_COLOUR_MODE_CHARACTER;
uint8_t g_seat_colours[ILC_SEATS] = {ILC_COLOUR_GOLD, ILC_COLOUR_SAPPHIRE, ILC_COLOUR_EMERALD, ILC_COLOUR_AMETHYST};
uint8_t g_character_colours[REGULARS] = {ILC_COLOUR_SAPPHIRE, ILC_COLOUR_PINK, ILC_COLOUR_AMETHYST,
                                         ILC_COLOUR_BRONZE,   ILC_COLOUR_EMERALD, ILC_COLOUR_GOLD};
uint8_t g_last_geometry = GEOMETRY_NONE;
int32_t g_forced_layout = -1;   // host tests: relayout's state as the sampler sees it

// Game thread: the pass's ghosts and the HP colour handlers' state (one game thread runs the UI).
ghost g_ghosts[SNAP_MEMBERS];
uint64_t g_glow_row = 0;       // HD-02 armed the glow for this row; HD-03 consumes it
uint32_t g_glow_rgba = 0;
uint64_t g_force_row = 0;      // HD-02 forced this row's digits: the cache goes back after the call
uint32_t g_force_cache = 0;
constexpr uint32_t COLOURED_MAX = 8;
uint64_t g_coloured[COLOURED_MAX];   // rows whose digits carry our colour (compared only, never dereferenced)
uint32_t g_coloured_next = 0;

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

// ---- the HP colour call rows -----------------------------------------------------------------------------------------
constexpr bool same_id(const char* a, const char* b)
{
    while (*a != '\0' && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

constexpr int32_t find_row(const char* id)
{
    for (uint32_t row = 0; row < patch::SITE_COUNT; ++row) {
        if (same_id(patch::SITES[row].id, id)) {
            return static_cast<int32_t>(row);
        }
    }
    return -1;
}

constexpr int32_t ROW_HP = find_row("HD-02");
constexpr int32_t ROW_DIGITS = find_row("HD-03");
static_assert(ROW_HP >= 0 && patch::SITES[ROW_HP].policy == ILC_SITE_POLICY_PATCH &&
                  patch::SITES[ROW_HP].kind == ILC_SITE_KIND_CALL &&
                  patch::SITES[ROW_HP].subsystem == ILC_SUBSYS_RENDER &&
                  patch::SITES[ROW_HP].rva == SITE_HP_CALL - sf::PINNED_BASE &&
                  (patch::SITES[ROW_HP].flags & ILC_SITE_FLAG_OPTIONAL) != 0,
              "HD-02 is an optional RENDER call row on the HP digits call");
static_assert(ROW_DIGITS >= 0 && patch::SITES[ROW_DIGITS].policy == ILC_SITE_POLICY_PATCH &&
                  patch::SITES[ROW_DIGITS].kind == ILC_SITE_KIND_CALL &&
                  patch::SITES[ROW_DIGITS].subsystem == ILC_SUBSYS_RENDER &&
                  patch::SITES[ROW_DIGITS].rva == SITE_DIGITS_CALL - sf::PINNED_BASE &&
                  (patch::SITES[ROW_DIGITS].flags & ILC_SITE_FLAG_OPTIONAL) != 0,
              "HD-03 is an optional RENDER call row on the digit setter call");

template <typename T>
bool load(uint64_t address, T* out)
{
    return load_game(out, reinterpret_cast<const void*>(address), sizeof(T));
}

bool store_u32(uint64_t address, uint32_t value)
{
    return store_game(reinterpret_cast<void*>(address), &value, sizeof(value));
}

int32_t coloured_index(uint64_t row)
{
    for (uint32_t k = 0; k < COLOURED_MAX; ++k) {
        if (g_coloured[k] == row) {
            return static_cast<int32_t>(k);
        }
    }
    return -1;
}

void mark_coloured(uint64_t row, bool on)
{
    const int32_t at = coloured_index(row);
    if (on && at < 0) {
        g_coloured[g_coloured_next] = row;
        g_coloured_next = (g_coloured_next + 1) % COLOURED_MAX;
    } else if (!on && at >= 0) {
        g_coloured[at] = 0;
    }
}

bool live()
{
    return __atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) != 0 && patch::subsystem_live(ILC_SUBSYS_RENDER);
}

// ---- co-op thread: the record ------------------------------------------------------------------------------------------
void withdraw_record()
{
    if (g_published != 0) {
        g_slot.withdraw();
        g_published = 0;
    }
}

bool sample(uint64_t now, rows_record* out)
{
    session::session_record srec;
    if (!session::read(&srec) || srec.head.epoch == 0 || (srec.head.flags & session::GATE_WORLD) == 0 ||
        control::current_state() != control::STATE_ON || !patch::subsystem_live(ILC_SUBSYS_RENDER)) {
        return false;
    }
    hud_snapshot snap;
    if (!snapshot::read(snap) || snap.head.epoch != srec.head.epoch) {
        return false;
    }
    zero_bytes(out, sizeof(*out));
    out->head.epoch = srec.head.epoch;
    out->head.generation = ++g_generation;
    out->sampled_ms = now;
    out->geometry = geometry_of(g_forced_layout >= 0 ? static_cast<uint32_t>(g_forced_layout) : relayout::status().state);
    out->enabled = enabled() ? 1 : 0;
    g_last_geometry = out->geometry;
    for (uint32_t s = 0; s < SNAP_SEATS && s < session::SEATS; ++s) {
        const session::seat_entry& e = srec.seat[s];
        const uint16_t member = (e.flags & session::SEATF_IN_SESSION) != 0 ? e.member : sf::MEMBER_NONE;
        out->seat_colour[s] = row_colour(g_colour_mode, g_seat_colours, g_character_colours, static_cast<uint8_t>(s + 1),
                                         member);
    }
    session::game_reader r(g_base);
    uintptr_t state = 0;
    const bool have_state = r.ptr(r.global(sf::BATTLE_STATE_PTR), &state) && state != 0;
    for (uint32_t k = 0; k < SNAP_MEMBERS; ++k) {
        row_extra& e = out->rows[k];
        e.member = sf::MEMBER_NONE;
        e.colour = COLOUR_NONE;
        e.action = 0xFFFF;
        if (k >= snap.member_count) {
            continue;
        }
        const hud_unit& u = snap.members[k];
        if (u.member == sf::MEMBER_NONE || u.member >= sf::MEMBER_IDS) {
            continue;
        }
        e.member = u.member;
        e.colour = u.seat >= 1 && u.seat <= SNAP_SEATS ? out->seat_colour[u.seat - 1] : COLOUR_NONE;
        uint8_t rec_state = 0;
        if (have_state &&
            r.u8(state + sf::MEMBER_RECORDS + static_cast<uintptr_t>(u.member) * sf::MEMBER_RECORD_SIZE + REC_STATE,
                 &rec_state)) {
            e.flags |= EXTRA_READ;
            if ((rec_state & REC_GAMBITS_ON) != 0) {
                e.flags |= EXTRA_GAMBITS_ON;
            }
        }
        if ((u.flags & UNIT_ACTOR) != 0) {
            const uintptr_t actor = targets::live_actor(r, u.handle, false);
            uint16_t action = 0xFFFF;
            if (actor != 0 && r.u16(actor + ACTOR_ACTION, &action)) {
                e.action = action;
                if (action < ACTION_LIMIT) {
                    e.flags |= EXTRA_ACTING;
                }
            }
        }
    }
    // "Choosing..." sits in the native ACTION label's place, left of the row: exactly where a guest's menu window slides
    // out (couch layout, layout.h). So it is shown only for Player 1 (whose menu is at the bottom-left) and only while
    // no guest window is open; a guest's own window already shows what that guest is choosing.
    frontend::seat_view views[frontend::SEATS];
    bool guest_open = false;
    for (uint32_t s = 0; s < frontend::SEATS; ++s) {
        frontend::read_seat(s, &views[s]);
        const bool open = (views[s].mode == menu::MODE_LIST || views[s].mode == menu::MODE_TARGET) &&
                          views[s].member_known != 0;
        guest_open = guest_open || (s != 0 && open);
    }
    for (uint32_t s = 0; s < frontend::SEATS && !guest_open; ++s) {
        const frontend::seat_view& v = views[s];
        if (s != 0 || (v.mode != menu::MODE_LIST && v.mode != menu::MODE_TARGET) || v.member_known == 0) {
            continue;
        }
        for (row_extra& e : out->rows) {
            if (e.member == v.member && e.member != sf::MEMBER_NONE) {
                e.flags |= EXTRA_CHOOSING;
                if (v.mode == menu::MODE_TARGET) {
                    e.flags |= EXTRA_CHOOSING_TARGET;
                }
            }
        }
    }
    return true;
}

// ---- game thread: the pass ----------------------------------------------------------------------------------------------
struct emitter {
    canvas& cv;
    uint32_t left;
    bool text(int32_t x, int32_t y, const char* s, rgba c, uint16_t sc)
    {
        if (left == 0) {
            bump(g_counters.capped);
            return false;
        }
        --left;
        return cv.text(s16(x), s16(y), s, c, sc);
    }
    bool sprite(atlas_id a, uint8_t page, uint8_t sheet, uint16_t cell, int32_t x, int32_t y, rgba c, int32_t w,
                int32_t h)
    {
        if (left == 0) {
            bump(g_counters.capped);
            return false;
        }
        if (w <= 0 || h <= 0) {
            return false;
        }
        --left;
        return cv.sprite(a, page, sheet, cell, s16(x), s16(y), c, static_cast<uint16_t>(w), static_cast<uint16_t>(h));
    }
};

const row_extra* extra_for(const rows_record* rec, uint16_t member)
{
    if (rec == nullptr) {
        return nullptr;
    }
    for (const row_extra& e : rec->rows) {
        if (e.member == member && member != sf::MEMBER_NONE) {
            return &e;
        }
    }
    return nullptr;
}

bool is_esper(uint16_t member) { return member >= ESPER_FIRST && member <= ESPER_LAST; }

// A short triangle-wave nudge (the native cursor's bob), 0..4 canvas units over 600 ms.
int32_t jab(uint64_t now)
{
    const uint32_t t = static_cast<uint32_t>(now % 600);
    return static_cast<int32_t>(t < 300 ? t * 4 / 300 : (600 - t) * 4 / 300);
}

// Sort key of a status icon: beneficial first, then the soonest to run out (unknown time last), then the bit.
uint32_t pick_key(const status_pick& p)
{
    const uint32_t group = beneficial(p.id) ? 0u : 1u;
    const uint32_t secs = p.seconds < 0 ? 0xFFFFu : (p.seconds > 0xFFFE ? 0xFFFEu : static_cast<uint32_t>(p.seconds));
    return group << 24 | secs << 8 | p.id;
}

}  // namespace

// Timed statuses that count down (the snapshot keeps a duration only for those), ordered by pick_key; the first
// `cap` are returned.
uint32_t pick_statuses(const hud_unit& u, uint32_t tick_scale, status_pick* out, uint32_t cap)
{
    status_pick all[SNAP_STATUS_IDS];
    uint32_t n = 0;
    for (uint32_t id = 1; id < SNAP_STATUS_IDS; ++id) {
        if (u.duration[id] <= 0 || id == 29) {
            continue;  // not counting down (HP Critical is not a timed status)
        }
        status_pick p{};
        p.id = static_cast<uint8_t>(id);
        p.seconds = (id == STATUS_DOOM || id == STATUS_PETRIFY) ? u.duration[id] - 1
                                                                : seconds_left(u.duration[id], tick_scale);
        uint32_t at = n++;
        while (at > 0 && pick_key(all[at - 1]) > pick_key(p)) {
            all[at] = all[at - 1];
            --at;
        }
        all[at] = p;
    }
    const uint32_t take = n < cap ? n : cap;
    copy_bytes(out, all, take * sizeof(status_pick));  // no C runtime call on the game thread
    return take;
}

namespace {

void draw_row(emitter& em, const anchor& a, const hud_unit& u, const row_extra* ex, const rows_record* rec,
              const hud_snapshot& s, uint32_t row_index, uint64_t now, uint32_t pieces)
{
    if (is_esper(u.member)) {
        return;  // the Esper row has its own layout (G47): nothing of ours on it
    }
    // Seat edge and numeral.
    const bool seated = u.seat >= 1 && u.seat <= SNAP_SEATS;
    const uint8_t colour_index = ex != nullptr ? ex->colour
                                               : (seated && rec != nullptr ? rec->seat_colour[u.seat - 1] : COLOUR_NONE);
    const rgba colour = seated ? colour_rgba(colour_index) : SILVER;
    const char glyph = seat_glyph(u.seat, (u.flags & UNIT_PARTY) != 0);
    const bool idle_gambit = !seated && ex != nullptr && (ex->flags & EXTRA_READ) != 0 &&
                             (ex->flags & EXTRA_GAMBITS_ON) == 0;
    rgba edge = colour;
    if (idle_gambit) {
        edge.a = DIM_ALPHA;
    }
    if ((pieces & PART_EDGE) != 0) {
        em.sprite(ATLAS_PARTYTOP_4_C, 0, 6, 1, a.edge_x, a.edge_y, edge, a.edge_w, a.edge_h);
    }
    if (glyph != 0 && (pieces & PART_EDGE) != 0) {
        const char text[2] = {glyph, '\0'};
        em.text(a.numeral_x, a.numeral_y, text, edge, a.numeral_scale);
        if (idle_gambit) {
            em.text(a.numeral_x, a.numeral_y, "/", edge, a.numeral_scale);  // gambits off: struck through
        }
    }
    if ((u.flags & UNIT_PRESENT) == 0) {
        return;
    }
    // Damage ghost on the HP gauge.
    if (row_index < SNAP_MEMBERS) {
        const uint32_t shown = ghost_step(g_ghosts[row_index], u.member, u.hp, u.hp_max, now);
        if (shown > u.hp && u.hp_max != 0 && (pieces & PART_GHOST) != 0) {
            const uint32_t max = u.hp_max;
            const int32_t x0 = a.gauge_x + static_cast<int32_t>((static_cast<uint64_t>(a.gauge_w) * u.hp) / max);
            const int32_t x1 = a.gauge_x + static_cast<int32_t>((static_cast<uint64_t>(a.gauge_w) *
                                                                 (shown < max ? shown : max)) / max);
            em.sprite(ATLAS_PARTYTOP_4_C, 0, 5, 1, x0, a.gauge_y, GHOST, x1 - x0, a.gauge_h);
        }
    }
    // Timed statuses with the time left.
    status_pick picks[MAX_STATUS_ICONS];
    const uint32_t tick_scale = (s.head.flags & SNAP_TICKS) != 0 ? s.tick_scale : 0;
    const uint32_t n = (pieces & PART_TIMERS) != 0 ? pick_statuses(u, tick_scale, picks, MAX_STATUS_ICONS) : 0;
    for (uint32_t k = 0; k < n; ++k) {
        const int32_t x = a.status_x + static_cast<int32_t>(k) * a.status_stride;
        rgba tint = TEXT_WHITE;
        if (beneficial(picks[k].id) && picks[k].seconds >= 0 && picks[k].seconds <= static_cast<int32_t>(DIM_LAST_S)) {
            tint.a = DIM_ALPHA;
        }
        em.sprite(ATLAS_PARTYTOP_4_C, 3, 0, picks[k].id, x, a.status_y, tint, a.status_w, a.status_h);
        char t[8];
        if (format_timer(u.duration[picks[k].id], tick_scale, picks[k].id, t, sizeof(t)) != 0) {
            em.text(x + a.timer_dx, a.status_y + a.timer_dy, t, tint, a.timer_scale);
        }
    }
    // Action line: "Choosing..." in the ACTION label's place while the seat's menu is open and nothing is performed.
    // Never the action's target (Marco, 2026-11-16: "Having the name of the target only brings visual polution.
    // There are target lines for that just as in vanilla.").
    if (ex == nullptr || (pieces & PART_ACTION) == 0) {
        return;
    }
    const bool acting = (ex->flags & EXTRA_ACTING) != 0;
    if ((ex->flags & EXTRA_CHOOSING) != 0 && !acting) {
        em.text(a.label_x, a.label_y,
                (ex->flags & EXTRA_CHOOSING_TARGET) != 0 ? "Choosing target..." : "Choosing...", TEXT_WHITE,
                a.label_scale);
    }
}

}  // namespace

// ---- geometry -----------------------------------------------------------------------------------------------------------
uint8_t geometry_of(uint32_t layout_state)
{
    switch (layout_state) {
    case relayout::LAYOUT_REDUCED:
    case relayout::LAYOUT_REDUCED_OTHER:
        return GEOMETRY_REDUCED;
    case relayout::LAYOUT_SETTLING:
    case relayout::LAYOUT_STOCK:
    case relayout::LAYOUT_FAILED:
        return GEOMETRY_STOCK;
    default:
        return GEOMETRY_NONE;  // not looked at, unloaded, invalid, foreign or busy: no layout we know
    }
}

int32_t row_layout_y(uint8_t geometry, uint32_t index, uint32_t rows)
{
    const geometry_facts& g = GEOMETRY[geometry < GEOMETRY_COUNT ? geometry : 0];
    return g.g40_y - g.gap * static_cast<int32_t>(rows - 1 - index);
}

int32_t canvas_y(uint8_t geometry, int32_t layout_y)
{
    (void)geometry;  // one mapping for both: the reduced rows keep the stock parent
    return Y_CANVAS_REF + scale(layout_y - Y_LAYOUT_REF);
}

bool row_anchor(uint8_t geometry, uint32_t index, uint32_t rows, anchor* out)
{
    if (out == nullptr || geometry >= GEOMETRY_COUNT || rows == 0 || rows > SNAP_MEMBERS || index >= rows) {
        return false;
    }
    const geometry_facts& g = GEOMETRY[geometry];
    zero_bytes(out, sizeof(*out));
    const int32_t top = canvas_y(geometry, row_layout_y(geometry, index, rows));
    const int32_t pitch = scale(g.gap);
    const auto x_of = [&](int32_t layout_x) { return g.name_x + scale(layout_x - g.name_ex); };
    out->top = s16(top);
    out->pitch = s16(pitch);
    out->name_x = g.name_x;
    out->name_y = s16(top + scale(g.name_ey));
    out->edge_x = s16(g.name_x + g.edge_dx);
    out->edge_w = g.edge_w;
    out->edge_y = s16(top + g.edge_dy + g.edge_inset);
    out->edge_h = s16(pitch - 2 * g.edge_inset);
    out->numeral_x = s16(out->edge_x + g.numeral_dx);
    out->numeral_y = out->name_y;
    out->numeral_scale = g.numeral_scale;
    out->gauge_x = s16(x_of(g.gauge_ex));
    out->gauge_y = s16(top + scale(g.gauge_ey));
    out->gauge_w = s16(scale(g.gauge_ew));
    out->gauge_h = s16(scale(g.gauge_eh));
    out->status_x = s16(x_of(g.mp_end) + g.icon_gap);
    out->status_y = s16(top + g.icon_dy);
    out->status_w = g.icon_w;
    out->status_h = g.icon_h;
    out->status_stride = g.icon_stride;
    out->timer_dx = 0;
    out->timer_dy = g.timer_dy;
    out->timer_scale = g.timer_scale;
    out->label_x = s16(x_of(g.action_dx + g.label_ex));
    out->label_y = s16(top + scale(g.action_dy + g.label_ey));
    out->label_scale = g.label_scale;
    out->suffix_x = s16(x_of(g.action_dx + g.again_ex + g.again_ew) + g.suffix_dx);
    out->suffix_y = s16(top + scale(g.action_dy + g.again_ey));
    out->suffix_scale = g.suffix_scale;
    out->cursor_w = g.cursor_w;
    out->cursor_h = g.cursor_h;
    out->cursor_x = g.cursor_left;
    out->cursor_y = s16(top + g.cursor_dy);
    return true;
}

// ---- rules ----------------------------------------------------------------------------------------------------------------
bool low_hp(uint32_t hp, uint32_t hp_max, uint32_t status)
{
    if (hp == 0 || (status & STATUS_KO) != 0) {
        return false;
    }
    if ((status & STATUS_HP_CRITICAL) != 0) {
        return true;
    }
    const uint64_t base = (status & STATUS_BUBBLE) != 0 ? 2ull * hp_max : hp_max;
    return hp < base / 5;
}

uint32_t glow_rgba(uint64_t now_ms)
{
    // A smoothstep triangle: 0 -> 1 -> 0 over the period; the alpha dips to GLOW_LOW_ALPHA at the middle.
    const uint32_t t = static_cast<uint32_t>(now_ms % GLOW_PERIOD_MS);
    const uint32_t tri = static_cast<uint32_t>(t * 2 <= GLOW_PERIOD_MS ? (t * 2048) / GLOW_PERIOD_MS
                                                                        : ((GLOW_PERIOD_MS - t) * 2048) / GLOW_PERIOD_MS);
    const uint32_t smooth = (tri * tri * (3072 - 2 * tri)) >> 20;  // 0..1024
    rgba c = GLOW_RED;
    c.a = static_cast<uint8_t>(0x80 - ((0x80 - GLOW_LOW_ALPHA) * smooth) / 1024);
    return pack(c);
}

int32_t seconds_left(int32_t ticks, uint32_t tick_scale)
{
    if (ticks <= 0 || tick_scale == 0 || tick_scale > 8) {
        return -1;
    }
    const uint64_t per_second = 300ull * tick_scale;
    return static_cast<int32_t>((static_cast<uint64_t>(ticks) + per_second - 1) / per_second);
}

uint32_t format_timer(int32_t ticks, uint32_t tick_scale, uint32_t status_id, char* out, uint32_t cap)
{
    if (out == nullptr || cap < 4 || status_id >= SNAP_STATUS_IDS) {
        return 0;
    }
    out[0] = '\0';
    int32_t value = 0;
    char unit = 0;
    if (status_id == STATUS_DOOM || status_id == STATUS_PETRIFY) {
        if (ticks <= 0) {
            return 0;
        }
        value = ticks - 1 > 99 ? 99 : ticks - 1;   // the step counter, as the overhead count shows it
    } else {
        const int32_t secs = seconds_left(ticks, tick_scale);
        if (secs < 0) {
            return 0;
        }
        if (secs < 100) {
            value = secs;
        } else {
            value = (secs + 59) / 60;               // minutes, rounded up
            value = value > 99 ? 99 : value;
            unit = 'm';
        }
    }
    uint32_t n = 0;
    if (value >= 10) {
        out[n++] = static_cast<char>('0' + value / 10);
    }
    out[n++] = static_cast<char>('0' + value % 10);
    if (unit != 0) {
        out[n++] = unit;
    }
    out[n] = '\0';
    return n;
}

bool beneficial(uint32_t status_id)
{
    switch (status_id) {
    case 18:  // Protect
    case 19:  // Shell
    case 20:  // Haste
    case 21:  // Bravery
    case 22:  // Faith
    case 23:  // Reflect
    case 24:  // Invisible
    case 25:  // Regen
    case 26:  // Float
    case 28:  // Bubble
    case 30:  // Libra
        return true;
    default:
        return false;
    }
}

char seat_glyph(uint8_t seat, bool party_member)
{
    if (seat >= 1 && seat <= SNAP_SEATS) {
        return static_cast<char>('0' + seat);
    }
    return party_member ? 'G' : 0;
}

uint8_t row_colour(uint8_t colour_mode, const uint8_t seat_colours[ILC_SEATS], const uint8_t character_colours[6],
                   uint8_t seat, uint16_t member)
{
    if (seat < 1 || seat > ILC_SEATS) {
        return COLOUR_NONE;
    }
    uint8_t c = seat_colours[seat - 1];
    if (colour_mode == ILC_COLOUR_MODE_CHARACTER && member < REGULARS) {
        c = character_colours[member];
    }
    return c < ILC_ENUM_COUNT(ILC_COLOURS) ? c : COLOUR_NONE;
}

rgba colour_rgba(uint8_t colour_index)
{
    return colour_index < ILC_ENUM_COUNT(ILC_COLOURS) ? PALETTE[colour_index] : SILVER;
}

uint32_t ghost_step(ghost& g, uint16_t member, uint32_t hp, uint32_t hp_max, uint64_t now_ms)
{
    if (g.member != member || g.hp_max != hp_max || hp_max == 0) {
        g = ghost{member, 0, hp_max, hp, hp, 0};   // another member in this row, or the maximum changed: start over
        return hp;
    }
    if (hp < g.hp) {
        // A hit: the ghost keeps what it shows now (a second hit extends it) and holds again.
        const uint32_t showing = ghost_step(g, member, g.hp, hp_max, now_ms);
        g.from = showing;
        g.hit_ms = now_ms;
        g.hp = hp;
        return g.from;
    }
    g.hp = hp;
    if (hp >= g.from || g.hit_ms == 0) {
        g.from = hp;   // healed past the ghost (a heal shows no ghost)
        return hp;
    }
    const uint64_t since = now_ms >= g.hit_ms ? now_ms - g.hit_ms : 0;
    if (since <= GHOST_HOLD_MS) {
        return g.from;
    }
    if (since >= GHOST_HOLD_MS + GHOST_DRAIN_MS) {
        g.from = hp;
        return hp;
    }
    // Drain: smoothstep from `from` down to hp.
    const uint64_t t = ((since - GHOST_HOLD_MS) * 1024) / GHOST_DRAIN_MS;   // 0..1024
    const uint64_t s = (t * t * (3072 - 2 * t)) >> 20;                       // 0..1024
    const uint64_t span = g.from - hp;
    return g.from - static_cast<uint32_t>((span * s) / 1024);
}

// ---- module -----------------------------------------------------------------------------------------------------------------
bool attach(uintptr_t image_base)
{
    g_base = image_base;
    const uint32_t hp = static_cast<uint32_t>(ROW_HP);
    const uint32_t digits = static_cast<uint32_t>(ROW_DIGITS);
    const bool ok = patch::set_handler(hp, hp_call) && patch::set_after(hp, hp_after) &&
                    patch::set_handler(digits, digits_call) && patch::set_site_armed(hp, true) &&
                    patch::set_site_armed(digits, true);
    if (!ok) {
        patch::set_site_armed(hp, false);
        patch::set_site_armed(digits, false);
    }
    __atomic_store_n(&g_attached, ok ? 1u : 0u, __ATOMIC_RELEASE);
    return ok;
}

void configure_from_settings(const ilc_settings& s)
{
    g_colour_mode = s.colour_mode;
    for (uint32_t k = 0; k < ILC_SEATS; ++k) {
        g_seat_colours[k] = s.seats[k].colour;
    }
    for (uint32_t k = 0; k < REGULARS; ++k) {
        g_character_colours[k] = s.character_colour[k];
    }
}

void step(uint64_t now_ms)
{
    static rows_record next;
    if (!sample(now_ms, &next)) {
        withdraw_record();
        return;
    }
    {
        ILC_LOOPBACK_COPY_STATIC(rows_record, FAM_ROWS, next, seam);  // the seam (host tests: the loopback codec)
        g_slot.publish(seam);
    }
    g_published = 1;
    bump(g_counters.samples);
}

void stop()
{
    g_slot.withdraw();
    g_published = 0;
}

bool read(rows_record* out)
{
    return g_slot.read(*out);
}

void set_enabled(bool on)
{
    __atomic_store_n(&g_enabled, on ? 1u : 0u, __ATOMIC_RELEASE);
}

bool enabled()
{
    return __atomic_load_n(&g_enabled, __ATOMIC_ACQUIRE) != 0;
}

void set_parts(uint32_t p)
{
    __atomic_store_n(&g_parts, p & PART_ALL, __ATOMIC_RELEASE);
}

uint32_t parts()
{
    return __atomic_load_n(&g_parts, __ATOMIC_ACQUIRE);
}

void draw(canvas& cv, const driver::pass_input& in)
{
    if (in.snap == nullptr || !enabled()) {
        return;
    }
    const hud_snapshot& s = *in.snap;
    if ((s.head.flags & SNAP_PARTY) == 0 || s.member_count == 0) {
        return;
    }
    rows_record rec;
    const bool have = g_slot.read(rec) && rec.head.epoch == s.head.epoch && rec.sampled_ms <= in.now_ms + RECORD_STALE_MS &&
                      in.now_ms <= rec.sampled_ms + RECORD_STALE_MS;
    if (!have) {
        bump(g_counters.no_record);
        return;  // the geometry is in the record: without it nothing is placed (the native list is unchanged)
    }
    if (rec.enabled == 0) {
        return;
    }
    if (rec.geometry >= GEOMETRY_COUNT) {
        bump(g_counters.no_geometry);
        return;
    }
    // Rows are bottom-anchored by the number of rows the HUD shows: rank them among the used slots.
    uint32_t rows = 0;
    for (uint32_t k = 0; k < s.member_count && k < SNAP_MEMBERS; ++k) {
        rows += s.members[k].member != sf::MEMBER_NONE ? 1u : 0u;
    }
    // The pass's own cap, or less when the frame keeps the open menu windows' floor back (canvas::reserve).
    const uint32_t share = cv.room() < PASS_MAX_COMMANDS ? cv.room() : PASS_MAX_COMMANDS;
    emitter em{cv, share};
    const uint32_t pieces = parts();
    anchor at[SNAP_MEMBERS];
    bool placed[SNAP_MEMBERS];
    uint32_t on_row[SNAP_MEMBERS];   // party-target hands already on each row
    zero_bytes(at, sizeof(at));       // explicit: the game thread calls no C runtime function
    zero_bytes(placed, sizeof(placed));
    zero_bytes(on_row, sizeof(on_row));
    uint32_t rank = 0;
    for (uint32_t k = 0; k < s.member_count && k < SNAP_MEMBERS; ++k) {
        const hud_unit& u = s.members[k];
        if (u.member == sf::MEMBER_NONE) {
            continue;
        }
        placed[k] = row_anchor(rec.geometry, rank++, rows, &at[k]);
        if (placed[k]) {
            draw_row(em, at[k], u, extra_for(&rec, u.member), &rec, s, k, in.now_ms, pieces);
        }
    }
    // Party-target cursors: each seat whose target page points at a party row, in that seat's colour; a second
    // hand on the same row steps left.
    for (uint32_t seat = 0; seat < SNAP_SEATS && (pieces & PART_CURSORS) != 0; ++seat) {
        const seat_target& t = s.seats[seat];
        if (t.on_page == 0 || t.handle == 0) {
            continue;
        }
        for (uint32_t k = 0; k < s.member_count && k < SNAP_MEMBERS; ++k) {
            const hud_unit& u = s.members[k];
            if (!placed[k] || (u.flags & UNIT_ACTOR) == 0 || u.handle != t.handle) {
                continue;
            }
            const anchor& a = at[k];
            const int32_t step = static_cast<int32_t>(on_row[k]++) * (a.cursor_w / 3);
            em.sprite(ATLAS_CURSOL_4_P, 0, 0, 0, a.cursor_x - step + jab(in.now_ms), a.cursor_y,
                      colour_rgba(rec.seat_colour[seat]), a.cursor_w, a.cursor_h);
            break;
        }
    }
    if (em.left != share) {
        bump(g_counters.drawn_frames);
    }
}

// ---- the HP colour call rows (game thread, inside the party row update) ----------------------------------------------------
int32_t hp_call(patch::ctx* c, uint32_t epoch)
{
    (void)epoch;
    g_glow_row = 0;
    if (!live() || c->rcx == 0 || c->rbx == 0) {
        bump(g_counters.hp_declined);
        patch::note_declined(static_cast<uint32_t>(ROW_HP));
        return patch::RUN_ORIGINAL;
    }
    const uint64_t row = c->rcx;
    const int32_t coloured = coloured_index(row);
    uint32_t hp_max = 0;
    const bool glow = enabled() && (parts() & PART_GLOW) != 0 && load(c->rbx + HUD_RECORD_HP_MAX, &hp_max) &&
                      low_hp(static_cast<uint32_t>(c->rdx), hp_max, static_cast<uint32_t>(c->r8));
    if (!glow && coloured < 0) {
        return patch::RUN_ORIGINAL;  // the common case: nothing of ours on this row
    }
    uint32_t cache = 0;
    if (!load(row + ROW_HP_CACHE, &cache) || !store_u32(row + ROW_HP_CACHE, HP_CACHE_FORCE)) {
        bump(g_counters.hp_declined);
        return patch::RUN_ORIGINAL;
    }
    g_force_row = row;
    g_force_cache = cache;
    if (glow) {
        g_glow_row = row;
        g_glow_rgba = glow_rgba(patch::now_ms());
    }
    mark_coloured(row, glow);  // not low any more: forced once with the native colour, then left alone
    bump(g_counters.forced);
    return patch::CALL_AROUND;
}

void hp_after(patch::ctx* c, uint32_t epoch)
{
    (void)c;
    (void)epoch;
    if (g_force_row != 0) {
        uint32_t cache = 0;
        if (load(g_force_row + ROW_HP_CACHE, &cache) && cache == HP_CACHE_FORCE) {
            store_u32(g_force_row + ROW_HP_CACHE, g_force_cache);  // the gauge update after it sees the cache as stock
        }
        g_force_row = 0;
    }
    g_glow_row = 0;
}

int32_t digits_call(patch::ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (g_glow_row != 0 && c->rdi == g_glow_row && live()) {
        c->r9 = (c->r9 & ~uint64_t{0xFFFFFFFF}) | g_glow_rgba;  // NOTADDR: r9d is the colour argument
        bump(g_counters.glowed);
    }
    g_glow_row = 0;
    return patch::RUN_ORIGINAL;
}

const counters* stats()
{
    return &g_counters;
}

status_view status()
{
    status_view v = {};
    v.attached = __atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) != 0 ? 1 : 0;
    v.enabled = enabled() ? 1 : 0;
    v.hp_rows_hooked = patch::trampoline(static_cast<uint32_t>(ROW_HP)) != nullptr &&
                               patch::trampoline(static_cast<uint32_t>(ROW_DIGITS)) != nullptr
                           ? 1
                           : 0;
    v.geometry = g_last_geometry;
    return v;
}

// ---- host tests -------------------------------------------------------------------------------------------------------------
void test_reset()
{
    zero_bytes(const_cast<counters*>(&g_counters), sizeof(counters));
    g_slot.withdraw();
    g_published = 0;
    __atomic_store_n(&g_enabled, 1u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_parts, static_cast<uint32_t>(PART_ALL), __ATOMIC_RELEASE);
    for (ghost& g : g_ghosts) {
        g = ghost{sf::MEMBER_NONE, 0, 0, 0, 0, 0};
    }
    zero_bytes(g_coloured, sizeof(g_coloured));
    g_coloured_next = 0;
    g_glow_row = 0;
    g_glow_rgba = 0;
    g_force_row = 0;
    g_force_cache = 0;
    g_last_geometry = GEOMETRY_NONE;
    g_forced_layout = -1;
    g_colour_mode = ILC_COLOUR_MODE_CHARACTER;
}

void test_publish(const rows_record& r)
{
    ILC_LOOPBACK_COPY_STATIC(rows_record, FAM_ROWS, r, seam);
    g_slot.publish(seam);
    g_published = 1;
}

bool test_read(rows_record* out)
{
    return out != nullptr && g_slot.read(*out);
}

void test_set_image_base(uintptr_t base)
{
    g_base = base;
}

void test_force_layout_state(int32_t state)
{
    g_forced_layout = state;
}

void test_set_attached(bool on)
{
    __atomic_store_n(&g_attached, on ? 1u : 0u, __ATOMIC_RELEASE);
}

int32_t test_row(uint32_t which)
{
    return which == 0 ? ROW_HP : (which == 1 ? ROW_DIGITS : -1);
}

bool test_glow_pending(uint64_t* row, uint32_t* rgba_out)
{
    if (row != nullptr) {
        *row = g_glow_row;
    }
    if (rgba_out != nullptr) {
        *rgba_out = g_glow_rgba;
    }
    return g_glow_row != 0;
}

uint32_t test_coloured_rows()
{
    uint32_t n = 0;
    for (uint64_t r : g_coloured) {
        n += r != 0 ? 1u : 0u;
    }
    return n;
}

const ghost* test_ghosts()
{
    return g_ghosts;
}

}  // namespace ilc::hud::party_rows
