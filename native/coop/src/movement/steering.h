// Ivalice Legacy co-op module: the steering maths for a guest's character (pure functions, no state of their own).
//
// A guest steers with the logical move actions of their seat (left stick or keys). The leader's locomotion command
// (physics +0xD0) does not follow the stick linearly. Four regions: nothing up to 0.40 deflection; a short linear
// rise; from 0.55 a constant value equal to 1/3 of the running command; from 0.92 the running command itself.
// Guests use the same response curve, so a guest's character can never move faster than the leader's.
//
// Directions are world headings in radians, atan2(x, z): 0 faces +Z. The camera gives a horizontal basis
// (forward, right) and the stick is turned into a world direction in that basis. The body (visual heading) turns
// toward the commanded heading at the leader's measured rate, 13.63 degrees per 60 Hz frame; an execution motion
// (an attack swing aimed at its target) turns faster.
//
// Everything here runs on the co-op thread. Nothing here reads memory or keeps time.
#pragma once

#include <stdint.h>

namespace ilc::movement {

constexpr double PI = 3.14159265358979323846;

// Physics command per frame (physics +0xD0), as the game issues it for the leader.
constexpr double RUN_STEP = 0.10;
constexpr double ARMED_RUN_STEP = 1.0 / 12.0;  // running with weapons drawn (fighting stance)
constexpr double WALK_STEP = 0.04;             // the seat's walk mode (walk/run toggle)

// The stick response plateaus (fractions of the step).
constexpr double STICK_WALK_START = 0.40;      // at or below: no movement
constexpr double STICK_WALK_FULL = 0.55;       // from here to the run threshold: a flat walk
constexpr double STICK_RUN_START = 0.92;       // from here: the full step
constexpr double STICK_WALK_SHARE = 1.0 / 3.0; // the flat walk is one third of the step

// Turning.
constexpr double TURN_DEGREES_PER_FRAME = 13.63;                           // measured on the leader
constexpr double TURN_RATE = TURN_DEGREES_PER_FRAME * 60.0 * PI / 180.0;  // radians per second
constexpr double ACTION_TURN_RATE = 25.0;                                  // execution motions snap harder
constexpr double DT_MAX = 0.050;                                           // seconds: a stall never turns more

// Camera basis filter: a yaw jump larger than this is believed only after it held for CAMERA_CONFIRM samples
// (a cut, or a torn read of the view matrix, must not swing everybody's controls for one frame).
constexpr double CAMERA_YAW_MAX_STEP = 0.35;
constexpr uint32_t CAMERA_CONFIRM = 3;
constexpr double CAMERA_ORTHO_TOLERANCE = 0.02;

// Wraps an angle into (-PI, PI].
double wrap(double radians);

// The share of the step for a stick strength 0..1 (see the plateaus above).
double stick_share(double strength);

// A horizontal camera basis: forward and right as (x, z) unit vectors.
struct basis {
    double fx, fz;
    double rx, rz;
};
// Before any camera sample: forward +Z, right -X (the basis the view matrix gives for a camera at yaw PI).
basis default_basis();

struct steer_result {
    uint8_t moving;      // the stick (or a key) asks for a direction
    uint8_t reserved[7];
    double heading;      // world heading of the request (valid when moving)
    double magnitude;    // physics command (0 when not moving or below the walk threshold)
};

// forward/right: -1..1 (up and right positive). walk: the seat's walk mode. armed: the character has its weapon
// out (running is slower). allowed: the seat may move now; otherwise nothing is requested.
steer_result steer(double forward, double right, const basis& b, bool walk, bool armed, bool allowed);

// Turns `visual` toward `target` by at most rate * dt (dt clamped to 0..DT_MAX), the shorter way round.
double turn_toward(double visual, double target, double rate, double dt);

// The camera basis from the game's 4x4 view matrix (row-major floats), with the jump filter.
struct camera_filter {
    double pending_heading;
    uint32_t pending_count;  // 0 = nothing pending
    uint32_t reserved;
};
// Returns the basis to use this sample: the new one when the matrix is a finite rotation (rows of unit length,
// mutually orthogonal within the tolerance) and its yaw is close to `previous`, or a larger jump that has held
// for CAMERA_CONFIRM samples; `previous` otherwise. *accepted says whether the new basis was taken.
basis basis_from_view(const float view[16], const basis& previous, camera_filter& filter, bool* accepted);

}  // namespace ilc::movement
