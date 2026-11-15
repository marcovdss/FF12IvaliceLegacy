// Ivalice Legacy co-op module: game facts behind the expanded Target Info (FFXII_TZA.exe 1.0.4.0, VAs at the pinned
// base 0x120000). Read only: nothing here is ever written, except the two argument registers of the TI-01 call
// (target_info.cpp), which turn the native window's open request into its own close request.
#pragma once

#include <stdint.h>

namespace ilc::hud::tifacts {

constexpr uint64_t PINNED_BASE = 0x120000;

// ---- the native TARGET INFO window ------------------------------------------------------------------------------
// 0x2BE1A0 (reached from 0x28FE40 / 0x28FEA0) calls the window's set routine 0x2BE300(owner = UI + 0x8FA0 + 0xAC0,
// edx = mode, r8d = id, r9d = handle) at TI-01, the only caller of 0x2BE300. 0x2BE1A0 itself returns before this
// call when r8d == 0 and r9d < 0, so every call through TI-01 is an open or update request. Inside 0x2BE300,
// r9d < 0 together with r8d == 0 is the close request: it clears owner +0x570 / +0x57E, runs the same resets as an
// open (0x290990, 0x35C070(0), 0x3C53E0(0)) and closes any open window through 0x2BEA40, without creating one.
// Owner +0x578 (the target the creator 0x2C0F30 reads) and +0x57E are read only by 0x2BE290, 0x2BE300 and 0x2C0F30.
constexpr uint64_t TI_OPEN_CALL = 0x2BE20E;     // VA: TI-01, call 0x2BE300 (documentation; the row carries it)
constexpr uint64_t TI_SET = 0x2BE300;           // VA: the set routine (documentation)
constexpr uint32_t CLOSE_ID = 0;                // r8d of the close request
constexpr uint32_t CLOSE_HANDLE = 0xFFFFFFFFu;  // NOTADDR: r9d of the close request (any negative value)

// ---- actors ---------------------------------------------------------------------------------------------------------
constexpr uint32_t ACTOR_UNIT = 0x10;           // pointer to the unit block
constexpr uint32_t UNIT_NAME = 0xF8;            // pointer to the name text (game encoding, NUL-terminated)
constexpr uint32_t ACTOR_BATTLE = 0x698;        // pointer to the battle record
constexpr uint32_t ACTOR_EXT = 0x6A0;           // pointer to the extended battle block
constexpr uint32_t EXT_STEAL_LEFT = 0x14;       // u16: bit per steal slot still available (clear = already stolen)
constexpr uint32_t ACTOR_LOOT = 0xE60;          // pointer to the loot record (the area's unit record)
constexpr uint32_t ACTOR_CLASS = 0xE68;         // pointer to the class record
constexpr uint32_t CLASS_BESTIARY = 0x52;       // u16: bestiary index (0xFFFF none)
constexpr uint32_t REC_STATUS_BLOCK = 0x38;     // u32: statuses the status writer 0x30E360 refuses to add (op 3)

// The loot record (0x50 bytes read): u16 item ids, 0xFFFF = empty; 0xE000..0xEFFF = gil.
constexpr uint32_t LOOT_SIZE = 0x50;
constexpr uint32_t LOOT_DROPS = 0x28;           // 5 slots
constexpr uint32_t LOOT_STEALS = 0x32;          // 3 slots
constexpr uint32_t LOOT_POACHES = 0x44;         // 2 slots
constexpr uint32_t LOOT_SPECIAL_0 = 0x4A;       // Monograph drop
constexpr uint32_t LOOT_SPECIAL_1 = 0x4E;       // Canopic Jar drop
constexpr uint16_t NO_ITEM = 0xFFFF;
constexpr uint16_t GIL_FIRST = 0xE000;
constexpr uint16_t GIL_LAST = 0xEFFF;

// ---- Anti-Libra ---------------------------------------------------------------------------------------------------
// The native TI hides level and numbers of a foe with the Anti-Libra augment through `test byte [rdi+0x111],2` at
// CD-01. A mod that patches it out (Legacy ships TIBB's noAntiLibra) makes Anti-Libra read absent; we follow the
// bytes found there.
constexpr uint64_t ANTI_LIBRA_TEST = 0x2BFFAA;  // VA: CD-01
constexpr uint8_t ANTI_LIBRA_STOCK[7] = {0xF6, 0x87, 0x11, 0x01, 0x00, 0x00, 0x02};

// ---- Hunter's Insight (a separate Lua mod): the item knowledge it keeps -------------------------------------------
// HI replaces the call at CD-02 with `jmp` into its own code. Its save block: u32 count (0x200), then per bestiary
// index 4 bytes: page 0 steals, 1 drops, 2 Monograph/Canopic, 3 poaches, bit = slot (1 = obtained once). Its
// settings: u32 cycle frames, then u32 alwaysShowItems. Both addresses are read out of HI's own code, which its jump
// leads to: the save block is the immediate HI loads right before it bounds-checks a bestiary index against the
// block's count; the settings are the RIP-relative operand of the zero test that opens HI's page-flag reader
// (target_info_sample.cpp, find_hi).
constexpr uint64_t HI_HOOK = 0x2C0ACF;          // VA: CD-02
constexpr uint8_t HI_HOOK_STOCK[5] = {0xE8, 0x8C, 0x58, 0xFD, 0xFF};
constexpr uint32_t HI_CAVE_SCAN = 0x1800;       // NOTADDR: bytes of HI's code scanned from its jump target
constexpr uint32_t HI_SAVE_COUNT = 0x200;       // NOTADDR
constexpr uint32_t HI_PAGE_STEALS = 0;
constexpr uint32_t HI_PAGE_DROPS = 1;
constexpr uint32_t HI_PAGE_SPECIAL = 2;
constexpr uint32_t HI_PAGE_POACHES = 3;

// ---- the game's name text (battle pack section 61) and the item tables -----------------------------------------
// The battle-pack loader 0x236C30 registers each section: st2e tables through 0x3845F0 (pointer global; the table's
// first-row offset at +0x0C becomes an absolute address), section 61 through 0x3846F0(global, section, depth 1),
// which stores a u32 (section - resource base) and rewrites the directory in place: section +4 + 4 * g = address of
// group g (g < count, u16 count at +0; entry `count` = the section end); in group g, +4 + 4 * e = address of string e
// (u16 count at +0; entry `count` = the group end). A string's text starts 2 bytes in. Addresses are absolute only
// while the resource base is 0 (the menu tables need the same).
constexpr uint64_t RESOURCE_BASE_PTR = 0x1F83530;  // VA: u64, 0 in practice
constexpr uint64_t NAME_TEXT = 0x2EBF170;          // VA: u32, section 61 (0x236FD6)
constexpr uint32_t TEXT_GROUPS_MAX = 64;
constexpr uint32_t TEXT_STRINGS_MAX = 0x800;       // NOTADDR: entry bits of a text link
constexpr uint32_t TEXT_PREFIX = 2;
constexpr uint32_t TABLE_COUNT = 0x04;             // st2e: u32 rows
constexpr uint32_t TABLE_STRIDE = 0x08;            // st2e: u16 row size
constexpr uint32_t TABLE_ROWS = 0x0C;              // st2e: u32 address of the first row (relocated)

// Item id ranges and their tables: text link u16 = group << 11 | entry.
struct item_table {
    uint16_t first;      // first item id
    uint16_t stride;     // the row size the link offset belongs to
    uint16_t link;       // offset of the u16 text link
    uint16_t reserved;
    uint64_t global;     // VA of the table pointer
};
constexpr item_table ITEM_TABLES[] = {
    {0x0000, 12, 8, 0, 0x2EBF100},   // VA: consumables, section 18 (0x236DCE)
    {0x1000, 52, 2, 0, 0x2EBF0F8},   // VA: equipment, section 13 (0x238169)
    {0x2000, 10, 6, 0, 0x2EBF098},   // VA: loot, section 32 (0x236E72)
};

}  // namespace ilc::hud::tifacts
