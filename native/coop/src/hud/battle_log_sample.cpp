// Ivalice Legacy co-op module: the battle log's co-op thread side (battle_log.h): drain the feed, assemble each hit
// from its bracket, resolve actors and names, read the chain, sample the units, run the model, publish the view; the
// settings and the expand control.
//
// Every game read goes through the fault-safe reader; a read that fails leaves its part out (never a guess). Nothing
// here writes game memory or calls a game function.
#include "bridge/bridge_internal.h"
#include "hud/battle_log.h"
#include "hud/battle_log_facts.h"
#include "hud/battle_log_internal.h"
#include "hud/hud_internal.h"
#include "hud/menus_draw.h"
#include "hud/snapshot.h"
#include "hud/target_info.h"
#include "ilc/bridge_abi.h"
#include "input/input_service.h"
#include "patch/patch_engine.h"
#include "patch/site_table.h"
#include "session/reader.h"

namespace ilc::hud::battle_log {
namespace {

namespace f = facts;
using hud::detail::copy_bytes;
using hud::detail::zero_bytes;
using session::game_reader;

constexpr uint32_t EXTRAS = 12;            // actors the feed named that the snapshot does not carry
constexpr uint64_t EXTRA_HOLD_MS = 10000;  // followed this long after their last message
constexpr uint32_t RESOLVED = 32;          // record -> actor cache (per epoch)
constexpr uint32_t SETTINGS_EVERY = 25;    // beats between two looks at the seat colours
constexpr uint32_t OBS_MAX = SNAP_MEMBERS + SNAP_TARGETS + EXTRAS;
constexpr uint16_t NO_ACTION = 0xFFFF;

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

struct resolved {
    uint64_t record;
    uint32_t handle;
    uint8_t side;
    uint8_t seat;
    uint16_t member;
    char name[NAME];
};

struct extra {
    uint32_t handle;
    uint8_t side;
    uint8_t seat;
    uint16_t member;
    uint64_t last_ms;
    char name[NAME];
};

// The units of this beat: snapshot rows and targets with their actor and record.
struct beat_unit {
    uint32_t handle;
    uint64_t actor;
    uint64_t record;
};

uintptr_t g_base = 0;
uint32_t g_started = 0;
uint32_t g_published = 0;
uint32_t g_epoch_model = 0;
uint64_t g_generation = 0;
uint32_t g_beat = 0;
uint32_t g_names_only = 0;
volatile uint64_t g_settings = 0;          // settings packed (any thread reads)
uint32_t g_last_held[4] = {};       // the expand control per seat at the previous beat
uint8_t g_seat_colour[ILC_SEATS] = {ILC_COLOUR_GOLD, ILC_COLOUR_SAPPHIRE, ILC_COLOUR_EMERALD, ILC_COLOUR_AMETHYST};
model g_model;
view g_next;
hud_snapshot g_snap;
event g_events[RING];
message g_msg;
unit_obs g_obs[OBS_MAX];
beat_unit g_beat_units[SNAP_MEMBERS + SNAP_TARGETS];
uint32_t g_beat_count = 0;
resolved g_resolved[RESOLVED];
uint32_t g_resolved_next = 0;
extra g_extras[EXTRAS];
input::snapshot g_input;

// One hit as its events arrive: HIT_BEGIN opens it, HIT_OUTCOME and HIT_NUMBERs fill it, HIT_END finishes it.
struct hit_build {
    uint32_t serial;                       // 0 = nothing open
    uint8_t has_outcome;
    uint8_t numbers;                       // HP amounts copied (all of them count in total)
    uint8_t amount_count;
    uint8_t reserved;
    uint64_t time_ms;
    hook_payload begin;
    hook_payload outcome;
    int32_t amounts[AMOUNTS_SHOWN];
    int32_t total;
    uint32_t reserved2;
};
hit_build g_build;
hit_build g_solo;                          // an outcome without a bracket of its own
hit g_hit;
kill g_kill;

// The chain, read once a beat after the drain.
struct chain_read {
    uint8_t known;
    uint8_t level;
    uint16_t shown;
};
chain_read g_chain;
uint32_t g_chain_epoch = 0;                // the epoch whose verify rows were compared
uint8_t g_chain_rows = 0;                  // 1 = CH-V1 and CH-V2 hold their pinned bytes

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

uintptr_t image_base() { return g_base != 0 ? g_base : static_cast<uintptr_t>(f::PINNED_BASE); }

uint64_t pack_settings(const settings& s)
{
    return static_cast<uint64_t>(s.enabled) | static_cast<uint64_t>(s.expanded) << 8 |
           static_cast<uint64_t>(s.expand_control) << 16 | static_cast<uint64_t>(s.filter) << 24 |
           static_cast<uint64_t>(s.filter_seat) << 32 | uint64_t{1} << 63;
}

settings unpack_settings(uint64_t v)
{
    if ((v >> 63) == 0) {
        return defaults();
    }
    settings s = {};
    s.enabled = static_cast<uint8_t>(v);
    s.expanded = static_cast<uint8_t>(v >> 8);
    s.expand_control = static_cast<uint8_t>(v >> 16);
    s.filter = static_cast<uint8_t>(v >> 24);
    s.filter_seat = static_cast<uint8_t>(v >> 32);
    return s;
}

void copy_text(char* dst, uint32_t cap, const char* src)
{
    uint32_t n = 0;
    for (; n + 1 < cap && src[n] != '\0'; ++n) {
        dst[n] = src[n];
    }
    if (cap != 0) {
        dst[n] = '\0';
    }
}

// ---- names ------------------------------------------------------------------------------------------------------------
bool name_of(uint32_t kind, uint32_t id, uint64_t pointer, char* out, uint32_t cap)
{
    out[0] = '\0';
    if (g_names_only != 0) {
        return menus_draw::test_live_name(kind, id, pointer, out, cap);
    }
    return menus_draw::live_name(kind, id, pointer, out, cap);
}

void action_name(uint16_t action, char* out, uint32_t cap)
{
    out[0] = '\0';
    if (action == NO_ACTION || action >= f::ACTION_LIMIT || action == f::ACTION_ATTACK) {
        return;
    }
    if (!name_of(1, action, 0, out, cap)) {
        out[0] = '\0';
    }
}

// A foe's name: the unit's name text, and the game's copy letter (1 = A) when it has one.
void foe_name(game_reader& r, uint64_t actor, char* out, uint32_t cap)
{
    out[0] = '\0';
    uintptr_t unit = 0;
    uintptr_t text = 0;
    if (actor == 0 || !r.ptr(actor + f::ACTOR_UNIT, &unit) || unit == 0 || !r.ptr(unit + f::UNIT_NAME, &text) ||
        text == 0 || !name_of(3, 0, text, out, cap)) {
        copy_text(out, cap, "?");
        return;
    }
    uint32_t instance = 0;
    if (r.u32(unit + f::UNIT_INSTANCE, &instance)) {
        const uint32_t letter = instance & 0xFFFF;
        uint32_t n = 0;
        while (n < cap && out[n] != '\0') {
            ++n;
        }
        if (letter >= 1 && letter <= 26 && n + 2 < cap) {
            out[n] = ' ';
            out[n + 1] = static_cast<char>('A' + letter - 1);
            out[n + 2] = '\0';
        }
    }
}

// The actor a handle names right now (the manager entry at its index, still carrying the full handle), or 0.
uint64_t live_actor(game_reader& r, uint32_t handle)
{
    if (handle == 0) {
        return 0;
    }
    uintptr_t manager = 0;
    uint32_t count = 0;
    uintptr_t entry = 0;
    uintptr_t actor = 0;
    uint32_t now = 0;
    const uint32_t index = handle & 0xFFFF;
    if (!r.ptr(r.global(f::ACTOR_MANAGER_PTR), &manager) || manager == 0 || !r.u32(manager + f::MANAGER_COUNT, &count) ||
        count > f::MANAGER_COUNT_MAX || index >= count ||
        !r.ptr(manager + f::MANAGER_ENTRIES + 8ull * index, &entry) || entry == 0 ||
        !r.ptr(entry + f::ENTRY_ACTOR, &actor) || actor == 0 || !r.u32(actor + f::ACTOR_HANDLE, &now) || now != handle) {
        return 0;
    }
    return actor;
}

// ---- record -> who ----------------------------------------------------------------------------------------------------
resolved* cache_find(uint64_t record)
{
    for (resolved& c : g_resolved) {
        if (c.record == record && record != 0) {
            return &c;
        }
    }
    return nullptr;
}

resolved* cache_put(uint64_t record)
{
    resolved* c = &g_resolved[g_resolved_next];
    g_resolved_next = (g_resolved_next + 1) % RESOLVED;
    zero_bytes(c, sizeof(resolved));
    c->record = record;
    c->member = 0xFFFF;
    return c;
}

// The actor whose battle record this is: the units of this beat first, then one walk of the actor manager.
uint64_t actor_of_record(game_reader& r, uint64_t record)
{
    for (uint32_t k = 0; k < g_beat_count; ++k) {
        if (g_beat_units[k].record == record) {
            return g_beat_units[k].actor;
        }
    }
    uintptr_t manager = 0;
    uint32_t count = 0;
    if (!r.ptr(r.global(f::ACTOR_MANAGER_PTR), &manager) || manager == 0 || !r.u32(manager + f::MANAGER_COUNT, &count) ||
        count > f::MANAGER_COUNT_MAX) {
        return 0;
    }
    for (uint32_t index = 0; index < count; ++index) {
        uintptr_t entry = 0;
        uintptr_t actor = 0;
        uintptr_t battle = 0;
        if (r.ptr(manager + f::MANAGER_ENTRIES + 8ull * index, &entry) && entry != 0 &&
            r.ptr(entry + f::ENTRY_ACTOR, &actor) && actor != 0 && r.ptr(actor + f::ACTOR_BATTLE, &battle) &&
            battle == record) {
            return actor;
        }
    }
    return 0;
}

bool resolve(game_reader& r, uint64_t record, const hud_snapshot& snap, who* out)
{
    zero_bytes(out, sizeof(who));
    out->member = 0xFFFF;
    if (record == 0) {
        return false;
    }
    if (resolved* stale = cache_find(record)) {
        // A record slot can be reused by another unit within an epoch: the cached actor must still own it.
        uint8_t side_byte = 0;
        uint8_t member = 0;
        bool still = r.u8(record + f::REC_SIDE, &side_byte) && r.u8(record + f::REC_MEMBER, &member);
        if (still && stale->side == SIDE_PARTY) {
            still = side_byte == 0 && member == stale->member;
        } else if (still) {
            const uint64_t actor = live_actor(r, stale->handle);
            uintptr_t battle = 0;
            still = side_byte != 0 && actor != 0 && r.ptr(actor + f::ACTOR_BATTLE, &battle) && battle == record;
        }
        if (!still) {
            stale->record = 0;
        }
    }
    if (const resolved* c = cache_find(record)) {
        out->handle = c->handle;
        out->side = c->side;
        out->seat = c->seat;
        out->member = c->member;
        copy_text(out->name, NAME, c->name);
        // The seat may have changed hands since: re-read it from the snapshot.
        for (uint32_t k = 0; k < snap.member_count && k < SNAP_MEMBERS; ++k) {
            if (snap.members[k].handle == out->handle && out->handle != 0) {
                out->seat = snap.members[k].seat;
            }
        }
        return true;
    }
    uint8_t side_byte = 0;
    uint8_t member = 0;
    if (!r.u8(record + f::REC_SIDE, &side_byte) || !r.u8(record + f::REC_MEMBER, &member)) {
        bump(detail::g_counters.unresolved);
        return false;
    }
    resolved* c = cache_put(record);
    if (side_byte == 0) {
        c->side = SIDE_PARTY;
        c->member = member;
        for (uint32_t k = 0; k < snap.member_count && k < SNAP_MEMBERS; ++k) {
            const hud_unit& u = snap.members[k];
            if (u.member == member && (u.flags & UNIT_ACTOR) != 0) {
                c->handle = u.handle;
                c->seat = u.seat;
            }
        }
        if (c->handle == 0) {
            const uint64_t actor = actor_of_record(r, record);
            uint32_t handle = 0;
            if (actor != 0 && r.u32(actor + f::ACTOR_HANDLE, &handle)) {
                c->handle = handle;
            }
        }
        if (!name_of(2, member, 0, c->name, NAME)) {
            bump(detail::g_counters.unresolved);
        }
    } else {
        c->side = SIDE_FOE;
        const uint64_t actor = actor_of_record(r, record);
        uint32_t handle = 0;
        if (actor != 0 && r.u32(actor + f::ACTOR_HANDLE, &handle)) {
            c->handle = handle;
        }
        foe_name(r, actor, c->name, NAME);
        if (actor == 0) {
            bump(detail::g_counters.unresolved);
        }
    }
    out->handle = c->handle;
    out->side = c->side;
    out->seat = c->seat;
    out->member = c->member;
    copy_text(out->name, NAME, c->name);
    return true;
}

void note_extra(const who& w, uint64_t now)
{
    if (w.handle == 0) {
        return;
    }
    for (uint32_t k = 0; k < g_beat_count; ++k) {
        if (g_beat_units[k].handle == w.handle) {
            return;   // the snapshot carries it
        }
    }
    extra* slot = nullptr;
    uint64_t oldest = ~0ull;
    for (extra& e : g_extras) {
        if (e.handle == w.handle) {
            slot = &e;
            break;
        }
        if (e.handle == 0 && oldest != 0) {
            slot = &e;
            oldest = 0;
        } else if (e.last_ms < oldest) {
            slot = &e;
            oldest = e.last_ms;
        }
    }
    slot->handle = w.handle;
    slot->side = w.side;
    slot->seat = w.seat;
    slot->member = w.member;
    slot->last_ms = now;
    copy_text(slot->name, NAME, w.name);
}

int32_t arg32(const event& e, uint32_t offset)
{
    int32_t v = 0;
    copy_bytes(&v, e.args + offset, sizeof(v));
    return v;
}

uint64_t arg64(const event& e, uint32_t offset)
{
    uint64_t v = 0;
    copy_bytes(&v, e.args + offset, sizeof(v));
    return v;
}

// ---- the chain (a snapshot read behind its verify rows) ----------------------------------------------------------
bool row_stock(game_reader& r, int32_t row)
{
    if (row < 0) {
        return false;
    }
    const patch::site& s = patch::SITES[row];
    uint8_t bytes[64];
    if (s.length > sizeof(bytes) || !r.read(image_base() + s.rva, bytes, s.length)) {
        return false;
    }
    for (uint32_t k = 0; k < s.length; ++k) {
        if (bytes[k] != static_cast<uint8_t>(s.expected[k])) {
            return false;
        }
    }
    return true;
}

chain_read chain_sample(game_reader& r, uint32_t epoch)
{
    chain_read c = {};
    if (g_chain_epoch != epoch || epoch == 0) {
        int32_t reset = -1;
        int32_t shown = -1;
        chain_rows(&reset, &shown);
        g_chain_rows = row_stock(r, reset) && row_stock(r, shown) ? 1 : 0;
        g_chain_epoch = epoch;
    }
    uint64_t pair = 0;   // level (low dword) and count (high dword), stored together by the reset
    uint32_t plus = 0;
    if (g_chain_rows == 0 || !r.u64(r.global(f::CHAIN_LEVEL), &pair) || !r.u32(r.global(f::CHAIN_SHOWN_PLUS), &plus)) {
        bump(detail::g_counters.chain_off);
        return c;
    }
    const int32_t level = static_cast<int32_t>(static_cast<uint32_t>(pair));
    const int32_t count = static_cast<int32_t>(static_cast<uint32_t>(pair >> 32));
    static_assert(f::CHAIN_COUNT == f::CHAIN_LEVEL + 4, "count follows level");
    if (level < 0 || level > f::CHAIN_LEVEL_MAX || count < 0 || count > f::CHAIN_MAX) {
        bump(detail::g_counters.chain_off);
        return c;
    }
    c.known = 1;
    c.level = static_cast<uint8_t>(level);
    c.shown = static_cast<uint16_t>(count + (plus != 0 ? 1 : 0));
    return c;
}

// ---- WEAK: the action's element (the weapon's for Attack) against the target's affinities -------------------------
bool weapon_element(game_reader& r, uint16_t weapon, uint8_t* out)
{
    uint64_t resource = 0;
    uintptr_t header = 0;
    uint32_t count = 0;
    uint16_t stride = 0;
    uint32_t rows = 0;
    if (weapon < f::EQUIP_FIRST || !r.u64(r.global(f::RESOURCE_BASE_PTR), &resource) || resource != 0 ||
        !r.ptr(r.global(f::EQUIP_TABLE_PTR), &header) || header == 0 || !r.u32(header + f::TABLE_COUNT, &count) ||
        !r.u16(header + f::TABLE_STRIDE, &stride) || !r.u32(header + f::TABLE_ROWS, &rows)) {
        return false;
    }
    const uint32_t index = static_cast<uint32_t>(weapon - f::EQUIP_FIRST);
    if (index >= count || stride <= f::EQUIP_ELEMENT) {
        return false;
    }
    const uint32_t row = rows + index * stride;   // formed in 32 bits, as the game does
    return r.u8(row + f::EQUIP_ELEMENT, out);
}

uint8_t weak_of(game_reader& r, const hook_payload& p)
{
    if ((p.known & HK_AFFINITY) == 0 || (p.known & HK_OUTCOME) == 0 || p.outcome != f::OUT_HIT) {
        return 0;
    }
    uint8_t element = 0;
    if (p.action == f::ACTION_ATTACK) {
        if ((p.known & HK_WEAPON) == 0 || !weapon_element(r, p.weapon, &element)) {
            return 0;   // ammunition is not read: an unknown element is never WEAK
        }
    } else if ((p.known & HK_ELEMENT) != 0) {
        element = p.element;
    }
    return element != 0 && (element & p.affinity[0]) != 0 && (element & p.affinity[1]) == 0 &&
                   (element & p.affinity[2]) == 0
               ? 1
               : 0;
}

// ---- the hit assembler -------------------------------------------------------------------------------------------
// A unit named by handle: its live actor's record resolved like a feed record, else the handle alone, named "?".
void who_by_handle(game_reader& r, uint32_t handle, const hud_snapshot& snap, who* out)
{
    zero_bytes(out, sizeof(who));
    out->member = 0xFFFF;
    if (handle == 0) {
        return;
    }
    const uint64_t actor = live_actor(r, handle);
    uintptr_t record = 0;
    if (actor != 0 && r.ptr(actor + f::ACTOR_BATTLE, &record) && record != 0 && resolve(r, record, snap, out) &&
        (out->handle == handle || out->handle == 0)) {
        out->handle = handle;
        return;
    }
    zero_bytes(out, sizeof(who));
    out->member = 0xFFFF;
    out->handle = handle;
    copy_text(out->name, NAME, "?");
}

// A unit the hook copied a record for: that record first (it was read at the hit), else by handle.
void who_of(game_reader& r, uint64_t record, uint32_t handle, const hud_snapshot& snap, who* out)
{
    if (record != 0 && resolve(r, record, snap, out) && (out->handle == handle || out->handle == 0)) {
        out->handle = handle;
        return;
    }
    who_by_handle(r, handle, snap, out);
}

void finish_hit(game_reader& r, const hud_snapshot& snap, hit_build& b)
{
    const bool outcome = b.has_outcome != 0;
    if (!outcome && b.numbers == 0) {
        zero_bytes(&b, sizeof(b));   // a bracket with nothing to say (the reserve path leaves before the outcome)
        return;
    }
    const hook_payload& src = outcome ? b.outcome : b.begin;
    zero_bytes(&g_hit, sizeof(g_hit));
    g_hit.serial = b.serial;
    g_hit.time_ms = b.time_ms;
    g_hit.action = src.action;
    g_hit.outcome = outcome && (src.known & HK_OUTCOME) != 0 ? src.outcome : OUTCOME_UNKNOWN;
    g_hit.flags = outcome ? static_cast<uint8_t>(src.flags & 0x0F) : 0;
    const bool records = outcome && (src.known & HK_RECORDS) != 0;
    who_of(r, records ? src.actor_record : 0, src.actor, snap, &g_hit.caster);
    who_of(r, records ? src.target_record : 0, src.target, snap, &g_hit.target);
    action_name(src.action, g_hit.action_name, ACTION_NAME);
    g_hit.weak = outcome ? weak_of(r, src) : 0;
    g_hit.pinned = b.numbers != 0 ? 1 : 0;
    g_hit.amount_count = b.amount_count;
    for (uint32_t k = 0; k < b.amount_count && k < AMOUNTS_SHOWN; ++k) {
        g_hit.amounts[k] = b.amounts[k];
    }
    g_hit.total = b.total;
    // Both are followed from now on, so the diffs that carry this hit's amount are seen.
    note_extra(g_hit.caster, b.time_ms);
    note_extra(g_hit.target, b.time_ms);
    if (model_hit(&g_model, g_hit) != 0) {
        bump(detail::g_counters.lines);
    }
    zero_bytes(&b, sizeof(b));
}

void hook_event(game_reader& r, const hud_snapshot& snap, const event& e)
{
    hook_payload p;
    copy_bytes(&p, e.args, sizeof(p));
    switch (e.msg) {
    case EV_HIT_BEGIN:
        if (g_build.serial != 0) {
            finish_hit(r, snap, g_build);   // its exit was missed (or lost in the ring)
        }
        zero_bytes(&g_build, sizeof(g_build));
        g_build.serial = p.serial;
        g_build.time_ms = e.time_ms;
        g_build.begin = p;
        break;
    case EV_HIT_OUTCOME:
        if (p.serial != 0 && p.serial == g_build.serial) {
            g_build.outcome = p;
            g_build.has_outcome = 1;
            break;
        }
        // No bracket of its own on the assembler's side (none open on that thread, or its entry was lost).
        zero_bytes(&g_solo, sizeof(g_solo));
        g_solo.time_ms = e.time_ms;
        g_solo.outcome = p;
        g_solo.has_outcome = 1;
        finish_hit(r, snap, g_solo);
        break;
    case EV_HIT_NUMBER: {
        const uint32_t target = g_build.has_outcome != 0 ? g_build.outcome.target : g_build.begin.target;
        // Only HP numbers on this hit's target belong to it (the caster's own HP or MP change is another matter).
        if (g_build.serial != 0 && p.serial == g_build.serial && p.actor == target && target != 0 && p.kind == 0 &&
            (p.known & HK_ACTOR) != 0) {
            if (g_build.amount_count < AMOUNTS_SHOWN) {
                g_build.amounts[g_build.amount_count++] = p.value;
            }
            g_build.total += p.value;
            if (g_build.numbers < 0xFF) {
                ++g_build.numbers;
            }
        }
        break;
    }
    case EV_HIT_END:
        if (g_build.serial != 0 && p.serial == g_build.serial) {
            finish_hit(r, snap, g_build);
        }
        break;
    case EV_KILL:
        zero_bytes(&g_kill, sizeof(g_kill));
        g_kill.time_ms = e.time_ms;
        who_by_handle(r, p.actor, snap, &g_kill.victim);
        g_kill.exp = p.value > 0 ? static_cast<uint32_t>(p.value) : 0;
        g_kill.lp = p.value2 > 0 ? static_cast<uint32_t>(p.value2) : 0;
        g_kill.chain_known = g_chain.known;
        g_kill.chain_level = g_chain.level;
        g_kill.chain = g_chain.shown;
        if (model_kill(&g_model, g_kill) != 0) {
            bump(detail::g_counters.lines);
        }
        break;
    default:
        bump(detail::g_counters.filtered);
        break;
    }
}

void feed(game_reader& r, const hud_snapshot& snap, uint64_t now)
{
    uint32_t lost = 0;
    const uint32_t n = ring_drain(g_events, RING, &lost);
    // After the drain: the chain update runs before the kill pop-up, so every kill drained is already counted.
    g_chain = chain_sample(r, snap.head.epoch);
    for (uint32_t k = 0; k < n; ++k) {
        const event& e = g_events[k];
        bump(detail::g_counters.drained);
        if (e.msg >= EV_HOOK_FIRST && (e.flags & EV_ARGS) != 0) {
            hook_event(r, snap, e);
            continue;
        }
        const int32_t action = arg32(e, f::ARG_ACTION);
        if ((e.flags & EV_ARGS) == 0 || e.msg >= f::MSG_TEMPLATES || action >= f::ACTION_BOUND) {
            bump(detail::g_counters.filtered);   // the emitter's own first test (or nothing to read)
            continue;
        }
        zero_bytes(&g_msg, sizeof(g_msg));
        g_msg.seq = e.stamp / 2;
        g_msg.msg = e.msg;
        g_msg.time_ms = e.time_ms;
        g_msg.action = action >= 0 && action < f::ACTION_LIMIT ? static_cast<uint16_t>(action) : NO_ACTION;
        g_msg.value = arg32(e, f::ARG_VALUE);
        g_msg.status = arg32(e, f::ARG_STATUS);
        g_msg.level = arg32(e, f::ARG_LEVEL);
        g_msg.item = arg32(e, f::ARG_ITEM);
        g_msg.count = arg32(e, f::ARG_COUNT);
        resolve(r, arg64(e, f::ARG_ACTOR_A), snap, &g_msg.a);
        resolve(r, arg64(e, f::ARG_ACTOR_B), snap, &g_msg.b);
        action_name(g_msg.action, g_msg.action_name, ACTION_NAME);
        if (g_msg.action == f::ACTION_ATTACK) {
            copy_text(g_msg.action_name, ACTION_NAME, "Attack");
        }
        if (g_msg.item > 0 && g_msg.item <= 0xFFFF &&
            !target_info::item_name_now(static_cast<uint16_t>(g_msg.item), g_msg.item_name, ACTION_NAME)) {
            g_msg.item_name[0] = '\0';
        }
        note_extra(g_msg.a, e.time_ms);
        note_extra(g_msg.b, e.time_ms);
        if (model_message(&g_model, g_msg) != 0) {
            bump(detail::g_counters.lines);
        }
    }
    if (g_build.serial != 0 && (now < g_build.time_ms || now - g_build.time_ms > BRACKET_MS)) {
        finish_hit(r, snap, g_build);   // its exit never came
    }
}

// ---- units ------------------------------------------------------------------------------------------------------------
void unit_actor_state(game_reader& r, uint64_t actor, unit_obs* o)
{
    uint32_t target = 0;
    uint16_t action = NO_ACTION;
    if (actor != 0 && r.u32(actor + f::ACTOR_TARGET, &target) && r.u16(actor + f::ACTOR_ACTION, &action)) {
        o->target = target;
        o->action_now = action;
        o->flags |= OBS_TARGET;
        action_name(action, o->action_name, ACTION_NAME);
    }
}

uint32_t gather(game_reader& r, const hud_snapshot& snap, uint64_t now)
{
    uint32_t n = 0;
    g_beat_count = 0;
    // Party rows, then targets that are not party members.
    for (uint32_t k = 0; k < snap.member_count && k < SNAP_MEMBERS; ++k) {
        const hud_unit& u = snap.members[k];
        if ((u.flags & UNIT_PRESENT) == 0 || (u.flags & UNIT_ACTOR) == 0 || u.handle == 0) {
            continue;
        }
        unit_obs& o = g_obs[n++];
        zero_bytes(&o, sizeof(o));
        o.handle = u.handle;
        o.side = SIDE_PARTY;
        o.seat = u.seat;
        o.flags = OBS_PRESENT;
        o.hp = u.hp;
        o.hp_max = u.hp_max;
        o.status = u.status_timed | u.status_lasting;
        o.member = u.member;
        o.action_now = NO_ACTION;
        name_of(2, u.member, 0, o.name, NAME);
        const uint64_t actor = live_actor(r, u.handle);
        uintptr_t record = 0;
        if (actor != 0) {
            r.ptr(actor + f::ACTOR_BATTLE, &record);
        }
        g_beat_units[g_beat_count++] = {u.handle, actor, record};
        unit_actor_state(r, actor, &o);
    }
    for (uint32_t k = 0; k < snap.target_count && k < SNAP_TARGETS; ++k) {
        const hud_unit& u = snap.targets[k];
        if ((u.flags & UNIT_PRESENT) == 0 || u.handle == 0 || (u.flags & UNIT_PARTY) != 0) {
            continue;
        }
        bool known = false;
        for (uint32_t j = 0; j < g_beat_count; ++j) {
            known = known || g_beat_units[j].handle == u.handle;
        }
        if (known) {
            continue;
        }
        unit_obs& o = g_obs[n++];
        zero_bytes(&o, sizeof(o));
        o.handle = u.handle;
        o.side = SIDE_FOE;
        o.flags = OBS_PRESENT;
        o.weakness = u.weakness;
        o.hp = u.hp;
        o.hp_max = u.hp_max;
        o.status = u.status_timed | u.status_lasting;
        o.member = 0xFFFF;
        o.action_now = NO_ACTION;
        const uint64_t actor = live_actor(r, u.handle);
        uintptr_t record = 0;
        if (actor != 0) {
            r.ptr(actor + f::ACTOR_BATTLE, &record);
        }
        g_beat_units[g_beat_count++] = {u.handle, actor, record};
        foe_name(r, actor, o.name, NAME);
        unit_actor_state(r, actor, &o);
    }
    // Actors the feed named that the snapshot does not carry: read the same way, while they exist.
    for (extra& e : g_extras) {
        if (e.handle == 0) {
            continue;
        }
        bool known = false;
        for (uint32_t j = 0; j < g_beat_count; ++j) {
            known = known || g_beat_units[j].handle == e.handle;
        }
        const uint64_t actor = known ? 0 : live_actor(r, e.handle);
        if (known || actor == 0 || now > e.last_ms + EXTRA_HOLD_MS) {
            zero_bytes(&e, sizeof(e));
            continue;
        }
        uintptr_t record = 0;
        uint32_t hp = 0;
        uint32_t hp_max = 0;
        uint32_t timed = 0;
        uint32_t lasting = 0;
        uint8_t weakness = 0;
        if (!r.ptr(actor + f::ACTOR_BATTLE, &record) || record == 0 || !r.u32(record + f::REC_HP, &hp) ||
            !r.u32(record + f::REC_HP_MAX, &hp_max) || !r.u32(record + f::REC_STATUS_TIMED, &timed) ||
            !r.u32(record + f::REC_STATUS_LASTING, &lasting) || !r.u8(record + f::REC_WEAKNESS, &weakness)) {
            continue;
        }
        unit_obs& o = g_obs[n++];
        zero_bytes(&o, sizeof(o));
        o.handle = e.handle;
        o.side = e.side;
        o.seat = e.seat;
        o.flags = OBS_PRESENT;
        o.weakness = weakness;
        o.hp = hp;
        o.hp_max = hp_max;
        o.status = timed | lasting;
        o.member = e.member;
        o.action_now = NO_ACTION;
        copy_text(o.name, NAME, e.name);
        unit_actor_state(r, actor, &o);
    }
    return n;
}

// ---- settings and colours -----------------------------------------------------------------------------------------------
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

// Any seat's press of the expand control toggles the expanded view: the control seen held now and not held at the
// previous beat (a press shorter than one beat can be missed; a held control toggles once).
void expand_control(const settings& s)
{
    if (s.expand_control == 0 || s.expand_control > ILC_PAD_CONTROL_COUNT || !input::snapshot_read(&g_input)) {
        zero_bytes(g_last_held, sizeof(g_last_held));
        return;
    }
    const uint32_t bit = 1u << (s.expand_control - 1);
    bool toggle = false;
    for (uint32_t k = 0; k < 4; ++k) {
        const ilc_seat_input_frame& fr = g_input.frames[k];
        const uint32_t held = fr.source_generation != 0 ? fr.held & ~fr.absorbed : 0;
        toggle = toggle || ((held & bit) != 0 && (g_last_held[k] & bit) == 0);
        g_last_held[k] = held;
    }
    if (toggle) {
        toggle_expanded();
        bump(detail::g_counters.toggles);
    }
}

void reset_state()
{
    model_reset(&g_model);
    zero_bytes(&g_build, sizeof(g_build));
    zero_bytes(&g_solo, sizeof(g_solo));
    zero_bytes(&g_chain, sizeof(g_chain));
    g_chain_epoch = 0;
    g_chain_rows = 0;
    zero_bytes(g_resolved, sizeof(g_resolved));
    g_resolved_next = 0;
    zero_bytes(g_extras, sizeof(g_extras));
    g_beat_count = 0;
}

void stop_publishing()
{
    if (g_published != 0) {
        detail::withdraw();
        g_published = 0;
    }
}

// The beat's work on a snapshot; false (nothing published) without a world.
bool beat(const hud_snapshot& snap, uint64_t now, bool have_snapshot, view* out)
{
    const settings s = current();
    if (s.enabled == 0) {
        uint32_t lost = 0;
        ring_drain(g_events, RING, &lost);   // discard: nothing is shown
        reset_state();
        g_epoch_model = 0;
        stop_publishing();
        return false;
    }
    if (!have_snapshot || (snap.head.flags & SNAP_WORLD) == 0 || snap.head.epoch == 0) {
        uint32_t lost = 0;
        ring_drain(g_events, RING, &lost);
        stop_publishing();
        return false;
    }
    if (snap.head.epoch != g_epoch_model) {
        reset_state();
        g_epoch_model = snap.head.epoch;
    }
    refresh_colours();
    game_reader r(image_base());
    const uint32_t n = gather(r, snap, now);   // before the feed: the beat's records resolve the feed's actors
    feed(r, snap, now);
    model_units(&g_model, now, g_obs, n, nullptr, nullptr);
    expand_control(s);
    const settings now_s = current();
    zero_bytes(&g_next, sizeof(g_next));
    g_next.head.epoch = snap.head.epoch;
    const uint8_t rows = hit_rows();
    g_next.head.flags = (now_s.expanded != 0 ? VIEW_EXPANDED : 0u) |
                        (__atomic_load_n(&detail::g_attached, __ATOMIC_ACQUIRE) != 0 ? VIEW_FEED : 0u) |
                        ((rows & HROW_OUTCOME) != 0 ? VIEW_HITS : 0u) | ((rows & HROW_NUMBER) != 0 ? VIEW_PINNED : 0u) |
                        ((rows & HROW_KILL) != 0 ? VIEW_KILLS : 0u) | (g_chain.known != 0 ? VIEW_CHAIN : 0u);
    g_next.head.generation = ++g_generation;
    g_next.built_ms = now;
    g_next.filter = now_s.filter < FILTER_COUNT ? now_s.filter : static_cast<uint8_t>(FILTER_ALL);
    g_next.filter_seat = now_s.filter_seat;
    for (uint32_t k = 0; k < 4; ++k) {
        g_next.seat_colour[k] = pack(SEAT_RGB[g_seat_colour[k]]);
    }
    g_next.line_count = static_cast<uint8_t>(model_lines(g_model, g_next.lines, HISTORY));
    if (out != nullptr) {
        copy_bytes(out, &g_next, sizeof(view));
    }
    detail::publish(g_next);
    g_published = 1;
    return true;
}

}  // namespace

// ---- settings ----------------------------------------------------------------------------------------------------------------
settings defaults()
{
    settings s = {};
    s.enabled = 1;
    s.expanded = 0;
    s.expand_control = 0;   // none until the Battle HUD page binds one
    s.filter = FILTER_ALL;
    s.filter_seat = 1;
    return s;
}

void configure(const settings& s)
{
    settings c = s;
    c.enabled = c.enabled != 0 ? 1 : 0;
    c.expanded = c.expanded != 0 ? 1 : 0;
    if (c.expand_control > ILC_PAD_CONTROL_COUNT) {
        c.expand_control = 0;
    }
    if (c.filter >= FILTER_COUNT) {
        c.filter = FILTER_ALL;
    }
    if (c.filter_seat < 1 || c.filter_seat > 4) {
        c.filter_seat = 1;
    }
    __atomic_store_n(&g_settings, pack_settings(c), __ATOMIC_RELEASE);
    __atomic_store_n(&detail::g_enabled, static_cast<uint32_t>(c.enabled), __ATOMIC_RELEASE);
}

settings current()
{
    return unpack_settings(__atomic_load_n(&g_settings, __ATOMIC_ACQUIRE));
}

void toggle_expanded()
{
    settings s = current();
    s.expanded = s.expanded != 0 ? 0 : 1;
    configure(s);
}

// ---- the module side -----------------------------------------------------------------------------------------------------
bool attach(uintptr_t image_base_)
{
    g_base = image_base_;
    g_started = 1;
    const uint32_t row = static_cast<uint32_t>(feed_row());
    const bool ok = patch::set_handler(row, lg01) && patch::set_site_armed(row, true);
    if (!ok) {
        patch::set_site_armed(row, false);
    }
    __atomic_store_n(&detail::g_attached, ok ? 1u : 0u, __ATOMIC_RELEASE);
    // The per-hit feed and the kill reward: each row acts only if the patch layer hooked it (an OPTIONAL row another
    // mod rewrote, or one whose gate failed, stays stock); amounts also need both ends of the bracket.
    static const struct {
        uint8_t bit;
        patch::handler_fn handler;
    } HIT_HANDLERS[] = {{HROW_BEGIN, hr01}, {HROW_OUTCOME, hr02}, {HROW_NUMBER, hr03}, {HROW_END, hr05},
                        {HROW_KILL, kr01}};
    uint8_t rows = 0;
    for (const auto& h : HIT_HANDLERS) {
        const int32_t r = hit_row_index(h.bit);
        if (r < 0) {
            continue;
        }
        const uint32_t hr = static_cast<uint32_t>(r);
        if (patch::trampoline(hr) != nullptr && patch::set_handler(hr, h.handler) && patch::set_site_armed(hr, true)) {
            rows = static_cast<uint8_t>(rows | h.bit);
        } else {
            patch::set_site_armed(hr, false);
        }
    }
    set_hit_rows(rows);
    return ok;
}

void step(uint64_t now_ms)
{
    if (g_started == 0) {
        return;
    }
    bool got = false;
    for (uint32_t attempt = 0; attempt < 2 && !got; ++attempt) {
        got = snapshot::read(g_snap);
    }
    beat(g_snap, now_ms, got, nullptr);
}

void stop()
{
    stop_publishing();
    __atomic_store_n(&detail::g_attached, 0u, __ATOMIC_RELEASE);
    set_hit_rows(0);
    reset_state();
    g_started = 0;
}

status_view status()
{
    status_view s = {};
    const settings c = current();
    s.attached = g_started != 0 ? 1 : 0;
    s.feed = __atomic_load_n(&detail::g_attached, __ATOMIC_ACQUIRE) != 0 ? 1 : 0;
    s.enabled = c.enabled;
    s.expanded = c.expanded;
    s.hit_rows = hit_rows();
    s.chain = g_chain.known;
    return s;
}

// ---- host tests ---------------------------------------------------------------------------------------------------------
void test_reset()
{
    detail::withdraw();
    g_published = 0;
    ring_test_reset();
    reset_state();
    g_epoch_model = 0;
    g_generation = 0;
    g_base = 0;
    g_beat = 1;   // the seat colours keep their defaults until a test asks
    g_started = 0;
    g_names_only = 0;
    zero_bytes(g_last_held, sizeof(g_last_held));
    zero_bytes(const_cast<counters*>(&detail::g_counters), sizeof(counters));
    __atomic_store_n(&detail::g_attached, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_settings, uint64_t{0}, __ATOMIC_RELEASE);
    __atomic_store_n(&detail::g_enabled, 1u, __ATOMIC_RELEASE);
}

void test_set_image_base(uintptr_t image_base_) { g_base = image_base_; }

void test_set_attached(bool on)
{
    __atomic_store_n(&detail::g_attached, on ? 1u : 0u, __ATOMIC_RELEASE);
}

bool test_step(const void* snapshot, uint64_t now_ms, view* out)
{
    const auto* s = static_cast<const hud_snapshot*>(snapshot);
    if (s != nullptr) {
        copy_bytes(&g_snap, s, sizeof(hud_snapshot));
    }
    return beat(g_snap, now_ms, s != nullptr, out);
}

void test_publish(const view& v) { detail::publish(v); }

model* test_model() { return &g_model; }

bool test_resolve(uint64_t record, who* out)
{
    game_reader r(image_base());
    return resolve(r, record, g_snap, out);
}

void test_set_names_only(bool on) { g_names_only = on ? 1u : 0u; }

void test_set_hit_rows(uint8_t rows) { set_hit_rows(rows); }

bool test_chain(uint16_t* shown, uint8_t* level)
{
    game_reader r(image_base());
    g_chain_epoch = 0;   // compare the verify rows again
    const chain_read c = chain_sample(r, 1);
    g_chain_epoch = 0;
    *shown = c.shown;
    *level = c.level;
    return c.known != 0;
}

}  // namespace ilc::hud::battle_log
