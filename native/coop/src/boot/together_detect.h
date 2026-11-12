// Ivalice Legacy co-op module: detection of an installed Ivalice Together.
//
// Together is a different co-op mod that patches the same game code. The two must never run in one process, and
// Together does not know about this module, so the check is entirely ours: the boot refuses to arm while any
// blocking marker is present, at FF12Hinit and again on the init thread just before the hook transaction.
//
// Markers (one bit each, reported in the Status record):
//  * files, relative to the game's x64 folder: the loader module, the helper runtime, the configurator;
//  * a loaded module with the loader module's name;
//  * the per-process mutexes of its loader module and of its runtime supervisor;
//  * a 64 KiB-aligned allocation in the low 2 GiB that starts with one of its three region magics, or that holds
//    its install marker at +0x60.
// Residue that cannot patch anything on its own (its settings folder, its Lua menu add-on) is reported but does not
// block. A check that cannot decide (a file or mutex query failing with anything but "not found") counts as present
// and sets UNCERTAIN: uncertainty never authorizes arming.
//
// Read-only: nothing is created, opened for writing, or kept open. The region scan reads 8-byte headers through the
// fault-safe probe.
#pragma once

#include <stdint.h>

namespace ilc::boot {

enum together_marker : uint32_t {
    TOGETHER_FILE_MODULE = 1u << 0,
    TOGETHER_FILE_RUNTIME = 1u << 1,
    TOGETHER_FILE_CONFIGURATOR = 1u << 2,
    TOGETHER_MODULE_LOADED = 1u << 3,
    TOGETHER_MUTEX_MODULE = 1u << 4,
    TOGETHER_MUTEX_SUPERVISOR = 1u << 5,
    TOGETHER_REGION_PRIMARY = 1u << 6,
    TOGETHER_REGION_INTERACTION = 1u << 7,
    TOGETHER_REGION_DAMAGE = 1u << 8,
    TOGETHER_INSTALL_MARKER = 1u << 9,
    TOGETHER_BLOCKING = 0x3FFu,
    TOGETHER_RESIDUE_DATA = 1u << 16,
    TOGETHER_RESIDUE_MENU = 1u << 17,
    TOGETHER_RESIDUE = TOGETHER_RESIDUE_DATA | TOGETHER_RESIDUE_MENU,
    TOGETHER_UNCERTAIN = 1u << 31,  // some check failed and was counted as present
};

enum marker_kind : uint32_t {
    MARKER_FILE = 1,      // text = path relative to the x64 folder
    MARKER_MODULE = 2,    // text = module file name
    MARKER_MUTEX = 3,     // text = mutex name prefix; the process id follows in decimal
    MARKER_REGION = 4,    // text = 8 magic bytes at `offset` from a 64 KiB-aligned allocation base
};

struct marker {
    uint32_t kind;
    uint32_t bit;
    uint32_t offset;    // MARKER_REGION only
    const char* text;   // ASCII
};

// The table (for host tests: they build each marker from it instead of spelling the names).
uint32_t marker_count();
const marker* marker_at(uint32_t index);

// Runs every check. x64_dir: the game's x64 folder (no trailing separator); pid: the process the mutex names use.
// Returns together_marker bits.
uint32_t detect_together(const wchar_t* x64_dir, uint32_t pid);

}  // namespace ilc::boot
