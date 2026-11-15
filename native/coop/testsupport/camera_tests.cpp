// Ivalice Legacy co-op module, host tests only: the shared camera exports (ILCT_Cam*).
#include <string.h>

#include "camera/camera.h"
#include "camera/camera_math.h"

using namespace ilc;
using namespace ilc::camera;

extern "C" {

uint32_t ILCT_CamSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(controller), sizeof(member), sizeof(frame), sizeof(latch_guard),
                              sizeof(latch_decision), sizeof(capsule), sizeof(pose_record), sizeof(collision_record),
                              sizeof(debug_view), sizeof(counters)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

// ---- the pure maths on caller memory ----
void ILCT_CamCtlInit(controller* c)
{
    *c = controller{};
    c->t = tuning();
    reset(*c);
}

void ILCT_CamCtlReset(controller* c)
{
    reset(*c);
}

void ILCT_CamUpdate(controller* c, const member* m, uint32_t n, double dt, frame* out)
{
    *out = update(*c, m, n, dt);
}

double ILCT_CamLimited(controller* c, double actual, double native, double dt, int32_t new_sample, int32_t wall,
                       int32_t allow_limiter, int32_t release)
{
    return limited_extra(*c, actual, native, dt, new_sample != 0, wall != 0, allow_limiter != 0, release != 0);
}

void ILCT_CamFocusTarget(const member* m, uint32_t n, vec3* out)
{
    *out = focus_target(tuning(), m, n);
}

double ILCT_CamNativeDistance(const vec3* f, const member* m, uint32_t n)
{
    return native_focus_distance(tuning(), *f, m, n);
}

uint32_t ILCT_CamCorridor(const member* m, uint32_t n, double boom, int32_t previously, capsule* out)
{
    return corridor(tuning(), m, n, boom, previously != 0, out);
}

float ILCT_CamFocusEscape(const member* m, uint32_t n, const vec3* focus)
{
    return focus_escape(tuning(), m, n, *focus);
}

int32_t ILCT_CamInside(const capsule* c, const float* hit)
{
    return inside_capsule(*c, hit) ? 1 : 0;
}

void ILCT_CamLatchReset(latch_guard* g)
{
    reset(*g);
}

void ILCT_CamLatchObserve(latch_guard* g, const uint32_t* counters, double boom, int32_t wall, double dt,
                          latch_decision* out)
{
    *out = observe(*g, tuning(), counters, boom, wall != 0, dt);
}

// ---- the subsystem ----
void ILCT_CamReset(void)
{
    camera::test_reset();
}

void ILCT_CamStart(uint64_t base)
{
    camera::start(static_cast<uintptr_t>(base));
}

void ILCT_CamStep(uint64_t now)
{
    camera::step(now);
}

void ILCT_CamStop(void)
{
    camera::stop();
}

void ILCT_CamRequestReset(void)
{
    camera::request_reset();
}

int32_t ILCT_CamAttach(void)
{
    return camera::attach() ? 1 : 0;
}

void ILCT_CamDebug(debug_view* out)
{
    camera::test_debug(out);
}

void ILCT_CamStats(counters* out)
{
    memcpy(out, (const void*)camera::stats(), sizeof(*out));
}

int32_t ILCT_CamPose(pose_record* out)
{
    return camera::pose_slot().read(*out) ? 1 : 0;
}

int32_t ILCT_CamCollision(collision_record* out)
{
    return camera::collision_slot().read(*out) ? 1 : 0;
}

void ILCT_CamPublishPose(const pose_record* r)
{
    camera::pose_slot().publish(*r);
}

void ILCT_CamPublishCollision(const collision_record* r)
{
    camera::collision_slot().publish(*r);
}

void ILCT_CamWithdraw(void)
{
    camera::pose_slot().withdraw();
    camera::collision_slot().withdraw();
}

// which: 1 CM-01, 2 the collision wrapper, 6 CM-06, 7 CM-07
int32_t ILCT_CamCall(uint32_t which, patch::ctx* c, uint32_t epoch)
{
    switch (which) {
    case 1: return camera::cm01(c, epoch);
    case 2: return camera::collision(c, epoch);
    case 6: return camera::cm06(c, epoch);
    case 7: return camera::cm07(c, epoch);
    default: return -1;
    }
}

uint64_t ILCT_CamThunk(void)
{
    return reinterpret_cast<uint64_t>(camera::cm07_thunk());
}

}  // extern "C"
