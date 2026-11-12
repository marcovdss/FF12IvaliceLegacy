// Ivalice Legacy co-op module: the movement planner (see planner.h).
#include "movement/planner.h"

#include <math.h>
#include <string.h>

namespace ilc::movement {
namespace {

constexpr double STRENGTH_FULL = 65535.0;

double axis(uint16_t positive, uint16_t negative)
{
    return (static_cast<double>(positive) - static_cast<double>(negative)) / STRENGTH_FULL;
}

bool finite_f(float v)
{
    return isfinite(v);
}

}  // namespace

void planner_reset(planner_state& st)
{
    memset(&st, 0, sizeof(st));
    st.camera = default_basis();
}

void plan(planner_state& st, const plan_inputs& in, plan_outputs& out)
{
    memset(&out, 0, sizeof(out));
    const session::session_record& rec = in.session;
    const uint32_t gates = rec.head.flags;
    out.record.head.epoch = rec.head.epoch;
    out.record.head.generation = rec.head.generation;
    const bool logical = (gates & session::GATE_LOGICAL) != 0;
    const bool guests_move = (gates & session::GATE_GUESTS_MOVE) != 0;
    const bool may_move = (gates & session::GATE_MOVEMENT) != 0 && guests_move;
    const bool flee_gates = (gates & session::GATE_WORLD) != 0 && (gates & session::GATE_MOVEMENT) != 0;
    out.record.head.flags = (guests_move ? STEERF_MOVE : 0u) | (logical ? STEERF_LOGICAL : 0u);

    double dt = 0.0;
    if (st.last_ms != 0 && in.now_ms > st.last_ms) {
        dt = static_cast<double>(in.now_ms - st.last_ms) / 1000.0;
    }
    st.last_ms = in.now_ms;

    // The camera basis is refreshed only while somebody may move (a menu camera must not turn the controls).
    if (may_move && in.camera_ok) {
        bool accepted = false;
        st.camera = basis_from_view(in.view, st.camera, st.filter, &accepted);
        out.camera_accepted = accepted ? 1u : 0u;
    }

    for (uint32_t s = 1; s < ACTIVE_SEATS; ++s) {
        const uint32_t k = s - 1;
        slot_state& ss = st.slot[k];
        const session::seat_entry& e = rec.seat[s];
        const seat_input& si = in.input[s];
        const actor_facts& af = in.actor[s];

        const bool manual = (e.flags & session::SEATF_MANUAL) != 0 && (e.flags & session::SEATF_IN_SESSION) != 0;
        // Walk/run flips on a fresh press while the seat plays, whether or not it can move this tick.
        if (logical && manual && si.present && si.walk_toggle) {
            ss.walk = ss.walk ? 0 : 1;
        }
        // Flee is RT on P1's pad: only in a valid world with the movement gate open, never into a menu.
        if (logical && flee_gates && manual && si.present && si.flee) {
            out.flee_seats |= 1u << s;
        }

        const bool steers = (e.flags & session::SEATF_STEERS) != 0 && (e.flags & session::SEATF_ACTOR) != 0 &&
                            e.actor != 0 && e.phys != 0 && rec.head.epoch != 0;
        if (!steers) {
            ss.actor = 0;
            ss.phys = 0;
            continue;
        }
        if (!af.ok || af.root == 0) {
            out.waiting |= 1u << k;  // the detours keep stock behaviour for it until the facts are back
            continue;
        }
        const bool same = ss.actor == e.actor && ss.phys == e.phys && ss.actor_generation == e.actor_generation &&
                          ss.world_epoch == rec.world_epoch;
        if (!same) {
            if (!af.headings_ok || !finite_f(af.phys_heading) || !finite_f(af.phys_visual)) {
                out.waiting |= 1u << k;
                ss.actor = 0;
                ss.phys = 0;
                continue;
            }
            ss.actor = e.actor;
            ss.phys = e.phys;
            ss.actor_generation = e.actor_generation;
            ss.world_epoch = rec.world_epoch;
            ss.last_heading = wrap(af.phys_heading);
            ss.visual = wrap(af.phys_visual);
            out.started |= 1u << k;
        }

        const bool allowed = may_move && si.present && !si.hold;
        const steer_result r = steer(axis(si.up, si.down), axis(si.right, si.left), st.camera, ss.walk != 0,
                                     af.armed != 0, allowed);
        double heading = ss.last_heading;
        double magnitude = 0.0;
        if (r.moving) {
            heading = r.heading;
            ss.last_heading = heading;
            magnitude = r.magnitude;
        }
        if (af.execution) {
            magnitude = 0.0;
            if (af.has_target && finite_f(af.target_heading)) {
                heading = wrap(af.target_heading);
                ss.last_heading = heading;
            }
        }
        ss.visual = turn_toward(ss.visual, heading, af.execution ? ACTION_TURN_RATE : TURN_RATE, dt);

        steer_entry& out_e = out.record.slot[k];
        out_e.actor = e.actor;
        out_e.phys = e.phys;
        out_e.root = af.root;
        out_e.heading = static_cast<float>(heading);
        out_e.visual = static_cast<float>(ss.visual);
        out_e.magnitude = static_cast<float>(magnitude);
        out_e.member = e.member;
        out_e.seat = static_cast<uint8_t>(s);
        out_e.flags = static_cast<uint8_t>(ENTRY_ACTIVE | (magnitude != 0.0 ? ENTRY_MOVING : 0));
        out_e.handle = e.handle;
        out_e.actor_generation = e.actor_generation;
        out.record.active |= 1u << k;
    }
}

int32_t slot_of_root(const steer_record& r, uint64_t root)
{
    for (uint32_t k = 0; k < SLOTS; ++k) {
        if ((r.active & (1u << k)) != 0 && (r.slot[k].flags & ENTRY_ACTIVE) != 0 && root != 0 &&
            r.slot[k].root == root) {
            return static_cast<int32_t>(k);
        }
    }
    return -1;
}

int32_t slot_of_phys(const steer_record& r, uint64_t phys)
{
    for (uint32_t k = 0; k < SLOTS; ++k) {
        if ((r.active & (1u << k)) != 0 && (r.slot[k].flags & ENTRY_ACTIVE) != 0 && phys != 0 &&
            r.slot[k].phys == phys) {
            return static_cast<int32_t>(k);
        }
    }
    return -1;
}

int32_t slot_of_actor(const steer_record& r, uint64_t actor)
{
    for (uint32_t k = 0; k < SLOTS; ++k) {
        if ((r.active & (1u << k)) != 0 && (r.slot[k].flags & ENTRY_ACTIVE) != 0 && actor != 0 &&
            r.slot[k].actor == actor) {
            return static_cast<int32_t>(k);
        }
    }
    return -1;
}

}  // namespace ilc::movement
