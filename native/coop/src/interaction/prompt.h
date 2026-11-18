// Ivalice Legacy co-op module: the interaction prompt ("!") over each guest's own character.
//
// The game shows one prompt, over the controlled actor (P1's leader): a world sprite from battle_4_p, the "!" while
// something usable is near (level 1) and the round marker once it can be used (levels 2/3; P1 also gets a label).
// Its parts, all on the game thread [verified static, FFXII_TZA.exe 1.0.4.0]:
//   * the field tick picks the best publication slot (0x3df810: the highest of 8 slots with a state) and hands
//     (handle, state, resource, text) to the prompt widget, 0x2900c0 -> 0x2c64c0(manager, ...): one widget at
//     overhead manager +0x1b80 (anchor record +0x1b0 = the controlled actor's overhead record, level +0x1b8, flags
//     +0x1b9, fade +0x1ba, pop time +0x1bc, label time +0x1be, lift time +0x1d0);
//   * 0x2c6be0 ticks those times once per frame (fade +0x20, pop +0x10, lift +0x20, each divided by [0x1dfe0b4]);
//   * the setter builds the label ("[confirm glyph] Name") into the widget's own text node (0x2b3400, 0x29cd00);
//   * the overhead pass 0x2c62b0 draws it: `call 0x2c7f90` at 0x2c6352 (manager, widget, list B, pool, list D). The
//     drawer takes one object from the pool (0x3f0040), picks the frame (battle_4_p, [0x916f00 + level - 1] past the
//     sheet's first frame), evaluates the level's key table (0x916e10 for level 1, 0x916e40 otherwise) at the pop
//     time and the lift curve at the lift time (0x2c6dc0), fills the object from the anchor record (0x2c69a0),
//     scales the quad (0x2708b0), offsets it, and appends it to list B (0x3f1290).
// Ivalice Together drew its own copy of that sprite (cropped from battle_4_p) in an overlay window over P2/P3.
// Legacy uses the game's own drawing instead, once more per guest: IX-21 is a call row on the drawer's only call
// (0x2c6352). After P1's prompt is drawn (CALL_AROUND), the after-handler appends one object per guest whose own
// interaction scan (the private pass of IX-01) found something, with the same frame, key tables, timing and
// offsets, anchored to that guest's overhead record, and for a usable level the same label from a text node of our
// own (one per guest, built the way the setter builds P1's, appended to list D). Nothing of the game's state is written: the pool and list B
// are the frame's own (reset every frame), and every time and level lives in our game-thread state.
//
// Who gets one (the co-op thread composes the record each beat, interaction.cpp):
//   * a guest seat in session, manual (never an AI-controlled member), alive, steering its character, its battle
//     menu not open, its character not the controlled actor;
//   * Play > "Guests use doors, chests, NPCs and shops" On (options::guest_interactions) and guests may interact now
//     (GATE_INTERACTION): the same conditions under which the private passes run;
//   * no guest interaction in flight or held (a request or a dialog/shop hold), no native modal;
//   * what the guest's private pass found this frame (doors, chests, crystals, switches, and the proximity "!" of
//     NPCs the actor update publishes), as level_of(state); a usable level drops to the "!" while the guest could not
//     use it now (shown_level), as the native widget does for P1.
// Player 1 keeps the game's own prompt, untouched.
//
// Threads. Co-op thread: publish() a small record (seqlock). Game thread: the IX-21 handler copies it (fail_open),
// decides, and its after-handler emits; each guest's animation state is game-thread only. Nothing blocks, allocates
// or calls the C runtime. A full pool stops emission for the frame (P1's prompt was drawn first).
// Subsystem: MOVEMENT (guest interactions). IX-21 is OPTIONAL: if another mod rewrote the call, no guest prompt.
#pragma once

#include <stdint.h>

#include "interaction/interaction.h"
#include "patch/detour.h"

namespace ilc::interaction::prompt {

// ---- game facts (VAs at the pinned base 0x120000) ----------------------------------------------------------------
constexpr uint64_t PINNED_BASE = 0x120000;
constexpr uint64_t SITE_PROMPT_CALL = 0x2C6352;    // IX-21: `call 0x2c7f90` in the overhead pass (its only caller)
constexpr uint64_t FN_PROMPT_DRAW = 0x2C7F90;      // P1's prompt drawer (manager, widget, list B, pool, list D)
constexpr uint64_t FN_OBJECT_ALLOC = 0x3F0040;     // obj* (pool*): null when count >= capacity
constexpr uint64_t FN_LIST_APPEND = 0x3F1290;      // void (list*, obj*)
constexpr uint64_t FN_OVERHEAD_SPRITE = 0x2C69A0;  // void (record*, obj*, frame*, s16 alpha, u32 rgba, texture*)
constexpr uint64_t FN_KEY_EVAL = 0x2C6DC0;         // void (out[6], keys*, i32 count, i32 time)
constexpr uint64_t FN_VEC_SCALE = 0x2708B0;        // void (float dst[4], const float src[4], float s): xyz scaled
constexpr uint64_t FN_BATTLE_TEXTURE = 0x35D3D0;   // texture* (): [[0x209ac30] + 0x350], battle_4_p
constexpr uint64_t UI_ATLASES = 0x209AC30;         // VA: pointer to the UI texture entries
constexpr uint32_t ATLAS_BATTLE = 0x350;           // battle_4_p's texture entry
constexpr uint64_t KEYS_OUTER = 0x916E10;          // .rdata: level 1 pop, 5 keys of 8 bytes
constexpr uint64_t KEYS_INNER = 0x916E40;          // .rdata: levels 2/3 bob, 10 keys
constexpr uint32_t KEYS_OUTER_COUNT = 5;
constexpr uint32_t KEYS_INNER_COUNT = 10;
constexpr uint64_t FRAME_DELTA = 0x916F00;         // .rdata: s8 per level 1..3 past the sheet's first frame (1, 0, 2)
constexpr uint64_t LIFT_TARGET = 0x1E0CF38;        // .data: s8, the lift and label curves' end (-17 in the file)
constexpr uint64_t UI_HEIGHT = 0x1E0C1DC;          // .data: i32; ui_scale = it / 448.0 (0x24aba0)
constexpr float UI_HEIGHT_REF = 448.0f;            // NOTADDR: 0x8c5b44
// The drawer moves the marker by ui_scale * label.y + lift.y + pop.y and the label by ui_scale * label.y + lift.y.

// The label (the "A Open" part: the confirm button glyph and the object's name), built by the widget setter 0x2c64c0
// into a text node (the widget itself; text at +0x130) and appended by the drawer to list D [verified static].
constexpr uint64_t FN_MESSAGE = 0x2F9860;          // char* (u32 id): system message lookup
constexpr uint32_t LABEL_MESSAGE = 0x46D8;         // NOTADDR: the label's prefix message id
constexpr uint64_t BUTTON_CONFIG = 0x1E0CED4;      // .data: u32, P1's confirm-button glyph setting
constexpr uint8_t GLYPH_CODE = 0x0F;               // the button-glyph control code, then '<' and two bytes
constexpr uint8_t GLYPH_KIND = 0x3C;
constexpr uint64_t FN_TEXT_INIT = 0x2B3400;        // void (node*, text*, resource): node defaults + text + font kind
constexpr uint64_t FN_TEXT_MEASURE = 0x29CD00;     // void (text*, u64 0, float scale, i32* w, i32* h)
constexpr uint64_t FN_TEXT_APPEND = 0x3F14D0;      // void (list D*, node*)
constexpr uint64_t FN_USABLE = 0x3DF6F0;           // i32 (u32 handle): 0 -> the label is drawn grey
constexpr uint32_t NODE_SIZE = 0x1E0;              // the widget's size; the text node is its start
constexpr uint32_t NODE_TEXT = 0x130;              // node: the label string (0x80 bytes)
constexpr uint32_t LABEL_MAX = 0x80;
constexpr uint32_t NODE_RGBA = 0x30;               // node: u8 r, g, b, a
constexpr uint32_t NODE_ANCHOR = 0xD0;             // node: vec4 anchor (copied from the marker object), w at +0xdc
constexpr uint32_t NODE_OFFSET = 0xF0;             // node: s16 x, y offsets, then u16 width, height
constexpr uint32_t ARG5 = 0x28;                    // at the drawer's entry: [rsp + 0x28] = list D
constexpr uint8_t LABEL_WHITE = 0x80;
constexpr uint8_t LABEL_GREY = 0x40;
constexpr uint64_t FRAME_DIVISOR = 0x1DFE0B4;      // .data: u32, divides every per-frame time step (1 in the file)

constexpr uint32_t OVERHEAD_FADE = 0x1D78;         // manager: s16 fade of every overhead mark (0x80 = full)
constexpr uint32_t OVERHEAD_RECORDS = 0x1D70;      // manager: first overhead record
constexpr uint32_t OVERHEAD_NEXT = 0x18;           // record: next
constexpr uint32_t OVERHEAD_HANDLE = 0x10;         // record: u32 actor handle
constexpr uint32_t OVERHEAD_WALK_MAX = 64;
constexpr uint32_t TEX_RESOURCE = 0x20;            // texture entry: resource; its header is resource + 0x30
constexpr uint32_t RES_HEADER = 0x30;
constexpr uint32_t HDR_FRAMES = 0x10;              // header: u16 offset of the frame table (8-byte frames)
constexpr uint32_t HDR_SHEETS = 0x14;              // header: u16 first frame per sheet
constexpr uint32_t RES_SHEET_INDEX = 0x44;         // resource: u16 sheet index the prompt drawer uses (= header +0x14)
constexpr uint32_t OBJ_QUAD = 0x50;                // world object: f32 x0, y0, x1, y1 (screen offsets)
constexpr uint32_t RGBA_WHITE = 0x80808080;        // NOTADDR: the drawer's colour (0x80 = 1.0 per channel)

// The drawer's time constants (0x2c6be0) and limits.
constexpr uint32_t FADE_STEP = 0x20;
constexpr uint32_t POP_STEP = 0x10;
constexpr uint32_t LIFT_STEP = 0x20;
constexpr uint32_t LABEL_STEP = 0x20;
constexpr uint32_t FADE_FULL = 0x80;
constexpr uint32_t TIME_END = 0x100;
constexpr uint32_t LEVEL_MAX = 3;
constexpr uint64_t ANIM_IDLE_RESET_MS = 250;       // no update for this long (the record was withdrawn): start over

// ---- the record ----------------------------------------------------------------------------------------------------
struct prompt_seat {
    uint32_t actor;    // the guest character's actor handle (its overhead record is found by it)
    uint32_t object;   // what the guest's scan found (a new object pops the marker again)
    uint8_t level;     // 0 none, 1 the "!", 2..3 usable
    uint8_t seat;      // 1..ACTIVE_SEATS-1
    uint16_t reserved;
    uint32_t reserved2;
    uint64_t text;     // the find's publication text (the object's name: a game pointer, read through the probe)
    uint64_t resource; // the find's publication resource (the label's font kind)
};
static_assert(sizeof(prompt_seat) == 32, "prompt_seat layout (host tests build it)");

struct prompt_record {
    patch::record_head head;   // epoch = session epoch
    uint32_t visible;          // bit k: seats[k] has a level
    uint32_t reserved;
    prompt_seat seats[SLOTS];
};
static_assert(SLOTS == 3 && sizeof(prompt_record) == 120, "prompt_record layout (host tests build it)");

// ---- the rules (pure) ----------------------------------------------------------------------------------------------
// The native widget's level for a published state: 1..3 as is; 0 and anything above 3 (a woken mimic's 10) draw none.
uint32_t level_of(uint32_t state);
// A usable level (2/3) drops to the "!" while it could not be used now.
uint32_t shown_level(uint32_t level, bool can_use_now);
// The level a guest sees for its find: level_of(state), dropped to the "!" only for an object that animates P1's
// leader (anything but a chest) while P1's leader is busy. The guest's own actor state is not a condition, as the
// native widget never looks at its owner's action either; the press itself checks it (press_allowed, which takes
// the u16 action at +0x714 and counts "follow the leader", 0x4124, as free).
uint32_t seat_level(uint32_t state, bool chest, bool leader_free);
// The label string the widget setter builds: [message 0x46d8] + {0x0F, '<', glyph byte pair from the button setting}
// + name, at most LABEL_MAX - 1 bytes plus its 0. False (nothing usable) when it would not fit or has no name.
bool label_string(const uint8_t* prefix, uint32_t prefix_len, uint32_t button_config, const uint8_t* name,
                  uint32_t name_len, uint8_t out[LABEL_MAX], uint32_t* out_len);

// One guest's animation, as the native widget keeps it (game thread).
struct anim {
    uint32_t actor;
    uint32_t object;
    uint8_t level;      // drawn level (0 = nothing)
    uint8_t leaving;    // fading out after the record said none
    uint16_t fade;      // 0..FADE_FULL
    uint16_t pop;       // 0..TIME_END: the level's key table time
    uint16_t lift;      // 0..TIME_END: the lift curve time
    uint16_t label;     // 0..TIME_END: the label curve time (rises while a label is wanted, falls otherwise)
    uint16_t reserved;
    uint64_t seen_ms;   // last update (module clock)
};
static_assert(sizeof(anim) == 32, "anim layout (host tests read it)");

// One frame for one guest: take the wanted level (0 = none) as 0x2c64c0 does (a new marker pops from 0, a higher
// level restarts the table, a lower one jumps to its rest), then tick as 0x2c6be0 does (`divisor` from the game, 0
// taken as 1; every step at least 1). `labelled`: a usable level with a name; its label time rises, otherwise it
// falls. Returns true while something is to be drawn.
bool anim_step(anim& a, uint32_t actor, uint32_t object, uint32_t level, bool labelled, uint32_t divisor,
               uint64_t now_ms);

// ---- the game functions IX-21 calls (relocated, or the host test's doubles) ----------------------------------------
struct game_fns {
    uint64_t (*alloc)(uint64_t pool);
    void (*append)(uint64_t list, uint64_t obj);
    void (*sprite)(uint64_t record, uint64_t obj, uint64_t frame, int32_t alpha, uint32_t rgba, uint64_t texture);
    void (*eval)(uint8_t* out, uint64_t keys, int32_t count, int32_t time);
    void (*scale)(float* dst, const float* src, float s);
    uint64_t (*message)(uint32_t id);
    void (*text_init)(uint64_t node, uint64_t text, uint64_t resource);
    void (*measure)(uint64_t text, uint64_t zero, float scale, int32_t* w, int32_t* h);
    void (*text_append)(uint64_t list, uint64_t node);
    int32_t (*usable)(uint32_t handle);
};

// ---- module --------------------------------------------------------------------------------------------------------
// Pins the game bytes IX-21 relies on, then sets its handlers and arms it. Its own: false leaves it stock (no guest
// prompt) and touches nothing else. Called by interaction::attach() after its rows armed. Two groups of pins: the
// marker's (the call's arguments, the drawer, the sprite, key and list functions, the texture, the key tables) decide
// whether IX-21 arms at all; the label's (the setter's button-glyph code, the message and text functions, the usable
// check) only decide whether guests get the button label. Another mod that rewrites the label setter (a button-prompt
// change) costs the label, never the "!" itself.
bool attach(uintptr_t image_base);
bool attached();

struct status_view {
    uint8_t attached;         // handlers set and the row armed (the marker's pins matched)
    uint8_t marker_pins;      // every marker pin matched
    uint8_t label_pins;       // every label pin matched: guests' button labels are drawn
    uint8_t gate_open;        // the patch layer's gate for IX-21 is open now (installed, armed, live, MOVEMENT)
    uint32_t row;             // IX-21's site row
    uint64_t first_mismatch;  // VA of the first pin that did not match (0 = none)
};
status_view status();
// Co-op thread: publish this beat's record (interaction::step composes it); withdraw() on stop or a lost world.
void publish(const prompt_record& r);
void withdraw();

// The handlers (also called by host tests with a synthetic ctx).
int32_t prompt_call(patch::ctx* c, uint32_t epoch);    // IX-21 before the drawer (rcx manager, r8 list B, r9 pool)
void prompt_after(patch::ctx* c, uint32_t epoch);      // IX-21 after it: the guests' markers

struct counters {
    volatile uint64_t published;
    volatile uint64_t passes_wrapped;   // drawer calls run with ours after them
    volatile uint64_t objects;          // markers appended
    volatile uint64_t pool_full;        // frames where the pool ran out before ours were done
    volatile uint64_t no_anchor;        // markers wanted without an overhead record for the guest
    volatile uint64_t no_texture;       // battle_4_p not loaded
    volatile uint64_t labels;           // labels appended
    volatile uint64_t labels_built;     // label nodes (re)built
    volatile uint64_t no_label;         // usable markers without a label (no name, too long, no list D)
};
const counters* stats();

// ---- host tests ----------------------------------------------------------------------------------------------------
void test_reset();
void test_set_image_base(uintptr_t base);
void test_set_fns(const game_fns* fns);    // nullptr = the game's own
void test_set_attached(bool on);
bool test_read(prompt_record* out);
const anim* test_anims();                  // SLOTS entries
const uint8_t* test_node(uint32_t slot);   // the slot's label node (NODE_SIZE bytes)
int32_t test_row();

}  // namespace ilc::interaction::prompt
