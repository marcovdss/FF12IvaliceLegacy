// Ivalice Legacy co-op module: seat assignment rules (see assign.h).
#include "party/assign.h"

#include <string.h>

#include "session/session.h"

namespace ilc::party {
namespace {

bool in_session(const seats_view& s, uint32_t seat)
{
    return seat < ACTIVE_SEATS && (seat == 0 || (s.flags[seat] & session::SEATF_IN_SESSION) != 0);
}

// The seat that controls `member`, or NO_SEAT.
uint8_t holder_of(const seats_view& s, uint32_t member)
{
    for (uint32_t k = 0; k < ACTIVE_SEATS; ++k) {
        if (in_session(s, k) && s.member[k] == member) {
            return static_cast<uint8_t>(k);
        }
    }
    return NO_SEAT;
}

bool seats_unique(const seats_view& s)
{
    for (uint32_t a = 0; a < ACTIVE_SEATS; ++a) {
        if (!in_session(s, a) || s.member[a] == MEMBER_NONE) {
            continue;
        }
        for (uint32_t b = a + 1; b < ACTIVE_SEATS; ++b) {
            if (in_session(s, b) && s.member[b] == s.member[a]) {
                return false;
            }
        }
    }
    return true;
}

bool member_able(const party_view& p, uint32_t member)
{
    const int32_t k = slot_in_party(p, member);
    return k >= 0 && p.able[k] == 1;
}

}  // namespace

bool benched(const seats_view& s, uint32_t seat)
{
    return seat > 0 && in_session(s, seat) && s.member[seat] == MEMBER_NONE;
}

bool picker_allows(uint32_t seat, uint32_t member, uint32_t p1_member)
{
    if (seat >= ACTIVE_SEATS || !is_regular(member)) {
        return false;
    }
    return seat == 0 || member != p1_member;
}

int32_t slot_in_party(const party_view& p, uint32_t member)
{
    if (member == MEMBER_NONE) {
        return -1;
    }
    for (uint32_t k = 0; k < SLOTS; ++k) {
        if (p.current[k] == member) {
            for (uint32_t u = 0; u < SLOTS; ++u) {
                if (p.ui[u] == member) {
                    return static_cast<int32_t>(k);
                }
            }
            return -1;
        }
    }
    return -1;
}

int32_t plan_change(const seats_view& s, const party_view& p, uint32_t seat, uint32_t target, change_plan* out)
{
    memset(out, 0, sizeof(*out));
    out->seat = static_cast<uint8_t>(seat < 0xFF ? seat : 0xFF);
    out->target = static_cast<uint16_t>(target <= 0xFFFF ? target : MEMBER_NONE);
    out->other = NO_SEAT;
    out->new_leader = MEMBER_NONE;
    out->released = MEMBER_NONE;
    for (uint32_t k = 0; k < SEATS; ++k) {
        out->after[k] = k < ACTIVE_SEATS && in_session(s, k) ? s.member[k] : MEMBER_NONE;
    }
    if (!in_session(s, seat)) {
        return REFUSE_BAD_SEAT;
    }
    if (!is_regular(target)) {
        return REFUSE_NOT_REGULAR;
    }
    if (!seats_unique(s)) {
        return REFUSE_DUPLICATE;
    }
    // Player 1 drives the native leader: any change waits while that is not so (a KO promotion, a change the
    // session has not adopted yet).
    if (s.member[0] == MEMBER_NONE || p.leader != s.member[0]) {
        return REFUSE_P1_UNSTABLE;
    }
    const int32_t slot = slot_in_party(p, target);
    if (slot < 0) {
        return REFUSE_NOT_IN_PARTY;
    }
    out->target_slot = static_cast<uint8_t>(slot);
    const uint16_t mine = s.member[seat];
    out->released = mine;
    if (mine == target) {
        out->kind = KIND_ALREADY;
        return CHANGE_OK;
    }
    if (p.able[slot] != 1) {
        return REFUSE_UNAVAILABLE;
    }
    if (p.busy[slot] != 0) {
        return REFUSE_BUSY;
    }
    const uint8_t holder = holder_of(s, target);
    if (holder != NO_SEAT) {
        // Swap when taken: the holder gets the requester's member, which must be one it can play.
        if (mine == MEMBER_NONE) {
            return REFUSE_BENCHED;
        }
        if (!member_able(p, mine)) {
            return REFUSE_UNAVAILABLE;
        }
        out->other = holder;
        out->after[seat] = static_cast<uint16_t>(target);
        out->after[holder] = mine;
        out->mask = (1u << seat) | (1u << holder);
        if (seat == 0 || holder == 0) {
            out->kind = KIND_SWAP_P1;
            out->new_leader = out->after[0];
        } else {
            out->kind = KIND_SWAP_GUESTS;
        }
        return CHANGE_OK;
    }
    out->after[seat] = static_cast<uint16_t>(target);
    out->mask = 1u << seat;
    if (seat == 0) {
        out->kind = KIND_LEADER;
        out->new_leader = static_cast<uint16_t>(target);
    } else {
        out->kind = KIND_TAKE_FREE;
    }
    return CHANGE_OK;
}

bool same_plan(const change_plan& a, const change_plan& b)
{
    return a.kind == b.kind && a.seat == b.seat && a.other == b.other && a.target == b.target &&
           a.released == b.released && a.new_leader == b.new_leader && a.mask == b.mask &&
           memcmp(a.after, b.after, sizeof(a.after)) == 0;
}

}  // namespace ilc::party
