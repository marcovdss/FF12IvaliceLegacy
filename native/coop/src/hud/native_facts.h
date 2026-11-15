// Ivalice Legacy co-op module: game facts for the native HUD relayout (the party list) and the target-circle switch
// (FFXII_TZA.exe 1.0.4.0, VAs at the pinned base 0x120000; the module always loads there, see sites.inc).
//
// The layout facts describe the field MRP (the menu resource pack section that lays out the field and battle HUD):
// a 0x30-byte header, then 0x14-byte group records, each pointing at its variable-length entries. Once loaded, the
// pack's offsets have been turned into absolute 32-bit pointers (the HUD heap lives below 4 GiB). The approved
// reduced layout (party list at 75 % about a bottom-right anchor, action bar 70 %, digits 85 %, selection panel moved
// right) is stated here as values and SHA-256 fingerprints of the stock and the reduced field bytes.
#pragma once

#include <stdint.h>

namespace ilc::hud::native_facts {

// ---- where the field HUD layout lives ---------------------------------------------------------------------------
constexpr uint64_t MRP_FIELD_SLOT = 0x209AC68;    // VA: u64, slot 1 of the MRP section table 0x209AC60 (field HUD)
constexpr uint32_t MRP_LIVE_MAGIC = 0x0150524D;   // NOTADDR: 'MRP' with byte 3 set to 1 once the section is live
constexpr uint32_t MRP_GROUP_COUNT = 0x1C;        // u32: groups in the section (52 in the stock pack)
constexpr uint32_t MRP_GROUP_LIST = 0x20;         // u32: absolute address of group record 0 (live)
constexpr uint32_t MRP_SPAN = 0x10000;            // NOTADDR: every live pointer must stay this close above the
                                                  // section start (the stock section is 0x2a40 bytes)
constexpr uint32_t GROUP_SIZE = 0x14;
constexpr uint32_t GROUP_COUNT_BYTE = 0x00;       // u8: entries in the group
constexpr uint32_t GROUP_RECT = 0x04;             // s16 x, s16 y, s16 w, s16 h (1920x1080 authoring units)
constexpr uint32_t GROUP_GAP = 0x0C;              // i32: row pitch of list groups
constexpr uint32_t GROUP_ENTRIES = 0x10;          // u32: absolute address of the first entry (live)
constexpr uint32_t ENTRY_SIZE_BYTE = 0x00;        // u8: entry length; entries follow each other
constexpr uint32_t ENTRY_TYPE_BYTE = 0x01;        // u8: 1 embed, 2 sprite, 4 quad, 5 text, 6 dynamic text, 7 gauge
constexpr uint32_t ENTRY_RECT = 0x04;             // s16 x4, as the group rect
constexpr uint32_t ENTRY_TEXT_SCALE = 0x10;       // u8: text entries of at least 0x18 bytes (100 = 1.0)
constexpr uint8_t ENTRY_TYPE_TEXT = 5;
constexpr uint32_t ENTRY_MIN = 0x0C;
constexpr uint32_t ENTRY_MAX = 0x80;
constexpr uint32_t ENTRIES_MAX = 96;

// ---- the groups the relayout owns ---------------------------------------------------------------------------------
// 40 party row template (embeds 41-48: MP bars, HP/MP digit variants, Esper row, TIME), 49 row glow, 50 the ACTION
// bar (one window per member; not the command menu), 51 the leader band. 21 is the party selection panel (rect only).
constexpr uint32_t GROUP_FIRST = 40;
constexpr uint32_t GROUP_LAST = 51;
constexpr uint32_t GROUP_TEMPLATE_LAST = 48;      // 40..48: the row template and its embedded groups
constexpr uint32_t GROUP_ROW = 40;
constexpr uint32_t GROUP_GLOW = 49;
constexpr uint32_t GROUP_ACTION = 50;
constexpr uint32_t GROUP_BAND = 51;
constexpr uint32_t GROUP_ESPER = 47;
constexpr uint32_t GROUP_PANEL = 21;

// Fields in the fingerprint, in this order: per group 40..51, its rect (8 bytes) and gap (4); per entry, its rect
// (8) and, for a text entry of at least 0x18 bytes, its text scale (1). 110 fields in the stock pack.
constexpr uint32_t FIELDS_MAX = 192;
constexpr char FINGERPRINT_STOCK[] = "24e93218b194875235248869b51c8ca710d297df3f0cf32a8cd204cc2b3dc71c";
constexpr char FINGERPRINT_REDUCED[] = "3c2d721e5fc305d28272ce5b61848d79eadd1a5983c56e68c67812d736641a81";

// The selection panel rect, stock and reduced (moved right).
constexpr int16_t PANEL_STOCK[4] = {570, 741, 1800, 304};
constexpr int16_t PANEL_REDUCED[4] = {950, 741, 1800, 304};

// ---- the reduced layout ---------------------------------------------------------------------------------------------
// Scale 3/4, rounded half away from zero. Root groups (40, 49, 50, 51) scale about the anchor; embedded groups and
// entries scale about their parent's origin. Then the reviewed corrections below.
constexpr int32_t SCALE_NUM = 3;
constexpr int32_t SCALE_DEN = 4;
constexpr int32_t ANCHOR_X = 1402;
constexpr int32_t ANCHOR_Y = 286;
constexpr int16_t ROOT_X_ROW = 122;               // reviewed x of the root groups after scaling
constexpr int16_t ROOT_X_GLOW = 317;
constexpr int16_t ROOT_X_ACTION = 78;
constexpr int16_t ROOT_X_BAND = 470;
constexpr int32_t ROW_GAP = 42;                   // gap of groups 40 and 49
// Digit sprites: width and height at 85 % (rounded half up, at least 1), then 4 units lower.
constexpr int32_t DIGIT_NUM = 85;
constexpr int32_t DIGIT_DEN = 100;
constexpr int32_t DIGIT_Y_SHIFT = 4;
struct digit_entries {
    uint8_t group;
    uint8_t first;
    uint8_t last;   // inclusive
};
constexpr digit_entries DIGITS[] = {
    {40, 7, 9}, {40, 11, 13}, {40, 15, 17},   // HP / MP digits of the row template
    {44, 0, 3}, {45, 0, 5}, {46, 0, 7},      // the 2/3/4-digit variants
    {47, 1, 3},                              // the Esper row's digits
    {48, 0, 4},                              // the TIME counter
};
constexpr uint32_t ROW_NAME_ENTRY = 19;           // group 40: the member name; y set to ROW_NAME_Y
constexpr int16_t ROW_NAME_Y = 3;
constexpr uint32_t ESPER_NAME_ENTRY = 0;          // group 47: the Esper's name; y and text scale set
constexpr int16_t ESPER_NAME_Y = 11;
constexpr uint8_t ESPER_NAME_SCALE = 61;

// Group 50 (the ACTION bar) takes fixed values, in field order: rect, gap, E0 gauge rect, E1 label rect, E1 text
// scale, E2 / E3 / E4 sprite rects. Its stock form has exactly these 8 fields.
constexpr uint32_t ACTION_FIELDS = 8;
constexpr int16_t ACTION_RECT[4] = {ROOT_X_ACTION, 244, 408, 48};
constexpr int32_t ACTION_GAP = 39;
constexpr int16_t ACTION_ENTRY_RECTS[5][4] = {
    {183, 20, 140, 17},   // E0 gauge
    {165, 0, 171, 25},    // E1 label
    {242, 6, 71, 32},     // E2 red X
    {119, 0, 94, 34},     // E3 'QUEUE' plate
    {220, 17, 113, 25},   // E4
};
constexpr uint8_t ACTION_TEXT_SCALE = 56;

// ---- the target circle -----------------------------------------------------------------------------------------------
// 0x28c590 walks the circle list [ui+0x10f00] (the TARGET INFO target's circle; the AoE aiming ring is the area list
// [ui+0x10f40], drawn elsewhere) and draws a node only when `test byte [rbx+0xd4c], 1` leaves ZF clear:
//   28c5a0  f6 83 4c 0d 00 00 01   test byte ptr [rbx+0xd4c], 1
//   28c5a7  74 57                  je 0x28c600   (skip this node)
// Off = the same instruction with immediate 0: ZF is always set, the je always skips, and no instruction boundary
// moves (one byte, written atomically). Insurgent's Vision "No Target Circle" writes `cmp eax,eax` + a 5-byte nop
// there instead: also ZF set, adopted as is.
constexpr uint64_t CIRCLE_SITE = 0x28C5A0;        // VA: 16-aligned; the qword covers the test and the je opcode
constexpr uint8_t CIRCLE_STOCK[8] = {0xF6, 0x83, 0x4C, 0x0D, 0x00, 0x00, 0x01, 0x74};
constexpr uint32_t CIRCLE_IMM = 6;                // offset of the immediate inside the qword
constexpr uint8_t CIRCLE_IMM_ON = 0x01;
constexpr uint8_t CIRCLE_IMM_OFF = 0x00;
constexpr uint32_t CIRCLE_LEN = 7;                // the instruction Vision replaces
constexpr uint8_t CIRCLE_JE = 0x74;               // byte 7 must stay the je opcode in every accepted form

// ---- the native TARGET INFO window (documentation for the Target Info plan; nothing here writes it) ---------------
constexpr uint64_t UI_MANAGER = 0x209BE80;        // VA: u64 UI manager
constexpr uint32_t UI_TARGET_INFO = 0x9FD0;       // its TARGET INFO window (null while closed)
constexpr uint32_t WINDOW_FLAGS = 0x40;           // u32: the window draw needs (flags & 0x405) == 5
constexpr uint32_t WINDOW_GROUP = 0x58;           // the window's layout group record

}  // namespace ilc::hud::native_facts
