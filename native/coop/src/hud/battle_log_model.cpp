// Ivalice Legacy co-op module: the battle log's model (pure): feed messages and unit samples -> lines (battle_log.h).
//
// No game memory, no game call, no allocation: everything lives in the `model` the caller owns.
#include "hud/battle_log.h"

#include "hud/battle_log_facts.h"
#include "hud/hud_internal.h"

namespace ilc::hud::battle_log {
namespace {

namespace f = facts;
using detail::zero_bytes;

constexpr uint16_t NO_ACTION = 0xFFFF;
// Pseudo actions for unattributed changes (merge keys; never game action ids, which stay below ACTION_LIMIT).
constexpr uint16_t SRC_POISON = 0xFFF0;
constexpr uint16_t SRC_SAP = 0xFFF1;
constexpr uint16_t SRC_REGEN = 0xFFF2;
constexpr uint16_t SRC_NONE = 0xFFF3;
constexpr uint64_t FORGET_MS = 10000;      // a unit neither sampled nor acting for this long is forgotten
constexpr uint32_t MERGE_LOOKBACK = 6;     // newest lines a merge looks at
constexpr uint64_t SPILL_MS = 400;         // an area action's victims arrive within this of each other

void copy_text(char* dst, uint32_t cap, const char* src)
{
    uint32_t n = 0;
    if (cap == 0) {
        return;
    }
    if (src != nullptr) {
        for (; n + 1 < cap && src[n] != '\0'; ++n) {
            dst[n] = src[n];
        }
    }
    dst[n] = '\0';
}

uint32_t length(const char* s, uint32_t cap)
{
    uint32_t n = 0;
    while (n < cap && s[n] != '\0') {
        ++n;
    }
    return n;
}

void append(char* dst, uint32_t cap, const char* src)
{
    uint32_t n = length(dst, cap);
    for (uint32_t k = 0; src[k] != '\0' && n + 1 < cap; ++k) {
        dst[n++] = src[k];
    }
    if (n < cap) {
        dst[n] = '\0';
    }
}

void append_number(char* dst, uint32_t cap, uint32_t v)
{
    char digits[12];
    uint32_t n = 0;
    do {
        digits[n++] = static_cast<char>('0' + v % 10);
        v /= 10;
    } while (v != 0 && n < sizeof(digits));
    char text[12];
    for (uint32_t k = 0; k < n; ++k) {
        text[k] = digits[n - 1 - k];
    }
    text[n] = '\0';
    append(dst, cap, text);
}

bool same_text(const char* a, const char* b, uint32_t cap)
{
    for (uint32_t k = 0; k < cap; ++k) {
        if (a[k] != b[k]) {
            return false;
        }
        if (a[k] == '\0') {
            return true;
        }
    }
    return true;
}

// ---- tracked units -------------------------------------------------------------------------------------------------------
tracked* track_find(model* m, uint32_t handle)
{
    if (handle == 0) {
        return nullptr;
    }
    for (tracked& t : m->units) {
        if (t.handle == handle) {
            return &t;
        }
    }
    return nullptr;
}

tracked* track_get(model* m, uint32_t handle)
{
    if (tracked* t = track_find(m, handle)) {
        return t;
    }
    if (handle == 0) {
        return nullptr;
    }
    tracked* slot = nullptr;
    uint64_t oldest = ~0ull;
    for (tracked& t : m->units) {
        if (t.handle == 0) {
            slot = &t;
            break;
        }
        const uint64_t last = t.sampled_ms > t.event_ms ? t.sampled_ms : t.event_ms;
        if (last < oldest) {
            oldest = last;
            slot = &t;
        }
    }
    zero_bytes(slot, sizeof(tracked));
    slot->handle = handle;
    slot->event_action = NO_ACTION;
    slot->action_now = NO_ACTION;
    return slot;
}

uint8_t dot_of(uint8_t side, uint8_t seat)
{
    if (side == SIDE_PARTY) {
        return seat >= 1 && seat <= 4 ? seat : static_cast<uint8_t>(DOT_GAMBIT);
    }
    return side == SIDE_FOE ? static_cast<uint8_t>(DOT_FOE) : static_cast<uint8_t>(DOT_SYSTEM);
}

uint8_t tone_of(uint8_t side, uint8_t seat)
{
    if (side == SIDE_PARTY) {
        return seat >= 1 && seat <= 4 ? seat : static_cast<uint8_t>(TONE_GAMBIT);
    }
    return side == SIDE_FOE ? static_cast<uint8_t>(TONE_FOE) : static_cast<uint8_t>(TONE_TEXT);
}

uint8_t category_of(uint8_t side)
{
    return side == SIDE_FOE ? static_cast<uint8_t>(CAT_FOE)
                            : side == SIDE_PARTY ? static_cast<uint8_t>(CAT_PARTY) : static_cast<uint8_t>(CAT_SYSTEM);
}

uint8_t seat_bit(uint8_t seat)
{
    return seat >= 1 && seat <= 4 ? static_cast<uint8_t>(1u << (seat - 1)) : 0;
}

// ---- lines -----------------------------------------------------------------------------------------------------------------
line* new_line(model* m, uint8_t cls, uint64_t now)
{
    line* l = &m->lines[m->head];
    m->head = (m->head + 1) % HISTORY;
    if (m->count < HISTORY) {
        ++m->count;
    }
    zero_bytes(l, sizeof(line));
    if (++m->next_id == 0) {
        m->next_id = 1;
    }
    l->id = m->next_id;
    l->cls = cls;
    l->action = NO_ACTION;
    l->born_ms = now;
    l->touched_ms = now;
    return l;
}

// The k-th newest line (0 = newest), or null.
line* newest(model* m, uint32_t k)
{
    if (k >= m->count) {
        return nullptr;
    }
    const uint32_t at = (m->head + HISTORY - 1 - k) % HISTORY;
    return &m->lines[at];
}

line* line_by_id(model* m, uint32_t id)
{
    if (id == 0) {
        return nullptr;
    }
    for (uint32_t k = 0; k < m->count; ++k) {
        line* l = newest(m, k);
        if (l->id == id) {
            return l;
        }
    }
    return nullptr;
}

bool recent(const line& l, uint64_t now, uint64_t window)
{
    return now >= l.touched_ms ? now - l.touched_ms <= window : true;
}

void set_actor(line* l, uint32_t handle, uint8_t side, uint8_t seat, const char* name)
{
    l->actor = handle;
    l->dot = dot_of(side, seat);
    l->actor_tone = tone_of(side, seat);
    l->categories |= category_of(side);
    l->seats |= seat_bit(seat);
    copy_text(l->actor_name, NAME, name);
}

void add_target(line* l, uint32_t handle, uint8_t side, uint8_t seat, const char* name)
{
    l->seats |= seat_bit(seat);
    if (l->target_count == 0) {
        l->target = handle;
        l->target_tone = tone_of(side, seat);
        copy_text(l->target_name, NAME, name);
        l->target_count = 1;
        return;
    }
    if (l->target == handle) {
        return;
    }
    // A second distinct target: the line lists the first and counts the rest (target handles beyond the first are not
    // kept, so a third hit on the second target counts it again only once per merge: target_count is a bound).
    if (l->target_count < 0xFF) {
        ++l->target_count;
    }
    l->flags &= static_cast<uint8_t>(~LF_SINGLE_TARGET);
}

void add_amount(line* l, int32_t amount)
{
    if (l->amount_count < AMOUNTS_SHOWN) {
        l->amounts[l->amount_count++] = amount;
    }
}

void add_icon(line* l, uint8_t icon)
{
    for (uint32_t k = 0; k < l->icon_count; ++k) {
        if (l->icons[k] == icon) {
            return;
        }
    }
    if (l->icon_count < ICONS) {
        l->icons[l->icon_count++] = icon;
    }
}

void touch(line* l, uint64_t now)
{
    if (now > l->touched_ms) {
        l->touched_ms = now;
    }
}

bool important_status(uint32_t bit)
{
    return bit == f::ST_STONE || bit == f::ST_PETRIFY || bit == f::ST_DOOM || bit == f::ST_XZONE;
}

void apply_hint(model* m, line* l, uint32_t victim, uint64_t now)
{
    if (m->hint_tags != 0 && m->hint_victim == victim &&
        (now >= m->hint_ms ? now - m->hint_ms : m->hint_ms - now) <= HINT_MS) {
        l->tags |= m->hint_tags;
        m->hint_tags = 0;
        m->hint_victim = 0;
    }
}

// ---- attribution ---------------------------------------------------------------------------------------------------------
struct attribution {
    tracked* actor;
    uint16_t action;
    uint8_t has_event;
    uint8_t attack;
    const char* name;
};

// The actor whose current target is the victim; the one whose latest action event is the most recent wins; without
// any recent event, an actor whose current action is known. Damage needs the opposite side, healing the same side.
attribution attribute(model* m, const tracked& victim, uint64_t now, bool same_side, bool need_action)
{
    attribution best = {nullptr, NO_ACTION, 0, 0, ""};
    uint64_t best_event = 0;
    for (tracked& a : m->units) {
        if (a.handle == 0 || a.target != victim.handle || (a.handle == victim.handle && !same_side)) {
            continue;
        }
        if (a.side != SIDE_NONE && victim.side != SIDE_NONE && (a.side == victim.side) != same_side) {
            continue;
        }
        const bool event = a.event_ms != 0 && now >= a.event_ms && now - a.event_ms <= ATTRIB_MS;
        const bool action_known = a.action_now != NO_ACTION && a.action_now < f::ACTION_LIMIT;
        if (!event && need_action && !action_known) {
            continue;
        }
        if (best.actor != nullptr) {
            if (best.has_event && !event) {
                continue;
            }
            if (best.has_event && event && a.event_ms <= best_event) {
                continue;
            }
            if (!best.has_event && !event) {
                continue;   // the first actor without an event stays (stable order)
            }
        }
        best.actor = &a;
        best.has_event = event ? 1 : 0;
        best_event = a.event_ms;
        if (event) {
            best.action = a.event_action;
            best.name = a.event_name;
        } else if (action_known) {
            best.action = a.action_now;
            best.name = a.action_name;
        } else {
            best.action = f::ACTION_ATTACK;   // an actor aiming at the victim with nothing known: an attack
            best.name = "";
        }
        best.attack = best.action == f::ACTION_ATTACK ? 1 : 0;
    }
    return best;
}

// The newest line (within MERGE_MS) of this actor and action in one of the classes `mask` (bit per line_class).
line* mergeable(model* m, uint32_t actor, uint16_t action, uint32_t mask, uint64_t now)
{
    for (uint32_t k = 0; k < MERGE_LOOKBACK; ++k) {
        line* l = newest(m, k);
        if (l == nullptr) {
            break;
        }
        if (!recent(*l, now, MERGE_MS)) {
            break;
        }
        if (l->actor == actor && l->action == action && ((mask >> l->cls) & 1u) != 0) {
            return l;
        }
    }
    return nullptr;
}

constexpr uint32_t BIT(uint32_t cls) { return 1u << cls; }

// The newest damage (or healing) line of an actor on the other side (the same side for healing) changed within
// SPILL_MS: an area action's other victims join it.
line* spill(model* m, const tracked& v, bool damage, uint64_t now)
{
    const uint8_t cls = damage ? LK_DAMAGE : LK_HEAL;
    for (uint32_t k = 0; k < MERGE_LOOKBACK; ++k) {
        line* l = newest(m, k);
        if (l == nullptr || !recent(*l, now, SPILL_MS)) {
            break;
        }
        if (l->cls != cls || l->actor == 0 || l->actor == v.handle || l->action >= SRC_POISON ||
            l->target_count == 0) {
            continue;
        }
        const tracked* a = track_find(m, l->actor);
        if (a != nullptr && a->side != SIDE_NONE && v.side != SIDE_NONE && (a->side == v.side) == !damage) {
            return l;
        }
    }
    return nullptr;
}

bool within(uint64_t now, uint64_t at, uint64_t window)
{
    return at != 0 && (now >= at ? now - at <= window : at - now <= window);
}

// The oldest pending hit on this victim still within PENDING_MS (expired ones are left for model_units).
pending* pending_for(model* m, uint32_t victim, uint64_t now)
{
    pending* best = nullptr;
    for (pending& p : m->pend) {
        if (p.victim == victim && victim != 0 && within(now, p.ms, PENDING_MS) && (best == nullptr || p.ms < best->ms)) {
            best = &p;
        }
    }
    return best;
}

// The caster's line for a hit on the victim: the newest mergeable line of that caster and action, else a new one.
// Returns the line; *fresh = it was created (or an action/status line took its first number).
line* caster_line(model* m, uint8_t cls, uint32_t actor, uint8_t side, uint8_t seat, const char* name,
                  uint16_t action, const char* action_name, uint64_t now, bool* fresh)
{
    line* l = mergeable(m, actor, action, BIT(cls) | BIT(LK_ACTION) | BIT(LK_STATUS), now);
    *fresh = false;
    if (l == nullptr) {
        l = new_line(m, cls, now);
        set_actor(l, actor, side, seat, name);
        l->action = action;
        copy_text(l->body, ACTION_NAME, action == f::ACTION_ATTACK ? "" : action_name);
        l->flags |= LF_SINGLE_TARGET;
        if (action == f::ACTION_ATTACK) {
            l->flags |= LF_ATTACK;
        }
        *fresh = true;
    } else if (l->cls == LK_ACTION || l->cls == LK_STATUS) {
        l->cls = cls;   // the action's line gets its first hit: the count stays 1
        l->flags |= LF_SINGLE_TARGET;
        if (l->target_count > 1) {
            l->flags &= static_cast<uint8_t>(~LF_SINGLE_TARGET);
        }
        *fresh = true;
    }
    if (l->count == 0) {
        l->count = 1;
    }
    return l;
}

void count_hits(line* l, bool fresh, uint32_t hits)
{
    const uint32_t n = fresh ? hits : l->count + hits;
    l->count = static_cast<uint8_t>(n > 0xFF ? 0xFF : (n == 0 ? 1 : n));
}

void hp_change(model* m, tracked& v, int32_t delta, uint64_t now, element_fn element, void* ctx)
{
    // Exact numbers already showed this change (the hit's own pop-up): the diff that follows is absorbed.
    if (v.absorb != 0 && within(now, v.absorb_ms, ABSORB_MS) && (v.absorb < 0) == (delta < 0)) {
        const int64_t left = static_cast<int64_t>(delta) - v.absorb;
        if ((delta < 0 && left >= 0) || (delta > 0 && left <= 0)) {
            v.absorb -= delta;
            return;
        }
        delta = static_cast<int32_t>(left);
        v.absorb = 0;
    } else if (v.absorb != 0 && !within(now, v.absorb_ms, ABSORB_MS)) {
        v.absorb = 0;
    }
    const bool damage = delta < 0;
    // The hit feed named the caster (its amount came from the Forge's Lua thread, so only the diff has it).
    if (pending* p = pending_for(m, v.handle, now)) {
        bool fresh = false;
        line* l = caster_line(m, damage ? LK_DAMAGE : LK_HEAL, p->actor, p->side, p->seat, p->name, p->action,
                              p->action_name, now, &fresh);
        count_hits(l, fresh, p->count);
        add_target(l, v.handle, v.side, v.seat, v.name);
        add_amount(l, delta);
        if (damage && v.side == SIDE_PARTY) {
            l->flags |= LF_PARTY_HIT;
        }
        l->tags |= damage ? p->tags : static_cast<uint8_t>(p->tags & ~TAG_WEAK);
        touch(l, now);
        zero_bytes(p, sizeof(pending));
        return;
    }
    const attribution a = attribute(m, v, now, !damage, !damage);
    line* l = nullptr;
    if (a.actor != nullptr) {
        const uint8_t cls = damage ? LK_DAMAGE : LK_HEAL;
        l = mergeable(m, a.actor->handle, a.action, BIT(cls) | BIT(LK_ACTION) | BIT(LK_STATUS), now);
        if (l == nullptr) {
            l = new_line(m, cls, now);
            set_actor(l, a.actor->handle, a.actor->side, a.actor->seat, a.actor->name);
            l->action = a.action;
            copy_text(l->body, ACTION_NAME, a.name);
            l->flags |= LF_SINGLE_TARGET;
            if (a.attack) {
                l->flags |= LF_ATTACK;
            }
        } else if (l->cls == LK_ACTION || l->cls == LK_STATUS) {
            l->cls = cls;   // the action's line gets its number (its first hit: the count stays 1)
            l->flags |= LF_SINGLE_TARGET;
            if (l->target_count > 1) {
                l->flags &= static_cast<uint8_t>(~LF_SINGLE_TARGET);
            }
        } else if (l->count < 0xFF) {
            ++l->count;
        }
        if (l->count == 0) {
            l->count = 1;
        }
    } else if ((l = spill(m, v, damage, now)) != nullptr) {
        // An area action: the victim is not anyone's target, but a line of the same kind from the other side (the
        // same side for healing) changed just now. The victim joins it.
        if (l->count < 0xFF) {
            ++l->count;
        }
    } else {
        // Nobody known aims at the victim: the victim's own line, named after a status that explains it if any.
        uint16_t src = SRC_NONE;
        const char* name = "";
        if (damage && (v.status >> f::ST_POISON & 1u) != 0) {
            src = SRC_POISON;
            name = "Poison";
        } else if (damage && (v.status >> f::ST_SAP & 1u) != 0) {
            src = SRC_SAP;
            name = "Sap";
        } else if (!damage && (v.status >> f::ST_REGEN & 1u) != 0) {
            src = SRC_REGEN;
            name = "Regen";
        }
        const uint8_t cls = damage ? LK_DAMAGE : LK_HEAL;
        l = mergeable(m, v.handle, src, BIT(cls), now);
        if (l == nullptr) {
            l = new_line(m, cls, now);
            set_actor(l, v.handle, v.side, v.seat, v.name);
            l->action = src;
            copy_text(l->body, ACTION_NAME, name);
            l->flags |= LF_SINGLE_TARGET;
            l->count = 1;
        } else if (l->count < 0xFF) {
            ++l->count;
        }
    }
    const bool attributed = a.actor != nullptr || (l->actor != v.handle && l->action < SRC_POISON);
    if (attributed) {
        add_target(l, v.handle, v.side, v.seat, v.name);
    } else {
        l->seats |= seat_bit(v.seat);
    }
    add_amount(l, delta);
    if (damage && v.side == SIDE_PARTY) {
        l->flags |= LF_PARTY_HIT;
    }
    if (damage && attributed && element != nullptr && v.weakness != 0) {
        const uint8_t e = element(ctx, l->action);
        if (e != ELEMENT_UNKNOWN && (e & v.weakness) != 0) {
            l->tags |= TAG_WEAK;
        }
    }
    if (damage) {
        apply_hint(m, l, v.handle, now);
    }
    touch(l, now);
}

void status_change(model* m, tracked& v, uint32_t bit, bool gained, uint64_t now)
{
    const uint8_t icon = static_cast<uint8_t>(bit | (gained ? 0u : ICON_OFF));
    line* l = nullptr;
    if (gained) {
        // A buff from an ally or a debuff from a foe: the actor whose latest event aims at the victim.
        attribution a = attribute(m, v, now, true, true);
        if (a.actor == nullptr || !a.has_event) {
            a = attribute(m, v, now, false, true);
        }
        if (a.actor != nullptr && a.has_event) {
            l = mergeable(m, a.actor->handle, a.action, BIT(LK_DAMAGE) | BIT(LK_HEAL) | BIT(LK_ACTION) | BIT(LK_STATUS),
                          now);
            if (l == nullptr) {
                l = new_line(m, LK_STATUS, now);
                set_actor(l, a.actor->handle, a.actor->side, a.actor->seat, a.actor->name);
                l->action = a.action;
                copy_text(l->body, ACTION_NAME, a.name);
                l->count = 1;
                l->flags |= LF_SINGLE_TARGET;
            } else if (l->cls == LK_ACTION) {
                l->cls = LK_STATUS;
            }
            add_target(l, v.handle, v.side, v.seat, v.name);
        }
    }
    if (l == nullptr) {
        // The unit's own status line: changes of one unit within MERGE_MS share it.
        l = mergeable(m, v.handle, NO_ACTION, BIT(LK_STATUS), now);
        if (l == nullptr) {
            l = new_line(m, LK_STATUS, now);
            set_actor(l, v.handle, v.side, v.seat, v.name);
            l->count = 1;
        }
    }
    add_icon(l, icon);
    l->categories |= CAT_STATUS;
    if (gained && bit == f::ST_LURE && v.side == SIDE_PARTY) {
        l->tags |= TAG_HOLDING;
    }
    if (gained && important_status(bit)) {
        l->flags |= LF_IMPORTANT;
    }
    touch(l, now);
}

// A KO line for a party member, a kill line for a foe; the feed's message and the diff of one unit make one line.
line* knock_out(model* m, uint32_t handle, uint8_t side, uint8_t seat, const char* name, const char* how,
                uint64_t now)
{
    const uint8_t cls = side == SIDE_FOE ? LK_KILL : LK_KO;
    for (uint32_t k = 0; k < MERGE_LOOKBACK; ++k) {
        line* l = newest(m, k);
        if (l == nullptr || !recent(*l, now, KO_MERGE_MS)) {
            break;
        }
        if (l->cls == cls && l->actor == handle && handle != 0) {
            if (how != nullptr && how[0] != '\0') {
                copy_text(l->body, ACTION_NAME, how);
            }
            touch(l, now);
            return l;
        }
    }
    line* l = new_line(m, cls, now);
    set_actor(l, handle, side, seat, name);
    l->count = 1;
    if (cls == LK_KILL) {
        l->dot = DOT_REWARD;
        l->actor_tone = TONE_FOE;
        l->categories = CAT_REWARD | CAT_FOE;
        copy_text(l->body, ACTION_NAME, "defeated");
        m->last_kill_ms = now;
        m->last_kill_id = l->id;
    } else {
        l->flags |= LF_IMPORTANT;
        l->categories |= CAT_STATUS;
        copy_text(l->body, ACTION_NAME, how != nullptr && how[0] != '\0' ? how : "is KO");
        l->icons[0] = static_cast<uint8_t>(f::ST_KO);
        l->icon_count = 1;
    }
    return l;
}

line* revive(model* m, tracked& v, int32_t amount, uint64_t now)
{
    for (uint32_t k = 0; k < MERGE_LOOKBACK; ++k) {
        line* l = newest(m, k);
        if (l == nullptr || !recent(*l, now, KO_MERGE_MS)) {
            break;
        }
        if (l->cls == LK_REVIVE && l->actor == v.handle) {
            if (amount > 0 && l->amount_count == 0) {
                add_amount(l, amount);
            }
            touch(l, now);
            return l;
        }
    }
    line* l = new_line(m, LK_REVIVE, now);
    set_actor(l, v.handle, v.side, v.seat, v.name);
    copy_text(l->body, ACTION_NAME, "is revived");
    l->categories |= CAT_STATUS;
    l->count = 1;
    if (amount > 0) {
        add_amount(l, amount);
    }
    return l;
}

// Loot and gil: onto the last kill line while it is fresh, else a reward line of their own.
line* reward(model* m, const char* text, uint64_t now)
{
    line* l = line_by_id(m, m->last_kill_id);
    if (l == nullptr || now < m->last_kill_ms || now - m->last_kill_ms > REWARD_MS) {
        l = mergeable(m, 0, NO_ACTION, BIT(LK_REWARD), now);
        if (l == nullptr) {
            l = new_line(m, LK_REWARD, now);
            l->dot = DOT_REWARD;
            l->categories = CAT_REWARD;
            l->count = 1;
            copy_text(l->body, ACTION_NAME, "Obtained");
        }
    }
    if (l->extra[0] != '\0') {
        append(l->extra, NAME, ", ");
    }
    append(l->extra, NAME, text);
    touch(l, now);
    return l;
}

line* simple(model* m, uint8_t cls, const who* actor, const char* body, const char* extra, uint64_t now,
             bool important)
{
    line* l = new_line(m, cls, now);
    if (actor != nullptr && (actor->handle != 0 || actor->name[0] != '\0')) {
        set_actor(l, actor->handle, actor->side, actor->seat, actor->name);
    } else {
        l->dot = DOT_SYSTEM;
        l->categories = CAT_SYSTEM;
    }
    if (cls == LK_SYSTEM) {
        l->categories |= CAT_SYSTEM;
    }
    copy_text(l->body, ACTION_NAME, body);
    copy_text(l->extra, NAME, extra);
    l->count = 1;
    if (important) {
        l->flags |= LF_IMPORTANT;
    }
    return l;
}

const char* cancel_reason(uint16_t msg)
{
    switch (msg) {
    case f::MSG_CANCEL_TIME: return "out of time";
    case f::MSG_CANCEL_INTERRUPT: return "interrupted";
    default: return "out of range";
    }
}

const char* unit_note(uint16_t msg)
{
    switch (msg) {
    case f::MSG_SHIFT: return "weakness changed";
    case f::MSG_PALING_UP: return "Paling: physical immune";
    case f::MSG_PALING_DOWN: return "Paling falls";
    case f::MSG_SHIELD_UP: return "Magick Shield: magick immune";
    case f::MSG_SHIELD_DOWN: return "Magick Shield fades";
    case f::MSG_WHITE_WIND_UP: return "White Wind: status immune";
    case f::MSG_WHITE_WIND_DOWN: return "White Wind dies";
    case f::MSG_CALLS_HELP: return "calls for help";
    case f::MSG_SPLITS: return "splits in two";
    case f::MSG_SUMMONS_UNDEAD: return "summons the undead";
    case f::MSG_FEEDS: return "feeds on the conduit";
    case f::MSG_STEALS_LOOT: return "steals your loot";
    default: return "";
    }
}

const char* field_note(uint16_t msg)
{
    // Fields 79..96: the drain/seal fields fall and lift in pairs; the rest are single notes.
    if (msg >= 79 && msg <= 90) {   // NOTADDR: message ids
        return (msg - 79) % 2 == 0 ? "A magick field falls" : "The magick field lifts";
    }
    switch (msg) {
    case 91: return "A magnetic field envelops the party";   // NOTADDR: message id
    case 92: return "The magnetic field dissipates";          // NOTADDR: message id
    default: return "";
    }
}

bool is_action_message(uint16_t msg)
{
    return msg == f::MSG_BEGINS_CASTING || msg == f::MSG_READIES || msg == f::MSG_USES ||
           (msg >= f::MSG_USES_EFFECT_FIRST && msg <= f::MSG_USES_EFFECT_LAST) || msg == 53 || msg == 54 ||
           msg == 57 || msg == 58 || msg == 60 || (msg >= 68 && msg <= 73);   // NOTADDR: message ids ("uses X, ...")
}

}  // namespace

// ---- the model ------------------------------------------------------------------------------------------------------------
void model_reset(model* m)
{
    zero_bytes(m, sizeof(model));
}

uint32_t model_message(model* m, const message& msg)
{
    const uint64_t now = msg.time_ms;
    const uint16_t id = msg.msg;
    line* l = nullptr;
    if (is_action_message(id)) {
        if (tracked* t = track_get(m, msg.a.handle)) {
            if (t->side == SIDE_NONE) {
                t->side = msg.a.side;
                t->seat = msg.a.seat;
                copy_text(t->name, NAME, msg.a.name);
            }
            t->event_action = msg.action;
            t->event_ms = now;
            t->event_kind = static_cast<uint8_t>(id);
            copy_text(t->event_name, ACTION_NAME, msg.action_name);
        }
        const bool foe = msg.a.side == SIDE_FOE;
        // Foes' casts and readies are the tells; a party member's "uses" names the action its effects merge into.
        // A party member's casts and readies wait for their effect (or never show, like the native log's own).
        if (foe || id == f::MSG_USES || (id >= f::MSG_USES_EFFECT_FIRST && id != f::MSG_BEGINS_CASTING &&
                                         id != f::MSG_READIES)) {
            l = new_line(m, LK_ACTION, now);
            set_actor(l, msg.a.handle, msg.a.side, msg.a.seat, msg.a.name);
            l->action = msg.action;
            copy_text(l->body, ACTION_NAME, msg.action_name);
            copy_text(l->extra, NAME,
                      id == f::MSG_READIES ? "readies" : id == f::MSG_BEGINS_CASTING ? "begins casting" : "uses");
            l->count = 1;
            l->flags |= LF_SINGLE_TARGET;
            if (id == f::MSG_READIES && foe) {
                l->flags |= LF_IMPORTANT;
            }
        }
        return l != nullptr ? l->id : 0;
    }
    switch (id) {
    case f::MSG_FALLEN:
    case f::MSG_VOID:
    case f::MSG_STONE: {
        const char* how = id == f::MSG_FALLEN ? "has fallen" : id == f::MSG_VOID ? "is cast into the void"
                                                                                 : "turns to stone";
        if (msg.b.side == SIDE_FOE) {
            l = knock_out(m, msg.b.handle, msg.b.side, msg.b.seat, msg.b.name, nullptr, now);
            if (id != f::MSG_FALLEN) {
                copy_text(l->extra, NAME, id == f::MSG_VOID ? "void" : "stone");
            }
        } else {
            l = knock_out(m, msg.b.handle, msg.b.side, msg.b.seat, msg.b.name, how, now);
            if (id == f::MSG_STONE) {
                l->icons[0] = static_cast<uint8_t>(f::ST_STONE);
            } else if (id == f::MSG_VOID) {
                l->icons[0] = static_cast<uint8_t>(f::ST_XZONE);
            }
        }
        break;
    }
    case f::MSG_CURED_OF:
    case f::MSG_FADES_FROM: {
        const tracked* t = track_find(m, msg.b.handle);
        if (t != nullptr && t->seen != 0) {
            break;   // the unit's own status diff shows it
        }
        if (msg.status >= 0 && msg.status < 32) {
            l = mergeable(m, msg.b.handle, NO_ACTION, BIT(LK_STATUS), now);
            if (l == nullptr) {
                l = simple(m, LK_STATUS, &msg.b, "", "", now, false);
            }
            add_icon(l, static_cast<uint8_t>(msg.status | ICON_OFF));
            l->categories |= CAT_STATUS;
            touch(l, now);
        }
        break;
    }
    case f::MSG_REVIVED: {
        tracked* t = track_get(m, msg.b.handle);
        if (t != nullptr) {
            if (t->side == SIDE_NONE) {
                t->side = msg.b.side;
                t->seat = msg.b.seat;
                copy_text(t->name, NAME, msg.b.name);
            }
            l = revive(m, *t, 0, now);
        }
        break;
    }
    case f::MSG_NO_EFFECT:
        l = simple(m, LK_SYSTEM, nullptr, msg.action_name, "no effect", now, false);
        break;
    case f::MSG_LEVEL_UP: {
        char text[NAME] = "level ";
        append_number(text, NAME, msg.level > 0 ? static_cast<uint32_t>(msg.level) : 0);
        l = simple(m, LK_REWARD, &msg.a, "is now", text, now, true);
        l->dot = DOT_REWARD;
        l->categories |= CAT_REWARD;
        break;
    }
    case f::MSG_OBTAIN_GIL: {
        char text[NAME] = "";
        append_number(text, NAME, msg.value > 0 ? static_cast<uint32_t>(msg.value) : 0);
        append(text, NAME, " gil");
        l = reward(m, text, now);
        break;
    }
    case f::MSG_POACHED:
    case f::MSG_POACH_FAILED:
    case f::MSG_STEAL_FAILED_FIRST:
    case f::MSG_STEAL_FAILED_LAST:
        l = simple(m, LK_STEAL, &msg.a,
                   id == f::MSG_POACHED ? "poached" : id == f::MSG_POACH_FAILED ? "poach failed" : "steal failed",
                   id == f::MSG_POACHED ? msg.item_name : "", now, false);
        add_target(l, msg.b.handle, msg.b.side, msg.b.seat, msg.b.name);
        l->categories |= CAT_REWARD;
        break;
    case f::MSG_BACK_ATTACK:
        l = simple(m, LK_SYSTEM, nullptr, "Back attack!", "", now, true);
        break;
    default:
        if ((id >= f::MSG_OBTAIN_FIRST && id <= f::MSG_OBTAIN_LAST) ||
            (id >= f::MSG_ADDED_FIRST && id <= f::MSG_ADDED_LAST)) {
            char text[NAME] = "";
            copy_text(text, NAME, msg.item_name[0] != '\0' ? msg.item_name : "?");
            if (msg.count > 1) {
                append(text, NAME, " x");
                append_number(text, NAME, static_cast<uint32_t>(msg.count));
            }
            l = reward(m, text, now);
        } else if (id >= f::MSG_STOLE_FIRST && id <= f::MSG_STOLE_GIL_LAST) {
            char text[NAME] = "";
            if (id >= f::MSG_STOLE_GIL_FIRST) {
                append_number(text, NAME, msg.item > 0 ? static_cast<uint32_t>(msg.item) : 0);
                append(text, NAME, " gil");
            } else {
                copy_text(text, NAME, msg.item_name[0] != '\0' ? msg.item_name : "?");
            }
            l = simple(m, LK_STEAL, &msg.a, "stole", text, now, false);
            add_target(l, msg.b.handle, msg.b.side, msg.b.seat, msg.b.name);
            l->categories |= CAT_REWARD;
        } else if (id >= f::MSG_CANCEL_TIME && id <= f::MSG_CANCEL_RANGE) {
            l = simple(m, LK_SYSTEM, &msg.a, msg.action_name, cancel_reason(id), now, false);
            l->action = msg.action;
        } else if (id >= f::MSG_SHIFT && id <= f::MSG_WHITE_WIND_DOWN) {
            l = simple(m, LK_SYSTEM, &msg.b, unit_note(id), "", now,
                       id == f::MSG_PALING_UP || id == f::MSG_SHIELD_UP || id == f::MSG_WHITE_WIND_UP);
        } else if (id >= f::MSG_CALLS_HELP && id <= f::MSG_STEALS_LOOT) {
            l = simple(m, LK_SYSTEM, &msg.a, unit_note(id), "", now, id != f::MSG_FEEDS);
        } else if (id >= f::MSG_FIELD_FIRST && id <= f::MSG_FIELD_LAST && field_note(id)[0] != '\0') {
            l = simple(m, LK_SYSTEM, nullptr, field_note(id), "", now, false);
        } else if (id <= f::MSG_TRAP_LAST) {
            l = simple(m, LK_SYSTEM, &msg.b, "triggered", msg.action_name, now, true);
        }
        break;
    }
    return l != nullptr ? l->id : 0;
}

void model_units(model* m, uint64_t now, const unit_obs* units, uint32_t n, element_fn element, void* ctx)
{
    // First every unit's new state (so attribution sees this sample's targets), then the diffs.
    struct before {
        tracked* t;
        uint32_t hp;
        uint32_t status;
        uint8_t seen;
    };
    before prev[TRACKED];
    uint32_t used = 0;
    for (uint32_t k = 0; k < n && used < TRACKED; ++k) {
        const unit_obs& o = units[k];
        if (o.handle == 0 || (o.flags & OBS_PRESENT) == 0) {
            continue;
        }
        tracked* t = track_get(m, o.handle);
        bool dup = false;
        for (uint32_t j = 0; j < used; ++j) {
            dup = dup || prev[j].t == t;
        }
        if (dup) {
            continue;
        }
        prev[used++] = {t, t->hp, t->status, t->seen};
        t->side = o.side;
        t->seat = o.seat;
        t->weakness = o.weakness;
        t->hp = o.hp;
        t->status = o.status;
        if ((o.flags & OBS_TARGET) != 0) {
            t->target = o.target;
            t->action_now = o.action_now;
            copy_text(t->action_name, ACTION_NAME, o.action_name);
        }
        if (o.name[0] != '\0' && !same_text(t->name, o.name, NAME)) {
            copy_text(t->name, NAME, o.name);
        }
        t->sampled_ms = now;
    }
    // Units someone aims at first, so an area action's line exists before its other victims look for it.
    uint32_t order[TRACKED];
    uint32_t ordered = 0;
    for (uint32_t pass = 0; pass < 2; ++pass) {
        for (uint32_t k = 0; k < used; ++k) {
            bool aimed = false;
            for (const tracked& a : m->units) {
                aimed = aimed || (a.handle != 0 && a.handle != prev[k].t->handle && a.target == prev[k].t->handle);
            }
            if (aimed == (pass == 0)) {
                order[ordered++] = k;
            }
        }
    }
    for (uint32_t i = 0; i < ordered; ++i) {
        const uint32_t k = order[i];
        tracked& t = *prev[k].t;
        if (prev[k].seen == 0) {
            t.seen = 1;   // the first sample is a baseline (it already holds whatever exact numbers showed)
            t.absorb = 0;
            continue;
        }
        const uint32_t was_hp = prev[k].hp;
        const uint32_t was = prev[k].status;
        const bool ko_was = was_hp == 0 || (was >> f::ST_KO & 1u) != 0;
        const bool ko_now = t.hp == 0 || (t.status >> f::ST_KO & 1u) != 0;
        if (t.hp < was_hp) {
            hp_change(m, t, -static_cast<int32_t>(was_hp - t.hp), now, element, ctx);
        } else if (t.hp > was_hp && !ko_was) {
            hp_change(m, t, static_cast<int32_t>(t.hp - was_hp), now, element, ctx);
        }
        if (ko_now && !ko_was) {
            knock_out(m, t.handle, t.side, t.seat, t.name, nullptr, now);
        } else if (ko_was && !ko_now) {
            revive(m, t, t.hp > was_hp ? static_cast<int32_t>(t.hp - was_hp) : 0, now);
        }
        const uint32_t changed = (was ^ t.status) & ~((1u << f::ST_KO) | (1u << f::ST_HP_CRITICAL));
        for (uint32_t bit = 0; bit < 32; ++bit) {
            if ((changed >> bit & 1u) != 0 && !(ko_now && bit != f::ST_KO && (t.status >> bit & 1u) == 0)) {
                status_change(m, t, bit, (t.status >> bit & 1u) != 0, now);
            }
        }
    }
    // Hits the feed saw that no HP change claimed (the victim was not followed yet, or nothing changed): the caster's
    // line still names the hit and its tags, without an amount.
    for (pending& p : m->pend) {
        if (p.victim == 0 || within(now, p.ms, PENDING_MS)) {
            continue;
        }
        bool fresh = false;
        line* l = caster_line(m, LK_DAMAGE, p.actor, p.side, p.seat, p.name, p.action, p.action_name, p.ms, &fresh);
        count_hits(l, fresh, p.count);
        add_target(l, p.victim, p.victim_side, p.victim_seat, p.victim_name);
        l->tags |= p.tags;
        touch(l, p.ms);
        zero_bytes(&p, sizeof(pending));
    }
    // Forget units nobody sampled or heard from for a while (their slots are reused).
    for (tracked& t : m->units) {
        const uint64_t last = t.sampled_ms > t.event_ms ? t.sampled_ms : t.event_ms;
        if (t.handle != 0 && now > last && now - last > FORGET_MS) {
            zero_bytes(&t, sizeof(tracked));
        }
    }
}

void model_hint(model* m, uint32_t victim, uint8_t tags, uint64_t now)
{
    for (uint32_t k = 0; k < MERGE_LOOKBACK; ++k) {
        line* l = newest(m, k);
        if (l == nullptr) {
            break;
        }
        const uint64_t age = now >= l->touched_ms ? now - l->touched_ms : 0;
        if (age > HINT_MS) {
            break;
        }
        if (l->cls == LK_DAMAGE && (l->target == victim || (l->target_count == 0 && l->actor == victim))) {
            l->tags |= tags;
            return;
        }
    }
    m->hint_victim = victim;
    m->hint_tags = tags;
    m->hint_ms = now;
}

uint8_t outcome_tags(uint8_t outcome)
{
    switch (outcome) {
    case f::OUT_MISS: return TAG_MISS;
    case f::OUT_PARRY: return TAG_PARRY;
    case f::OUT_EVADE:
    case f::OUT_SHIELD:
    case f::OUT_EVADE_COUNTER:
    case f::OUT_SHIELD_COUNTER:
    case f::OUT_MAGICK_EVADE: return TAG_BLOCK;
    case f::OUT_IMMUNE:
    case f::OUT_SAFETY: return TAG_IMMUNE;
    default: return 0;   // a hit, reflected, skipped, unknown
    }
}

uint32_t model_hit(model* m, const hit& h)
{
    const uint64_t now = h.time_ms;
    if (h.outcome == f::OUT_SKIPPED || h.caster.handle == 0 || h.target.handle == 0) {
        return 0;
    }
    const uint8_t no_damage = outcome_tags(h.outcome);
    const uint8_t crit = (h.flags & f::HIT_CRIT) != 0 ? static_cast<uint8_t>(TAG_CRIT) : 0;
    if (no_damage != 0) {
        // Nothing reaches the victim: the caster's line says so, on its own or merged with the action's other hits.
        bool fresh = false;
        line* l = caster_line(m, LK_DAMAGE, h.caster.handle, h.caster.side, h.caster.seat, h.caster.name, h.action,
                              h.action_name, now, &fresh);
        count_hits(l, fresh, 1);
        add_target(l, h.target.handle, h.target.side, h.target.seat, h.target.name);
        l->tags |= no_damage;
        touch(l, now);
        return l->id;
    }
    const uint8_t weak = h.weak != 0 ? static_cast<uint8_t>(TAG_WEAK) : 0;
    if (h.pinned != 0 && h.amount_count != 0) {
        // The hit's own numbers: the line now; the victim's diff that follows is absorbed.
        bool heal = true;
        for (uint32_t k = 0; k < h.amount_count && k < AMOUNTS_SHOWN; ++k) {
            heal = heal && h.amounts[k] > 0;
        }
        bool fresh = false;
        line* l = caster_line(m, heal ? LK_HEAL : LK_DAMAGE, h.caster.handle, h.caster.side, h.caster.seat,
                              h.caster.name, h.action, h.action_name, now, &fresh);
        count_hits(l, fresh, 1);
        add_target(l, h.target.handle, h.target.side, h.target.seat, h.target.name);
        for (uint32_t k = 0; k < h.amount_count && k < AMOUNTS_SHOWN; ++k) {
            add_amount(l, h.amounts[k]);
        }
        if (!heal && h.target.side == SIDE_PARTY) {
            l->flags |= LF_PARTY_HIT;
        }
        l->tags |= static_cast<uint8_t>(crit | (heal ? 0 : weak));
        touch(l, now);
        if (tracked* v = track_get(m, h.target.handle)) {
            if (v->side == SIDE_NONE) {
                v->side = h.target.side;
                v->seat = h.target.seat;
                copy_text(v->name, NAME, h.target.name);
            }
            v->absorb = within(now, v->absorb_ms, ABSORB_MS) ? v->absorb + h.total : h.total;
            v->absorb_ms = now;
            if (v->sampled_ms == 0 && v->event_ms == 0) {
                v->sampled_ms = now;   // a unit created only for its absorb is kept until it ages out
            }
        }
        return l->id;
    }
    // No amount (the Forge shows it from its Lua thread): the victim's next HP change is this hit's. Hits of one
    // caster and action on one victim fold into one pending entry.
    pending* slot = nullptr;
    for (pending& p : m->pend) {
        if (p.victim == h.target.handle && p.actor == h.caster.handle && p.action == h.action &&
            within(now, p.ms, PENDING_MS)) {
            if (p.count < 0xFF) {
                ++p.count;
            }
            p.tags |= static_cast<uint8_t>(crit | weak);
            return 0;
        }
    }
    for (pending& p : m->pend) {
        if (p.victim == 0) {
            slot = &p;
            break;
        }
        if (slot == nullptr || p.ms < slot->ms) {
            slot = &p;   // full: the oldest goes
        }
    }
    zero_bytes(slot, sizeof(pending));
    slot->victim = h.target.handle;
    slot->actor = h.caster.handle;
    slot->side = h.caster.side;
    slot->seat = h.caster.seat;
    slot->tags = static_cast<uint8_t>(crit | weak);
    slot->count = 1;
    slot->action = h.action;
    slot->victim_side = h.target.side;
    slot->victim_seat = h.target.seat;
    slot->ms = now != 0 ? now : 1;
    copy_text(slot->name, NAME, h.caster.name);
    copy_text(slot->action_name, ACTION_NAME, h.action_name);
    copy_text(slot->victim_name, NAME, h.target.name);
    return 0;
}

uint32_t model_kill(model* m, const kill& k)
{
    if (k.victim.handle == 0 || k.victim.side == SIDE_PARTY) {
        return 0;   // the reward pop-up only ever names a foe
    }
    line* l = knock_out(m, k.victim.handle, SIDE_FOE, 0, k.victim.name, nullptr, k.time_ms);
    l->exp = k.exp;
    l->lp = static_cast<uint16_t>(k.lp > 0xFFFF ? 0xFFFF : k.lp);
    l->reward |= REWARD_EXP;
    if (k.chain_known != 0) {
        l->chain = k.chain;
        l->chain_level = k.chain_level;
        l->reward |= REWARD_CHAIN;
    }
    touch(l, k.time_ms);
    return l->id;
}

uint32_t model_lines(const model& m, line* out, uint32_t cap)
{
    // The ring holds lines in creation order; born order is the same (a line is born when created).
    uint32_t n = 0;
    const uint32_t first = (m.head + HISTORY - m.count) % HISTORY;
    for (uint32_t k = 0; k < m.count && n < cap; ++k) {
        const line& l = m.lines[(first + k) % HISTORY];
        if (l.id != 0) {
            out[n++] = l;
        }
    }
    return n;
}

}  // namespace ilc::hud::battle_log
