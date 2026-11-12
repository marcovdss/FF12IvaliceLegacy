// Ivalice Legacy co-op module: input isolation (subsystem INPUT_ISOLATION).
//
// Guest pads and keyboard never drive the game; Player 1 keeps the stock path, or is fed through it when P1 plays
// on a pad only SDL reads. All hooks are at the game's own input device finalize calls and focus handling:
//
//   IN-01  XInput pad finalize        (call row, tick thread)   per device object, before the shared finalizer
//   IN-02  DirectInput pad finalize   (call row, tick thread)
//   IN-03  Steam pad finalize         (call row, tick thread)
//   IN-05  keyboard finalize          (call row, tick thread)   the keyboard filter
//   IN-06  pad focus gate             (mid-function, tick thread) pads keep working while the window is unfocused
//   IN-06a verify-only gate row: if the bytes before IN-06 are not stock (another mod rewrote that approach), the
//          patch layer skips IN-06 at install and status() reports it
//   IN-07  focus-loss event dispatch  (call row, main thread)   focus loss reads as "active": the game keeps running
//
// Pad policy, per device object at IN-01/02/03, when the record says isolation applies (some guest seat has a pad
// or the keyboard configured, or P1 is fed by substitution):
//   * P1's own device passes untouched: the XInput object whose polled user index (+0x458) is P1's slot, while
//     P1's live source is that XInput slot. Flee: +0x443 is OR-ed with any guest's flee request.
//   * P1 on SDL: exactly one pad object (the "host") gets P1's logical state written over its whole platform block
//     +0x414..+0x44E just before the finalizer. Host preference: the DirectInput object whose product id matches
//     P1's pad, then any XInput object, then Steam, then any DirectInput object; re-elected when the host has not
//     been seen for a whole device round. Taps shorter than a game tick are kept: presses since the last read
//     (edge cursor, reseeded on overrun or a new epoch) are OR-ed into the level.
//   * Every other pad object gets the neutral block (and, for XInput objects, packet number +0x45C = 0 so the
//     next real packet is interpreted again). That is every guest pad, and also any unowned pad, as the reference
//     behaviour did: a pad nobody paired must not drive P1's character either.
//   * When isolation stops applying, the previous host gets the neutral block once (release), then stock.
// Keyboard: while a guest seat (or nobody) owns keyboard and mouse and isolation applies, every key state is
// cleared except a few shared keys (map, speed, map transparency), rebuilt from the raw sample. The filter has a
// lease: each game keyboard update spends one unit of a budget the co-op thread refills to KB_BUDGET every beat,
// so a stalled co-op thread gives the keyboard back after KB_BUDGET updates even inside the liveness window.
//
// Isolation does not depend on GATE_WORLD: its decisions come from the settings and the input service, not from
// world memory, and guest input must stay out of title screens, menus and cutscenes too. Guests' flee requests
// come from the movement subsystem (movement::flee_seats, epoch-tagged: a word from another epoch reads 0) and
// are OR-ed into P1's flee byte (+0x443) on P1's own device or the substitution host.
//
// Every handler runs behind isolation's own fail-open check: the subsystem live, and a record published for the
// dispatcher's epoch or one epoch either side of it (isolation reads no world memory, so an epoch change must not
// open a stock window), else stock. Bodies reach game memory only through the fault-safe probe and check the
// device class (vtable) first. A disconnected XInput object (IN-01's edi != 0) is never the substitution host;
// the host's packet number is cleared every tick so a hard disarm leaves it showing its real pad.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "ilc/seat_input.h"
#include "patch/detour.h"

namespace ilc::session {
struct session_record;
}

namespace ilc::isolation {

constexpr int32_t KB_BUDGET = 240;            // keyboard updates per refill (refilled every co-op beat)
constexpr uint32_t BLOCK_OFFSET = 0x414;      // device platform block, through +0x44E
constexpr uint32_t BLOCK_SIZE = 0x3B;

enum record_flag : uint32_t {
    ISO_GUARD = 0x01,       // a guest seat has a pad or the keyboard configured: mask every non-P1 pad
    ISO_SUBST = 0x02,       // P1's live source is SDL: feed P1 through the host object (masks the rest too)
    ISO_KB_FILTER = 0x04,   // filter the keyboard (a guest or nobody owns it, and GUARD)
    ISO_FLEE = 0x08,        // force flee on P1's device (tests); guests' requests come from movement::flee_seats
    ISO_BACKGROUND = 0x10,  // pads work unfocused (IN-06) and focus loss does not pause (IN-07)
    ISO_RELEASE = 0x20,     // isolation just stopped: neutralize the previous host once
    ISO_P1_NATIVE = 0x40,   // P1's live source is an XInput slot (p1_slot)
};

// The published record (small on purpose: a mid-function body copies it).
struct iso_record {
    patch::record_head head;  // epoch = session epoch, flags = record_flag, generation = session generation
    uint8_t p1_slot;          // XInput user index of P1's live pad, 0xFF none
    uint8_t kbm_seat;         // settings value (0 none, 1..)
    uint8_t guest_seats;      // bit s: seat s+1 has a configured guest device or the keyboard
    uint8_t reserved0;
    uint16_t p1_vid;          // P1's SDL pad vendor/product (0 unknown): host preference only
    uint16_t p1_pid;
    uint32_t reserved1;
};
static_assert(sizeof(iso_record) == 32, "iso_record layout (host tests read it)");

// ---- co-op thread ---------------------------------------------------------------------------------------------
void configure_from_settings(const ilc_settings& s);  // applied settings: kbm seat, configured devices, P1 pad id
void publish(uint64_t now_ms);                         // after the session step: build and publish; refill budget
// The record publish() would build now (reads the pad mirror and the configuration; moves the release counter).
iso_record compose(uint32_t epoch, uint64_t generation, const session::session_record* s);
void withdraw();                                        // at stop: detours fall back to stock
// Release before a disarm (for the kill switch): from the next publish on, records carry only ISO_RELEASE, so no
// pad is masked or substituted and the current host gets the neutral block (and its packet number cleared) once.
// release_settled() is true once such a record was built and no host remains. end_release() returns to normal.
// The caller owns the sequence and its time cap.
void begin_release();
bool release_settled();
void end_release();

// Boot (once, before arming): registers the handlers and arms IN-01/02/03/05/06/07. False if a row is missing.
bool attach();

// ---- pure functions (host tests) ------------------------------------------------------------------------------
// The platform block f(S) for one XINPUT_GAMEPAD image: sticks trunc(v/128) (Y negated), 14 buttons, LT>0, RT>0
// (or flee), every field XInput never writes zero. `extra` = ilc_pad_control press bits OR-ed into the buttons.
void platform_block(const ilc_xinput_gamepad& pad, uint32_t extra, bool flee, uint8_t out[BLOCK_SIZE]);

struct counters {
    volatile uint64_t passed;       // P1's own device, untouched
    volatile uint64_t substituted;  // host writes
    volatile uint64_t masked;       // neutral writes
    volatile uint64_t released;     // release writes
    volatile uint64_t stock;        // isolation did not apply
    volatile uint64_t wrong_class;  // the vtable did not match the hook's class: stock
    volatile uint64_t probe_failed; // a probe read or write failed: stock
    volatile uint64_t kb_filtered;
    volatile uint64_t kb_expired;   // budget spent: stock
    volatile uint64_t background;   // IN-06 unfocused runs
    volatile uint64_t focus_kept;   // IN-07 events rewritten
    volatile uint64_t host_changes;
};
const counters* stats();

struct status_view {
    uint32_t in06_armed;    // 0 = IN-06 not hooked (IN-06a did not match at install: background pads off)
    uint32_t published;     // records published
    uint64_t host;          // current host object (compare only)
};
status_view status();

// ---- host tests ----------------------------------------------------------------------------------------------
patch::record_slot<iso_record>& slot();
void test_publish(const iso_record& r);
void test_reset();                     // counters, host election, cursor, budget = KB_BUDGET
int32_t test_budget();
void test_set_budget(int32_t value);
enum block_variant : uint32_t { VARIANT_OK = 0, VARIANT_NO_NEGATE = 1, VARIANT_SHIFT = 2, VARIANT_NO_CLEAR = 3,
                                VARIANT_NONE = 4 };
void test_block_variant(uint32_t v);   // instrument controls for the substitution test (must make it fail)
void test_flee_source(uint32_t (*source)(uint32_t epoch));  // nullptr = the movement subsystem's flee word
// The handlers, for direct calls in host tests.
int32_t in01(patch::ctx* c, uint32_t epoch);
int32_t in02(patch::ctx* c, uint32_t epoch);
int32_t in03(patch::ctx* c, uint32_t epoch);
int32_t in05(patch::ctx* c, uint32_t epoch);
int32_t in06(patch::ctx* c, uint32_t epoch);
int32_t in07(patch::ctx* c, uint32_t epoch);

}  // namespace ilc::isolation
