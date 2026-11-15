// Ivalice Legacy co-op module: parsing the game's data tables for the battle menu (see tables.h).
#include "menu/tables.h"

#include <string.h>

#include "menu/menu_facts.h"

namespace ilc::menu {

namespace {

uint16_t rd16(const uint8_t* p) { return static_cast<uint16_t>(p[0] | (p[1] << 8)); }
uint32_t rd32(const uint8_t* p)
{
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) | (static_cast<uint32_t>(p[2]) << 16) |
           (static_cast<uint32_t>(p[3]) << 24);
}

// The smallest row each table is read with, and the largest row count the menu accepts.
struct table_shape {
    uint32_t min_stride;
    uint32_t max_count;
};

constexpr table_shape SHAPES[T_COUNT] = {
    {facts::ACT_ROW_MIN, ACTION_CAP},
    {facts::LIC_ROW_MIN, 1024},              // rows past LICENCE_CAP can never be acquired and are not kept
    {facts::MEMBER_ROW_MIN, 64},
    {10, ITEM_CAP},
    {7, MAGICK_CAP},
    {7, TECHNICK_CAP},
    {6, 1024},  // augments: section 58 has 129 rows; only the Channeling values (91..93) are kept
};

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

bool view_section(const uint8_t* section, size_t size, table_view* out)
{
    *out = {};
    if (section == nullptr || size < 16 || memcmp(section, "st2e", 4) != 0) {
        return false;
    }
    const uint32_t count = rd32(section + 4);
    const uint32_t stride = rd16(section + 8);
    const uint32_t first = rd32(section + 12);
    if (stride == 0 || first > size || count > (size - first) / stride) {
        return false;
    }
    *out = {section + first, count, stride};
    return true;
}

bool begin_table(catalog_tables* t, table_id which, uint32_t count, uint32_t stride)
{
    if (which >= T_COUNT || stride < SHAPES[which].min_stride || stride > MAX_STRIDE ||
        count > SHAPES[which].max_count) {
        return false;
    }
    switch (which) {
    case T_ACTIONS:
        memset(t->actions, 0, sizeof(t->actions));
        t->action_count = 0;
        return true;
    case T_LICENCES:
        memset(t->licences, 0, sizeof(t->licences));
        t->licence_count = 0;
        return true;
    case T_MEMBERS:
        memset(t->fixed, 0, sizeof(t->fixed));
        t->fixed_known = 0;
        return count >= MEMBER_CAP;
    case T_ITEMS:
        memset(t->items, 0, sizeof(t->items));
        t->item_count = 0;
        return true;
    case T_MAGICKS:
        memset(t->magicks, 0, sizeof(t->magicks));
        t->magick_count = 0;
        return true;
    case T_TECHNICKS:
        memset(t->technicks, 0, sizeof(t->technicks));
        t->technick_count = 0;
        return true;
    case T_AUGMENTS:
        memset(t->channeling, 0, sizeof(t->channeling));
        t->channeling_known = 0;
        return count > facts::AUG_CHANNELING_LAST;
    default:
        return false;
    }
}

void parse_rows(catalog_tables* t, table_id which, uint32_t first, const uint8_t* rows, uint32_t n, uint32_t stride)
{
    for (uint32_t k = 0; k < n; ++k) {
        const uint32_t index = first + k;
        const uint8_t* row = rows + static_cast<size_t>(k) * stride;
        switch (which) {
        case T_ACTIONS:
            if (index < ACTION_CAP) {
                action_rule& r = t->actions[index];
                r.present = 1;
                r.cost = row[facts::ACT_COST];
                r.area = row[facts::ACT_AREA];
                r.targeting = row[facts::ACT_TARGETING];
                r.kind = row[facts::ACT_KIND];
                r.rules = rd16(row + facts::ACT_RULES);
                r.target_flags = rd16(row + facts::ACT_TARGET_FLAGS);
            }
            break;
        case T_LICENCES:
            if (index < LICENCE_CAP) {
                licence_node& node = t->licences[index];
                node = {};
                node.type = row[facts::LIC_TYPE];
                const bool lists = (node.type >= 9 && node.type <= 14) || node.type == 20;
                for (uint32_t a = 0; lists && a < LICENCE_ACTIONS; ++a) {
                    const uint16_t action = rd16(row + facts::LIC_ACTIONS + 2 * a);
                    if (action != 0xFFFF) {
                        node.action[node.count++] = action;
                    }
                }
            }
            break;
        case T_MEMBERS:
            if (index < MEMBER_CAP) {
                for (uint32_t a = 0; a < FIXED_SLOTS; ++a) {
                    t->fixed[index][a] = rd16(row + facts::MEMBER_CONTENTS + 2 * a);
                }
            }
            break;
        case T_ITEMS:
            if (index < ITEM_CAP) {
                t->items[index] = {row[7], rd16(row + 8), row[0], {}};
            }
            break;
        case T_MAGICKS:
            if (index < MAGICK_CAP) {
                t->magicks[index] = {row[2], row[2], row[6], {}};
            }
            break;
        case T_TECHNICKS:
            if (index < TECHNICK_CAP) {
                t->technicks[index] = {row[2], row[2], row[6], {}};
            }
            break;
        case T_AUGMENTS:
            if (index >= facts::AUG_CHANNELING_FIRST && index <= facts::AUG_CHANNELING_LAST) {
                t->channeling[index - facts::AUG_CHANNELING_FIRST] = rd16(row + 4);
            }
            break;
        default:
            break;
        }
    }
}

void end_table(catalog_tables* t, table_id which, uint32_t count)
{
    switch (which) {
    case T_ACTIONS: t->action_count = count; break;
    case T_LICENCES: t->licence_count = count < LICENCE_CAP ? count : LICENCE_CAP; break;
    case T_MEMBERS: t->fixed_known = 1; break;
    case T_ITEMS: t->item_count = count; break;
    case T_MAGICKS: t->magick_count = count; break;
    case T_TECHNICKS: t->technick_count = count; break;
    case T_AUGMENTS: t->channeling_known = 1; break;
    default: break;
    }
}

bool parse_table(catalog_tables* t, table_id which, const table_view& v)
{
    if (v.rows == nullptr || !begin_table(t, which, v.count, v.stride)) {
        return false;
    }
    parse_rows(t, which, 0, v.rows, v.count, v.stride);
    end_table(t, which, v.count);
    return true;
}

int32_t finish_tables(catalog_tables* t, uint32_t parsed_mask)
{
    constexpr uint32_t REQUIRED = (1u << T_ACTIONS) | (1u << T_LICENCES) | (1u << T_ITEMS) | (1u << T_MAGICKS) |
                                  (1u << T_TECHNICKS);
    if (!((parsed_mask >> T_MEMBERS) & 1u)) {
        t->fixed_known = 0;
        memset(t->fixed, 0, sizeof(t->fixed));
    }
    if (!((parsed_mask >> T_AUGMENTS) & 1u)) {
        t->channeling_known = 0;
        memset(t->channeling, 0, sizeof(t->channeling));
    }
    if ((parsed_mask & REQUIRED) != REQUIRED) {
        t->status = static_cast<uint32_t>(TABLES_MISSING);
        t->ready = 0;
        t->signature = 0;
        return TABLES_MISSING;
    }
    t->ready = 0;
    uint64_t h = 0xCBF29CE484222325ull;  // NOTADDR: FNV-1a offset basis
    h = fnv(h, &t->action_count, offsetof(catalog_tables, signature) - offsetof(catalog_tables, action_count));
    h = fnv(h, t->actions, sizeof(action_rule) * t->action_count);
    h = fnv(h, t->licences, sizeof(licence_node) * t->licence_count);
    h = fnv(h, t->items, sizeof(content_row) * t->item_count);
    h = fnv(h, t->magicks, sizeof(content_row) * t->magick_count);
    h = fnv(h, t->technicks, sizeof(content_row) * t->technick_count);
    h = fnv(h, t->fixed, sizeof(t->fixed));
    t->signature = h;
    t->status = TABLES_OK;
    t->ready = 1;
    return TABLES_OK;
}

const action_rule* rule_of(const catalog_tables& t, uint32_t action)
{
    if (action >= t.action_count || action >= ACTION_CAP || !t.actions[action].present) {
        return nullptr;
    }
    return &t.actions[action];
}

}  // namespace ilc::menu
