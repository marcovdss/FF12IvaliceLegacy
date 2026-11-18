// Ivalice Legacy co-op module: the shared camera (see camera.h).
#include "camera/camera.h"

#include <math.h>
#include <string.h>

#include "patch/call_island.h"
#include "patch/patch_engine.h"
#include "runtime/probe.h"
#include "session/policy.h"
#include "session/reader.h"
#include "session/session.h"

// The CM-07 continuation: xmm6 *= the boom the handler left in xmm2's low lane, then back to the game right after
// the replaced mulss. xmm2 is dead at 0x32CD79: it is volatile across the call at 0x32CD74 just before, nothing up to
// 0x32CD8B reads it, and 0x32CD8B reloads it (`movss xmm2,[rsi+0x34]`), with no branch in between. No stack use, and
// mulss changes no flags.
extern "C" {
void* volatile ilc_camera_cm07_return = nullptr;
extern const char ilc_camera_cm07_scale[];
}
asm(".text\n"
    ".p2align 4\n"
    ".globl ilc_camera_cm07_scale\n"
    "ilc_camera_cm07_scale:\n"
    "    mulss %xmm2, %xmm6\n"
    "    jmpq *ilc_camera_cm07_return(%rip)\n");

namespace ilc::camera {
namespace {

// ---- game facts (FFXII_TZA.exe 1.0.4.0; VAs at the pinned base) ---------------------------------------------------
constexpr uint64_t PINNED_BASE = 0x120000;
constexpr uint64_t CAMERA_MASTER = 0x2AED630;   // VA: +0x90 eye xyz, +0xA0 focus xyz
constexpr uint64_t PRIMARY_RIG = 0x2098060;     // VA: +0x30 yaw, +0x34 pitch, +0x38 boom, +0x40 flags
constexpr uint32_t MASTER_EYE = 0x90;
constexpr uint32_t MASTER_FOCUS = 0xA0;
constexpr uint32_t RIG_YAW = 0x30;
constexpr uint32_t RIG_BOOM = 0x38;
constexpr uint32_t RIG_FLAGS = 0x40;
constexpr uint32_t RIG_BACK_ON_WALL = 0x04000000;  // NOTADDR: flag bit
constexpr uint32_t CONTROLLER_FOCUS = 0x60;      // [rdi+0x60..0x68] focus, +0x6C weight
constexpr uint64_t CM01_RESUME = 0x32B26A;      // VA: after the replaced focus store
constexpr uint64_t CM06_RESUME = 0x32CD6A;      // VA: after the replaced mulss
constexpr uint64_t CM07_RESUME = 0x32CD7E;      // VA: after the replaced mulss
constexpr double MANUAL_ORBIT_RADIANS = 0.012;
constexpr double MANUAL_ORBIT_HOLD_S = 0.35;
constexpr float RETRY_STEP = 0.75f;
constexpr float MAX_RAY_RATIO = 0.98f;

constexpr uint64_t game(uint64_t va)
{
    return patch::IMAGE_BASE + (va - PINNED_BASE);
}

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

constexpr int32_t ROW_CM01 = find_row("CM-01");
constexpr int32_t ROW_CM02 = find_row("CM-02");
constexpr int32_t ROW_CM03 = find_row("CM-03");
constexpr int32_t ROW_CM04 = find_row("CM-04");
constexpr int32_t ROW_CM05 = find_row("CM-05");
constexpr int32_t ROW_CM06 = find_row("CM-06");
constexpr int32_t ROW_CM07 = find_row("CM-07");
static_assert(ROW_CM01 >= 0 && ROW_CM06 >= 0 && ROW_CM07 >= 0 && !patch::island::is_call_row(ROW_CM01) &&
                  !patch::island::is_call_row(ROW_CM06) && !patch::island::is_call_row(ROW_CM07),
              "focus and zoom sites are mid-function rows");
static_assert(patch::island::is_call_row(ROW_CM02) && patch::island::is_call_row(ROW_CM03) &&
                  patch::island::is_call_row(ROW_CM04) && patch::island::is_call_row(ROW_CM05),
              "the collision query calls are call rows: the wrapper calls the query itself");
static_assert(patch::island::callee_rva(ROW_CM02) == 0x110C00 && patch::island::callee_rva(ROW_CM05) == 0x110C00,
              "the wrapped function is the collision query 0x230C00");
static_assert(patch::SITES[ROW_CM02].subsystem == ILC_SITE_SUBSYS_CAMERA &&
                  patch::SITES[ROW_CM07].subsystem == ILC_SITE_SUBSYS_CAMERA,
              "camera rows carry the CAMERA arm bit");

// ---- state ------------------------------------------------------------------------------------------------------
patch::record_slot<pose_record> g_pose;
patch::record_slot<collision_record> g_collision;
counters g_counters;
volatile uint32_t g_reset_request = 0;

struct core {
    uintptr_t base;
    uint8_t started;
    uint8_t was_requested;
    uint8_t active;
    uint8_t have_yaw;
    session::reacquire acquire;
    uint64_t last_ms;
    controller ctl;
    latch_guard latch;
    double orbit_hold;
    double previous_yaw;
    uint8_t signature[44];
    uint8_t have_signature;
    uint8_t corridor_on;
    uint8_t reserved[2];
    uint32_t roles;
    double published_extra;
    double native_boom;
};
core g;

void count(volatile uint64_t& c)
{
    __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED);
}

bool read_game(uint64_t address, void* out, size_t n)
{
    return ilc::read_game(out, reinterpret_cast<const void*>(address), n);
}

float f32(uint64_t bits)
{
    float f;
    const uint32_t lo = static_cast<uint32_t>(bits);
    memcpy(&f, &lo, 4);
    return f;
}

uint64_t bits_of(float f)
{
    uint32_t b;
    memcpy(&b, &f, 4);
    return b;
}

bool fresh(uint64_t stamp_ms)
{
    const uint64_t now = patch::now_ms();
    if (now < stamp_ms || now - stamp_ms > POSE_STALE_MS) {
        count(g_counters.stale);
        return false;
    }
    return true;
}

// ---- hook bodies -------------------------------------------------------------------------------------------------
int32_t body01(patch::ctx* c, const pose_record& r)
{
    if ((r.head.flags & CAM_ACTIVE) == 0 || !fresh(r.stamp_ms)) {
        return patch::RUN_ORIGINAL;
    }
    // The pose is published at the co-op beat; the game renders faster. Predict along the focus velocity for the
    // record's age (at most 50 ms), as the render-rate resampler would.
    const uint64_t now = patch::now_ms();
    double age = static_cast<double>(now - r.stamp_ms) / 1000.0;
    age = age < PREDICT_MAX_S ? age : PREDICT_MAX_S;
    const float values[4] = {static_cast<float>(r.focus[0] + r.velocity[0] * age),
                             static_cast<float>(r.focus[1] + r.velocity[1] * age),
                             static_cast<float>(r.focus[2] + r.velocity[2] * age), 1.0f};
    if (!store_game(reinterpret_cast<void*>(c->rdi + CONTROLLER_FOCUS), values, sizeof(values))) {
        return patch::RUN_ORIGINAL;
    }
    count(g_counters.focus_written);
    c->resume = game(CM01_RESUME);
    return patch::RESUME;
}

// The boom the rig multiplication uses: rig boom + extra, only for the primary rig and never against a wall.
bool zoom_total(const patch::ctx* c, const pose_record& r, float* total)
{
    if ((r.head.flags & CAM_ACTIVE) == 0 || !fresh(r.stamp_ms)) {
        return false;
    }
    uint32_t flags = 0;
    float boom = 0.0f;
    if (c->rsi != game(PRIMARY_RIG) || !read_game(c->rsi + RIG_FLAGS, &flags, 4) ||
        (flags & RIG_BACK_ON_WALL) != 0 || !read_game(c->rsi + RIG_BOOM, &boom, 4)) {
        count(g_counters.zoom_yielded);
        return false;
    }
    *total = boom + r.extra;
    count(g_counters.zoom_applied);
    return true;
}

int32_t body06(patch::ctx* c, const pose_record& r)
{
    // mulss xmm3, [rsi+0x38] with rig boom + extra instead; the two movss that follow run as they are.
    float total;
    if (!zoom_total(c, r, &total)) {
        return patch::RUN_ORIGINAL;
    }
    const float product = f32(c->xmm[3][0]) * total;
    c->xmm[3][0] = (c->xmm[3][0] & 0xFFFFFFFF00000000ull) | bits_of(product);
    c->resume = game(CM06_RESUME);
    return patch::RESUME;
}

int32_t body07(patch::ctx* c, const pose_record& r)
{
    // mulss xmm6, [rsi+0x38] with rig boom + extra: XMM6 is not in the saved state, so the continuation thunk does
    // the multiplication with the boom handed over in xmm2's low lane (dead here; see the thunk). Restored by the stub.
    float total;
    if (!zoom_total(c, r, &total)) {
        return patch::RUN_ORIGINAL;
    }
    c->xmm[2][0] = (c->xmm[2][0] & 0xFFFFFFFF00000000ull) | bits_of(total);
    c->resume = reinterpret_cast<uint64_t>(ilc_camera_cm07_scale);
    return patch::RESUME;
}

using query_fn = uint32_t (*)(uint64_t, float*, float*, float*, uint64_t, uint64_t);

// The collision query wrapper (call row: registers at the query's entry; ctx->rsp points at the return address,
// stack arguments 5 and 6 at +0x28/+0x30).
int32_t body_collision(patch::ctx* c, const collision_record& r)
{
    const uint32_t count_caps = r.capsule_count < MAX_CAPSULES ? r.capsule_count : MAX_CAPSULES;
    if ((r.head.flags & CAM_ACTIVE) == 0 || (count_caps == 0 && r.escape_max2 <= 0.0f)) {
        return patch::RUN_ORIGINAL;  // nothing to classify: the game's own query, untouched
    }
    const uint32_t row = static_cast<uint32_t>(c->index);
    const query_fn query = reinterpret_cast<query_fn>(patch::trampoline(row));  // call rows: the callee
    uint64_t back = 0;  // the return address: read before anything runs, so a failed read is the game's own call
    uint64_t stack[2];
    float start[4], end[4];
    if (query == nullptr || !read_game(c->rsp, &back, sizeof(back)) ||
        !read_game(c->rsp + 0x28, stack, sizeof(stack)) || !read_game(c->r8, start, sizeof(start)) ||
        !read_game(c->r9, end, sizeof(end))) {
        return patch::RUN_ORIGINAL;
    }
    float* const hit_out = reinterpret_cast<float*>(c->rdx);
    alignas(16) float retry_start[4];
    float retry_t = 0.0f;
    uint32_t internal = 0;
    __atomic_add_fetch(&g_counters.queries, 1, __ATOMIC_RELAXED);
    uint32_t result = query(c->rcx, hit_out, reinterpret_cast<float*>(c->r8), reinterpret_cast<float*>(c->r9),
                            stack[0], stack[1]);
    while (result != 0) {
        float hit[3];
        if (!read_game(c->rdx, hit, sizeof(hit))) {
            break;  // cannot classify: keep the game's hit
        }
        bool inside = false;
        if (r.escape_max2 > 0.0f) {
            const float dx = hit[0] - start[0], dy = hit[1] - start[1], dz = hit[2] - start[2];
            const float d2 = dx * dx + dy * dy + dz * dz;
            if (!(d2 == d2)) {
                break;  // NaN: external
            }
            if (d2 <= r.escape_max2) {
                inside = true;
                count(g_counters.focus_internal);
            }
        }
        if (!inside) {
            if (count_caps == 0) {
                break;  // no corridor: the hit stands, uncounted
            }
            for (uint32_t k = 0; k < count_caps && !inside; ++k) {
                inside = inside_capsule(r.capsules[k], hit);
            }
            if (!inside) {
                __atomic_add_fetch(&g_counters.hits[3], 1u, __ATOMIC_RELAXED);
                break;  // external: the game's hit stands
            }
        }
        __atomic_add_fetch(&g_counters.hits[0], 1u, __ATOMIC_RELAXED);
        if (++internal > MAX_RETRIES) {
            __atomic_add_fetch(&g_counters.hits[2], 1u, __ATOMIC_RELAXED);
            result = 0;
            break;
        }
        // Ask again from just past this hit, along the original ray (never backwards, never past 98% of it).
        const float sx = end[0] - start[0], sz = end[2] - start[2];
        const float length = sqrtf(sx * sx + sz * sz);
        if (!(length > 1.0e-6f)) {
            __atomic_add_fetch(&g_counters.hits[2], 1u, __ATOMIC_RELAXED);
            result = 0;
            break;
        }
        float t = ((hit[0] - start[0]) * sx + (hit[2] - start[2]) * sz) / length + RETRY_STEP;
        t = t > retry_t + RETRY_STEP ? t : retry_t + RETRY_STEP;
        retry_t = t;
        const float ratio = t / length;
        if (!(ratio < MAX_RAY_RATIO)) {
            __atomic_add_fetch(&g_counters.hits[2], 1u, __ATOMIC_RELAXED);
            result = 0;
            break;
        }
        for (int a = 0; a < 3; ++a) {
            retry_start[a] = start[a] + (end[a] - start[a]) * ratio;
        }
        retry_start[3] = start[3];
        __atomic_add_fetch(&g_counters.hits[1], 1u, __ATOMIC_RELAXED);
        __atomic_add_fetch(&g_counters.queries, 1, __ATOMIC_RELAXED);
        result = query(c->rcx, hit_out, retry_start, reinterpret_cast<float*>(c->r9), stack[0], stack[1]);
    }
    c->rax = result;
    c->resume = back;
    return patch::RESUME;
}

// ---- co-op thread ------------------------------------------------------------------------------------------------
void withdraw_all()
{
    g_pose.withdraw();
    g_collision.withdraw();
}

}  // namespace

void start(uintptr_t image_base)
{
    const uint32_t keep = g.ctl.reset_epoch;
    g = core{};
    g.base = image_base;
    g.started = 1;
    g.ctl.t = tuning();
    g.ctl.reset_epoch = keep;
    reset(g.ctl);
    reset(g.latch);
    withdraw_all();
}

void step(uint64_t now_ms)
{
    if (!g.started) {
        return;
    }
    count(g_counters.steps);
    const double dt = g.last_ms != 0 && now_ms > g.last_ms ? static_cast<double>(now_ms - g.last_ms) / 1000.0 : 0.02;
    g.last_ms = now_ms;
    session::session_record s;
    const uint32_t epoch = session::epoch();
    if (epoch == 0 || !session::read(&s) || s.head.epoch != epoch) {
        g.was_requested = 0;
        g.active = 0;
        withdraw_all();
        return;
    }
    // Roles: P1 if alive, each manual guest with an actor that is not down.
    session::game_reader rd(g.base);
    member m[MAX_MEMBERS];
    uint32_t n = 0;
    for (uint32_t seat = 0; seat < ILC_ACTIVE_SEATS_MAX && seat < ILC_SEATS; ++seat) {
        const session::seat_entry& e = s.seat[seat];
        const bool known_dead = (e.flags & session::SEATF_ALIVE_KNOWN) != 0 && (e.flags & session::SEATF_ALIVE) == 0;
        const bool counts = seat == 0 ? (e.flags & session::SEATF_ALIVE) != 0 && (e.flags & session::SEATF_ACTOR) != 0
                                      : (e.flags & session::SEATF_MANUAL) != 0 &&
                                            (e.flags & session::SEATF_ACTOR) != 0 && !known_dead;
        float p[3];
        if (counts && e.actor != 0 && rd.read(static_cast<uintptr_t>(e.actor) + session::facts::ACTOR_POSITION, p, sizeof(p))) {
            m[n++] = {seat, actor_key_of(e.actor), {p[0], p[1], p[2]}};
        }
    }
    g.roles = n;
    const bool requested = (s.head.flags & session::GATE_WORLD) != 0 && (s.head.flags & session::GATE_CAMERA) != 0 &&
                           n >= 2;
    const bool reset_requested = __atomic_exchange_n(&g_reset_request, 0u, __ATOMIC_ACQ_REL) != 0;

    float positions[MAX_MEMBERS * 3];
    for (uint32_t i = 0; i < n; ++i) {
        positions[i * 3 + 0] = static_cast<float>(m[i].position.x);
        positions[i * 3 + 1] = static_cast<float>(m[i].position.y);
        positions[i * 3 + 2] = static_cast<float>(m[i].position.z);
    }
    float master[7] = {};
    const bool master_ok = rd.read(rd.global(CAMERA_MASTER) + MASTER_EYE, master, sizeof(master));
    bool native_ready = false;
    if (requested && master_ok && session::camera_positions_ready(positions, n)) {
        const double d = native_focus_distance(g.ctl.t, {master[4], master[5], master[6]}, m, n);
        native_ready = d >= 0.0 && d <= g.ctl.t.native_seed_max_distance;
    }
    if (reset_requested && g.active && requested) {
        // A seat toggle while the shared camera stays on (still two or more roles): reset in place, as the reference
        // does on a toggle. A fresh controller (new reset epoch: the hooks snap) seeds at the new party centre on this
        // very step and the records keep publishing, so the game's camera never takes over in between and nothing
        // waits for the game's focus to come back near the party. The three-sample wait is for off-to-on only.
        reset(g.ctl);
        reset(g.latch);
        g.have_signature = 0;
        g.corridor_on = 0;
        g.orbit_hold = 0.0;
        count(g_counters.resets);
    }
    g.acquire = session::camera_reacquire(requested, g.was_requested != 0, g.acquire, native_ready);
    g.was_requested = requested ? 1 : 0;
    const bool active = g.acquire.active != 0;
    if (active && !g.active) {
        reset(g.ctl);
        reset(g.latch);
        g.have_yaw = 0;
        g.orbit_hold = 0.0;
        g.have_signature = 0;
        g.corridor_on = 0;
    } else if (!active && g.active) {
        reset(g.ctl);
        reset(g.latch);
    }
    g.active = active ? 1 : 0;
    if (!active) {
        withdraw_all();
        return;
    }
    count(g_counters.active_steps);

    const frame f = update(g.ctl, m, n, dt);

    // The rig and the camera master: the limiter's inputs.
    float rig[3] = {};
    uint32_t flags = 0;
    const bool rig_ok = rd.read(rd.global(PRIMARY_RIG) + RIG_YAW, rig, sizeof(rig)) &&
                        rd.u32(rd.global(PRIMARY_RIG) + RIG_FLAGS, &flags);
    double extra = f.extra_distance;
    g.native_boom = rig_ok ? rig[2] : -1.0;
    if (rig_ok && master_ok) {
        uint8_t signature[sizeof(g.signature)];
        memcpy(signature, master, 28);
        memcpy(signature + 28, rig, 12);
        memcpy(signature + 40, &flags, 4);
        const bool new_sample = !g.have_signature || memcmp(signature, g.signature, sizeof(signature)) != 0;
        memcpy(g.signature, signature, sizeof(signature));
        g.have_signature = 1;
        g.orbit_hold = g.orbit_hold - dt > 0.0 ? g.orbit_hold - dt : 0.0;
        if (new_sample) {
            if (g.have_yaw) {
                const double dy = rig[0] - g.previous_yaw;
                const double wrapped = atan2(sin(dy), cos(dy));
                if (fabs(wrapped) >= MANUAL_ORBIT_RADIANS) {
                    g.orbit_hold = MANUAL_ORBIT_HOLD_S;  // P1 is orbiting: the limiter steps aside
                }
            }
            g.previous_yaw = rig[0];
            g.have_yaw = 1;
        }
        const double ex = master[0] - master[4], ey = master[1] - master[5], ez = master[2] - master[6];
        const double actual = sqrt(ex * ex + ey * ey + ez * ez);
        const bool wall = (flags & RIG_BACK_ON_WALL) != 0;
        uint32_t counters_now[4];
        for (int k = 0; k < 4; ++k) {
            counters_now[k] = __atomic_load_n(&g_counters.hits[k], __ATOMIC_RELAXED);
        }
        const latch_decision ld = observe(g.latch, g.ctl.t, counters_now, rig[2], wall, dt);
        if (ld.activated) {
            count(g_counters.latch_activations);
        }
        if (actual == actual && actual < 1e30) {
            extra = limited_extra(g.ctl, actual, rig[2], dt, new_sample, wall, g.orbit_hold <= 0.0, g.orbit_hold > 0.0);
        }
    }
    g.published_extra = extra;

    pose_record pose = {};
    pose.head.epoch = epoch;
    pose.head.flags = CAM_ACTIVE;
    pose.head.generation = s.head.generation;
    pose.stamp_ms = patch::now_ms();
    pose.focus[0] = static_cast<float>(f.focus.x);
    pose.focus[1] = static_cast<float>(f.focus.y);
    pose.focus[2] = static_cast<float>(f.focus.z);
    pose.velocity[0] = static_cast<float>(f.focus_velocity.x);
    pose.velocity[1] = static_cast<float>(f.focus_velocity.y);
    pose.velocity[2] = static_cast<float>(f.focus_velocity.z);
    pose.extra = static_cast<float>(extra);
    pose.reset_epoch = f.reset_epoch;
    g_pose.publish(pose);

    collision_record col = {};
    col.head = pose.head;
    col.capsule_count = corridor(g.ctl.t, m, n, g.native_boom, g.corridor_on != 0, col.capsules);
    g.corridor_on = col.capsule_count > 0 ? 1 : 0;
    col.escape_max2 = focus_escape(g.ctl.t, m, n, f.focus);
    g_collision.publish(col);
}

void stop()
{
    withdraw_all();
    g.started = 0;
}

void request_reset()
{
    __atomic_store_n(&g_reset_request, 1u, __ATOMIC_RELEASE);
}

int32_t cm01(patch::ctx* c, uint32_t epoch) { return patch::fail_open<pose_record, g_pose, body01>(c, epoch); }
int32_t cm06(patch::ctx* c, uint32_t epoch) { return patch::fail_open<pose_record, g_pose, body06>(c, epoch); }
int32_t cm07(patch::ctx* c, uint32_t epoch) { return patch::fail_open<pose_record, g_pose, body07>(c, epoch); }
int32_t collision(patch::ctx* c, uint32_t epoch)
{
    return patch::fail_open<collision_record, g_collision, body_collision>(c, epoch);
}

bool attach()
{
    __atomic_store_n(&ilc_camera_cm07_return, reinterpret_cast<void*>(game(CM07_RESUME)), __ATOMIC_RELEASE);
    const struct {
        int32_t row;
        patch::handler_fn fn;
    } hooks[] = {{ROW_CM01, cm01},      {ROW_CM02, collision}, {ROW_CM03, collision}, {ROW_CM04, collision},
                 {ROW_CM05, collision}, {ROW_CM06, cm06},      {ROW_CM07, cm07}};
    bool ok = true;
    for (const auto& h : hooks) {
        ok = h.row >= 0 && patch::set_handler(static_cast<uint32_t>(h.row), h.fn) &&
             patch::set_site_armed(static_cast<uint32_t>(h.row), true) && ok;
    }
    return ok;
}

const counters* stats()
{
    return &g_counters;
}

void test_debug(debug_view* out)
{
    out->active = g.active;
    out->pending_samples = g.acquire.ready_samples;
    out->roles = g.roles;
    out->capsules = g.corridor_on;
    out->extra = g.published_extra;
    out->native_boom = g.native_boom;
    out->orbit_hold = g.orbit_hold;
    out->ctl = g.ctl;
}

patch::record_slot<pose_record>& pose_slot()
{
    return g_pose;
}

patch::record_slot<collision_record>& collision_slot()
{
    return g_collision;
}

void test_reset()
{
    g = core{};
    memset(&g_counters, 0, sizeof(g_counters));
    g_reset_request = 0;
    withdraw_all();
}

void* cm07_thunk()
{
    return const_cast<char*>(ilc_camera_cm07_scale);
}

}  // namespace ilc::camera
