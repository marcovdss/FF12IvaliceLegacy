// Ivalice Legacy co-op module: the start-up switch ("Co-op module On/Off") read from the settings file.
//
// The Lua settings store is the only writer of scripts/config/IvaliceLegacy/coop.json. The module reads one value
// from it, once, at FF12Hinit, before any Lua script can push settings: module.enabledAtStart. The file is opened
// read-only with full sharing (it never blocks the store), at most MAX_BYTES are read, and nothing is written,
// created or kept open.
//
// Outcomes, fail closed:
//   missing file            -> the native default (On), flag "defaults"
//   unreadable / too large  -> Off (SETTINGS_UNREADABLE, detail = Win32 error, or 0 for too large)
//   not JSON, not an object -> Off (SETTINGS_INVALID, detail INVALID_SYNTAX)
//   schema missing or not 1 -> Off (SETTINGS_INVALID, detail INVALID_SCHEMA; a newer schema is INVALID_NEWER_SCHEMA)
//   module or enabledAtStart of the wrong type -> Off (SETTINGS_INVALID, detail INVALID_VALUE)
//   module or enabledAtStart missing -> the native default
//   enabledAtStart false    -> Off (MODULE_OFF)
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace ilc::boot {

enum settings_outcome : uint32_t {
    SETTINGS_ON = 0,
    SETTINGS_OFF = 1,
    SETTINGS_UNREADABLE = 2,
    SETTINGS_INVALID = 3,
};

enum settings_invalid : uint32_t {
    INVALID_SYNTAX = 1,
    INVALID_SCHEMA = 2,
    INVALID_NEWER_SCHEMA = 3,
    INVALID_VALUE = 4,
};

struct settings_switch {
    uint32_t outcome;    // settings_outcome
    uint32_t detail;     // Win32 error (UNREADABLE) or settings_invalid (INVALID)
    bool defaulted;      // the file or the key was missing: the native default decided
};

constexpr size_t SETTINGS_MAX_BYTES = 1u << 20;

// x64_dir: the game's x64 folder. default_on: the native default (ilc_settings.module_enabled_at_start).
settings_switch read_module_switch(const wchar_t* x64_dir, bool default_on);

// The parser alone (host tests): the same rules on a buffer.
settings_switch parse_module_switch(const char* text, size_t size, bool default_on);

}  // namespace ilc::boot
