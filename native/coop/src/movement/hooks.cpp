// Ivalice Legacy co-op module: the movement detour handlers (see movement.h for what each site does).
//
// Every handler is a fail_open Body over the steering record: it runs on the game thread, never blocks, allocates
// or calls a game function, and keeps to the site's contract (which registers it may change). Pointers in the
// record are compared with the game's own registers first; game memory is read or written through the fault-safe
// probe and only for an actor the record names.
#include <string.h>

#include "movement/movement.h"
#include "movement/movement_facts.h"
#include "patch/call_island.h"
#include "patch/detour.h"
#include "runtime/probe.h"
#include "session/game_facts.h"

namespace ilc::movement {

namespace detail {
extern patch::record_slot<steer_record> g_slot;
extern counters g_counters;
}  // namespace detail

namespace {

using patch::ctx;
using patch::RESUME;
using patch::RUN_ORIGINAL;

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

constexpr bool movement_row(int32_t row, uint8_t kind)
{
    return row >= 0 && patch::SITES[row].policy == ILC_SITE_POLICY_PATCH &&
           patch::SITES[row].subsystem == ILC_SITE_SUBSYS_MOVEMENT && patch::SITES[row].kind == kind;
}

constexpr int32_t MV01 = find_row("MV-01");
constexpr int32_t MV02 = find_row("MV-02");
constexpr int32_t MV03 = find_row("MV-03");
constexpr int32_t MV05 = find_row("MV-05");
constexpr int32_t MV06 = find_row("MV-06");
static_assert(movement_row(MV01, ILC_SITE_KIND_DETOUR) && movement_row(MV02, ILC_SITE_KIND_DETOUR) &&
                  movement_row(MV03, ILC_SITE_KIND_DETOUR) && movement_row(MV05, ILC_SITE_KIND_DETOUR) &&
                  movement_row(MV06, ILC_SITE_KIND_DETOUR),
              "the movement handlers here are for the non-call movement detours");
constexpr int32_t MV04 = find_row("MV-04");
static_assert(movement_row(MV04, ILC_SITE_KIND_CALL) && movement_row(find_row("MV-07"), ILC_SITE_KIND_CALL),
              "MV-04 and MV-07 are call-type sites (hooked through a call island)");
static_assert(patch::island::is_call_row(static_cast<uint32_t>(MV04)), "MV-04 runs through a call island");

// Code addresses the handlers continue at: the image base is pinned, so a VA is the runtime address.
constexpr uint64_t va(uintptr_t v)
{
    return static_cast<uint64_t>(patch::IMAGE_BASE + (v - session::facts::IMAGE_VA));
}
static_assert(patch::IMAGE_BASE == session::facts::IMAGE_VA, "the pinned image base");

void bump(volatile uint64_t& counter)
{
    __atomic_add_fetch(&counter, 1, __ATOMIC_RELAXED);
}

bool load_u64(uint64_t address, uint64_t* out)
{
    return address != 0 && copy_from_caller(out, reinterpret_cast<const void*>(address), sizeof(*out));
}

uint32_t float_bits(float f)
{
    uint32_t bits;
    memcpy(&bits, &f, sizeof(bits));
    return bits;
}

void set_xmm0_s0(ctx* c, float value)
{
    c->xmm[0][0] = (c->xmm[0][0] & 0xFFFFFFFF00000000ull) | float_bits(value);
}

// The record names this scene root: its actor and physics pointers still lead back to the same objects.
bool root_matches(uint64_t root, const steer_entry& e)
{
    uint64_t actor = 0;
    uint64_t phys = 0;
    return load_u64(root + facts::ROOT_ACTOR, &actor) && actor == e.actor &&
           load_u64(root + facts::ROOT_PHYSICS, &phys) && phys == e.phys;
}

// ---- MV-01: locomotion vector entry -----------------------------------------------------------------------------
// Live at entry: rcx = root, rdx = out vector, r8d = root-motion select, r9 = root-motion vector. May change the
// physics heading/pitch/command before the body runs, and r8d.
int32_t mv01_body(ctx* c, const steer_record& r)
{
    if ((r.head.flags & STEERF_MOVE) == 0) {
        return RUN_ORIGINAL;
    }
    const int32_t k = slot_of_root(r, c->rcx);
    if (k < 0) {
        return RUN_ORIGINAL;  // not a guest's actor: the overwhelmingly common case, no memory touched
    }
    const steer_entry& e = r.slot[k];
    if (!root_matches(c->rcx, e)) {
        return RUN_ORIGINAL;
    }
    const float pitch_heading[2] = {0.0f, e.heading};
    if (!copy_to_caller(reinterpret_cast<void*>(e.phys + facts::PHYS_PITCH), pitch_heading, sizeof(pitch_heading)) ||
        !copy_to_caller(reinterpret_cast<void*>(e.phys + facts::PHYS_MAGNITUDE), &e.magnitude, sizeof(float))) {
        return RUN_ORIGINAL;
    }
    bump(detail::g_counters.vectors_written);

    // The function takes the root-motion vector whenever r8d is set and then ignores the physics command. An
    // empty root-motion record can stay selected after the follower AI lets go of an actor, which would freeze a
    // guest who is steering. Select the steering path only for real input, outside an execution motion, and only
    // when that vector is exactly zero (+0 or -0 in every lane). Real attack, knockback and scripted motion keep it.
    if ((float_bits(e.magnitude) & 0x7FFFFFFFu) == 0 || static_cast<uint32_t>(c->r8) == 0 || c->r9 == 0) {
        return RUN_ORIGINAL;
    }
    uint64_t flags = 0;
    if (!load_u64(e.actor + facts::ACTOR_FLAGS, &flags) || (flags & facts::ACTOR_EXECUTION_BIT) != 0) {
        return RUN_ORIGINAL;
    }
    uint32_t vec[3];
    if (!copy_from_caller(vec, reinterpret_cast<const void*>(c->r9), sizeof(vec))) {
        return RUN_ORIGINAL;
    }
    if (((vec[0] | vec[1] | vec[2]) & 0x7FFFFFFFu) == 0) {
        c->r8 = 0;  // as `xor r8d, r8d`
        bump(detail::g_counters.root_motion_overridden);
    }
    return RUN_ORIGINAL;
}

// ---- MV-02: steering heading store ------------------------------------------------------------------------------
// Live: xmm0.s0 = heading about to be stored, rdi = physics object. May change xmm0.s0.
int32_t mv02_body(ctx* c, const steer_record& r)
{
    if ((r.head.flags & STEERF_MOVE) == 0) {
        return RUN_ORIGINAL;
    }
    const int32_t k = slot_of_phys(r, c->rdi);
    if (k >= 0) {
        set_xmm0_s0(c, r.slot[k].heading);
    }
    return RUN_ORIGINAL;
}

// ---- MV-03: facing store ----------------------------------------------------------------------------------------
// Live: xmm0.s0 = facing about to be stored at actor +0x15C, rbx = actor. May change xmm0.s0.
int32_t mv03_body(ctx* c, const steer_record& r)
{
    if ((r.head.flags & STEERF_MOVE) == 0) {
        return RUN_ORIGINAL;
    }
    const int32_t k = slot_of_actor(r, c->rbx);
    if (k < 0) {
        return RUN_ORIGINAL;
    }
    const steer_entry& e = r.slot[k];
    uint64_t root = 0;
    uint64_t phys = 0;
    if (!load_u64(e.actor + session::facts::ACTOR_ROOT, &root) || root != e.root ||
        !load_u64(root + facts::ROOT_PHYSICS, &phys) || phys != e.phys) {
        return RUN_ORIGINAL;
    }
    set_xmm0_s0(c, e.visual);
    return RUN_ORIGINAL;
}

// ---- MV-04: visual facing call (a call row: registers at the callee's entry) ------------------------------------
// Live: rcx = root (the callee's argument), rbx = actor. The body's facing is not a plain store: the game's visual
// facing setter 0x2E5E10(root, facing, pitch, 0) also rebuilds the orientation it derives from it. So for a guest's
// actor whose physics facing differs from the guest's visual heading, call that setter here (as the game itself
// does elsewhere), then let the original read run. A call row's handler frame has static unwind data, so a fault
// inside the setter unwinds through us to the game function; no game call happens on a mid-function detour.
using visual_setter_fn = void (*)(uint64_t root, float facing, float pitch, int32_t flags);

int32_t mv04_body(ctx* c, const steer_record& r)
{
    if ((r.head.flags & STEERF_MOVE) == 0) {
        return RUN_ORIGINAL;
    }
    const int32_t k = slot_of_root(r, c->rcx);
    if (k < 0 || c->rbx != r.slot[k].actor) {
        return RUN_ORIGINAL;
    }
    const steer_entry& e = r.slot[k];
    if (!root_matches(c->rcx, e)) {
        return RUN_ORIGINAL;
    }
    float facing_pitch[2];
    if (!copy_from_caller(facing_pitch, reinterpret_cast<const void*>(e.phys + facts::PHYS_VISUAL),
                          sizeof(facing_pitch))) {
        return RUN_ORIGINAL;
    }
    if (facing_pitch[0] == e.visual) {
        return RUN_ORIGINAL;  // already facing that way: nothing to rebuild
    }
    const visual_setter_fn setter = reinterpret_cast<visual_setter_fn>(va(facts::VISUAL_FACING_SETTER));
    setter(c->rcx, e.visual, facing_pitch[1], 0);
    bump(detail::g_counters.facings_set);
    return RUN_ORIGINAL;
}

// ---- MV-05: follower list evaluator -----------------------------------------------------------------------------
// Live: r13 = actor, r10 = list. A controlled actor evaluates either native follower list as empty: the empty
// exit is valid from the site (rbx, r12, r14, r15 are not saved yet).
int32_t mv05_body(ctx* c, const steer_record& r)
{
    if ((r.head.flags & STEERF_LOGICAL) == 0 || slot_of_actor(r, c->r13) < 0) {
        return RUN_ORIGINAL;
    }
    if (c->r10 != va(session::facts::FOLLOW_LIST_PRIMARY) && c->r10 != va(session::facts::FOLLOW_LIST_SECONDARY)) {
        return RUN_ORIGINAL;
    }
    bump(detail::g_counters.list_rejected);
    c->resume = va(facts::FOLLOWER_LIST_EMPTY_EXIT);
    return RESUME;
}

// ---- MV-06: approach gate ---------------------------------------------------------------------------------------
// Live: eax = leader handle, ebx = the actor's handle, rdi = actor, caller's rbx at [rsp+0x30]. A controlled actor
// takes the no-approach exit, which does not restore rbx: restore it here first (the displaced instruction).
// Uncontrolled actors run the original compare and load (the native flags feed the next branch).
int32_t mv06_body(ctx* c, const steer_record& r)
{
    if ((r.head.flags & STEERF_LOGICAL) == 0 || slot_of_actor(r, c->rdi) < 0) {
        return RUN_ORIGINAL;
    }
    // [rsp+0x30] is this thread's own live frame (the original instruction reads it next).
    c->rbx = *reinterpret_cast<const volatile uint64_t*>(c->rsp + facts::APPROACH_SAVED_RBX);
    bump(detail::g_counters.approach_blocked);
    c->resume = va(facts::APPROACH_NO_EXIT);
    return RESUME;
}

}  // namespace

int32_t mv01(ctx* c, uint32_t epoch)
{
    return patch::fail_open<steer_record, detail::g_slot, mv01_body>(c, epoch);
}

int32_t mv02(ctx* c, uint32_t epoch)
{
    return patch::fail_open<steer_record, detail::g_slot, mv02_body>(c, epoch);
}

int32_t mv03(ctx* c, uint32_t epoch)
{
    return patch::fail_open<steer_record, detail::g_slot, mv03_body>(c, epoch);
}

int32_t mv04(ctx* c, uint32_t epoch)
{
    return patch::fail_open<steer_record, detail::g_slot, mv04_body>(c, epoch);
}

int32_t mv05(ctx* c, uint32_t epoch)
{
    return patch::fail_open<steer_record, detail::g_slot, mv05_body>(c, epoch);
}

int32_t mv06(ctx* c, uint32_t epoch)
{
    return patch::fail_open<steer_record, detail::g_slot, mv06_body>(c, epoch);
}

int32_t row_of(const char* id)
{
    return find_row(id);
}

bool attach()
{
    struct binding {
        int32_t row;
        patch::handler_fn fn;
    };
    const binding rows[] = {{MV01, mv01}, {MV02, mv02}, {MV03, mv03}, {MV04, mv04}, {MV05, mv05}, {MV06, mv06}};
    if (!gtq::register_op(OP_FOLLOW, ILC_SITE_SUBSYS_MOVEMENT, op_follow)) {
        return false;
    }
    for (const binding& b : rows) {
        if (!patch::set_handler(static_cast<uint32_t>(b.row), b.fn) ||
            !patch::set_site_armed(static_cast<uint32_t>(b.row), true)) {
            return false;
        }
    }
    return true;
}

}  // namespace ilc::movement
