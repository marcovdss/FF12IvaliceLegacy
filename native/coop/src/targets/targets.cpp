// Ivalice Legacy co-op module: target candidates and the target cursor (see targets.h).
#include "targets/targets.h"

#include <math.h>
#include <string.h>

#include "targets/targets_facts.h"

namespace ilc::targets {

namespace {

using menu::TG_COUNT;
using menu::TG_FOES;
using menu::TG_NEUTRALS;
using menu::TG_PARTY;
using menu::TG_RESERVE;

bool attackable(uint8_t type) { return type == facts::UNIT_FOE || type == facts::UNIT_MIMIC || type == facts::UNIT_HUNT; }

bool finite(float v) { return v == v && v < 1.0e30f && v > -1.0e30f; }

// A world actor the picker may offer at all (before range): live, visible, targetable, alive, with a handle.
bool eligible(const actor_snapshot& a)
{
    return a.handle != 0 && a.actor != 0 && (a.flags & SNAP_ACTIVE) && !(a.flags & SNAP_HIDDEN) &&
           (a.flags & SNAP_RECORDS) && !(a.flags & SNAP_BLOCKED) && a.hp != 0 && !(a.status & facts::ST_KO) &&
           (attackable(a.unit_type) || a.unit_type == facts::UNIT_NEUTRAL) && finite(a.x) && finite(a.z);
}

double ground_distance(float ax, float az, float bx, float bz)
{
    const double dx = static_cast<double>(ax) - static_cast<double>(bx);
    const double dz = static_cast<double>(az) - static_cast<double>(bz);
    return sqrt(dx * dx + dz * dz);
}

// The suffix of one eligible foe among all eligible actors: the game's own copy number when it has one, else its
// position among the actors that share its name (only when there are several).
void suffix_of(const picker_input& in, uint32_t k, uint8_t* letter, uint16_t* number)
{
    const actor_snapshot& a = in.actors[k];
    *letter = 0;
    *number = 0;
    if (a.instance >= 1 && a.instance <= 26) {
        *letter = static_cast<uint8_t>('A' + a.instance - 1);
        return;
    }
    if (a.instance > 26 && a.instance != 0xFFFF) {
        *number = a.instance;
        return;
    }
    if (a.name == 0) {
        return;
    }
    uint32_t ordinal = 0;
    uint32_t siblings = 0;
    for (uint32_t j = 0; j < in.actor_count && j < ACTOR_CAP; ++j) {
        if (!eligible(in.actors[j]) || in.actors[j].name != a.name) {
            continue;
        }
        if (j < k) {
            ++ordinal;
        }
        ++siblings;
    }
    if (siblings > 1) {
        if (ordinal < 26) {
            *letter = static_cast<uint8_t>('A' + ordinal);
        } else {
            *number = static_cast<uint16_t>(ordinal + 1);
        }
    }
}

struct list_builder {
    candidate_list* l;
    uint8_t group = TG_COUNT;

    void begin(uint8_t g)
    {
        group = g;
        l->first[g] = l->used;
        l->count[g] = 0;
        l->selectable[g] = 0;
    }
    candidate* add()
    {
        if (l->used >= CANDIDATE_CAP) {
            l->truncated = 1;
            return nullptr;
        }
        candidate* c = &l->c[l->used++];
        memset(c, 0, sizeof(*c));
        c->group = group;
        c->member = MEMBER_NONE;
        ++l->count[group];
        return c;
    }
    void selectable(candidate* c)
    {
        c->flags |= CF_SELECTABLE;
        ++l->selectable[group];
    }
};

// World actors of one group, nearest first (stable: equal distances keep scan order).
void add_world(const picker_input& in, list_builder& b, bool foes)
{
    uint16_t order[ACTOR_CAP];
    double dist[ACTOR_CAP];
    uint32_t n = 0;
    const uint32_t count = in.actor_count < ACTOR_CAP ? in.actor_count : ACTOR_CAP;
    for (uint32_t k = 0; k < count; ++k) {
        const actor_snapshot& a = in.actors[k];
        if (!eligible(a) || attackable(a.unit_type) != foes) {
            continue;
        }
        const double d = ground_distance(a.x, a.z, in.issuer_x, in.issuer_z);
        if (!(d <= static_cast<double>(facts::TARGET_RANGE))) {
            continue;
        }
        uint32_t at = n++;
        while (at > 0 && dist[at - 1] > d) {
            order[at] = order[at - 1];
            dist[at] = dist[at - 1];
            --at;
        }
        order[at] = static_cast<uint16_t>(k);
        dist[at] = d;
    }
    for (uint32_t j = 0; j < n; ++j) {
        const actor_snapshot& a = in.actors[order[j]];
        candidate* c = b.add();
        if (c == nullptr) {
            return;
        }
        c->handle = a.handle;
        c->actor = a.actor;
        c->name = a.name;
        c->status = a.status;
        c->distance = static_cast<float>(dist[j]);
        suffix_of(in, order[j], &c->letter, &c->number);
        b.selectable(c);
    }
}

}  // namespace

uint32_t remedy_mask(uint32_t action)
{
    // Status bits: 1 Stone, 2 Petrify, 3 Stop, 4 Sleep, 5 Confuse, 7 Blind, 8 Poison, 9 Silence, 10 Toad,
    // 11 Oil, 13 Disable, 14 Immobilize, 15 Slow, 16 Disease. Action ids are the game's (items use the action they
    // name; Legacy keeps these rows).
    struct remedy {
        uint16_t action;
        uint32_t mask;
    };
    static constexpr uint32_t ALL = (1u << 1) | (1u << 2) | (1u << 4) | (1u << 5) | (1u << 7) | (1u << 8) |
                                    (1u << 9) | (1u << 10) | (1u << 11) | (1u << 13) | (1u << 14) | (1u << 16);
    static constexpr remedy TABLE[] = {
        {0x01, 1u << 7},                       // Blindna
        {0x02, 1u << 9},                       // Vox
        {0x03, 1u << 8},                       // Poisona
        {0x07, (1u << 1) | (1u << 2)},         // Stona
        {0x09, 1u << 16},                      // Cleanse
        {0x0A, ALL | (1u << 3) | (1u << 15)},  // Esuna
        {0x0C, 1u << 15},                      // Dispel
        {0x59, (1u << 1) | (1u << 2)},         // Gold Needle
        {0x5A, 1u << 9},                       // Echo Herbs
        {0x5B, 1u << 8},                       // Antidote
        {0x5C, 1u << 7},                       // Eye Drops
        {0x5D, 1u << 4},                       // Prince's Kiss
        {0x5E, 1u << 11},                      // Handkerchief
        {0x5F, (1u << 3) | (1u << 15)},        // Chronos Tear
        {0x60, 1u << 5},                       // Nu Khai Sand
        {0x61, 1u << 16},                      // Serum
        {0x62, ALL},                           // Remedy
    };
    for (const remedy& r : TABLE) {
        if (r.action == action) {
            return r.mask;
        }
    }
    return 0;
}

void build_candidates(const picker_input& in, const action_rules& act, candidate_list* out)
{
    memset(out, 0, sizeof(*out));
    list_builder b{out};

    b.begin(TG_FOES);
    add_world(in, b, true);
    b.begin(TG_NEUTRALS);
    add_world(in, b, false);

    b.begin(TG_PARTY);
    const uint32_t party = in.party_count < PARTY_SLOTS ? in.party_count : PARTY_SLOTS;
    uint16_t active[PARTY_SLOTS];
    uint32_t active_count = 0;
    for (uint32_t k = 0; k < party; ++k) {
        const party_member& p = in.party[k];
        if (p.handle == 0 || p.actor == 0) {
            continue;
        }
        candidate* c = b.add();
        if (c == nullptr) {
            break;
        }
        active[active_count++] = p.member;
        c->handle = p.handle;
        c->member = p.member;
        c->actor = p.actor;
        c->status = p.status;
        if (p.handle == in.issuer_handle) {
            c->flags |= CF_ISSUER;
        }
        bool ok = true;
        if (p.present) {
            const bool ko = p.hp == 0 || (p.status & facts::ST_KO);
            const bool stone = (p.status & facts::ST_STONE) != 0;
            if (act.revive) {
                ok = ko;
            } else if (ko && !(act.rules & 0x40)) {
                ok = false;
            }
            if (stone && !(act.rules & 0x80)) {
                ok = false;
            }
        }
        if (ok) {
            b.selectable(c);
        }
    }

    b.begin(TG_RESERVE);
    const uint32_t reserve = in.reserve_count < RESERVE_SLOTS ? in.reserve_count : RESERVE_SLOTS;
    for (uint32_t k = 0; k < reserve; ++k) {
        const uint16_t m = in.reserve[k];
        bool skip = m == MEMBER_NONE || m > 0xFF;
        for (uint32_t j = 0; j < active_count && !skip; ++j) {
            skip = active[j] == m;
        }
        for (uint32_t j = 0; j < k && !skip; ++j) {
            skip = in.reserve[j] == m;
        }
        if (skip) {
            continue;
        }
        candidate* c = b.add();
        if (c == nullptr) {
            break;
        }
        c->member = m;
        b.selectable(c);
    }
    // Groups the action does not offer keep their candidates (the presentation may show them) but are never
    // selectable through the model, which cycles only the action's own groups.
    (void)act.groups;
}

const candidate* selectable_at(const candidate_list& l, uint32_t group, uint32_t k)
{
    if (group >= TG_COUNT) {
        return nullptr;
    }
    uint32_t seen = 0;
    for (uint32_t j = 0; j < l.count[group]; ++j) {
        const candidate& c = l.c[l.first[group] + j];
        if (!(c.flags & CF_SELECTABLE)) {
            continue;
        }
        if (seen++ == k) {
            return &c;
        }
    }
    return nullptr;
}

uint8_t available_groups(const candidate_list& l)
{
    uint8_t mask = 0;
    for (uint32_t g = 0; g < TG_COUNT; ++g) {
        if (l.selectable[g]) {
            mask |= static_cast<uint8_t>(1u << g);
        }
    }
    return mask;
}

void cursor_reset(target_cursor* c)
{
    memset(c, 0, sizeof(*c));
    c->action = 0xFFFF;
    c->group = TG_COUNT;
    c->member = MEMBER_NONE;
}

namespace {

bool same_identity(const candidate& c, const target_cursor& cur)
{
    if (c.group == TG_RESERVE) {
        return cur.handle == 0 && cur.member == c.member;
    }
    return cur.handle != 0 && c.handle == cur.handle;
}

void remember(target_cursor* cur, const candidate* c)
{
    if (c == nullptr) {
        cur->handle = 0;
        cur->member = MEMBER_NONE;
        cur->issuer_locked = 0;
        return;
    }
    cur->handle = c->group == TG_RESERVE ? 0 : c->handle;
    cur->member = c->member;
}

}  // namespace

int32_t sync(menu::menu_model* m, target_cursor* cur, const candidate_list& l)
{
    if (m->mode != menu::MODE_TARGET || m->pending.kind != menu::EK_ACTION) {
        cursor_reset(cur);
        return SYNC_IDLE;
    }
    const uint8_t available = static_cast<uint8_t>(available_groups(l) & m->pending.groups);
    if (m->target_group >= TG_COUNT || !(available & (1u << m->target_group))) {
        // The current group has nothing selectable: move to the next one that has (the action's own order).
        if (available != 0) {
            menu::model_cycle_group(m, 1, available);
        }
    }
    if (cur->action != m->pending.action || cur->group != m->target_group) {
        cursor_reset(cur);
        cur->action = m->pending.action;
        cur->group = m->target_group;
        cur->fresh = 1;
    }
    const uint32_t group = m->target_group;
    const uint32_t n = group < TG_COUNT ? l.selectable[group] : 0;
    menu::model_set_target_count(m, static_cast<uint16_t>(n));
    if (n == 0) {
        remember(cur, nullptr);
        return available == 0 ? SYNC_EMPTY : SYNC_OK;
    }
    int32_t pick = -1;
    int32_t issuer = -1;
    int32_t kept = -1;
    int32_t afflicted = -1;
    const uint32_t remedy = remedy_mask(m->pending.action);
    for (uint32_t k = 0; k < n; ++k) {
        const candidate* c = selectable_at(l, group, k);
        if (issuer < 0 && (c->flags & CF_ISSUER)) {
            issuer = static_cast<int32_t>(k);
        }
        if (kept < 0 && same_identity(*c, *cur)) {
            kept = static_cast<int32_t>(k);
        }
        if (afflicted < 0 && remedy != 0 && (c->status & remedy)) {
            afflicted = static_cast<int32_t>(k);
        }
    }
    const bool party = group == TG_PARTY;
    if (party && cur->issuer_locked && issuer >= 0) {
        pick = issuer;
    } else if (party && cur->fresh) {
        if (afflicted >= 0) {
            pick = afflicted;
            cur->issuer_locked = 0;
        } else if (issuer >= 0) {
            pick = issuer;
            cur->issuer_locked = 1;
        }
    }
    if (pick < 0 && kept >= 0 && !cur->fresh) {
        pick = kept;
        cur->issuer_locked = party && kept == issuer ? 1 : 0;
    }
    if (pick < 0) {
        pick = static_cast<uint32_t>(m->target_index) < n ? static_cast<int32_t>(m->target_index)
                                                          : static_cast<int32_t>(n - 1);
        if (cur->fresh) {
            pick = 0;
        }
        cur->issuer_locked = party && pick == issuer ? 1 : 0;
    }
    cur->fresh = 0;
    menu::model_set_target_index(m, static_cast<uint16_t>(pick));
    remember(cur, selectable_at(l, group, static_cast<uint32_t>(pick)));
    return SYNC_OK;
}

void note_selection(const menu::menu_model& m, target_cursor* cur, const candidate_list& l)
{
    if (m.mode != menu::MODE_TARGET) {
        return;
    }
    if (cur->action != m.pending.action || cur->group != m.target_group) {
        return;  // a new group or action: the next sync starts fresh
    }
    const candidate* c = selectable_at(l, m.target_group, m.target_index);
    remember(cur, c);
    cur->issuer_locked = c != nullptr && m.target_group == TG_PARTY && (c->flags & CF_ISSUER) ? 1 : 0;
    cur->fresh = 0;
}

}  // namespace ilc::targets
