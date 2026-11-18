// Ivalice Legacy co-op overlay: the three themes compiled into the DLL (Phase 8, Marco 2026-11-16: C "Bold Modern" is
// the default, A "Humanist" and B "Crisp" are the other built-ins).
//
// Each one is the artist's package as the converter wrote it (THEME-FORMAT.md 6): atlas/font.inc (glyph rows, kerning
// rows, metrics and the run-length coded atlas, as constant tables) and theme.json (embedded as bytes and read at
// attach by the same reader and the same rules as a player's theme: theme.h). tools/gen_overlay_themes.py copies them
// into src/overlay/themes/<id>/ and pins every file in themes.lock.json; build.py refuses a drift.
//
// Data only: no state, any thread.
#pragma once

#include <stdint.h>

#include "overlay/font.h"

namespace ilc::overlay::builtin {

struct theme_data {
    const char* id;              // "legacy-bold", the key the settings store
    const font::set* font;       // tables (atlas pointer null: a slot decodes it)
    const uint8_t* json;         // theme.json, UTF-8
    uint32_t json_len;
};

constexpr uint32_t COUNT = 3;
constexpr uint32_t DEFAULT = 0;  // legacy-bold (C)

// 0 legacy-bold (C, the default), 1 legacy-humanist (A), 2 legacy-crisp (B); past the end: the default.
const theme_data& get(uint32_t k);
// The index of built-in `id` (n bytes), or COUNT when it is none.
uint32_t find(const char* id, uint32_t n);

}  // namespace ilc::overlay::builtin
