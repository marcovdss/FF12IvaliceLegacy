// Ivalice Legacy co-op module: seat marks in the world (see world_marks.h).
#include "hud/world_marks.h"

#include "control/control.h"
#include "hud/hud_internal.h"
#include "hud/party_rows.h"
#include "hud/snapshot.h"
#include "hud/snapshot_facts.h"
#include "patch/site_table.h"
#include "runtime/probe.h"
#include "session/reader.h"
#include "session/session.h"

namespace ilc::hud::world_marks {
namespace {

namespace sf = snapfacts;
using detail::zero_bytes;

constexpr uint32_t ACTOR_STANCE = 0x02;     // u8; bit 7 = fighting stance (weapon drawn)
constexpr uint8_t STANCE_FIGHTING = 0x80;
constexpr uint32_t REGULARS = 6;

// What WM-01 relies on, checked once at attach (pinned build); any other bytes -> WM-01 stays stock.
struct prologue {
    uint64_t va;
    uint8_t n;
    uint8_t bytes[36];
};
constexpr prologue PROLOGUES[] = {
    // the pass: rcx = manager, rdx = list A, r8 = list B, r9 = pool (mov r14, r9; mov r15, r8; mov r12, rdx ...)
    {FN_OVERHEAD_PASS, 36, {0x48, 0x89, 0x5C, 0x24, 0x08, 0x48, 0x89, 0x6C, 0x24, 0x10, 0x48, 0x89, 0x74, 0x24, 0x18, 0x57,
                            0x41, 0x54, 0x41, 0x55, 0x41, 0x56, 0x41, 0x57, 0x48, 0x83, 0xEC, 0x40, 0x4D, 0x8B, 0xF1, 0x4D,
                            0x8B, 0xF8, 0x4C, 0x8B}},
    // its arguments at the call: lea rax,[rbp-0x10]; lea r9,[rsp+0x30] (pool); [rsp+0x20] = list D; lea r8,[rbp-0x30]
    // (list B); lea rdx,[rbp-0x50] (list A)
    {SITE_OVERHEAD_CALL - 22, 22, {0x48, 0x8D, 0x45, 0xF0, 0x4C, 0x8D, 0x4C, 0x24, 0x30, 0x48, 0x89, 0x44, 0x24, 0x20, 0x4C,
                                   0x8D, 0x45, 0xD0, 0x48, 0x8D, 0x55, 0xB0}},
    // the pool allocator: {base, capacity, count}, null when count >= capacity
    {FN_OBJECT_ALLOC, 16, {0x48, 0x83, 0xEC, 0x38, 0x48, 0x63, 0x41, 0x0C, 0x3B, 0x41, 0x08, 0x7C, 0x07, 0x33, 0xC0, 0x48}},
    {FN_SPRITE_FRAME, 16, {0x40, 0x53, 0x57, 0x48, 0x83, 0xEC, 0x48, 0x48, 0x8B, 0x05, 0x42, 0x35, 0xB5, 0x01, 0x48, 0x33}},
    // the list append from its second store: obj +0xd8 prev = 0, then an empty list (head +0x00) takes obj as head and
    // tail (+0x10), ret (the first store, obj +0xd0 next = 0, sits before it)
    {FN_LIST_APPEND + 9, 20, {0x48, 0x89, 0x82, 0xD8, 0x00, 0x00, 0x00, 0x48, 0x39, 0x01, 0x75, 0x08, 0x48, 0x89, 0x11, 0x48,
                              0x89, 0x51, 0x10, 0xC3}},
};

counters g_counters;
volatile uint32_t g_attached = 0;          // bit per row (status_view)
volatile uint32_t g_lines_on = 1;
volatile uint32_t g_diamonds_on = 1;
volatile uint32_t g_identify = 0;          // bit per seat, taken by the next beat
volatile uintptr_t g_base = PINNED_BASE;
const game_fns* volatile g_test_fns = nullptr;

patch::record_slot<lines_record> g_lines = {};
patch::record_slot<diamonds_record> g_diamonds = {};

// Co-op thread.
uint32_t g_published = 0;
uint64_t g_generation = 0;
trigger_state g_triggers = {};
uint8_t g_colour_mode = ILC_COLOUR_MODE_CHARACTER;
uint8_t g_seat_colours[ILC_SEATS] = {ILC_COLOUR_GOLD, ILC_COLOUR_SAPPHIRE, ILC_COLOUR_EMERALD, ILC_COLOUR_AMETHYST};
uint8_t g_character_colours[REGULARS] = {ILC_COLOUR_SAPPHIRE, ILC_COLOUR_PINK, ILC_COLOUR_AMETHYST,
                                         ILC_COLOUR_BRONZE,   ILC_COLOUR_EMERALD, ILC_COLOUR_GOLD};

// Game thread (WM-01): what the pass was handed, kept from the call to its after-handler.
struct pending_pass {
    uint64_t manager;
    uint64_t list_b;
    uint64_t pool;
    uint32_t armed;
    uint32_t reserved;
    diamonds_record rec;
};
pending_pass g_pending = {};

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

// ---- rows ---------------------------------------------------------------------------------------------------------------
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

constexpr int32_t ROW_LINE = find_row("TG-06");
constexpr int32_t ROW_ARROW = find_row("TG-05");
constexpr int32_t ROW_OVERHEAD = find_row("WM-01");
constexpr bool targets_row(int32_t row, uint32_t kind, uint64_t va)
{
    return row >= 0 && patch::SITES[row].policy == ILC_SITE_POLICY_PATCH && patch::SITES[row].kind == kind &&
           patch::SITES[row].subsystem == ILC_SUBSYS_TARGETS_CARDS && patch::SITES[row].rva == va - PINNED_BASE;
}
static_assert(targets_row(ROW_LINE, ILC_SITE_KIND_DETOUR, SITE_LINE_CLUT) && patch::SITES[ROW_LINE].hook_len == 6,
              "TG-06 is the TARGETS_CARDS detour over `mov r14d, eax; xor r8d, r8d` (its pins include `mov r9d, eax`)");
static_assert(targets_row(ROW_ARROW, ILC_SITE_KIND_DETOUR, SITE_ARROW_CLUT) && patch::SITES[ROW_ARROW].hook_len == 8,
              "TG-05 is the TARGETS_CARDS detour over `mov edx, edi; mov ecx, ebx; lea r9d, [rax + r9*2]`");
static_assert(targets_row(ROW_OVERHEAD, ILC_SITE_KIND_CALL, SITE_OVERHEAD_CALL) &&
                  (patch::SITES[ROW_OVERHEAD].flags & ILC_SITE_FLAG_OPTIONAL) != 0,
              "WM-01 is an optional TARGETS_CARDS call row on the overhead pass call");

template <typename T>
bool load(uint64_t address, T* out)
{
    return load_game(out, reinterpret_cast<const void*>(address), sizeof(T));
}

bool store(uint64_t address, const void* bytes, uint32_t n)
{
    return store_game(reinterpret_cast<void*>(address), bytes, n);
}

bool targets_live()
{
    return patch::subsystem_live(ILC_SUBSYS_TARGETS_CARDS);
}

const line_entry* entry_for(const lines_record& r, uint32_t source)
{
    if (source == 0) {
        return nullptr;
    }
    const uint32_t n = r.count < LINE_ENTRIES ? r.count : LINE_ENTRIES;
    for (uint32_t k = 0; k < n; ++k) {
        if (r.entries[k].handle == source && r.entries[k].kind != MARK_NONE && r.entries[k].clut < CLUT_COUNT) {
            return &r.entries[k];
        }
    }
    return nullptr;
}

// ---- TG-06 / TG-05 bodies (mid-function: registers and the record copy only; reads through the probe) --------------------
int32_t line_body(patch::ctx* c, const lines_record& r)
{
    if (r.enabled == 0 || __atomic_load_n(&g_lines_on, __ATOMIC_ACQUIRE) == 0) {
        patch::note_declined(static_cast<uint32_t>(ROW_LINE));
        return patch::RUN_ORIGINAL;
    }
    // One probe read for both fields (+0x810 source .. +0x841 type): one region check per line.
    uint8_t span[LINE_TYPE + 2 - LINE_SOURCE];
    if (!read_game(span, reinterpret_cast<const void*>(c->r15 + LINE_SOURCE), sizeof(span))) {
        bump(g_counters.lines_unread);
        return patch::RUN_ORIGINAL;
    }
    const uint32_t source = static_cast<uint32_t>(span[0]) | (static_cast<uint32_t>(span[1]) << 8) |
                            (static_cast<uint32_t>(span[2]) << 16) | (static_cast<uint32_t>(span[3]) << 24);
    const uint32_t type = static_cast<uint32_t>(span[LINE_TYPE - LINE_SOURCE]) |
                          (static_cast<uint32_t>(span[LINE_TYPE - LINE_SOURCE + 1]) << 8);
    const uint32_t native = static_cast<uint32_t>(c->rax);
    const uint32_t clut = line_clut(r, source, type, native);
    if (clut != native) {
        c->rax = clut;  // the trampoline replays `mov r14d, eax; xor r8d, r8d`: every later use reads r14d
        bump(g_counters.lines_recoloured);
    }
    return patch::RUN_ORIGINAL;
}

int32_t arrow_body(patch::ctx* c, const lines_record& r)
{
    if (r.enabled == 0 || __atomic_load_n(&g_lines_on, __ATOMIC_ACQUIRE) == 0) {
        patch::note_declined(static_cast<uint32_t>(ROW_ARROW));
        return patch::RUN_ORIGINAL;
    }
    uint32_t source = 0;
    if (!load(c->rsi + ARROW_SOURCE, &source)) {
        bump(g_counters.lines_unread);
        return patch::RUN_ORIGINAL;
    }
    const uint32_t native = static_cast<uint32_t>(c->rax) + 2u * static_cast<uint32_t>(c->r9);
    const uint32_t clut = arrow_clut(r, source, native);
    if (clut != native) {
        c->rax = clut;  // replayed: `lea r9d, [rax + r9*2]` = clut
        c->r9 = 0;
        bump(g_counters.arrows_recoloured);
    }
    return patch::RUN_ORIGINAL;
}

int32_t overhead_body(patch::ctx* c, const diamonds_record& r);

// ---- game functions -----------------------------------------------------------------------------------------------------
game_fns live_fns()
{
    const game_fns* test = g_test_fns;
    if (test != nullptr) {
        return *test;
    }
    const uintptr_t base = g_base;
    game_fns f;
    f.alloc = reinterpret_cast<decltype(f.alloc)>(base + (FN_OBJECT_ALLOC - PINNED_BASE));
    f.frame = reinterpret_cast<decltype(f.frame)>(base + (FN_SPRITE_FRAME - PINNED_BASE));
    f.append = reinterpret_cast<decltype(f.append)>(base + (FN_LIST_APPEND - PINNED_BASE));
    return f;
}

float to_float(int32_t v) { return static_cast<float>(v); }

// One sprite object: anchor, the frame, colour + alpha + a centred quad, the lift, then the list. False = pool full.
bool emit(const game_fns& f, uint64_t pool, uint64_t list, uint64_t texture, const float anchor[4], uint32_t sheet,
          uint32_t cell, uint32_t scale_pct, uint32_t alpha, const uint32_t* tint = nullptr)
{
    const uint64_t obj = f.alloc(pool);
    if (obj == 0) {
        return false;
    }
    float head[4] = {anchor[0], anchor[1], anchor[2], -1.0f};  // w = -1.0, as the leader marker writes
    if (!store(obj + OBJ_ANCHOR, head, sizeof(head))) {
        return true;   // the object stays unlinked: the game never draws it
    }
    const uint64_t frame = f.frame(obj, texture, sheet, cell);
    uint32_t dims = 0;
    if (frame == 0 || !load(frame + FRAME_SIZE, &dims)) {
        return true;
    }
    const int32_t w = static_cast<int32_t>(((dims & 0xFFFu) * scale_pct) / 100u);
    const int32_t h = static_cast<int32_t>((((dims >> 12) & 0xFFFu) * scale_pct) / 100u);
    struct {
        uint32_t r, g, b, a;
        float x0, y0, x1, y1;
    } look = {tint != nullptr ? tint[0] : ALPHA_FULL, tint != nullptr ? tint[1] : ALPHA_FULL,
              tint != nullptr ? tint[2] : ALPHA_FULL, alpha, to_float(-w / 2), to_float(-h / 2), to_float(w - w / 2),
              to_float(h - h / 2)};
    static_assert(sizeof(look) == 0x20, "colour + quad");
    const float lift[2] = {to_float(DIAMOND_LIFT), 1.0f};
    if (!store(obj + OBJ_COLOUR, &look, sizeof(look)) || !store(obj + OBJ_LIFT, lift, sizeof(lift))) {
        return true;
    }
    f.append(list, obj);
    bump(g_counters.objects);
    return true;
}

}  // namespace

// ---- the rules -------------------------------------------------------------------------------------------------------------
namespace {

// targetline_p palettes (HUD-NATIVE-RE 5.1): 0 blue, 1 red, 2 yellow, 3 green, 4 gold, 5 purple, 6 white.
constexpr uint8_t CLUT_BLUE = 0, CLUT_RED = 1, CLUT_YELLOW = 2, CLUT_GREEN = 3, CLUT_GOLD = 4, CLUT_PURPLE = 5;
// Per ilc_colour, the palettes by preference: the exact one, or the nearest by colour (RGB distance to the baked
// palette colours) for pink (245,120,190 -> purple 76, red 83, gold 158, yellow 174) and bronze (205,127,50 -> gold 62,
// red 67, yellow 95). Never 6 (white): the gambit allies' lines.
constexpr uint8_t PREFS = 4;
constexpr uint8_t CLUT_PREFS[ILC_ENUM_COUNT(ILC_COLOURS)][PREFS] = {
    {CLUT_BLUE, CLUT_BLUE, CLUT_BLUE, CLUT_BLUE},          // sapphire
    {CLUT_RED, CLUT_RED, CLUT_RED, CLUT_RED},              // ruby
    {CLUT_PURPLE, CLUT_RED, CLUT_GOLD, CLUT_YELLOW},       // pink
    {CLUT_GREEN, CLUT_GREEN, CLUT_GREEN, CLUT_GREEN},      // emerald
    {CLUT_GOLD, CLUT_GOLD, CLUT_GOLD, CLUT_GOLD},          // gold
    {CLUT_PURPLE, CLUT_PURPLE, CLUT_PURPLE, CLUT_PURPLE},  // amethyst
    {CLUT_GOLD, CLUT_RED, CLUT_YELLOW, CLUT_YELLOW},       // bronze
};
static_assert(ILC_COLOUR_SAPPHIRE == 0 && ILC_COLOUR_RUBY == 1 && ILC_COLOUR_EMERALD == 3 && ILC_COLOUR_GOLD == 4 &&
                  ILC_COLOUR_AMETHYST == 5,
              "the exact colours are their own palette index");

bool exact_palette(uint8_t colour)
{
    return colour < ILC_ENUM_COUNT(ILC_COLOURS) && colour != ILC_COLOUR_PINK && colour != ILC_COLOUR_BRONZE;
}

}  // namespace

uint8_t clut_of_colour(uint8_t colour)
{
    return colour < ILC_ENUM_COUNT(ILC_COLOURS) ? CLUT_PREFS[colour][0] : CLUT_NONE;
}

void line_cluts(const uint8_t colours[SEATS], uint8_t out[SEATS])
{
    uint32_t used = 0;   // bit per palette a seat already holds
    for (uint32_t s = 0; s < SEATS; ++s) {
        out[s] = CLUT_NONE;
        if (exact_palette(colours[s])) {
            out[s] = CLUT_PREFS[colours[s]][0];
            used |= 1u << out[s];
        }
    }
    for (uint32_t s = 0; s < SEATS; ++s) {
        const uint8_t c = colours[s];
        if (c >= ILC_ENUM_COUNT(ILC_COLOURS) || exact_palette(c)) {
            continue;
        }
        uint8_t pick = CLUT_PREFS[c][0];
        for (uint32_t k = 0; k < PREFS; ++k) {
            if ((used & (1u << CLUT_PREFS[c][k])) == 0) {
                pick = CLUT_PREFS[c][k];
                break;
            }
        }
        out[s] = pick;
        used |= 1u << pick;
    }
}

uint8_t gem_cell(uint8_t colour)
{
    switch (colour) {
    case ILC_COLOUR_SAPPHIRE: return 1;
    case ILC_COLOUR_EMERALD: return 2;
    case ILC_COLOUR_AMETHYST: return 3;
    case ILC_COLOUR_RUBY: return 4;
    case ILC_COLOUR_GOLD: return 6;
    case ILC_COLOUR_PINK:     // no pink gem: the grey one, tinted (gem_tint)
    case ILC_COLOUR_BRONZE: return 7;
    default: return GEM_NONE;
    }
}

bool gem_tint(uint8_t colour, uint32_t rgb[3])
{
    rgb[0] = rgb[1] = rgb[2] = ALPHA_FULL;
    if (colour != ILC_COLOUR_PINK && colour != ILC_COLOUR_BRONZE) {
        return false;
    }
    const rgba c = party_rows::colour_rgba(colour);   // 0x80 = 1.0
    const uint32_t top = c.r > c.g ? (c.r > c.b ? c.r : c.b) : (c.g > c.b ? c.g : c.b);
    if (top == 0) {
        return false;
    }
    const uint8_t ch[3] = {c.r, c.g, c.b};
    for (uint32_t k = 0; k < 3; ++k) {
        const uint32_t v = (static_cast<uint32_t>(ch[k]) * 0xFFu + top / 2) / top;
        rgb[k] = v > 0xFF ? 0xFF : v;
    }
    return true;
}

uint32_t party_clut_of_type(uint32_t type)
{
    return type == TYPE_PARTY ? CLUT_PARTY : (type == TYPE_PARTY_SUPPORT ? CLUT_PARTY_SUPPORT : CLUT_NONE);
}

uint32_t line_clut(const lines_record& r, uint32_t source, uint32_t type, uint32_t native)
{
    // Only a party line still in the party palette 0x2713B0 gives its type (3 -> 0 blue, 4 -> 3 green) is recoloured:
    // foe and neutral lines, and any other palette (a special case of another hook, value 4 included), keep theirs.
    if (native != party_clut_of_type(type)) {
        return native;
    }
    const line_entry* e = entry_for(r, source);
    return e != nullptr ? e->clut : native;
}

uint32_t arrow_clut(const lines_record& r, uint32_t source, uint32_t native)
{
    // The arrow's own rule, not the lines' one. The game gives the arrow two palettes (0x3F787C..0x3F78BF): 4 gold
    // (in range) and 1 red (out of range). Red is the game's out-of-range warning and is always kept. Gold, and the
    // party palettes 0 and 3 (another hook's), take the palette of the record's entry for the source: a seat's own
    // colour, so whoever leads is identified by the arrow (the gambit white 6 for a party member no seat holds).
    // Parity with the co-op fork (its arrow cave recolours every state but palette 1) and our own earlier rule. Any
    // other value is another hook's special case: kept.
    if (native != ARROW_ON && native != CLUT_PARTY && native != CLUT_PARTY_SUPPORT) {
        return native;
    }
    const line_entry* e = entry_for(r, source);
    return e != nullptr ? e->clut : native;
}

uint32_t diamond_alpha(uint64_t shown_ms, uint64_t now_ms)
{
    if (shown_ms == 0 || now_ms < shown_ms) {
        return 0;
    }
    const uint64_t t = now_ms - shown_ms;
    if (t < DIAMOND_FADE_IN_MS) {
        return static_cast<uint32_t>((t * ALPHA_FULL) / DIAMOND_FADE_IN_MS);
    }
    const uint64_t out_at = DIAMOND_FADE_IN_MS + DIAMOND_HOLD_MS;
    if (t < out_at) {
        return ALPHA_FULL;
    }
    if (t >= out_at + DIAMOND_FADE_OUT_MS) {
        return 0;
    }
    return static_cast<uint32_t>(((out_at + DIAMOND_FADE_OUT_MS - t) * ALPHA_FULL) / DIAMOND_FADE_OUT_MS);
}

uint32_t trigger_step(trigger_state& st, uint32_t epoch, const seat_input in[SEATS], uint32_t identify_mask,
                      uint64_t now_ms)
{
    bool engaged = false;
    for (uint32_t s = 0; s < SEATS; ++s) {
        engaged = engaged || (in[s].held != 0 && in[s].stance_known != 0 && in[s].stance != 0);
    }
    if (epoch == 0 || epoch != st.epoch) {
        // A new world: this beat is the baseline. Nothing fires and nothing stays shown.
        zero_bytes(&st, sizeof(st));
        st.epoch = epoch;
        st.engaged = engaged ? 1 : 0;
        for (uint32_t s = 0; s < SEATS; ++s) {
            st.seats[s].held = in[s].held != 0 ? 1 : 0;
            st.seats[s].member = in[s].member;
            st.seats[s].handle = in[s].handle;
        }
        return 0;
    }
    // Fight start: the party enters stance after being out of it for ENGAGE_QUIET_MS (or for the first time here).
    const bool fight = engaged && st.engaged == 0 &&
                       (st.quiet_since == 0 || now_ms >= st.quiet_since + ENGAGE_QUIET_MS);
    if (!engaged && st.engaged != 0) {
        st.quiet_since = now_ms;
    }
    st.engaged = engaged ? 1 : 0;
    uint32_t fired = 0;
    for (uint32_t s = 0; s < SEATS; ++s) {
        seat_track& t = st.seats[s];
        const seat_input& i = in[s];
        uint32_t bits = 0;
        if (i.held == 0) {
            t.held = 0;
            t.member = i.member;
            t.handle = 0;
            t.shown_ms = 0;
            continue;
        }
        if (t.held == 0) {
            bits |= TRIG_DROP_IN;
        } else if (t.member != i.member || t.handle != i.handle) {
            bits |= TRIG_SWAP;
        }
        if (fight) {
            bits |= TRIG_FIGHT;
        }
        if ((identify_mask & (1u << s)) != 0) {
            bits |= TRIG_IDENTIFY;
        }
        t.held = 1;
        t.member = i.member;
        t.handle = i.handle;
        if (bits != 0) {
            // Not visible: a fresh fade-in. Still fading in: it carries on. Visible past its fade-in: back to full at
            // once, the hold restarted.
            if (diamond_alpha(t.shown_ms, now_ms) == 0) {
                t.shown_ms = now_ms != 0 ? now_ms : 1;
            } else if (now_ms >= t.shown_ms + DIAMOND_FADE_IN_MS) {
                t.shown_ms = now_ms - DIAMOND_FADE_IN_MS;
            }
            fired |= bits << (4 * s);
        }
    }
    return fired;
}

// 0.1.57 (Marco: identification should stay): a seat that holds a character keeps its diamond. A held seat whose
// diamond is not up fades it in; one past its fade-in is held at full (its hold restarts every beat), so it never fades
// out while the seat plays. The triggers above still fire (and count) as before.
void persist_step(trigger_state& st, const seat_input in[SEATS], uint64_t now_ms)
{
    if (st.epoch == 0) {
        return;
    }
    for (uint32_t s = 0; s < SEATS; ++s) {
        seat_track& t = st.seats[s];
        if (in[s].held == 0 || t.held == 0) {
            continue;
        }
        if (diamond_alpha(t.shown_ms, now_ms) == 0) {
            t.shown_ms = now_ms != 0 ? now_ms : 1;
        } else if (now_ms >= t.shown_ms + DIAMOND_FADE_IN_MS) {
            t.shown_ms = now_ms - DIAMOND_FADE_IN_MS;
        }
    }
}

// ---- handlers ---------------------------------------------------------------------------------------------------------------
int32_t line_call(patch::ctx* c, uint32_t epoch)
{
    return patch::fail_open<lines_record, g_lines, line_body>(c, epoch);
}

int32_t arrow_call(patch::ctx* c, uint32_t epoch)
{
    return patch::fail_open<lines_record, g_lines, arrow_body>(c, epoch);
}

namespace {

int32_t overhead_body(patch::ctx* c, const diamonds_record& r)
{
    g_pending.armed = 0;
    if (r.enabled == 0 || __atomic_load_n(&g_diamonds_on, __ATOMIC_ACQUIRE) == 0 || c->rcx == 0 || c->r8 == 0 ||
        c->r9 == 0) {
        return patch::RUN_ORIGINAL;
    }
    const uint64_t now = patch::now_ms();
    bool any = false;
    for (uint32_t s = 0; s < SEATS; ++s) {
        any = any || (r.seats[s].handle != 0 && r.seats[s].gem != GEM_NONE && diamond_alpha(r.seats[s].shown_ms, now) > 0);
    }
    if (!any) {
        return patch::RUN_ORIGINAL;  // the common case: nothing to show, the pass runs as stock
    }
    g_pending.manager = c->rcx;
    g_pending.list_b = c->r8;
    g_pending.pool = c->r9;
    g_pending.rec = r;
    g_pending.armed = 1;
    return patch::CALL_AROUND;
}

}  // namespace

int32_t overhead_call(patch::ctx* c, uint32_t epoch)
{
    return patch::fail_open<diamonds_record, g_diamonds, overhead_body>(c, epoch);
}

void overhead_after(patch::ctx* c, uint32_t epoch)
{
    (void)c;
    (void)epoch;
    if (g_pending.armed == 0) {
        return;
    }
    g_pending.armed = 0;
    bump(g_counters.passes_wrapped);
    if (!targets_live()) {
        return;  // TARGETS_CARDS went off while the pass ran: add nothing
    }
    const diamonds_record& r = g_pending.rec;
    const uint64_t now = patch::now_ms();
    uint32_t alpha[SEATS];
    float anchor[SEATS][4];
    bool found[SEATS];
    uint32_t wanted = 0;
    for (uint32_t s = 0; s < SEATS; ++s) {
        alpha[s] = r.seats[s].handle != 0 && r.seats[s].gem != GEM_NONE ? diamond_alpha(r.seats[s].shown_ms, now) : 0;
        found[s] = false;
        wanted += alpha[s] > 0 ? 1u : 0u;
    }
    if (wanted == 0) {
        return;
    }
    // The anchors: one walk of the manager's overhead records.
    uint64_t rec = 0;
    if (!load(g_pending.manager + OVERHEAD_RECORDS, &rec)) {
        return;
    }
    for (uint32_t n = 0; rec != 0 && n < OVERHEAD_WALK_MAX; ++n) {
        uint32_t handle = 0;
        if (!load(rec + OVERHEAD_HANDLE, &handle)) {
            break;
        }
        for (uint32_t s = 0; s < SEATS; ++s) {
            if (alpha[s] > 0 && !found[s] && handle == r.seats[s].handle) {
                found[s] = read_game(anchor[s], reinterpret_cast<const void*>(rec + OVERHEAD_ANCHOR),
                                            sizeof(anchor[s]));
            }
        }
        if (!load(rec + OVERHEAD_NEXT, &rec)) {
            break;
        }
    }
    uint64_t atlases = 0;
    uint64_t texture = 0;
    const uintptr_t base = g_base;
    if (!load(base + (UI_ATLASES - PINNED_BASE), &atlases) || atlases == 0 ||
        !load(atlases + ATLAS_PARTYTOP, &texture) || texture == 0) {
        return;  // partytop_4_c not loaded: nothing to draw with
    }
    const game_fns f = live_fns();
    for (uint32_t s = 0; s < SEATS; ++s) {
        if (alpha[s] == 0) {
            continue;
        }
        if (!found[s]) {
            bump(g_counters.no_anchor);
            continue;
        }
        const uint32_t numeral = r.seats[s].seat >= 1 && r.seats[s].seat <= SEATS ? r.seats[s].seat : s + 1;
        // Both objects or neither: a gem without its numeral says nothing.
        int32_t capacity = 0;
        int32_t used = 0;
        if (!load(g_pending.pool + POOL_CAPACITY, &capacity) || !load(g_pending.pool + POOL_COUNT, &used) ||
            used < 0 || capacity - used < static_cast<int32_t>(OBJECTS_PER_SEAT)) {
            bump(g_counters.pool_full);
            return;
        }
        uint32_t tint[3];
        const bool tinted = gem_tint(r.seats[s].colour, tint);
        if (!emit(f, g_pending.pool, g_pending.list_b, texture, anchor[s], GEM_SHEET, r.seats[s].gem, GEM_SCALE_PCT,
                  alpha[s], tinted ? tint : nullptr) ||
            !emit(f, g_pending.pool, g_pending.list_b, texture, anchor[s], DIGIT_SHEET, numeral, DIGIT_SCALE_PCT,
                  alpha[s])) {
            bump(g_counters.pool_full);
            return;
        }
    }
}

// ---- co-op thread -----------------------------------------------------------------------------------------------------------
namespace {

void withdraw_records()
{
    if (g_published != 0) {
        g_lines.withdraw();
        g_diamonds.withdraw();
        g_published = 0;
    }
}

bool held(const session::seat_entry& e)
{
    return (e.flags & session::SEATF_IN_SESSION) != 0 && e.member != sf::MEMBER_NONE && e.handle != 0;
}

}  // namespace

bool attach(uintptr_t image_base)
{
    g_base = image_base;
    uint32_t mask = 0;
    // WM-01 calls game functions: their first bytes (and the pass's own arguments) must be the pinned ones.
    session::game_reader r(image_base);
    bool pins = true;
    for (const prologue& p : PROLOGUES) {
        uint8_t now[sizeof(p.bytes)];
        pins = pins && r.read(r.global(p.va), now, p.n);
        for (uint32_t k = 0; pins && k < p.n; ++k) {
            pins = now[k] == p.bytes[k];
        }
    }
    const int32_t rows[3] = {ROW_LINE, ROW_ARROW, ROW_OVERHEAD};
    const patch::handler_fn fns[3] = {line_call, arrow_call, overhead_call};
    for (uint32_t k = 0; k < 3; ++k) {
        const uint32_t row = static_cast<uint32_t>(rows[k]);
        bool ok = (k != 2 || pins) && patch::set_handler(row, fns[k]);
        if (ok && k == 2) {
            ok = patch::set_after(row, overhead_after);
        }
        ok = ok && patch::set_site_armed(row, true);
        if (!ok) {
            patch::set_site_armed(row, false);   // each row stands alone: one that cannot attach stays stock
        } else {
            mask |= 1u << k;
        }
    }
    __atomic_store_n(&g_attached, mask, __ATOMIC_RELEASE);
    return mask == 7u;
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
    if (__atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) == 0) {
        return;
    }
    session::session_record srec;
    if (!session::read(&srec) || srec.head.epoch == 0 || (srec.head.flags & session::GATE_WORLD) == 0 ||
        control::current_state() != control::STATE_ON || !targets_live()) {
        withdraw_records();
        return;
    }
    static lines_record lines;
    static diamonds_record dia;
    zero_bytes(&lines, sizeof(lines));
    zero_bytes(&dia, sizeof(dia));
    lines.head.epoch = dia.head.epoch = srec.head.epoch;
    lines.head.generation = dia.head.generation = ++g_generation;
    lines.enabled = lines_enabled() ? 1 : 0;
    dia.enabled = diamonds_enabled() ? 1 : 0;
    dia.sampled_ms = now_ms;

    session::game_reader r(g_base);
    seat_input in[SEATS];
    zero_bytes(in, sizeof(in));
    uint8_t colour[SEATS];
    for (uint32_t s = 0; s < SEATS && s < session::SEATS; ++s) {
        const session::seat_entry& e = srec.seat[s];
        colour[s] = party_rows::COLOUR_NONE;
        if (!held(e)) {
            continue;
        }
        in[s].held = 1;
        in[s].member = e.member;
        in[s].handle = e.handle;
        uint8_t stance = 0;
        if ((e.flags & session::SEATF_ACTOR) != 0 && e.actor != 0 && r.u8(e.actor + ACTOR_STANCE, &stance)) {
            in[s].stance_known = 1;
            in[s].stance = (stance & STANCE_FIGHTING) != 0 ? 1 : 0;
        }
        colour[s] = party_rows::row_colour(g_colour_mode, g_seat_colours, g_character_colours,
                                           static_cast<uint8_t>(s + 1), e.member);
    }
    // Every seat its own palette (pink and bronze the nearest one no other seat holds; never the gambit white).
    uint8_t clut[SEATS];
    line_cluts(colour, clut);
    for (uint32_t s = 0; s < SEATS && s < session::SEATS; ++s) {
        if (in[s].held != 0 && clut[s] != CLUT_NONE && lines.count < LINE_ENTRIES) {
            lines.entries[lines.count++] = {in[s].handle, MARK_SEAT, static_cast<uint8_t>(s + 1), clut[s], 0};
        }
    }
    // Gambit allies: the party rows no seat holds.
    hud_snapshot snap;
    if (snapshot::read(snap) && snap.head.epoch == srec.head.epoch && (snap.head.flags & SNAP_PARTY) != 0) {
        for (uint32_t k = 0; k < snap.member_count && k < SNAP_MEMBERS; ++k) {
            const hud_unit& u = snap.members[k];
            if ((u.flags & UNIT_ACTOR) == 0 || u.handle == 0 || lines.count >= LINE_ENTRIES) {
                continue;
            }
            bool seated = false;
            for (uint32_t s = 0; s < SEATS; ++s) {
                seated = seated || (in[s].held != 0 && in[s].handle == u.handle);
            }
            if (!seated) {
                lines.entries[lines.count++] = {u.handle, MARK_GAMBIT, 0, CLUT_GAMBIT, 0};
            }
        }
    }
    const uint32_t identify_mask = __atomic_exchange_n(&g_identify, 0u, __ATOMIC_ACQ_REL);
    const uint32_t fired = trigger_step(g_triggers, srec.head.epoch, in, identify_mask, now_ms);
    persist_step(g_triggers, in, now_ms);
    for (uint32_t s = 0; s < SEATS; ++s) {
        if (((fired >> (4 * s)) & 0xFu) != 0) {
            bump(g_counters.triggers);
        }
        const seat_track& t = g_triggers.seats[s];
        if (in[s].held == 0 || t.shown_ms == 0) {
            continue;
        }
        dia.seats[s] = {in[s].handle, static_cast<uint8_t>(s + 1), gem_cell(colour[s]), colour[s], 0, t.shown_ms};
    }
    g_lines.publish(lines);
    g_diamonds.publish(dia);
    g_published = 1;
    bump(g_counters.samples);
}

void stop()
{
    g_lines.withdraw();
    g_diamonds.withdraw();
    g_published = 0;
}

void set_lines_enabled(bool on) { __atomic_store_n(&g_lines_on, on ? 1u : 0u, __ATOMIC_RELEASE); }
void set_diamonds_enabled(bool on) { __atomic_store_n(&g_diamonds_on, on ? 1u : 0u, __ATOMIC_RELEASE); }
bool lines_enabled() { return __atomic_load_n(&g_lines_on, __ATOMIC_ACQUIRE) != 0; }
bool diamonds_enabled() { return __atomic_load_n(&g_diamonds_on, __ATOMIC_ACQUIRE) != 0; }

void identify(uint32_t seat)
{
    if (seat < SEATS) {
        __atomic_or_fetch(&g_identify, 1u << seat, __ATOMIC_ACQ_REL);
    }
}

const counters* stats() { return &g_counters; }

status_view status()
{
    status_view v;
    zero_bytes(&v, sizeof(v));
    v.attached = static_cast<uint8_t>(g_attached);
    v.lines = lines_enabled() ? 1 : 0;
    v.diamonds = diamonds_enabled() ? 1 : 0;
    return v;
}

// ---- host tests -------------------------------------------------------------------------------------------------------------
void test_reset()
{
    zero_bytes(const_cast<counters*>(&g_counters), sizeof(counters));
    g_lines.withdraw();
    g_diamonds.withdraw();
    g_published = 0;
    g_generation = 0;
    zero_bytes(&g_triggers, sizeof(g_triggers));
    zero_bytes(&g_pending, sizeof(g_pending));
    g_lines_on = 1;
    g_diamonds_on = 1;
    g_identify = 0;
    g_test_fns = nullptr;
    g_colour_mode = ILC_COLOUR_MODE_CHARACTER;
}

void test_publish_lines(const lines_record& r) { g_lines.publish(r); }
void test_publish_diamonds(const diamonds_record& r) { g_diamonds.publish(r); }
bool test_read_lines(lines_record* out) { return g_lines.read(*out); }
bool test_read_diamonds(diamonds_record* out) { return g_diamonds.read(*out); }
void test_set_image_base(uintptr_t base) { g_base = base; }
uint32_t test_identify_pending() { return __atomic_load_n(&g_identify, __ATOMIC_ACQUIRE); }
void test_set_fns(const game_fns* fns) { g_test_fns = fns; }
void test_set_attached(uint32_t mask) { __atomic_store_n(&g_attached, mask, __ATOMIC_RELEASE); }
const trigger_state* test_triggers() { return &g_triggers; }

int32_t test_row(uint32_t which)
{
    return which == 0 ? ROW_LINE : which == 1 ? ROW_ARROW : which == 2 ? ROW_OVERHEAD : -1;
}

}  // namespace ilc::hud::world_marks
