// Ivalice Legacy co-op module: game facts the HUD canvas uses (FFXII_TZA.exe 1.0.4.0, VAs at the pinned base
// 0x120000; the module always loads there, see sites.inc). Static evidence for each is in the HD-01/HD-V* rows.
#pragma once

#include <stdint.h>

namespace ilc::hud::facts {

constexpr uint64_t PINNED_BASE = 0x120000;

// ---- the frame hook (HD-01) -------------------------------------------------------------------------------------
// The combat-log manager's handler 0x2be4f0, message 4 (its draw message): `mov rdx,[rbx+0x38]; mov rcx,rbx;
// call 0x2bf230`. The window draw 0x245e60 stores the window's draw context at window+0x38 (a 0xa0-byte block on
// its own stack) for the duration of messages 3/4/5 and clears it afterwards.
constexpr uint64_t FN_LOG_DRAW = 0x2BF230;        // VA: log draw (rcx = manager, rdx = context); HD-V1
constexpr uint64_t LOG_DRAW_RETURN = 0x2BE7DF;    // VA: after HD-01's call (`xor eax, eax`): where a skipped log draw resumes
constexpr uint32_t WINDOW_CONTEXT = 0x38;         // window+0x38: the draw context while the window draws
constexpr uint32_t WINDOW_PARENT = 0x10;          // window+0x10: its parent window (0 = a root)
constexpr uint32_t ANCESTORS_MAX = 8;             // NOTADDR: bound of the walk to the root
constexpr uint32_t CONTEXT_SIZE = 0xA0;           // the draw context block (0x2d8c00 clears 0xa0 bytes)
// The window draw 0x245e60 keeps its context at rbp+0x10 (window+0x38 points there) and, in the same stack frame at
// rbp+0xF0 = context+0xE0, the 4x4 matrix its children draw in: it passes that as rdx to each child's draw (0x246370).
// A root (parent 0) copies the matrix it was given there (0x245ec0..0x245efb) and never fills its own context's
// matrix, which stays as 0x2d8c00 cleared it: all zero (0.1.55 in game: "depth 2, matrix [0 0 / 0 0 / t 0 0]"). So
// the HUD tree's root space is the root's context+0xE0, live while the root draws its children. [V] static
constexpr uint32_t CONTEXT_CHILD_SPACE = 0xE0;
constexpr uint32_t MATRIX_BYTES = 0x40;
constexpr uint32_t CONTEXT_ALPHA = 0x58;          // s16: alpha scale (0x80 = 1.0); 0 = the node draw draws nothing
constexpr uint32_t CONTEXT_BRIGHT = 0x5A;         // s16: colour scale (0x80 = 1.0)
constexpr uint32_t CONTEXT_CLUT = 0x5C;           // u8: palette of the next sprite
constexpr uint32_t CONTEXT_COLOUR = 0x90;         // u32 RGBA (0x80 = 1.0) of the next text or bar
constexpr uint32_t CONTEXT_FLAGS = 0x5C;          // u32; bit CONTEXT_CLIP = clip the window's draws to its rect at +0x48
constexpr uint32_t CONTEXT_CLIP = 0x20000;        // NOTADDR: 0x2d8c00 sets it on every new context

// ---- the clip (HD-V12) ------------------------------------------------------------------------------------------
// The scissor setter 0x2d8530(context): CONTEXT_CLIP set = the render scissor is the context's rect at +0x48, clear =
// no scissor (0x249fd0 with null -> 0x184460). The window draw 0x245e60 calls it with the window's context before the
// window's messages (0x246235), so while the log manager draws (HD-01), every draw is clipped to the log window: in
// 0.1.56 only the battle log (inside that rect) showed, and the menus, party extras and Target Info were cut away.
// The replay lifts the clip before it draws and sets the log manager's again after.
constexpr uint64_t FN_SCISSOR = 0x2D8530;         // VA: (context); HD-V12

// ---- text (HD-V2, HD-V3) ----------------------------------------------------------------------------------------
constexpr uint64_t FN_TEXT_DRAW = 0x2D8650;       // VA: (context, int x, int y, params): int -> float, then 0x2d8690
constexpr uint64_t FN_TEXT_PARAMS = 0x2B3400;     // VA: (params 0xd0, string, font): the parameter block init
constexpr uint64_t EMPTY_STRING = 0x1CEB638;      // VA: the string the log draw initialises its block with
constexpr uint32_t PARAMS_SIZE = 0xD0;            // 0x2ac1e0 clears 0xd0 bytes
constexpr uint32_t PARAMS_STRING = 0x28;          // char*: the text (game encoding, NUL-terminated)
constexpr uint32_t PARAMS_SCALE = 0x34;           // float: overall text size (1.0 = native)

// ---- sprites (HD-V4..HD-V6, HD-V8): the MRP node draw 0x2435c0 path ---------------------------------------------
constexpr uint64_t FN_BATCH_BEGIN = 0x2D9C40;     // VA: (batch, context, 1)
constexpr uint64_t FN_BATCH_SPRITE = 0x2D9380;    // VA: (batch, context, texture, colours[4], &uvwh, x, y, w, h, 0)
constexpr uint64_t FN_BATCH_END = 0x2D9B00;       // VA: (batch, context)
constexpr uint32_t BATCH_SIZE = 0x30;             // the node draw's batch block (48 bytes on its stack)
constexpr uint32_t BATCH_SPRITES = 12;            // the node draw ends and restarts a batch after 12 sprites
constexpr uint64_t FN_TEXTURE_BY_NAME = 0x24A5A0; // VA: registry entry for a loaded texture name, or 0
constexpr uint32_t TEXTURE_PICTURE = 0x20;        // registry entry +0x20: the TIM2 picture header
constexpr uint32_t PICTURE_USER = 0x30;           // picture header +0x30: the 'eXt\0' user-data block (sprite tables)
constexpr uint32_t EXT_MAGIC = 0x00745865;        // NOTADDR: "eXt\0" little-endian
constexpr uint32_t EXT_SIZE = 0x04;               // u32 block size
constexpr uint32_t EXT_FRAMES = 0x10;             // u16 offset of the frame table (8 bytes per frame)
constexpr uint32_t EXT_CLUT_GROUPS = 0x12;        // u16 offset of u8[16]: CLUT group -> first CLUT
constexpr uint32_t EXT_PAGES = 0x14;              // u16 A[]: A[page] = index (into A) of the page's sheet list

// ---- bar (HD-V7) ------------------------------------------------------------------------------------------------
constexpr uint64_t FN_SELECTION_BAR = 0x242C90;   // VA: (context, x, y, w, h, &rgba): battle_4_c 3.1 cap + stretch

// ---- state the replay checks --------------------------------------------------------------------------------------
constexpr uint64_t UI_READY = 0x215E6D0;          // VA: u32 the batch begin and the bar test (0 = draw nothing)
constexpr uint64_t RENDER_ARENA = 0x1F7F4C0;      // VA: pointer to the frame command ring (start, cursor, end qwords)
constexpr uint32_t ARENA_START = 0x00;
constexpr uint32_t ARENA_CURSOR = 0x08;
constexpr uint32_t ARENA_END = 0x10;
constexpr uint64_t ARENA_CAPACITY = 0x2000000;    // NOTADDR: 32 MiB, the constructor's size (0x1a14e0)
// Ring cost of one 0x1815xx batch at its end (0x181330 allocates a 0x238-byte command, rounded up + 0x10) and an
// estimate of its vertex payload; a sprite batch of up to 12 sprites is one such batch.
constexpr uint32_t ARENA_BATCH_BYTES = 0x250;
constexpr uint32_t ARENA_VERTEX_BYTES = 0x30;

}  // namespace ilc::hud::facts
