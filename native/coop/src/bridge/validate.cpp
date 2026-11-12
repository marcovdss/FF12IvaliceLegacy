// Ivalice Legacy co-op module: validation of inbound bridge records (see validate.h).
#include "bridge/validate.h"

#include <stddef.h>

namespace ilc {
namespace {

// ---- Generic field rules, expanded from the tables in bridge_abi.h -------------------------------------

enum : uint8_t { KIND_VAL, KIND_ARR, KIND_REC, KIND_RECS, KIND_PAD, KIND_BYTES };
#define ILC_KIND_VAL KIND_VAL
#define ILC_KIND_ARR KIND_ARR
#define ILC_KIND_REC KIND_REC
#define ILC_KIND_RECS KIND_RECS
#define ILC_KIND_PAD KIND_PAD
#define ILC_KIND_BYTES KIND_BYTES

struct field_rule {
    uint32_t offset;
    uint32_t count;
    uint32_t elem_size;
    uint8_t kind;
    uint32_t lo;
    uint32_t hi;
};

template <typename T>
struct rules_of;

#define ILC_RULE(kind, type, name, count, lo, hi, doc)                                                      \
    field_rule{static_cast<uint32_t>(offsetof(S, name)), static_cast<uint32_t>(count),                     \
               static_cast<uint32_t>(sizeof(S::name) / (count)), ILC_KIND_##kind, static_cast<uint32_t>(lo), \
               static_cast<uint32_t>(hi)},
#define ILC_RULES(type, id, LIST, doc)                                                                      \
    template <>                                                                                             \
    struct rules_of<type> {                                                                                 \
        using S = type;                                                                                     \
        static constexpr uint32_t struct_id = id;                                                           \
        static constexpr field_rule table[] = {LIST(ILC_RULE)};                                             \
        static constexpr uint32_t size = sizeof(table) / sizeof(table[0]);                                  \
    };
ILC_STRUCTS(ILC_RULES)
#undef ILC_RULES
#undef ILC_RULE

template <typename T>
constexpr uint32_t field_id_at(uint32_t offset)
{
    for (uint32_t i = 0; i < rules_of<T>::size; ++i) {
        if (rules_of<T>::table[i].offset == offset) {
            return (rules_of<T>::struct_id << 8) | i;
        }
    }
    return 0;
}

#define FID(T, name) field_id_at<T>(static_cast<uint32_t>(offsetof(T, name)))

bool fail(reject* out, int32_t code, uint32_t field, uint32_t outer, uint32_t inner)
{
    out->code = code;
    out->field = field;
    out->outer = outer;
    out->inner = inner;
    return false;
}

uint32_t load_le(const uint8_t* p, uint32_t size)
{
    uint32_t v = 0;
    for (uint32_t i = size; i != 0; --i) {
        v = (v << 8) | p[i - 1];
    }
    return v;
}

// Ranges of VAL/ARR fields and zero PAD bytes of one record. Nested records are checked by their callers.
template <typename T>
bool check_fields(const T& object, uint32_t outer, reject* out)
{
    const uint8_t* base = reinterpret_cast<const uint8_t*>(&object);
    for (uint32_t i = 0; i < rules_of<T>::size; ++i) {
        const field_rule& rule = rules_of<T>::table[i];
        const uint32_t id = (rules_of<T>::struct_id << 8) | i;
        if (rule.kind == KIND_VAL || rule.kind == KIND_ARR) {
            if (rule.elem_size > 4) {
                continue;  // 64-bit fields exist only in outbound records
            }
            for (uint32_t e = 0; e < rule.count; ++e) {
                const uint32_t v = load_le(base + rule.offset + e * rule.elem_size, rule.elem_size);
                if (v < rule.lo || v > rule.hi) {
                    return fail(out, ILC_E_BAD_FIELD, id, outer, e);
                }
            }
        } else if (rule.kind == KIND_PAD) {
            for (uint32_t e = 0; e < rule.count; ++e) {
                if (base[rule.offset + e] != 0) {
                    return fail(out, ILC_E_BAD_FIELD, id, outer, e);
                }
            }
        }
    }
    return true;
}

bool all_zero(const uint8_t* p, uint32_t count, uint32_t* first)
{
    for (uint32_t i = 0; i < count; ++i) {
        if (p[i] != 0) {
            *first = i;
            return false;
        }
    }
    return true;
}

// ---- Device identities ----------------------------------------------------------------------------------

// Well-formed UTF-8, no control characters (U+0000..U+001F, U+007F, U+0080..U+009F). Returns the offset of the first bad byte or len.
uint32_t first_bad_utf8(const uint8_t* s, uint32_t len)
{
    uint32_t i = 0;
    while (i < len) {
        const uint8_t c = s[i];
        if (c < 0x80) {
            if (c < 0x20 || c == 0x7F) {
                return i;
            }
            ++i;
            continue;
        }
        uint32_t extra = 0;
        uint8_t lo = 0x80, hi = 0xBF;
        if (c >= 0xC2 && c <= 0xDF) {
            extra = 1;
            if (c == 0xC2) {
                lo = 0xA0;  // U+0080..U+009F are control characters
            }
        } else if (c == 0xE0) {
            extra = 2;
            lo = 0xA0;
        } else if ((c >= 0xE1 && c <= 0xEC) || c == 0xEE || c == 0xEF) {
            extra = 2;
        } else if (c == 0xED) {
            extra = 2;
            hi = 0x9F;  // no surrogates
        } else if (c == 0xF0) {
            extra = 3;
            lo = 0x90;
        } else if (c >= 0xF1 && c <= 0xF3) {
            extra = 3;
        } else if (c == 0xF4) {
            extra = 3;
            hi = 0x8F;
        } else {
            return i;
        }
        if (len - i <= extra) {
            return i;
        }
        if (s[i + 1] < lo || s[i + 1] > hi) {
            return i;
        }
        for (uint32_t k = 2; k <= extra; ++k) {
            if (s[i + k] < 0x80 || s[i + k] > 0xBF) {
                return i;
            }
        }
        i += extra + 1;
    }
    return len;
}

bool identity_unused(const ilc_device_identity& id, uint32_t outer, reject* out)
{
    using T = ilc_device_identity;
    uint32_t at = 0;
    if (!all_zero(id.guid, sizeof(id.guid), &at)) {
        return fail(out, ILC_E_BAD_FIELD, FID(T, guid), outer, at);
    }
    if (!all_zero(id.uid, sizeof(id.uid), &at)) {
        return fail(out, ILC_E_BAD_FIELD, FID(T, uid), outer, at);
    }
    if (id.ordinal != 0) {
        return fail(out, ILC_E_BAD_FIELD, FID(T, ordinal), outer, 0);
    }
    if (id.has_uid != 0) {
        return fail(out, ILC_E_BAD_FIELD, FID(T, has_uid), outer, 0);
    }
    if (id.name_len != 0) {
        return fail(out, ILC_E_BAD_FIELD, FID(T, name_len), outer, 0);
    }
    if (!all_zero(id.name, sizeof(id.name), &at)) {
        return fail(out, ILC_E_BAD_FIELD, FID(T, name), outer, at);
    }
    return check_fields(id, outer, out);  // reserved bytes
}

bool identity_used(const ilc_device_identity& id, uint32_t outer, reject* out)
{
    using T = ilc_device_identity;
    if (!check_fields(id, outer, out)) {
        return false;
    }
    uint32_t at = 0;
    if (all_zero(id.guid, sizeof(id.guid), &at)) {
        return fail(out, ILC_E_BAD_FIELD, FID(T, guid), outer, 0);
    }
    if (id.has_uid == 0 && !all_zero(id.uid, sizeof(id.uid), &at)) {
        return fail(out, ILC_E_BAD_FIELD, FID(T, uid), outer, at);
    }
    const uint32_t bad = first_bad_utf8(id.name, id.name_len);  // name_len <= 96 checked above
    if (bad != id.name_len) {
        return fail(out, ILC_E_BAD_FIELD, FID(T, name), outer, bad);
    }
    if (!all_zero(id.name + id.name_len, ILC_NAME_MAX - id.name_len, &at)) {
        return fail(out, ILC_E_BAD_FIELD, FID(T, name), outer, id.name_len + at);
    }
    return true;
}

// Two identities are the same device when GUID, uid flag, uid and ordinal match (the name is display only).
bool same_identity(const ilc_device_identity& a, const ilc_device_identity& b)
{
    const uint32_t key = static_cast<uint32_t>(offsetof(ilc_device_identity, name_len));
    const uint8_t* x = reinterpret_cast<const uint8_t*>(&a);
    const uint8_t* y = reinterpret_cast<const uint8_t*>(&b);
    for (uint32_t i = 0; i < key; ++i) {
        if (x[i] != y[i]) {
            return false;
        }
    }
    return true;
}

// ---- Bindings -------------------------------------------------------------------------------------------

bool check_binding(const ilc_binding& b, uint32_t field, uint32_t outer, uint32_t inner, bool allow_pad,
                   reject* out)
{
    bool ok = false;
    switch (b.kind) {
    case ILC_BIND_NONE:
        ok = b.code == 0;
        break;
    case ILC_BIND_KEY:
        ok = b.code >= 1;
        break;
    case ILC_BIND_MOUSE:
        ok = b.code == 1 || b.code == 2;
        break;
    case ILC_BIND_PAD:
        ok = allow_pad && b.code >= ILC_ENUM_MIN(ILC_PAD_CONTROLS) && b.code <= ILC_ENUM_MAX(ILC_PAD_CONTROLS);
        break;
    default:
        ok = false;
        break;
    }
    return ok ? true : fail(out, ILC_E_BAD_FIELD, field, outer, inner);
}

// A group of bindings (one action, or the kill switch): each valid, no repeated non-empty binding.
bool check_binding_group(const ilc_binding* group, uint32_t count, uint32_t field, uint32_t outer,
                         uint32_t first_index, bool allow_pad, reject* out)
{
    for (uint32_t i = 0; i < count; ++i) {
        if (!check_fields(group[i], outer, out)) {
            out->inner = first_index + i;  // which binding, not which element of a scalar
            return false;
        }
        if (!check_binding(group[i], field, outer, first_index + i, allow_pad, out)) {
            return false;
        }
        if (group[i].kind == ILC_BIND_NONE) {
            continue;
        }
        for (uint32_t j = 0; j < i; ++j) {
            if (group[j].kind == group[i].kind && group[j].code == group[i].code) {
                return fail(out, ILC_E_CONFLICT, field, outer, first_index + i);
            }
        }
    }
    return true;
}

// ---- Seats ----------------------------------------------------------------------------------------------

bool check_seat(const ilc_seat_settings& seat, uint32_t index, reject* out)
{
    using T = ilc_seat_settings;
    using D = ilc_seat_device;
    if (!check_fields(seat, index, out)) {
        return false;
    }
    const uint8_t c = seat.character;
    const bool regular = c < ILC_CHARACTERS;
    if (!(regular || c == ILC_CHAR_AUTO || (c == ILC_CHAR_NONE && index != 0))) {
        return fail(out, ILC_E_BAD_FIELD, FID(T, character), index, 0);
    }
    if (index == 0 && seat.parked != 0) {
        return fail(out, ILC_E_BAD_FIELD, FID(T, parked), index, 0);
    }

    const ilc_seat_device& device = seat.device;
    if (!check_fields(device, index, out)) {
        return false;
    }
    if (index >= ILC_ACTIVE_SEATS_MAX && device.kind != ILC_DEVICE_NONE) {
        return fail(out, ILC_E_BAD_FIELD, FID(D, kind), index, 0);  // a seat that cannot play owns no pad
    }
    if (device.kind != ILC_DEVICE_XINPUT && device.xinput_slot != 0) {
        return fail(out, ILC_E_BAD_FIELD, FID(D, xinput_slot), index, 0);
    }
    if (device.kind == ILC_DEVICE_SDL) {
        if (!identity_used(device.sdl, index, out)) {
            return false;
        }
    } else if (!identity_unused(device.sdl, index, out)) {
        return false;
    }

    const uint32_t bindings = FID(T, bindings);
    for (uint32_t action = 0; action < ILC_ACTIONS; ++action) {
        const uint32_t first = action * ILC_BINDINGS_PER_ACTION;
        if (!check_binding_group(&seat.bindings[first], ILC_BINDINGS_PER_ACTION, bindings, index, first, true,
                                 out)) {
            return false;
        }
        if (index == 0 && (ILC_P1_ACTION_MASK & (1u << action)) == 0) {
            for (uint32_t slot = 0; slot < ILC_BINDINGS_PER_ACTION; ++slot) {
                if (seat.bindings[first + slot].kind != ILC_BIND_NONE) {
                    return fail(out, ILC_E_BAD_FIELD, bindings, index, first + slot);
                }
            }
        }
    }
    return true;
}

// The physical pad behind a seat's device, alias-aware: an SDL identity listed in an alias is its XInput slot.
struct pad_key {
    uint8_t kind;  // ILC_DEVICE_NONE, ILC_DEVICE_XINPUT (slot), ILC_DEVICE_SDL (identity)
    uint8_t slot;
    const ilc_device_identity* identity;
};

pad_key key_of(const ilc_settings& s, const ilc_seat_device& device)
{
    if (device.kind == ILC_DEVICE_XINPUT) {
        return {ILC_DEVICE_XINPUT, device.xinput_slot, nullptr};
    }
    if (device.kind == ILC_DEVICE_SDL) {
        for (uint32_t a = 0; a < s.alias_count; ++a) {
            if (same_identity(s.aliases[a].sdl, device.sdl)) {
                return {ILC_DEVICE_XINPUT, s.aliases[a].xinput_slot, nullptr};
            }
        }
        return {ILC_DEVICE_SDL, 0, &device.sdl};
    }
    return {ILC_DEVICE_NONE, 0, nullptr};
}

bool same_pad(const pad_key& a, const pad_key& b)
{
    if (a.kind == ILC_DEVICE_NONE || a.kind != b.kind) {
        return false;
    }
    return a.kind == ILC_DEVICE_XINPUT ? a.slot == b.slot : same_identity(*a.identity, *b.identity);
}

}  // namespace

// ---- Public ---------------------------------------------------------------------------------------------

uint32_t header_generation_field()
{
    return FID(ilc_header, generation);
}

uint32_t kbm_seat_field()
{
    return FID(ilc_settings, kbm_seat);
}

bool validate_header(const ilc_header& header, uint32_t in_len, uint16_t record_type, reject* out)
{
    using H = ilc_header;
    if (header.magic != ILC_ABI_MAGIC) {
        return fail(out, ILC_E_BAD_HEADER, FID(H, magic), 0, 0);
    }
    if (header.abi_version != ILC_ABI_VERSION) {
        return fail(out, ILC_E_ABI_MISMATCH, FID(H, abi_version), 0, 0);
    }
    if (header.record_type != record_type) {
        return fail(out, ILC_E_BAD_TYPE, FID(H, record_type), 0, 0);
    }
    if (header.size != in_len) {
        return fail(out, ILC_E_BAD_LENGTH, FID(H, size), 0, 0);
    }
    return true;
}

bool validate_settings(const ilc_settings& s, reject* out)
{
    using T = ilc_settings;
    using A = ilc_device_alias;
    if (!check_fields(s, 0, out)) {
        return false;
    }
    if (!check_binding_group(s.kill_switch, ILC_KILL_SWITCH_BINDINGS, FID(T, kill_switch), ILC_LOC_KILL_SWITCH,
                             0, false, out)) {
        return false;
    }

    const ilc_economy& economy = s.economy;
    if (!check_fields(economy, 0, out)) {
        return false;
    }
    if (economy.present == 0) {
        uint32_t at = 0;
        if (!all_zero(reinterpret_cast<const uint8_t*>(&economy), sizeof(economy), &at)) {
            return fail(out, ILC_E_BAD_FIELD, FID(T, economy), 0, at);
        }
    } else {
        if (economy.divisor_x100 < 100) {
            return fail(out, ILC_E_BAD_FIELD, FID(ilc_economy, divisor_x100), 0, 0);
        }
        if (economy.cap_x100 < 100) {
            return fail(out, ILC_E_BAD_FIELD, FID(ilc_economy, cap_x100), 0, 0);
        }
    }

    // Aliases first: the seat device rules look them up.
    for (uint32_t a = 0; a < ILC_ALIASES_MAX; ++a) {
        const ilc_device_alias& alias = s.aliases[a];
        const uint32_t outer = ILC_LOC_ALIAS_0 + a;
        if (!check_fields(alias, outer, out)) {
            return false;
        }
        if (a >= s.alias_count) {
            if (alias.xinput_slot != 0) {
                return fail(out, ILC_E_BAD_FIELD, FID(A, xinput_slot), outer, 0);
            }
            if (!identity_unused(alias.sdl, outer, out)) {
                return false;
            }
            continue;
        }
        if (!identity_used(alias.sdl, outer, out)) {
            return false;
        }
        for (uint32_t b = 0; b < a; ++b) {
            if (same_identity(s.aliases[b].sdl, alias.sdl)) {
                return fail(out, ILC_E_CONFLICT, FID(A, sdl), outer, 0);  // one identity, one slot
            }
        }
    }

    for (uint32_t i = 0; i < ILC_SEATS; ++i) {
        if (!check_seat(s.seats[i], i, out)) {
            return false;
        }
    }
    // Keyboard and mouse belong to a seat that can play, or to nobody. Reported at that seat.
    if (s.kbm_seat > ILC_ACTIVE_SEATS_MAX) {
        return fail(out, ILC_E_BAD_FIELD, FID(T, kbm_seat), s.kbm_seat - 1u, 0);
    }

    // Cross-seat rules.
    using S = ilc_seat_settings;
    for (uint32_t i = 0; i < ILC_SEATS; ++i) {
        const pad_key mine = key_of(s, s.seats[i].device);
        for (uint32_t j = 0; j < i; ++j) {
            if (same_pad(key_of(s, s.seats[j].device), mine)) {
                return fail(out, ILC_E_CONFLICT, FID(ilc_seat_device, kind), i, 0);  // one pad, one owner
            }
            const uint8_t c = s.seats[i].character;
            if (c < ILC_CHARACTERS && s.seats[j].character == c) {
                return fail(out, ILC_E_CONFLICT, FID(S, character), i, 0);  // one character, one seat
            }
        }
    }
    // P1 never loses its last input.
    if (s.seats[0].device.kind == ILC_DEVICE_NONE && s.kbm_seat != 1) {
        return fail(out, ILC_E_CONFLICT, FID(T, kbm_seat), 0, 0);
    }
    return true;
}

bool validate_command(const ilc_command& c, reject* out)
{
    using C = ilc_command;
    if (!check_fields(c, 0, out)) {
        return false;
    }
    if (c.header.generation == 0) {
        return fail(out, ILC_E_BAD_FIELD, FID(ilc_header, generation), 0, 0);
    }
    const uint32_t seat = FID(C, seat), arg0 = FID(C, arg0), arg1 = FID(C, arg1);
    bool seat_ok = false;
    bool arg0_ok = true;
    bool arg1_ok = true;
    switch (c.command) {
    case ILC_CMD_PARK:
        seat_ok = c.seat == 0 || c.seat >= 2;
        arg0_ok = c.arg0 <= 1;
        arg1_ok = c.arg1 == 0;
        break;
    case ILC_CMD_MOVE_SEAT_NOW:
        seat_ok = c.seat >= 1;
        arg0_ok = c.arg0 < ILC_CHARACTERS;
        arg1_ok = c.arg1 == 0;
        break;
    case ILC_CMD_PAIRING_START:
        seat_ok = c.seat >= 1;
        arg0_ok = c.arg0 != 0;
        break;
    case ILC_CMD_CAPTURE_START:
        seat_ok = c.seat >= 1;
        arg0_ok = c.arg0 < ILC_ACTIONS && (c.seat != 1 || (ILC_P1_ACTION_MASK & (1u << c.arg0)) != 0);
        arg1_ok = c.arg1 < ILC_BINDINGS_PER_ACTION;
        break;
    case ILC_CMD_PAIRING_CANCEL:
    case ILC_CMD_CAPTURE_CANCEL:
        seat_ok = c.seat >= 1;
        arg0_ok = c.arg0 == 0;
        arg1_ok = c.arg1 == 0;
        break;
    case ILC_CMD_RESTORE_DEFAULTS:
        seat_ok = true;
        arg0_ok = c.arg0 == 0;
        arg1_ok = c.arg1 == 0;
        break;
    case ILC_CMD_KILL_SWITCH:
        seat_ok = c.seat == 0;
        arg0_ok = c.arg0 <= 1;
        arg1_ok = c.arg1 == 0;
        break;
    default:
        return fail(out, ILC_E_BAD_FIELD, FID(C, command), 0, 0);  // unreachable: range 1..8 checked
    }
    if (c.seat > ILC_ACTIVE_SEATS_MAX) {
        return fail(out, ILC_E_BAD_FIELD, seat, c.seat - 1u, 0);  // no command reaches a seat that cannot play
    }
    if (!seat_ok) {
        return fail(out, ILC_E_BAD_FIELD, seat, 0, 0);
    }
    if (!arg0_ok) {
        return fail(out, ILC_E_BAD_FIELD, arg0, 0, 0);
    }
    if (!arg1_ok) {
        return fail(out, ILC_E_BAD_FIELD, arg1, 0, 0);
    }
    return true;
}

uint32_t settings_digest(const ilc_settings& s)
{
    const uint8_t* p = reinterpret_cast<const uint8_t*>(&s);
    uint32_t h = 0x811C9DC5u;
    for (uint32_t i = sizeof(ilc_header); i < sizeof(ilc_settings); ++i) {
        h ^= p[i];
        h *= 0x01000193u;
    }
    return h;
}

}  // namespace ilc
