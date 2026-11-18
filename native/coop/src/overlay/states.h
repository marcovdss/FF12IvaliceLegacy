// Ivalice Legacy co-op overlay: the Legacy script states on screen (the script-to-screen channel).
//
// The Lua systems keep the states players coordinate on in script memory only (prototypes/family_identities.lua,
// proficiency_runtime.lua, accessory_effects.lua, kings_runtime.lua): a foe Broken for some seconds, Staggered, its
// Defense dented by Sunder, tired from avoiding, Spotted, oiled and not yet kindled; a party member's guard worn, its
// fist Cadence or pole Counterflow; and the Proficiency / identity procs as they fire. A Lua timer (never a sync hook:
// the same rule as the G24 seam, HUD-DESIGN 2.6) packs a bounded record and pushes it through one bridge op,
// ILC_OP_PUSH_HUD_STATES (bridge_abi.h, ilc_hud_states). The bridge validates it and keeps the latest copy; the co-op
// thread resolves its battle-record addresses to actor handles each beat and publishes this record; the render
// thread draws it (Target Info's states line and tags, the party rows' weapon words, the log's proc tags).
//
// A record older than STALE_MS (the script stopped pushing, the menu reloaded, co-op off) shows nothing: every piece
// it feeds is drawn only while live.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "patch/detour.h"

namespace ilc::overlay::states {

constexpr uint32_t FOES = ILC_HUD_FOES;
constexpr uint32_t WORDS = ILC_HUD_WORDS;
constexpr uint32_t PROCS = ILC_HUD_PROCS;
constexpr uint64_t STALE_MS = 1500;          // the script pushes every 250 ms; four missed pushes and it is gone
constexpr uint64_t SKEW_MS = 250;            // a push stamped this far after the beat's clock still counts
constexpr uint64_t PROC_TAG_MS = 2500;       // a proc names the log line of its actor this long

struct foe {
    uint32_t handle;             // the actor (0 = unused)
    uint8_t flags;               // ilc_hud_foe_flag
    uint8_t broken_s;            // seconds of Broken left (ILC_HUD_FOE_BROKEN)
    uint8_t dent_pct;            // Defense dent, percent of its starting value (0..50)
    uint8_t tired;               // guard fatigue steps (0..4)
    uint8_t spotter_seat;        // 1..4: the seat whose member marked it (ILC_HUD_FOE_SPOTTED), 0 unknown
    uint8_t crown_element;       // 0..7 (element bit order) for ILC_HUD_FOE_CROWNED, 0xFF none
    uint8_t reserved[2];
};
static_assert(sizeof(foe) == 12, "foe layout");

struct word {
    uint16_t member;             // party id; 0xFFFF past word_count (on the wire an unused entry is all zero)
    uint8_t kind;                // ilc_hud_word
    uint8_t value;               // Guard: worn steps 0..4; Cadence: steps; Flow: the bonus (+N)
};
static_assert(sizeof(word) == 4, "word layout");

struct proc {
    uint32_t handle;             // the actor whose hit fired it (0 = unused)
    uint8_t tag;                 // ilc_hud_tag
    uint8_t reserved[3];
    uint64_t at_ms;              // module clock when the co-op thread saw it
};
static_assert(sizeof(proc) == 16, "proc layout");

struct record {
    patch::record_head head;     // epoch = session epoch, generation = the script's push counter
    uint64_t built_ms;           // when the co-op thread resolved it
    uint64_t pushed_ms;          // when the script's push arrived
    uint8_t foe_count;
    uint8_t word_count;
    uint8_t proc_count;
    uint8_t reserved[5];
    foe foes[FOES];
    word words[WORDS];
    proc procs[PROCS];
};
static_assert(sizeof(record) <= 512, "a record_slot copy");

// ---- pure ------------------------------------------------------------------------------------------------------------
// The tag's word as the log and Target Info show it ("SUNDER"), "" for an unknown tag.
const char* tag_name(uint32_t tag);
// The weapon word's label ("Guard", "Cadence", "Flow"), "" for none / unknown.
const char* word_label(uint32_t kind);
// Whether a word is shown: Guard from 2 worn steps, Cadence from 1, Flow from 10.
bool word_live(const word& w);
// The foe entry for a handle, or nullptr.
const foe* foe_for(const record& r, uint32_t handle);
const word* word_for(const record& r, uint16_t member);
// The newest proc tag of `actor` seen within PROC_TAG_MS of `now` and not before `since` (the log line's birth), or 0.
uint8_t proc_for(const record& r, uint32_t actor, uint64_t since, uint64_t now);

// ---- module (co-op thread) ---------------------------------------------------------------------------------------------
// The bridge's latest push (validated): kept until the co-op thread resolves it (any thread; seqlock).
void ingest(const ilc_hud_states& pushed, uint64_t now_ms);
void start(uintptr_t image_base);
void step(uint64_t now_ms);      // resolve and publish (after the snapshot)
void stop();
bool read(record& out);          // any thread, wait-free

struct counters {
    volatile uint64_t pushes;    // records the bridge accepted
    volatile uint64_t published;
    volatile uint64_t stale;     // beats with no push within STALE_MS (nothing published)
    volatile uint64_t unresolved;// entries whose record address named no live actor
};
const counters* stats();

// ---- host tests ---------------------------------------------------------------------------------------------------------
void test_reset();
void test_publish(const record& r);
// Resolves a push through `handle_of` (record address -> handle) without the game: what step() publishes.
using resolve_fn = uint32_t (*)(void* ctx, uint64_t record_address);
bool test_resolve(const ilc_hud_states& pushed, uint64_t now_ms, resolve_fn handle_of, void* ctx, record* out);

}  // namespace ilc::overlay::states
