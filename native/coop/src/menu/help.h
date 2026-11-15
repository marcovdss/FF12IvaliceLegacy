// Ivalice Legacy co-op module: an action's help line, from the game's own help text (read-only).
//
// The battle menu's help line for an action (magicks, technicks, items, Quickenings, Espers) is the help text whose id
// sits in the action row at +0x00. The game resolves an id with 0x2F9860, restated here and walked through the
// fault-safe reader, never by calling it:
//   bank = id / 1000 (< 28), index = id % 1000;
//   the bank must be loaded (qword at 0x2EC3D80 + bank * 8 is not 0);
//   header = qword at 0x2F973C0 + bank * 8; index < u32 [header + 4];
//   directory = u32 [header + 0x14]; list = u32 [directory + 4]; text = u32 [list + 4 + index * 4]
// (every 32-bit value is an address: the resource base is 0). A miss is the game's empty string. Action help ids are
// bank 4 (the battle's help_action text, which Legacy replaces with its own), so the help follows Legacy's text. An
// item shows the help of the action it uses (item row +7), the row the game executes.
//
// The text is the game's single-byte text. Formatting tokens start with 0x0F: 0x0F 0x29 is a colour run (a header
// whose low three bits count its values, 0 = one), 0x0F 0x3C a colour switch and 0x0F 0x3F an inline icon, each with
// two value bytes. They are removed here; a token of another kind makes the text unusable (the caller falls back to
// its own line). Turning the characters into ASCII is the presentation's job.
#pragma once

#include <stdint.h>

#include "session/reader.h"

namespace ilc::menu {

constexpr uint32_t HELP_BANKS = 28;
constexpr uint32_t HELP_BANK_SIZE = 1000;
constexpr uint32_t HELP_RAW_CAP = 192;   // the longest help line seen is under 100 bytes

// Remove the formatting tokens; returns the length (0 when a token is not one we know, or `in` is empty). Stops at
// the first 0 byte; `out` is 0-terminated.
uint32_t strip_help_tokens(const uint8_t* in, uint32_t n, uint8_t* out, uint32_t cap);

// The help id in an action's row (+0x00); false when the action table or the row cannot be read.
bool action_help_id(session::game_reader& r, uint32_t action, uint32_t* id);

// Where the game's help text for `id` is (0x2F9860's walk); 0 when the game would give its empty string.
uintptr_t help_text_at(session::game_reader& r, uint32_t id);

// The text at `at` without its tokens (at most cap - 1 bytes, 0-terminated); returns the length, 0 = none.
uint32_t read_help(session::game_reader& r, uintptr_t at, uint8_t* out, uint32_t cap);

// An action's help in one go. *where = the text's address (a cache key), 0 when there is none.
uint32_t action_help(session::game_reader& r, uint32_t action, uint8_t* out, uint32_t cap, uintptr_t* where);

// ASCII text fitted into `cap` bytes (NUL included): kept whole when it fits, else cut at the last space that leaves
// room for "..." (or hard, when there is no space). Returns the length.
uint32_t fit_text(const char* in, char* out, uint32_t cap);

}  // namespace ilc::menu
