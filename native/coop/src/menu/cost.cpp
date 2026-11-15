// Ivalice Legacy co-op module: action costs as the game charges them, and Legacy's MP economy (see cost.h).
#include "menu/cost.h"

#include "menu/menu_facts.h"

namespace ilc::menu {

namespace {

bool has_augment(const seat_state& s, uint32_t n)
{
    return n / 8 < sizeof(s.augments) && ((s.augments[n / 8] >> (n % 8)) & 1) != 0;
}
bool quickening_or_esper(uint32_t action)
{
    return (action >= facts::QUICKENING_FIRST && action <= facts::QUICKENING_LAST) ||
           (action >= facts::ESPER_FIRST && action <= facts::ESPER_LAST);
}
// The caster pays in gil: the game's 0x2F9820 (Offering, unless the record is on side 1).
bool pays_in_gil(const seat_state& s) { return s.side != 1 && has_augment(s, facts::AUG_OFFERING); }

// The Channeling sum, or -1 when a Channeling augment is present and its value is unknown.
int32_t channeling(const catalog_tables& t, const seat_state& s)
{
    int32_t sum = 0;
    for (uint32_t k = 0; k < 3; ++k) {
        if (has_augment(s, facts::AUG_CHANNELING_FIRST + k)) {
            if (!t.channeling_known) {
                return -1;
            }
            sum += t.channeling[k];
        }
    }
    return sum;
}

// base - (base / 100) * reduction, each step in single precision, truncated toward zero (cvttss2si).
int32_t channelled(int32_t base, int32_t reduction)
{
    const float b = static_cast<float>(base);
    const float step = b / facts::COST_PERCENT;
    const float off = step * static_cast<float>(reduction);
    return static_cast<int32_t>(b - off);
}

// The economy's factor and rounding, in double precision as Lua computes them.
double economy_factor(uint32_t magick, const economy_params& p)
{
    const double divisor = p.divisor_x100 / 100.0;
    const double cap = p.cap_x100 / 100.0;
    const double m = static_cast<double>(magick);
    if (m <= divisor) {
        return 1.0;
    }
    const double f = m * 100.0 / p.divisor_x100;
    return f < cap ? f : cap;
}

bool economy_on(const economy_params& p) { return p.present == 1 && p.divisor_x100 >= 100 && p.cap_x100 >= 100; }
bool surcharges_game_price(const economy_params& p)
{
    return p.version_major > 0 || p.version_minor >= 2;   // 0.2.0 and later; 0.1.x scaled the listed cost
}

uint64_t g_economy = 0;   // an economy_params, packed (co-op thread writes; anyone may read)

}  // namespace

// ---- the game's cost functions ------------------------------------------------------------------------------------
int32_t cost_helper(const catalog_tables& t, uint32_t action, const seat_state& s)
{
    const action_rule* r = rule_of(t, action);
    if (r == nullptr) {
        return -1;
    }
    const int32_t base = r->cost;
    if (s.side != 1 && quickening_or_esper(action)) {
        return base;
    }
    if (has_augment(s, facts::AUG_OFFERING)) {
        const float price = static_cast<float>(base) * facts::GIL_PRICE_MUL / facts::GIL_PRICE_DIV;
        return static_cast<int32_t>(price) * facts::GIL_PRICE_SCALE;
    }
    int32_t cost;
    const int32_t reduction = channeling(t, s);
    if (reduction < 0) {
        if (base != 0) {
            return -1;
        }
        cost = 0;   // nothing to lower, whatever the values
    } else {
        cost = channelled(base, reduction);
    }
    if (has_augment(s, facts::AUG_SPELLSPRING)) {
        return 0;
    }
    if (has_augment(s, facts::AUG_PARSIMONY)) {
        return cost / 2;
    }
    return cost;
}

int32_t mp_cost(const catalog_tables& t, uint32_t action, const seat_state& s)
{
    if (action >= facts::ACTION_LIMIT) {
        return 0;
    }
    const action_rule* r = rule_of(t, action);
    if (r == nullptr) {
        return -1;
    }
    if (r->kind == 5 || r->kind == 6) {
        return 0;   // paid with Mist charges
    }
    if (pays_in_gil(s)) {
        return 0;
    }
    return cost_helper(t, action, s);
}

int32_t gil_cost(const catalog_tables& t, uint32_t action, const seat_state& s)
{
    if (s.side == 1 || action >= facts::ACTION_LIMIT) {
        return 0;
    }
    const action_rule* r = rule_of(t, action);
    if (r == nullptr) {
        return -1;
    }
    if (r->kind == 5 || r->kind == 6 || !pays_in_gil(s)) {
        return 0;
    }
    return cost_helper(t, action, s);
}

int32_t charge_cost(const catalog_tables& t, uint32_t action)
{
    if (action >= facts::ACTION_LIMIT) {
        return 0;
    }
    const action_rule* r = rule_of(t, action);
    if (r == nullptr) {
        return -1;
    }
    return r->kind == 5 || r->kind == 6 ? r->cost : 0;
}

int32_t cost_admission(const catalog_tables& t, uint32_t action, const seat_state& s, int32_t gil)
{
    const int32_t mp = mp_cost(t, action, s);
    if (mp >= 0 && s.mp < mp) {
        return COST_MP;
    }
    const int32_t charges = charge_cost(t, action);
    if (charges > 0 && s.charges < charges) {
        return COST_CHARGES;
    }
    if (s.side == 0) {
        const int32_t price = gil_cost(t, action, s);
        if (price > 0 && gil < price && s.gil_reserved < price) {
            return COST_GIL;
        }
        // (A negative price never refuses: the game's comparisons are signed and both sides start at 0 or more.)
    }
    return mp < 0 ? COST_UNKNOWN : COST_OK;
}

// ---- Legacy's MP economy ------------------------------------------------------------------------------------------
bool economy_priced(const catalog_tables& t, uint32_t action)
{
    const action_rule* r = rule_of(t, action);
    return r != nullptr && r->kind == facts::KIND_MAGICK && r->cost > 0;
}

int32_t economy_paid(const catalog_tables& t, uint32_t action, const seat_state& s)
{
    const action_rule* r = rule_of(t, action);
    if (r == nullptr) {
        return -1;
    }
    const int32_t listed = r->cost;
    if (listed <= 0 || has_augment(s, facts::AUG_OFFERING)) {
        return 0;
    }
    const int32_t reduction = channeling(t, s);
    if (reduction < 0) {
        return -1;
    }
    int32_t amount = channelled(listed, reduction);
    if (has_augment(s, facts::AUG_SPELLSPRING)) {
        return 0;
    }
    if (has_augment(s, facts::AUG_PARSIMONY)) {
        amount /= 2;
    }
    return amount > 0 ? amount : 0;
}

int32_t economy_total(int32_t base, uint32_t magick, const economy_params& p)
{
    if (base <= 0) {
        return 0;
    }
    const double total = static_cast<double>(base) * economy_factor(magick, p) + 0.5;
    return static_cast<int32_t>(total);   // >= 0.5, so truncation is floor
}

int32_t economy_surcharge(const catalog_tables& t, uint32_t action, const seat_state& s, const economy_params& p)
{
    if (!economy_on(p) || s.side != 0 || !economy_priced(t, action)) {
        return 0;
    }
    int32_t base = rule_of(t, action)->cost;
    if (surcharges_game_price(p)) {
        base = economy_paid(t, action, s);
        if (base < 0) {
            return -1;
        }
    }
    return base > 0 ? economy_total(base, s.magick, p) - base : 0;
}

int32_t shown_mp_cost(const catalog_tables& t, uint32_t action, const seat_state& s, const economy_params& p)
{
    const int32_t mp = mp_cost(t, action, s);
    if (mp < 0) {
        return -1;
    }
    const int32_t extra = economy_surcharge(t, action, s, p);
    if (extra < 0) {
        return -1;
    }
    return mp + extra;
}

void set_economy(const economy_params& p)
{
    uint64_t packed = 0;
    __builtin_memcpy(&packed, &p, sizeof(packed));
    __atomic_store_n(&g_economy, packed, __ATOMIC_RELEASE);
}

economy_params current_economy()
{
    const uint64_t packed = __atomic_load_n(&g_economy, __ATOMIC_ACQUIRE);
    economy_params p;
    __builtin_memcpy(&p, &packed, sizeof(p));
    return p;
}

void configure_from_settings(const ilc_settings& s) { set_economy(s.economy); }

static_assert(sizeof(economy_params) == sizeof(uint64_t), "the economy parameters travel as one word");

// ---- one catalog entry ----------------------------------------------------------------------------------------------
void price_entry(const catalog_tables& t, const shared_state& sh, const seat_state& s, const economy_params& p,
                 entry* e)
{
    const uint32_t action = e->action;
    e->cost_flags = 0;
    const action_rule* r = rule_of(t, action);
    if (r == nullptr) {
        return;   // describe() already left it unusable, cost -1
    }
    const auto clamp16 = [](int32_t v) { return static_cast<int16_t>(v < -1 ? -1 : (v > 32767 ? 32767 : v)); };
    if (action < facts::ACTION_LIMIT && (r->kind == 5 || r->kind == 6)) {
        e->cost = clamp16(charge_cost(t, action));
        e->cost_flags |= CF_CHARGES;
    } else {
        const int32_t shown = shown_mp_cost(t, action, s, p);
        e->cost = clamp16(shown);
        if (shown < 0) {
            e->flags |= EF_COST_UNKNOWN;
        } else if (shown > mp_cost(t, action, s)) {
            e->cost_flags |= CF_SURCHARGE;
        }
        if (gil_cost(t, action, s) > 0) {
            e->cost_flags |= CF_GIL;
        }
    }
    if (!(e->flags & EF_ENABLED)) {
        return;
    }
    switch (cost_admission(t, action, s, sh.gil)) {
    case COST_MP:
        e->flags = static_cast<uint8_t>((e->flags & ~EF_ENABLED) | EF_MP_SHORT);
        break;
    case COST_CHARGES:
        e->flags = static_cast<uint8_t>(e->flags & ~EF_ENABLED);
        e->cost_flags |= CF_CHARGES_SHORT;
        break;
    case COST_GIL:
        e->flags = static_cast<uint8_t>(e->flags & ~EF_ENABLED);
        e->cost_flags |= CF_GIL_SHORT;
        break;
    default:
        break;
    }
}

}  // namespace ilc::menu
