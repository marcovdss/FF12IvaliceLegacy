// Ivalice Legacy co-op module: the HUD frame hook HD-01 and the native replay of a canvas frame (see canvas.h).
#include "hud/canvas.h"

#include "hud/battle_log.h"
#include "hud/driver.h"
#include "hud/hud_internal.h"
#include "hud/snapshot.h"
#include "overlay/overlay.h"
#include "patch/site_table.h"
#include "runtime/guard.h"
#include "runtime/probe.h"

#include <x86intrin.h>

namespace ilc::hud {

namespace detail {

counters g_counters = {};

}  // namespace detail

namespace {

using detail::copy_bytes;
using detail::zero_bytes;

constexpr bool same_id(const char* a, const char* b)
{
    while (*a != '\0' && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

constexpr int32_t find_row(const char* id)
{
    for (uint32_t row = 0; row < patch::SITE_COUNT; ++row) {
        if (same_id(patch::SITES[row].id, id)) {
            return static_cast<int32_t>(row);
        }
    }
    return -1;
}

constexpr int32_t ROW_H0 = find_row("HD-01");
static_assert(ROW_H0 >= 0 && patch::SITES[ROW_H0].policy == ILC_SITE_POLICY_PATCH &&
                  patch::SITES[ROW_H0].kind == ILC_SITE_KIND_CALL &&
                  patch::SITES[ROW_H0].subsystem == ILC_SUBSYS_RENDER,
              "the frame hook is a RENDER call row (a call row may call game functions)");

// The native functions the replay calls, each pinned by its HD-V row (checked again at attach).
struct pin {
    const char* row;
    uint64_t va;
};
constexpr pin PINS[] = {{"HD-V1", facts::FN_LOG_DRAW},     {"HD-V2", facts::FN_TEXT_DRAW},
                        {"HD-V3", facts::FN_TEXT_PARAMS},  {"HD-V4", facts::FN_BATCH_BEGIN},
                        {"HD-V5", facts::FN_BATCH_SPRITE}, {"HD-V6", facts::FN_BATCH_END},
                        {"HD-V7", facts::FN_SELECTION_BAR}, {"HD-V8", facts::FN_TEXTURE_BY_NAME},
                        {"HD-V12", facts::FN_SCISSOR}};
constexpr bool pins_match_rows()
{
    for (const pin& p : PINS) {
        const int32_t row = find_row(p.row);
        if (row < 0 || patch::SITES[row].rva != p.va - facts::PINNED_BASE ||
            patch::SITES[row].policy != ILC_SITE_POLICY_GATE || patch::SITES[row].gate != static_cast<uint32_t>(ROW_H0)) {
            return false;
        }
    }
    return true;
}
static_assert(pins_match_rows(), "every native function of the replay has a GATE row on the frame hook");

using text_draw_fn = void (*)(void* context, int32_t x, int32_t y, void* params);
using text_params_fn = void (*)(void* params, const void* string, uint64_t font);
using batch_begin_fn = void (*)(void* batch, void* context, int32_t one);
using batch_sprite_fn = void (*)(void* batch, void* context, uint64_t texture, const uint32_t* colours,
                                 const int16_t* uvwh, int32_t x, int32_t y, int32_t w, int32_t h, int64_t zero);
using batch_end_fn = void (*)(void* batch, void* context);
using bar_fn = void (*)(void* context, int32_t x, int32_t y, int32_t w, int32_t h, const uint32_t* rgba);
using texture_fn = uint64_t (*)(const char* name);
using scissor_fn = void (*)(void* context);

uintptr_t g_base = 0x120000;  // NOTADDR: the pinned image base until attach
volatile uint32_t g_pins_ok = 0;
volatile uint32_t g_attached = 0;
volatile uint32_t g_inject_fault = 0;

uint64_t game(uint64_t va)
{
    return g_base + (va - facts::PINNED_BASE);
}

// Every read of game memory in the frame hook goes through the probe's hot-path cache (probe.h: load_game,
// read_game): one VirtualQuery per read on the game's large regions cost milliseconds per frame (0.1.53, F1/F2).
template <typename T>
bool load(uint64_t address, T* out)
{
    return load_game(out, reinterpret_cast<const void*>(address), sizeof(T));
}

void bump(volatile uint64_t& c)
{
    __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED);
}

// ---- game-thread state (the hook is not re-entered: one game thread draws the UI) --------------------------------
struct pending {
    uint64_t manager;
    uint64_t context;
    uint32_t layer;  // the frame driver's gate said yes this frame (driver.h): the co-op layer is drawn and stamped
    uint32_t reserved;
};
pending g_pending = {};
frame g_frame;                               // the copy being replayed
alignas(16) uint8_t g_pristine[facts::CONTEXT_SIZE];
alignas(16) uint8_t g_work[facts::CONTEXT_SIZE];
alignas(16) uint8_t g_params[facts::PARAMS_SIZE + 0x30];
alignas(16) uint8_t g_batch[0x40];
alignas(16) uint32_t g_colours[4];
alignas(16) int16_t g_uvwh[4];
alignas(16) uint32_t g_bar_rgba[1];
alignas(16) uint8_t g_noclip[facts::CONTEXT_SIZE];   // all zero: CONTEXT_CLIP clear, so the scissor setter lifts the clip
uint64_t g_clip_context = 0;   // the context whose clip the replay sets back (the log manager's, live during HD-01)

struct ring_state {
    uint64_t start, end, cursor;
    uint64_t sampled_ms;  // module clock of the sample (a longer gap than RING_GAP_MS is not one frame's traffic)
    uint64_t spacing_ms;  // its distance to the sample before, when at most RING_SLOW_MS (0 = none: the cadence rule)
    uint32_t valid;
};
ring_state g_ring = {};
budget_view g_budget = {};

// A resolved atlas for this replay: registry entry and the eXt block's readable extent. Looked up afresh on every
// replay, as in 0.1.53 (0.1.55: the 0.1.54 cache across frames could keep a registry entry the game had replaced while
// the old one still looked valid). The lookups are cheap now that the reads around them skip VirtualQuery.
struct atlas_cache {
    uint64_t entry;
    uint64_t ext;        // address of the 'eXt' block
    uint32_t size;       // block size (bounded by the committed region)
    uint16_t frames;     // offsets inside the block
    uint16_t clut_groups;
    uint32_t state;      // 0 unresolved, 1 resolved, 2 failed
};
atlas_cache g_atlas[ATLAS_COUNT];

// The game functions the replay calls (host tests may substitute stand-ins: test_set_natives).
struct natives {
    text_draw_fn text_draw;
    text_params_fn text_params;
    batch_begin_fn batch_begin;
    batch_sprite_fn batch_sprite;
    batch_end_fn batch_end;
    bar_fn bar;
    texture_fn texture_by_name;
    scissor_fn scissor;
};
const test_natives* volatile g_test_natives = nullptr;

natives current_natives()
{
    const test_natives* t = g_test_natives;
    if (t != nullptr) {
        return {reinterpret_cast<text_draw_fn>(t->text_draw),     reinterpret_cast<text_params_fn>(t->text_params),
                reinterpret_cast<batch_begin_fn>(t->batch_begin), reinterpret_cast<batch_sprite_fn>(t->batch_sprite),
                reinterpret_cast<batch_end_fn>(t->batch_end),     reinterpret_cast<bar_fn>(t->bar),
                reinterpret_cast<texture_fn>(t->texture_by_name), reinterpret_cast<scissor_fn>(t->scissor)};
    }
    return {reinterpret_cast<text_draw_fn>(game(facts::FN_TEXT_DRAW)),
            reinterpret_cast<text_params_fn>(game(facts::FN_TEXT_PARAMS)),
            reinterpret_cast<batch_begin_fn>(game(facts::FN_BATCH_BEGIN)),
            reinterpret_cast<batch_sprite_fn>(game(facts::FN_BATCH_SPRITE)),
            reinterpret_cast<batch_end_fn>(game(facts::FN_BATCH_END)),
            reinterpret_cast<bar_fn>(game(facts::FN_SELECTION_BAR)),
            reinterpret_cast<texture_fn>(game(facts::FN_TEXTURE_BY_NAME)),
            reinterpret_cast<scissor_fn>(game(facts::FN_SCISSOR))};
}

void atlas_forget_all()
{
    for (uint32_t k = 0; k < ATLAS_COUNT; ++k) {
        g_atlas[k].state = 0;
    }
}

// ---- sprite tables in the TIM2 eXt block: (atlas, page, sheet, cell) -> registry entry, UV, size and CLUT -------
bool ext_read(const atlas_cache& a, uint32_t offset, void* out, uint32_t n)
{
    if (offset > a.size || n > a.size - offset) {
        return false;
    }
    return copy_from_checked(out, reinterpret_cast<const void*>(a.ext + offset), n);
}

bool resolve_atlas(uint32_t id, texture_fn by_name)
{
    atlas_cache& a = g_atlas[id];
    if (a.state != 0) {
        return a.state == 1;
    }
    a.state = 2;
    bump(detail::g_counters.atlas_lookups);
    const uint64_t entry = by_name(atlas_name(id));
    uint64_t picture = 0;
    if (entry == 0 || !load(entry + facts::TEXTURE_PICTURE, &picture) || picture == 0) {
        return false;
    }
    const uint64_t ext = picture + facts::PICTURE_USER;
    uintptr_t begin = 0;
    uintptr_t end = 0;
    uint32_t head[6] = {};
    if (!game_region(reinterpret_cast<const void*>(ext), &begin, &end) || ext + sizeof(head) > end ||
        !copy_from_checked(head, reinterpret_cast<const void*>(ext), sizeof(head)) || head[0] != facts::EXT_MAGIC) {
        return false;
    }
    const uint64_t room = end - ext;
    a.entry = entry;
    a.ext = ext;
    a.size = static_cast<uint32_t>(head[1] < room ? head[1] : room);
    a.frames = static_cast<uint16_t>(head[4] & 0xFFFF);
    a.clut_groups = static_cast<uint16_t>(head[4] >> 16);
    a.state = 1;
    return true;
}

struct sprite_frame {
    uint64_t entry;
    int16_t u, v, w, h;
    uint8_t clut;
};

bool resolve_sprite(const cmd& c, sprite_frame* out, texture_fn by_name)
{
    if (c.atlas >= ATLAS_COUNT || !resolve_atlas(c.atlas, by_name)) {
        return false;
    }
    const atlas_cache& a = g_atlas[c.atlas];
    uint16_t sheets = 0;
    uint16_t first = 0;
    uint16_t u = 0;
    uint16_t v = 0;
    uint32_t packed = 0;
    if (!ext_read(a, facts::EXT_PAGES + 2u * c.page, &sheets, 2) ||
        !ext_read(a, facts::EXT_PAGES + 2u * (static_cast<uint32_t>(sheets) + c.sheet), &first, 2)) {
        return false;
    }
    const uint32_t at = a.frames + 8u * (static_cast<uint32_t>(first) + c.cell);
    if (!ext_read(a, at, &u, 2) || !ext_read(a, at + 2, &v, 2) || !ext_read(a, at + 4, &packed, 4)) {
        return false;
    }
    const uint32_t palette = packed >> 24;
    uint8_t clut_base = 0;
    if (!ext_read(a, static_cast<uint32_t>(a.clut_groups) + (palette >> 4), &clut_base, 1)) {
        return false;
    }
    out->entry = a.entry;
    out->u = static_cast<int16_t>(u);
    out->v = static_cast<int16_t>(v);
    out->w = static_cast<int16_t>(packed & 0xFFF);
    out->h = static_cast<int16_t>((packed >> 12) & 0xFFF);
    // A command's palette override stays inside the cell's own CLUT group (0 = the cell's own palette).
    const uint32_t within = c.palette != 0 ? static_cast<uint32_t>(c.palette - 1) & 0xF : palette & 0xF;
    out->clut = static_cast<uint8_t>(clut_base + within);
    return out->w > 0 && out->h > 0;
}

// ---- the replay (inside guard::call) ------------------------------------------------------------------------------
struct replay_job {
    const frame* f;
    uint64_t drawn;
    uint64_t texts, sprites, bars, skipped;
    uint64_t chars;     // characters of the texts drawn
    uint64_t clips;     // scissor calls (the lift and the restore)
    uint32_t finished;  // the replay reached its end (stored after the last game call)
};

void reset_work()
{
    copy_bytes(g_work, g_pristine, sizeof(g_work));
}

void replay(void* arg)
{
    replay_job& job = *static_cast<replay_job*>(arg);
    if (__atomic_load_n(&g_inject_fault, __ATOMIC_RELAXED) != 0) {
        asm volatile("movq 8, %%rax" : : : "rax", "memory");  // a load from address 8: the fault drill
    }
    const natives n = current_natives();
    const auto text_draw = n.text_draw;
    const auto text_params = n.text_params;
    const auto batch_begin = n.batch_begin;
    const auto batch_sprite = n.batch_sprite;
    const auto batch_end = n.batch_end;
    const auto bar = n.bar;
    atlas_forget_all();  // every atlas is looked up again in each replay (see atlas_cache)
    // The clip: the log manager's rect is the render scissor while HD-01 runs (hud_facts.h, HD-V12). Lifted for the
    // canvas, which covers the whole HUD, and set back from the log manager's own context afterwards.
    const uint64_t clip_back = g_clip_context;
    if (clip_back != 0 && n.scissor != nullptr) {
        n.scissor(g_noclip);
        ++job.clips;
    }
    const frame& f = *job.f;
    uint32_t in_batch = 0;  // sprites in the open batch (0 = none open)
    const auto close_batch = [&]() {
        if (in_batch != 0) {
            batch_end(g_batch, g_work);
            in_batch = 0;
        }
    };
    for (uint32_t k = 0; k < f.count && k < MAX_COMMANDS; ++k) {
        const cmd& c = f.cmds[k];
        if (c.kind == CMD_TEXT) {
            close_batch();
            if (c.text >= f.text_used) {
                ++job.skipped;
                continue;
            }
            reset_work();
            *reinterpret_cast<uint32_t*>(g_work + facts::CONTEXT_COLOUR) = c.colour;
            text_params(g_params, reinterpret_cast<const void*>(game(facts::EMPTY_STRING)), c.font);
            *reinterpret_cast<const char**>(g_params + facts::PARAMS_STRING) = f.text + c.text;
            if (c.scale != 100) {
                *reinterpret_cast<float*>(g_params + facts::PARAMS_SCALE) = static_cast<float>(c.scale) / 100.0f;
            }
            text_draw(g_work, c.x, c.y, g_params);
            ++job.texts;
            for (uint32_t at = c.text; at < f.text_used && f.text[at] != '\0'; ++at) {
                ++job.chars;
            }
        } else if (c.kind == CMD_SPRITE) {
            sprite_frame s;
            if (!resolve_sprite(c, &s, n.texture_by_name)) {
                ++job.skipped;
                continue;
            }
            if (in_batch == facts::BATCH_SPRITES) {
                close_batch();
            }
            if (in_batch == 0) {
                reset_work();
                zero_bytes(g_batch, sizeof(g_batch));
                batch_begin(g_batch, g_work, 1);
            }
            g_work[facts::CONTEXT_CLUT] = s.clut;
            for (uint32_t v = 0; v < 4; ++v) {
                g_colours[v] = c.colour;
            }
            g_uvwh[0] = s.u;
            g_uvwh[1] = s.v;
            g_uvwh[2] = s.w;
            g_uvwh[3] = s.h;
            batch_sprite(g_batch, g_work, s.entry, g_colours, g_uvwh, c.x, c.y, c.w != 0 ? c.w : s.w,
                         c.h != 0 ? c.h : s.h, 0);
            ++in_batch;
            ++job.sprites;
        } else if (c.kind == CMD_BAR) {
            close_batch();
            reset_work();
            g_bar_rgba[0] = c.colour;
            bar(g_work, c.x, c.y, c.w, c.h, g_bar_rgba);
            ++job.bars;
        } else {
            ++job.skipped;
            continue;
        }
        ++job.drawn;
    }
    close_batch();
    if (clip_back != 0 && n.scissor != nullptr) {
        n.scissor(reinterpret_cast<void*>(clip_back));
        ++job.clips;
    }
    job.finished = 1;
}

// ---- budget: the render ring's traffic between two frames ----------------------------------------------------------
// Sampled once on every hook call while attached, whatever the frame then draws (an empty layer, the stock log,
// nothing), so the delta is always about one frame's traffic. A baseline from a call more than RING_GAP_MS ago (the
// HUD tree did not draw in between: a load, a movie, the main menu) is not charged to this frame: the delta is
// dropped and this sample is the baseline from here. The exception is a slow frame rate: calls at most RING_SLOW_MS
// apart and at most twice the spacing before are consecutive frames (under 10 fps every delta would otherwise be a
// gap and the budget would measure nothing). The peak decays once per charged sample; a gap leaves it as it was.
void ring_sample(uint64_t now)
{
    uint64_t arena = 0;
    uint64_t start = 0;
    uint64_t cursor = 0;
    uint64_t end = 0;
    if (!load(game(facts::RENDER_ARENA), &arena) || arena == 0 || !load(arena + facts::ARENA_START, &start) ||
        !load(arena + facts::ARENA_CURSOR, &cursor) || !load(arena + facts::ARENA_END, &end) || end <= start ||
        end - start > 0x40000000ull || cursor < start || cursor > end) {  // NOTADDR: 1 GiB sanity bound
        g_ring.valid = 0;
        return;
    }
    const uint64_t capacity = end - start;
    uint64_t used = 0;
    uint64_t spacing = 0;
    bool charged = false;
    if (g_ring.valid != 0 && g_ring.start == start && g_ring.end == end) {
        const uint64_t dt = now >= g_ring.sampled_ms ? now - g_ring.sampled_ms : ~uint64_t{0};
        const bool steady = dt <= RING_SLOW_MS && g_ring.spacing_ms != 0 && dt <= 2 * g_ring.spacing_ms;
        spacing = dt <= RING_SLOW_MS ? dt : 0;
        if (dt <= RING_GAP_MS || steady) {
            used = cursor >= g_ring.cursor ? cursor - g_ring.cursor : (end - g_ring.cursor) + (cursor - start);
            charged = true;
        } else {
            bump(detail::g_counters.ring_gaps);  // not one frame: nothing charged, the baseline restarts here
        }
    }
    g_ring = {start, end, cursor, now, spacing, 1};
    if (charged) {
        const uint64_t decayed = g_budget.peak - g_budget.peak / 32;
        g_budget.peak = used > decayed ? used : decayed;
    }
    g_budget.capacity = capacity;
    g_budget.frame_use = used;
    g_budget.cursor = cursor;
}

// Once per hook call (h0 clears the flag): at the draw decision when the frame gets that far (after the native log
// draw for a call-around), otherwise before the hook hands the log draw back to the game.
uint32_t g_sampled = 0;
void sample_once()
{
    if (g_sampled == 0 && __atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) != 0) {
        g_sampled = 1;
        ring_sample(patch::now_ms());  // fault-safe reads only
    }
}

// This frame's sample (sample_once) against the limits.
bool ring_allows(uint64_t ours)
{
    g_budget.ours = ours;
    if (g_ring.valid == 0) {
        return false;
    }
    const uint64_t capacity = g_ring.end - g_ring.start;
    const uint64_t safe = capacity / 4 < RING_SAFE ? capacity / 4 : RING_SAFE;
    return ours <= RING_OURS_MAX && g_budget.peak + ours <= safe;
}

// What the game's batch and bar draws put into the render ring for this replay: the cursor now against the sample
// taken just before it (sample_once). Sprites and bars allocate there as they are drawn, so a replay that drew some
// and moved the cursor by nothing reached the game's functions without output (counted; the boot logs a warning). A
// diagnostic reading only: nothing is decided on it. Text uses the font batch and is not seen here.
void note_output(uint64_t ring_draws)
{
    if (ring_draws == 0 || g_ring.valid == 0) {
        return;
    }
    uint64_t arena = 0;
    uint64_t cursor = 0;
    if (!load(game(facts::RENDER_ARENA), &arena) || arena == 0 || !load(arena + facts::ARENA_CURSOR, &cursor) ||
        cursor < g_ring.start || cursor > g_ring.end) {
        return;
    }
    const uint64_t before = g_ring.cursor;
    const uint64_t used = cursor >= before ? cursor - before : (g_ring.end - before) + (cursor - g_ring.start);
    __atomic_store_n(&detail::g_counters.ring_ours_last, used, __ATOMIC_RELAXED);
    if (used == 0) {
        bump(detail::g_counters.zero_output);
    }
}

// A draw matrix that maps the layout somewhere: every element finite, and a 2D part that is not singular (an all-zero
// matrix draws everything at one point).
bool matrix_usable(const float m[16])
{
    for (uint32_t k = 0; k < 16; ++k) {
        uint32_t bits;
        copy_bytes(&bits, &m[k], sizeof(bits));
        if ((bits & 0x7F800000u) == 0x7F800000u) {   // NOTADDR: an infinity or a NaN
            return false;
        }
    }
    const float det = m[0] * m[5] - m[1] * m[4];
    return det > 1e-6f || det < -1e-6f;
}

// The frame drawn on this hook call, or the reason it is not. True when the co-op layer was presented (replayed to
// its end, or empty).
bool draw_frame(uint32_t epoch)
{
    sample_once();  // before any reason not to draw: an empty or refused frame still moves the baseline
    const uint64_t now = patch::now_ms();
    bool layer = g_pending.layer != 0;
    const bool test = test_draw_on() && latest(g_frame) && g_frame.head.epoch == epoch && g_frame.count != 0 &&
                      now <= g_frame.built_ms + STALE_MS && g_frame.built_ms <= now + STALE_MS;
    if (!layer && !test) {
        bump(detail::g_counters.declined_frame);
        return false;
    }
    uint64_t bound = 0;
    uint32_t ready = 0;
    int16_t alpha = 0;
    if (!load(g_pending.manager + facts::WINDOW_CONTEXT, &bound) || bound != g_pending.context) {
        bump(detail::g_counters.declined_context);
        return false;
    }
    // The log manager is a child of the log background window, which slides and fades with the log. Draw in the
    // top-most ancestor's context instead (the HUD tree's root space): a window's context stays set at +0x38 while
    // its children draw, so every ancestor of the window drawing now has a live one.
    uint64_t window = g_pending.manager;
    uint64_t context = g_pending.context;
    uint32_t depth = 0;
    bool root = false;   // the walk ended on a window with no parent (a root of the HUD tree)
    for (; depth < facts::ANCESTORS_MAX; ++depth) {
        uint64_t parent = 0;
        uint64_t parent_context = 0;
        if (!load(window + facts::WINDOW_PARENT, &parent)) {
            break;
        }
        if (parent == 0) {
            root = true;
            break;
        }
        if (!load(parent + facts::WINDOW_CONTEXT, &parent_context) || parent_context == 0) {
            break;
        }
        window = parent;
        context = parent_context;
    }
    g_budget.depth = depth;
    if (!read_game(g_pristine, reinterpret_cast<const void*>(context), sizeof(g_pristine)) ||
        !load(game(facts::UI_READY), &ready) || ready == 0) {
        bump(detail::g_counters.declined_context);
        return false;
    }
    // The draw matrix. A context's own matrix is used when it is usable; a root's context has none (hud_facts.h:
    // CONTEXT_CHILD_SPACE), so the root space is the matrix the root hands its children. A frame with no usable matrix
    // is not drawn at all (everything at scale 0 would be invisible while the menus still took input).
    float m[16];
    copy_bytes(m, g_pristine, sizeof(m));
    g_budget.source = MATRIX_CONTEXT;
    if (!matrix_usable(m)) {
        float world[16];
        if (root && read_game(world, reinterpret_cast<const void*>(context + facts::CONTEXT_CHILD_SPACE), sizeof(world)) &&
            matrix_usable(world)) {
            copy_bytes(g_pristine, world, sizeof(world));
            g_budget.source = MATRIX_CHILD_SPACE;
            bump(detail::g_counters.child_space);
        } else {
            copy_bytes(g_budget.matrix, m, sizeof(g_budget.matrix));
            g_budget.source = MATRIX_NONE;
            bump(detail::g_counters.declined_matrix);
            bump(detail::g_counters.declined_context);
            return false;
        }
    }
    copy_bytes(&alpha, g_pristine + facts::CONTEXT_ALPHA, sizeof(alpha));
    copy_bytes(g_budget.matrix, g_pristine, sizeof(g_budget.matrix));
    if (alpha <= 0) {
        bump(detail::g_counters.declined_context);
        return false;
    }
    // The co-op layer (driver.h): the element passes compose this frame from the snapshot, the Diagnostics test frame
    // (if any) underneath them. Without a coherent snapshot only the test frame is drawn, and nothing is stamped.
    const frame* f = test ? &g_frame : nullptr;
    if (layer) {
        const frame* composed = nullptr;
        const int32_t c = driver::compose(epoch, now, f, &composed);
        if (c == driver::COMPOSE_FAULTED) {
            bump(detail::g_counters.faults);
            patch::fault_subsystem(ILC_SUBSYS_RENDER);  // stock from the next frame; the stamp is withdrawn
            return false;
        }
        if (c == driver::COMPOSE_OK) {
            f = composed;
        } else {
            layer = false;
        }
    }
    if (f == nullptr) {
        bump(detail::g_counters.declined_frame);
        return false;
    }
    if (f->count == 0) {
        if (layer) {
            driver::presented(now);  // an empty layer (no menu open, nothing to show) is presented too
        }
        return layer;
    }
    if (!ring_allows(ring_estimate(*f))) {
        bump(detail::g_counters.declined_budget);  // nothing drawn, so the menu layer was not presented either
        return false;
    }
    g_clip_context = g_pending.context;   // the context handed to the log draw: the clip in force now
    replay_job job = {f, 0, 0, 0, 0, 0, 0, 0, 0};
    const int32_t r = guard::call(replay, &job);
    g_clip_context = 0;
    if (r == guard::FAULTED) {
        bump(detail::g_counters.faults);
        patch::fault_subsystem(ILC_SUBSYS_RENDER);  // stock from the next frame
        driver::faulted();                          // and the co-op menus idle from the next beat
        atlas_forget_all();                         // nothing cached survives a fault
        return false;
    }
    if (r != guard::RETURNED || job.finished == 0) {
        bump(detail::g_counters.declined_off);
        return false;
    }
    __atomic_add_fetch(&detail::g_counters.commands, job.drawn, __ATOMIC_RELAXED);
    __atomic_add_fetch(&detail::g_counters.texts, job.texts, __ATOMIC_RELAXED);
    __atomic_add_fetch(&detail::g_counters.sprites, job.sprites, __ATOMIC_RELAXED);
    __atomic_add_fetch(&detail::g_counters.bars, job.bars, __ATOMIC_RELAXED);
    __atomic_add_fetch(&detail::g_counters.skipped_commands, job.skipped, __ATOMIC_RELAXED);
    __atomic_store_n(&detail::g_counters.text_last, job.texts, __ATOMIC_RELAXED);
    __atomic_store_n(&detail::g_counters.chars_last, job.chars, __ATOMIC_RELAXED);
    __atomic_add_fetch(&detail::g_counters.clips, job.clips, __ATOMIC_RELAXED);
    note_output(job.sprites + job.bars);
    bump(detail::g_counters.drawn);
    if (layer) {
        driver::presented(now);
    }
    return layer;
}

bool armed()
{
    return __atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) != 0 && guard::ready() &&
           patch::subsystem_live(ILC_SUBSYS_RENDER);
}

}  // namespace

namespace {

// ---- the frame hook's own cost ------------------------------------------------------------------------------------
// The time spent in h0 and h0_after (our work and the game's draw calls the replay makes; the native log draw in
// between excluded), summed per hook call and published when the next call starts: last, max, and a total for the
// average. Time-stamp counter ticks; the counters record turns them into microseconds.
uint64_t g_hook_ticks = 0;   // this call so far
uint32_t g_hook_open = 0;    // a call is being summed

void hook_call_starts()
{
    if (g_hook_open != 0) {
        const uint64_t spent = g_hook_ticks;
        __atomic_store_n(&detail::g_counters.hook_ticks_last, spent, __ATOMIC_RELAXED);
        if (spent > __atomic_load_n(&detail::g_counters.hook_ticks_max, __ATOMIC_RELAXED)) {
            __atomic_store_n(&detail::g_counters.hook_ticks_max, spent, __ATOMIC_RELAXED);  // one writer: the game thread
        }
        __atomic_add_fetch(&detail::g_counters.hook_ticks_total, spent, __ATOMIC_RELAXED);
        __atomic_add_fetch(&detail::g_counters.hook_calls, 1, __ATOMIC_RELAXED);
    }
    g_hook_ticks = 0;
    g_hook_open = 1;
}

int32_t h0_body(patch::ctx* c, uint32_t epoch);
void h0_after_body(patch::ctx* c, uint32_t epoch);

}  // namespace

// ---- the hook -------------------------------------------------------------------------------------------------------
int32_t h0(patch::ctx* c, uint32_t epoch)
{
    const uint64_t started = __rdtsc();
    hook_call_starts();
    const int32_t r = h0_body(c, epoch);
    g_hook_ticks += __rdtsc() - started;
    return r;
}

void h0_after(patch::ctx* c, uint32_t epoch)
{
    const uint64_t started = __rdtsc();
    h0_after_body(c, epoch);
    g_hook_ticks += __rdtsc() - started;
}

namespace {

// HD-01, call row: registers at the log draw's entry (rcx = log manager, rdx = its draw context). The log draw always
// runs; only when a canvas may be drawn (the co-op layer this frame, or the Diagnostics test draw) is it called
// around, so the after-handler draws on top of the native log.
int32_t h0_body(patch::ctx* c, uint32_t epoch)
{
    bump(detail::g_counters.reached);
    overlay::note_hud(c->rcx, c->rdx);   // the overlay's HUD heartbeat: the HUD tree draws this frame
    g_sampled = 0;  // the ring is sampled once per call, whatever the call then does (sample_once)
    const bool ok = armed() && c->rcx != 0 && c->rdx != 0;
    const bool layer = ok && driver::frame_gate(epoch, patch::now_ms());
    // Our overlay's battle log was presented within the last frames: the stock log text draw is skipped at this call
    // (the heartbeat above is already noted, and the log windows keep their draw bit, so HD-01 keeps running).
    if (ok && !layer && !test_draw_on() && overlay::log_replaces_native(patch::now_ms())) {
        sample_once();
        battle_log::note_native_hidden();
        c->resume = game(facts::LOG_DRAW_RETURN);
        return patch::RESUME;
    }
    if (!ok || (!layer && !test_draw_on())) {
        sample_once();
        bump(detail::g_counters.declined_off);
        patch::note_declined(static_cast<uint32_t>(ROW_H0));
        return patch::RUN_ORIGINAL;
    }
    g_pending.manager = c->rcx;
    g_pending.context = c->rdx;
    g_pending.layer = layer ? 1u : 0u;
    if (layer && battle_log::replaces_native(epoch, patch::now_ms())) {
        // The co-op battle log replaces the native one: the layer is drawn here, at the same point in the HUD tree,
        // and the native log draw is skipped. If the layer cannot be drawn this frame, the native log draws instead.
        if (draw_frame(epoch)) {
            battle_log::note_native_hidden();
            c->resume = game(facts::LOG_DRAW_RETURN);
            return patch::RESUME;
        }
        return patch::RUN_ORIGINAL;
    }
    return patch::CALL_AROUND;
}

void h0_after_body(patch::ctx* c, uint32_t epoch)
{
    (void)c;
    if (!armed() || (g_pending.layer == 0 && !test_draw_on())) {
        sample_once();
        bump(detail::g_counters.declined_off);
        return;
    }
    draw_frame(epoch);
}

}  // namespace

// ---- attach and views -----------------------------------------------------------------------------------------------
bool attach(uintptr_t image_base)
{
    g_base = image_base;
    driver::attach(image_base);
    bool pins = true;
    for (const pin& p : PINS) {
        const patch::site& s = patch::SITES[find_row(p.row)];
        uint8_t bytes[32] = {};
        pins = pins && s.length <= sizeof(bytes) &&
               copy_from_caller(bytes, reinterpret_cast<const void*>(game(p.va)), s.length);
        for (uint32_t k = 0; pins && k < s.length; ++k) {
            pins = bytes[k] == static_cast<uint8_t>(s.expected[k]);
        }
    }
    __atomic_store_n(&g_pins_ok, pins ? 1u : 0u, __ATOMIC_RELEASE);
    const bool guarded = guard::attach();
    const uint32_t row = static_cast<uint32_t>(ROW_H0);
    bool ok = pins && guarded && patch::set_handler(row, h0) && patch::set_after(row, h0_after) &&
              patch::set_site_armed(row, true);
    if (!ok) {
        patch::set_site_armed(row, false);
    }
    __atomic_store_n(&g_attached, ok ? 1u : 0u, __ATOMIC_RELEASE);
    return ok;
}

const counters* stats()
{
    return &detail::g_counters;
}

budget_view budget()
{
    return g_budget;
}

status_view status()
{
    status_view v = {};
    v.attached = __atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) != 0 ? 1 : 0;
    v.pins_ok = __atomic_load_n(&g_pins_ok, __ATOMIC_ACQUIRE) != 0 ? 1 : 0;
    v.guard_ready = guard::ready() ? 1 : 0;
    v.test_draw = test_draw_on() ? 1 : 0;
    v.row_hooked = patch::trampoline(static_cast<uint32_t>(ROW_H0)) != nullptr ? 1 : 0;
    return v;
}

// ---- host tests -------------------------------------------------------------------------------------------------------
void test_reset()
{
    zero_bytes(const_cast<counters*>(&detail::g_counters), sizeof(counters));
    g_budget = budget_view{};
    g_ring = ring_state{};
    g_sampled = 0;
    g_pending = pending{};
    g_hook_ticks = 0;
    g_hook_open = 0;
    g_test_natives = nullptr;
    atlas_forget_all();
    __atomic_store_n(&g_inject_fault, 0u, __ATOMIC_RELAXED);
    withdraw();
    test_set_test_draw(false);
    driver::test_reset();
    snapshot::test_reset();
}

void test_set_image_base(uintptr_t base)
{
    g_base = base;
    driver::attach(base);
}

void test_set_pins_ok(bool ok)
{
    __atomic_store_n(&g_attached, ok ? 1u : 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_pins_ok, ok ? 1u : 0u, __ATOMIC_RELEASE);
}

void test_inject_fault(bool on)
{
    __atomic_store_n(&g_inject_fault, on ? 1u : 0u, __ATOMIC_RELAXED);
}

void test_set_natives(const test_natives* n)
{
    g_test_natives = n;
    atlas_forget_all();
}

void test_forget_atlases()
{
    atlas_forget_all();
}

void test_set_ring(uint64_t start, uint64_t end, uint64_t cursor)
{
    g_ring = {start, end, cursor, patch::now_ms(), 0, 1};
}

int32_t test_replay(const frame& f, uint64_t context)
{
    if (!copy_from_caller(g_pristine, reinterpret_cast<const void*>(context), sizeof(g_pristine))) {
        return -1;
    }
    g_clip_context = 0;   // no hook call around it: nothing to lift or set back
    replay_job job = {&f, 0, 0, 0, 0, 0, 0, 0, 0};
    const int32_t r = guard::call(replay, &job);
    if (r == guard::FAULTED) {
        bump(detail::g_counters.faults);
        patch::fault_subsystem(ILC_SUBSYS_RENDER);
        atlas_forget_all();
    }
    return r;
}

int32_t test_row()
{
    return ROW_H0;
}

}  // namespace ilc::hud
