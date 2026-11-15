// Ivalice Legacy co-op module: the shared camera's maths (no game memory, no threads; host-testable).
//
// One group camera frames every player on the field: the focus follows the group centre (upper actor biased, so
// stairs keep the higher player in view) with a deadzone and a critically damped spring, leads in the direction of
// motion, and the boom grows as the players spread (out quickly, in slowly). It is N-member generic; Legacy uses it
// for P1..P3 (ILC_ACTIVE_SEATS_MAX).
//
//  * controller: group focus, lead and zoom. Velocity history is keyed by SEAT, not by list position: a member who
//    drops out (KO, parked) and back in starts a fresh history, and the remaining members never inherit another
//    member's previous position.
//  * limited_extra: the collision limiter. The extra boom freezes (not zeroes) while the game reports its camera
//    against a wall, steps back and recovers slowly under sustained collision pressure, and stays out of the way of
//    P1's own orbit (the caller turns it off for a moment after a manual orbit).
//  * latch_guard: recognises a native boom collapse caused by geometry INSIDE the party corridor (its counters come
//    from the collision wrapper). The decision is computed and reported; applying it needs a game-thread write of
//    the rig and is not done (see camera.h).
//  * corridor: capsules between every pair of members far enough apart (a pair-wise "N-capsule" corridor), at
//    camera height, length-clamped, radius capped by the boom; the collision wrapper repeats a collision query whose
//    first hit lies inside any of them, so geometry between the players does not collapse the boom.
//  * focus_escape: when the camera focus is away from every actor's head, hits right next to the ray's origin are
//    treated as internal too.
//
// The constants are the tuned values of the reference behaviour, restated.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"

namespace ilc::camera {

struct vec3 {
    double x, y, z;
};

struct tuning {
    double target_height = 1.30;
    double centre_deadzone = 0.03;
    double vertical_deadzone = 0.10;
    double vertical_upper_bias = 0.85;
    double centre_settle_seconds = 0.30;
    double velocity_filter_seconds = 0.12;
    double lead_seconds = 0.12;
    double max_lead = 0.90;
    double separation_deadzone = 4.0;
    double zoom_per_unit = 1.05;
    double max_extra_distance = 24.0;
    double zoom_out_seconds = 0.10;
    double zoom_in_seconds = 0.42;
    double vertical_separation_cap = 6.0;
    double vertical_separation_weight = 0.30;
    double maximum_sample_speed = 30.0;
    double position_sample_epsilon = 0.0005;
    double velocity_sample_stale_seconds = 0.09;
    double collision_enter_pressure = 0.75;
    double collision_hold_seconds = 0.45;
    double collision_recover_units_per_second = 6.0;
    double native_wall_hold_seconds = 0.08;
    double native_wall_recover_units_per_second = 16.0;
    double native_wall_release_distance = 1.5;
    double collision_active_pressure = 0.15;
    double collision_enter_ratio = 0.16;
    double collision_active_ratio = 0.08;
    double collision_minimum_boom = 1.0;
    double collision_backoff_units = 0.75;
    double vertical_rise_settle_seconds = 0.18;
    double vertical_fall_settle_seconds = 0.08;
    double terrain_transition_vertical_speed = 0.45;
    double terrain_transition_full_vertical_speed = 1.20;
    double terrain_transition_blend_rise_seconds = 0.015;
    double terrain_transition_blend_fall_seconds = 0.24;
    double terrain_transition_lead_scale = 0.10;
    double terrain_transition_deadzone = 0.035;
    double terrain_transition_horizontal_settle_seconds = 0.18;
    double terrain_transition_vertical_settle_seconds = 0.10;
    double internal_collision_corridor_radius = 5.0;
    double internal_collision_vertical_radius = 3.25;
    double internal_collision_corridor_length = 16.0;
    double internal_collision_min_separation = 6.0;
    double internal_collision_radius_boom_ratio = 0.75;
    double internal_collision_min_boom = 8.0;
    double internal_collision_boom_release = 6.0;
    double internal_latch_recent_seconds = 0.16;
    double internal_latch_max_suppression_seconds = 0.45;
    double internal_latch_collapse_ratio = 0.28;
    double internal_latch_recover_ratio = 0.92;
    double focus_collision_escape_min_actor_distance = 1.25;
    double focus_collision_escape_max_hit_distance = 5.30;
    double native_seed_max_distance = 16.0;
};

constexpr uint32_t MAX_MEMBERS = ILC_SEATS;  // arrays; Legacy fills at most ILC_ACTIVE_SEATS_MAX
constexpr uint32_t MAX_CAPSULES = ILC_ACTIVE_SEATS_MAX * (ILC_ACTIVE_SEATS_MAX - 1) / 2;  // every pair

struct member {
    uint32_t seat;       // 0-based seat index: with actor_key, the velocity history's key
    uint32_t actor_key;  // actor_key_of(the seat's actor): a seat that changes body starts a fresh history
    vec3 position;
};

// A 32-bit key of an actor pointer (0 = unknown, then the seat alone keys the history).
constexpr uint32_t actor_key_of(uint64_t actor)
{
    return actor == 0 ? 0u : (static_cast<uint32_t>(actor ^ (actor >> 32)) | 1u);
}

struct frame {
    vec3 focus;
    double extra_distance;
    double effective_separation;
    vec3 lead;
    vec3 focus_velocity;
    uint32_t reset_epoch;
};

struct seat_history {
    uint8_t known;
    uint8_t reserved[3];
    uint32_t actor_key;  // the body this history was measured on
    vec3 previous;
    double age;
    vec3 velocity;
};

struct controller {
    tuning t;
    uint32_t reset_epoch;
    uint8_t has_focus;
    uint8_t moving;
    uint8_t reserved[2];
    vec3 focus;
    vec3 focus_velocity;
    seat_history seat[MAX_MEMBERS];
    vec3 filtered_velocity;
    double extra_distance;
    double terrain_blend;
    // limiter
    uint8_t cap_known;
    uint8_t collision_active;
    uint8_t wall_latched;
    uint8_t anchor_known;
    uint8_t reserved2[4];
    double collision_cap;
    double collision_correction;
    double collision_hold;
    double wall_extra;
    double wall_clear_age;
    vec3 wall_anchor;
    double last_output_extra;
};

void reset(controller& c);  // a new reset epoch (the renderer and the hooks snap instead of flying in)
// The group focus target: centroid X/Z; Y = mean + bias * (highest - mean) + target height.
vec3 focus_target(const tuning& t, const member* m, uint32_t n);
double widest_separation(const member* m, uint32_t n);
// One step (dt clamped to 1..50 ms). n = 0 keeps the last focus.
frame update(controller& c, const member* m, uint32_t n, double dt);
// The extra boom the hooks may add this step (the collision limiter, auto-orbit off).
double limited_extra(controller& c, double actual_boom, double native_boom, double dt, bool new_native_sample,
                     bool native_back_on_wall, bool allow_limiter, bool wall_release_requested);
// Distance from the game's own focus to the party target (area-entry readiness), or -1 if not finite.
double native_focus_distance(const tuning& t, const vec3& native_focus, const member* m, uint32_t n);

struct latch_guard {
    uint8_t have_counters;
    uint8_t suppressing;
    uint8_t have_reference;
    uint8_t reserved;
    uint32_t previous[4];
    double recent_internal;
    double suppression;
    double reference;
};
struct latch_decision {
    uint8_t suppress;
    uint8_t activated;
    uint8_t released;
    uint8_t reserved;
    double restored_boom;  // valid while suppress
};
void reset(latch_guard& g);
latch_decision observe(latch_guard& g, const tuning& t, const uint32_t counters[4], double native_boom,
                       bool back_on_wall, double dt);

struct capsule {
    float a[3];
    float b[3];
    float horizontal_r2;
    float vertical_r2;
};
static_assert(sizeof(capsule) == 32, "capsule layout (the collision record)");
// Capsules for every pair at least min_separation apart; none when the native boom is below the gate (8.0 to turn
// on, kept down to 6.0 once on). native_boom < 0 = unknown (no boom gate). Returns the count.
uint32_t corridor(const tuning& t, const member* m, uint32_t n, double native_boom, bool previously_active,
                  capsule out[MAX_CAPSULES]);
// The squared maximum hit distance of the focus escape, or 0 when it does not apply.
float focus_escape(const tuning& t, const member* m, uint32_t n, const vec3& camera_focus);
// The collision wrapper's classification (single precision, as the game's floats): is this hit inside a capsule?
bool inside_capsule(const capsule& c, const float hit[3]);

}  // namespace ilc::camera
