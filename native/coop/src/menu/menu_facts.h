// Ivalice Legacy co-op module: game facts behind a guest's battle menu (FFXII_TZA.exe 1.0.4.0, fixed image base).
//
// Globals and functions are VAs of the pinned build; readers relocate them to the image base they were given (a host
// test passes a synthetic image). Offsets inside game objects are plain numbers. Everything here is read-only: the
// menu model never writes game memory. Every table global below was located in the battle-pack loader; the reader
// still refuses a table whose header or rows do not look like one (the menu then stays closed) instead of guessing.
#pragma once

#include <stdint.h>

namespace ilc::menu::facts {

constexpr uintptr_t IMAGE_VA = 0x120000;               // VA: the fixed image base

// ---- the game's loaded data tables -------------------------------------------------------------------------------
// The battle data pack is loaded once; the pack loader 0x236C30 registers each section into a global that holds a
// qword pointer to the table's header in memory:
//   +0x04 u32 row count, +0x08 u16 row size, +0x0C u32 rows address (rebased in place to a 32-bit address).
// A row is (uint32_t)(u32 [header+0x0C] + id * u16 [header+0x08]), formed in 32 bits as the game does. The game adds
// the resource base below, which lives in .bss, is never written and so is 0; a non-zero value would already break
// the game's own direct reads, so the reader refuses the tables then (fail closed). Whatever the External File Loader
// replaced (Legacy's own sections) is what sits in memory, so these tables are Legacy's, not vanilla.
constexpr uintptr_t RESOURCE_BASE_PTR = 0x1F83530;     // VA: u64, 0 in practice
constexpr uint32_t TABLE_COUNT = 0x04;
constexpr uint32_t TABLE_STRIDE = 0x08;
constexpr uint32_t TABLE_ROWS = 0x0C;
constexpr uintptr_t ACTION_TABLE_PTR = 0x2EBF138;      // VA: actions (section 14, one row per action id)
constexpr uintptr_t AUGMENT_TABLE_PTR = 0x2EBF040;     // VA: augments (section 58; u16 value at +4)
constexpr uintptr_t LICENCE_TABLE_PTR = 0x2EBF038;     // VA: licence nodes (section 12)
constexpr uintptr_t MEMBER_TABLE_PTR = 0x2EBF130;      // VA: party-member table (section 16, one row per party id)
constexpr uintptr_t ITEM_TABLE_PTR = 0x2EBF100;        // VA: consumable items (section 18)
constexpr uintptr_t MAGICK_TABLE_PTR = 0x2EBF0F0;      // VA: magicks (section 29)
constexpr uintptr_t TECHNICK_TABLE_PTR = 0x2EBF0E8;    // VA: technicks (section 30)
constexpr uintptr_t NAME_TEXT_PTR = 0x2EBF170;         // VA: name text directory (section 61), for presentation

// Action row.
constexpr uint32_t ACT_HELP = 0x00;                    // u16 help text id (the battle menu's help line)
constexpr uint32_t ACT_AREA = 0x06;                    // u8 area radius
constexpr uint32_t ACT_COST = 0x0A;                    // u8 base cost (MP, or charges for kinds 5 and 6)
constexpr uint32_t ACT_TARGETING = 0x0C;               // u8: bit 0 self only, bit 2 allies, bit 3 foes, bits 5-7 initial
constexpr uint32_t ACT_KIND = 0x1E;                    // u8 menu kind (1 magick, 2 technick, 3 item, 5 Esper,
                                                       // 6 Quickening; 5 and 6 are paid with Mist charges)
constexpr uint8_t KIND_MAGICK = 1;
constexpr uint32_t ACT_RULES = 0x2C;                   // u16: bits 0-2 magick school, bit 6 KO allies, bit 7 stoned
                                                       // allies, bit 13 needs no licence
constexpr uint32_t ACT_TARGET_FLAGS = 0x36;            // u16: bit 5 reserve members
constexpr uint32_t ACT_ROW_MIN = 0x38;
constexpr uint32_t ACTION_LIMIT = 0x21F;               // ids above 0x21E cost nothing (the cost helper's own bound)

// Licence node row: +5 type; types 9..14 and 20 list up to eight action ids (u16, 0xFFFF none) at +8.
constexpr uint32_t LIC_TYPE = 0x05;
constexpr uint32_t LIC_ACTIONS = 0x08;
constexpr uint32_t LIC_ROW_MIN = 0x18;
constexpr uint8_t LIC_TYPE_QUICKENING = 19;
// Item row: +0 sort key, +7 the action the item uses, +8 u16 the action whose name and help it shows.
// Magick and technick rows: +2 action, +6 sort key.
// Party-member row (index = party id 0..0x27): ten u16 content ids at +0x58. For the story guests the game checks
// (0x3060E0) these are the only actions they may use; for everybody else the list is a starting gift, not a menu.
constexpr uint32_t MEMBER_CONTENTS = 0x58;
constexpr uint32_t MEMBER_ROW_MIN = 0x6C;
constexpr uint32_t MEMBER_ROWS = 0x28;                 // party ids 0..0x27 (the record getter's bound)
constexpr uint16_t CONTENT_MAGICK = 0x3000;
constexpr uint16_t CONTENT_TECHNICK = 0x4000;
// Party ids that use the fixed list (0x3060E0: 7..0x19 and 0x1B..0x27). Ids 0..6 and 0x1A use party ownership and
// licences (0x323EC0 treats 0..6 as licence holders).
constexpr bool fixed_list_member(uint32_t member)
{
    return (member >= 7 && member <= 0x19) || (member >= 0x1B && member <= 0x27);
}
constexpr bool licence_member(uint32_t member) { return member <= 6; }

// ---- shared state: one per game --------------------------------------------------------------------------------
constexpr uintptr_t LOCATION_PTR = 0x2092758;          // VA: the block that also holds the menu flags and the gil
constexpr uint32_t PARTY_GIL = 0x08;                   // u32 the party's gil (0x253690 reads it)
constexpr uint32_t MENU_FLAGS = 0x2204;                // u32: bit 0 Magicks & Technicks, bits 7/8 Mist
constexpr uintptr_t BATTLE_STATE_PTR = 0x2EBF190;      // VA: party block (member records, inventory, ownership)
constexpr uint32_t ITEM_COUNTS = 0x4748;               // 64 u16 counters (see below)
// Item counter word w: owned = w & 0x3FF; available = owned - (w >> 10) (the upper bits are held back, by queued
// actions as far as the arithmetic shows). The game's own readers: 0x30AE30 owned, 0x309EC0 available.
constexpr uint16_t COUNT_OWNED_MASK = 0x3FF;
constexpr uint32_t COUNT_HELD_SHIFT = 10;
constexpr uint32_t MAGICK_OWNED = 0x4F10;              // 12 bytes, bit = magick index
constexpr uint32_t TECHNICK_OWNED = 0x4F1C;            // 4 bytes, bit = technick index
constexpr uint32_t MIST_OWNED = 0x4F80;                // 4 bytes: bits 0..17 Quickenings, 18..30 Espers
constexpr uintptr_t COMMAND_SEALS = 0x22C314C;         // VA: u8 area command seals
constexpr uint8_t SEAL_ATTACK = 0x04;
constexpr uint8_t SEAL_MAGICK = 0x08;
constexpr uint8_t SEAL_TECHNICK = 0x10;
constexpr uint8_t SEAL_ITEM = 0x20;
// The player's own item order (the Items screen's manual sort): count at [owner]+0x47B8, u16 item indices at the
// 32-bit address u32 [ITEM_ORDER_OFFSET] (relative to the resource base, which is 0).
constexpr uintptr_t ITEM_ORDER_OWNER_PTR = 0x20927C0;  // VA
constexpr uint32_t ITEM_ORDER_COUNT = 0x47B8;          // u32
constexpr uintptr_t ITEM_ORDER_OFFSET = 0x2092764;     // VA: u32, relative to the resource base

// ---- per character: the member record a bound actor's +0x698 points at -----------------------------------------
constexpr uint32_t MEMBER_RECORDS = 0x08;              // battle state + 0x08 + id * 0x1C8 (getter 0x320A40, ids
                                                       // 0..0x27)
constexpr uint32_t MEMBER_RECORD_SIZE = 0x1C8;
constexpr uint32_t ACTOR_HANDLE = 0x08;                // u32
constexpr uint32_t ACTOR_BATTLE = 0x698;               // pointer to the member record
constexpr uint32_t ACTOR_GIL_RESERVED = 0xE58;         // s32: gil put aside for the queued action (set from the gil
                                                       // price when an action is queued, refunded on cancel)
constexpr uint32_t REC_STATE = 0x00;                   // u8: bit 2 = gambits on
constexpr uint8_t REC_GAMBITS_ON = 0x04;
constexpr uint32_t REC_SIDE = 0x05;                    // u8: 0 party member; 1 changes how the cost helper treats
                                                       // the caster
constexpr uint32_t REC_MAGICK = 0x2B;                  // u8 Magick (current, equipment included)
constexpr uint32_t REC_STATUS_LASTING = 0x3C;          // u32 status bits
constexpr uint32_t REC_MP = 0x4C;                      // s16 current MP (the admission check compares it signed)
constexpr uint32_t REC_CHARGES = 0x4E;                 // u8: what kinds 5 and 6 are paid with
constexpr uint32_t REC_STATUS_TIMED = 0x64;            // u32 status bits
constexpr uint32_t REC_AUGMENTS_TIMED = 0x68;          // 12 bytes, augment n = byte n >> 3, bit n & 7
constexpr uint32_t REC_AUGMENTS_LASTING = 0x78;        // 12 bytes
constexpr uint32_t REC_LICENCES = 0x194;               // 46 bytes, bit = licence node index
constexpr uint32_t REC_MAGICK_LICENCES = 0x1AA;        // 23 bytes, the game's live magick licence summary

// ---- statuses (both status words) --------------------------------------------------------------------------------
constexpr uint32_t ST_KO = 1u << 0;
constexpr uint32_t ST_STONE = 1u << 1;
constexpr uint32_t ST_SILENCE = 1u << 9;
constexpr uint32_t ST_BERSERK = 1u << 27;
// KO, Stone, Stop, Sleep, Confuse, Disable, X-Zone: no command at all.
constexpr uint32_t ST_NO_COMMAND = 0x8000203Bu;        // NOTADDR: bits 0, 1, 3, 4, 5, 13, 31
constexpr uint32_t ST_GAMBITS_BLOCKED = ST_KO | ST_STONE;

// ---- actions -------------------------------------------------------------------------------------------------------
constexpr uint16_t ACTION_ATTACK = 0x96;
constexpr uint16_t ACTION_DISMISS = 0x113;
constexpr uint16_t QUICKENING_FIRST = 0xD0;            // 0xD0..0xE1, three per regular character
constexpr uint16_t QUICKENING_LAST = 0xE1;
constexpr uint16_t ESPER_FIRST = 0x106;                // 0x106..0x112, mist bit 18 + n and licence node 18 + n
constexpr uint16_t ESPER_LAST = 0x112;
constexpr uint32_t ESPER_BIT_FIRST = 18;
constexpr uint32_t ESPER_BIT_LAST = 30;
// Actions whose effect is a revival (the target picker offers KO allies for them).
constexpr uint16_t REVIVAL_ACTIONS[] = {0x05, 0x0F, 0x58, 0xAE};

// ---- cost helper inputs (augment ids) ------------------------------------------------------------------------------
constexpr uint32_t AUG_OFFERING = 32;     // MP cost moves elsewhere
constexpr uint32_t AUG_PARSIMONY = 36;    // halves the cost
constexpr uint32_t AUG_SPELLSPRING = 51;  // no MP cost
constexpr uint32_t AUG_CHANNELING_FIRST = 91;  // 91..93: percentage reductions, values from the augment table
constexpr uint32_t AUG_CHANNELING_LAST = 93;
// The cost helper's constants (single precision): Channeling is a percentage of the base; Offering's gil price is
// trunc(base * 64 / 50) * 10.
constexpr float COST_PERCENT = 100.0f;
constexpr float GIL_PRICE_MUL = 64.0f;
constexpr float GIL_PRICE_DIV = 50.0f;
constexpr int32_t GIL_PRICE_SCALE = 10;

// ---- game functions the menu layer relies on (called on the game thread by src/submit) -------------------------
constexpr uintptr_t FN_ADMISSION = 0x303BD0;           // VA: int(actor, u16 action): 0 = may use, else 0x12..0x15
constexpr uintptr_t FN_MP_COST = 0x309110;             // VA: effective MP cost (what the admission compares)
constexpr uintptr_t FN_COST_HELPER = 0x2F95D0;         // VA: augment-adjusted cost (menu/cost.h restates it)
constexpr uintptr_t FN_GIL_COST = 0x309060;            // VA: gil price (Offering)
constexpr uintptr_t FN_CHARGE_COST = 0x308FF0;         // VA: Mist charges (menu kinds 5/6)
constexpr uintptr_t FN_HELP_TEXT = 0x2F9860;           // VA: char*(int id): help text (menu/help.h restates it)
constexpr uintptr_t FN_AVAILABLE = 0x3064F0;           // VA: int(u8 member, u16 action, int): 1 = owned/fixed and licensed
constexpr uintptr_t FN_NAME_TEXT = 0x3214B0;           // VA: char*(int ref): name text, ref = sub-table << 11 | index
constexpr uintptr_t FN_GAMBITS_QUERY = 0x309B40;       // VA: int(member id): gambits on
constexpr uintptr_t FN_GAMBITS_SET = 0x311AF0;         // VA: setter
constexpr uint32_t SOUND_GAMBITS_ON = 0x12;
constexpr uint32_t SOUND_GAMBITS_OFF = 0x13;

}  // namespace ilc::menu::facts
