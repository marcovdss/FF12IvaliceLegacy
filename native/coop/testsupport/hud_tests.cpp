// Ivalice Legacy co-op module, host tests only: the HUD canvas, its frame hook and the guard (ILCT_Hud*).
#include <windows.h>

#include <string.h>

#include "hud/canvas.h"
#include "hud/driver.h"
#include "hud/hud_facts.h"
#include "hud/snapshot_facts.h"
#include "runtime/guard.h"
#include "runtime/probe.h"

using namespace ilc;

namespace {

hud::canvas g_test_canvas;

// Guard drills. Each keeps values in non-volatile registers across the call so a resumed fault that failed to
// restore them would show.
volatile uint64_t g_guard_trace = 0;

void guard_returns(void* arg)
{
    g_guard_trace = reinterpret_cast<uint64_t>(arg);
}

void guard_reads_null(void* arg)
{
    g_guard_trace = reinterpret_cast<uint64_t>(arg);
    asm volatile("movq 8, %%rax" : : : "rax", "memory");
    g_guard_trace = 0;
}

void guard_writes_null(void* arg)
{
    (void)arg;
    asm volatile("movq $1, %%rax\n\tmovq %%rax, 16" : : : "rax", "memory");
}

// Clobbers every non-volatile register (as a game function that saved them would have done before faulting), then
// faults: the guard must hand the caller back its own values.
void guard_clobbers_then_faults(void* arg)
{
    (void)arg;
    asm volatile(
        "movq $0x1111, %%rbx\n"
        "movq $0x2222, %%rbp\n"
        "movq $0x3333, %%rsi\n"
        "movq $0x4444, %%rdi\n"
        "movq $0x5555, %%r12\n"
        "movq $0x6666, %%r13\n"
        "movq $0x7777, %%r14\n"
        "movq $0x8888, %%r15\n"
        "pxor %%xmm6, %%xmm6\n"
        "pxor %%xmm15, %%xmm15\n"
        "subq $0x1000, %%rsp\n"
        "movq 8, %%rax\n"
        :
        :
        : "rax", "memory");
}

void guard_nested(void* arg)
{
    *static_cast<int32_t*>(arg) = guard::call(guard_returns, nullptr);
}

// ---- the frame-hook bench: h0 + h0_after on host memory, the game's draw functions stood in for ---------------------
// The world the hook reads lives in one large committed region (the game's heaps are large regions, and VirtualQuery
// walks the whole region), the image facts in an image-sized region: the cost per frame is the module's own work
// (gate, ring sample, ancestors, context, compose, replay loop, atlas checks) plus trivial stand-in calls.
float g_bench_matrix[16];        // the matrix the last stand-in text draw was handed (its context's first 64 bytes)
struct bench_counts {
    uint64_t texts, params, begins, sprites, ends, bars, lookups;
    uint64_t sprites_on_entry;   // sprites drawn with the texture entry the registry names now
};
bench_counts g_bench = {};
uint64_t g_bench_entry = 0;      // what the texture lookup answers (the registry's current entry)
uint64_t g_bench_arena = 0;      // the ring cursors; 0 = the stand-ins add nothing to the ring
uint64_t g_bench_cursor = 0x100000;  // NOTADDR: the ring cursor carried from one bench to the next (no false traffic)

void bench_text_draw(void* context, int32_t, int32_t, void*)
{
    ++g_bench.texts;
    memcpy(g_bench_matrix, context, sizeof(g_bench_matrix));
}
void bench_text_params(void*, const void*, uint64_t) { ++g_bench.params; }
void bench_batch_begin(void*, void*, int32_t) { ++g_bench.begins; }
void bench_ring(uint64_t bytes)
{
    if (g_bench_arena != 0) {
        uint64_t cursor = 0;
        memcpy(&cursor, reinterpret_cast<const void*>(g_bench_arena + hud::facts::ARENA_CURSOR), sizeof(cursor));
        cursor += bytes;
        if (cursor > 0x100000 + hud::facts::ARENA_CAPACITY) {  // NOTADDR: the ring wraps
            cursor = 0x100000 + bytes;
        }
        memcpy(reinterpret_cast<void*>(g_bench_arena + hud::facts::ARENA_CURSOR), &cursor, sizeof(cursor));
    }
}
void bench_batch_sprite(void*, void*, uint64_t texture, const uint32_t*, const int16_t*, int32_t, int32_t, int32_t,
                        int32_t, int64_t)
{
    ++g_bench.sprites;
    if (texture == g_bench_entry) {
        ++g_bench.sprites_on_entry;
    }
    bench_ring(4 * hud::facts::ARENA_VERTEX_BYTES);
}
void bench_batch_end(void*, void*) { ++g_bench.ends; }
void bench_bar(void*, int32_t, int32_t, int32_t, int32_t, const uint32_t*)
{
    ++g_bench.bars;
    bench_ring(3 * (hud::facts::ARENA_BATCH_BYTES + 4 * hud::facts::ARENA_VERTEX_BYTES));
}
uint64_t g_bench_clip_lifts = 0;     // scissor calls with the clip bit clear (lift)
uint64_t g_bench_clip_last = 0;      // the context of the last scissor call
void bench_scissor(void* context)
{
    uint32_t flags = 0;
    memcpy(&flags, static_cast<const uint8_t*>(context) + hud::facts::CONTEXT_FLAGS, sizeof(flags));
    if ((flags & hud::facts::CONTEXT_CLIP) == 0) {
        ++g_bench_clip_lifts;
    }
    g_bench_clip_last = reinterpret_cast<uint64_t>(context);
}
uint64_t bench_texture(const char*)
{
    ++g_bench.lookups;
    return g_bench_entry;
}

// The HUD root space of the bench (a scale and an offset, as a screen mapping would be).
const float BENCH_ROOT_SPACE[16] = {0.5f, 0, 0, 0, 0, 0.5f, 0, 0, 0, 0, 1, 0, 40.0f, 30.0f, 0, 1};

const hud::test_natives BENCH_NATIVES = {
    reinterpret_cast<void*>(bench_text_draw),   reinterpret_cast<void*>(bench_text_params),
    reinterpret_cast<void*>(bench_batch_begin), reinterpret_cast<void*>(bench_batch_sprite),
    reinterpret_cast<void*>(bench_batch_end),   reinterpret_cast<void*>(bench_bar),
    reinterpret_cast<void*>(bench_texture),     reinterpret_cast<void*>(bench_scissor)};

template <typename T>
void put(uint64_t address, T value)
{
    memcpy(reinterpret_cast<void*>(address), &value, sizeof(T));
}

}  // namespace

extern "C" {

struct ilct_bench_result {
    uint64_t frames;           // timed frames
    uint64_t qpc_ticks;        // QueryPerformanceCounter ticks over the timed frames (h0 + h0_after calls only)
    uint64_t qpc_frequency;
    uint64_t queries;          // VirtualQuery calls of the probe over the timed frames
    uint64_t atlas_lookups;    // texture lookups by name over the timed frames
    uint64_t texts, sprites, bars, begins;   // stand-in draw calls over the timed frames
    uint64_t drawn;            // frames replayed to the end over the timed frames
    uint64_t hook_ticks_total; // the hook's own rdtsc sum over the timed frames (the counter)
    uint64_t hook_calls;
    uint64_t heap_bytes;       // the region the world lives in
    uint64_t sprites_on_entry; // sprites drawn with the entry the registry named at that moment
    uint64_t zero_output;      // the hook's zero-output count over the timed frames
    uint64_t declined_matrix;  // frames refused for want of a usable matrix over the timed frames
    uint64_t child_space;      // frames drawn in the root's child space over the timed frames
    float matrix[16];          // the matrix the last stand-in text draw was handed
    uint64_t clip_lifts;       // scissor lifts over the timed frames
    uint64_t clip_last;        // the context of the last scissor call (the clip set back)
    uint64_t manager_context;  // the context handed to the log draw (what the clip must be set back to)
};

// Runs `warm` untimed then `frames` timed hook calls on the published snapshot and views (the caller sets them up,
// with the fixed clock). legacy = 1: the 0.1.53 behaviour (every game read probed with a VirtualQuery, the atlas cache
// dropped before every frame). `restore_base` is the image base the HUD gets back afterwards. 0 = ran.
// flags: BENCH_REPLACE_AT(n) (bits 0..15, timed frame 1..) = before that frame the registry starts naming another
// entry for every atlas (a copy of the picture elsewhere) while the old entry stays intact and valid, as when the game
// re-registers its HUD textures: every sprite from then on must be drawn with the new entry. BENCH_RING (bit 16) = the
// stand-in batch and bar draws add to the ring like the game's (else they add nothing: zero output). BENCH_NO_ROOT_SPACE
// (bit 17) = the root's child space is all zero too (no usable matrix anywhere).
int32_t ILCT_HudBench(uint32_t epoch, uint32_t warm, uint32_t frames, uint32_t heap_mib, int32_t legacy,
                      uint64_t restore_base, uint32_t flags, ilct_bench_result* out)
{
    memset(out, 0, sizeof(*out));
    const SIZE_T heap_size = static_cast<SIZE_T>(heap_mib != 0 ? heap_mib : 1) << 20;
    const SIZE_T image_size = static_cast<SIZE_T>(hud::snapfacts::RENDER_FLAGS - hud::facts::PINNED_BASE + 0x1000);
    uint8_t* heap = static_cast<uint8_t*>(VirtualAlloc(nullptr, heap_size, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
    uint8_t* image = static_cast<uint8_t*>(VirtualAlloc(nullptr, image_size, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
    if (heap == nullptr || image == nullptr) {
        if (heap != nullptr) VirtualFree(heap, 0, MEM_RELEASE);
        if (image != nullptr) VirtualFree(image, 0, MEM_RELEASE);
        return -1;
    }
    memset(heap, 0, heap_size);    // every page touched, as a heap in use
    memset(image, 0, image_size);
    const uint64_t h = reinterpret_cast<uint64_t>(heap);
    const uint64_t base = reinterpret_cast<uint64_t>(image);
    const auto img = [base](uint64_t va) { return base + (va - hud::facts::PINNED_BASE); };
    // the log manager -> its background window -> the root, each with a draw context (alpha and colour 1.0)
    const uint64_t mgr = h + 0x10000, bg = h + 0x20000, root = h + 0x21000;
    const uint64_t ctx = h + 0x30000, ctx_bg = h + 0x30200, ctx_root = h + 0x30400;
    const uint64_t contexts[] = {ctx, ctx_bg, ctx_root};
    const float identity[16] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
    for (uint64_t c : contexts) {
        put<uint32_t>(c + hud::facts::CONTEXT_FLAGS, hud::facts::CONTEXT_CLIP);
        put<int16_t>(c + hud::facts::CONTEXT_ALPHA, 0x80);
        put<int16_t>(c + hud::facts::CONTEXT_BRIGHT, 0x80);
        if (c != ctx_root) {
            memcpy(reinterpret_cast<void*>(c), identity, sizeof(identity));   // children: a live matrix
        }
    }
    // the root: its own context's matrix all zero (as 0x2d8c00 leaves it), the root space in its child space
    if ((flags & 0x20000u) == 0) {
        memcpy(reinterpret_cast<void*>(ctx_root + hud::facts::CONTEXT_CHILD_SPACE), BENCH_ROOT_SPACE,
               sizeof(BENCH_ROOT_SPACE));
    }
    put<uint64_t>(mgr + hud::facts::WINDOW_CONTEXT, ctx);
    put<uint64_t>(mgr + hud::facts::WINDOW_PARENT, bg);
    put<uint64_t>(bg + hud::facts::WINDOW_CONTEXT, ctx_bg);
    put<uint64_t>(bg + hud::facts::WINDOW_PARENT, root);
    put<uint64_t>(root + hud::facts::WINDOW_CONTEXT, ctx_root);
    // the render ring's cursors
    const uint64_t arena = h + 0x40000;
    put<uint64_t>(arena + hud::facts::ARENA_START, 0x100000);
    put<uint64_t>(arena + hud::facts::ARENA_CURSOR, g_bench_cursor);
    put<uint64_t>(arena + hud::facts::ARENA_END, 0x100000 + hud::facts::ARENA_CAPACITY);
    // one texture entry for every atlas: a picture whose eXt block resolves every page/sheet/cell to a 16x16 frame
    const uint64_t entry = h + 0x50000, picture = h + 0x51000, ext = picture + hud::facts::PICTURE_USER;
    constexpr uint32_t EXT_BYTES = 0x4000, FRAMES_AT = 0x400, CLUT_AT = 0x100;
    put<uint64_t>(entry + hud::facts::TEXTURE_PICTURE, picture);
    put<uint32_t>(ext, hud::facts::EXT_MAGIC);
    put<uint32_t>(ext + hud::facts::EXT_SIZE, EXT_BYTES);
    put<uint16_t>(ext + hud::facts::EXT_FRAMES, static_cast<uint16_t>(FRAMES_AT));
    put<uint16_t>(ext + hud::facts::EXT_CLUT_GROUPS, static_cast<uint16_t>(CLUT_AT));
    for (uint32_t at = FRAMES_AT; at + 8 <= EXT_BYTES; at += 8) {
        put<uint32_t>(ext + at + 4, 16u | 16u << 12);
    }
    g_bench_entry = entry;
    g_bench_arena = (flags & 0x10000u) != 0 ? arena : 0;
    const uint32_t replace_at = flags & 0xFFFFu;
    // the image facts
    put<uint32_t>(img(hud::snapfacts::RENDER_FLAGS), hud::snapfacts::RENDER_UI);
    put<uint64_t>(img(hud::facts::RENDER_ARENA), arena);
    put<uint32_t>(img(hud::facts::UI_READY), 1);

    hud::test_set_image_base(static_cast<uintptr_t>(base));
    hud::test_set_natives(&BENCH_NATIVES);
    const bool layer_was = hud::driver::layer_enabled();
    hud::driver::set_layer_enabled((flags & 0x40000u) == 0);   // BENCH_LAYER_OFF (bit 18): the 0.1.57 default
    test_set_hot_reads(legacy == 0);
    const auto frame = [&]() {
        if (legacy != 0) {
            hud::test_forget_atlases();  // 0.1.53: the cache was reset at the start of every replay
        }
        patch::ctx c;
        memset(&c, 0, sizeof(c));
        c.rcx = mgr;
        c.rdx = ctx;
        if (hud::h0(&c, epoch) == patch::CALL_AROUND) {
            hud::h0_after(&c, epoch);
        }
    };
    for (uint32_t k = 0; k < warm; ++k) {
        frame();
    }
    const hud::counters before = *hud::stats();
    const probe_counters probes = probe_stats();
    g_bench = {};
    g_bench_clip_lifts = 0;
    g_bench_clip_last = 0;
    memset(g_bench_matrix, 0, sizeof(g_bench_matrix));
    LARGE_INTEGER t0, t1, f;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&t0);
    for (uint32_t k = 0; k < frames; ++k) {
        if (replace_at != 0 && k + 1 == replace_at) {
            const uint64_t entry2 = h + 0x58000, picture2 = h + 0x60000;
            memcpy(reinterpret_cast<void*>(picture2), reinterpret_cast<const void*>(picture),
                   hud::facts::PICTURE_USER + EXT_BYTES);
            put<uint64_t>(entry2 + hud::facts::TEXTURE_PICTURE, picture2);
            g_bench_entry = entry2;   // the old entry and picture stay as they were
            g_bench.sprites_on_entry = 0;
        }
        frame();
    }
    QueryPerformanceCounter(&t1);
    const hud::counters timed = *hud::stats();
    const bench_counts calls = g_bench;
    const uint64_t lifts = g_bench_clip_lifts;
    const uint64_t queries = probe_stats().queries - probes.queries;
    frame();  // publishes the last timed call's cost (the hook's own sum)
    const hud::counters after = *hud::stats();
    out->frames = frames;
    out->qpc_ticks = static_cast<uint64_t>(t1.QuadPart - t0.QuadPart);
    out->qpc_frequency = static_cast<uint64_t>(f.QuadPart);
    out->queries = queries;
    out->atlas_lookups = timed.atlas_lookups - before.atlas_lookups;
    out->texts = calls.texts;
    out->sprites = calls.sprites;
    out->bars = calls.bars;
    out->begins = calls.begins;
    out->drawn = timed.drawn - before.drawn;
    out->hook_ticks_total = after.hook_ticks_total - before.hook_ticks_total;
    out->hook_calls = after.hook_calls - before.hook_calls;
    out->heap_bytes = heap_size;
    out->sprites_on_entry = calls.sprites_on_entry;
    out->zero_output = timed.zero_output - before.zero_output;
    out->declined_matrix = timed.declined_matrix - before.declined_matrix;
    out->child_space = timed.child_space - before.child_space;
    memcpy(out->matrix, g_bench_matrix, sizeof(out->matrix));
    out->clip_lifts = lifts;
    out->clip_last = g_bench_clip_last;
    out->manager_context = ctx;

    test_set_hot_reads(true);
    hud::driver::set_layer_enabled(layer_was);
    hud::test_set_natives(nullptr);
    hud::test_set_image_base(static_cast<uintptr_t>(restore_base));
    memcpy(&g_bench_cursor, reinterpret_cast<const void*>(arena + hud::facts::ARENA_CURSOR), sizeof(g_bench_cursor));
    g_bench_entry = 0;
    g_bench_arena = 0;
    VirtualFree(heap, 0, MEM_RELEASE);
    VirtualFree(image, 0, MEM_RELEASE);
    return 0;
}

// ---- the probe's hot-path reads ------------------------------------------------------------------------------------
int32_t ILCT_ProbeReadGame(void* dst, uint64_t src, uint64_t n) { return read_game(dst, reinterpret_cast<const void*>(src), n) ? 1 : 0; }
int32_t ILCT_ProbeLoadGame(void* dst, uint64_t src, uint64_t width)
{
    return load_game(dst, reinterpret_cast<const void*>(src), width) ? 1 : 0;
}
int32_t ILCT_ProbeStoreGame(uint64_t dst, const void* src, uint64_t n)
{
    return store_game(reinterpret_cast<void*>(dst), src, n) ? 1 : 0;
}
void ILCT_ProbeInvalidate(void) { invalidate_game_regions(); }
void ILCT_ProbeSetHot(int32_t on) { test_set_hot_reads(on != 0); }
void ILCT_ProbeStats(probe_counters* out) { *out = probe_stats(); }
uint32_t ILCT_ProbeSites(probe_site* out, uint32_t cap) { return probe_sites(out, cap); }

uint32_t ILCT_HudSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(hud::frame),       sizeof(hud::cmd),         sizeof(hud::counters),
                              sizeof(hud::budget_view), sizeof(hud::status_view), hud::MAX_COMMANDS,
                              hud::TEXT_POOL,           sizeof(guard::fault_view)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

void ILCT_HudReset(void) { hud::test_reset(); }

void ILCT_HudSetTestDraw(int32_t on) { hud::test_set_test_draw(on != 0); }

int32_t ILCT_HudTestDraw(void) { return hud::test_draw_on() ? 1 : 0; }

void ILCT_HudConfigure(const ilc_settings* s) { hud::configure_from_settings(*s); }

int32_t ILCT_HudAttach(uint64_t base) { return hud::attach(static_cast<uintptr_t>(base)) ? 1 : 0; }

void ILCT_HudSetImageBase(uint64_t base) { hud::test_set_image_base(static_cast<uintptr_t>(base)); }

void ILCT_HudSetPinsOk(int32_t ok) { hud::test_set_pins_ok(ok != 0); }

void ILCT_HudInjectFault(int32_t on) { hud::test_inject_fault(on != 0); }

void ILCT_HudSetRing(uint64_t start, uint64_t end, uint64_t cursor) { hud::test_set_ring(start, end, cursor); }

int32_t ILCT_HudEncode(const char* ascii, char* out, uint32_t cap, uint32_t* substituted)
{
    return hud::encode(ascii, out, cap, substituted);
}

void ILCT_HudBegin(uint32_t epoch, uint64_t now_ms) { g_test_canvas.begin(epoch, now_ms); }

int32_t ILCT_HudText(int32_t x, int32_t y, const char* ascii, uint32_t rgba, uint32_t scale, uint32_t font)
{
    const hud::rgba c = {static_cast<uint8_t>(rgba), static_cast<uint8_t>(rgba >> 8), static_cast<uint8_t>(rgba >> 16),
                         static_cast<uint8_t>(rgba >> 24)};
    return g_test_canvas.text(static_cast<int16_t>(x), static_cast<int16_t>(y), ascii, c,
                              static_cast<uint16_t>(scale), static_cast<uint8_t>(font)) ? 1 : 0;
}

int32_t ILCT_HudSprite(uint32_t atlas, uint32_t page, uint32_t sheet, uint32_t cell, int32_t x, int32_t y,
                       uint32_t rgba, uint32_t w, uint32_t h)
{
    const hud::rgba c = {static_cast<uint8_t>(rgba), static_cast<uint8_t>(rgba >> 8), static_cast<uint8_t>(rgba >> 16),
                         static_cast<uint8_t>(rgba >> 24)};
    return g_test_canvas.sprite(static_cast<hud::atlas_id>(atlas), static_cast<uint8_t>(page),
                                static_cast<uint8_t>(sheet), static_cast<uint16_t>(cell), static_cast<int16_t>(x),
                                static_cast<int16_t>(y), c, static_cast<uint16_t>(w), static_cast<uint16_t>(h)) ? 1 : 0;
}

int32_t ILCT_HudBar(int32_t x, int32_t y, uint32_t w, uint32_t h, uint32_t rgba)
{
    const hud::rgba c = {static_cast<uint8_t>(rgba), static_cast<uint8_t>(rgba >> 8), static_cast<uint8_t>(rgba >> 16),
                         static_cast<uint8_t>(rgba >> 24)};
    return g_test_canvas.bar(static_cast<int16_t>(x), static_cast<int16_t>(y), static_cast<uint16_t>(w),
                             static_cast<uint16_t>(h), c) ? 1 : 0;
}

uint32_t ILCT_HudRefused(void) { return g_test_canvas.refused(); }

void ILCT_HudFrame(hud::frame* out) { memcpy(out, &g_test_canvas.view(), sizeof(*out)); }

void ILCT_HudPublish(void) { hud::publish(g_test_canvas.view()); }

void ILCT_HudPublishFrame(const hud::frame* f) { hud::publish(*f); }

void ILCT_HudWithdraw(void) { hud::withdraw(); }

int32_t ILCT_HudLatest(hud::frame* out) { return hud::latest(*out) ? 1 : 0; }

uint64_t ILCT_HudRingEstimate(const hud::frame* f) { return hud::ring_estimate(*f); }

void ILCT_HudProduce(uint32_t epoch, uint64_t now_ms) { hud::test_produce(epoch, now_ms); }

void ILCT_HudStep(uint64_t now_ms) { hud::step(now_ms); }

void ILCT_HudStop(void) { hud::stop(); }

int32_t ILCT_HudCall(patch::ctx* c, uint32_t epoch) { return hud::h0(c, epoch); }

void ILCT_HudAfter(patch::ctx* c, uint32_t epoch) { hud::h0_after(c, epoch); }

void ILCT_HudStats(hud::counters* out) { memcpy(out, hud::stats(), sizeof(*out)); }

void ILCT_HudBudget(hud::budget_view* out) { *out = hud::budget(); }

void ILCT_HudStatus(hud::status_view* out) { *out = hud::status(); }

int32_t ILCT_HudReplay(const hud::frame* f, uint64_t context) { return hud::test_replay(*f, context); }

int32_t ILCT_HudRow(void) { return hud::test_row(); }

uint64_t ILCT_HudCountersAddress(void) { return reinterpret_cast<uint64_t>(hud::stats()); }

int32_t ILCT_HudGuardAttach(void) { return guard::attach() ? 1 : 0; }

void ILCT_HudGuardReset(void) { guard::test_reset(); }

void ILCT_HudGuardFault(guard::fault_view* out) { *out = guard::last_fault(); }

uint64_t ILCT_HudGuardTrace(void) { return g_guard_trace; }

// mode 0 returns, 1 reads address 8, 2 writes address 16, 3 clobbers the non-volatile registers then faults,
// 4 nests a second guarded call (it must be refused BUSY: the result goes to *nested).
int32_t ILCT_HudGuardCall(uint32_t mode, uint64_t arg, int32_t* nested)
{
    switch (mode) {
    case 0: return guard::call(guard_returns, reinterpret_cast<void*>(arg));
    case 1: return guard::call(guard_reads_null, reinterpret_cast<void*>(arg));
    case 2: return guard::call(guard_writes_null, reinterpret_cast<void*>(arg));
    case 3: return guard::call(guard_clobbers_then_faults, nullptr);
    case 4: return guard::call(guard_nested, nested);
    default: return -1;
    }
}

// Calls mode 3 with known values in every non-volatile register and reports which came back changed (bit per
// register: rbx, rbp, rsi, rdi, r12..r15, xmm6, xmm15). 0 = all restored. *result receives the guard's result.
uint32_t ILCT_HudGuardPreserves(int32_t* result);

}  // extern "C"

// ilct_guard_preserves(result = RCX) -> EAX mask (see ILCT_HudGuardPreserves). Entry RSP = 8 mod 16; eight pushes
// and 0x58 bytes put the call at RSP = 0 mod 16. Saves and restores its caller's non-volatile registers itself.
asm(".text\n"
    ".p2align 4\n"
    ".globl ILCT_HudGuardPreserves\n"
    "ILCT_HudGuardPreserves:\n"
    "    pushq %rbx\n"
    "    pushq %rbp\n"
    "    pushq %rsi\n"
    "    pushq %rdi\n"
    "    pushq %r12\n"
    "    pushq %r13\n"
    "    pushq %r14\n"
    "    pushq %r15\n"
    "    subq $0x58, %rsp\n"
    "    movq %rcx, 0x20(%rsp)\n"
    "    movdqu %xmm6, 0x30(%rsp)\n"
    "    movdqu %xmm15, 0x40(%rsp)\n"
    "    movq $0xB0B0, %rbx\n"
    "    movq $0xB1B1, %rbp\n"
    "    movq $0xB2B2, %rsi\n"
    "    movq $0xB3B3, %rdi\n"
    "    movq $0xB4B4, %r12\n"
    "    movq $0xB5B5, %r13\n"
    "    movq $0xB6B6, %r14\n"
    "    movq $0xB7B7, %r15\n"
    "    movq $0xC6C6, %rax\n"
    "    movq %rax, %xmm6\n"
    "    movq $0xCFCF, %rax\n"
    "    movq %rax, %xmm15\n"
    "    movl $3, %ecx\n"
    "    xorl %edx, %edx\n"
    "    xorl %r8d, %r8d\n"
    "    call ILCT_HudGuardCall\n"
    "    movl %eax, %r10d\n"
    "    xorl %r11d, %r11d\n"
    "    cmpq $0xB0B0, %rbx\n"
    "    je 1f\n"
    "    orl $0x001, %r11d\n"
    "1:  cmpq $0xB1B1, %rbp\n"
    "    je 1f\n"
    "    orl $0x002, %r11d\n"
    "1:  cmpq $0xB2B2, %rsi\n"
    "    je 1f\n"
    "    orl $0x004, %r11d\n"
    "1:  cmpq $0xB3B3, %rdi\n"
    "    je 1f\n"
    "    orl $0x008, %r11d\n"
    "1:  cmpq $0xB4B4, %r12\n"
    "    je 1f\n"
    "    orl $0x010, %r11d\n"
    "1:  cmpq $0xB5B5, %r13\n"
    "    je 1f\n"
    "    orl $0x020, %r11d\n"
    "1:  cmpq $0xB6B6, %r14\n"
    "    je 1f\n"
    "    orl $0x040, %r11d\n"
    "1:  cmpq $0xB7B7, %r15\n"
    "    je 1f\n"
    "    orl $0x080, %r11d\n"
    "1:  movq %xmm6, %rax\n"
    "    cmpq $0xC6C6, %rax\n"
    "    je 1f\n"
    "    orl $0x100, %r11d\n"
    "1:  movq %xmm15, %rax\n"
    "    cmpq $0xCFCF, %rax\n"
    "    je 1f\n"
    "    orl $0x200, %r11d\n"
    "1:  movq 0x20(%rsp), %rcx\n"
    "    movl %r10d, (%rcx)\n"
    "    movl %r11d, %eax\n"
    "    movdqu 0x30(%rsp), %xmm6\n"
    "    movdqu 0x40(%rsp), %xmm15\n"
    "    addq $0x58, %rsp\n"
    "    popq %r15\n"
    "    popq %r14\n"
    "    popq %r13\n"
    "    popq %r12\n"
    "    popq %rdi\n"
    "    popq %rsi\n"
    "    popq %rbp\n"
    "    popq %rbx\n"
    "    ret\n");
