// Ivalice Legacy co-op module: the guest battle-menu model (see model.h).
#include "menu/model.h"

#include <string.h>

namespace ilc::menu {

namespace {

menu_event ev(const menu_model& m, uint8_t kind)
{
    menu_event e{};
    e.kind = kind;
    e.page = m.depth ? m.stack[m.depth - 1].page : static_cast<uint8_t>(PG_NONE);
    e.index = m.mode == MODE_TARGET ? m.target_index : (m.depth ? m.stack[m.depth - 1].index : 0);
    e.group = TG_COUNT;
    return e;
}

const entry* row(const catalog& c, uint32_t page, uint32_t index)
{
    return index < page_count(c, page) ? &c.entries[c.pages[page].first + index] : nullptr;
}

// What a row is, across rebuilds.
bool same_row(const entry& a, const entry& b)
{
    if (a.kind != b.kind) {
        return false;
    }
    switch (a.kind) {
    case EK_ACTION:
        return a.action == b.action && a.display == b.display && a.role == b.role;
    case EK_PAGE:
        return a.page == b.page;
    default:
        return a.role == b.role;  // the Gambits toggle, the empty label
    }
}

uint16_t reconciled_index(const catalog& c, uint32_t page, const entry* identity, uint32_t old_index)
{
    const uint32_t count = page_count(c, page);
    if (count == 0) {
        return 0;
    }
    if (identity != nullptr) {
        for (uint32_t k = 0; k < count; ++k) {
            if (same_row(*row(c, page, k), *identity)) {
                return static_cast<uint16_t>(k);
            }
        }
    }
    const uint32_t start = old_index < count ? old_index : count - 1;
    for (uint32_t k = 0; k < count; ++k) {
        const uint32_t at = (start + k) % count;
        if (row(c, page, at)->kind != EK_LABEL) {
            return static_cast<uint16_t>(at);
        }
    }
    return static_cast<uint16_t>(start);
}

bool links(const catalog& c, uint32_t parent, uint32_t child)
{
    for (uint32_t k = 0; k < page_count(c, parent); ++k) {
        const entry* e = row(c, parent, k);
        if (e->kind == EK_PAGE && e->page == child) {
            return true;
        }
    }
    return false;
}

const entry* find_enabled_action(const catalog& c, const entry& pending)
{
    for (uint32_t page = 0; page < PG_COUNT; ++page) {
        for (uint32_t k = 0; k < page_count(c, page); ++k) {
            const entry* e = row(c, page, k);
            if (e->kind == EK_ACTION && e->action == pending.action && e->display == pending.display &&
                e->role == pending.role && (e->flags & EF_ENABLED)) {
                return e;
            }
        }
    }
    return nullptr;
}

bool same_catalog(const catalog& a, const catalog& b)
{
    if (a.valid != b.valid || a.member != b.member || a.used != b.used ||
        memcmp(a.pages, b.pages, sizeof(a.pages)) != 0) {
        return false;
    }
    return memcmp(a.entries, b.entries, sizeof(entry) * a.used) == 0;
}

// Target groups of an action in the order it offers them.
uint32_t group_order(const entry& e, uint8_t* out)
{
    static constexpr uint8_t FOES_FIRST[TG_COUNT] = {TG_FOES, TG_NEUTRALS, TG_PARTY, TG_RESERVE};
    static constexpr uint8_t PARTY_FIRST[TG_COUNT] = {TG_PARTY, TG_NEUTRALS, TG_FOES, TG_RESERVE};
    const uint8_t* order = (e.flags & EF_PARTY_FIRST) ? PARTY_FIRST : FOES_FIRST;
    uint32_t n = 0;
    for (uint32_t k = 0; k < TG_COUNT; ++k) {
        if (e.groups & (1u << order[k])) {
            out[n++] = order[k];
        }
    }
    return n;
}

void clear_target(menu_model* m)
{
    m->pending = {};
    m->target_index = 0;
    m->target_count = 0;
}

}  // namespace

void model_reset(menu_model* m) { memset(m, 0, sizeof(*m)); }

const entry* model_selected(const menu_model& m)
{
    if (m.mode != MODE_LIST || m.depth == 0) {
        return nullptr;
    }
    const frame& f = m.stack[m.depth - 1];
    return row(m.pages, f.page, f.index);
}

bool catalog_is_valid(const catalog& c)
{
    if (!c.valid || !c.pages[PG_ROOT].present || c.used > ENTRY_CAP) {
        return false;
    }
    for (uint32_t page = 0; page < PG_COUNT; ++page) {
        const page_slot& p = c.pages[page];
        if (!p.present) {
            continue;
        }
        if (static_cast<uint32_t>(p.first) + p.count > c.used) {
            return false;
        }
        for (uint32_t k = 0; k < p.count; ++k) {
            const entry& e = c.entries[p.first + k];
            if (e.kind == EK_PAGE && (e.page >= PG_COUNT || !c.pages[e.page].present)) {
                return false;
            }
            if (e.kind > EK_TOGGLE) {
                return false;
            }
        }
    }
    return true;
}

int32_t model_commit(menu_model* m, const catalog& c, commit_result* result)
{
    commit_result r{};
    if (!catalog_is_valid(c)) {
        if (result) {
            *result = r;
        }
        return -1;
    }
    const bool was_target = m->mode == MODE_TARGET;
    entry identities[MAX_DEPTH];
    bool known[MAX_DEPTH] = {};
    for (uint32_t p = 0; p < m->depth; ++p) {
        const uint32_t count = page_count(m->pages, m->stack[p].page);
        if (count) {
            const uint32_t at = m->stack[p].index < count ? m->stack[p].index : count - 1;
            identities[p] = *row(m->pages, m->stack[p].page, at);
            known[p] = true;
        }
    }
    uint32_t kept = 0;
    uint16_t indices[MAX_DEPTH] = {};
    for (uint32_t p = 0; p < m->depth; ++p) {
        const uint32_t page = m->stack[p].page;
        if (page >= PG_COUNT || !c.pages[page].present) {
            break;
        }
        if (p > 0 && !links(c, m->stack[p - 1].page, page)) {
            break;
        }
        indices[p] = reconciled_index(c, page, known[p] ? &identities[p] : nullptr, m->stack[p].index);
        ++kept;
    }
    const entry* replacement = nullptr;
    bool target_kept = false;
    bool target_cancelled = false;
    if (m->mode == MODE_TARGET) {
        replacement = find_enabled_action(c, m->pending);
        target_kept = replacement != nullptr;
        target_cancelled = !target_kept;
    }
    const entry pending_new = replacement ? *replacement : entry{};
    r.changed = !same_catalog(m->pages, c);
    if (&m->pages != &c) {
        memcpy(&m->pages, &c, sizeof(c));
    }
    if (m->mode != MODE_CLOSED) {
        m->depth = static_cast<uint8_t>(kept);
        for (uint32_t p = 0; p < kept; ++p) {
            m->stack[p].index = indices[p];
        }
        if (m->depth == 0) {
            model_close(m);
            target_cancelled = target_cancelled || was_target;
            target_kept = false;
        }
    }
    if (target_kept) {
        m->pending = pending_new;
        if (!(m->pending.groups & (1u << m->target_group))) {
            m->target_group = m->pending.default_group;
            m->target_index = 0;
        }
    } else if (target_cancelled && m->mode == MODE_TARGET) {
        m->mode = MODE_LIST;
        clear_target(m);
    }
    r.depth = m->depth;
    r.target_kept = target_kept;
    r.target_cancelled = target_cancelled;
    if (result) {
        *result = r;
    }
    return 0;
}

menu_event model_open(menu_model* m)
{
    if (!catalog_is_valid(m->pages)) {
        return ev(*m, EV_IGNORED);
    }
    m->mode = MODE_LIST;
    m->depth = 1;
    m->stack[0] = {PG_ROOT, 0, 0};
    m->target_group = TG_FOES;
    clear_target(m);
    return ev(*m, EV_OPENED);
}

menu_event model_close(menu_model* m)
{
    m->mode = MODE_CLOSED;
    m->depth = 0;
    clear_target(m);
    return ev(*m, EV_CLOSED);
}

menu_event model_toggle(menu_model* m) { return m->mode == MODE_CLOSED ? model_open(m) : model_close(m); }

menu_event model_navigate(menu_model* m, int32_t delta, bool wrap)
{
    if (m->mode == MODE_TARGET) {
        const int32_t count = m->target_count;
        if (count <= 1 || delta == 0) {
            return ev(*m, EV_IGNORED);
        }
        const int32_t was = m->target_index;
        int32_t next = was + delta;
        if (wrap) {
            next = ((next % count) + count) % count;
        } else {
            next = next < 0 ? 0 : (next >= count ? count - 1 : next);
        }
        if (next == was) {
            return ev(*m, EV_IGNORED);
        }
        m->target_index = static_cast<uint16_t>(next);
        return ev(*m, EV_TARGET_MOVED);
    }
    if (m->mode != MODE_LIST || m->depth == 0 || delta == 0) {
        return ev(*m, EV_IGNORED);
    }
    frame& f = m->stack[m->depth - 1];
    const int32_t count = static_cast<int32_t>(page_count(m->pages, f.page));
    if (count == 0) {
        return ev(*m, EV_IGNORED);
    }
    int32_t next = f.index;
    if (wrap) {
        for (int32_t k = 0; k < count; ++k) {
            next = (((next + delta) % count) + count) % count;
            if (row(m->pages, f.page, next)->kind != EK_LABEL) {
                break;
            }
        }
    } else {
        const int32_t step = delta < 0 ? -1 : 1;
        next += step;
        while (next >= 0 && next < count && row(m->pages, f.page, next)->kind == EK_LABEL) {
            next += step;
        }
        if (next < 0 || next >= count) {
            return ev(*m, EV_IGNORED);
        }
    }
    if (next == f.index) {
        return ev(*m, EV_IGNORED);
    }
    f.index = static_cast<uint16_t>(next);
    return ev(*m, EV_CURSOR);
}

menu_event model_navigate_page(menu_model* m, int32_t direction, uint32_t page_size)
{
    if (m->mode != MODE_LIST || m->depth == 0) {
        return ev(*m, EV_IGNORED);
    }
    frame& f = m->stack[m->depth - 1];
    const int32_t count = static_cast<int32_t>(page_count(m->pages, f.page));
    if (count == 0) {
        return ev(*m, EV_IGNORED);
    }
    const int32_t size = page_size == 0 ? 1 : static_cast<int32_t>(page_size > 4096 ? 4096 : page_size);
    const int32_t step = direction < 0 ? -size : size;
    const int32_t last = count - 1;
    auto clamp = [&](int32_t v) { return v < 0 ? 0 : (v > last ? last : v); };
    auto label = [&](int32_t at) { return row(m->pages, f.page, at)->kind == EK_LABEL; };
    int32_t at = clamp(f.index + step);
    if (at == f.index) {
        return ev(*m, EV_IGNORED);
    }
    const int32_t search = step < 0 ? -1 : 1;
    while (at >= 0 && at <= last && label(at)) {
        at += search;
    }
    if (at < 0 || at > last) {
        at = clamp(f.index + step);
        while (at >= 0 && at <= last && label(at)) {
            at -= search;
        }
    }
    if (at < 0 || at > last || at == f.index) {
        return ev(*m, EV_IGNORED);
    }
    f.index = static_cast<uint16_t>(at);
    return ev(*m, EV_CURSOR);
}

menu_event model_confirm(menu_model* m)
{
    if (m->mode == MODE_TARGET) {
        if (m->pending.kind != EK_ACTION || m->target_count == 0) {
            return ev(*m, EV_INVALID_TARGET);
        }
        menu_event e = ev(*m, EV_SUBMIT);
        e.action = m->pending.action;
        e.group = m->target_group;
        e.index = static_cast<uint16_t>(m->target_index % m->target_count);
        model_close(m);
        e.page = PG_NONE;
        return e;
    }
    const entry* sel = model_selected(*m);
    if (sel == nullptr || !(sel->flags & EF_ENABLED)) {
        return ev(*m, EV_IGNORED);
    }
    if (sel->kind == EK_PAGE) {
        if (m->depth >= MAX_DEPTH) {
            return ev(*m, EV_IGNORED);
        }
        m->stack[m->depth++] = {sel->page, 0, 0};
        return ev(*m, EV_PAGE_OPENED);
    }
    if (sel->kind == EK_TOGGLE) {
        return ev(*m, EV_TOGGLE_GAMBITS);
    }
    if (sel->kind != EK_ACTION) {
        return ev(*m, EV_IGNORED);
    }
    if ((sel->flags & EF_SELF_ONLY) || sel->groups == 0) {
        menu_event e = ev(*m, EV_SUBMIT);
        e.action = sel->action;
        e.group = TG_COUNT;
        e.index = 0;
        model_close(m);
        e.page = PG_NONE;
        return e;
    }
    m->pending = *sel;
    m->target_group = sel->default_group;
    m->target_index = 0;
    m->target_count = 0;
    m->mode = MODE_TARGET;
    menu_event e = ev(*m, EV_TARGET_OPENED);
    e.action = sel->action;
    e.group = m->target_group;
    return e;
}

menu_event model_back(menu_model* m)
{
    if (m->mode == MODE_CLOSED) {
        return ev(*m, EV_IGNORED);
    }
    if (m->mode == MODE_TARGET) {
        if (m->depth == 0) {
            return model_close(m);
        }
        m->mode = MODE_LIST;
        clear_target(m);
        return ev(*m, EV_TARGET_CLOSED);
    }
    if (m->depth > 1) {
        --m->depth;
        return ev(*m, EV_PAGE_CLOSED);
    }
    return model_close(m);
}

void model_set_target_count(menu_model* m, uint16_t count)
{
    m->target_count = count;
    if (count == 0) {
        m->target_index = 0;
    } else if (m->target_index >= count) {
        m->target_index = static_cast<uint16_t>(count - 1);
    }
}

void model_set_target_index(menu_model* m, uint16_t index)
{
    if (m->mode == MODE_TARGET && index < m->target_count) {
        m->target_index = index;
    }
}

menu_event model_cycle_group(menu_model* m, int32_t direction, uint8_t available)
{
    if (m->mode != MODE_TARGET || m->pending.kind != EK_ACTION) {
        return ev(*m, EV_IGNORED);
    }
    uint8_t order[TG_COUNT];
    const uint32_t n = group_order(m->pending, order);
    if (n == 0) {
        return ev(*m, EV_IGNORED);
    }
    uint32_t current = 0;
    for (uint32_t k = 0; k < n; ++k) {
        if (order[k] == m->target_group) {
            current = k;
        }
    }
    const int32_t step = direction >= 0 ? 1 : -1;
    for (uint32_t offset = 1; offset <= n; ++offset) {
        const int32_t at = ((static_cast<int32_t>(current) + step * static_cast<int32_t>(offset)) % static_cast<int32_t>(n) +
                            static_cast<int32_t>(n)) % static_cast<int32_t>(n);
        const uint8_t group = order[at];
        if (group == m->target_group || !(available & (1u << group))) {
            continue;
        }
        m->target_group = group;
        m->target_index = 0;
        m->target_count = 0;
        menu_event e = ev(*m, EV_TARGET_GROUP);
        e.group = group;
        return e;
    }
    return ev(*m, EV_IGNORED);
}

}  // namespace ilc::menu
