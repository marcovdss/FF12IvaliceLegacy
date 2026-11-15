// Ivalice Legacy co-op module: a guest's battle-menu catalog (pure).
//
// The catalog is the tree of pages a guest's menu shows for the character it controls:
//   Root:   Attack | Magicks & Technicks | Mist (or Dismiss while this character's Esper is out) | Gambits | Items
//   Magicks & Technicks: White, Black, Time, Green, Arcane Magicks (one page each) and Technicks
//   Mist:   Quickening, Summon
//   Items:  every item held, in the player's own item order
// built from the game's loaded tables (tables.h) and what the game says right now: the shared inventory and
// ownership bits, and the character's own licences, MP, augments and statuses. Entries carry action ids, never text:
// the presentation names them from the game's own text, so Legacy's renamed and added actions read correctly.
//
// The rules (Ivalice Together's guest menu, rewritten for Legacy's module):
//  * Magicks: owned (party ownership bit) and licensed (the character's live magick licence summary, or the action
//    needs no licence). Technicks: owned and licensed by an acquired licence node, or needing none. These rules hold
//    for party ids 0..6; the story guests the game checks against a fixed list (7..0x19, 0x1B..0x27) use that list
//    instead. What is not available is not listed; a school with nothing in it is a disabled row.
//  * Quickenings: the character's own three, as many as the Mist ownership bits or the acquired Quickening licences
//    allow. Espers: Mist bit 18 + n and licence node 18 + n. Mist is offered when it has rows or the menu flags open it.
//  * Items: held (owned = counter & 0x3FF), in the player's saved order when it is complete, else by sort key. Each
//    shows the available count (owned - counter >> 10); an item with none available is listed but greyed, so a
//    second seat cannot pick the last one another seat has already queued.
//  * Greys: what the game's admission check would refuse on cost (MP, Mist charges, gil; menu/cost.h); statuses
//    KO, Stone, Stop, Sleep, Confuse, Disable and X-Zone grey everything; Berserk leaves only Attack; Silence greys
//    the magick schools; the area's command seals grey their own family (Attack, Magicks, Technicks, Items).
#pragma once

#include <stdint.h>

#include "menu/tables.h"

namespace ilc::menu {

// ---- what the game says right now ---------------------------------------------------------------------------------
struct shared_state {
    uint32_t menu_flags;
    uint8_t magick_owned[12];
    uint8_t technick_owned[4];
    uint8_t mist_owned[4];
    uint8_t seals;            // the area's command seals
    uint8_t order_known;      // 1 = item_order holds the player's saved order
    uint8_t order_count;
    uint8_t reserved;
    uint16_t item_counts[ITEM_CAP];  // raw counters: owned = value & 0x3FF, available = owned - (value >> 10)
    uint16_t item_order[ITEM_CAP];   // item indices
    int32_t gil;              // the party's gil (the admission compares it signed)
};
static_assert(sizeof(shared_state) == 288, "shared_state layout (host tests build it)");

struct seat_state {
    uint16_t member;          // party id of the controlled character
    uint8_t record_state;     // member record +0: bit 2 = gambits on
    uint8_t side;             // member record +5
    int16_t mp;
    uint8_t charges;          // member record +0x4E
    uint8_t esper_owner;      // 1 = an Esper is out and this character summoned it
    uint32_t status;          // timed | lasting status bits
    uint8_t licences[46];
    uint8_t magick_licences[23];
    uint8_t augments[12];     // timed | lasting augment bits
    uint8_t magick;           // member record +0x2B: Magick (Legacy's MP economy scales with it)
    uint8_t reserved[2];
    int32_t gil_reserved;     // actor +0xE58: gil this actor already put aside for its queued action
};
static_assert(sizeof(seat_state) == 100, "seat_state layout (host tests build it)");

// ---- the tree ----------------------------------------------------------------------------------------------------
enum page_id : uint8_t {
    PG_ROOT = 0,
    PG_MAGICKS = 1,
    PG_WHITE = 2,             // PG_WHITE + school (0..4)
    PG_BLACK = 3,
    PG_TIME = 4,
    PG_GREEN = 5,
    PG_ARCANE = 6,
    PG_TECHNICKS = 7,
    PG_MIST = 8,
    PG_QUICKENINGS = 9,
    PG_SUMMONS = 10,
    PG_ITEMS = 11,
    PG_COUNT = 12,
    PG_NONE = 0xFF,
};
constexpr uint32_t SCHOOLS = 5;

enum entry_kind : uint8_t {
    EK_LABEL = 0,             // "No actions available": never selectable
    EK_PAGE = 1,
    EK_ACTION = 2,
    EK_TOGGLE = 3,            // Gambits
};

enum entry_flag : uint8_t {
    EF_ENABLED = 0x01,
    EF_SELF_ONLY = 0x02,      // no target picker: the character itself
    EF_REVIVE = 0x04,         // a revival (the picker offers KO allies)
    EF_PARTY_FIRST = 0x08,    // the target groups are offered party first (else foes first)
    EF_MP_SHORT = 0x10,       // greyed: not enough MP
    EF_SEALED = 0x20,         // greyed: a status or a command seal
    EF_COST_UNKNOWN = 0x40,   // the effective cost could not be computed (not greyed for MP)
    EF_HELD_BACK = 0x80,      // greyed: every unit of this item is held back (available 0)
};

// More about an entry's price (entry.cost_flags).
enum cost_flag : uint8_t {
    CF_SURCHARGE = 0x01,      // the shown MP includes Legacy's surcharge (more than the game's admission asks)
    CF_GIL = 0x02,            // the game takes gil instead of MP (Offering)
    CF_CHARGES = 0x04,        // paid with Mist charges: `cost` is charges, not MP
    CF_GIL_SHORT = 0x08,      // greyed: not enough gil
    CF_CHARGES_SHORT = 0x10,  // greyed: not enough Mist charges
};

enum target_group : uint8_t {
    TG_FOES = 0,
    TG_NEUTRALS = 1,
    TG_PARTY = 2,
    TG_RESERVE = 3,
    TG_COUNT = 4,
};

// What a root or category row is (the entry's role); actions carry ROLE_ACTION.
enum entry_role : uint8_t {
    ROLE_ACTION = 0,
    ROLE_ATTACK = 1,
    ROLE_MAGICKS = 2,
    ROLE_MIST = 3,
    ROLE_DISMISS = 4,
    ROLE_GAMBITS = 5,
    ROLE_ITEMS = 6,
    ROLE_SCHOOL = 7,
    ROLE_TECHNICKS = 8,
    ROLE_QUICKENING = 9,
    ROLE_SUMMON = 10,
    ROLE_EMPTY = 11,
};

struct entry {
    uint8_t kind;             // entry_kind
    uint8_t flags;            // entry_flag
    uint8_t page;             // EK_PAGE: the page it opens; else PG_NONE
    uint8_t groups;           // target groups (bit = target_group)
    uint16_t action;          // EK_ACTION: action id
    uint16_t display;         // the action whose name/help to show (items), = action otherwise
    uint16_t quantity;        // items: available (owned minus what is held back)
    int16_t cost;             // MP the action takes (the game's price plus Legacy's surcharge), Mist charges for
                              // kinds 5/6; -1 unknown
    uint8_t default_group;    // target_group
    uint8_t area;             // area radius (0 for Quickenings)
    uint8_t role;             // entry_role
    uint8_t cost_flags;       // cost_flag
};
static_assert(sizeof(entry) == 16, "entry layout (host tests read it)");

constexpr uint32_t ENTRY_CAP = 256;

struct page_slot {
    uint16_t first;
    uint16_t count;
    uint8_t present;
    uint8_t reserved[3];
};
static_assert(sizeof(page_slot) == 8, "page_slot layout");

struct catalog {
    uint32_t valid;           // 1 = a complete tree
    uint16_t member;
    uint16_t used;
    uint64_t signature;       // inputs that built it: tables, shared and seat state
    page_slot pages[PG_COUNT];
    entry entries[ENTRY_CAP];
};

enum build_status : int32_t {
    BUILD_OK = 0,
    BUILD_NO_TABLES = -1,     // the tables are not complete
    BUILD_NO_MEMBER = -2,     // not a character with a menu (party ids 0..6 and the fixed-list guests)
    BUILD_NO_FIXED = -3,      // a fixed-list guest, and the party-member table is unknown
    BUILD_FULL = -4,          // more entries than the tree holds (a foreign table)
    BUILD_INVALID = -5,       // the tree failed the model's own check (never expected from build_catalog)
};

// ---- rules -------------------------------------------------------------------------------------------------------
// Costs and the admission's cost checks: menu/cost.h. Every action entry is priced and greyed there.

// The signature of everything a catalog is built from (Legacy's economy parameters in force included).
uint64_t catalog_signature(const catalog_tables& t, const shared_state& sh, const seat_state& s);

// Build the tree. On failure `out` is left invalid.
int32_t build_catalog(const catalog_tables& t, const shared_state& sh, const seat_state& s, catalog* out);

// Read helpers.
inline const entry* page_entries(const catalog& c, uint32_t page)
{
    return page < PG_COUNT && c.pages[page].present ? &c.entries[c.pages[page].first] : nullptr;
}
inline uint32_t page_count(const catalog& c, uint32_t page)
{
    return page < PG_COUNT && c.pages[page].present ? c.pages[page].count : 0;
}

}  // namespace ilc::menu
