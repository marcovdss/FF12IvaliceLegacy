// Ivalice Legacy co-op module: seat marks in the world (seat-coloured target lines and the numbered seat diamonds).
//
// Both marks are the game's own world drawing, recoloured or fed by us; nothing here is an overlay.
//
// Target lines. The game draws one line node per (source, target, type) from the UI's line list and binds the
// line texture with CLUT = 0x2713b0(node +0x840 type): party lines blue (green for support actions), foe lines red
// (purple). Every later use of that CLUT reads the register written at 0x3f54d2, so one detour there (TG-06, r15 = the
// node, +0x810 = the source actor's handle) recolours the strip and its glow. The native target arrow (the ribbon
// between the leader and its target, owner +0x2d0) binds with CLUT = [+0x330] + 2 * [+0x32c] at 0x3f71a0; TG-05 sits
// on the two moves before it (rsi = the arrow node) and changes that sum. Both rows are the site table's own (TG-06,
// TG-05: TARGETS_CARDS PATCH rows, installed from the first build, stock until a handler is set). The rule:
//   * the source is a character a seat holds -> that seat's colour (the co-op colour setting, by seat or by
//     character, resolved as the party list does) on any party line type (3, 4);
//   * the source is a party member no seat holds (a gambit ally) -> CLUT 6, the native white/silver;
//   * anything else (foes, neutrals, unknown handles) -> the native CLUT, untouched;
//   * the arrow follows the same owner rule, but only in its native "on" colour (gold, 4): its red state carries the
//     game's own meaning and stays red.
// The line strip's vertex colour is a fixed 0xFE grey, so an exact RGBA would need a code change per draw; the seat
// colour is therefore one of the seven baked palettes (ilc_colour is that palette index).
//
// Seat diamonds. The world-marker dispatcher's overhead pass 0x2c62b0(manager, list A, list B, pool, list D) is
// called once per frame from 0x28edad; the pool holds 128 objects of 0x100 bytes and is reset every frame, so an
// object never outlives its frame. WM-01 (a call row there) runs the pass and then appends our objects the way the
// game's own leader marker does: 0x3f0040(pool) -> anchor -> 0x3f12e0(obj, partytop_4_c, sheet, cell) -> colour,
// alpha and quad -> 0x3f1290(list B, obj). Per visible seat: a gem (sheet 0x0105; its cell picks the colour) and the
// seat numeral over it (sheet 0x0002, cells 0-9). The anchor is the character's overhead record (+0x00, found by
// handle at +0x10), the one the native status icons use; a character without a record shows no diamond. A full
// pool (0x3f0040 returns null) stops emission for the frame: native objects keep priority because ours come last.
// When a seat's diamond shows: fight start (a held character enters fighting stance after a quiet stretch), the seat
// swapping character, the seat dropping in, and identify(seat) (the Identify button; the Battle HUD page wires the binding). It
// fades in, holds and fades out by the game's own per-object alpha (+0x4c), computed on the game thread from the
// time it was shown, so a stalled co-op thread cannot leave one stuck.
//
// Threads. The co-op thread samples (session record, HUD snapshot, colours, stance) and publishes two small records
// (lines, diamonds) in seqlocks. TG-06/TG-05 are mid-function MinHook rows: they read the record copy and the node
// the game handed them (through the fault-safe probe) and change one or two registers; they call nothing. WM-01 is a
// call row: its after-handler calls the three game functions above. None of them blocks, allocates or calls the C
// runtime. Every switch off (element switch, TARGETS_CARDS, F8, DRY, no record of this epoch) is the stock path.
// Subsystem: TARGETS_CARDS ("target rings, TARGET INFO and cards"), the subsystem of TG-05/TG-06; WM-01 joins it.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "patch/detour.h"
#include "patch/patch_engine.h"

namespace ilc::hud::world_marks {

// ---- game facts (VAs at the pinned base 0x120000) ----------------------------------------------------------------------
constexpr uint64_t PINNED_BASE = 0x120000;
constexpr uint64_t SITE_LINE_CLUT = 0x3F54D2;      // TG-06: `mov r14d, eax; xor r8d, r8d` (eax = the line's CLUT)
constexpr uint64_t SITE_ARROW_CLUT = 0x3F719C;     // TG-05: `mov edx, edi; mov ecx, ebx; lea r9d, [rax + r9*2]`
constexpr uint64_t SITE_OVERHEAD_CALL = 0x28EDAD;  // WM-01: `call 0x2c62b0` (the overhead pass)
constexpr uint64_t FN_OVERHEAD_PASS = 0x2C62B0;
constexpr uint64_t FN_OBJECT_ALLOC = 0x3F0040;     // obj* (pool*): null when count >= capacity
constexpr uint64_t FN_SPRITE_FRAME = 0x3F12E0;     // frame* (obj*, texture entry*, page << 8 | sheet, cell)
constexpr uint64_t FN_LIST_APPEND = 0x3F1290;      // void (list*, obj*)
constexpr uint64_t UI_ATLASES = 0x209AC30;         // VA: pointer; +0x360 = partytop_4_c's texture entry
constexpr uint32_t ATLAS_PARTYTOP = 0x360;

constexpr uint32_t LINE_SOURCE = 0x810;            // line node: u32 source actor handle
constexpr uint32_t LINE_TYPE = 0x840;              // u16 line type (1/2 foe, 3/4 party)
constexpr uint32_t ARROW_SOURCE = 0x2D0;           // arrow node: u32 source actor handle
constexpr uint8_t TYPE_PARTY = 3;                  // party -> target
constexpr uint8_t TYPE_PARTY_SUPPORT = 4;          // party, action flag 0x800
constexpr uint8_t ARROW_ON = 4;                    // the arrow's native "on" colour (gold); 1 = red
constexpr uint8_t CLUT_GAMBIT = 6;                 // white/silver: a party member no seat holds
constexpr uint8_t CLUT_COUNT = 7;                  // targetline_p carries seven palettes
constexpr uint8_t CLUT_NONE = 0xFF;

constexpr uint32_t OVERHEAD_RECORDS = 0x1D70;      // manager: first overhead record
constexpr uint32_t OVERHEAD_NEXT = 0x18;           // record: next
constexpr uint32_t OVERHEAD_HANDLE = 0x10;         // record: u32 actor handle
constexpr uint32_t OVERHEAD_ANCHOR = 0x00;         // record: vec4 world anchor
constexpr uint32_t OVERHEAD_WALK_MAX = 64;         // records looked at per frame (the list holds one per actor)
constexpr uint32_t OBJ_ANCHOR = 0x00;              // world object: vec4 anchor (w = -1.0 as the leader marker writes)
constexpr uint32_t OBJ_COLOUR = 0x40;              // R, G, B, A as u32 (0x80 = 1.0), then the quad x0, y0, x1, y1
constexpr uint32_t OBJ_LIFT = 0x70;                // f32 screen lift (negative = up), then f32 1.0 (as the status icon)
constexpr uint32_t POOL_CAPACITY = 0x08;           // pool {u64 base, s32 capacity, s32 count}
constexpr uint32_t POOL_COUNT = 0x0C;
constexpr uint32_t FRAME_SIZE = 0x04;              // sprite frame: u32 w:12 | h:12 | palette:8
constexpr uint32_t GEM_SHEET = 0x0105;             // partytop_4_c page 1 sheet 5: the gems (40 x 70)
constexpr uint32_t DIGIT_SHEET = 0x0002;           // partytop_4_c page 0 sheet 2: small digits 0-9 (28 x 38)
constexpr uint8_t GEM_NONE = 0;

// Diamond timing and size (look [U]: tuned in game).
constexpr uint64_t DIAMOND_FADE_IN_MS = 150;
constexpr uint64_t DIAMOND_HOLD_MS = 2500;         // full alpha after the fade-in
constexpr uint64_t DIAMOND_FADE_OUT_MS = 700;
constexpr uint64_t ENGAGE_QUIET_MS = 3000;         // out of stance this long before a new stance counts as a fight start
constexpr uint32_t GEM_SCALE_PCT = 50;             // drawn size, percent of the cell
constexpr uint32_t DIGIT_SCALE_PCT = 50;
constexpr int32_t DIAMOND_LIFT = -48;              // px above the overhead anchor
constexpr uint32_t ALPHA_FULL = 0x80;

constexpr uint32_t SEATS = 4;
constexpr uint32_t LINE_ENTRIES = 8;               // four seats + four party rows

// ---- the rules (pure) ----------------------------------------------------------------------------------------------------
enum mark_kind : uint8_t {
    MARK_NONE = 0,
    MARK_SEAT = 1,      // a seat holds this character
    MARK_GAMBIT = 2,    // a party member no seat holds
};

struct line_entry {
    uint32_t handle;    // the source actor handle (0 = unused)
    uint8_t kind;       // mark_kind
    uint8_t seat;       // 1..4 for MARK_SEAT
    uint8_t clut;       // the palette its lines take
    uint8_t reserved;
};
static_assert(sizeof(line_entry) == 8, "line_entry layout (host tests build it)");

struct lines_record {
    patch::record_head head;   // epoch = session epoch
    uint8_t enabled;           // the element switch
    uint8_t count;             // entries used
    uint8_t reserved[6];
    line_entry entries[LINE_ENTRIES];
};
static_assert(sizeof(lines_record) == 88, "lines_record layout (host tests build it)");

// The CLUT a line node takes: `native` (0x2713b0's result) unless its source is in the record and the type is a
// party line. The arrow: `native` unless its source is a seat's or a gambit ally's and the native colour is ARROW_ON.
uint32_t line_clut(const lines_record& r, uint32_t source, uint32_t type, uint32_t native);
uint32_t arrow_clut(const lines_record& r, uint32_t source, uint32_t native);
// A seat colour (ilc_colour, the palette index) as a line CLUT; CLUT_NONE if it is not one of the seven.
uint8_t clut_of_colour(uint8_t colour);
// The partytop_4_c gem cell for a seat colour: 1 blue, 2 green, 3 purple, 4 red, 6 gold, 7 grey (yellow -> gold).
uint8_t gem_cell(uint8_t colour);

// Diamond alpha (0..0x80) at `now` for a diamond shown at `shown_ms` (0 = not shown): fade in, hold, fade out.
uint32_t diamond_alpha(uint64_t shown_ms, uint64_t now_ms);

// The show triggers, per seat, sampled each beat.
struct seat_input {
    uint8_t held;              // the seat holds a character (in session, a member, an actor handle)
    uint8_t stance_known;      // the stance byte was read
    uint8_t stance;            // fighting stance (actor +0x02 bit 7)
    uint8_t reserved;
    uint16_t member;
    uint16_t reserved2;
    uint32_t handle;
};
static_assert(sizeof(seat_input) == 12, "seat_input layout (host tests build it)");

struct seat_track {
    uint16_t member;
    uint8_t held;
    uint8_t reserved;
    uint32_t handle;
    uint64_t shown_ms;         // 0 = not shown
};
static_assert(sizeof(seat_track) == 16, "seat_track layout");

struct trigger_state {
    uint32_t epoch;            // the world the tracks belong to (0 = none yet)
    uint8_t engaged;           // some held character was in stance at the last beat
    uint8_t reserved[3];
    uint64_t quiet_since;      // when the party last left stance (0 = never engaged in this world)
    seat_track seats[SEATS];
};
static_assert(sizeof(trigger_state) == 80, "trigger_state layout (host tests read it)");

enum trigger_bit : uint32_t {
    TRIG_FIGHT = 0x1,
    TRIG_SWAP = 0x2,
    TRIG_DROP_IN = 0x4,
    TRIG_IDENTIFY = 0x8,
};
// One beat: updates the tracks and returns the triggers that fired, 4 bits per seat (seat s at bits 4s..4s+3). A new
// epoch takes the first beat as its baseline (no trigger: an area load is none of the four). A seat that stops holding a
// character loses its diamond at once. Showing a diamond that is still visible keeps it at full and restarts its hold.
uint32_t trigger_step(trigger_state& st, uint32_t epoch, const seat_input in[SEATS], uint32_t identify_mask,
                      uint64_t now_ms);

struct diamond {
    uint32_t handle;           // the character it hangs over (0 = none)
    uint8_t seat;              // 1..4
    uint8_t gem;               // gem cell
    uint16_t reserved;
    uint64_t shown_ms;         // 0 = not shown
};
static_assert(sizeof(diamond) == 16, "diamond layout (host tests build it)");

struct diamonds_record {
    patch::record_head head;   // epoch = session epoch
    uint64_t sampled_ms;
    uint8_t enabled;           // the element switch
    uint8_t reserved[7];
    diamond seats[SEATS];
};
static_assert(sizeof(diamonds_record) == 96, "diamonds_record layout (host tests build it)");

// ---- the game functions WM-01 calls (relocated, or the host test's doubles) -----------------------------------------------
struct game_fns {
    uint64_t (*alloc)(uint64_t pool);
    uint64_t (*frame)(uint64_t obj, uint64_t texture, uint32_t sheet, uint32_t cell);
    void (*append)(uint64_t list, uint64_t obj);
};

// ---- module ---------------------------------------------------------------------------------------------------------------
bool attach(uintptr_t image_base);                    // the handlers of TG-06, TG-05 and WM-01 (each row on its own)
void configure_from_settings(const ilc_settings& s);  // seat/character colours and the colour mode
void step(uint64_t now_ms);                           // co-op thread: sample and publish (after the snapshot)
void stop();                                          // withdraw both records
// Element switches (the Battle HUD page wires them; on by default).
void set_lines_enabled(bool on);
void set_diamonds_enabled(bool on);
bool lines_enabled();
bool diamonds_enabled();
// The Identify button: show this seat's diamond (0..3) at the next beat. Any thread.
void identify(uint32_t seat);

// The handlers (also called by host tests with a synthetic ctx).
int32_t line_call(patch::ctx* c, uint32_t epoch);      // TG-06 (r15 = line node, eax = CLUT)
int32_t arrow_call(patch::ctx* c, uint32_t epoch);     // TG-05 (rsi = arrow node, eax + 2 * r9d = CLUT)
int32_t overhead_call(patch::ctx* c, uint32_t epoch);  // WM-01 before the pass (rcx manager, rdx/r8 lists, r9 pool)
void overhead_after(patch::ctx* c, uint32_t epoch);    // WM-01 after the pass: our objects

struct counters {
    volatile uint64_t samples;          // records published
    volatile uint64_t lines_recoloured; // line binds given a seat or gambit CLUT
    volatile uint64_t arrows_recoloured;
    volatile uint64_t lines_unread;     // line or arrow nodes the probe could not read (stock)
    volatile uint64_t passes_wrapped;   // overhead passes run with ours after them
    volatile uint64_t objects;          // world objects appended (gems and numerals)
    volatile uint64_t pool_full;        // frames where the pool ran out before ours were done
    volatile uint64_t no_anchor;        // visible diamonds without an overhead record
    volatile uint64_t triggers;         // diamonds shown
};
const counters* stats();

struct status_view {
    uint8_t attached;                   // bit 0 TG-06, bit 1 TG-05, bit 2 WM-01 (handler set and armed)
    uint8_t lines;
    uint8_t diamonds;
    uint8_t reserved;
};
status_view status();

constexpr uint32_t OBJECTS_PER_SEAT = 2;   // a gem and its numeral

// ---- host tests -----------------------------------------------------------------------------------------------------------
void test_reset();
void test_publish_lines(const lines_record& r);
void test_publish_diamonds(const diamonds_record& r);
bool test_read_lines(lines_record* out);
bool test_read_diamonds(diamonds_record* out);
void test_set_image_base(uintptr_t base);
void test_set_fns(const game_fns* fns);   // nullptr = the game's own
void test_set_attached(uint32_t mask);
const trigger_state* test_triggers();
int32_t test_row(uint32_t which);         // 0 TG-06, 1 TG-05, 2 WM-01
uint32_t test_identify_pending();         // Identify bits not yet taken by a beat (bit = seat 0..3)

}  // namespace ilc::hud::world_marks
