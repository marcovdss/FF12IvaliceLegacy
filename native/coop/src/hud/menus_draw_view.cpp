// Ivalice Legacy co-op module: the co-op battle menus in the native window style, the view half (see menus_draw.h).
//
// Co-op thread only. After the menus ran their beat, each seat's menu state is turned into what the menu pass draws:
// the window kind, a window of rows around the cursor with every name already in ASCII, greys, costs and counts, the
// Gambits state, the help line, the seat colour and when the window opened. Names come from the game's own text,
// read through the fault-safe reader exactly as the game's name lookup walks it (never by calling it):
//   * an action's name: action row +0x34 = a name reference (sub-table << 11 | index) into the name directory;
//   * a character's name: party-member row +0x30, the same way;
//   * a foe's name: the unit's name text pointer (the target picker carries it).
// Entries are the game's single-byte text with a two-byte prefix; the decoder keeps the characters ASCII can show.
#include "hud/menus_draw.h"

#include "control/control.h"
#include "frontend/frontend.h"
#include "frontend/rules.h"
#include "hud/hud_internal.h"
#include "hud/relayout.h"
#include "hud/native.h"
#include "ilc/bridge_defaults.h"
#include "menu/help.h"
#include "menu/menu_facts.h"
#include "menu/menus.h"
#include "patch/patch_engine.h"
#include "session/reader.h"
#include "session/session.h"
#include "session/world.h"

namespace ilc::hud::menus_draw {

void reset_draw_state();   // menus_draw.cpp

namespace {

namespace mf = menu::facts;
using session::game_reader;

// Text the name directory hands out (game facts).
constexpr uint32_t ACTION_NAME_REF = 0x34;   // u16 in an action row
constexpr uint32_t MEMBER_NAME_REF = 0x30;   // u16 in a party-member row
constexpr uint32_t NAME_SUB_SHIFT = 11;
constexpr uint32_t NAME_SUB_MASK = 0x1F;
constexpr uint32_t NAME_INDEX_MASK = 0x7FF;
constexpr uint32_t NAME_READ = 48;           // bytes read per name (the prefix, the text, its NUL)

// Seat colours (ilc_colour order), 0x80 = full: the same RGB choice as the canvas's test draw, halved to the PS2 scale.
struct rgb {
    uint8_t r, g, b;
};
constexpr rgb SEAT_RGB[ILC_ENUM_COUNT(ILC_COLOURS)] = {
    {70, 130, 240},   // sapphire
    {230, 60, 70},    // ruby
    {245, 120, 190},  // pink
    {60, 210, 110},   // emerald
    {240, 190, 50},   // gold
    {170, 100, 235},  // amethyst
    {205, 127, 50},   // bronze
};

struct colours_t {
    uint8_t mode;                          // ilc_colour_mode
    uint8_t seat[ILC_SEATS];
    uint8_t character[ILC_CHARACTERS];
};
constexpr colours_t colours_of(const ilc_settings& s)
{
    colours_t c = {};
    c.mode = s.colour_mode;
    for (uint32_t k = 0; k < ILC_SEATS; ++k) {
        c.seat[k] = s.seats[k].colour;
    }
    for (uint32_t k = 0; k < ILC_CHARACTERS; ++k) {
        c.character[k] = s.character_colour[k];
    }
    return c;
}
colours_t g_colours = colours_of(default_settings());   // until the player's settings arrive

struct seat_track {
    uint8_t kind;
    uint8_t reserved[7];
    uint64_t opened_ms;
};
seat_track g_track[SEATS];
uint32_t g_track_epoch = 0;

uintptr_t g_base = 0;
bool g_started = false;
uint32_t g_published = 0;
uint64_t g_generation = 0;
const menu_view* g_test_view = nullptr;
int32_t g_force_reduced = -1;
menu_view g_next;

// A small name cache: names change only with a load (every epoch) and the directory pointer.
struct cached_name {
    uint32_t key;          // kind << 28 | id, 0 = free
    uint32_t reserved;
    uint64_t pointer;      // foe names: the text pointer
    char text[ROW_TEXT];
};
constexpr uint32_t CACHE = 64;
cached_name g_cache[CACHE];
uint32_t g_cache_next = 0;
uint32_t g_cache_epoch = 0;
uint64_t g_cache_directory = 0;

// Help lines, keyed by the text's address (a few per beat at most: the highlighted row of each seat).
struct cached_help {
    uint64_t at;           // 0 = free
    char text[HELP_TEXT];
};
constexpr uint32_t HELP_CACHE = 8;
cached_help g_help_cache[HELP_CACHE];
uint32_t g_help_next = 0;

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

void copy_text(char* out, uint32_t cap, const char* in)
{
    uint32_t n = 0;
    if (in != nullptr) {
        for (; in[n] != '\0' && n + 1 < cap; ++n) {
            out[n] = in[n];
        }
    }
    out[n] = '\0';
}

void append_text(char* out, uint32_t cap, const char* in)
{
    uint32_t n = 0;
    while (n < cap && out[n] != '\0') {
        ++n;
    }
    if (n < cap) {
        copy_text(out + n, cap - n, in);
    }
}

void put_uint(char* out, uint32_t cap, uint32_t v)
{
    char digits[12];
    uint32_t n = 0;
    do {
        digits[n++] = static_cast<char>('0' + v % 10);
        v /= 10;
    } while (v != 0 && n < sizeof(digits));
    uint32_t k = 0;
    for (; k < n && k + 1 < cap; ++k) {
        out[k] = digits[n - 1 - k];
    }
    out[k] = '\0';
}

uintptr_t image_base() { return g_base != 0 ? g_base : mf::IMAGE_VA; }

// ---- the live name reader --------------------------------------------------------------------------------------------
bool table_row(game_reader& r, uintptr_t global, uint32_t id, uint32_t field, uint16_t* out)
{
    uint64_t base = 0;
    uintptr_t hdr = 0;
    uint32_t count = 0;
    uint16_t stride = 0;
    uint32_t rows = 0;
    if (!r.u64(r.global(mf::RESOURCE_BASE_PTR), &base) || base != 0 || !r.ptr(r.global(global), &hdr) || hdr == 0 ||
        !r.u32(hdr + mf::TABLE_COUNT, &count) || id >= count || !r.u16(hdr + mf::TABLE_STRIDE, &stride) ||
        stride < field + 2 || !r.u32(hdr + mf::TABLE_ROWS, &rows)) {
        return false;
    }
    const uintptr_t row = static_cast<uint32_t>(rows + id * stride);
    return r.u16(row + field, out);
}

bool read_text(game_reader& r, uintptr_t at, char* out, uint32_t cap)
{
    uint8_t raw[NAME_READ];
    uint32_t got = 0;
    // Read what is readable (a name near the end of a region reads shorter).
    for (; got < NAME_READ; ++got) {
        if (!r.u8(at + got, &raw[got])) {
            break;
        }
        if (got >= 2 && raw[got] == 0) {
            break;
        }
    }
    if (got < 3) {
        return false;
    }
    // A directory entry starts with a two-byte prefix (both zero in every entry seen); a bare string does not.
    const uint32_t skip = raw[0] == 0 && raw[1] == 0 ? 2 : 0;
    return decode_game_text(raw + skip, got - skip, out, cap) != 0;
}

bool name_by_ref(game_reader& r, uint32_t ref, char* out, uint32_t cap)
{
    uintptr_t dir = 0;
    uint16_t subs = 0;
    uint32_t sub_at = 0;
    uint16_t entries = 0;
    uint32_t entry_at = 0;
    const uint32_t sub = (ref >> NAME_SUB_SHIFT) & NAME_SUB_MASK;
    const uint32_t index = ref & NAME_INDEX_MASK;
    if (!r.ptr(r.global(mf::NAME_TEXT_PTR), &dir) || dir == 0 || !r.u16(dir, &subs) || sub >= subs ||
        !r.u32(dir + 4 + 4 * sub, &sub_at) || sub_at == 0 || !r.u16(sub_at, &entries) || index >= entries ||
        !r.u32(sub_at + 4 + 4 * index, &entry_at) || entry_at == 0) {
        return false;
    }
    return read_text(r, entry_at, out, cap);
}

enum name_kind : uint32_t { NK_ACTION = 1, NK_MEMBER = 2, NK_FOE = 3, NK_HELP = 4 };

cached_name* cache_find(uint32_t key, uint64_t pointer)
{
    for (cached_name& c : g_cache) {
        if (c.key == key && c.pointer == pointer) {
            return &c;
        }
    }
    return nullptr;
}

using fetch_fn = bool (*)(uint32_t id, uint64_t pointer, char* out, uint32_t cap);

bool cached(uint32_t kind, uint32_t id, uint64_t pointer, char* out, uint32_t cap, fetch_fn fetch)
{
    const uint32_t key = kind << 28 | (id & 0x0FFFFFFFu);   // NOTADDR: kind in the top nibble
    if (const cached_name* c = cache_find(key, pointer)) {
        copy_text(out, cap, c->text);
        return true;
    }
    char text[ROW_TEXT];
    if (!fetch(id, pointer, text, sizeof(text))) {
        bump(const_cast<counters*>(stats())->name_misses);
        copy_text(out, cap, "?");
        return false;
    }
    cached_name& slot = g_cache[g_cache_next];
    g_cache_next = (g_cache_next + 1) % CACHE;
    slot.key = key;
    slot.pointer = pointer;
    copy_text(slot.text, sizeof(slot.text), text);
    copy_text(out, cap, text);
    return true;
}

bool fetch_action(uint32_t id, uint64_t, char* out, uint32_t cap)
{
    game_reader r(image_base());
    uint16_t ref = 0;
    return table_row(r, mf::ACTION_TABLE_PTR, id, ACTION_NAME_REF, &ref) && name_by_ref(r, ref, out, cap);
}

bool fetch_member(uint32_t id, uint64_t, char* out, uint32_t cap)
{
    game_reader r(image_base());
    uint16_t ref = 0;
    return table_row(r, mf::MEMBER_TABLE_PTR, id, MEMBER_NAME_REF, &ref) && name_by_ref(r, ref, out, cap);
}

bool fetch_foe(uint32_t, uint64_t pointer, char* out, uint32_t cap)
{
    game_reader r(image_base());
    return pointer != 0 && read_text(r, static_cast<uintptr_t>(pointer), out, cap);
}

// An action's help line: the game's help text (menu/help.h), decoded and fitted. False when the game has none.
bool help_line(uint32_t action, bool use_cache, char* out, uint32_t cap)
{
    game_reader r(image_base());
    uint32_t id = 0;
    const uintptr_t at = menu::action_help_id(r, action, &id) ? menu::help_text_at(r, id) : 0;
    if (at == 0) {
        return false;
    }
    if (use_cache) {
        for (const cached_help& c : g_help_cache) {
            if (c.at == at) {
                copy_text(out, cap, c.text);
                return out[0] != '\0';
            }
        }
    }
    uint8_t raw[menu::HELP_RAW_CAP];
    char ascii[menu::HELP_RAW_CAP];
    char text[HELP_TEXT];
    text[0] = '\0';
    const uint32_t n = menu::read_help(r, at, raw, sizeof(raw));
    if (n != 0 && decode_game_text(raw, n, ascii, sizeof(ascii)) != 0) {
        menu::fit_text(ascii, text, sizeof(text));
    }
    if (use_cache) {
        cached_help& slot = g_help_cache[g_help_next];
        g_help_next = (g_help_next + 1) % HELP_CACHE;
        slot.at = at;
        copy_text(slot.text, sizeof(slot.text), text);
    }
    copy_text(out, cap, text);
    return out[0] != '\0';
}

bool live_help(void*, uint32_t action, char* out, uint32_t cap) { return help_line(action, true, out, cap); }

bool live_action(void*, uint32_t action, char* out, uint32_t cap)
{
    return cached(NK_ACTION, action, 0, out, cap, fetch_action);
}

bool live_member(void*, uint32_t member, char* out, uint32_t cap)
{
    return cached(NK_MEMBER, member, 0, out, cap, fetch_member);
}

bool live_foe(void*, uint64_t text, char* out, uint32_t cap)
{
    return cached(NK_FOE, 0, text, out, cap, fetch_foe);
}

void cache_check(uint32_t epoch)
{
    game_reader r(image_base());
    uintptr_t dir = 0;
    r.ptr(r.global(mf::NAME_TEXT_PTR), &dir);
    if (epoch != g_cache_epoch || dir != g_cache_directory) {
        detail::zero_bytes(g_cache, sizeof(g_cache));
        detail::zero_bytes(g_help_cache, sizeof(g_help_cache));
        g_help_next = 0;
        g_cache_next = 0;
        g_cache_epoch = epoch;
        g_cache_directory = dir;
    }
}

// ---- rows and help ---------------------------------------------------------------------------------------------------
bool greyed(const menu::entry& e)
{
    return (e.flags & menu::EF_ENABLED) == 0 ||
           (e.flags & (menu::EF_MP_SHORT | menu::EF_SEALED | menu::EF_HELD_BACK)) != 0;
}

bool magick_page(uint32_t page) { return page >= menu::PG_WHITE && page <= menu::PG_ARCANE; }

void entry_help(const menu::entry& e, uint32_t page, const names& n, char* out, uint32_t cap)
{
    out[0] = '\0';
    switch (e.kind) {
    case menu::EK_PAGE:
        switch (e.role) {
        case menu::ROLE_MAGICKS: copy_text(out, cap, "Cast magicks and use technicks."); return;
        case menu::ROLE_MIST: copy_text(out, cap, "Quickenings and summons."); return;
        case menu::ROLE_ITEMS: copy_text(out, cap, "Use an item."); return;
        case menu::ROLE_SCHOOL: copy_text(out, cap, role_label(e)); append_text(out, cap, "."); return;
        case menu::ROLE_TECHNICKS: copy_text(out, cap, "Use a technick."); return;
        case menu::ROLE_QUICKENING: copy_text(out, cap, "Unleash a Quickening."); return;
        case menu::ROLE_SUMMON: copy_text(out, cap, "Summon an Esper."); return;
        default: return;
        }
    case menu::EK_TOGGLE:
        copy_text(out, cap, "Turn this character's gambits on or off.");
        return;
    case menu::EK_ACTION:
        if ((e.flags & menu::EF_SEALED) != 0) {
            copy_text(out, cap, "Cannot be used now.");
        } else if ((e.flags & menu::EF_MP_SHORT) != 0) {
            copy_text(out, cap, "Not enough MP.");
        } else if ((e.flags & menu::EF_HELD_BACK) != 0) {
            copy_text(out, cap, "None left to use.");
        } else if ((e.cost_flags & menu::CF_CHARGES_SHORT) != 0) {
            copy_text(out, cap, "Not enough Mist charges.");
        } else if ((e.cost_flags & menu::CF_GIL_SHORT) != 0) {
            copy_text(out, cap, "Not enough gil.");
        } else if ((e.flags & menu::EF_ENABLED) == 0) {
            copy_text(out, cap, "Cannot be used now.");
        } else if (n.help != nullptr && n.help(n.ctx, e.action, out, cap)) {
            return;   // the game's own help line
        } else if ((e.flags & menu::EF_SELF_ONLY) != 0) {
            copy_text(out, cap, "Target: self");
        } else {
            const bool foes = (e.groups & (1u << menu::TG_FOES)) != 0;
            const bool party = (e.groups & (1u << menu::TG_PARTY)) != 0;
            copy_text(out, cap, foes && party ? "Target: foes or party" : (party ? "Target: party" : "Target: foes"));
        }
        if (magick_page(page) && e.cost > 0 && (e.flags & menu::EF_SEALED) == 0) {
            char n[8];
            put_uint(n, sizeof(n), static_cast<uint32_t>(e.cost));
            append_text(out, cap, "  MP ");
            append_text(out, cap, n);
        }
        return;
    default:
        return;
    }
}

void entry_row(const menu::entry& e, uint32_t page, const names& n, view_row* r)
{
    const char* label = role_label(e);
    if (label != nullptr) {
        copy_text(r->text, ROW_TEXT, label);
    } else {
        n.action(n.ctx, e.display, r->text, ROW_TEXT);   // a name that cannot be read shows as '?'
    }
    r->value[0] = '\0';
    if (e.kind == menu::EK_ACTION && page == menu::PG_ITEMS) {
        put_uint(r->value, ROW_VALUE, e.quantity);
    } else if (e.kind == menu::EK_ACTION && magick_page(page)) {
        if ((e.flags & menu::EF_COST_UNKNOWN) != 0 || e.cost < 0) {
            copy_text(r->value, ROW_VALUE, "?");
        } else if (e.cost > 0) {
            put_uint(r->value, ROW_VALUE, static_cast<uint32_t>(e.cost));
        }
    }
    r->flags = 0;
    if (e.kind == menu::EK_LABEL || greyed(e)) {
        r->flags |= RF_GREY;
    }
    if (e.kind == menu::EK_PAGE) {
        r->flags |= RF_PAGE;
    }
    if (e.kind == menu::EK_TOGGLE) {
        r->flags |= RF_TOGGLE;
    }
}

// The rows [first, first + VIEW_ROWS) around `cursor` of `total` items.
uint16_t window_first(uint32_t cursor, uint32_t total)
{
    if (total <= VIEW_ROWS || cursor < VIEW_ROWS / 2) {
        return 0;
    }
    const uint32_t first = cursor - VIEW_ROWS / 2 + 1;
    return static_cast<uint16_t>(first + VIEW_ROWS > total ? total - VIEW_ROWS : first);
}

void seat_title(uint32_t seat, const char* title, seat_view* out)
{
    out->title[0] = '\0';
    if (seat != 0) {
        // A guest's header names its seat: "P2 - Green Magicks".
        char p[4] = {'P', static_cast<char>('1' + seat), '\0', '\0'};
        copy_text(out->title, TITLE_TEXT, p);
        append_text(out->title, TITLE_TEXT, " - ");
    }
    append_text(out->title, TITLE_TEXT, title);
}

bool reduced_layout()
{
    if (g_force_reduced >= 0) {
        return g_force_reduced != 0;
    }
    const uint32_t state = native::status().layout_state;
    return state == relayout::LAYOUT_REDUCED || state == relayout::LAYOUT_REDUCED_OTHER;
}

bool build_view(uint64_t now_ms, menu_view* out)
{
    detail::zero_bytes(out, sizeof(menu_view));
    session::session_record rec;
    if (!session::read(&rec) || rec.head.epoch == 0 || (rec.head.flags & session::GATE_WORLD) == 0 ||
        control::current_state() != control::STATE_ON || !patch::subsystem_live(ILC_SUBSYS_RENDER)) {
        return false;
    }
    out->head.epoch = rec.head.epoch;
    out->head.flags = reduced_layout() ? uint32_t{VIEW_REDUCED} : 0u;
    out->head.generation = ++g_generation;
    out->built_ms = now_ms != 0 ? now_ms : 1;
    if (g_test_view != nullptr) {
        const uint32_t epoch = out->head.epoch;
        const uint64_t generation = out->head.generation;
        detail::copy_bytes(out, g_test_view, sizeof(menu_view));
        out->head.epoch = epoch;
        out->head.generation = generation;
        out->built_ms = now_ms != 0 ? now_ms : 1;
        return true;
    }
    if (g_track_epoch != rec.head.epoch) {
        detail::zero_bytes(g_track, sizeof(g_track));
        g_track_epoch = rec.head.epoch;
    }
    cache_check(rec.head.epoch);
    frontend::state_view st;
    frontend::read_state(&st);
    const bool hidden = st.susp.suspended != 0 || st.recovery != 0 || st.presenter_live == 0;
    const names live = {live_action, live_member, live_foe, nullptr, live_help};
    for (uint32_t s = 0; s < SEATS; ++s) {
        seat_view& sv = out->seats[s];
        sv.cursor = 0xFF;
        sv.member = targets::MEMBER_NONE;
        if (!hidden) {
            frontend::seat_view fv;
            frontend::read_seat(s, &fv);
            const menu::seat_menu* sm = frontend::seat_menu_of(s);
            if (sm != nullptr) {
                const seat_source src = {&sm->model, &sm->gambits, frontend::candidates(s), frontend::selector_of(s),
                                         fv.member, static_cast<uint8_t>(s), {}};
                build_seat(src, live, &sv);
            }
        }
        if (sv.kind != WK_NONE) {
            out->open_mask |= 1u << s;
            sv.colour = seat_colour(s, sv.member, &sv.colour_index);
            if (g_track[s].kind == WK_NONE) {
                g_track[s].opened_ms = now_ms != 0 ? now_ms : 1;
            }
            sv.opened_ms = g_track[s].opened_ms;
        }
        g_track[s].kind = sv.kind;
    }
    return true;
}

}  // namespace

// ---- pure helpers ---------------------------------------------------------------------------------------------------------
uint32_t decode_game_text(const uint8_t* in, uint32_t in_len, char* out, uint32_t cap)
{
    uint32_t n = 0;
    if (cap == 0) {
        return 0;
    }
    for (uint32_t i = 0; i < in_len && in[i] != 0 && n + 1 < cap; ++i) {
        const uint8_t c = in[i];
        char ch = 0;
        if (c == 0x0F && i + 1 < in_len && in[i + 1] == 0x29) {
            // A colour token: 0F 29, a header whose low bits count the value bytes (0 = one reset byte).
            const uint32_t values = i + 2 < in_len ? (in[i + 2] & 7u) : 0;
            i += 2 + (values == 0 ? 1 : values);
            continue;
        }
        if (c >= 32 && c <= 57) {
            ch = static_cast<char>('A' + (c - 32));
        } else if (c >= 58 && c <= 83) {
            ch = static_cast<char>('a' + (c - 58));
        } else if (c >= 0x85 && c <= 0x8E) {
            ch = static_cast<char>('0' + (c - 0x85));
        } else {
            switch (c) {
            case 2: case 4: ch = ' '; break;
            case 115: ch = 'e'; break;   // e acute
            case 129: ch = 'u'; break;   // u acute
            case 154: ch = '?'; break;
            case 158: ch = '%'; break;
            case 160: ch = '&'; break;
            case 162: ch = '/'; break;
            case 165: ch = '-'; break;
            case 166: ch = '+'; break;
            case 167: ch = ','; break;
            case 168: ch = '.'; break;
            case 169: ch = ':'; break;
            case 170: ch = ';'; break;
            case 171: ch = '!'; break;
            case 172: ch = '\''; break;
            case 173: ch = '"'; break;
            case 174: ch = '('; break;
            case 175: ch = ')'; break;
            default: ch = 0; break;      // a control code or a glyph ASCII cannot show: left out
            }
        }
        if (ch != 0 && !(ch == ' ' && (n == 0 || out[n - 1] == ' '))) {
            out[n++] = ch;
        }
    }
    while (n > 0 && out[n - 1] == ' ') {
        --n;
    }
    out[n] = '\0';
    return n;
}

const char* role_label(const menu::entry& e)
{
    switch (e.role) {
    case menu::ROLE_MAGICKS: return "Magicks & Technicks";
    case menu::ROLE_MIST: return "Mist";
    case menu::ROLE_GAMBITS: return "Gambits";
    case menu::ROLE_ITEMS: return "Items";
    case menu::ROLE_SCHOOL: return page_title(e.page);
    case menu::ROLE_TECHNICKS: return "Technicks";
    case menu::ROLE_QUICKENING: return "Quickenings";
    case menu::ROLE_SUMMON: return "Summon";
    case menu::ROLE_EMPTY: return "No actions available";
    default: return nullptr;   // Attack, Dismiss and every action: the game's own name
    }
}

const char* page_title(uint32_t page)
{
    switch (page) {
    case menu::PG_MAGICKS: return "Magicks & Technicks";
    case menu::PG_WHITE: return "White Magicks";
    case menu::PG_BLACK: return "Black Magicks";
    case menu::PG_TIME: return "Time Magicks";
    case menu::PG_GREEN: return "Green Magicks";
    case menu::PG_ARCANE: return "Arcane Magicks";
    case menu::PG_TECHNICKS: return "Technicks";
    case menu::PG_MIST: return "Mist";
    case menu::PG_QUICKENINGS: return "Quickenings";
    case menu::PG_SUMMONS: return "Summon";
    case menu::PG_ITEMS: return "Items";
    default: return nullptr;   // the root: the character's name
    }
}

const char* group_name(uint32_t group)
{
    switch (group) {
    case menu::TG_FOES: return "Foes";
    case menu::TG_NEUTRALS: return "Others";
    case menu::TG_PARTY: return "Party";
    case menu::TG_RESERVE: return "Reserve";
    default: return nullptr;
    }
}

void build_seat(const seat_source& src, const names& n, seat_view* out)
{
    const uint32_t seat = src.seat;
    out->kind = WK_NONE;
    out->cursor = 0xFF;
    out->row_count = 0;
    out->member = src.member;
    out->title[0] = out->name[0] = out->help[0] = '\0';
    if (src.member != targets::MEMBER_NONE) {
        n.member(n.ctx, src.member, out->name, ROW_TEXT);
    }
    const frontend::selector* sel = static_cast<const frontend::selector*>(src.selector);
    if (sel != nullptr && sel->kind != frontend::SEL_NONE) {
        out->kind = WK_SELECTOR;
        out->selector = sel->kind;
        out->total = sel->count;
        out->first = window_first(sel->index, sel->count);
        for (uint32_t k = out->first; k < sel->count && k < frontend::SELECTOR_SLOTS && out->row_count < VIEW_ROWS;
             ++k) {
            view_row& r = out->rows[out->row_count];
            n.member(n.ctx, sel->member[k], r.text, ROW_TEXT);
            r.value[0] = '\0';
            r.flags = sel->member[k] == src.member ? RF_ISSUER : 0;
            if (k == sel->index) {
                out->cursor = out->row_count;
            }
            ++out->row_count;
        }
        const char* title = sel->kind == frontend::SEL_LEADER      ? "Change Leader"
                            : sel->kind == frontend::SEL_CHARACTER ? "Change Character"
                                                                   : "Choose a Leader";
        seat_title(seat, title, out);
        copy_text(out->help, HELP_TEXT,
                  sel->kind == frontend::SEL_LEADER      ? "Choose the party leader."
                  : sel->kind == frontend::SEL_CHARACTER ? "Choose the character to control."
                                                         : "Your character is down. Choose who leads.");
        return;
    }
    if (src.model == nullptr) {
        return;
    }
    const menu::menu_model& m = *src.model;
    if (m.mode == menu::MODE_LIST && m.depth > 0 && m.depth <= menu::MAX_DEPTH) {
        const uint32_t page = m.stack[m.depth - 1].page;
        const uint32_t index = m.stack[m.depth - 1].index;
        const menu::entry* entries = menu::page_entries(m.pages, page);
        const uint32_t total = menu::page_count(m.pages, page);
        out->kind = WK_LIST;
        out->page = static_cast<uint8_t>(page);
        out->depth = m.depth;
        out->total = static_cast<uint16_t>(total);
        out->first = window_first(index, total);
        for (uint32_t k = out->first; entries != nullptr && k < total && out->row_count < VIEW_ROWS; ++k) {
            view_row& r = out->rows[out->row_count];
            entry_row(entries[k], page, n, &r);
            if (entries[k].kind == menu::EK_TOGGLE && src.gambits != nullptr && src.gambits->known != 0) {
                r.flags |= RF_KNOWN;
                if (src.gambits->enabled != 0) {
                    r.flags |= RF_ON;
                }
            }
            if (k == index) {
                out->cursor = out->row_count;
                entry_help(entries[k], page, n, out->help, HELP_TEXT);
            }
            ++out->row_count;
        }
        const char* title = page_title(page);
        seat_title(seat, title != nullptr ? title : out->name, out);
        return;
    }
    if (m.mode == menu::MODE_TARGET && src.cands != nullptr) {
        const targets::candidate_list& l = *src.cands;
        const uint32_t group = m.target_group < menu::TG_COUNT ? uint32_t{m.target_group} : uint32_t{menu::TG_FOES};
        const uint32_t total = l.selectable[group];
        out->kind = WK_TARGET;
        out->group = static_cast<uint8_t>(group);
        out->groups = targets::available_groups(l);
        out->total = static_cast<uint16_t>(total);
        out->first = window_first(m.target_index, total);
        for (uint32_t k = out->first; k < total && out->row_count < VIEW_ROWS; ++k) {
            const targets::candidate* c = targets::selectable_at(l, group, k);
            if (c == nullptr) {
                break;
            }
            view_row& r = out->rows[out->row_count];
            if (c->member != targets::MEMBER_NONE) {
                n.member(n.ctx, c->member, r.text, ROW_TEXT);
            } else {
                n.foe(n.ctx, c->name, r.text, ROW_TEXT - 4);
                if (c->letter != 0) {
                    char suffix[3] = {' ', static_cast<char>(c->letter), '\0'};
                    append_text(r.text, ROW_TEXT, suffix);
                } else if (c->number != 0) {
                    char num[8];
                    put_uint(num, sizeof(num), c->number);
                    append_text(r.text, ROW_TEXT, " ");
                    append_text(r.text, ROW_TEXT, num);
                }
            }
            r.value[0] = '\0';
            r.flags = (c->flags & targets::CF_ISSUER) != 0 ? RF_ISSUER : 0;
            if (k == m.target_index) {
                out->cursor = out->row_count;
            }
            ++out->row_count;
        }
        char action[ROW_TEXT];
        action[0] = '\0';
        n.action(n.ctx, m.pending.display, action, sizeof(action));
        seat_title(seat, action, out);
        copy_text(out->help, HELP_TEXT, total != 0 ? "Choose a target." : "Nothing in range.");
        return;
    }
}

uint32_t seat_colour(uint32_t seat, uint32_t member, uint8_t* index)
{
    uint8_t c = seat < ILC_SEATS ? g_colours.seat[seat] : 0;
    if (g_colours.mode == ILC_COLOUR_MODE_CHARACTER && member < ILC_CHARACTERS) {
        c = g_colours.character[member];
    }
    if (c >= ILC_ENUM_COUNT(ILC_COLOURS)) {
        c = 0;
    }
    if (index != nullptr) {
        *index = c;
    }
    const rgb v = SEAT_RGB[c];
    return pack(rgba{static_cast<uint8_t>((v.r + 1) / 2), static_cast<uint8_t>((v.g + 1) / 2),
                     static_cast<uint8_t>((v.b + 1) / 2), 0x80});
}

// ---- the co-op thread ------------------------------------------------------------------------------------------------------
void configure_from_settings(const ilc_settings& s)
{
    g_colours = colours_of(s);
}

void start(uintptr_t image_base, uint64_t now_ms)
{
    (void)now_ms;
    g_base = image_base;
    detail::zero_bytes(g_track, sizeof(g_track));
    g_track_epoch = 0;
    g_started = true;
}

void step(uint64_t now_ms)
{
    if (!g_started) {
        return;
    }
    if (!build_view(now_ms, &g_next)) {
        if (g_published != 0) {
            withdraw();
            g_published = 0;
        }
        detail::zero_bytes(g_track, sizeof(g_track));
        return;
    }
    publish(g_next);
    g_published = 1;
}

void stop()
{
    withdraw();
    g_published = 0;
    g_started = false;
}

// ---- host tests ----------------------------------------------------------------------------------------------------------
void test_reset()
{
    reset_draw_state();
    detail::zero_bytes(g_track, sizeof(g_track));
    detail::zero_bytes(g_cache, sizeof(g_cache));
    detail::zero_bytes(g_help_cache, sizeof(g_help_cache));
    g_help_next = 0;
    g_track_epoch = 0;
    g_cache_next = 0;
    g_cache_epoch = 0;
    g_cache_directory = 0;
    g_published = 0;
    g_base = 0;
    g_test_view = nullptr;
    g_force_reduced = -1;
    configure_from_settings(default_settings());
}

void test_set_image_base(uintptr_t image_base)
{
    g_base = image_base;
}

void test_set_view_source(const menu_view* v)
{
    g_test_view = v;
}

bool test_build(uint64_t now_ms, menu_view* out)
{
    return build_view(now_ms, out);
}

void test_force_reduced(int32_t reduced)
{
    g_force_reduced = reduced;
}

bool live_name(uint32_t kind, uint32_t id, uint64_t pointer, char* out, uint32_t cap)
{
    switch (kind) {
    case NK_ACTION: return live_action(nullptr, id, out, cap);
    case NK_MEMBER: return live_member(nullptr, id, out, cap);
    case NK_FOE: return live_foe(nullptr, pointer, out, cap);
    default: return false;
    }
}

bool test_live_name(uint32_t kind, uint32_t id, uint64_t pointer, char* out, uint32_t cap)
{
    switch (kind) {
    case NK_ACTION: return fetch_action(id, pointer, out, cap);
    case NK_MEMBER: return fetch_member(id, pointer, out, cap);
    case NK_FOE: return fetch_foe(id, pointer, out, cap);
    case NK_HELP: return help_line(id, false, out, cap);
    default: return false;
    }
}

}  // namespace ilc::hud::menus_draw
