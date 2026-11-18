// Ivalice Legacy co-op module: diagnostic lines in the loader's log (x64/hook.log).
//
// The FF12 module loader writes hook.log through simpleLog.dll, which every loader module imports
// (`_log`, exported by name). The module does not import it: the drain looks simpleLog.dll up among the modules
// already loaded (GetModuleHandleW, never LoadLibrary) and takes `_log` with GetProcAddress, so the import table
// stays as audited and a process without the loader (the host tests) simply has no sink: lines are counted as
// dropped, nothing else changes.
//
//   void _log(uint32_t level, const char* text)    Microsoft x64; text is a plain NUL-terminated string (no format
//                                                  expansion); the logger adds "[ HH:MM:SS ] " and the line end.
//   level 0 = info (hook.ini logInfo), 1 = warning (logWarning), 2 = error (logError).
//   [V] static: simpleLog.dll 0x180002F50 (the export), the hook.ini parser at 0x1800026B0..0x1800027DE (bits 4, 8,
//   0x10 = logInfo, logWarning, logError) and the level test at 0x180003045; the text is written by length after a
//   strlen loop (0x180003303), never passed to a formatter.
//
// Threads. post() never blocks and never allocates: it formats into a fixed ring under a try-lock and drops the line
// (counted) when the ring is full or another thread holds the lock. It may be called from the boot threads, the
// co-op thread and bridge calls (script threads), never from a detour or the game thread. drain() hands the queued
// lines to the logger, which writes a file: only the co-op thread (once per beat), the init thread and FF12Hinit
// call it, never a game thread.
//
// Verbosity: settings log_level 0 keeps start-up, faults, refusals and state changes; 1 adds every applied settings
// record, the co-op on/off switch and seat toggles (post(..., VERBOSE)).
#pragma once

#include <stdint.h>

namespace ilc::log {

enum level : uint32_t {
    INFO = 0,
    WARNING = 1,
    ERR = 2,
};

constexpr uint32_t LINE_MAX = 200;   // bytes per line, the prefix and the terminator included
constexpr uint32_t RING = 32;        // lines held between drains
constexpr bool VERBOSE = true;

// A line built in place (no CRT formatting): text, decimal and hex numbers. Overlong text is cut.
struct line {
    char text[LINE_MAX];
    uint32_t len;

    line();
    line& s(const char* str);
    line& u(uint64_t value);
    line& i(int64_t value);
    line& x(uint64_t value);   // 0x-prefixed lower-case hex
};

// Queue a line (prefixed "Ivalice Legacy Co-op: "). verbose lines are kept only while log_level is 1.
// False when it was dropped (ring full, lock busy, or a verbose line at normal level).
bool post(uint32_t lvl, const line& l, bool verbose = false);
bool post(uint32_t lvl, const char* text, bool verbose = false);

void set_verbose(bool on);   // settings log_level (co-op thread)
bool verbose();

// Write the queued lines to the loader's log. Returns the lines handed over (0 without a sink: they are dropped).
uint32_t drain();

struct counters {
    volatile uint64_t posted;
    volatile uint64_t written;
    volatile uint64_t dropped_full;     // the ring was full
    volatile uint64_t dropped_busy;     // another thread held the ring
    volatile uint64_t dropped_quiet;    // verbose lines at normal level
    volatile uint64_t dropped_no_sink;  // drained with no logger in the process
};
const counters* stats();

// Host tests.
using sink_fn = void (*)(uint32_t lvl, const char* text);
void test_set_sink(sink_fn sink);   // nullptr: look the loader's logger up again
void test_reset();

}  // namespace ilc::log
