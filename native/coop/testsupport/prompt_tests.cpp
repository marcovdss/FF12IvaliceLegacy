// Ivalice Legacy co-op module, host tests only: the guests' interaction prompt (ILCT_IxPrompt*): the rules, the
// animation, the record, IX-21's handlers with a synthetic ctx, and doubles of the game functions the emitter calls.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "interaction/prompt.h"

using namespace ilc;
namespace pr = ilc::interaction::prompt;

namespace {

// ---- world-object doubles --------------------------------------------------------------------------------------
// Same contracts as the game's 0x3f0040 (pool {base, capacity, count}, 0x100-byte objects, null when full) and
// 0x3f1290 (+0xd0 next / +0xd8 prev, the list's head +0x00 / tail +0x10). The sprite double records its arguments
// and lays the quad out as the game's does for a 32 x 57 frame (bottom-centred); the evaluator double records its
// arguments and answers a fixed pose (a level table: x 0, y -10, alpha 0x80, scale 0x100 or the overrides below; a
// two-key curve: y 0 until its time reaches 0x100, then its second key's y, alpha time / 2); the scale double scales
// x0, y0, x1 and copies y1 like 0x2708b0.
struct sprite_call {
    uint64_t record;
    uint64_t obj;
    uint64_t frame;
    int64_t alpha;
    uint64_t rgba;
    uint64_t texture;
};
struct eval_call {
    uint64_t keys;
    int32_t count;
    int32_t time;
    int32_t key1_y;     // the second key's y (the lift curve's target), as the emitter built it
    int32_t reserved;
};
constexpr uint32_t LOG = 32;
sprite_call g_sprites[LOG];
uint32_t g_sprite_n = 0;
eval_call g_evals[LOG];
uint32_t g_eval_n = 0;
uint32_t g_appends = 0;
int16_t g_pose_alpha = 0x80;
int16_t g_pose_scale = 0x100;
// Label doubles: the message lookup answers g_message; text init records (node, text, resource) and writes the text
// pointer at +0x28 as 0x2b3400 does; measure answers 8 px per byte by 20; the list D append links +0xf8/+0x100 and
// the list head at +0x40 as 0x3f14d0 does; usable answers g_usable.
struct init_call {
    uint64_t node;
    uint64_t text;
    uint64_t resource;
};
init_call g_inits[LOG];
uint32_t g_init_n = 0;
uint32_t g_message_calls = 0;
uint32_t g_text_appends = 0;
uint64_t g_message = 0;
int32_t g_usable = 1;

uint64_t dbl_alloc(uint64_t pool)
{
    int32_t* p = reinterpret_cast<int32_t*>(pool);
    const int32_t count = p[3];
    if (count >= p[2]) {
        return 0;
    }
    p[3] = count + 1;
    uint8_t* obj = reinterpret_cast<uint8_t*>(*reinterpret_cast<uint64_t*>(pool)) + static_cast<uint64_t>(count) * 0x100;
    memset(obj, 0, 0x100);
    return reinterpret_cast<uint64_t>(obj);
}

void dbl_append(uint64_t list, uint64_t obj)
{
    uint64_t* l = reinterpret_cast<uint64_t*>(list);
    *reinterpret_cast<uint64_t*>(obj + 0xD0) = 0;
    *reinterpret_cast<uint64_t*>(obj + 0xD8) = 0;
    if (l[0] == 0) {
        l[0] = obj;
        l[2] = obj;
    } else {
        *reinterpret_cast<uint64_t*>(l[2] + 0xD0) = obj;
        *reinterpret_cast<uint64_t*>(obj + 0xD8) = l[2];
        l[2] = obj;
    }
    ++g_appends;
}

void dbl_sprite(uint64_t record, uint64_t obj, uint64_t frame, int32_t alpha, uint32_t rgba, uint64_t texture)
{
    if (g_sprite_n < LOG) {
        g_sprites[g_sprite_n++] = {record, obj, frame, static_cast<int16_t>(alpha), rgba, texture};
    }
    float* quad = reinterpret_cast<float*>(obj + pr::OBJ_QUAD);
    quad[0] = -16.0f;
    quad[1] = -57.0f;
    quad[2] = 16.0f;
    quad[3] = 0.0f;
    *reinterpret_cast<int32_t*>(obj + 0x4C) = (0x80 * static_cast<int16_t>(alpha)) >> 7;
}

void dbl_eval(uint8_t* out, uint64_t keys, int32_t count, int32_t time)
{
    const int8_t* k = reinterpret_cast<const int8_t*>(keys);
    if (g_eval_n < LOG) {
        g_evals[g_eval_n++] = {keys, count, time, count == 2 ? k[9] : 0, 0};
    }
    int8_t x = 0;
    int8_t y = -10;
    int16_t alpha = g_pose_alpha;
    if (count == 2) {
        y = time >= 0x100 ? k[9] : 0;  // a lift or label curve: its first key's y until it has run
        alpha = static_cast<int16_t>((time < 0x100 ? time : 0x100) / 2);  // and its alpha 0 -> 0x80
    }
    out[0] = static_cast<uint8_t>(x);
    out[1] = static_cast<uint8_t>(y);
    memcpy(out + 2, &alpha, 2);
    memcpy(out + 4, &g_pose_scale, 2);
}

void dbl_scale(float* dst, const float* src, float s)
{
    const float y1 = src[3];
    dst[0] = src[0] * s;
    dst[1] = src[1] * s;
    dst[2] = src[2] * s;
    dst[3] = y1;
}

uint64_t dbl_message(uint32_t id)
{
    ++g_message_calls;
    return id == pr::LABEL_MESSAGE ? g_message : 0;
}

void dbl_text_init(uint64_t node, uint64_t text, uint64_t resource)
{
    if (g_init_n < LOG) {
        g_inits[g_init_n++] = {node, text, resource};
    }
    *reinterpret_cast<uint64_t*>(node + 0x28) = text;
}

void dbl_measure(uint64_t text, uint64_t zero, float scale, int32_t* w, int32_t* h)
{
    (void)zero;
    *w = static_cast<int32_t>(strlen(reinterpret_cast<const char*>(text)) * 8 * scale);
    *h = 20;
}

void dbl_text_append(uint64_t list, uint64_t node)
{
    *reinterpret_cast<uint64_t*>(node + 0x100) = 0;
    *reinterpret_cast<uint64_t*>(node + 0xF8) = 0;
    uint64_t* head = reinterpret_cast<uint64_t*>(list + 0x40);
    if (*head == 0) {
        *head = node;
    } else {
        uint64_t tail = *head;
        while (*reinterpret_cast<uint64_t*>(tail + 0xF8) != 0) {
            tail = *reinterpret_cast<uint64_t*>(tail + 0xF8);
        }
        *reinterpret_cast<uint64_t*>(tail + 0xF8) = node;
        *reinterpret_cast<uint64_t*>(node + 0x100) = tail;
    }
    ++g_text_appends;
}

int32_t dbl_usable(uint32_t handle)
{
    (void)handle;
    return g_usable;
}

const pr::game_fns g_doubles = {dbl_alloc,   dbl_append,    dbl_sprite,      dbl_eval,  dbl_scale,
                                dbl_message, dbl_text_init, dbl_measure, dbl_text_append, dbl_usable};

}  // namespace

extern "C" {

uint32_t ILCT_IxPromptSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(pr::prompt_seat), sizeof(pr::prompt_record), sizeof(pr::anim),
                              sizeof(pr::counters)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

uint32_t ILCT_IxPromptLevelOf(uint32_t state) { return pr::level_of(state); }
uint32_t ILCT_IxPromptShownLevel(uint32_t level, int32_t can) { return pr::shown_level(level, can != 0); }

uint32_t ILCT_IxPromptSeatLevel(uint32_t state, int32_t chest, int32_t leader_free)
{
    return pr::seat_level(state, chest != 0, leader_free != 0);
}

int32_t ILCT_IxPromptLabelString(const uint8_t* prefix, uint32_t prefix_len, uint32_t config, const uint8_t* name,
                                 uint32_t name_len, uint8_t* out, uint32_t* out_len)
{
    return pr::label_string(prefix, prefix_len, config, name, name_len, out, out_len) ? 1 : 0;
}

int32_t ILCT_IxPromptAnimStep(pr::anim* a, uint32_t actor, uint32_t object, uint32_t level, int32_t labelled,
                              uint32_t divisor, uint64_t now_ms)
{
    return pr::anim_step(*a, actor, object, level, labelled != 0, divisor, now_ms) ? 1 : 0;
}

void ILCT_IxPromptReset()
{
    pr::test_reset();
    g_sprite_n = 0;
    g_eval_n = 0;
    g_appends = 0;
    g_pose_alpha = 0x80;
    g_pose_scale = 0x100;
    g_init_n = 0;
    g_message_calls = 0;
    g_text_appends = 0;
    g_message = 0;
    g_usable = 1;
}

void ILCT_IxPromptSetLabelDoubles(uint64_t message, int32_t usable)
{
    g_message = message;
    g_usable = usable;
}

void ILCT_IxPromptNode(uint32_t slot, uint8_t* out)
{
    const uint8_t* node = pr::test_node(slot);
    if (node != nullptr) {
        memcpy(out, node, pr::NODE_SIZE);
    }
}

uint64_t ILCT_IxPromptNodeAddress(uint32_t slot) { return reinterpret_cast<uint64_t>(pr::test_node(slot)); }

uint32_t ILCT_IxPromptInits(uint64_t* out, uint32_t cap)
{
    uint32_t n = 0;
    for (; n < g_init_n && n < cap; ++n) {
        const uint64_t row[3] = {g_inits[n].node, g_inits[n].text, g_inits[n].resource};
        memcpy(out + 3 * n, row, sizeof(row));
    }
    return n;
}

uint32_t ILCT_IxPromptLabelCalls(uint32_t* out)
{
    out[0] = g_message_calls;
    out[1] = g_text_appends;
    return g_init_n;
}

void ILCT_IxPromptUseDoubles(int32_t on) { pr::test_set_fns(on != 0 ? &g_doubles : nullptr); }
void ILCT_IxPromptSetImageBase(uint64_t base) { pr::test_set_image_base(static_cast<uintptr_t>(base)); }
void ILCT_IxPromptSetPose(int32_t alpha, int32_t scale)
{
    g_pose_alpha = static_cast<int16_t>(alpha);
    g_pose_scale = static_cast<int16_t>(scale);
}
int32_t ILCT_IxPromptAttach(uint64_t base) { return pr::attach(static_cast<uintptr_t>(base)) ? 1 : 0; }
int32_t ILCT_IxPromptAttached() { return pr::attached() ? 1 : 0; }
void ILCT_IxPromptPublish(const pr::prompt_record* r) { pr::publish(*r); }
void ILCT_IxPromptWithdraw() { pr::withdraw(); }
int32_t ILCT_IxPromptRead(pr::prompt_record* out) { return pr::test_read(out) ? 1 : 0; }
void ILCT_IxPromptAnims(pr::anim* out) { memcpy(out, pr::test_anims(), sizeof(pr::anim) * interaction::SLOTS); }
int32_t ILCT_IxPromptRow() { return pr::test_row(); }
int32_t ILCT_IxPromptCall(patch::ctx* c, uint32_t epoch) { return pr::prompt_call(c, epoch); }
void ILCT_IxPromptAfter(patch::ctx* c, uint32_t epoch) { pr::prompt_after(c, epoch); }
void ILCT_IxPromptStats(pr::counters* out) { memcpy(out, const_cast<const pr::counters*>(pr::stats()), sizeof(*out)); }

uint32_t ILCT_IxPromptSprites(uint64_t* out, uint32_t cap)
{
    uint32_t n = 0;
    for (; n < g_sprite_n && n < cap; ++n) {
        const sprite_call& s = g_sprites[n];
        const uint64_t row[6] = {s.record, s.obj, s.frame, static_cast<uint64_t>(s.alpha), s.rgba, s.texture};
        memcpy(out + 6 * n, row, sizeof(row));
    }
    return n;
}

uint32_t ILCT_IxPromptEvals(int64_t* out, uint32_t cap)
{
    uint32_t n = 0;
    for (; n < g_eval_n && n < cap; ++n) {
        const eval_call& e = g_evals[n];
        const int64_t row[4] = {static_cast<int64_t>(e.keys), e.count, e.time, e.key1_y};
        memcpy(out + 4 * n, row, sizeof(row));
    }
    return n;
}

uint32_t ILCT_IxPromptAppends() { return g_appends; }

}  // extern "C"
