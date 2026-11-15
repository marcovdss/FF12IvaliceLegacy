// Ivalice Legacy co-op module: target circles off (the TARGET INFO target's circle; the AoE aiming ring stays).
//
// One site, 0x28c5a0 (native_facts.h): the test that lets the circle-list draw skip a node. Its 8-byte window (the
// 7-byte test plus the following je opcode) is classified as:
//   STOCK   the game's bytes                                  -> ensure_off() writes ours
//   OURS    the same test with immediate 0 (one byte differs) -> ours if this module wrote it, else adopted as is
//   VISION  Insurgent's Vision "No Target Circle": cmp eax,eax and a 5-byte nop (one 0x90 run or a multi-byte nop
//           form), je kept                                    -> adopted, never written
//   FOREIGN anything else                                     -> skipped and reported, never written
// ensure_off() changes the immediate byte with one locked compare-exchange of the aligned qword (expected = the
// stock qword), so no instruction boundary moves and a concurrent writer makes it fail instead of mixing bytes; the
// page protection is raised for the write and put back as it was. restore() writes the stock byte back the same way,
// only if the site still holds exactly our form and this module wrote it. Vision's bytes are never touched: when
// Vision turns its option off it writes the stock bytes itself, and the next ensure_off() applies ours if wanted.
//
// The area ring (the area list at [ui+0x10f40], drawn by 0x3f3260) is a different list and a different draw: this
// switch never touches it. The two-slot marker block at ui+0x7ea0 (0x28c970) is not covered either.
#pragma once

#include <stdint.h>

namespace ilc::hud::circles {

enum site_class : uint32_t {
    SITE_UNREADABLE = 0,
    SITE_STOCK = 1,
    SITE_OURS = 2,
    SITE_VISION = 3,
    SITE_FOREIGN = 4,
};

enum ensure_result : uint32_t {
    CIRCLES_UNKNOWN = 0,
    CIRCLES_OFF_OURS = 1,      // our byte is in place (written by this module)
    CIRCLES_OFF_ADOPTED = 2,   // Vision's patch (or our exact form written by someone else) is in place: zero writes
    CIRCLES_ON = 3,            // stock (restored, or not wanted)
    CIRCLES_FOREIGN = 4,       // unknown bytes: skipped
    CIRCLES_UNREADABLE = 5,
    CIRCLES_WRITE_FAILED = 6,  // the compare-exchange or the protection change failed: nothing changed
};

enum restore_result : int32_t {
    RESTORE_NOTHING = 0,
    RESTORE_DONE = 1,
    RESTORE_GONE = 2,          // someone else rewrote the site since (Vision turned on, ...): ownership dropped
    RESTORE_FAILED = 3,
};

struct counters {
    volatile uint64_t looks;
    volatile uint64_t writes;        // compare-exchanges attempted (apply and restore)
    volatile uint64_t applied;
    volatile uint64_t restored;
    volatile uint64_t adopted;       // looks that found Vision's form (or our form not written by us)
    volatile uint64_t foreign;
    volatile uint64_t write_failed;
    volatile uint64_t dropped;
};

struct status_view {
    uint32_t state;      // ensure_result after the last call
    uint32_t site;       // site_class seen last
    uint8_t owned;       // this module wrote the byte that is there now
    uint8_t reserved[7];
    uint8_t bytes[8];    // the window as seen last
};

uint32_t classify(const uint8_t window[8]);

// Game thread (or the exit path). Never block.
uint32_t ensure_off();
int32_t restore();
bool owned();

status_view status();
const counters* stats();

// ---- host tests ----------------------------------------------------------------------------------------------------
void test_reset();
void test_set_site(uint64_t va);  // 0 = the game's site
void test_fail_cas(bool fail);    // the next compare-exchanges see a changed qword

}  // namespace ilc::hud::circles
