// Ivalice Legacy co-op module: town visibility rules (see filter.h).
#include "towns/filter.h"

#include <string.h>

#include "session/session.h"
#include "towns/towns_facts.h"

namespace ilc::towns {

namespace {

constexpr uint64_t bit(uint32_t member)
{
    return member < 64 ? (1ull << member) : 0;
}

bool in_slots(const uint16_t slots[SLOTS], uint16_t member)
{
    for (uint32_t k = 0; k < SLOTS; ++k) {
        if (slots[k] == member) {
            return true;
        }
    }
    return false;
}

}  // namespace

uint64_t controlled_members(const session::session_record& r)
{
    uint64_t out = 0;
    // P1 is always in session: their character keeps its body even while a swap with a guest settles.
    const session::seat_entry& p1 = r.seat[0];
    if ((p1.flags & session::SEATF_IN_SESSION) != 0) {
        out |= bit(p1.member);
    }
    for (uint32_t s = 1; s < session::ACTIVE_SEATS; ++s) {
        const session::seat_entry& e = r.seat[s];
        const uint16_t need = session::SEATF_IN_SESSION | session::SEATF_MANUAL;
        if ((e.flags & need) == need) {
            out |= bit(e.member);
        }
    }
    return out;
}

bool keeps(uint64_t allowed, uint8_t leader, uint16_t member)
{
    if (member == facts::EMPTY) {
        return true;
    }
    return (leader != facts::NO_LEADER && member == leader) || (bit(member) & allowed) != 0;
}

bool town_mode(bool table_ok, uint32_t location_raw, uint32_t area_count, uint8_t town_byte, uint32_t mode_bits)
{
    if ((mode_bits & facts::MODE_SPECIAL) != 0 || !table_ok) {
        return false;
    }
    const uint32_t id = static_cast<int32_t>(location_raw) < 0 ? 0u : location_raw;  // the engine clamps negatives
    return id < area_count && (town_byte & facts::AREA_TOWN_BIT) != 0;
}

int32_t filter_slots(uint64_t allowed, uint8_t leader, uint16_t current[SLOTS], uint16_t ui[SLOTS])
{
    if (leader == facts::NO_LEADER || !in_slots(current, leader)) {
        return FILTER_NO_LEADER;
    }
    int32_t hidden = 0;
    for (uint32_t k = 0; k < SLOTS; ++k) {
        if (!keeps(allowed, leader, current[k])) {
            current[k] = facts::EMPTY;
            ui[k] = facts::EMPTY;
            hidden |= 1 << k;
        }
    }
    return hidden;
}

uint32_t need_of(uint64_t allowed, uint8_t leader, const uint16_t current[SLOTS], const uint16_t roster[SLOTS])
{
    // A rebuild builds current from the roster; the filter then needs the leader there, or it writes nothing.
    if (leader == facts::NO_LEADER || !in_slots(roster, leader)) {
        return 0;
    }
    uint32_t need = 0;
    for (uint32_t k = 0; k < SLOTS; ++k) {
        if (!keeps(allowed, leader, current[k])) {
            need |= NEED_HIDE;
        }
        if (roster[k] != facts::EMPTY && keeps(allowed, leader, roster[k]) && !in_slots(current, roster[k])) {
            need |= NEED_SHOW;
        }
    }
    return need;
}

void planner_reset(planner_state& s)
{
    memset(&s, 0, sizeof(s));
}

int32_t plan(planner_state& s, const planner_inputs& in)
{
    if (!s.has_key || in.key != s.key) {
        s.key = in.key;
        s.key_since_ms = in.now_ms;
        s.applied = 0;
        s.refused = 0;
        s.has_key = 1;
    }
    if (in.world_epoch != s.world_epoch) {
        s.world_epoch = in.world_epoch;
        s.key_since_ms = in.now_ms;  // a new world settles again
        s.applied = 0;
        s.refused = 0;
    }
    if (s.in_flight) {
        return PLAN_IN_FLIGHT;
    }
    if (!in.in_town) {
        return PLAN_NOT_TOWN;
    }
    if (in.degraded) {
        return PLAN_DEGRADED;
    }
    if (in.need == 0) {
        return PLAN_NOTHING;
    }
    const uint64_t settle = (in.need & NEED_SHOW) != 0 ? SHOW_SETTLE_MS : HIDE_SETTLE_MS;
    if (in.now_ms - s.key_since_ms < settle) {
        return PLAN_WAIT_SETTLE;
    }
    if (s.applied >= MAX_APPLIED || s.refused >= MAX_REFUSED) {
        return PLAN_GAVE_UP;
    }
    if (s.pushed_once && in.now_ms - s.last_push_ms < MIN_INTERVAL_MS) {
        return PLAN_WAIT_INTERVAL;
    }
    if (!in.world_ok) {
        return PLAN_WAIT_WORLD;
    }
    if (in.task_busy) {
        return PLAN_WAIT_TASK;
    }
    return PLAN_PUSH;
}

void planner_pushed(planner_state& s, bool accepted, uint32_t ticket, uint64_t now_ms)
{
    s.last_push_ms = now_ms;
    s.pushed_once = 1;
    if (accepted) {
        s.in_flight = 1;
        s.ticket = ticket;
    } else {
        ++s.refused;
    }
}

void planner_result(planner_state& s, int32_t result)
{
    s.in_flight = 0;
    s.ticket = 0;
    if (result >= 1) {
        ++s.applied;
    } else {
        ++s.refused;
    }
}

}  // namespace ilc::towns
