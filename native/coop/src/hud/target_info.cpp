// Ivalice Legacy co-op module: the expanded Target Info: pure rules, the drawing, the pass and TI-01 (see
// target_info.h). The co-op thread's side (the view it composes from the snapshot and the game) is in
// target_info_sample.cpp.
//
// Game-thread code here (draw, compose, ti01) never blocks, allocates, waits or calls the C runtime: copies go
// through detail::copy_bytes, buffers are filled by hand.
#include "hud/target_info.h"
#include "runtime/loopback.h"

#include "control/control.h"
#include "firewall/firewall.h"
#include "hud/hud_internal.h"
#include "hud/layout.h"
#include "hud/target_info_facts.h"
#include "hud/target_info_internal.h"
#include "hud/target_info_native.h"
#include "ilc/bridge_abi.h"
#include "patch/patch_engine.h"

namespace ilc::hud::target_info {

namespace detail_ti {

view_slot g_view = {};
volatile uint32_t g_enabled = 1;
volatile uint32_t g_attached = 0;
volatile uint64_t g_last_drawn = 0;   // game thread writes (the pass), any thread reads
volatile int32_t g_test_owned = -1;   // host tests: P1's ownership instead of the firewall record (-1 = the record)
volatile uint64_t g_test_stamp = 0;   // host tests: the menu layer's stamp instead of the driver's (0 = the driver)
counters g_counters = {};

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

void publish(const view& v_in)
{
    ILC_LOOPBACK_COPY_STATIC(view, FAM_TI_VIEW, v_in, v);  // the seam (host tests: the loopback codec)
    const uint32_t odd = (g_view.seq + 1u) | 1u;
    __atomic_store_n(&g_view.seq, odd, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    hud::detail::copy_bytes(const_cast<view*>(&g_view.value), &v, sizeof(view));
    g_view.valid = 1;
    __atomic_store_n(&g_view.seq, odd + 1u, __ATOMIC_RELEASE);
}

void withdraw()
{
    const uint32_t odd = (g_view.seq + 1u) | 1u;
    __atomic_store_n(&g_view.seq, odd, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    g_view.valid = 0;
    __atomic_store_n(&g_view.seq, odd + 1u, __ATOMIC_RELEASE);
}

}  // namespace detail_ti

using namespace detail_ti;
using hud::detail::copy_bytes;
using hud::detail::zero_bytes;

namespace {

namespace tf = tifacts;

// ---- TI-01 ----------------------------------------------------------------------------------------------------------
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

constexpr int32_t ROW_TI = find_row("TI-01");
static_assert(ROW_TI >= 0 && patch::SITES[ROW_TI].policy == ILC_SITE_POLICY_PATCH &&
                  patch::SITES[ROW_TI].kind == ILC_SITE_KIND_CALL &&
                  patch::SITES[ROW_TI].subsystem == ILC_SUBSYS_RENDER &&
                  patch::SITES[ROW_TI].rva == tf::TI_OPEN_CALL - tf::PINNED_BASE,
              "TI-01 is the RENDER call row on the native TARGET INFO window's open call");

// ---- small text helpers (no C runtime) ---------------------------------------------------------------------------
uint32_t put_text(char* out, uint32_t at, uint32_t cap, const char* s)
{
    while (*s != '\0' && at + 1 < cap) {
        out[at++] = *s++;
    }
    out[at] = '\0';
    return at;
}

uint32_t put_u32(char* out, uint32_t at, uint32_t cap, uint32_t v)
{
    char digits[10];
    uint32_t n = 0;
    do {
        digits[n++] = static_cast<char>('0' + v % 10);
        v /= 10;
    } while (v != 0 && n < 10);
    while (n > 0 && at + 1 < cap) {
        out[at++] = digits[--n];
    }
    out[at] = '\0';
    return at;
}

uint32_t length_of(const char* s, uint32_t cap)
{
    uint32_t n = 0;
    while (n < cap && s[n] != '\0') {
        ++n;
    }
    return n;
}

// ---- colours (PS2 scale: 0x80 = 1.0) -------------------------------------------------------------------------------
constexpr rgba C_HEADER = {0x80, 0x80, 0x80, 0x66};   // the native header's own colour
constexpr rgba C_RULE = {0x6B, 0x70, 0x80, 0x3C};
constexpr rgba C_NAME = {0x6C, 0x73, 0x80, 0x80};
constexpr rgba C_DANGER = {0x80, 0x38, 0x38, 0x80};
constexpr rgba C_LABEL = {0x73, 0x76, 0x7C, 0x80};
constexpr rgba C_DIM = {0x5C, 0x63, 0x6F, 0x80};
constexpr rgba C_NUMBER = {0x73, 0x76, 0x7C, 0x80};
constexpr rgba C_BAR_BACK = {0x04, 0x06, 0x0C, 0x5A};
constexpr rgba C_BAR_GHOST = {0x7A, 0x6A, 0x45, 0x6C};
constexpr rgba C_BAR_HP = {0x6E, 0x76, 0x80, 0x80};
constexpr rgba C_IMMUNE = {0x40, 0x40, 0x44, 0x70};
constexpr rgba C_ITEM = {0x67, 0x70, 0x80, 0x80};
constexpr rgba C_RARE = {0x80, 0x61, 0x66, 0x80};
constexpr rgba C_STEAL = {0x60, 0x78, 0x6B, 0x80};
constexpr rgba C_UNKNOWN = {0x50, 0x54, 0x5C, 0x80};
constexpr rgba C_CHIP = {0x67, 0x6C, 0x74, 0x80};

rgba faded(rgba c, uint8_t alpha)
{
    c.a = static_cast<uint8_t>((static_cast<uint32_t>(c.a) * alpha) / 0x80u);
    return c;
}

rgba unpack(uint32_t packed)
{
    return {static_cast<uint8_t>(packed), static_cast<uint8_t>(packed >> 8), static_cast<uint8_t>(packed >> 16),
            static_cast<uint8_t>(packed >> 24)};
}

// ---- layout (authoring units) -------------------------------------------------------------------------------------
constexpr int16_t X0 = corner::MARGIN_X;
constexpr int16_t EDGE_DX = -12;          // the seat edge, left of the block
constexpr uint16_t EDGE_W = 3;
constexpr int16_t NAME_DY = 28;           // below the header line
constexpr uint16_t LEVEL_SCALE = 70;
constexpr uint16_t NAME_SCALE = 90;
constexpr int16_t NAME_DX = 64;
constexpr int16_t LINE_HP = 34;           // header->name 28, name->HP line 34
constexpr uint16_t LABEL_SCALE = 65;
constexpr uint16_t NUMBER_SCALE = 85;
constexpr uint16_t PERCENT_SCALE = 70;
constexpr int16_t BAR_DY = 28;            // HP text -> bar
constexpr uint16_t BAR_W = 260;
constexpr uint16_t BAR_H = 6;
constexpr int16_t LINE_GAP = 14;          // bar -> next line
constexpr uint16_t ELEMENT_SIZE = 26;
constexpr int16_t ELEMENT_STEP = 30;
constexpr int16_t ICONS_DX = 70;
constexpr uint16_t STATUS_W = 22;
constexpr uint16_t STATUS_H = 27;
constexpr int16_t STATUS_STEP = 28;
constexpr int16_t STATUS_STEP_TIMED = 46;
constexpr uint16_t TIMER_SCALE = 55;
constexpr int16_t LINE_ROW = 32;
constexpr int16_t DROPS_GAP = 8;
constexpr uint16_t DROP_SCALE = 70;
constexpr int16_t DROP_STEP = 22;
constexpr int16_t DROP_TAG_DX = 200;
constexpr uint16_t TAG_SCALE = 55;
constexpr uint16_t CHIP = 8;
constexpr uint16_t BADGE_W = 30;
constexpr uint16_t BADGE_H = 36;
constexpr int16_t BADGE_DY = -12;

// Atlas cells (the atlases' own sprite tables).
constexpr uint8_t PAGE_ELEMENTS = 10;     // battle_4_c 10.0, cell = element bit
constexpr uint8_t PAGE_STATUS = 3;        // partytop_4_c 3.0, cell = status bit
constexpr uint16_t CELL_LIBRA = 30;       // partytop_4_c 3.0
constexpr uint8_t PAGE_RED_X = 13;        // battle_4_c 13.0 cell 1 (the native TI's Anti-Libra mark, G22 E16)
constexpr uint16_t CELL_RED_X = 1;

class pen {
public:
    pen(canvas& cv, uint8_t alpha) : cv_(cv), alpha_(alpha), start_(cv.view().count) {}
    // The pass's own share, and never into what the frame keeps back for later passes (canvas::reserve).
    bool room() const { return cv_.view().count - start_ < MAX_COMMANDS_TI && cv_.room() != 0; }
    // Room for `n` more commands: a section is drawn whole or not at all (compose), never cut mid-row.
    bool fits(uint32_t n) const { return added() + n <= MAX_COMMANDS_TI && cv_.room() >= n; }
    void text(int16_t x, int16_t y, const char* s, rgba c, uint16_t scale)
    {
        if (room() && s[0] != '\0') {
            cv_.text(x, y, s, faded(c, alpha_), scale);
        }
    }
    void sprite(atlas_id atlas, uint8_t page, uint16_t cell, int16_t x, int16_t y, rgba tint, uint16_t w, uint16_t h)
    {
        if (room()) {
            cv_.sprite(atlas, page, 0, cell, x, y, faded(tint, alpha_), w, h);
        }
    }
    void bar(int16_t x, int16_t y, uint16_t w, uint16_t h, rgba c)
    {
        if (room() && w != 0 && h != 0) {
            cv_.bar(x, y, w, h, faded(c, alpha_));
        }
    }
    uint32_t added() const { return cv_.view().count - start_; }
    canvas& cv() { return cv_; }
    uint8_t alpha() const { return alpha_; }

private:
    canvas& cv_;
    uint8_t alpha_;
    uint32_t start_;
};

uint16_t bar_width(uint32_t value, uint32_t max)
{
    if (max == 0 || value == 0) {
        return 0;
    }
    const uint64_t w = (static_cast<uint64_t>(value > max ? max : value) * BAR_W) / max;
    return static_cast<uint16_t>(w == 0 ? 1 : w);
}

uint32_t bits_of(uint8_t mask)
{
    uint32_t n = 0;
    for (; mask != 0; mask = static_cast<uint8_t>(mask & (mask - 1))) {
        ++n;
    }
    return n;
}

const char* tag_of(const drop_line& d)
{
    switch (d.kind) {
    case STEAL: return "steal";
    case SPECIAL: return "special";
    case POACH: return "poach";
    default: return "";
    }
}

rgba colour_of(const drop_line& d)
{
    if (d.state == ITEM_UNKNOWN) {
        return C_UNKNOWN;
    }
    if (d.kind == STEAL) {
        return C_STEAL;
    }
    if ((d.flags & DROP_RARE) != 0 || d.kind == SPECIAL) {
        return C_RARE;
    }
    return C_ITEM;
}

}  // namespace

// ---- the corner header --------------------------------------------------------------------------------------------
namespace corner {

box header_box(side s)
{
    const int32_t x = s == LEFT ? MARGIN_X : AUTHOR_W - MARGIN_X - RULE_W;
    return {x, HEADER_Y, RULE_W, RULE_DY + RULE_H};
}

box header_box_on(side s, uint32_t screen_w, uint32_t screen_h)
{
    const box b = header_box(s);
    // Uniform scale, centred: s = min(w / 1920, h / 1080), in 1/65536 steps; rounded to the nearest pixel.
    const uint64_t sx = (static_cast<uint64_t>(screen_w) << 16) / static_cast<uint64_t>(AUTHOR_W);
    const uint64_t sy = (static_cast<uint64_t>(screen_h) << 16) / static_cast<uint64_t>(AUTHOR_H);
    const uint64_t k = sx < sy ? sx : sy;
    const int64_t ox = (static_cast<int64_t>(screen_w) - static_cast<int64_t>((AUTHOR_W * k) >> 16)) / 2;
    const int64_t oy = (static_cast<int64_t>(screen_h) - static_cast<int64_t>((AUTHOR_H * k) >> 16)) / 2;
    const auto at = [k](int64_t v) { return static_cast<int32_t>((v * static_cast<int64_t>(k) + 0x8000) >> 16); };
    return {static_cast<int32_t>(ox) + at(b.x), static_cast<int32_t>(oy) + at(b.y), at(b.w), at(b.h)};
}

int32_t text_width(uint32_t chars, uint16_t scale)
{
    return static_cast<int32_t>((static_cast<uint64_t>(chars) * ADVANCE_X100 * scale) / 10000u);
}

void header(canvas& cv, side s, int16_t y, const char* label, uint8_t alpha)
{
    const box b = header_box(s);
    const uint32_t n = length_of(label, TEXT_MAX);
    const int32_t x_text = s == LEFT ? b.x : b.x + b.w - text_width(n, HEADER_SCALE);
    cv.text(static_cast<int16_t>(x_text), y, label, faded(C_HEADER, alpha), HEADER_SCALE);
    cv.bar(static_cast<int16_t>(b.x), static_cast<int16_t>(y + RULE_DY), RULE_W, RULE_H, faded(C_RULE, alpha));
}

}  // namespace corner

// ---- pure rules ---------------------------------------------------------------------------------------------------
int32_t select_seat(const hud_snapshot& snap, int32_t* target_index)
{
    int32_t best = -1;
    int32_t best_target = -1;
    uint64_t best_since = 0;
    for (uint32_t k = 0; k < SNAP_SEATS; ++k) {
        const seat_target& s = snap.seats[k];
        if (s.handle == 0) {
            continue;
        }
        int32_t index = -1;
        for (uint32_t t = 0; t < snap.target_count && t < SNAP_TARGETS; ++t) {
            if (snap.targets[t].handle == s.handle && (snap.targets[t].flags & UNIT_PRESENT) != 0) {
                index = static_cast<int32_t>(t);
                break;
            }
        }
        if (index < 0) {
            continue;
        }
        if (best < 0 || s.since_ms > best_since) {
            best = static_cast<int32_t>(k);
            best_target = index;
            best_since = s.since_ms;
        }
    }
    if (target_index != nullptr) {
        *target_index = best_target;
    }
    return best;
}

uint32_t gate(const gate_in& in)
{
    if (in.party != 0) {
        return VIEW_REVEAL;   // a party target always reveals (no badge: it is not Libra's doing)
    }
    if (in.anti_libra != 0 && in.anti_libra_stock != 0) {
        return VIEW_ANTI_LIBRA;   // hidden, whatever Libra says (the native rule)
    }
    if (in.libra_known != 0 && in.libra != 0) {
        return VIEW_REVEAL | VIEW_LIBRA;
    }
    return 0;
}

uint32_t ghost_step(ghost_state* g, uint32_t handle, uint32_t hp, uint64_t now)
{
    if (g->handle != handle) {
        g->handle = handle;
        g->ghost = hp;
        g->from = hp;
        g->last_hp = hp;
        g->hit_ms = 0;
        return hp;
    }
    if (hp < g->last_hp) {
        // Another hit while the ghost still shows keeps the ghost where it is and restarts the hold.
        g->from = g->ghost > g->last_hp ? g->ghost : g->last_hp;
        g->hit_ms = now;
    }
    g->last_hp = hp;
    if (hp >= g->ghost) {
        g->ghost = hp;
        g->from = hp;
        g->hit_ms = 0;
        return hp;
    }
    const uint64_t since = now >= g->hit_ms ? now - g->hit_ms : 0;
    if (since <= GHOST_HOLD_MS) {
        g->ghost = g->from;
    } else if (since >= GHOST_HOLD_MS + GHOST_EASE_MS) {
        g->ghost = hp;
    } else {
        const uint64_t t = since - GHOST_HOLD_MS;
        const uint64_t span = g->from - hp;
        g->ghost = hp + static_cast<uint32_t>(span - (span * t) / GHOST_EASE_MS);
    }
    return g->ghost;
}

uint32_t decode_text(const uint8_t* text, uint32_t len, char* out, uint32_t cap)
{
    if (cap == 0) {
        return 0;
    }
    uint32_t n = 0;
    for (uint32_t i = 0; i < len && text[i] != 0 && n + 1 < cap;) {
        const uint8_t b = text[i];
        if (b == 0x0F) {
            // A control token: 0F 29 head + channel bytes (head & 7 of them; 4 bytes when 0), else 4 bytes.
            uint32_t skip = 4;
            if (i + 2 < len && text[i + 1] == 0x29) {
                const uint32_t count = text[i + 2] & 7u;
                skip = count == 0 ? 4 : 3 + count;
            }
            i += skip;
            continue;
        }
        char c = 0;
        if (b >= 32 && b < 32 + 26) {
            c = static_cast<char>('A' + (b - 32));
        } else if (b >= 58 && b < 58 + 26) {
            c = static_cast<char>('a' + (b - 58));
        } else if (b >= 0x85 && b < 0x85 + 10) {
            c = static_cast<char>('0' + (b - 0x85));
        } else {
            switch (b) {
            case 2: c = ' '; break;     // newline
            case 3: c = 0; break;       // wait token
            case 4: c = ' '; break;
            case 115: c = 'e'; break;   // e acute
            case 129: c = 'u'; break;   // u acute
            case 154: c = '?'; break;
            case 158: c = '%'; break;
            case 160: c = '&'; break;
            case 162: c = '/'; break;
            case 165: c = '-'; break;
            case 166: c = '+'; break;
            case 167: c = ','; break;
            case 168: c = '.'; break;
            case 169: c = ':'; break;
            case 170: c = ';'; break;
            case 171: c = '!'; break;
            case 172: c = '\''; break;
            case 173: c = '"'; break;
            case 174: c = '('; break;
            case 175: c = ')'; break;
            default: c = '?'; break;
            }
        }
        ++i;
        if (c == 0 || (c == ' ' && (n == 0 || out[n - 1] == ' '))) {
            continue;   // leading and doubled spaces dropped
        }
        out[n++] = c;
    }
    while (n > 0 && out[n - 1] == ' ') {
        --n;
    }
    out[n] = '\0';
    return n;
}

uint32_t build_drops(const uint8_t* loot, const uint8_t* known, uint16_t steal_left, name_fn names, void* ctx,
                     drop_line* out, uint32_t cap)
{
    struct page {
        uint8_t kind;
        uint8_t hi_page;
        uint8_t slots;
        uint8_t offsets[5];
    };
    static constexpr page PAGES[] = {
        {DROP, tf::HI_PAGE_DROPS, 5, {tf::LOOT_DROPS, tf::LOOT_DROPS + 2, tf::LOOT_DROPS + 4, tf::LOOT_DROPS + 6,
                                      tf::LOOT_DROPS + 8}},
        {STEAL, tf::HI_PAGE_STEALS, 3, {tf::LOOT_STEALS, tf::LOOT_STEALS + 2, tf::LOOT_STEALS + 4, 0, 0}},
        {SPECIAL, tf::HI_PAGE_SPECIAL, 2, {tf::LOOT_SPECIAL_0, tf::LOOT_SPECIAL_1, 0, 0, 0}},
        {POACH, tf::HI_PAGE_POACHES, 2, {tf::LOOT_POACHES, tf::LOOT_POACHES + 2, 0, 0, 0}},
    };
    if (loot == nullptr || known == nullptr || out == nullptr) {
        return 0;
    }
    uint32_t n = 0;
    for (const page& p : PAGES) {
        for (uint32_t slot = 0; slot < p.slots && n < cap; ++slot) {
            const uint32_t off = p.offsets[slot];
            const uint16_t item = static_cast<uint16_t>(loot[off] | loot[off + 1] << 8);
            if (item == tf::NO_ITEM) {
                continue;
            }
            drop_line& d = out[n++];
            d.kind = p.kind;
            d.slot = static_cast<uint8_t>(slot);
            d.flags = 0;
            if (p.kind == STEAL && (steal_left >> slot & 1u) == 0) {
                d.flags |= DROP_TAKEN;
            }
            if (p.kind == DROP && slot >= 3) {
                d.flags |= DROP_RARE;   // the later drop slots are the rarer ones [U: slot order = chance order]
            }
            if (item >= tf::GIL_FIRST && item <= tf::GIL_LAST) {
                d.flags |= DROP_GIL;
            }
            const bool obtained = (known[p.hi_page] >> slot & 1u) != 0;
            d.text[0] = '\0';
            if (!obtained) {
                d.state = ITEM_UNKNOWN;
                put_text(d.text, 0, ITEM_TEXT, "???");
                continue;
            }
            d.state = ITEM_KNOWN;
            if ((d.flags & DROP_GIL) != 0) {
                put_text(d.text, put_u32(d.text, 0, ITEM_TEXT, static_cast<uint32_t>(item - tf::GIL_FIRST)), ITEM_TEXT,
                         " Gil");
            } else if (names == nullptr || !names(ctx, item, d.text, ITEM_TEXT) || d.text[0] == '\0') {
                d.state = ITEM_UNKNOWN;   // known, but its name could not be read: shown as unknown, never invented
                put_text(d.text, 0, ITEM_TEXT, "???");
            }
        }
    }
    return n;
}

uint32_t build_statuses(const hud_unit& u, uint32_t block, bool show_immune, uint32_t tick_scale, uint8_t* ids,
                        uint8_t* flags, uint16_t* secs)
{
    constexpr uint32_t HIDDEN = (1u << 0) | (1u << 29) | (1u << 30);   // KO, HP Critical (blank cell), Libra
    const uint32_t active = (u.status_timed | u.status_lasting) & ~HIDDEN;
    uint32_t n = 0;
    for (uint32_t id = 0; id < SNAP_STATUS_IDS && n < STATUS_SLOTS; ++id) {
        if ((active >> id & 1u) == 0) {
            continue;
        }
        ids[n] = static_cast<uint8_t>(id);
        flags[n] = STATUS_ACTIVE;
        secs[n] = 0;
        const bool counting = ((u.status_timed & ~u.status_lasting) >> id & 1u) != 0;
        if (counting && tick_scale != 0 && u.duration[id] > 0) {
            const uint64_t s = (static_cast<uint64_t>(u.duration[id]) + 300u * tick_scale - 1) /
                               (300u * static_cast<uint64_t>(tick_scale));
            secs[n] = static_cast<uint16_t>(s > 9999 ? 9999 : s);
            flags[n] |= STATUS_TIMED;
        }
        ++n;
    }
    if (show_immune) {
        const uint32_t immune = block & ~active & ~HIDDEN;
        for (uint32_t id = 0; id < SNAP_STATUS_IDS && n < STATUS_SLOTS; ++id) {
            if ((immune >> id & 1u) != 0) {
                ids[n] = static_cast<uint8_t>(id);
                flags[n] = STATUS_IMMUNE;
                secs[n] = 0;
                ++n;
            }
        }
    }
    return n;
}

uint8_t fade_alpha(const view& v, uint64_t now)
{
    if ((v.head.flags & VIEW_SHOWN) == 0) {
        return 0;
    }
    if ((v.head.flags & (VIEW_KO | VIEW_GONE)) == 0 || v.fade_from_ms == 0) {
        return 0x80;
    }
    uint64_t t = now > v.fade_from_ms ? now - v.fade_from_ms : 0;
    if ((v.head.flags & VIEW_GONE) != 0) {
        if (t <= GONE_HOLD_MS) {
            return 0x80;
        }
        t -= GONE_HOLD_MS;
    }
    if (t >= KO_FADE_MS) {
        return 0;
    }
    return static_cast<uint8_t>(0x80 - (0x80 * t) / KO_FADE_MS);
}

uint32_t compose(canvas& cv, const view& v, uint64_t now, int16_t dy)
{
    const uint8_t alpha = fade_alpha(v, now);
    if (alpha == 0) {
        return 0;
    }
    pen p(cv, alpha);
    const uint32_t flags = v.head.flags;
    const bool reveal = (flags & VIEW_REVEAL) != 0;
    // Short of room (the frame keeps the menus' and the log's floors back), the block ends after the last section that
    // fits whole: a row is never cut in the middle. Every section before the seat edge leaves room for it.
    const uint32_t edge = v.seat != 0 ? 1u : 0u;
    bool open = true;
    const auto section = [&p, &open, edge](uint32_t cost) {
        open = open && p.fits(cost + edge);
        return open;
    };
    int16_t y = static_cast<int16_t>(corner::HEADER_Y + dy);
    if (!section(2)) {
        return 0;
    }
    corner::header(cv, corner::LEFT, y, "TARGET INFO", alpha);
    const int16_t badge_x = static_cast<int16_t>(X0 + corner::RULE_W - BADGE_W);
    const bool badge = (flags & (VIEW_ANTI_LIBRA | VIEW_LIBRA)) != 0;
    if (badge && section(1)) {
        if ((flags & VIEW_ANTI_LIBRA) != 0) {
            p.sprite(ATLAS_BATTLE_4_C, PAGE_RED_X, CELL_RED_X, badge_x, static_cast<int16_t>(y + BADGE_DY), WHITE,
                     BADGE_W, BADGE_W);
        } else {
            p.sprite(ATLAS_PARTYTOP_4_C, PAGE_STATUS, CELL_LIBRA, badge_x, static_cast<int16_t>(y + BADGE_DY), WHITE,
                     BADGE_W, BADGE_H);
        }
    }

    // Level and name.
    y = static_cast<int16_t>(y + NAME_DY);
    char buf[64];
    const bool named = (flags & VIEW_NAME) != 0;
    section((v.level != 0 ? 1u : 0u) + (named ? 1u : 0u) + (named && (flags & VIEW_BOSS) != 0 ? 1u : 0u));
    if (open && v.level != 0) {
        put_u32(buf, put_text(buf, 0, sizeof(buf), "Lv"), sizeof(buf), v.level);
        p.text(X0, static_cast<int16_t>(y + 6), buf, C_LABEL, LEVEL_SCALE);
    }
    if (open && named) {
        p.text(static_cast<int16_t>(X0 + NAME_DX), y, v.name, (flags & VIEW_DANGER) != 0 ? C_DANGER : C_NAME,
               NAME_SCALE);
        if ((flags & VIEW_BOSS) != 0) {
            const int32_t w = corner::text_width(length_of(v.name, NAME_MAX), NAME_SCALE);
            p.text(static_cast<int16_t>(X0 + NAME_DX + w + 12), static_cast<int16_t>(y + 6), "BOSS", C_DANGER,
                   LEVEL_SCALE);
        }
    }
    if ((flags & VIEW_FAMILY) != 0) {
        y = static_cast<int16_t>(y + 26);   // seam: nothing sets VIEW_FAMILY yet
    }

    // HP: numbers and percentage (Libra), the bar and its ghost (always).
    int16_t edge_bottom = static_cast<int16_t>(y + 30);
    const uint16_t ghost_w = bar_width(v.ghost > v.hp ? v.ghost : v.hp, v.hp_max);
    const uint16_t hp_w = bar_width(v.hp, v.hp_max);
    if ((flags & VIEW_HP) != 0 && v.hp_max != 0 &&
        section(2u + (reveal ? 2u : 0u) + (ghost_w != 0 ? 1u : 0u) + (hp_w != 0 ? 1u : 0u))) {
        y = static_cast<int16_t>(y + LINE_HP);
        p.text(X0, static_cast<int16_t>(y + 4), "HP", C_LABEL, LABEL_SCALE);
        if (reveal) {
            uint32_t n = put_u32(buf, 0, sizeof(buf), v.hp);
            n = put_text(buf, n, sizeof(buf), " / ");
            n = put_u32(buf, n, sizeof(buf), v.hp_max);
            p.text(static_cast<int16_t>(X0 + 40), y, buf, C_NUMBER, NUMBER_SCALE);
            const int32_t w = corner::text_width(n, NUMBER_SCALE);
            const uint32_t pct = static_cast<uint32_t>((static_cast<uint64_t>(v.hp) * 100 + v.hp_max - 1) / v.hp_max);
            put_text(buf, put_u32(buf, 0, sizeof(buf), pct > 100 ? 100 : pct), sizeof(buf), "%");
            p.text(static_cast<int16_t>(X0 + 40 + w + 16), static_cast<int16_t>(y + 4), buf, C_DIM, PERCENT_SCALE);
        }
        y = static_cast<int16_t>(y + BAR_DY);
        p.bar(X0, y, BAR_W, BAR_H, C_BAR_BACK);
        p.bar(X0, y, ghost_w, BAR_H, C_BAR_GHOST);
        p.bar(X0, y, hp_w, BAR_H, C_BAR_HP);
        edge_bottom = static_cast<int16_t>(y + BAR_H);
        y = static_cast<int16_t>(y + BAR_H + LINE_GAP - LINE_ROW + 8);
    }

    // Weak / Half / Null (Libra).
    struct group {
        const char* label;
        uint8_t mask;
        bool greyed;
    };
    const group groups[3] = {{"Weak", v.weakness, false},
                             {"Half", (flags & VIEW_ELEMENTS_EXT) != 0 ? v.half : uint8_t{0}, false},
                             {"Null", (flags & VIEW_ELEMENTS_EXT) != 0 ? v.null_ : uint8_t{0}, true}};
    uint32_t element_cost = 0;
    for (const group& g : groups) {
        element_cost += g.mask != 0 ? 1u + bits_of(g.mask) : 0u;
    }
    if (reveal && element_cost != 0 && section(element_cost)) {
        y = static_cast<int16_t>(y + LINE_ROW);
        int16_t x = X0;
        for (const group& g : groups) {
            if (g.mask == 0) {
                continue;
            }
            p.text(x, static_cast<int16_t>(y + 4), g.label, x == X0 ? C_LABEL : C_DIM, LABEL_SCALE);
            x = static_cast<int16_t>(x + 52);
            for (uint32_t e = 0; e < 8; ++e) {
                if ((g.mask >> e & 1u) != 0) {
                    p.sprite(ATLAS_BATTLE_4_C, PAGE_ELEMENTS, static_cast<uint16_t>(e), x, y,
                             g.greyed ? C_IMMUNE : WHITE, ELEMENT_SIZE, ELEMENT_SIZE);
                    x = static_cast<int16_t>(x + ELEMENT_STEP);
                }
            }
            x = static_cast<int16_t>(x + 12);
        }
    }

    // Statuses on the target (always), immune ones greyed (Libra: they are only in the view when revealed).
    uint32_t status_cost = v.status_count != 0 ? 1u : 0u;
    for (uint32_t k = 0; k < v.status_count && k < STATUS_SLOTS; ++k) {
        status_cost += (v.status_flags[k] & STATUS_TIMED) != 0 && (flags & VIEW_TIMERS) != 0 ? 2u : 1u;
    }
    if (v.status_count != 0 && section(status_cost)) {
        y = static_cast<int16_t>(y + LINE_ROW);
        p.text(X0, static_cast<int16_t>(y + 6), "Status", C_LABEL, LABEL_SCALE);
        int16_t x = static_cast<int16_t>(X0 + ICONS_DX);
        for (uint32_t k = 0; k < v.status_count && k < STATUS_SLOTS; ++k) {
            const bool immune = (v.status_flags[k] & STATUS_IMMUNE) != 0;
            p.sprite(ATLAS_PARTYTOP_4_C, PAGE_STATUS, v.status_id[k], x, y, immune ? C_IMMUNE : WHITE, STATUS_W,
                     STATUS_H);
            if ((v.status_flags[k] & STATUS_TIMED) != 0 && (flags & VIEW_TIMERS) != 0) {
                put_u32(buf, 0, sizeof(buf), v.status_secs[k]);
                p.text(static_cast<int16_t>(x + STATUS_W + 1), static_cast<int16_t>(y + 8), buf, C_NUMBER,
                       TIMER_SCALE);
                x = static_cast<int16_t>(x + STATUS_STEP_TIMED);
            } else {
                x = static_cast<int16_t>(x + STATUS_STEP);
            }
        }
    }

    // Seat edge: the selecting seat's colour, from the header to the HP bar (every section above left room for it).
    if (v.seat != 0) {
        const int16_t top = static_cast<int16_t>(corner::HEADER_Y + dy);
        p.bar(static_cast<int16_t>(X0 + EDGE_DX), top, EDGE_W, static_cast<uint16_t>(edge_bottom - top),
              unpack(v.seat_colour));
    }

    // Drops (Hunter's Insight knowledge; not Libra-gated): the title and as many whole lines as fit, the first ones.
    uint32_t drops = 0;
    uint32_t drop_cost = 2;
    if (open && (flags & VIEW_DROPS) != 0) {
        for (uint32_t k = 0; k < v.drop_count && k < DROP_LINES; ++k) {
            const drop_line& d = v.drops[k];
            const uint32_t line = 1u + (d.text[0] != '\0' ? 1u : 0u) + (tag_of(d)[0] != '\0' ? 1u : 0u);
            if (!p.fits(drop_cost + line)) {
                break;
            }
            drop_cost += line;
            ++drops;
        }
    }
    if (drops != 0) {
        y = static_cast<int16_t>(y + LINE_ROW + DROPS_GAP);
        p.text(X0, y, "DROPS", C_HEADER, LABEL_SCALE);
        p.bar(X0, static_cast<int16_t>(y + 18), corner::RULE_W, corner::RULE_H, C_RULE);
        y = static_cast<int16_t>(y + 24);
        for (uint32_t k = 0; k < drops; ++k) {
            const drop_line& d = v.drops[k];
            rgba c = colour_of(d);
            if ((d.flags & DROP_TAKEN) != 0) {
                c.a = static_cast<uint8_t>(c.a / 2);
            }
            p.bar(X0, static_cast<int16_t>(y + 7), CHIP, CHIP, d.state == ITEM_UNKNOWN ? C_UNKNOWN : C_CHIP);
            p.text(static_cast<int16_t>(X0 + 14), y, d.text, c, DROP_SCALE);
            p.text(static_cast<int16_t>(X0 + DROP_TAG_DX), static_cast<int16_t>(y + 4), tag_of(d), c, TAG_SCALE);
            y = static_cast<int16_t>(y + DROP_STEP);
        }
    }
    return p.added();
}

bool hide_native(const native_in& in)
{
    const auto fresh = [&in](uint64_t t) {
        return t != 0 && t <= in.now + LIVE_MS && in.now <= t + LIVE_MS;
    };
    return in.enabled != 0 && in.coop_on != 0 && in.attached != 0 && in.p1_owned != 0 && fresh(in.last_drawn) &&
           fresh(in.last_stamp);
}

// ---- the pass and TI-01 (game thread) -----------------------------------------------------------------------------
bool latest(view& out)
{
    const uint32_t first = __atomic_load_n(&g_view.seq, __ATOMIC_ACQUIRE);
    if ((first & 1u) != 0) {
        return false;
    }
    const uint32_t ok = g_view.valid;
    copy_bytes(&out, const_cast<const view*>(&g_view.value), sizeof(view));
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
    return __atomic_load_n(&g_view.seq, __ATOMIC_RELAXED) == first && ok == 1;
}

void draw(canvas& cv, const driver::pass_input& in)
{
    if (__atomic_load_n(&g_enabled, __ATOMIC_ACQUIRE) == 0 || in.snap == nullptr) {
        return;
    }
    view v;
    if (!latest(v) && !latest(v)) {
        bump(g_counters.skipped_epoch);
        return;
    }
    if (v.head.epoch == 0 || v.head.epoch != in.snap->head.epoch) {
        bump(g_counters.skipped_epoch);
        return;
    }
    __atomic_store_n(&g_last_drawn, in.now_ms != 0 ? in.now_ms : uint64_t{1}, __ATOMIC_RELEASE);
    if ((v.head.flags & VIEW_SHOWN) == 0) {
        return;
    }
    if (compose(cv, v, in.now_ms, layout::header_dy(in.layout, layout::boss_gauge(*in.snap))) != 0) {
        bump(g_counters.drawn);
    }
}

bool native_hidden_now(uint64_t now)
{
    const uint64_t test_stamp = __atomic_load_n(&g_test_stamp, __ATOMIC_ACQUIRE);
    native_in in = {};
    in.enabled = __atomic_load_n(&g_enabled, __ATOMIC_ACQUIRE) != 0 ? 1 : 0;
    in.coop_on = control::current_state() == control::STATE_ON ? 1 : 0;
    in.attached = __atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) != 0 ? 1 : 0;
    const int32_t owned = __atomic_load_n(&g_test_owned, __ATOMIC_ACQUIRE);
    in.p1_owned = (owned >= 0 ? owned != 0 : firewall::menus_own_p1()) ? 1 : 0;
    in.now = now;
    in.last_drawn = __atomic_load_n(&g_last_drawn, __ATOMIC_ACQUIRE);
    in.last_stamp = test_stamp != 0 ? test_stamp : driver::stats()->last_stamp_ms;
    return hide_native(in);
}

int32_t ti01(patch::ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (!native_hidden_now(patch::now_ms())) {
        bump(g_counters.native_stock);
        patch::note_declined(static_cast<uint32_t>(ROW_TI));
        return patch::RUN_ORIGINAL;
    }
    // The open request becomes the set routine's own close request: no window is created (an open one closes),
    // and the resets an open would run still run.
    c->r8 = tf::CLOSE_ID;
    c->r9 = tf::CLOSE_HANDLE;
    bump(g_counters.native_closed);
    return patch::RUN_ORIGINAL;
}

bool attach(uintptr_t image_base)
{
    sample_attach(image_base);
    giveback::attach(image_base);
    const uint32_t row = static_cast<uint32_t>(ROW_TI);
    const bool ok = patch::set_handler(row, ti01) && patch::set_site_armed(row, true);
    if (!ok) {
        patch::set_site_armed(row, false);
    }
    __atomic_store_n(&g_attached, ok ? 1u : 0u, __ATOMIC_RELEASE);
    return ok;
}

void set_enabled(bool on)
{
    __atomic_store_n(&g_enabled, on ? 1u : 0u, __ATOMIC_RELEASE);
}

bool enabled()
{
    return __atomic_load_n(&g_enabled, __ATOMIC_ACQUIRE) != 0;
}

const counters* stats()
{
    return &g_counters;
}

void test_set_attached(bool on)
{
    __atomic_store_n(&g_attached, on ? 1u : 0u, __ATOMIC_RELEASE);
}

void test_publish(const view& v)
{
    publish(v);
}

void test_set_p1_owned(int32_t owned)
{
    __atomic_store_n(&g_test_owned, owned, __ATOMIC_RELEASE);
}

void test_set_last(uint64_t drawn_ms, uint64_t stamp_ms)
{
    __atomic_store_n(&g_last_drawn, drawn_ms, __ATOMIC_RELEASE);
    __atomic_store_n(&g_test_stamp, stamp_ms, __ATOMIC_RELEASE);
}

}  // namespace ilc::hud::target_info
