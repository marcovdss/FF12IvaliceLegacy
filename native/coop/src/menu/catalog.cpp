// Ivalice Legacy co-op module: building a guest's battle-menu catalog (see catalog.h for the rules).
#include "menu/catalog.h"

#include <string.h>

#include "menu/cost.h"
#include "menu/menu_facts.h"

namespace ilc::menu {

namespace {

bool bit(const uint8_t* bytes, uint32_t size, uint32_t index)
{
    return index < size * 8 && (bytes[index >> 3] >> (index & 7)) & 1u;
}


// The game's live magick licence summary: flag f stands for licence node f + 176, except flags 32..42, which name
// the nodes of the higher spells in their own order.
constexpr uint32_t SUMMARY_NODE_SHIFT = 176;
constexpr uint16_t SUMMARY_SPECIAL_FIRST = 32;
constexpr uint16_t SUMMARY_SPECIAL[] = {211, 209, 210, 208, 212, 213, 214, 216, 215, 217, 218};  // flags 32..42

int32_t summary_node(uint32_t flag)
{
    if ((flag >= 6 && flag <= 31) || (flag >= 179 && flag <= 183)) {
        return static_cast<int32_t>(flag + SUMMARY_NODE_SHIFT);
    }
    const uint32_t k = flag - SUMMARY_SPECIAL_FIRST;
    if (flag >= SUMMARY_SPECIAL_FIRST && k < sizeof(SUMMARY_SPECIAL) / sizeof(SUMMARY_SPECIAL[0])) {
        return SUMMARY_SPECIAL[k];
    }
    return -1;
}

// The three Quickenings of each regular character (first id; the other two follow it).
constexpr uint16_t QUICKENING_BASE[6] = {0xD0, 0xDC, 0xD3, 0xD6, 0xD9, 0xDF};

bool node_lists(const catalog_tables& t, uint32_t node, uint32_t action)
{
    const licence_node& n = t.licences[node];
    for (uint32_t k = 0; k < n.count; ++k) {
        if (n.action[k] == action) {
            return true;
        }
    }
    return false;
}

// Licensed through any acquired licence node (technicks, and magicks without a summary).
bool licensed_by_nodes(const catalog_tables& t, const seat_state& s, uint32_t action)
{
    for (uint32_t node = 0; node < t.licence_count; ++node) {
        if (bit(s.licences, sizeof(s.licences), node) && node_lists(t, node, action)) {
            return true;
        }
    }
    return false;
}

// Licensed through the live magick licence summary.
bool licensed_by_summary(const catalog_tables& t, const seat_state& s, uint32_t action)
{
    for (uint32_t flag = 0; flag < sizeof(s.magick_licences) * 8; ++flag) {
        if (!bit(s.magick_licences, sizeof(s.magick_licences), flag)) {
            continue;
        }
        const int32_t node = summary_node(flag);
        if (node >= 0 && static_cast<uint32_t>(node) < t.licence_count && node_lists(t, node, action)) {
            return true;
        }
    }
    return false;
}

bool needs_no_licence(const catalog_tables& t, uint32_t action)
{
    const action_rule* r = rule_of(t, action);
    return r != nullptr && (r->rules & 0x2000);
}

bool is_quickening(uint32_t action) { return action >= facts::QUICKENING_FIRST && action <= facts::QUICKENING_LAST; }

bool fixed_lists(const catalog_tables& t, uint32_t member, uint16_t content)
{
    for (uint32_t k = 0; k < FIXED_SLOTS; ++k) {
        if (t.fixed[member][k] == content) {
            return true;
        }
    }
    return false;
}

// Content rows that pass `keep`, ordered by (sort key, index).
template <typename Keep>
uint32_t ordered_rows(const content_row* rows, uint32_t count, uint8_t* out, Keep keep)
{
    uint32_t n = 0;
    for (uint32_t k = 0; k < count; ++k) {
        if (!keep(k)) {
            continue;
        }
        uint32_t at = n++;
        while (at > 0 && rows[out[at - 1]].sort > rows[k].sort) {
            out[at] = out[at - 1];
            --at;
        }
        out[at] = static_cast<uint8_t>(k);
    }
    return n;
}

struct builder {
    catalog* c;
    uint8_t current = PG_ROOT;
    bool full = false;
    entry scratch{};

    void begin(uint8_t page)
    {
        current = page;
        c->pages[page] = {c->used, 0, 1, {}};
    }
    entry* add(uint8_t kind, uint8_t role)
    {
        if (c->used >= ENTRY_CAP) {
            full = true;
            scratch = {};
            return &scratch;
        }
        entry* e = &c->entries[c->used++];
        *e = {};
        e->kind = kind;
        e->role = role;
        e->page = PG_NONE;
        e->flags = EF_ENABLED;
        ++c->pages[current].count;
        return e;
    }
    entry* add_page_row(uint8_t role, uint8_t target, bool enabled)
    {
        entry* e = add(EK_PAGE, role);
        e->page = target;
        e->flags = enabled ? EF_ENABLED : 0;
        return e;
    }
    void label_if_empty()
    {
        if (c->pages[current].count == 0) {
            entry* e = add(EK_LABEL, ROLE_EMPTY);
            e->flags = 0;
        }
    }
    entry* page_row(uint8_t page, uint8_t target)
    {
        for (uint32_t k = 0; k < c->pages[page].count; ++k) {
            entry& e = c->entries[c->pages[page].first + k];
            if (e.kind == EK_PAGE && e.page == target) {
                return &e;
            }
        }
        return nullptr;
    }
};

// The action part of an entry, from the action table.
void describe(const catalog_tables& t, entry* e, uint32_t action, uint32_t display)
{
    e->action = static_cast<uint16_t>(action);
    e->display = static_cast<uint16_t>(display);
    e->default_group = TG_PARTY;
    const action_rule* r = rule_of(t, action);
    if (r == nullptr) {
        e->cost = -1;
        e->flags = static_cast<uint8_t>(e->flags & ~EF_ENABLED);
        return;
    }
    e->cost = r->cost;
    e->area = is_quickening(action) ? 0 : r->area;
    const bool self = r->targeting & 0x01;
    const bool ally = r->targeting & 0x04;
    const bool foe = r->targeting & 0x08;
    const uint32_t initial = (r->targeting >> 5) & 0x07;
    const bool party_first = initial == 1 || initial == 2;
    if (self) {
        e->groups = 1u << TG_PARTY;
        e->flags |= EF_SELF_ONLY;
    } else {
        if (ally || (r->rules & 0x40) || (r->rules & 0x80)) {
            e->groups |= (1u << TG_PARTY) | (1u << TG_NEUTRALS);
        }
        if (foe) {
            e->groups |= 1u << TG_FOES;
        }
        if (r->target_flags & 0x20) {
            e->groups |= 1u << TG_RESERVE;
        }
    }
    if (party_first) {
        e->flags |= EF_PARTY_FIRST;
    }
    for (uint16_t id : facts::REVIVAL_ACTIONS) {
        if (id == action) {
            e->flags |= EF_REVIVE;
        }
    }
    // Default group: Foes for initial target 4, Party for 1/2, else the first offered group.
    static constexpr uint8_t FOES_FIRST_ORDER[TG_COUNT] = {TG_FOES, TG_NEUTRALS, TG_PARTY, TG_RESERVE};
    static constexpr uint8_t PARTY_FIRST_ORDER[TG_COUNT] = {TG_PARTY, TG_NEUTRALS, TG_FOES, TG_RESERVE};
    if (initial == 4 && (e->groups & (1u << TG_FOES))) {
        e->default_group = TG_FOES;
    } else if (party_first && (e->groups & (1u << TG_PARTY))) {
        e->default_group = TG_PARTY;
    } else {
        const uint8_t* order = party_first ? PARTY_FIRST_ORDER : FOES_FIRST_ORDER;
        for (uint32_t k = 0; k < TG_COUNT; ++k) {
            if (e->groups & (1u << order[k])) {
                e->default_group = order[k];
                break;
            }
        }
    }
}

void disable(entry& e, uint8_t why)
{
    if (e.flags & EF_ENABLED) {
        e.flags = static_cast<uint8_t>((e.flags & ~EF_ENABLED) | why);
    }
}

bool page_has_enabled(const catalog& c, uint32_t page)
{
    for (uint32_t k = 0; k < page_count(c, page); ++k) {
        const entry& e = c.entries[c.pages[page].first + k];
        if (e.kind != EK_LABEL && (e.flags & EF_ENABLED)) {
            return true;
        }
    }
    return false;
}

void restrict_tree(catalog* c, const shared_state& sh, const seat_state& s)
{
    const bool everything = s.status & facts::ST_NO_COMMAND;
    const bool berserk = s.status & facts::ST_BERSERK;
    const bool non_attack = everything || berserk;
    const bool silenced = s.status & facts::ST_SILENCE;
    const bool magicks_blocked = non_attack || silenced || (sh.seals & facts::SEAL_MAGICK);

    bool blocked[PG_COUNT] = {};
    if (non_attack) {
        blocked[PG_TECHNICKS] = blocked[PG_QUICKENINGS] = blocked[PG_SUMMONS] = true;
    } else if (sh.seals & facts::SEAL_TECHNICK) {
        blocked[PG_TECHNICKS] = true;
    }
    if (magicks_blocked) {
        for (uint32_t k = 0; k < SCHOOLS; ++k) {
            blocked[PG_WHITE + k] = true;
        }
    }
    if (non_attack || (sh.seals & facts::SEAL_ITEM)) {
        blocked[PG_ITEMS] = true;
    }
    for (uint32_t page = 0; page < PG_COUNT; ++page) {
        if (!blocked[page]) {
            continue;
        }
        for (uint32_t k = 0; k < page_count(*c, page); ++k) {
            disable(c->entries[c->pages[page].first + k], EF_SEALED);
        }
    }
    static constexpr uint8_t PARENTS[] = {PG_MAGICKS, PG_MIST};
    for (uint32_t page : PARENTS) {
        for (uint32_t k = 0; k < page_count(*c, page); ++k) {
            entry& e = c->entries[c->pages[page].first + k];
            if (e.kind == EK_PAGE && e.page < PG_COUNT && blocked[e.page]) {
                disable(e, EF_SEALED);
            }
        }
    }
    for (uint32_t k = 0; k < page_count(*c, PG_ROOT); ++k) {
        entry& e = c->entries[c->pages[PG_ROOT].first + k];
        bool keep = (e.flags & EF_ENABLED) != 0;
        if (everything) {
            keep = false;
        } else if (berserk) {
            keep = e.role == ROLE_ATTACK && !(sh.seals & facts::SEAL_ATTACK);
            if (keep) {
                e.flags |= EF_ENABLED;
            }
        } else if (e.role == ROLE_ATTACK) {
            keep = keep && !(sh.seals & facts::SEAL_ATTACK);
        } else if (e.role == ROLE_MAGICKS) {
            if (silenced || (sh.seals & (facts::SEAL_MAGICK | facts::SEAL_TECHNICK))) {
                keep = keep && page_has_enabled(*c, PG_MAGICKS);
            }
        } else if (e.role == ROLE_ITEMS) {
            keep = keep && !blocked[PG_ITEMS];
        }
        if (!keep) {
            disable(e, EF_SEALED);
        }
    }
}

uint64_t fnv(uint64_t h, const void* data, size_t n)
{
    const uint8_t* p = static_cast<const uint8_t*>(data);
    for (size_t k = 0; k < n; ++k) {
        h ^= p[k];
        h *= 0x100000001B3ull;  // NOTADDR: FNV-1a prime
    }
    return h;
}

}  // namespace

uint64_t catalog_signature(const catalog_tables& t, const shared_state& sh, const seat_state& s)
{
    uint64_t h = 0xCBF29CE484222325ull;  // NOTADDR: FNV-1a offset basis
    h = fnv(h, &t.signature, sizeof(t.signature));
    h = fnv(h, &sh, sizeof(sh));
    h = fnv(h, &s, sizeof(s));
    const economy_params economy = current_economy();
    h = fnv(h, &economy, sizeof(economy));
    return h;
}

int32_t build_catalog(const catalog_tables& t, const shared_state& sh, const seat_state& s, catalog* out)
{
    memset(out, 0, sizeof(*out));
    if (!t.ready) {
        return BUILD_NO_TABLES;
    }
    const uint32_t member = s.member;
    const bool fixed = facts::fixed_list_member(member);
    if (!fixed && !facts::licence_member(member)) {
        return BUILD_NO_MEMBER;
    }
    if (fixed && !t.fixed_known) {
        return BUILD_NO_FIXED;
    }
    out->member = static_cast<uint16_t>(member);
    builder b{out};
    uint8_t order[ITEM_CAP > MAGICK_CAP ? ITEM_CAP : MAGICK_CAP];

    // Items: held, by sort key, then in the player's saved order when it names exactly the held items.
    uint32_t held = ordered_rows(t.items, t.item_count, order,
                                 [&](uint32_t k) { return (sh.item_counts[k] & facts::COUNT_OWNED_MASK) != 0; });
    if (sh.order_known && sh.order_count <= ITEM_CAP) {
        uint8_t saved[ITEM_CAP];
        uint32_t n = 0;
        bool seen[ITEM_CAP] = {};
        bool ok = true;
        for (uint32_t k = 0; k < sh.order_count && ok; ++k) {
            const uint16_t item = sh.item_order[k];
            bool is_held = false;
            for (uint32_t j = 0; j < held; ++j) {
                is_held = is_held || order[j] == item;
            }
            if (!is_held) {
                continue;
            }
            ok = !seen[item];
            seen[item] = true;
            saved[n++] = static_cast<uint8_t>(item);
        }
        if (ok && n == held) {
            memcpy(order, saved, n);
        }
    }
    b.begin(PG_ITEMS);
    for (uint32_t k = 0; k < held; ++k) {
        const content_row& row = t.items[order[k]];
        entry* e = b.add(EK_ACTION, ROLE_ACTION);
        describe(t, e, row.action, row.display);
        const uint16_t w = sh.item_counts[order[k]];
        const int32_t available = static_cast<int32_t>(w & facts::COUNT_OWNED_MASK) -
                                  static_cast<int32_t>(w >> facts::COUNT_HELD_SHIFT);
        e->quantity = static_cast<uint16_t>(available > 0 ? available : 0);  // what is left to use
        if (available <= 0) {
            disable(*e, EF_HELD_BACK);
        }
    }
    const bool items_held = held != 0;
    b.label_if_empty();

    // Magick schools.
    bool school_open[SCHOOLS] = {};
    for (uint32_t school = 0; school < SCHOOLS; ++school) {
        const uint32_t n = ordered_rows(t.magicks, t.magick_count, order, [&](uint32_t k) {
            const action_rule* r = rule_of(t, t.magicks[k].action);
            if (r == nullptr || (r->rules & 0x07) != school) {
                return false;
            }
            if (fixed) {
                return fixed_lists(t, member, static_cast<uint16_t>(facts::CONTENT_MAGICK + k));
            }
            return bit(sh.magick_owned, sizeof(sh.magick_owned), k) &&
                   (licensed_by_summary(t, s, t.magicks[k].action) || needs_no_licence(t, t.magicks[k].action));
        });
        b.begin(static_cast<uint8_t>(PG_WHITE + school));
        for (uint32_t k = 0; k < n; ++k) {
            entry* e = b.add(EK_ACTION, ROLE_ACTION);
            const uint32_t action = t.magicks[order[k]].action;
            describe(t, e, action, action);
        }
        school_open[school] = n != 0;
        b.label_if_empty();
    }

    // Technicks.
    const uint32_t technicks = ordered_rows(t.technicks, t.technick_count, order, [&](uint32_t k) {
        const uint32_t action = t.technicks[k].action;
        if (fixed) {
            return fixed_lists(t, member, static_cast<uint16_t>(facts::CONTENT_TECHNICK + k));
        }
        return bit(sh.technick_owned, sizeof(sh.technick_owned), k) &&
               (licensed_by_nodes(t, s, action) || needs_no_licence(t, action));
    });
    b.begin(PG_TECHNICKS);
    for (uint32_t k = 0; k < technicks; ++k) {
        const uint32_t action = t.technicks[order[k]].action;
        describe(t, b.add(EK_ACTION, ROLE_ACTION), action, action);
    }
    b.label_if_empty();

    b.begin(PG_MAGICKS);
    for (uint32_t school = 0; school < SCHOOLS; ++school) {
        b.add_page_row(ROLE_SCHOOL, static_cast<uint8_t>(PG_WHITE + school), school_open[school]);
    }
    b.add_page_row(ROLE_TECHNICKS, PG_TECHNICKS, technicks != 0);

    // Mist: Quickenings and Espers (regular characters only).
    uint16_t quickenings[3];
    uint32_t quickening_count = 0;
    uint16_t espers[13];
    uint32_t esper_count = 0;
    if (!fixed && member < 6) {
        const uint16_t first = QUICKENING_BASE[member];
        for (uint32_t k = 0; k < 3; ++k) {
            if (bit(sh.mist_owned, sizeof(sh.mist_owned), first + k - facts::QUICKENING_FIRST)) {
                quickenings[quickening_count++] = static_cast<uint16_t>(first + k);
            }
        }
        uint32_t licensed = 0;
        for (uint32_t node = 0; node < t.licence_count; ++node) {
            if (t.licences[node].type == facts::LIC_TYPE_QUICKENING && bit(s.licences, sizeof(s.licences), node)) {
                ++licensed;
            }
        }
        if (licensed > 3) {
            licensed = 3;
        }
        if (licensed > quickening_count) {
            quickening_count = licensed;
            for (uint32_t k = 0; k < licensed; ++k) {
                quickenings[k] = static_cast<uint16_t>(first + k);
            }
        }
        for (uint32_t b18 = facts::ESPER_BIT_FIRST; b18 <= facts::ESPER_BIT_LAST; ++b18) {
            if (bit(sh.mist_owned, sizeof(sh.mist_owned), b18) && bit(s.licences, sizeof(s.licences), b18)) {
                espers[esper_count++] = static_cast<uint16_t>(facts::ESPER_FIRST + b18 - facts::ESPER_BIT_FIRST);
            }
        }
    }
    if (quickening_count) {
        b.begin(PG_QUICKENINGS);
        for (uint32_t k = 0; k < quickening_count; ++k) {
            describe(t, b.add(EK_ACTION, ROLE_ACTION), quickenings[k], quickenings[k]);
        }
    }
    if (esper_count) {
        b.begin(PG_SUMMONS);
        for (uint32_t k = 0; k < esper_count; ++k) {
            describe(t, b.add(EK_ACTION, ROLE_ACTION), espers[k], espers[k]);
        }
    }
    const bool mist_rows = quickening_count || esper_count;
    const bool mist_open = mist_rows || (sh.menu_flags & ((1u << 7) | (1u << 8)));
    if (mist_open) {
        b.begin(PG_MIST);
        if (quickening_count) {
            b.add_page_row(ROLE_QUICKENING, PG_QUICKENINGS, true);
        }
        if (esper_count) {
            b.add_page_row(ROLE_SUMMON, PG_SUMMONS, true);
        }
    }

    // Root.
    b.begin(PG_ROOT);
    describe(t, b.add(EK_ACTION, ROLE_ATTACK), facts::ACTION_ATTACK, facts::ACTION_ATTACK);
    bool any_fixed = false;
    if (fixed) {
        for (uint32_t k = 0; k < FIXED_SLOTS; ++k) {
            const uint16_t content = t.fixed[member][k];
            const uint16_t kind = content & 0xF000;
            const uint32_t index = content & 0x0FFF;
            any_fixed = any_fixed || (kind == facts::CONTENT_MAGICK && index < t.magick_count) ||
                        (kind == facts::CONTENT_TECHNICK && index < t.technick_count);
        }
    }
    if ((sh.menu_flags & 0x01) || any_fixed) {
        b.add_page_row(ROLE_MAGICKS, PG_MAGICKS, true);
    }
    if (s.esper_owner) {
        entry* e = b.add(EK_ACTION, ROLE_DISMISS);
        describe(t, e, facts::ACTION_DISMISS, facts::ACTION_DISMISS);
        e->groups = 0;
        e->default_group = TG_PARTY;
        e->flags = static_cast<uint8_t>((e->flags | EF_SELF_ONLY | EF_ENABLED) & ~EF_PARTY_FIRST);
    } else if (mist_open) {
        b.add_page_row(ROLE_MIST, PG_MIST, mist_rows);
    }
    b.add(EK_TOGGLE, ROLE_GAMBITS);
    b.add_page_row(ROLE_ITEMS, PG_ITEMS, items_held);

    if (b.full) {
        memset(out, 0, sizeof(*out));
        return BUILD_FULL;
    }
    // Every action is priced and greyed as the game's admission check would (MP, Mist charges, gil), with Legacy's
    // surcharge in the shown MP. Dismiss is not admitted through that check.
    const economy_params economy = current_economy();
    for (uint32_t k = 0; k < out->used; ++k) {
        entry& e = out->entries[k];
        if (e.kind == EK_ACTION && e.role != ROLE_DISMISS) {
            price_entry(t, sh, s, economy, &e);
        }
    }
    restrict_tree(out, sh, s);
    out->signature = catalog_signature(t, sh, s);
    out->valid = 1;
    return BUILD_OK;
}

}  // namespace ilc::menu
