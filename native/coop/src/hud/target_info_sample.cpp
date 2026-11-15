// Ivalice Legacy co-op module: the Target Info's view, composed on the co-op thread (see target_info.h).
//
// Once per beat, after the snapshot sampler: the latest HudSnapshot says which seat targeted last and what that
// target's HP, statuses, level and weakness are; a few reads of the game add what the snapshot does not carry, for
// that one target only: its name, the status block mask, the loot record, the steal state, the bestiary index, the
// Hunter's Insight knowledge for it and the names of its items. Every read goes through the fault-safe reader; a
// read that fails leaves its part out (never a guess). Nothing here writes game memory or calls a game function.
#include "bridge/bridge_internal.h"
#include "control/control.h"
#include "hud/hud_internal.h"
#include "hud/target_info.h"
#include "hud/target_info_facts.h"
#include "hud/target_info_internal.h"
#include "hud/target_info_native.h"
#include "ilc/bridge_abi.h"
#include "patch/patch_engine.h"
#include "session/reader.h"
#include "targets/live.h"

namespace ilc::hud::target_info {

using namespace detail_ti;
using hud::detail::copy_bytes;
using hud::detail::zero_bytes;
using session::game_reader;

namespace {

namespace tf = tifacts;

constexpr uint64_t HI_RESCAN_MS = 5000;
constexpr uint64_t ANTI_LIBRA_RECHECK_MS = 1000;
constexpr uint32_t SETTINGS_EVERY = 25;     // beats between two looks at the seat colours
constexpr uint32_t NAME_CACHE = 64;
constexpr uint32_t NAME_READ = 48;          // bytes of a name read at most

// Seat colours (ilc_colour order), 0x80 = full: the canvas's own palette (canvas.cpp), restated here.
constexpr rgba scaled(uint8_t r, uint8_t g, uint8_t b)
{
    return {static_cast<uint8_t>((r + 1) / 2), static_cast<uint8_t>((g + 1) / 2), static_cast<uint8_t>((b + 1) / 2),
            0x80};
}
const rgba SEAT_RGB[ILC_ENUM_COUNT(ILC_COLOURS)] = {
    scaled(70, 130, 240),   // sapphire
    scaled(230, 60, 70),    // ruby
    scaled(245, 120, 190),  // pink
    scaled(60, 210, 110),   // emerald
    scaled(240, 190, 50),   // gold
    scaled(170, 100, 235),  // amethyst
    scaled(205, 127, 50),   // bronze
};

// ---- co-op thread state --------------------------------------------------------------------------------------------
uintptr_t g_base = 0;                       // 0 = the pinned base
uint32_t g_published = 0;
uint64_t g_generation = 0;
uint32_t g_beat = 0;
uint8_t g_seat_colour[ILC_SEATS] = {ILC_COLOUR_GOLD, ILC_COLOUR_SAPPHIRE, ILC_COLOUR_EMERALD, ILC_COLOUR_AMETHYST};
hud_snapshot g_snap;                        // the snapshot copy this beat composes from
view g_next;                                // built here, then published
view g_last;                                // the last view that showed a target (held while it is gone)
uint64_t g_gone_since = 0;
uint32_t g_epoch = 0;
ghost_state g_ghost = {};
uint32_t g_ko_handle = 0;
uint64_t g_ko_since = 0;

struct hi_link {
    uint8_t state;           // hi_state
    uint8_t has_settings;
    uint8_t reserved[6];
    uint64_t save;
    uint64_t settings;
    uint64_t scanned_ms;
};
hi_link g_hi = {};
uint8_t g_cave[tf::HI_CAVE_SCAN];

uint8_t g_anti_stock = 1;
uint64_t g_anti_read_ms = 0;
uint8_t g_text_ready = 0;

struct name_entry {
    uint16_t item;
    uint8_t used;
    uint8_t reserved;
    char text[ITEM_TEXT];
};
name_entry g_names[NAME_CACHE];
uint32_t g_name_next = 0;
uint64_t g_text_signature = 0;

uintptr_t image_base() { return g_base != 0 ? g_base : static_cast<uintptr_t>(tf::PINNED_BASE); }

uint64_t since(uint64_t now, uint64_t then) { return now >= then ? now - then : 0; }

// ---- settings: the seat colours ------------------------------------------------------------------------------------
void refresh_colours()
{
    if (g_beat++ % SETTINGS_EVERY != 0) {
        return;
    }
    static ilc_settings applied;
    bridge_copy_applied(&applied);
    for (uint32_t k = 0; k < ILC_SEATS; ++k) {
        const uint8_t c = applied.seats[k].colour;
        g_seat_colour[k] = c < ILC_ENUM_COUNT(ILC_COLOURS) ? c : static_cast<uint8_t>(ILC_COLOUR_GOLD);
    }
}

// ---- Anti-Libra test: stock or patched out -------------------------------------------------------------------------
bool anti_libra_stock(game_reader& r, uint64_t now)
{
    if (g_anti_read_ms != 0 && since(now, g_anti_read_ms) < ANTI_LIBRA_RECHECK_MS) {
        return g_anti_stock != 0;
    }
    // Unreadable counts as stock: the native rule applies unless the test is seen patched.
    uint8_t bytes[sizeof(tf::ANTI_LIBRA_STOCK)];
    bool stock = true;
    if (r.read(r.global(tf::ANTI_LIBRA_TEST), bytes, sizeof(bytes))) {
        for (uint32_t k = 0; k < sizeof(bytes); ++k) {
            stock = stock && bytes[k] == tf::ANTI_LIBRA_STOCK[k];
        }
    }
    g_anti_stock = stock ? 1 : 0;
    g_anti_read_ms = now != 0 ? now : 1;
    return stock;
}

// ---- Hunter's Insight ------------------------------------------------------------------------------------------------
uint64_t le(const uint8_t* p, uint32_t n)
{
    uint64_t v = 0;
    for (uint32_t k = 0; k < n; ++k) {
        v |= static_cast<uint64_t>(p[k]) << (8 * k);
    }
    return v;
}

uint32_t read_cave(game_reader& r, uintptr_t cave)
{
    uint32_t n = 0;
    while (n < sizeof(g_cave)) {
        const uintptr_t at = cave + n;
        uint32_t chunk = static_cast<uint32_t>(((at | 0xFFFu) + 1) - at);
        if (chunk > sizeof(g_cave) - n) {
            chunk = static_cast<uint32_t>(sizeof(g_cave) - n);
        }
        if (!r.read(at, g_cave + n, chunk)) {
            break;
        }
        n += chunk;
    }
    return n;
}

// HI's bounds check of a bestiary index against its save block's count, then the address of that index's 4 bytes
// (9 bytes from b; b[3] is a branch distance). Right before it HI loads the block's address into rax: a 64-bit
// immediate 10 bytes back, a sign-extended 32-bit one 7 bytes back, or a 32-bit one into eax 5 bytes back. Returns
// the form found (1..3, 0 none); b must have 10 readable bytes before it.
uint32_t save_check_at(const uint8_t* b, uint64_t* address)
{
    const bool check = b[0] == 0x3B && b[1] == 0x10 && b[2] == 0x73 && b[4] == 0x48 && b[5] == 0x8D &&
                       b[6] == 0x44 && b[7] == 0x90 && b[8] == 0x04;
    if (!check) {
        return 0;
    }
    if (b[-10] == 0x48 && b[-9] == 0xB8) {
        *address = le(b - 8, 8);
        return 1;
    }
    if (b[-7] == 0x48 && b[-6] == 0xC7 && b[-5] == 0xC0) {
        *address = static_cast<uint64_t>(static_cast<int64_t>(static_cast<int32_t>(le(b - 4, 4))));
        return 2;
    }
    if (b[-5] == 0xB8) {
        *address = le(b - 4, 4);
        return 3;
    }
    return 0;
}

// The opening of HI's page-flag reader: it saves rbx, reserves its shadow space, keeps the page index in ebx and
// compares its alwaysShowItems word with zero, then branches. The word is RIP-relative (disp32 at +9, the next
// instruction at +14) or absolute (abs32 at +10). Returns the word's address (`here` = b's address), or 0.
uint64_t flag_reader_at(const uint8_t* b, uintptr_t here)
{
    const bool opening = b[0] == 0x53 && b[1] == 0x48 && b[2] == 0x83 && b[3] == 0xEC && b[4] == 0x20 &&
                         b[5] == 0x8B && b[6] == 0xDA && b[7] == 0x83;
    if (!opening) {
        return 0;
    }
    if (b[8] == 0x3D && b[13] == 0x00 && b[14] == 0x75) {
        const int32_t disp = static_cast<int32_t>(static_cast<uint32_t>(le(b + 9, 4)));
        return static_cast<uint64_t>(here + 14 + static_cast<intptr_t>(disp));
    }
    if (b[8] == 0x3C && b[9] == 0x25 && b[14] == 0x00 && b[15] == 0x75) {
        return le(b + 10, 4);
    }
    return 0;
}

void find_hi(game_reader& r, uint64_t now)
{
    bump(g_counters.hi_scans);
    zero_bytes(&g_hi, sizeof(g_hi));
    g_hi.scanned_ms = now != 0 ? now : 1;
    uint8_t hook[5];
    const uintptr_t site = r.global(tf::HI_HOOK);
    if (!r.read(site, hook, sizeof(hook))) {
        g_hi.state = HI_FOREIGN;
        return;
    }
    bool stock = true;
    for (uint32_t k = 0; k < sizeof(hook); ++k) {
        stock = stock && hook[k] == tf::HI_HOOK_STOCK[k];
    }
    if (stock) {
        g_hi.state = HI_ABSENT;
        return;
    }
    g_hi.state = HI_FOREIGN;
    if (hook[0] != 0xE9) {
        return;
    }
    const int32_t rel = static_cast<int32_t>(static_cast<uint32_t>(le(hook + 1, 4)));
    const uintptr_t cave = site + 5 + static_cast<intptr_t>(rel);
    const uint32_t n = read_cave(r, cave);
    for (uint32_t i = 10; i + 9 <= n && g_hi.save == 0; ++i) {
        uint64_t candidate = 0;
        if (save_check_at(g_cave + i, &candidate) != 0) {
            uint32_t count = 0;
            if (candidate != 0 && r.u32(static_cast<uintptr_t>(candidate), &count) && count == tf::HI_SAVE_COUNT) {
                g_hi.save = candidate;
            }
        }
    }
    if (g_hi.save == 0) {
        return;
    }
    for (uint32_t i = 0; i + 16 <= n && g_hi.has_settings == 0; ++i) {
        const uint64_t asi = flag_reader_at(g_cave + i, cave + i);
        uint32_t pair[2] = {0, 0};
        if (asi >= 4 && r.read(static_cast<uintptr_t>(asi - 4), pair, sizeof(pair)) && pair[0] > 0 &&
            pair[0] < 100000 && pair[1] <= 1) {
            g_hi.settings = asi - 4;
            g_hi.has_settings = 1;
        }
    }
    g_hi.state = HI_FOUND;
}

// The knowledge bytes (pages 0..3) for a bestiary index; false = no knowledge source.
bool hi_knowledge(game_reader& r, uint16_t bestiary, uint64_t now, uint8_t known[4])
{
    if (g_hi.state == HI_UNKNOWN || (g_hi.state != HI_FOUND && since(now, g_hi.scanned_ms) >= HI_RESCAN_MS)) {
        find_hi(r, now);
    }
    if (g_hi.state != HI_FOUND) {
        return false;
    }
    uint32_t count = 0;
    if (!r.u32(static_cast<uintptr_t>(g_hi.save), &count) || count != tf::HI_SAVE_COUNT) {
        find_hi(r, now);   // reloaded or gone: look again now
        if (g_hi.state != HI_FOUND) {
            return false;
        }
    }
    uint32_t pair[2] = {0, 0};
    const bool always = g_hi.has_settings != 0 && r.read(static_cast<uintptr_t>(g_hi.settings), pair, sizeof(pair)) &&
                        pair[1] == 1;
    if (always || bestiary >= tf::HI_SAVE_COUNT) {
        known[0] = known[1] = known[2] = known[3] = 0xFF;   // HI shows every item then
        return true;
    }
    return r.read(static_cast<uintptr_t>(g_hi.save + 4 + 4ull * bestiary), known, 4);
}

// ---- item names from the game's name text --------------------------------------------------------------------------
uint64_t text_signature(game_reader& r)
{
    uint64_t base = 1;
    uint32_t text = 0;
    if (!r.u64(r.global(tf::RESOURCE_BASE_PTR), &base) || !r.u32(r.global(tf::NAME_TEXT), &text)) {
        return 0;
    }
    uint64_t sig = 0xCBF29CE484222325ull ^ base;   // NOTADDR: FNV-1a offset basis
    sig = (sig ^ text) * 0x100000001B3ull;          // NOTADDR: FNV-1a prime
    for (const tf::item_table& t : tf::ITEM_TABLES) {
        uintptr_t table = 0;
        r.ptr(r.global(t.global), &table);
        sig = (sig ^ table) * 0x100000001B3ull;     // NOTADDR
    }
    return base == 0 ? sig : 0;   // a non-zero resource base: addresses are not absolute, no names
}

bool lookup_text(game_reader& r, uint32_t link, char* out, uint32_t cap)
{
    const uint32_t group = link >> 11;
    const uint32_t entry = link & (tf::TEXT_STRINGS_MAX - 1);
    uint32_t section = 0;
    uint16_t groups = 0;
    if (!r.u32(r.global(tf::NAME_TEXT), &section) || section == 0 || !r.u16(section, &groups) ||
        groups > tf::TEXT_GROUPS_MAX || group >= groups) {
        return false;
    }
    uint32_t at[2] = {0, 0};
    uint32_t end = 0;
    if (!r.read(section + 4 + 4 * group, at, sizeof(at)) || !r.u32(section + 4 + 4 * groups, &end) ||
        !(section < at[0] && at[0] < at[1] && at[1] <= end)) {
        return false;
    }
    const uint32_t blob = at[0];
    const uint32_t blob_end = at[1];
    uint16_t strings = 0;
    if (!r.u16(blob, &strings) || entry >= strings) {
        return false;
    }
    uint32_t s[2] = {0, 0};
    if (!r.read(blob + 4 + 4 * entry, s, sizeof(s)) || !(blob < s[0] && s[0] + tf::TEXT_PREFIX <= s[1] &&
                                                          s[1] <= blob_end)) {
        return false;
    }
    uint32_t len = s[1] - s[0] - tf::TEXT_PREFIX;
    if (len > NAME_READ) {
        len = NAME_READ;
    }
    uint8_t raw[NAME_READ];
    if (len == 0 || !r.read(s[0] + tf::TEXT_PREFIX, raw, len)) {
        return false;
    }
    return decode_text(raw, len, out, cap) != 0;
}

bool lookup_item(game_reader& r, uint16_t item, char* out, uint32_t cap)
{
    const tf::item_table* t = nullptr;
    for (const tf::item_table& candidate : tf::ITEM_TABLES) {
        if (item >= candidate.first && item < candidate.first + 0x1000) {
            t = &candidate;
        }
    }
    if (t == nullptr) {
        return false;
    }
    uintptr_t table = 0;
    uint32_t count = 0;
    uint16_t stride = 0;
    uint32_t rows = 0;
    if (!r.ptr(r.global(t->global), &table) || table == 0 || !r.u32(table + tf::TABLE_COUNT, &count) ||
        !r.u16(table + tf::TABLE_STRIDE, &stride) || !r.u32(table + tf::TABLE_ROWS, &rows) || rows == 0 ||
        stride != t->stride) {
        return false;
    }
    const uint32_t index = static_cast<uint32_t>(item - t->first);
    uint16_t link = 0;
    if (index >= count || !r.u16(static_cast<uintptr_t>(rows) + static_cast<uintptr_t>(index) * stride + t->link,
                                 &link)) {
        return false;
    }
    return lookup_text(r, link, out, cap);
}

struct name_ctx {
    game_reader* r;
};

bool item_name(void* ctx, uint16_t item, char* out, uint32_t cap)
{
    game_reader& r = *static_cast<name_ctx*>(ctx)->r;
    for (const name_entry& e : g_names) {
        if (e.used != 0 && e.item == item) {
            uint32_t k = 0;
            for (; k + 1 < cap && e.text[k] != '\0'; ++k) {
                out[k] = e.text[k];
            }
            out[k] = '\0';
            return true;
        }
    }
    if (g_text_ready == 0 || !lookup_item(r, item, out, cap)) {
        bump(g_counters.name_misses);
        return false;
    }
    name_entry& e = g_names[g_name_next++ % NAME_CACHE];
    e.item = item;
    e.used = 1;
    uint32_t k = 0;
    for (; k + 1 < ITEM_TEXT && out[k] != '\0'; ++k) {
        e.text[k] = out[k];
    }
    e.text[k] = '\0';
    return true;
}

// ---- one target --------------------------------------------------------------------------------------------------
uint8_t party_level(const hud_snapshot& snap)
{
    uint8_t best = 0;
    for (uint32_t k = 0; k < snap.member_count && k < SNAP_MEMBERS; ++k) {
        if ((snap.members[k].flags & UNIT_PRESENT) != 0 && snap.members[k].level > best) {
            best = snap.members[k].level;
        }
    }
    return best;
}

void read_name(game_reader& r, uintptr_t actor, view* v)
{
    uintptr_t unit = 0;
    uintptr_t name = 0;
    if (!r.ptr(actor + tf::ACTOR_UNIT, &unit) || unit == 0 || !r.ptr(unit + tf::UNIT_NAME, &name) || name == 0) {
        return;
    }
    uint8_t raw[NAME_READ];
    uint32_t len = NAME_READ;
    while (len >= 8 && !r.read(name, raw, len)) {
        len /= 2;   // the text may end close to an unreadable page
    }
    if (len >= 8 && decode_text(raw, len, v->name, NAME_MAX) != 0) {
        v->head.flags |= VIEW_NAME;
    }
}

void read_drops(game_reader& r, uintptr_t actor, uint64_t now, view* v)
{
    uintptr_t loot_at = 0;
    uint8_t loot[tf::LOOT_SIZE];
    if (!r.ptr(actor + tf::ACTOR_LOOT, &loot_at) || loot_at == 0 || !r.read(loot_at, loot, sizeof(loot))) {
        return;
    }
    uint16_t bestiary = tf::NO_ITEM;
    uintptr_t cls = 0;
    if (r.ptr(actor + tf::ACTOR_CLASS, &cls) && cls != 0) {
        r.u16(cls + tf::CLASS_BESTIARY, &bestiary);
    }
    uint16_t steal_left = 0x07;   // no extended block: every steal still available (HI's own default)
    uintptr_t ext = 0;
    if (r.ptr(actor + tf::ACTOR_EXT, &ext) && ext != 0) {
        r.u16(ext + tf::EXT_STEAL_LEFT, &steal_left);
    }
    uint8_t known[4];
    if (!hi_knowledge(r, bestiary, now, known)) {
        return;
    }
    const uint64_t sig = text_signature(r);
    if (sig != g_text_signature) {
        zero_bytes(g_names, sizeof(g_names));
        g_name_next = 0;
        g_text_signature = sig;
    }
    g_text_ready = sig != 0 ? 1 : 0;
    name_ctx ctx = {&r};
    v->drop_count = static_cast<uint8_t>(build_drops(loot, known, steal_left, item_name, &ctx, v->drops, DROP_LINES));
    v->head.flags |= VIEW_DROPS;
}

void fill(game_reader& r, const hud_snapshot& snap, uint32_t seat, const hud_unit& u, uint64_t now, view* v)
{
    v->head.flags = VIEW_SHOWN;
    v->handle = u.handle;
    v->seat = static_cast<uint8_t>(seat + 1);
    v->seat_colour = pack(SEAT_RGB[g_seat_colour[seat]]);
    v->level = u.level;
    v->weakness = u.weakness;
    v->hp = u.hp;
    v->hp_max = u.hp_max;
    if (u.hp_max != 0) {
        v->head.flags |= VIEW_HP;
    }
    v->ghost = ghost_step(&g_ghost, u.handle, u.hp, now);
    const bool party = (u.flags & UNIT_PARTY) != 0;
    if (party) {
        v->head.flags |= VIEW_PARTY;
    }
    if ((u.hp == 0 && u.hp_max != 0) || (u.status_lasting & 1u) != 0 || (u.status_timed & 1u) != 0) {
        if (g_ko_handle != u.handle) {
            g_ko_handle = u.handle;
            g_ko_since = now != 0 ? now : 1;
        }
        v->head.flags |= VIEW_KO;
        v->fade_from_ms = g_ko_since;
    } else if (g_ko_handle == u.handle) {
        g_ko_handle = 0;   // revived
    }
    if ((snap.head.flags & SNAP_TICKS) != 0 && snap.tick_scale != 0) {
        v->head.flags |= VIEW_TIMERS;
    }
    gate_in g = {};
    g.party = party ? 1 : 0;
    g.libra_known = (snap.head.flags & SNAP_LIBRA_KNOWN) != 0 ? 1 : 0;
    g.libra = (snap.head.flags & SNAP_LIBRA) != 0 ? 1 : 0;
    g.anti_libra = (u.flags & UNIT_ANTI_LIBRA) != 0 ? 1 : 0;
    g.anti_libra_stock = g.anti_libra != 0 && anti_libra_stock(r, now) ? 1 : 0;
    v->head.flags |= gate(g);
    const uint8_t top = party_level(snap);
    if (!party && top != 0 && u.level >= static_cast<uint32_t>(top) + DANGER_LEVELS) {
        v->head.flags |= VIEW_DANGER;
    }
    const uintptr_t actor = (u.flags & UNIT_ACTOR) != 0 ? targets::live_actor(r, u.handle, false) : 0;
    uint32_t block = 0;
    if (actor != 0) {
        read_name(r, actor, v);
        uintptr_t battle = 0;
        if (r.ptr(actor + tf::ACTOR_BATTLE, &battle) && battle != 0 && r.u32(battle + tf::REC_STATUS_BLOCK, &block)) {
            v->head.flags |= VIEW_IMMUNE;
        }
        if (!party) {
            read_drops(r, actor, now, v);
        }
    }
    const bool show_immune = (v->head.flags & VIEW_REVEAL) != 0 && (v->head.flags & VIEW_IMMUNE) != 0;
    v->status_count = static_cast<uint8_t>(build_statuses(u, block, show_immune, snap.tick_scale, v->status_id,
                                                          v->status_flags, v->status_secs));
}

bool build(const hud_snapshot& snap, uint64_t now, view* v)
{
    zero_bytes(v, sizeof(*v));
    v->head.epoch = snap.head.epoch;
    v->head.generation = ++g_generation;
    v->built_ms = now;
    if (snap.head.epoch != g_epoch) {
        g_epoch = snap.head.epoch;
        zero_bytes(&g_last, sizeof(g_last));
        zero_bytes(&g_ghost, sizeof(g_ghost));
        g_gone_since = 0;
        g_ko_handle = 0;
    }
    refresh_colours();
    int32_t index = -1;
    const int32_t seat = select_seat(snap, &index);
    if (seat >= 0 && index >= 0) {
        game_reader r(image_base());
        fill(r, snap, static_cast<uint32_t>(seat), snap.targets[index], now, v);
        copy_bytes(&g_last, v, sizeof(view));
        g_gone_since = 0;
        return true;
    }
    // Nothing to select now: the last target stays GONE_HOLD_MS, then fades.
    if ((g_last.head.flags & VIEW_SHOWN) != 0) {
        if (g_gone_since == 0) {
            g_gone_since = now != 0 ? now : 1;
        }
        if (since(now, g_gone_since) < GONE_HOLD_MS + KO_FADE_MS) {
            const uint64_t generation = v->head.generation;
            copy_bytes(v, &g_last, sizeof(view));
            v->head.generation = generation;
            v->built_ms = now;
            if ((v->head.flags & VIEW_KO) == 0) {
                v->head.flags |= VIEW_GONE;
                v->fade_from_ms = g_gone_since;
            }
            return true;
        }
        zero_bytes(&g_last, sizeof(g_last));
    }
    return true;   // an empty view: the pass is live, nothing shown
}

void reset_state()
{
    zero_bytes(&g_last, sizeof(g_last));
    zero_bytes(&g_ghost, sizeof(g_ghost));
    g_gone_since = 0;
    g_ko_handle = 0;
    g_epoch = 0;
}

}  // namespace

void sample_attach(uintptr_t image_base_)
{
    g_base = image_base_;
}

void step(uint64_t now_ms)
{
    giveback::sample(now_ms);   // the TI-V rows, whatever the element and co-op state (the give-back needs them then)
    const bool wanted = enabled() && patch::subsystem_live(ILC_SUBSYS_RENDER) &&
                        control::current_state() == control::STATE_ON;
    if (wanted && (snapshot::read(g_snap) || snapshot::read(g_snap)) && (g_snap.head.flags & SNAP_WORLD) != 0 &&
        g_snap.head.epoch != 0) {
        build(g_snap, now_ms, &g_next);
        publish(g_next);
        bump((g_next.head.flags & VIEW_SHOWN) != 0 ? g_counters.built : g_counters.empty);
        g_published = 1;
        return;
    }
    if (g_published != 0) {
        withdraw();
        bump(g_counters.withdrawn);
        g_published = 0;
        reset_state();
    }
}

void stop()
{
    withdraw();
    g_published = 0;
    reset_state();
}

status_view status()
{
    status_view s = {};
    s.attached = __atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) != 0 ? 1 : 0;
    s.enabled = enabled() ? 1 : 0;
    s.hi = g_hi.state;
    s.text_ready = g_text_ready;
    s.anti_libra_stock = g_anti_stock;
    return s;
}

// ---- host tests ---------------------------------------------------------------------------------------------------
void test_reset()
{
    withdraw();
    g_published = 0;
    reset_state();
    zero_bytes(const_cast<counters*>(&g_counters), sizeof(counters));
    zero_bytes(&g_hi, sizeof(g_hi));
    zero_bytes(g_names, sizeof(g_names));
    g_name_next = 0;
    g_text_signature = 0;
    g_text_ready = 0;
    g_anti_stock = 1;
    g_anti_read_ms = 0;
    g_base = 0;
    g_beat = 1;   // the seat colours keep their defaults until a test asks
    g_generation = 0;
    __atomic_store_n(&g_enabled, 1u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_last_drawn, uint64_t{0}, __ATOMIC_RELEASE);
    __atomic_store_n(&g_test_stamp, uint64_t{0}, __ATOMIC_RELEASE);
    test_set_p1_owned(-1);
}

void test_set_image_base(uintptr_t image_base_)
{
    g_base = image_base_;
}

bool test_build(const hud_snapshot& snap, uint64_t now_ms, view* out)
{
    return build(snap, now_ms, out);
}

bool item_name_now(uint16_t item, char* out, uint32_t cap)
{
    game_reader r(image_base());
    return text_signature(r) != 0 && lookup_item(r, item, out, cap);
}

bool test_item_name(uint16_t item, char* out, uint32_t cap)
{
    game_reader r(image_base());
    const uint64_t sig = text_signature(r);
    if (sig == 0) {
        return false;
    }
    return lookup_item(r, item, out, cap);
}

uint32_t test_find_hi(uint64_t* save_out, uint64_t* settings_out)
{
    game_reader r(image_base());
    find_hi(r, patch::now_ms());
    *save_out = g_hi.save;
    *settings_out = g_hi.has_settings != 0 ? g_hi.settings : 0;
    return g_hi.state;
}


// A block of code with the two shapes find_hi reads (each written byte for byte as the readers above test it):
// the save-block load and check at TEST_HI_SAVE_AT (the load 10 bytes earlier), the flag reader at TEST_HI_FLAGS_AT
// with its RIP-relative operand `flag_disp` (relative to TEST_HI_FLAGS_AT + 14). The rest is int3.
uint32_t test_hi_code(uint8_t* out, uint32_t cap, uint64_t save, int32_t flag_disp)
{
    if (cap < TEST_HI_FLAGS_AT + 16) {
        return 0;
    }
    for (uint32_t k = 0; k < cap; ++k) {
        out[k] = 0xCC;
    }
    uint8_t* load = out + TEST_HI_SAVE_AT - 10;
    load[0] = 0x48;
    load[1] = 0xB8;
    for (uint32_t k = 0; k < 8; ++k) {
        load[2 + k] = static_cast<uint8_t>(save >> (8 * k));
    }
    uint8_t* b = out + TEST_HI_SAVE_AT;
    b[0] = 0x3B;
    b[1] = 0x10;
    b[2] = 0x73;
    b[3] = 0x0E;
    b[4] = 0x48;
    b[5] = 0x8D;
    b[6] = 0x44;
    b[7] = 0x90;
    b[8] = 0x04;
    uint8_t* f = out + TEST_HI_FLAGS_AT;
    f[0] = 0x53;
    f[1] = 0x48;
    f[2] = 0x83;
    f[3] = 0xEC;
    f[4] = 0x20;
    f[5] = 0x8B;
    f[6] = 0xDA;
    f[7] = 0x83;
    f[8] = 0x3D;
    for (uint32_t k = 0; k < 4; ++k) {
        f[9 + k] = static_cast<uint8_t>(static_cast<uint32_t>(flag_disp) >> (8 * k));
    }
    f[13] = 0x00;
    f[14] = 0x75;
    f[15] = 0x05;
    uint64_t found = 0;
    return save_check_at(b, &found) == 1 && found == save && flag_reader_at(f, 0) == static_cast<uint64_t>(14 + flag_disp)
               ? 1
               : 0;
}

}  // namespace ilc::hud::target_info
