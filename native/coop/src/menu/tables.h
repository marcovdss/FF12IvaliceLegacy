// Ivalice Legacy co-op module: the game's data tables a battle menu is built from.
//
// The catalog never ships its own copy of the game's data. It reads the tables the game itself loaded, which are
// Legacy's whenever Legacy replaced them (renamed, re-costed or re-targeted actions, its licence boards, its items),
// so a guest's menu lists exactly what Player 1's would. Rows are parsed into a compact copy once per load; the page
// builder then works on that copy only.
//
// One parser serves two front ends: the live reader (rows in game memory, located through the table globals) and
// the host tests (rows in a section file, as the External File Loader would load it).
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace ilc::menu {

constexpr uint32_t ACTION_CAP = 1024;
constexpr uint32_t LICENCE_CAP = 368;    // the member record has 46 bytes of licence bits
constexpr uint32_t ITEM_CAP = 64;        // the inventory has 64 counters
constexpr uint32_t MAGICK_CAP = 96;      // 12 bytes of ownership bits
constexpr uint32_t TECHNICK_CAP = 32;    // 4 bytes of ownership bits
constexpr uint32_t MEMBER_CAP = 40;      // party ids 0..0x27; the story guests among them have fixed actions
constexpr uint32_t FIXED_SLOTS = 10;
constexpr uint32_t LICENCE_ACTIONS = 8;
constexpr uint32_t MAX_STRIDE = 1024;  // the largest row any table may have (the live reader copies rows in chunks)

// One row of each table, as far as a menu needs it.
struct action_rule {
    uint8_t present;      // 1 = the table has this id
    uint8_t cost;         // base cost
    uint8_t area;         // area radius
    uint8_t targeting;    // self / allies / foes / initial target
    uint8_t kind;         // menu kind
    uint8_t reserved;
    uint16_t rules;       // school, KO/stone allies, no licence needed
    uint16_t target_flags;
    uint16_t reserved2;
};
static_assert(sizeof(action_rule) == 12, "action_rule layout (host tests read it)");

struct content_row {
    uint16_t action;      // the action it uses
    uint16_t display;     // the action whose name it shows (items; = action otherwise)
    uint8_t sort;
    uint8_t reserved[3];
};
static_assert(sizeof(content_row) == 8, "content_row layout");

struct licence_node {
    uint8_t type;
    uint8_t count;        // listed actions
    uint16_t action[LICENCE_ACTIONS];
};
static_assert(sizeof(licence_node) == 18, "licence_node layout");

enum table_status : int32_t {
    TABLES_OK = 0,
    TABLES_MISSING = -1,      // a table global is unknown or null
    TABLES_UNREADABLE = -2,   // a read failed
    TABLES_BAD_LAYOUT = -3,   // a count or row size outside what the menu can use
    TABLES_MOVED = -4,        // a table global changed while it was read
    TABLES_BAD_BASE = -5,     // the resource base is not 0, so rows are not where the game reads them
};

enum table_id : uint32_t {
    T_ACTIONS = 0,
    T_LICENCES = 1,
    T_MEMBERS = 2,
    T_ITEMS = 3,
    T_MAGICKS = 4,
    T_TECHNICKS = 5,
    T_AUGMENTS = 6,
    T_COUNT = 7,
};

struct catalog_tables {
    uint32_t status;              // the last finish_tables result
    uint32_t action_count;
    uint32_t licence_count;
    uint32_t item_count;
    uint32_t magick_count;
    uint32_t technick_count;
    uint32_t fixed_known;         // 1 = the party-member table was read
    uint32_t channeling_known;    // 1 = the augment values below were read
    uint16_t channeling[3];       // values of augments 91, 92, 93
    uint16_t ready;               // 1 = complete (finish_tables returned TABLES_OK)
    int32_t optional_status;      // the live reader: the first error of an optional table (members, augments),
                                  // which leaves the catalog usable (story guests or Channeling costs unknown)
    uint32_t reserved;
    uint64_t signature;           // FNV-1a over every parsed field: a new value means new tables
    action_rule actions[ACTION_CAP];
    licence_node licences[LICENCE_CAP];
    content_row items[ITEM_CAP];
    content_row magicks[MAGICK_CAP];
    content_row technicks[TECHNICK_CAP];
    uint16_t fixed[MEMBER_CAP][FIXED_SLOTS];   // content ids per party id (only fixed-list guests use them)
};

// A table as found: rows, row count, row size. Rows are read through `rows`, which the front end has checked.
struct table_view {
    const uint8_t* rows;
    uint32_t count;
    uint32_t stride;
};

// A section file (an ST2E table: magic, u32 count at +4, u16 row size at +8, u32 first-row offset at +12).
bool view_section(const uint8_t* section, size_t size, table_view* out);

// Parse one table into `t` (the others are left as they are). false = the layout is not one the menu can use.
bool parse_table(catalog_tables* t, table_id which, const table_view& v);
// The same in pieces, for rows read a chunk at a time: begin (checks the shape, clears that table), rows
// [first, first + n), end (records the count).
bool begin_table(catalog_tables* t, table_id which, uint32_t count, uint32_t stride);
void parse_rows(catalog_tables* t, table_id which, uint32_t first, const uint8_t* rows, uint32_t n, uint32_t stride);
void end_table(catalog_tables* t, table_id which, uint32_t count);

// Mark the tables complete: every required table parsed, signature computed. Returns TABLES_OK or a refusal.
int32_t finish_tables(catalog_tables* t, uint32_t parsed_mask);

// The action rule for an id, or nullptr.
const action_rule* rule_of(const catalog_tables& t, uint32_t action);

}  // namespace ilc::menu
