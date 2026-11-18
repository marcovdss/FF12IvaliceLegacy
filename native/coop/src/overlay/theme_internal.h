// Ivalice Legacy co-op overlay: what theme.cpp (the module) and theme_read.cpp (the reader) share. Not for other modules.
#pragma once

#include <stdint.h>

#include "overlay/theme.h"

namespace ilc::overlay::theme {

// A theme in memory: its tokens, its font set and the one allocation the set points into (the decoded atlas, and for a
// player theme its glyph, kerning and metric rows).
struct slot {
    tokens t;
    font::set f;
    uint8_t* block;
    uint64_t block_size;
    char digest[65];
    uint8_t builtin;          // 1 + built-in index, 0 for a player theme
    uint8_t reserved[7];
};

// The built-in default's tokens (C), read once from its embedded theme.json (the base every other theme starts from).
const tokens& default_tokens();
// The engine's own values for every field (before any theme.json): what C's optional sections fall back to.
void base_tokens(tokens* t);
// The derived text styles from the tokens.
void resolve(tokens* t);

// Memory for the reader (VirtualAlloc; zeroed). nullptr on failure.
void* alloc(uint64_t bytes);
void release(void* p);

// A whole file into a new buffer (<= cap bytes). NO_FILE, TOO_LARGE or READ_FAILED on failure; *out owns the buffer.
uint32_t read_file(const wchar_t* path, uint32_t cap, uint8_t** out, uint32_t* len);

// "name" -> its index in `names` (count entries), or font::ICON_NONE.
uint16_t icon_index(const char* const* names, uint32_t count, const char* name, uint32_t len);

// The converter's built-in icon set, in its order (a theme without "icons" was baked with it).
extern const char* const DEFAULT_ICON_NAMES[];
extern const uint32_t DEFAULT_ICON_COUNT;

}  // namespace ilc::overlay::theme
