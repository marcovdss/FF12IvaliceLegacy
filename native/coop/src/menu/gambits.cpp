// Ivalice Legacy co-op module: Gambits on/off state (see gambits.h).
#include "menu/gambits.h"

#include "menu/menu_facts.h"

namespace ilc::menu {

int32_t gambit_check_identity(const gambit_identity& id)
{
    if (id.member > 5) {
        return GB_INVALID_MEMBER;
    }
    if (id.handle == 0 || id.actor == 0) {
        return GB_STALE_IDENTITY;
    }
    if (id.physics == 0) {
        return GB_STALE_PHYSICS;
    }
    if (id.record == 0 || id.party_block == 0 ||
        id.record != id.party_block + facts::MEMBER_RECORDS + uint64_t{id.member} * facts::MEMBER_RECORD_SIZE) {
        return GB_STALE_RECORD;
    }
    return GB_APPLIED;
}

void gambit_observe(gambit_state* g, uint8_t record_state)
{
    if (g->in_flight) {
        return;  // the operation's own answer decides
    }
    g->known = 1;
    g->enabled = (record_state & facts::REC_GAMBITS_ON) ? 1 : 0;
}

int32_t gambit_request(gambit_state* g, const gambit_identity& id, uint32_t status)
{
    ++g->requests;
    int32_t refusal = GB_PENDING;
    if (g->in_flight) {
        refusal = GB_BUSY;
    } else if (!g->known) {
        refusal = GB_UNKNOWN;
    } else {
        const int32_t proof = gambit_check_identity(id);
        if (proof != GB_APPLIED) {
            refusal = proof;
        } else if (status & facts::ST_GAMBITS_BLOCKED) {
            refusal = GB_BLOCKED_STATUS;
        }
    }
    if (refusal != GB_PENDING) {
        ++g->refused;
        g->last_result = refusal;
        g->sound = 0;
        return refusal;
    }
    g->in_flight = 1;
    g->desired = g->enabled ? 0 : 1;
    g->last_result = GB_PENDING;
    g->sound = 0;
    return GB_PENDING;
}

void gambit_complete(gambit_state* g, int32_t result, uint32_t final_enabled)
{
    if (!g->in_flight) {
        return;
    }
    g->in_flight = 0;
    if (result == GB_APPLIED && final_enabled <= 1) {
        g->enabled = static_cast<uint8_t>(final_enabled);
        g->known = 1;
        g->last_result = GB_APPLIED;
        g->sound = final_enabled ? facts::SOUND_GAMBITS_ON : facts::SOUND_GAMBITS_OFF;
        ++g->applied;
        return;
    }
    g->last_result = result == GB_APPLIED ? GB_VERIFY_FAILED : result;
    g->sound = 0;
    ++g->refused;
}

}  // namespace ilc::menu
