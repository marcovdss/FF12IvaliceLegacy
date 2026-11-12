// Ivalice Legacy co-op module: ownership planning and character release (see handoff.h).
#include "session/handoff.h"

#include <string.h>

namespace ilc::session {
namespace {

using namespace facts;

void emit(effect_list& out, uint8_t kind, uint8_t reason, const owner_token& token)
{
    if (out.count >= EFFECTS_MAX) {
        ++out.dropped;
        return;
    }
    effect& e = out.item[out.count++];
    memset(&e, 0, sizeof(e));
    e.kind = kind;
    e.reason = reason;
    e.token = token;
}

uint64_t later(uint64_t a, uint64_t b)
{
    return a > b ? a : b;
}

release_intent fresh_intent(const owner_token& token, uint64_t now)
{
    release_intent r;
    memset(&r, 0, sizeof(r));
    r.token = token;
    r.created_ms = now;
    r.deadline_ms = now + RELEASE_WINDOW_MS;
    r.next_attempt_ms = now;
    return r;
}

// One intent, one sample: false = the intent is finished (its terminal effect, if any, is in `out`).
bool step_intent(release_intent& r, const follower_sample* s, uint64_t now, uint32_t world_epoch, effect_list& out)
{
    const owner_token& token = r.token;
    if (token.world_epoch != world_epoch) {
        emit(out, FX_FOLLOW_ABANDONED, WHY_WORLD, token);
        return false;
    }
    if (s != nullptr && !same_token(s->token, token)) {
        emit(out, FX_FOLLOW_ABANDONED, WHY_GENERATION, token);
        return false;
    }
    if (s != nullptr && sample_cancels(*s)) {
        emit(out, FX_FOLLOW_ABANDONED, WHY_INELIGIBLE, token);
        return false;
    }
    if (s != nullptr && sample_confirms_follow(*s)) {
        if (!r.has_request) {
            emit(out, FX_FOLLOW_CONFIRMED, WHY_ALREADY_FOLLOWING, token);
            return false;
        }
        if (s->sample > r.requested_sample) {
            emit(out, FX_FOLLOW_CONFIRMED, WHY_ACCEPTED, token);
            return false;
        }
    }
    // KO, a command-blocking status or a turn as the native leader can last any time: wait without writing and
    // without spending an attempt.
    const bool must_wait = s != nullptr && (s->alive == T_NO || s->command_blocked == T_YES || s->leader == token.member);
    // A read gap is also write-free, but it does not by itself turn into an unbounded wait.
    const bool unsure = s != nullptr && (!s->available || s->alive == T_UNKNOWN || s->command_blocked == T_UNKNOWN);
    if (must_wait) {
        r.deadline_ms = later(r.deadline_ms, now + RELEASE_WINDOW_MS);
        r.waiting = 1;
        return true;
    }
    if (unsure) {
        r.deadline_ms = later(r.deadline_ms, now + RELEASE_WINDOW_MS);
        return true;
    }
    if (r.waiting) {
        if (s == nullptr || !sample_allows_request(*s)) {
            // The status can clear long before the action and follower list settle: keep waiting for the whole
            // contract to appear.
            r.deadline_ms = later(r.deadline_ms, now + RELEASE_WINDOW_MS);
            return true;
        }
        // A new eligible stretch: a fresh bounded window. Requests made before the wait cannot confirm it.
        r.deadline_ms = now + RELEASE_WINDOW_MS;
        r.attempts = 0;
        r.next_attempt_ms = now;
        r.has_request = 0;
        r.requested_sample = 0;
        r.waiting = 0;
    }
    if (s != nullptr && !sample_cancels(*s) && !sample_confirms_follow(*s) && !sample_allows_request(*s)) {
        // Still settling from its last action (a quick off-on-off toggle lands here): nothing is safe to write
        // until it is idle again, and the last release must not be dropped by the clock.
        r.deadline_ms = later(r.deadline_ms, now + RELEASE_WINDOW_MS);
        r.waiting = 1;
        return true;
    }
    if (now >= r.deadline_ms) {
        const bool gap = s != nullptr && !sample_cancels(*s) &&
                         (!s->available || s->alive != T_YES || s->leader == MEMBER_NONE || s->command_blocked != T_NO);
        if (gap) {
            r.deadline_ms = now + RELEASE_WINDOW_MS;
            return true;
        }
        emit(out, FX_FOLLOW_ABANDONED, WHY_BOUNDED, token);
        return false;
    }
    if (r.attempts >= RELEASE_ATTEMPTS) {
        // The last request keeps its full answer window.
        if (now < r.next_attempt_ms) {
            return true;
        }
        emit(out, FX_FOLLOW_ABANDONED, WHY_BOUNDED, token);
        return false;
    }
    if (s == nullptr || !sample_allows_request(*s) || now < r.next_attempt_ms) {
        return true;
    }
    ++r.attempts;
    r.next_attempt_ms = now + RELEASE_RETRY_MS;
    r.has_request = 1;
    r.requested_sample = s->sample;
    emit(out, FX_ASK_FOLLOW, WHY_ELIGIBLE, token);
    return true;
}

int32_t find_intent(const ownership& state, const owner_token& token)
{
    for (uint32_t i = 0; i < state.intent_count; ++i) {
        if (same_incarnation(state.intent[i].token, token)) {
            return static_cast<int32_t>(i);
        }
    }
    return -1;
}

void remove_intent(ownership& state, uint32_t index)
{
    for (uint32_t i = index + 1; i < state.intent_count; ++i) {
        state.intent[i - 1] = state.intent[i];
    }
    --state.intent_count;
    memset(&state.intent[state.intent_count], 0, sizeof(release_intent));
}

void put_intent(ownership& state, const release_intent& r)
{
    const int32_t at = find_intent(state, r.token);
    if (at >= 0) {
        state.intent[at] = r;
    } else if (state.intent_count < INTENTS_MAX) {
        state.intent[state.intent_count++] = r;
    }
}

struct key_set {
    owner_token k[SEATS * 2];
    uint32_t n;
    bool has(const owner_token& t) const
    {
        for (uint32_t i = 0; i < n; ++i) {
            if (same_incarnation(k[i], t)) {
                return true;
            }
        }
        return false;
    }
    void add(const owner_token& t)
    {
        if (n < SEATS * 2) {
            k[n++] = t;
        }
    }
};

}  // namespace

bool same_token(const owner_token& a, const owner_token& b)
{
    return a.seat == b.seat && a.member == b.member && a.actor_generation == b.actor_generation &&
           a.world_epoch == b.world_epoch && a.handle == b.handle && a.actor == b.actor && a.phys == b.phys;
}

bool same_incarnation(const owner_token& a, const owner_token& b)
{
    return a.member == b.member && a.actor_generation == b.actor_generation && a.world_epoch == b.world_epoch;
}

bool sample_confirms_follow(const follower_sample& s)
{
    return s.available && s.alive == T_YES && s.materialized && s.layout_coherent && s.token.handle != 0 &&
           s.command_blocked == T_NO && s.leader != MEMBER_NONE && s.leader != s.token.member &&
           s.action == ACTION_FOLLOW && s.follows_list;
}

bool sample_allows_request(const follower_sample& s)
{
    return s.available && s.alive == T_YES && s.materialized && s.layout_coherent && s.token.handle != 0 &&
           !s.cutscene && s.command_blocked == T_NO && s.leader != MEMBER_NONE && s.leader != s.token.member &&
           s.action_state == 1 && s.target == 0 && s.action == ACTION_IDLE && s.descriptor_action == ACTION_FOLLOW &&
           s.follows_list;
}

bool sample_cancels(const follower_sample& s)
{
    // KO and a turn as native leader are reasons to wait, not proof that the incarnation is gone.
    return s.cutscene || !s.materialized || !s.layout_coherent;
}

const follower_sample* sample_set::find(const owner_token& incarnation) const
{
    for (uint32_t i = 0; i < count; ++i) {
        if (same_incarnation(items[i].of, incarnation)) {
            return &items[i];
        }
    }
    return nullptr;
}

void plan_ownership(ownership& state, const owner_token* desired, const uint8_t* has_desired, const uint8_t* eligible,
                    const sample_set& samples, uint64_t now_ms, uint32_t world_epoch, effect_list& out)
{
    // Incarnations about to be (or staying) owned: their old release intents must never fire.
    key_set keep = {};
    for (uint32_t s = 0; s < SEATS; ++s) {
        if (has_desired[s] && eligible[s]) {
            keep.add(desired[s]);
        }
        if (state.has_owner[s] && has_desired[s] && same_token(desired[s], state.owner[s])) {
            keep.add(state.owner[s]);
        }
    }
    for (uint32_t i = 0; i < state.intent_count;) {
        if (keep.has(state.intent[i].token)) {
            remove_intent(state, i);
        } else {
            ++i;
        }
    }
    uint32_t changed = 0;
    for (uint32_t s = 0; s < SEATS; ++s) {
        const bool differs = state.has_owner[s] != has_desired[s] ||
                             (state.has_owner[s] && !same_token(state.owner[s], desired[s]));
        if (differs) {
            changed |= 1u << s;
        }
    }
    if (changed == 0) {
        return;
    }
    key_set old = {};
    owner_token old_tokens[SEATS];
    uint32_t old_count = 0;
    for (uint32_t s = 0; s < SEATS; ++s) {
        if ((changed & (1u << s)) && state.has_owner[s]) {
            old_tokens[old_count++] = state.owner[s];
            old.add(state.owner[s]);
        }
    }
    for (uint32_t i = 0; i < old_count; ++i) {
        emit(out, FX_RELEASE, WHY_NONE, old_tokens[i]);
    }
    for (uint32_t i = 0; i < old_count; ++i) {
        emit(out, FX_CLEAR, WHY_NONE, old_tokens[i]);
    }
    // A character moving straight to another seat is not asked to follow in between.
    for (uint32_t i = 0; i < old_count; ++i) {
        if (keep.has(old_tokens[i])) {
            continue;
        }
        release_intent r = fresh_intent(old_tokens[i], now_ms);
        if (step_intent(r, samples.find(old_tokens[i]), now_ms, world_epoch, out)) {
            put_intent(state, r);
        }
    }
    for (uint32_t s = 0; s < SEATS; ++s) {
        if (changed & (1u << s)) {
            state.has_owner[s] = 0;
            memset(&state.owner[s], 0, sizeof(owner_token));
        }
    }
    for (uint32_t s = 0; s < SEATS; ++s) {
        if ((changed & (1u << s)) && has_desired[s] && eligible[s]) {
            state.owner[s] = desired[s];
            state.has_owner[s] = 1;
            emit(out, FX_ACQUIRE, old.has(desired[s]) ? WHY_TRANSFER : WHY_NONE, desired[s]);
        }
    }
}

void advance_intents(ownership& state, const sample_set& samples, uint64_t now_ms, uint32_t world_epoch,
                     effect_list& out)
{
    for (uint32_t i = 0; i < state.intent_count;) {
        release_intent& r = state.intent[i];
        if (step_intent(r, samples.find(r.token), now_ms, world_epoch, out)) {
            ++i;
        } else {
            remove_intent(state, i);
        }
    }
}

void end_intents(ownership& state, uint8_t reason, effect_list& out)
{
    for (uint32_t i = 0; i < state.intent_count; ++i) {
        emit(out, FX_FOLLOW_ABANDONED, reason, state.intent[i].token);
    }
    memset(state.intent, 0, sizeof(state.intent));
    state.intent_count = 0;
}

void refuse_acquire(ownership& state, const owner_token& token, bool restore_intent, uint64_t now_ms)
{
    if (token.seat < SEATS && state.has_owner[token.seat] && same_token(state.owner[token.seat], token)) {
        state.has_owner[token.seat] = 0;
        memset(&state.owner[token.seat], 0, sizeof(owner_token));
    }
    if (restore_intent && find_intent(state, token) < 0) {
        put_intent(state, fresh_intent(token, now_ms));
    }
}

uint32_t world_epochs::observe(bool valid, uint64_t a, uint64_t b)
{
    if (!valid) {
        if (epoch != 0) {
            invalid_since = 1;
        }
        return epoch;
    }
    if (epoch == 0 || !has_signature || a != sig_a || b != sig_b || invalid_since) {
        ++epoch;
    }
    sig_a = a;
    sig_b = b;
    has_signature = 1;
    invalid_since = 0;
    return epoch;
}

bool generation_tracker::observe(uint8_t seat, uint16_t member, uint64_t actor, uint64_t phys, uint32_t handle,
                                 uint32_t world_epoch, tri alive, bool present, owner_token* out)
{
    if (member >= GENERATION_MEMBERS) {
        return false;
    }
    entry& p = e[member];
    if (!present) {
        if (p.known) {
            p.present = 0;
        }
        return false;
    }
    if (actor == 0 || phys == 0) {
        return false;  // pointers absent while the character is in the party: no token, and no rebirth either
    }
    uint32_t stable = handle;
    if (handle == 0 && p.known && p.actor == actor && p.phys == phys) {
        stable = p.handle;  // a failed handle read must not invent an incarnation
    }
    const bool handle_changed = p.known && p.handle != 0 && handle != 0 && p.handle != handle;
    bool reborn = !p.known || p.actor != actor || p.phys != phys || handle_changed || !p.present;
    if (p.known) {
        reborn = reborn || p.epoch != world_epoch;
    }
    if (reborn) {
        p.generation = ++p.next;
    }
    p.epoch = world_epoch;
    p.actor = actor;
    p.phys = phys;
    p.handle = stable;
    p.alive = alive != T_UNKNOWN ? static_cast<int8_t>(alive) : (p.known ? p.alive : static_cast<int8_t>(T_YES));
    p.present = 1;
    p.known = 1;
    out->seat = seat;
    out->reserved0 = 0;
    out->member = member;
    out->actor_generation = p.generation;
    out->world_epoch = world_epoch;
    out->handle = stable;
    out->actor = actor;
    out->phys = phys;
    return true;
}

}  // namespace ilc::session
