// Ivalice Legacy co-op module: the interaction prompt over each guest's own character (see prompt.h).
#include "interaction/prompt.h"

#include <string.h>

#include "patch/call_island.h"
#include "patch/site_table.h"
#include "runtime/probe.h"
#include "session/reader.h"

namespace ilc::interaction::prompt {
namespace {

constexpr bool same_id(const char* a, const char* b)
{
    while (*a != 0 && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

constexpr int32_t find_row(const char* id)
{
    for (uint32_t i = 0; i < patch::SITE_COUNT; ++i) {
        if (same_id(patch::SITES[i].id, id)) {
            return static_cast<int32_t>(i);
        }
    }
    return -1;
}

constexpr int32_t ROW_PROMPT = find_row("IX-21");
static_assert(ROW_PROMPT >= 0 && patch::SITES[ROW_PROMPT].kind == ILC_SITE_KIND_CALL &&
                  patch::SITES[ROW_PROMPT].policy == ILC_SITE_POLICY_PATCH &&
                  patch::SITES[ROW_PROMPT].subsystem == ILC_SITE_SUBSYS_MOVEMENT &&
                  (patch::SITES[ROW_PROMPT].flags & ILC_SITE_FLAG_OPTIONAL) != 0 &&
                  patch::SITES[ROW_PROMPT].rva == SITE_PROMPT_CALL - PINNED_BASE,
              "IX-21: an optional MOVEMENT call row on the prompt drawer call");
static_assert(patch::island::is_call_row(static_cast<uint32_t>(ROW_PROMPT)),
              "IX-21 runs through a call island (its after-handler calls the game)");

// What IX-21 relies on, checked once at attach (pinned build); any other bytes -> IX-21 stays stock.
struct pin {
    uint64_t va;
    uint8_t n;
    uint8_t bytes[40];
};
constexpr pin PINS[] = {
    // the drawer's arguments before the call: lea rdx,[rsi+0x1b80] (P1's widget); mov [rsp+0x20],rbp (list D);
    // mov r9,r14 (pool); mov r8,r15 (list B); mov rcx,rsi (manager). The call itself is the row's own (the patch
    // layer checks it and has retargeted its rel32 by the time attach runs).
    {SITE_PROMPT_CALL - 21, 21, {0x48, 0x8D, 0x96, 0x80, 0x1B, 0x00, 0x00, 0x48, 0x89, 0x6C, 0x24, 0x20, 0x4D,
                                 0x8B, 0xCE, 0x4D, 0x8B, 0xC7, 0x48, 0x8B, 0xCE}},
    // the drawer's own entry (the callee the row wraps)
    {FN_PROMPT_DRAW, 16, {0x4C, 0x8B, 0xDC, 0x55, 0x53, 0x57, 0x41, 0x57, 0x49, 0x8D, 0x6B, 0xA9, 0x48, 0x81, 0xEC,
                          0x88}},
    // the functions the after-handler calls
    {FN_OVERHEAD_SPRITE, 16, {0x40, 0x53, 0x55, 0x56, 0x57, 0x41, 0x56, 0x48, 0x83, 0xEC, 0x40, 0x48, 0x8B, 0x05, 0x7E,
                              0xDE}},
    {FN_KEY_EVAL, 16, {0x48, 0x89, 0x5C, 0x24, 0x18, 0x56, 0x57, 0x41, 0x56, 0x48, 0x83, 0xEC, 0x60, 0x48, 0x8B,
                       0x05}},
    {FN_VEC_SCALE, 16, {0x0F, 0x28, 0xC2, 0x0F, 0x28, 0xCA, 0xF3, 0x0F, 0x59, 0x02, 0xF3, 0x0F, 0x11, 0x01, 0xF3,
                        0x0F}},
    {FN_OBJECT_ALLOC, 16, {0x48, 0x83, 0xEC, 0x38, 0x48, 0x63, 0x41, 0x0C, 0x3B, 0x41, 0x08, 0x7C, 0x07, 0x33, 0xC0,
                           0x48}},
    // the list append: obj +0xd0/+0xd8 cleared, an empty list (head +0x00) takes obj as head and tail (+0x10), ret
    {FN_LIST_APPEND, 29, {0x33, 0xC0, 0x48, 0x89, 0x82, 0xD0, 0x00, 0x00, 0x00, 0x48, 0x89, 0x82, 0xD8, 0x00, 0x00,
                          0x00, 0x48, 0x39, 0x01, 0x75, 0x08, 0x48, 0x89, 0x11, 0x48, 0x89, 0x51, 0x10, 0xC3}},
    // the texture the drawer binds: mov rax,[0x209ac30]; mov rax,[rax+0x350]; ret
    {FN_BATTLE_TEXTURE, 15, {0x48, 0x8B, 0x05, 0x59, 0xD8, 0xD3, 0x01, 0x48, 0x8B, 0x80, 0x50, 0x03, 0x00, 0x00, 0xC3}},
    // the per-level frame offsets and the two key tables (8-byte keys: s8 x, s8 y, u8 ease, u8 alpha, s16 time,
    // s16 scale)
    {FRAME_DELTA, 3, {0x01, 0x00, 0x02}},
    {KEYS_OUTER, 40, {0x00, 0xF6, 0x00, 0x00, 0x00, 0x00, 0x40, 0x01, 0x00, 0xF6, 0x01, 0x40, 0x40, 0x00,
                      0xC8, 0x01, 0x00, 0xF6, 0x02, 0x80, 0x80, 0x00, 0x40, 0x01, 0x00, 0xF6, 0x01, 0x80,
                      0xC8, 0x00, 0x00, 0x01, 0x00, 0xF6, 0x00, 0x80, 0x00, 0x01, 0x00, 0x01}},
    {KEYS_INNER, 16, {0x00, 0xF6, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0xFF, 0xF6, 0x00, 0x20, 0x10, 0x00, 0x00,
                      0x01}},
    // the label as the setter builds it: mov ecx,0x46d8 ... call 0x2f9860 (the prefix message); then the glyph bytes
    // from [0x1e0ced4]: cl = cfg | 0x80, al = ((cfg >> 1) & 0xc1) | 0x81, word 0x3c0f
    {0x2C67C1, 18, {0xB9, 0xD8, 0x46, 0x00, 0x00, 0x0C, 0x04, 0x88, 0x86, 0xB9, 0x01, 0x00, 0x00, 0xE8, 0x8D, 0x30,
                    0x03, 0x00}},
    {0x2C67EE, 28, {0x8B, 0x0D, 0xE0, 0x66, 0xB4, 0x01, 0x8B, 0xC1, 0xD1, 0xF8, 0x80, 0xC9, 0x80, 0x24, 0xC1, 0x88,
                    0x4C, 0x24, 0x33, 0x0C, 0x81, 0x66, 0xC7, 0x44, 0x24, 0x30, 0x0F, 0x3C}},
    // ... copied to the widget's +0x130 (0x80 bytes), then 0x2b3400(widget, widget + 0x130, resource)
    {0x2C6880, 38, {0x41, 0xB8, 0x80, 0x00, 0x00, 0x00, 0x48, 0x8D, 0x54, 0x24, 0x50, 0x48, 0x8D, 0x8E, 0x30, 0x01,
                    0x00, 0x00, 0xE8, 0x99, 0xE6, 0xF8, 0xFF, 0x4C, 0x8B, 0x44, 0x24, 0x48, 0x48, 0x8D, 0x96, 0x30,
                    0x01, 0x00, 0x00, 0x48, 0x8B, 0xCE}},
    {FN_MESSAGE, 16, {0x40, 0x53, 0x48, 0x83, 0xEC, 0x20, 0xB8, 0xD3, 0x4D, 0x62, 0x10, 0x8B, 0xD9, 0xF7, 0xE9,
                      0xC1}},
    {FN_TEXT_INIT, 16, {0x48, 0x89, 0x5C, 0x24, 0x08, 0x48, 0x89, 0x74, 0x24, 0x10, 0x57, 0x48, 0x83, 0xEC, 0x20,
                        0x49}},
    {FN_TEXT_MEASURE, 16, {0x40, 0x53, 0x48, 0x83, 0xEC, 0x40, 0x48, 0x8B, 0xC2, 0x0F, 0x29, 0x74, 0x24, 0x30, 0x48,
                           0x8B}},
    // the list D append: node +0x100 / +0xf8 cleared, then linked at the tail
    {FN_TEXT_APPEND, 16, {0x33, 0xC0, 0x48, 0x89, 0x82, 0x00, 0x01, 0x00, 0x00, 0x48, 0x89, 0x82, 0xF8, 0x00, 0x00,
                          0x00}},
    {FN_USABLE, 16, {0x48, 0x89, 0x5C, 0x24, 0x08, 0x57, 0x48, 0x83, 0xEC, 0x20, 0x33, 0xFF, 0xE8, 0xAF, 0x91, 0xF7}},
};
// PINS[0 .. LABEL_PINS_FROM) are the marker's (IX-21 arms only if they match); the rest are the label's (they only
// turn the guests' button label on or off).
constexpr uint32_t LABEL_PINS_FROM = 11;
static_assert(PINS[LABEL_PINS_FROM - 1].va == KEYS_INNER && PINS[LABEL_PINS_FROM].va == 0x2C67C1,
              "the label's pins start with the setter's code");

counters g_counters;
volatile uint32_t g_attached = 0;
volatile uint32_t g_marker_pins = 0;      // attach: the marker's pins matched
volatile uint32_t g_label_pins = 0;       // attach: the label's pins matched (labels drawn only then)
volatile uint64_t g_first_mismatch = 0;   // attach: VA of the first pin that did not match
volatile uintptr_t g_base = PINNED_BASE;
const game_fns* volatile g_test_fns = nullptr;
patch::record_slot<prompt_record> g_record = {};

// Game thread only.
anim g_anims[SLOTS];
struct pending {
    uint32_t armed;
    uint32_t reserved;
    uint64_t manager;
    uint64_t list_b;
    uint64_t pool;
    uint64_t list_d;   // 0: unreadable, no labels this frame
    prompt_record rec;
};
pending g_pending;
// One label node per guest: the start of a widget-sized block, as the setter builds P1's (game thread only; the list D
// the drawer feeds keeps a pointer to it until the frame is drawn, as it keeps one to P1's widget).
alignas(16) uint8_t g_nodes[SLOTS][NODE_SIZE];
struct node_key {
    uint32_t built;
    uint32_t object;
    uint64_t text;
    uint32_t config;
    uint8_t grey;
    uint8_t reserved[3];
};
node_key g_keys[SLOTS];

void bump(volatile uint64_t& counter)
{
    __atomic_add_fetch(&counter, 1, __ATOMIC_RELAXED);
}

void zero_counters()
{
    volatile uint64_t* w = &g_counters.published;
    for (size_t k = 0; k < sizeof(counters) / sizeof(uint64_t); ++k) {
        w[k] = 0;
    }
}

template <typename T>
bool load(uint64_t address, T* out)
{
    return address != 0 && read_game(out, reinterpret_cast<const void*>(address), sizeof(T));
}

uint64_t at(uint64_t va)
{
    return g_base + (va - PINNED_BASE);
}

game_fns live_fns()
{
    const game_fns* t = g_test_fns;
    if (t != nullptr) {
        return *t;
    }
    const uintptr_t base = g_base;
    game_fns f;
    f.alloc = reinterpret_cast<decltype(f.alloc)>(base + (FN_OBJECT_ALLOC - PINNED_BASE));
    f.append = reinterpret_cast<decltype(f.append)>(base + (FN_LIST_APPEND - PINNED_BASE));
    f.sprite = reinterpret_cast<decltype(f.sprite)>(base + (FN_OVERHEAD_SPRITE - PINNED_BASE));
    f.eval = reinterpret_cast<decltype(f.eval)>(base + (FN_KEY_EVAL - PINNED_BASE));
    f.scale = reinterpret_cast<decltype(f.scale)>(base + (FN_VEC_SCALE - PINNED_BASE));
    f.message = reinterpret_cast<decltype(f.message)>(base + (FN_MESSAGE - PINNED_BASE));
    f.text_init = reinterpret_cast<decltype(f.text_init)>(base + (FN_TEXT_INIT - PINNED_BASE));
    f.measure = reinterpret_cast<decltype(f.measure)>(base + (FN_TEXT_MEASURE - PINNED_BASE));
    f.text_append = reinterpret_cast<decltype(f.text_append)>(base + (FN_TEXT_APPEND - PINNED_BASE));
    f.usable = reinterpret_cast<decltype(f.usable)>(base + (FN_USABLE - PINNED_BASE));
    return f;
}

uint16_t step_up(uint16_t v, uint32_t step, uint32_t limit)
{
    const uint32_t next = static_cast<uint32_t>(v) + step;
    return static_cast<uint16_t>(next > limit ? limit : next);
}

uint16_t step_down(uint16_t v, uint32_t step)
{
    return static_cast<uint16_t>(v > step ? v - step : 0u);
}

uint64_t addr(const void* p)
{
    return reinterpret_cast<uint64_t>(p);
}

// A game string through the fault-safe probe: up to LABEL_MAX - 1 bytes before its 0. False when unreadable or
// longer (a label is never cut inside a control code).
bool read_cstr(uint64_t text, uint8_t out[LABEL_MAX], uint32_t* len)
{
    *len = 0;
    if (text == 0) {
        out[0] = 0;
        return true;
    }
    for (uint32_t k = 0; k < LABEL_MAX; ++k) {
        uint8_t b = 0;
        if (!load(text + k, &b)) {
            return false;
        }
        out[k] = b;
        if (b == 0) {
            *len = k;
            return true;
        }
    }
    return false;
}

// (Re)builds slot k's label node the way 0x2c64c0 builds P1's (message prefix, glyph, name; 0x2b3400; 0x29cd00; the
// centring offsets), only when the object, its name or the button setting changed. False: no label.
bool build_label(const game_fns& f, uint32_t k, const prompt_seat& s)
{
    node_key& key = g_keys[k];
    uint32_t config = 0;
    load(at(BUTTON_CONFIG), &config);
    if (key.built != 0 && key.object == s.object && key.text == s.text && key.config == config) {
        return true;
    }
    key = node_key{};
    if (s.text == 0) {
        return false;
    }
    uint8_t prefix[LABEL_MAX];
    uint8_t name[LABEL_MAX];
    uint8_t text[LABEL_MAX];
    uint32_t prefix_len = 0, name_len = 0, len = 0;
    if (!read_cstr(f.message(LABEL_MESSAGE), prefix, &prefix_len) || !read_cstr(s.text, name, &name_len) ||
        !label_string(prefix, prefix_len, config, name, name_len, text, &len)) {
        return false;
    }
    uint8_t* node = g_nodes[k];
    memset(node, 0, NODE_SIZE);
    memcpy(node + NODE_TEXT, text, len + 1);
    f.text_init(addr(node), addr(node + NODE_TEXT), s.resource);
    int32_t w = 0, h = 0;
    f.measure(addr(node + NODE_TEXT), 0, 1.0f, &w, &h);
    const int16_t offsets[4] = {static_cast<int16_t>(-(w >> 1)), static_cast<int16_t>(-h), static_cast<int16_t>(w),
                                static_cast<int16_t>(h)};
    memcpy(node + NODE_OFFSET, offsets, sizeof(offsets));
    key.built = 1;
    key.object = s.object;
    key.text = s.text;
    key.config = config;
    key.grey = f.usable(s.object) == 0 ? 1 : 0;
    bump(g_counters.labels_built);
    return true;
}

// The frame 0x2c7f90 picks for `level`: the frame table entry [sheet first + delta[level - 1]] of battle_4_p.
bool frame_of(uint64_t texture, uint32_t level, uint64_t* frame)
{
    uint64_t resource = 0;
    uint16_t sheet = 0, first = 0, table = 0;
    int8_t delta = 0;
    if (level < 1 || level > LEVEL_MAX || !load(texture + TEX_RESOURCE, &resource) || resource == 0) {
        return false;
    }
    const uint64_t header = resource + RES_HEADER;
    if (!load(resource + RES_SHEET_INDEX, &sheet) || !load(header + HDR_SHEETS + 2ull * sheet, &first) ||
        !load(header + HDR_FRAMES, &table) || !load(at(FRAME_DELTA) + (level - 1), &delta)) {
        return false;
    }
    const int64_t index = static_cast<int64_t>(first) + delta;
    if (index < 0) {
        return false;
    }
    *frame = header + table + 8ull * static_cast<uint64_t>(index);
    return true;
}

// The overhead record of `handle` (the one the native status icons and P1's prompt anchor to), or 0.
uint64_t record_of(uint64_t manager, uint32_t handle)
{
    uint64_t rec = 0;
    if (handle == 0 || !load(manager + OVERHEAD_RECORDS, &rec)) {
        return 0;
    }
    for (uint32_t n = 0; rec != 0 && n < OVERHEAD_WALK_MAX; ++n) {
        uint32_t h = 0;
        if (!load(rec + OVERHEAD_HANDLE, &h)) {
            return 0;
        }
        if (h == handle) {
            return rec;
        }
        if (!load(rec + OVERHEAD_NEXT, &rec)) {
            return 0;
        }
    }
    return 0;
}

struct key_out {
    int8_t x;
    int8_t y;
    int16_t alpha;
    int16_t scale;
    uint16_t pad;
};
static_assert(sizeof(key_out) == 8, "0x2c6dc0 writes 6 bytes");

// One marker and its label, the way 0x2c7f90 builds P1's. False only when the pool is full.
bool emit_one(const game_fns& f, uint32_t k, const anim& a, uint64_t record, uint64_t texture, uint64_t frame,
              int32_t fade_all)
{
    const int32_t base_alpha = (fade_all * static_cast<int32_t>(a.fade)) >> 7;
    if (base_alpha == 0) {
        return true;
    }
    key_out pop = {};
    key_out lift = {};
    f.eval(reinterpret_cast<uint8_t*>(&pop), at(a.level == 1 ? KEYS_OUTER : KEYS_INNER),
           static_cast<int32_t>(a.level == 1 ? KEYS_OUTER_COUNT : KEYS_INNER_COUNT), a.pop);
    // The lift and label curves the drawer builds on its stack: from 0 to [0x1e0cf38], eased, alpha 0 to 0x80, over
    // TIME_END (the lift's first y is 0 unless the manager's flag bit 0 is set, which guests do not use).
    int8_t lift_target = 0;
    load(at(LIFT_TARGET), &lift_target);
    uint8_t curve_keys[16] = {0, 0, 0, 0, 0x00, 0x00, 0x00, 0x01,
                              0, static_cast<uint8_t>(lift_target), 1, 0x80, 0x00, 0x01, 0x00, 0x01};
    f.eval(reinterpret_cast<uint8_t*>(&lift), addr(curve_keys), 2, a.lift);
    key_out label = {};
    f.eval(reinterpret_cast<uint8_t*>(&label), addr(curve_keys), 2, a.label);
    int32_t height = 0;
    load(at(UI_HEIGHT), &height);
    const float ui_scale = static_cast<float>(height) / UI_HEIGHT_REF;
    const float label_dy = ui_scale * static_cast<float>(label.y) + static_cast<float>(lift.y);
    const uint64_t obj = f.alloc(g_pending.pool);
    if (obj == 0) {
        return false;
    }
    const int32_t alpha = (static_cast<int32_t>(pop.alpha) * base_alpha) >> 7;
    f.sprite(record, obj, frame, static_cast<int16_t>(alpha), RGBA_WHITE, texture);
    float* quad = reinterpret_cast<float*>(obj + OBJ_QUAD);
    f.scale(quad, quad, static_cast<float>(pop.scale) * (1.0f / 256.0f));
    const float dy = label_dy + static_cast<float>(pop.y);
    quad[0] += static_cast<float>(pop.x);
    quad[2] += static_cast<float>(pop.x);
    quad[1] += dy;
    quad[3] += dy;
    f.append(g_pending.list_b, obj);
    bump(g_counters.objects);
    // The label: drawn while its curve is visible, from the node built for this object, anchored where the marker
    // is, offset by the label and lift curves; grey when 0x3df6f0 says the object is not usable now.
    const node_key& key = g_keys[k];
    if (key.built != 0 && label.alpha != 0 && g_pending.list_d != 0) {
        uint8_t* node = g_nodes[k];
        const uint8_t rgb = key.grey != 0 ? LABEL_GREY : LABEL_WHITE;
        node[NODE_RGBA] = rgb;
        node[NODE_RGBA + 1] = rgb;
        node[NODE_RGBA + 2] = rgb;
        node[NODE_RGBA + 3] = static_cast<uint8_t>((static_cast<int32_t>(label.alpha) * base_alpha) >> 7);
        memcpy(node + NODE_ANCHOR, reinterpret_cast<const void*>(obj), 16);
        const float w = 1.0f;
        memcpy(node + NODE_ANCHOR + 12, &w, sizeof(w));
        const int16_t y = static_cast<int16_t>(static_cast<int32_t>(label_dy));
        memcpy(node + NODE_OFFSET + 2, &y, sizeof(y));
        f.text_append(g_pending.list_d, addr(node));
        bump(g_counters.labels);
    }
    return true;
}

int32_t prompt_body(patch::ctx* c, const prompt_record& r)
{
    g_pending.armed = 0;
    if (c->rcx == 0 || c->r8 == 0 || c->r9 == 0) {
        return patch::RUN_ORIGINAL;
    }
    bool any = r.visible != 0;
    for (uint32_t k = 0; k < SLOTS && !any; ++k) {
        any = g_anims[k].level != 0;  // a marker still fading out
    }
    if (!any) {
        return patch::RUN_ORIGINAL;  // the common case: no guest near anything, the drawer runs as stock
    }
    g_pending.manager = c->rcx;
    g_pending.list_b = c->r8;
    g_pending.pool = c->r9;
    uint64_t list_d = 0;
    g_pending.list_d = load(c->rsp + ARG5, &list_d) ? list_d : 0;
    g_pending.rec = r;
    g_pending.armed = 1;
    return patch::CALL_AROUND;
}

}  // namespace

// ---- rules -------------------------------------------------------------------------------------------------------
uint32_t level_of(uint32_t state)
{
    return state >= 1 && state <= LEVEL_MAX ? state : 0u;
}

uint32_t shown_level(uint32_t level, bool can_use_now)
{
    return level >= 2 && !can_use_now ? 1u : level;
}

uint32_t seat_level(uint32_t state, bool chest, bool leader_free)
{
    return shown_level(level_of(state), chest || leader_free);
}

bool label_string(const uint8_t* prefix, uint32_t prefix_len, uint32_t button_config, const uint8_t* name,
                  uint32_t name_len, uint8_t out[LABEL_MAX], uint32_t* out_len)
{
    const uint32_t len = prefix_len + 4 + name_len;
    if (name_len == 0 || len > LABEL_MAX - 1) {
        return false;
    }
    uint32_t n = 0;
    for (uint32_t k = 0; k < prefix_len; ++k) {
        out[n++] = prefix[k];
    }
    out[n++] = GLYPH_CODE;
    out[n++] = GLYPH_KIND;
    out[n++] = static_cast<uint8_t>(((button_config >> 1) & 0xC1u) | 0x81u);
    out[n++] = static_cast<uint8_t>((button_config & 0xFFu) | 0x80u);
    for (uint32_t k = 0; k < name_len; ++k) {
        out[n++] = name[k];
    }
    out[n] = 0;
    *out_len = n;
    return true;
}

bool anim_step(anim& a, uint32_t actor, uint32_t object, uint32_t level, bool labelled, uint32_t divisor,
               uint64_t now_ms)
{
    const uint32_t d = divisor == 0 ? 1u : divisor;
    // Each step at least 1: a divisor above a step would freeze the native widget; ours must still fade out.
    const auto per_frame = [d](uint32_t step) { return step / d != 0 ? step / d : 1u; };
    if (a.level != 0 && now_ms - a.seen_ms > ANIM_IDLE_RESET_MS) {
        a = anim{};  // the record was away: whatever shows next is new
    }
    a.seen_ms = now_ms;
    if (level != 0 && actor != 0) {
        if (a.level == 0 || a.actor != actor || (a.leaving != 0 && a.object != object)) {
            a.actor = actor;
            a.object = object;
            a.level = static_cast<uint8_t>(level);
            a.fade = 0;
            a.pop = 0;
            a.lift = 0;
        } else if (level > a.level || (a.object != object && level == 1)) {
            a.pop = 0;  // a higher level restarts its table; a new object at the "!" pops again
        } else if (level < a.level) {
            a.pop = TIME_END;  // a lower level shows at its rest
        }
        a.level = static_cast<uint8_t>(level);
        a.object = object;
        a.leaving = 0;
        a.fade = step_up(a.fade, per_frame(FADE_STEP), FADE_FULL);
        a.pop = step_up(a.pop, per_frame(POP_STEP), TIME_END);
        a.lift = step_up(a.lift, per_frame(LIFT_STEP), TIME_END);
        a.label = labelled && level >= 2 ? step_up(a.label, per_frame(LABEL_STEP), TIME_END)
                                         : step_down(a.label, per_frame(LABEL_STEP));
        return true;
    }
    if (a.level == 0) {
        return false;
    }
    a.leaving = 1;
    const uint32_t down = per_frame(FADE_STEP);
    if (a.fade <= down) {
        a = anim{};
        return false;
    }
    a.fade = static_cast<uint16_t>(a.fade - down);
    a.pop = step_up(a.pop, per_frame(POP_STEP), TIME_END);
    a.label = step_down(a.label, per_frame(LABEL_STEP));
    return true;
}

// ---- handlers ----------------------------------------------------------------------------------------------------
int32_t prompt_call(patch::ctx* c, uint32_t epoch)
{
    return patch::fail_open<prompt_record, g_record, prompt_body>(c, epoch);
}

void prompt_after(patch::ctx* c, uint32_t epoch)
{
    (void)c;
    (void)epoch;
    if (g_pending.armed == 0) {
        return;
    }
    g_pending.armed = 0;
    bump(g_counters.passes_wrapped);
    if (!patch::subsystem_live(ILC_SITE_SUBSYS_MOVEMENT)) {
        return;  // guest interactions went off while P1's prompt was drawn: add nothing
    }
    const prompt_record& r = g_pending.rec;
    const uint64_t now = patch::now_ms();
    uint32_t divisor = 1;
    load(at(FRAME_DIVISOR), &divisor);
    bool draw[SLOTS];
    bool wanted = false;
    bool labelled[SLOTS];
    for (uint32_t k = 0; k < SLOTS; ++k) {
        const prompt_seat& s = r.seats[k];
        const bool on = (r.visible & (1u << k)) != 0;
        const uint32_t level = on ? level_of(s.level) : 0u;
        // A label only while its own pins matched (another mod's button prompts cost the label, not the marker).
        labelled[k] = level >= 2 && s.text != 0 && __atomic_load_n(&g_label_pins, __ATOMIC_RELAXED) != 0;
        draw[k] = anim_step(g_anims[k], on ? s.actor : 0u, on ? s.object : 0u, level, labelled[k], divisor, now);
        if (!draw[k]) {
            g_keys[k] = node_key{};  // gone: whatever shows next builds its own label
        }
        wanted = wanted || draw[k];
    }
    if (!wanted) {
        return;
    }
    int16_t fade_all = 0;
    uint64_t atlases = 0;
    uint64_t texture = 0;
    if (!load(g_pending.manager + OVERHEAD_FADE, &fade_all) || fade_all <= 0) {
        return;  // the game hides every overhead mark now (P1's prompt included)
    }
    if (!load(at(UI_ATLASES), &atlases) || atlases == 0 || !load(atlases + ATLAS_BATTLE, &texture) || texture == 0) {
        bump(g_counters.no_texture);
        return;
    }
    const game_fns f = live_fns();
    for (uint32_t k = 0; k < SLOTS; ++k) {
        if (!draw[k]) {
            continue;
        }
        const anim& a = g_anims[k];
        const uint64_t record = record_of(g_pending.manager, a.actor);
        uint64_t frame = 0;
        if (record == 0) {
            bump(g_counters.no_anchor);
            continue;
        }
        if (!frame_of(texture, a.level, &frame)) {
            continue;
        }
        if (labelled[k] && (g_pending.list_d == 0 || !build_label(f, k, r.seats[k]))) {
            bump(g_counters.no_label);
        }
        if (!emit_one(f, k, a, record, texture, frame, fade_all)) {
            bump(g_counters.pool_full);
            return;
        }
    }
}

// ---- module ------------------------------------------------------------------------------------------------------
bool attach(uintptr_t image_base)
{
    g_base = image_base;
    session::game_reader r(image_base);
    bool marker = true;
    bool label = true;
    uint64_t first = 0;
    for (uint32_t i = 0; i < sizeof(PINS) / sizeof(PINS[0]); ++i) {
        const pin& p = PINS[i];
        uint8_t now[sizeof(p.bytes)];
        bool same = r.read(r.global(p.va), now, p.n);
        for (uint32_t k = 0; same && k < p.n; ++k) {
            same = now[k] == p.bytes[k];
        }
        if (!same) {
            first = first != 0 ? first : p.va;
            (i < LABEL_PINS_FROM ? marker : label) = false;
        }
    }
    __atomic_store_n(&g_marker_pins, marker ? 1u : 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_label_pins, label ? 1u : 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_first_mismatch, first, __ATOMIC_RELEASE);
    const uint32_t row = static_cast<uint32_t>(ROW_PROMPT);
    bool ok = marker && patch::set_handler(row, prompt_call) && patch::set_after(row, prompt_after) &&
              patch::set_site_armed(row, true);
    if (!ok) {
        patch::set_site_armed(row, false);  // stays stock: P1's prompt as the game draws it, no guest markers
    }
    __atomic_store_n(&g_attached, ok ? 1u : 0u, __ATOMIC_RELEASE);
    return ok;
}

bool attached()
{
    return __atomic_load_n(&g_attached, __ATOMIC_ACQUIRE) != 0;
}

status_view status()
{
    status_view v = {};
    v.attached = attached() ? 1 : 0;
    v.marker_pins = __atomic_load_n(&g_marker_pins, __ATOMIC_ACQUIRE) != 0 ? 1 : 0;
    v.label_pins = __atomic_load_n(&g_label_pins, __ATOMIC_ACQUIRE) != 0 ? 1 : 0;
    v.row = static_cast<uint32_t>(ROW_PROMPT);
    v.gate_open = patch::gate(v.row) != 0 ? 1 : 0;
    v.first_mismatch = __atomic_load_n(&g_first_mismatch, __ATOMIC_ACQUIRE);
    return v;
}

void publish(const prompt_record& r)
{
    g_record.publish(r);
    bump(g_counters.published);
}

void withdraw()
{
    g_record.withdraw();
}

const counters* stats()
{
    return &g_counters;
}

// ---- host tests --------------------------------------------------------------------------------------------------
void test_reset()
{
    g_record.withdraw();
    memset(g_anims, 0, sizeof(g_anims));
    memset(&g_pending, 0, sizeof(g_pending));
    memset(g_nodes, 0, sizeof(g_nodes));
    memset(g_keys, 0, sizeof(g_keys));
    zero_counters();
    g_test_fns = nullptr;
    g_base = PINNED_BASE;
    __atomic_store_n(&g_attached, 0u, __ATOMIC_RELEASE);
    // Host tests drive the handlers without attach: the label's pins count as matched until an attach says otherwise.
    __atomic_store_n(&g_label_pins, 1u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_marker_pins, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_first_mismatch, 0ull, __ATOMIC_RELEASE);
}

void test_set_image_base(uintptr_t base)
{
    g_base = base;
}

void test_set_fns(const game_fns* fns)
{
    g_test_fns = fns;
}

void test_set_attached(bool on)
{
    __atomic_store_n(&g_attached, on ? 1u : 0u, __ATOMIC_RELEASE);
}

bool test_read(prompt_record* out)
{
    return g_record.read(*out);
}

const anim* test_anims()
{
    return g_anims;
}

const uint8_t* test_node(uint32_t slot)
{
    return slot < SLOTS ? g_nodes[slot] : nullptr;
}

int32_t test_row()
{
    return ROW_PROMPT;
}

}  // namespace ilc::interaction::prompt
