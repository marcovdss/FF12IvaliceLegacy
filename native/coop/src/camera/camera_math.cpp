// Ivalice Legacy co-op module: the shared camera's maths (see camera_math.h).
#include "camera/camera_math.h"

#include <math.h>
#include <string.h>

namespace ilc::camera {
namespace {

double hlen(double x, double z)
{
    return sqrt(x * x + z * z);
}

double hlen(const vec3& v)
{
    return hlen(v.x, v.z);
}

double len3(const vec3& v)
{
    return sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
}

vec3 clamp_horizontal(const vec3& v, double maximum)
{
    const double l = hlen(v);
    if (l <= maximum || l <= 1e-8) {
        return v;
    }
    const double s = maximum / l;
    return {v.x * s, v.y, v.z * s};
}

double clampd(double v, double lo, double hi)
{
    return v < lo ? lo : (v > hi ? hi : v);
}

double alpha(double dt, double seconds)
{
    return 1.0 - exp(-dt / (seconds > 0.01 ? seconds : 0.01));
}

// A critically damped spring on one axis.
void spring(double& position, double& velocity, double target, double dt, double settle)
{
    const double omega = 4.0 / (settle > 0.01 ? settle : 0.01);
    const double displacement = position - target;
    const double temporary = (velocity + omega * displacement) * dt;
    const double decay = exp(-omega * dt);
    position = target + (displacement + temporary) * decay;
    velocity = (velocity - omega * temporary) * decay;
}

double axis(const vec3& v, int a)
{
    return a == 0 ? v.x : (a == 1 ? v.y : v.z);
}

void set_axis(vec3& v, int a, double value)
{
    (a == 0 ? v.x : (a == 1 ? v.y : v.z)) = value;
}

// The velocity history, keyed by (seat, actor): one sample per member. A seat that leaves the framed set (parked,
// dropped, disconnected, down) forgets its history, and a seat whose body changed (a character swap, a reconcile
// after a pad change) starts a fresh one: a position jump between two bodies never becomes a velocity.
vec3 group_velocity(controller& c, const member* m, uint32_t n, double dt)
{
    bool present[MAX_MEMBERS] = {};
    for (uint32_t i = 0; i < n; ++i) {
        if (m[i].seat < MAX_MEMBERS) {
            present[m[i].seat] = true;
        }
    }
    for (uint32_t s = 0; s < MAX_MEMBERS; ++s) {
        if (!present[s]) {
            memset(&c.seat[s], 0, sizeof(c.seat[s]));  // a returning seat starts a fresh history
        }
    }
    for (uint32_t i = 0; i < n; ++i) {
        if (m[i].seat < MAX_MEMBERS && c.seat[m[i].seat].known && c.seat[m[i].seat].actor_key != m[i].actor_key) {
            memset(&c.seat[m[i].seat], 0, sizeof(c.seat[m[i].seat]));  // the seat now drives another body
        }
    }
    bool seeded = false;
    for (uint32_t i = 0; i < n; ++i) {
        if (m[i].seat >= MAX_MEMBERS) {
            continue;
        }
        seat_history& h = c.seat[m[i].seat];
        if (!h.known) {
            h.known = 1;
            h.actor_key = m[i].actor_key;
            h.previous = m[i].position;
            h.age = 0.0;
            h.velocity = {0, 0, 0};
            seeded = true;
            continue;
        }
        h.age += dt;
        const vec3 d = {m[i].position.x - h.previous.x, m[i].position.y - h.previous.y,
                        m[i].position.z - h.previous.z};
        if (len3(d) > c.t.position_sample_epsilon) {
            const double interval = h.age > 1.0e-6 ? h.age : 1.0e-6;
            h.velocity = clamp_horizontal({d.x / interval, d.y / interval, d.z / interval}, c.t.maximum_sample_speed);
            h.previous = m[i].position;
            h.age = 0.0;
        } else if (h.age >= c.t.velocity_sample_stale_seconds) {
            h.velocity = {0, 0, 0};
        }
    }
    (void)seeded;
    double sx = 0.0, sz = 0.0;
    uint32_t counted = 0;
    bool any_moving = false;
    for (uint32_t i = 0; i < n; ++i) {
        if (m[i].seat >= MAX_MEMBERS) {
            continue;
        }
        const vec3& v = c.seat[m[i].seat].velocity;
        any_moving = any_moving || hlen(v) >= 0.05;
        sx += v.x;
        sz += v.z;
        ++counted;
    }
    if (!any_moving || counted == 0) {
        return {0, 0, 0};
    }
    const vec3 average = {sx / counted, 0.0, sz / counted};
    if (hlen(average) < 1e-6) {
        return {0, 0, 0};
    }
    return average;
}

}  // namespace

void reset(controller& c)
{
    const tuning t = c.t;
    uint32_t epoch = c.reset_epoch + 1u;
    if (epoch == 0) {
        epoch = 1;
    }
    c = controller{};
    c.t = t;
    c.reset_epoch = epoch;
}

vec3 focus_target(const tuning& t, const member* m, uint32_t n)
{
    if (n == 0) {
        return {0.0, t.target_height, 0.0};
    }
    double sx = 0, sy = 0, sz = 0, top = m[0].position.y;
    for (uint32_t i = 0; i < n; ++i) {
        sx += m[i].position.x;
        sy += m[i].position.y;
        sz += m[i].position.z;
        top = m[i].position.y > top ? m[i].position.y : top;
    }
    const double mean = sy / n;
    return {sx / n, mean + (top - mean) * t.vertical_upper_bias + t.target_height, sz / n};
}

double widest_separation(const member* m, uint32_t n)
{
    double widest = 0.0;
    for (uint32_t i = 0; i < n; ++i) {
        for (uint32_t j = i + 1; j < n; ++j) {
            const double d = hlen(m[j].position.x - m[i].position.x, m[j].position.z - m[i].position.z);
            widest = d > widest ? d : widest;
        }
    }
    return widest;
}

frame update(controller& c, const member* m, uint32_t n, double dt_in)
{
    const tuning& t = c.t;
    const double dt = clampd(dt_in, 1.0 / 1000.0, 0.05);
    if (n == 0) {
        return {c.focus, c.extra_distance, 0.0, {0, 0, 0}, c.focus_velocity, c.reset_epoch};
    }
    const double separation = widest_separation(m, n);
    const vec3 centre = focus_target(t, m, n);
    const vec3 velocity = group_velocity(c, m, n, dt);
    bool moving = false;
    double vertical_motion = 0.0;
    for (uint32_t i = 0; i < n; ++i) {
        if (m[i].seat < MAX_MEMBERS) {
            const vec3& v = c.seat[m[i].seat].velocity;
            moving = moving || len3(v) > 0.10;
            vertical_motion = fabs(v.y) > vertical_motion ? fabs(v.y) : vertical_motion;
        }
    }
    c.moving = moving ? 1 : 0;
    const double va = alpha(dt, t.velocity_filter_seconds);
    c.filtered_velocity.x += (velocity.x - c.filtered_velocity.x) * va;
    c.filtered_velocity.y += (velocity.y - c.filtered_velocity.y) * va;
    c.filtered_velocity.z += (velocity.z - c.filtered_velocity.z) * va;
    vec3 lead = clamp_horizontal({c.filtered_velocity.x * t.lead_seconds, 0.0, c.filtered_velocity.z * t.lead_seconds},
                                 t.max_lead);

    const double range = t.terrain_transition_full_vertical_speed - t.terrain_transition_vertical_speed;
    const double terrain_target =
        clampd((vertical_motion - t.terrain_transition_vertical_speed) / (range > 1e-4 ? range : 1e-4), 0.0, 1.0);
    const double blend_seconds = terrain_target > c.terrain_blend ? t.terrain_transition_blend_rise_seconds
                                                                   : t.terrain_transition_blend_fall_seconds;
    c.terrain_blend += (terrain_target - c.terrain_blend) * alpha(dt, blend_seconds);
    if (c.terrain_blend < 1.0e-4 && terrain_target == 0.0) {
        c.terrain_blend = 0.0;
    }
    const double blend = c.terrain_blend;
    if (blend > 0.0) {
        const double scale = 1.0 + (t.terrain_transition_lead_scale - 1.0) * blend;
        lead = {lead.x * scale, 0.0, lead.z * scale};
    }

    if (!c.has_focus) {
        c.has_focus = 1;
        c.focus = centre;
        c.focus_velocity = {0, 0, 0};
    } else {
        const double dx = centre.x - c.focus.x;
        const double dz = centre.z - c.focus.z;
        const double distance = hlen(dx, dz);
        double deadzone = t.centre_deadzone - (separation - 2.0 > 0.0 ? separation - 2.0 : 0.0) * 0.025;
        deadzone = deadzone > 0.01 ? deadzone : 0.01;
        if (hlen(c.filtered_velocity) > 0.20 && separation > 3.0) {
            deadzone = deadzone < 0.08 ? deadzone : 0.08;
        }
        if (blend > 0.0) {
            const double cap = deadzone < t.terrain_transition_deadzone ? deadzone : t.terrain_transition_deadzone;
            deadzone += (cap - deadzone) * blend;
        }
        vec3 target;
        if (distance <= deadzone) {
            target.x = c.focus.x;
            target.z = c.focus.z;
        } else {
            const double amount = (distance - deadzone) / distance;
            target.x = c.focus.x + dx * amount + lead.x;
            target.z = c.focus.z + dz * amount + lead.z;
        }
        const double dy = centre.y - c.focus.y;
        target.y = fabs(dy) <= t.vertical_deadzone ? c.focus.y : centre.y - copysign(t.vertical_deadzone, dy);
        for (int a = 0; a < 3; ++a) {
            double settle = t.centre_settle_seconds;
            if (blend > 0.0 && a != 1) {
                settle += (t.terrain_transition_horizontal_settle_seconds - settle) * blend;
            }
            if (a == 1) {
                settle = axis(target, 1) < c.focus.y ? t.vertical_fall_settle_seconds : t.vertical_rise_settle_seconds;
                settle += (t.terrain_transition_vertical_settle_seconds - settle) * blend;
            }
            double p = axis(c.focus, a), v = axis(c.focus_velocity, a);
            spring(p, v, axis(target, a), dt, settle);
            set_axis(c.focus, a, p);
            set_axis(c.focus_velocity, a, v);
        }
    }

    double lo = m[0].position.y, hi = m[0].position.y;
    for (uint32_t i = 1; i < n; ++i) {
        lo = m[i].position.y < lo ? m[i].position.y : lo;
        hi = m[i].position.y > hi ? m[i].position.y : hi;
    }
    const double spread = hi - lo;
    const double vertical = (spread < t.vertical_separation_cap ? spread : t.vertical_separation_cap) *
                            t.vertical_separation_weight;
    const double effective = sqrt(separation * separation + vertical * vertical);
    double desired = t.zoom_per_unit * (effective - t.separation_deadzone > 0.0 ? effective - t.separation_deadzone : 0.0);
    desired = desired < t.max_extra_distance ? desired : t.max_extra_distance;
    const double zoom_seconds = desired > c.extra_distance ? t.zoom_out_seconds : t.zoom_in_seconds;
    c.extra_distance += (desired - c.extra_distance) * alpha(dt, zoom_seconds);
    return {c.focus, c.extra_distance, effective, lead, c.focus_velocity, c.reset_epoch};
}

namespace {

double collision_safe(controller& c, double actual_boom, double native_boom, double dt, bool new_sample, bool wall,
                      bool release)
{
    const tuning& t = c.t;
    const double desired = c.extra_distance > 0.0 ? c.extra_distance : 0.0;
    const double actual = actual_boom > 0.0 ? actual_boom : 0.0;
    const double native = native_boom > 0.0 ? native_boom : 0.0;
    (void)actual;
    const double minimum = t.collision_minimum_boom;
    if (desired <= 1e-4) {
        c.cap_known = 1;
        c.collision_cap = native;
        c.collision_correction = 0.0;
        c.collision_hold = 0.0;
        c.collision_active = 0;
        c.wall_latched = 0;
        c.wall_extra = 0.0;
        c.wall_clear_age = 0.0;
        c.anchor_known = 0;
        c.last_output_extra = 0.0;
        return 0.0;
    }
    if (wall) {
        // The game reports its camera against a wall: freeze the extension where it was (never zero it).
        if (!c.wall_latched) {
            const double previous = c.last_output_extra > 0.0 ? c.last_output_extra : 0.0;
            const double base = previous > 1e-4 ? previous : desired;
            c.wall_extra = desired < base ? desired : base;
        } else {
            c.wall_extra = desired < c.wall_extra ? desired : c.wall_extra;
        }
        c.cap_known = 0;
        c.collision_correction = c.wall_extra;
        c.collision_hold = 0.0;
        c.wall_clear_age = 0.0;
        c.anchor_known = c.has_focus;
        c.wall_anchor = c.focus;
        c.collision_active = 1;
        c.wall_latched = 1;
        c.last_output_extra = c.wall_extra;
        return c.wall_extra;
    }
    if (c.wall_latched) {
        double anchor_distance = 0.0;
        if (c.anchor_known && c.has_focus) {
            anchor_distance = hlen(c.focus.x - c.wall_anchor.x, c.focus.z - c.wall_anchor.z);
        }
        const bool can_release = release || (c.moving && anchor_distance >= t.native_wall_release_distance);
        double frozen = desired < c.wall_extra ? desired : c.wall_extra;
        if (!can_release) {
            c.wall_clear_age = 0.0;
            c.collision_correction = frozen;
            c.last_output_extra = frozen;
            return frozen;
        }
        c.wall_clear_age += dt;
        if (c.wall_clear_age > t.native_wall_hold_seconds) {
            const double grown = frozen + t.native_wall_recover_units_per_second * dt;
            frozen = desired < grown ? desired : grown;
            c.wall_extra = frozen;
        }
        c.collision_correction = frozen;
        c.last_output_extra = frozen;
        if (frozen >= desired - 0.05) {
            c.wall_latched = 0;
            c.wall_extra = 0.0;
            c.wall_clear_age = 0.0;
            c.anchor_known = 0;
            c.collision_active = 0;
            c.cap_known = 0;
        }
        return frozen;
    }
    const double desired_total = native + desired > minimum ? native + desired : minimum;
    const double previous_total =
        native + c.collision_correction > minimum ? native + c.collision_correction : minimum;
    if (!c.cap_known) {
        c.cap_known = 1;
        c.collision_cap = desired_total;
    }
    c.collision_cap = c.collision_cap < desired_total ? c.collision_cap : desired_total;
    const double pressure = previous_total - actual_boom;
    const double absolute = c.collision_active ? t.collision_active_pressure : t.collision_enter_pressure;
    const double ratio_threshold = c.collision_active ? t.collision_active_ratio : t.collision_enter_ratio;
    const double ratio = (pressure > 0.0 ? pressure : 0.0) / (previous_total > 1.0 ? previous_total : 1.0);
    if (new_sample && pressure > absolute && ratio > ratio_threshold) {
        if (!c.collision_active) {
            const double backed = previous_total - t.collision_backoff_units;
            const double safe_total = backed > minimum ? backed : minimum;
            c.collision_cap = c.collision_cap < safe_total ? c.collision_cap : safe_total;
        }
        c.collision_active = 1;
        c.collision_hold = t.collision_hold_seconds;
    } else if (c.collision_active) {
        c.collision_hold = c.collision_hold - dt > 0.0 ? c.collision_hold - dt : 0.0;
        if (c.collision_hold <= 0.0) {
            const double rate =
                c.wall_latched ? t.native_wall_recover_units_per_second : t.collision_recover_units_per_second;
            const double grown = c.collision_cap + rate * dt;
            c.collision_cap = desired_total < grown ? desired_total : grown;
        }
        if (c.collision_cap >= desired_total - 0.05 && ratio <= t.collision_active_ratio) {
            c.collision_active = 0;
            c.wall_latched = 0;
        }
    } else {
        c.collision_cap = desired_total;
        c.wall_latched = 0;
    }
    double correction = c.collision_cap - native;
    correction = correction < desired ? correction : desired;
    correction = correction > 0.0 ? correction : 0.0;
    c.collision_correction = correction;
    c.last_output_extra = correction;
    return correction;
}

}  // namespace

double limited_extra(controller& c, double actual_boom, double native_boom, double dt_in, bool new_native_sample,
                     bool native_back_on_wall, bool allow_limiter, bool wall_release_requested)
{
    const double dt = clampd(dt_in, 0.0, 0.05);
    if (native_back_on_wall || c.wall_latched || allow_limiter) {
        return collision_safe(c, actual_boom, native_boom, dt, new_native_sample, native_back_on_wall,
                              wall_release_requested);
    }
    c.cap_known = 0;
    c.collision_correction = 0.0;
    c.collision_hold = 0.0;
    c.collision_active = 0;
    c.wall_latched = 0;
    const double extra = c.extra_distance > 0.0 ? c.extra_distance : 0.0;
    c.last_output_extra = extra;
    return extra;
}

double native_focus_distance(const tuning& t, const vec3& f, const member* m, uint32_t n)
{
    if (n == 0) {
        return -1.0;
    }
    const vec3 target = focus_target(t, m, n);
    const double dx = f.x - target.x, dy = f.y - target.y, dz = f.z - target.z;
    const double d = sqrt(dx * dx + dy * dy + dz * dz);
    return d == d && d < 1e30 ? d : -1.0;
}

void reset(latch_guard& g)
{
    memset(&g, 0, sizeof(g));
}

latch_decision observe(latch_guard& g, const tuning& t, const uint32_t counters[4], double native, bool wall,
                       double dt_in)
{
    latch_decision d = {};
    const double dt = clampd(dt_in, 0.0, 0.05);
    if (!(native == native) || native > 1e30 || native < -1e30) {
        reset(g);
        return d;
    }
    uint32_t internal_delta = 0, external_delta = 0;
    if (g.have_counters) {
        internal_delta = counters[0] - g.previous[0];
        external_delta = counters[3] - g.previous[3];
    }
    memcpy(g.previous, counters, sizeof(g.previous));
    g.have_counters = 1;
    if (external_delta) {
        g.recent_internal = 0.0;
        d.released = g.suppressing;
        g.suppressing = 0;
        g.suppression = 0.0;
    } else if (internal_delta) {
        g.recent_internal = t.internal_latch_recent_seconds;
    } else {
        g.recent_internal = g.recent_internal - dt > 0.0 ? g.recent_internal - dt : 0.0;
    }
    if (!g.have_reference) {
        if (!wall) {
            g.have_reference = 1;
            g.reference = native;
        }
        return d;
    }
    const double reference = g.reference;
    const bool collapsed = reference > 3.0 && native < reference * (1.0 - t.internal_latch_collapse_ratio);
    const bool activated = !g.suppressing && !external_delta && g.recent_internal > 0.0 && wall && collapsed;
    d.activated = activated ? 1 : 0;
    if (activated) {
        g.suppressing = 1;
        g.suppression = t.internal_latch_max_suppression_seconds;
    }
    if (g.suppressing) {
        if (internal_delta) {
            g.suppression = t.internal_latch_max_suppression_seconds;
        } else {
            g.suppression = g.suppression - dt > 0.0 ? g.suppression - dt : 0.0;
        }
        const bool recovered = !wall && native >= reference * t.internal_latch_recover_ratio;
        if (recovered || g.suppression <= 0.0) {
            g.suppressing = 0;
            g.suppression = 0.0;
            g.reference = native;
            d.released = 1;
            return d;
        }
        d.suppress = 1;
        d.restored_boom = reference;
        return d;
    }
    if (!wall) {
        g.reference = native;
    }
    return d;
}

uint32_t corridor(const tuning& t, const member* m, uint32_t n, double native_boom, bool previously_active,
                  capsule out[MAX_CAPSULES])
{
    if (n < 2) {
        return 0;
    }
    for (uint32_t i = 0; i < n; ++i) {
        const vec3& p = m[i].position;
        if (!(p.x == p.x && p.y == p.y && p.z == p.z) || fabs(p.x) > 1e30 || fabs(p.y) > 1e30 || fabs(p.z) > 1e30) {
            return 0;
        }
    }
    const bool boom_known = native_boom >= 0.0 && native_boom == native_boom && native_boom < 1e30;
    if (boom_known) {
        const double limit = previously_active ? t.internal_collision_boom_release : t.internal_collision_min_boom;
        if (native_boom < limit) {
            return 0;
        }
    }
    double radius = t.internal_collision_corridor_radius > 0.1 ? t.internal_collision_corridor_radius : 0.1;
    if (boom_known && native_boom > 0.0) {
        double by_boom = native_boom * t.internal_collision_radius_boom_ratio;
        by_boom = by_boom > 0.5 ? by_boom : 0.5;
        radius = radius < by_boom ? radius : by_boom;
    }
    const double vertical = t.internal_collision_vertical_radius > 0.1 ? t.internal_collision_vertical_radius : 0.1;
    uint32_t count = 0;
    for (uint32_t i = 0; i < n && count < MAX_CAPSULES; ++i) {
        for (uint32_t j = i + 1; j < n && count < MAX_CAPSULES; ++j) {
            vec3 a = m[i].position, b = m[j].position;
            const double dx = b.x - a.x, dz = b.z - a.z;
            const double span = hlen(dx, dz);
            if (span < t.internal_collision_min_separation) {
                continue;
            }
            const double limit = t.internal_collision_corridor_length;
            if (limit > 0.0 && span > limit) {
                const double keep = (limit / span) / 2.0;
                const double mx = (a.x + b.x) / 2.0, mz = (a.z + b.z) / 2.0;
                a = {mx - dx * keep, a.y, mz - dz * keep};
                b = {mx + dx * keep, b.y, mz + dz * keep};
            }
            capsule& c = out[count++];
            c.a[0] = static_cast<float>(a.x);
            c.a[1] = static_cast<float>(a.y + t.target_height);
            c.a[2] = static_cast<float>(a.z);
            c.b[0] = static_cast<float>(b.x);
            c.b[1] = static_cast<float>(b.y + t.target_height);
            c.b[2] = static_cast<float>(b.z);
            c.horizontal_r2 = static_cast<float>(radius * radius);
            c.vertical_r2 = static_cast<float>(vertical * vertical);
        }
    }
    return count;
}

float focus_escape(const tuning& t, const member* m, uint32_t n, const vec3& f)
{
    if (n < 2 || !(f.x == f.x && f.y == f.y && f.z == f.z)) {
        return 0.0f;
    }
    double nearest = 1e300;
    for (uint32_t i = 0; i < n; ++i) {
        const vec3& p = m[i].position;
        const double dx = f.x - p.x, dy = f.y - (p.y + t.target_height), dz = f.z - p.z;
        const double d = sqrt(dx * dx + dy * dy + dz * dz);
        if (!(d == d)) {
            return 0.0f;
        }
        nearest = d < nearest ? d : nearest;
    }
    if (nearest < t.focus_collision_escape_min_actor_distance || t.focus_collision_escape_max_hit_distance <= 0.0) {
        return 0.0f;
    }
    return static_cast<float>(t.focus_collision_escape_max_hit_distance * t.focus_collision_escape_max_hit_distance);
}

bool inside_capsule(const capsule& c, const float hit[3])
{
    // The hit projected onto the capsule's axis (horizontal plane), then the vertical band at that point.
    float rx = hit[0] - c.a[0];
    float rz = hit[2] - c.a[2];
    const float sx = c.b[0] - c.a[0];
    const float sz = c.b[2] - c.a[2];
    const float l2 = sx * sx + sz * sz;
    float u = 0.0f;
    if (!(l2 == l2)) {
        return false;  // NaN: external
    }
    if (l2 > 1.0e-6f) {
        u = (rx * sx + rz * sz) / l2;
        u = u > 0.0f ? u : 0.0f;
        u = u < 1.0f ? u : 1.0f;
        rx -= sx * u;
        rz -= sz * u;
    }
    const float y = c.a[1] + (c.b[1] - c.a[1]) * u;
    const float dy = hit[1] - y;
    if (!(dy * dy <= c.vertical_r2)) {
        return false;
    }
    return rx * rx + rz * rz <= c.horizontal_r2;
}

}  // namespace ilc::camera
