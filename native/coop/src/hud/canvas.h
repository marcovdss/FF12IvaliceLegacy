// Ivalice Legacy co-op module: the HUD canvas (hud::canvas) and its frame hook.
//
// The co-op HUD is drawn by the game itself. Once per frame the field/battle HUD tree sends the combat-log manager its
// draw message; the call to the log draw there is the frame hook HD-01 ("H0", a call row). The log draw runs first,
// then the hook replays the latest published canvas frame on the draw context of the manager's top-most ancestor (the
// HUD tree's root space, so the canvas neither slides nor fades with the log window) with the game's own
// functions: text through the native text draw (font, outline, colour, size), sprites through the MRP node batch
// (atlas, page.sheet, cell from the TIM2 eXt tables, per-vertex tint) and a thin bar through the list selection bar.
//
// Threads. The co-op thread builds a frame with `canvas` (plain memory, no game call) and publishes it into a single
// seqlock slot; the game thread copies it out at the hook (a torn copy is skipped, never waited for) and replays it.
// No game function is ever called off the game thread, and the replay never walks game pointers: it reads only the
// frame, the draw context the game handed the hook, the texture registry entries of the atlases it names and the
// render ring's three cursors, all through the fault-safe probe.
//
// Fail-open. The hook draws nothing (the log draw alone runs, stock) unless: RENDER is live, the diagnostics test
// draw is on (the only producer in this build), a frame of this epoch is at most STALE_MS old, the draw context
// is readable and the window not faded out, the UI-ready flag is set, every native function's first bytes were
// checked at attach (and the HD-V* gates kept HD-01 hooked), the guard is installed and the frame fits the budget.
// A fault anywhere in the replay is caught by guard::call: the RENDER subsystem is latched faulted at once and the
// hook is stock from the next frame.
//
// Budget. Text costs heap and CPU only (the font batch); sprites and bars allocate commands in the render ring
// (32 MiB, wraps without failing: an overrun would overwrite commands still in flight). The hook samples the ring's
// cursor on every call (whatever it then draws, so the baseline is never older than one frame), measures the traffic
// between two consecutive calls (a delta over RING_GAP_MS is not one frame's and is not charged, unless the calls keep
// a slow steady cadence: RING_SLOW_MS), keeps a slowly decaying peak (a gap neither charges nor decays it), and
// replays only if the frame's own estimate stays under RING_OURS_MAX and peak + estimate under RING_SAFE (a quarter of
// the ring: half of it with two frames in flight).
// A frame over a limit is not drawn at all (never half a HUD).
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "patch/detour.h"
#include "patch/patch_engine.h"

namespace ilc::hud {

constexpr uint32_t MAX_COMMANDS = 192;
constexpr uint32_t TEXT_POOL = 3072;    // encoded bytes, strings NUL-terminated
constexpr uint32_t TEXT_MAX = 120;      // characters per string
constexpr uint64_t STALE_MS = 250;      // a frame older than this is not drawn
constexpr uint64_t RING_OURS_MAX = 0x80000;   // NOTADDR: 512 KiB of ring per frame for the canvas
constexpr uint64_t RING_SAFE = 0x800000;      // NOTADDR: 8 MiB = a quarter of the ring, peak traffic + ours
constexpr uint64_t RING_GAP_MS = 100;   // a ring delta spanning longer than this is not one frame's traffic ...
constexpr uint64_t RING_SLOW_MS = 500;  // ... unless the hook keeps a slow, steady cadence: up to this long and at
                                        // most twice the previous spacing (a frame rate down to 2 fps, not a load)

enum cmd_kind : uint8_t { CMD_NONE = 0, CMD_TEXT = 1, CMD_SPRITE = 2, CMD_BAR = 3 };

// The HUD atlases the canvas may name (their texture registry names).
enum atlas_id : uint8_t {
    ATLAS_BATTLE_4_C = 0,
    ATLAS_PARTYTOP_4_C,
    ATLAS_PARTYTOP_8_C,
    ATLAS_BATTLE_4_P,
    ATLAS_ICONS_C,
    ATLAS_CURSOL_4_P,
    ATLAS_BT_WIN_0_C,
    ATLAS_BUTTONICON,
    ATLAS_TARGETLINE_P,   // the target line's strip and glow dot (page 0 sheet 4: cell 0 strip, cell 1 dot)
    ATLAS_COUNT
};
const char* atlas_name(uint32_t atlas);

// Vertex colour as the game takes it: 0x80 = 1.0 per channel (PS2 modulate), packed r | g << 8 | b << 16 | a << 24.
struct rgba {
    uint8_t r, g, b, a;
};
constexpr uint32_t pack(rgba c)
{
    return static_cast<uint32_t>(c.r) | static_cast<uint32_t>(c.g) << 8 | static_cast<uint32_t>(c.b) << 16 |
           static_cast<uint32_t>(c.a) << 24;
}
constexpr rgba WHITE = {0x80, 0x80, 0x80, 0x80};
constexpr uint8_t PALETTE_OWN = 0xFF;   // a sprite in its cell's own palette

struct cmd {
    uint8_t kind;      // cmd_kind
    uint8_t atlas;     // SPRITE: atlas_id
    uint8_t font;      // TEXT: font argument of the parameter init (0 = the combat log's)
    uint8_t page;      // SPRITE
    uint8_t sheet;     // SPRITE
    uint8_t palette;   // SPRITE: 0 = the cell's own palette, else 1 + a palette of the cell's CLUT group (0..15)
    uint16_t cell;     // SPRITE
    int16_t x, y;      // the hook window's space: the 1920x1080 authoring units of the HUD layout
    uint16_t w, h;     // SPRITE: display size (0 = the cell's own); BAR: size
    uint16_t scale;    // TEXT: size x100 (100 = native)
    uint16_t text;     // TEXT: offset into the frame's pool
    uint32_t colour;   // packed rgba: TEXT colour, SPRITE tint (all four vertices), BAR colour
};
static_assert(sizeof(cmd) == 24, "cmd layout");

struct frame {
    patch::record_head head;   // epoch = the session epoch it was built for; generation = build counter
    uint64_t built_ms;
    uint32_t count;
    uint32_t text_used;
    cmd cmds[MAX_COMMANDS];
    char text[TEXT_POOL];
};

// The co-op thread's builder. Every call appends or refuses (full, bad argument); refusals are counted and the rest
// of the frame stays valid.
class canvas {
public:
    void begin(uint32_t epoch, uint64_t now_ms);
    // ASCII text, encoded for the game's font; a character the encoding lacks becomes '?'. scale: x100.
    bool text(int16_t x, int16_t y, const char* ascii, rgba colour, uint16_t scale = 100, uint8_t font = 0);
    // palette: PALETTE_OWN, or a palette of the cell's CLUT group the atlas has (the glow dot: 0..6, 6 = white).
    bool sprite(atlas_id atlas, uint8_t page, uint8_t sheet, uint16_t cell, int16_t x, int16_t y, rgba tint = WHITE,
                uint16_t w = 0, uint16_t h = 0, uint8_t palette = PALETTE_OWN);
    bool bar(int16_t x, int16_t y, uint16_t w, uint16_t h, rgba colour);
    const frame& view() const { return f_; }
    uint32_t refused() const { return refused_; }
    // Commands and text bytes kept back for a later pass (the frame driver keeps the open menu windows' floor for the
    // menu pass while the passes before it draw): until lifted (reserve(0, 0)) the canvas refuses what would eat
    // into them. begin() lifts it.
    void reserve(uint32_t commands, uint32_t text);
    uint32_t room() const { return f_.count < cmd_limit_ ? cmd_limit_ - f_.count : 0; }           // commands left
    uint32_t text_room() const { return f_.text_used < text_limit_ ? text_limit_ - f_.text_used : 0; }
    uint32_t reserved_commands() const { return MAX_COMMANDS - cmd_limit_; }
    uint32_t reserved_text() const { return TEXT_POOL - text_limit_; }

private:
    frame f_;
    uint32_t refused_ = 0;
    uint32_t cmd_limit_ = MAX_COMMANDS;
    uint32_t text_limit_ = TEXT_POOL;
};

// ASCII -> the game's single-byte text encoding (the st2e banks' character table). Returns the encoded length (no
// NUL) or -1 if it does not fit `cap` (which must leave room for the NUL); *substituted counts '?' replacements.
int32_t encode(const char* ascii, char* out, uint32_t cap, uint32_t* substituted);

// ---- publication (co-op thread writes, game thread reads) ---------------------------------------------------------
void publish(const frame& f);
void withdraw();
bool latest(frame& out);   // any thread, wait-free; false if none, withdrawn or torn

// Ring cost estimate of a frame (bytes of the render ring its sprites and bars allocate).
uint64_t ring_estimate(const frame& f);

// ---- the module side ---------------------------------------------------------------------------------------------
bool attach(uintptr_t image_base);                  // pins checked, guard installed, HD-01 handler + after-handler
void configure_from_settings(const ilc_settings& s);  // the Diagnostics test draw switch (generation 0 = off)
void step(uint64_t now_ms);                         // co-op thread: the test draw producer
void stop();                                        // withdraw; the hook is stock

bool test_draw_on();

struct counters {
    uint64_t reached;          // the hook ran (gate open)
    uint64_t drawn;            // frames replayed to the end
    uint64_t commands;         // commands replayed
    uint64_t texts, sprites, bars;
    uint64_t declined_off;     // the switch, RENDER or attach said no
    uint64_t declined_frame;   // no frame, torn, other epoch or stale
    uint64_t declined_context; // context unreadable, not the window's, faded out, or UI not ready
    uint64_t declined_budget;  // over a ring limit or the ring unreadable
    uint64_t skipped_commands; // a sprite whose atlas or cell could not be resolved (the rest drawn)
    uint64_t faults;           // guarded faults (RENDER latched)
    uint64_t published;        // frames the producer published
    uint64_t ring_gaps;        // ring samples not one frame after the last: a new baseline, nothing charged or decayed
    // The frame hook's own cost per call (h0 + h0_after, the native log draw between them excluded), in time-stamp
    // counter ticks: the last finished call, the longest, and the sum over `hook_calls` calls (for the average).
    uint64_t hook_ticks_last;
    uint64_t hook_ticks_max;
    uint64_t hook_ticks_total;
    uint64_t hook_calls;
    uint64_t atlas_lookups;    // texture lookups by name (FN_TEXTURE_BY_NAME): the atlas cache's misses
    uint64_t text_last;        // texts replayed on the last frame drawn (the text cost is the game's font batch: CPU)
    uint64_t chars_last;       // ... and their characters
    uint64_t ring_ours_last;   // render-ring bytes the last replay with sprites or bars moved the cursor by
    uint64_t zero_output;      // replays that drew sprites or bars and moved the ring cursor by nothing
    uint64_t child_space;      // frames drawn in the root's child space (its own context had no usable matrix)
    uint64_t declined_matrix;  // frames not drawn: no usable draw matrix (also counted in declined_context)
    uint64_t clips;            // scissor calls of the replay (2 per replayed frame: lift, set back)
};
const counters* stats();

struct budget_view {
    uint64_t capacity;         // ring size seen (end - start)
    uint64_t frame_use;        // ring bytes used between the last two hook calls
    uint64_t peak;             // decaying peak of frame_use
    uint64_t ours;             // estimate of the last frame considered
    uint64_t cursor;           // the ring cursor at the last hook call
    float matrix[16];          // the drawing context's transform at the last hook call (for the coordinate check)
    uint32_t depth;            // ancestors walked from the log manager to the window whose context was used
    uint32_t source;           // where `matrix` came from: matrix_source
};
enum matrix_source : uint32_t {
    MATRIX_CONTEXT = 0,        // the window's own context
    MATRIX_CHILD_SPACE = 1,    // a root's context+0xE0: the matrix it hands its children (hud_facts.h)
    MATRIX_NONE = 2,           // neither usable: the frame was not drawn (`matrix` = the unusable one)
};
budget_view budget();

struct status_view {
    uint8_t attached;          // pins matched, guard installed, handlers set
    uint8_t pins_ok;
    uint8_t guard_ready;
    uint8_t test_draw;
    uint8_t row_hooked;        // HD-01 is hooked (no HD-V gate skipped it)
    uint8_t reserved[3];
};
status_view status();

// The hook itself (also called by host tests with a synthetic ctx).
int32_t h0(patch::ctx* c, uint32_t epoch);
void h0_after(patch::ctx* c, uint32_t epoch);

// Host tests only.
void test_reset();
void test_set_test_draw(bool on);
void test_set_image_base(uintptr_t base);
void test_set_pins_ok(bool ok);
void test_inject_fault(bool on);     // the replay dereferences address 8 before drawing
void test_set_ring(uint64_t start, uint64_t end, uint64_t cursor);  // the ring baseline of the last hook call
int32_t test_replay(const frame& f, uint64_t context);   // the guarded replay on a context, no checks before it
// Stand-ins for the game functions the replay calls (null = the game's own); the atlas cache is dropped either way.
struct test_natives {
    void* text_draw;
    void* text_params;
    void* batch_begin;
    void* batch_sprite;
    void* batch_end;
    void* bar;
    void* texture_by_name;
    void* scissor;
};
void test_set_natives(const test_natives* n);
void test_forget_atlases();
void test_produce(uint32_t epoch, uint64_t now_ms);      // build and publish the test-draw frame
int32_t test_row();

}  // namespace ilc::hud
