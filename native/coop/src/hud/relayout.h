// Ivalice Legacy co-op module: the native party list, reduced (live edit of the field HUD layout).
//
// The party list, its leader band and glow, the per-member ACTION bars and the party selection panel are laid out by
// records of the field MRP (native_facts.h). Windows read those records when they are created, so a live edit of the
// records is picked up the next time the game builds its HUD windows (party rows on a HUD rebuild, the panel when it
// opens), exactly like the stock values.
//
//  * ensure_reduced() (game thread, from the UI tick's job): reads the layout through the fault-safe probe and
//    classifies it by the SHA-256 of its fields:
//      - stock, and the selection panel stock: once the same allocation has looked stock for SETTLE_MS, every field
//        is rewritten to the reduced value (the transform's own result must hash to the reduced fingerprint first),
//        each write checked against the bytes just read, then the whole layout is read back and must hash to the
//        reduced fingerprint. A failed check writes the stock values back and gives up on that allocation;
//      - reduced and written by us in this allocation: nothing to do (a three-read guard keeps this cheap);
//      - anything else (another mod changed one of these records, or the reduced layout we did not write): left
//        alone and reported (FOREIGN / REDUCED_OTHER). Nothing is ever written to a layout we do not recognise.
//  * restore() (game thread, or the exit path once the game thread no longer runs the HUD): puts back the stock
//    bytes saved when we applied, only into the same allocation and only while it still hashes to the reduced
//    fingerprint with the panel reduced. If the game has reloaded the layout (stock again) or someone else changed
//    it, ownership is dropped without a write.
//
// Reads and writes go through runtime/probe.h; nothing is allocated, no lock is waited on. The state is used by one
// caller at a time (a try-flag; the exit path gives up after a bounded wait).
#pragma once

#include <stdint.h>

namespace ilc::hud::relayout {

constexpr uint64_t SETTLE_MS = 1250;  // a stock allocation must stay unchanged this long before it is edited

enum layout_state : uint32_t {
    LAYOUT_UNKNOWN = 0,        // not looked at yet
    LAYOUT_UNLOADED = 1,       // no live field MRP (null slot, or the magic is not the live one)
    LAYOUT_INVALID = 2,        // a record failed the structure checks (reloading, or not the layout we know)
    LAYOUT_SETTLING = 3,       // stock; waiting for it to stay unchanged for SETTLE_MS
    LAYOUT_REDUCED = 4,        // reduced, written by this module into this allocation
    LAYOUT_REDUCED_OTHER = 5,  // reduced, but not written by this module here: left alone, never restored
    LAYOUT_FOREIGN = 6,        // another mod changed these records: skipped, never written
    LAYOUT_FAILED = 7,         // a write did not verify: stock values written back; no retry on this allocation
    LAYOUT_BUSY = 8,           // another caller held the state (only the exit path can see this)
    LAYOUT_STOCK = 9,          // stock again: our edit was given back, or the game reloaded the layout in place
};

enum restore_result : int32_t {
    RESTORE_NOTHING = 0,       // nothing of ours was there
    RESTORE_DONE = 1,          // stock bytes are back, read back and verified
    RESTORE_GONE = 2,          // the allocation we edited was replaced or reloaded stock: ownership dropped, no write
    RESTORE_CHANGED = 3,       // someone else changed the layout since: ownership dropped, no write
    RESTORE_FAILED = 4,        // a write or the read-back failed (the layout may be partly stock)
    RESTORE_BUSY = 5,
};

struct counters {
    volatile uint64_t looks;           // ensure_reduced calls that read the layout
    volatile uint64_t quick;           // ... answered by the guard (still ours)
    volatile uint64_t applied;         // reduced layouts written and verified
    volatile uint64_t restored;        // restores written and verified
    volatile uint64_t dropped;         // ownership dropped without a write (reloaded, replaced or changed)
    volatile uint64_t foreign;         // looks that found a layout we do not recognise
    volatile uint64_t invalid;         // looks that found a record failing the structure checks
    volatile uint64_t settling;        // looks that waited for a stock allocation to settle
    volatile uint64_t raced;           // writes refused because the layout changed between read and write
    volatile uint64_t failed;          // writes or read-backs that did not verify
    volatile uint64_t busy;            // calls that found the state held
};

struct status_view {
    uint32_t state;           // layout_state after the last look
    uint32_t fields;          // fields read at the last look
    uint64_t base;            // the allocation looked at last (0 = none)
    uint64_t owned_base;      // the allocation we reduced and still own (0 = none)
    uint8_t fingerprint[32];  // of the last look
};

// ---- game thread ---------------------------------------------------------------------------------------------------
uint32_t ensure_reduced(uint64_t now_ms);
int32_t restore();
bool owned();

// ---- exit path: restore() under a bounded wait for the state (gives RESTORE_BUSY after wait_ms) -----------------
int32_t restore_waiting(uint32_t wait_ms);

// ---- any thread ------------------------------------------------------------------------------------------------------
status_view status();
const counters* stats();

// ---- host tests -------------------------------------------------------------------------------------------------------
void test_reset();
void test_set_slot(uint64_t va);  // where the field MRP pointer is read (0 = the game's slot)
bool test_hold(bool hold);        // take (true) or release (false) the state as another caller would

}  // namespace ilc::hud::relayout
