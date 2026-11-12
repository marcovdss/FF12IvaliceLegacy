// Ivalice Legacy co-op module: the steering maths (see steering.h).
#include "movement/steering.h"

#include <math.h>

namespace ilc::movement {

double wrap(double radians)
{
    return atan2(sin(radians), cos(radians));
}

double stick_share(double strength)
{
    if (!(strength > STICK_WALK_START)) {  // NaN reads as "no input"
        return 0.0;
    }
    if (strength >= STICK_RUN_START) {
        return 1.0;
    }
    if (strength >= STICK_WALK_FULL) {
        return STICK_WALK_SHARE;
    }
    return STICK_WALK_SHARE * (strength - STICK_WALK_START) / (STICK_WALK_FULL - STICK_WALK_START);
}

basis default_basis()
{
    return basis{0.0, 1.0, -1.0, 0.0};
}

steer_result steer(double forward, double right, const basis& b, bool walk, bool armed, bool allowed)
{
    steer_result out = {};
    if (!allowed || !isfinite(forward) || !isfinite(right) || (forward == 0.0 && right == 0.0)) {
        return out;
    }
    const double length = sqrt(forward * forward + right * right);
    const double strength = length < 1.0 ? length : 1.0;
    const double x = forward * b.fx + right * b.rx;
    const double z = forward * b.fz + right * b.rz;
    out.moving = 1;
    out.heading = atan2(x, z);
    const double step = walk ? WALK_STEP : (armed ? ARMED_RUN_STEP : RUN_STEP);
    out.magnitude = step * stick_share(strength);
    return out;
}

double turn_toward(double visual, double target, double rate, double dt)
{
    if (!(dt > 0.0)) {
        dt = 0.0;
    } else if (dt > DT_MAX) {
        dt = DT_MAX;
    }
    const double limit = rate * dt;
    double delta = wrap(target - visual);
    if (delta > limit) {
        delta = limit;
    } else if (delta < -limit) {
        delta = -limit;
    }
    return wrap(visual + delta);
}

basis basis_from_view(const float view[16], const basis& previous, camera_filter& filter, bool* accepted)
{
    *accepted = false;
    for (uint32_t k = 0; k < 16; ++k) {
        if (!isfinite(view[k])) {
            return previous;
        }
    }
    // The three rotation rows: unit length and mutually orthogonal, or this is not a camera we can read.
    const float* row[3] = {view, view + 4, view + 8};
    for (uint32_t i = 0; i < 3; ++i) {
        const double n = static_cast<double>(row[i][0]) * row[i][0] + static_cast<double>(row[i][1]) * row[i][1] +
                         static_cast<double>(row[i][2]) * row[i][2];
        if (fabs(n - 1.0) > CAMERA_ORTHO_TOLERANCE) {
            return previous;
        }
        for (uint32_t j = i + 1; j < 3; ++j) {
            const double d = static_cast<double>(row[i][0]) * row[j][0] +
                             static_cast<double>(row[i][1]) * row[j][1] +
                             static_cast<double>(row[i][2]) * row[j][2];
            if (fabs(d) > CAMERA_ORTHO_TOLERANCE) {
                return previous;
            }
        }
    }
    const double heading = PI - atan2(static_cast<double>(view[8]), static_cast<double>(view[10]));
    basis next;
    next.fx = sin(heading);
    next.fz = cos(heading);
    next.rx = -next.fz;
    next.rz = next.fx;

    const double previous_heading = atan2(previous.fx, previous.fz);
    if (fabs(wrap(heading - previous_heading)) <= CAMERA_YAW_MAX_STEP) {
        filter.pending_count = 0;
        *accepted = true;
        return next;
    }
    // A jump: believe it once it has held (within the step) for CAMERA_CONFIRM samples in a row.
    if (filter.pending_count != 0 && fabs(wrap(heading - filter.pending_heading)) <= CAMERA_YAW_MAX_STEP) {
        ++filter.pending_count;
    } else {
        filter.pending_heading = heading;
        filter.pending_count = 1;
    }
    if (filter.pending_count >= CAMERA_CONFIRM) {
        filter.pending_count = 0;
        *accepted = true;
        return next;
    }
    return previous;
}

}  // namespace ilc::movement
