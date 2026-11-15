// Ivalice Legacy co-op module: the native party list, reduced (see relayout.h).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "hud/relayout.h"

#include <string.h>

#include "hud/native_facts.h"
#include "input/sha256.h"
#include "runtime/probe.h"

namespace ilc::hud::relayout {
namespace {

namespace nf = native_facts;

enum field_kind : uint8_t {
    KIND_GROUP_RECT = 1,
    KIND_GROUP_GAP = 2,
    KIND_ENTRY_RECT = 3,
    KIND_TEXT_SCALE = 4,
};
constexpr uint8_t NO_ENTRY = 0xFF;
constexpr uint32_t READ_OK = 0;  // collect(): anything else is a layout_state

struct field {
    uint64_t address;
    uint8_t kind;
    uint8_t group;
    uint8_t entry;
    uint8_t length;  // 8, 4 or 1
    uint8_t value[8];
};

struct layout {
    uint64_t base;
    uint64_t panel;          // address of the selection panel's rect
    int16_t panel_rect[4];
    uint32_t count;
    field fields[nf::FIELDS_MAX];
};

// Game-thread state (one caller at a time: g_busy).
layout g_look;                      // the last full read
layout g_check;                     // re-reads (before writing, after writing, before restoring)
field g_target[nf::FIELDS_MAX];     // the reduced values computed from g_look
field g_stock[nf::FIELDS_MAX];      // the stock values of the allocation we own
uint32_t g_stock_count = 0;
uint64_t g_owned_base = 0;
uint64_t g_guard_address = 0;       // group 40's rect in the allocation we own
uint8_t g_guard_value[8];
bool g_candidate = false;
uint64_t g_candidate_base = 0;
uint64_t g_candidate_since = 0;
uint64_t g_failed_base = 0;
uint64_t g_slot_override = 0;
uint8_t g_hash_input[nf::FIELDS_MAX * 8];

// Published for the status view (written under g_busy, read anywhere).
volatile uint32_t g_state = LAYOUT_UNKNOWN;
volatile uint32_t g_fields = 0;
volatile uint64_t g_base = 0;
uint8_t g_fingerprint[32];
counters g_counters;
volatile uint32_t g_busy = 0;

void bump(volatile uint64_t& c)
{
    __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED);
}

bool take()
{
    return __atomic_exchange_n(&g_busy, 1u, __ATOMIC_ACQ_REL) == 0;
}

void give()
{
    __atomic_store_n(&g_busy, 0u, __ATOMIC_RELEASE);
}

template <typename T>
bool read(uint64_t address, T* out)
{
    return copy_from_caller(out, reinterpret_cast<const void*>(address), sizeof(T));
}

uint64_t slot_address()
{
    return g_slot_override != 0 ? g_slot_override : nf::MRP_FIELD_SLOT;
}

// 0 = no live pointer (or unreadable).
uint64_t read_slot()
{
    uint64_t base = 0;
    if (!load_from_caller(&base, reinterpret_cast<const void*>(slot_address()), sizeof(base))) {
        return 0;
    }
    return base;
}

bool inside(uint64_t base, uint64_t address, uint64_t length)
{
    return address >= base && length <= nf::MRP_SPAN && address - base <= nf::MRP_SPAN - length;
}

constexpr int8_t hex_value(char c)
{
    return c >= '0' && c <= '9' ? static_cast<int8_t>(c - '0')
           : c >= 'a' && c <= 'f' ? static_cast<int8_t>(c - 'a' + 10)
                                  : static_cast<int8_t>(-1);
}

bool digest_is(const uint8_t digest[32], const char* hex)
{
    for (uint32_t k = 0; k < 32; ++k) {
        const int8_t hi = hex_value(hex[2 * k]);
        const int8_t lo = hex_value(hex[2 * k + 1]);
        if (hi < 0 || lo < 0 || digest[k] != static_cast<uint8_t>(hi << 4 | lo)) {
            return false;
        }
    }
    return true;
}

bool rect_is(const int16_t a[4], const int16_t b[4])
{
    return a[0] == b[0] && a[1] == b[1] && a[2] == b[2] && a[3] == b[3];
}

bool add(layout& l, uint64_t address, uint8_t kind, uint32_t group, uint8_t entry, uint8_t length)
{
    if (l.count >= nf::FIELDS_MAX) {
        return false;
    }
    field& f = l.fields[l.count];
    memset(&f, 0, sizeof(f));
    if (!copy_from_caller(f.value, reinterpret_cast<const void*>(address), length)) {
        return false;
    }
    f.address = address;
    f.kind = kind;
    f.group = static_cast<uint8_t>(group);
    f.entry = entry;
    f.length = length;
    ++l.count;
    return true;
}

// Reads every field of groups 40..51 and the panel rect. READ_OK, LAYOUT_UNLOADED or LAYOUT_INVALID.
uint32_t collect(layout& l, uint64_t base)
{
    l.base = base;
    l.count = 0;
    l.panel = 0;
    uint32_t magic = 0;
    if (base == 0 || !read(base, &magic) || magic != nf::MRP_LIVE_MAGIC) {
        return LAYOUT_UNLOADED;
    }
    uint32_t group_count = 0;
    uint32_t group_list = 0;
    if (!read(base + nf::MRP_GROUP_COUNT, &group_count) || !read(base + nf::MRP_GROUP_LIST, &group_list) ||
        group_count <= nf::GROUP_LAST || group_count > 0x400 ||
        !inside(base, group_list, static_cast<uint64_t>(group_count) * nf::GROUP_SIZE)) {
        return LAYOUT_INVALID;
    }
    l.panel = group_list + nf::GROUP_PANEL * nf::GROUP_SIZE + nf::GROUP_RECT;
    if (!copy_from_caller(l.panel_rect, reinterpret_cast<const void*>(l.panel), sizeof(l.panel_rect))) {
        return LAYOUT_INVALID;
    }
    for (uint32_t g = nf::GROUP_FIRST; g <= nf::GROUP_LAST; ++g) {
        const uint64_t record = group_list + static_cast<uint64_t>(g) * nf::GROUP_SIZE;
        uint8_t count = 0;
        uint32_t entries = 0;
        if (!read(record + nf::GROUP_COUNT_BYTE, &count) || !read(record + nf::GROUP_ENTRIES, &entries) ||
            count > nf::ENTRIES_MAX || (count != 0 && !inside(base, entries, nf::ENTRY_MIN))) {
            return LAYOUT_INVALID;
        }
        if (!add(l, record + nf::GROUP_RECT, KIND_GROUP_RECT, g, NO_ENTRY, 8) ||
            !add(l, record + nf::GROUP_GAP, KIND_GROUP_GAP, g, NO_ENTRY, 4)) {
            return LAYOUT_INVALID;
        }
        uint64_t entry = entries;
        for (uint32_t k = 0; k < count; ++k) {
            uint8_t head[2] = {0, 0};
            if (!copy_from_caller(head, reinterpret_cast<const void*>(entry), sizeof(head))) {
                return LAYOUT_INVALID;
            }
            const uint8_t size = head[nf::ENTRY_SIZE_BYTE];
            const uint8_t type = head[nf::ENTRY_TYPE_BYTE];
            if (size < nf::ENTRY_MIN || size > nf::ENTRY_MAX || !inside(base, entry, size)) {
                return LAYOUT_INVALID;
            }
            if (!add(l, entry + nf::ENTRY_RECT, KIND_ENTRY_RECT, g, static_cast<uint8_t>(k), 8)) {
                return LAYOUT_INVALID;
            }
            if (type == nf::ENTRY_TYPE_TEXT && size >= 0x18 &&
                !add(l, entry + nf::ENTRY_TEXT_SCALE, KIND_TEXT_SCALE, g, static_cast<uint8_t>(k), 1)) {
                return LAYOUT_INVALID;
            }
            entry += size;
        }
    }
    return READ_OK;
}

void fingerprint(const field* fields, uint32_t count, uint8_t out[32])
{
    uint32_t n = 0;
    for (uint32_t i = 0; i < count; ++i) {
        memcpy(g_hash_input + n, fields[i].value, fields[i].length);
        n += fields[i].length;
    }
    sha256(g_hash_input, n, out);
}

bool same_fields(const layout& a, const layout& b)
{
    if (a.base != b.base || a.count != b.count || a.panel != b.panel || !rect_is(a.panel_rect, b.panel_rect)) {
        return false;
    }
    for (uint32_t i = 0; i < a.count; ++i) {
        const field& x = a.fields[i];
        const field& y = b.fields[i];
        if (x.address != y.address || x.kind != y.kind || x.length != y.length ||
            memcmp(x.value, y.value, x.length) != 0) {
            return false;
        }
    }
    return true;
}

// ---- the transform (stock -> reduced) ----------------------------------------------------------------------------

int32_t scaled(int32_t v)
{
    const int32_t a = v < 0 ? -v : v;
    const int32_t m = (a * nf::SCALE_NUM * 2 + nf::SCALE_DEN) / (nf::SCALE_DEN * 2);  // |v| * 3/4, half up
    return v < 0 ? -m : m;
}

int32_t digit_size(int32_t v)
{
    const int32_t m = (v * nf::DIGIT_NUM + nf::DIGIT_DEN / 2) / nf::DIGIT_DEN;
    return m < 1 ? 1 : m;
}

bool is_root(uint32_t g)
{
    return g == nf::GROUP_ROW || g == nf::GROUP_GLOW || g == nf::GROUP_ACTION || g == nf::GROUP_BAND;
}

bool is_digit(uint32_t g, uint32_t e)
{
    for (const nf::digit_entries& d : nf::DIGITS) {
        if (d.group == g && e >= d.first && e <= d.last) {
            return true;
        }
    }
    return false;
}

void get_rect(const field& f, int32_t r[4])
{
    int16_t v[4];
    memcpy(v, f.value, sizeof(v));
    for (uint32_t k = 0; k < 4; ++k) {
        r[k] = v[k];
    }
}

void put_rect(field& f, const int32_t r[4])
{
    int16_t v[4];
    for (uint32_t k = 0; k < 4; ++k) {
        v[k] = static_cast<int16_t>(r[k]);
    }
    memcpy(f.value, v, sizeof(v));
}

void put_rect16(field& f, const int16_t r[4])
{
    memcpy(f.value, r, 8);
}

bool reduce_action(const field& from, field& to)
{
    switch (from.kind) {
    case KIND_GROUP_RECT:
        put_rect16(to, nf::ACTION_RECT);
        return true;
    case KIND_GROUP_GAP: {
        const int32_t gap = nf::ACTION_GAP;
        memcpy(to.value, &gap, 4);
        return true;
    }
    case KIND_ENTRY_RECT:
        if (from.entry >= sizeof(nf::ACTION_ENTRY_RECTS) / sizeof(nf::ACTION_ENTRY_RECTS[0])) {
            return false;
        }
        put_rect16(to, nf::ACTION_ENTRY_RECTS[from.entry]);
        return true;
    case KIND_TEXT_SCALE:
        if (from.entry != 1) {
            return false;
        }
        to.value[0] = nf::ACTION_TEXT_SCALE;
        return true;
    default:
        return false;
    }
}

bool reduce_field(const field& from, field& to)
{
    to = from;
    const uint32_t g = from.group;
    if (g == nf::GROUP_ACTION) {
        return reduce_action(from, to);
    }
    switch (from.kind) {
    case KIND_GROUP_RECT: {
        int32_t r[4];
        get_rect(from, r);
        if (is_root(g)) {
            r[0] = nf::ANCHOR_X + scaled(r[0] - nf::ANCHOR_X);
            r[1] = nf::ANCHOR_Y + scaled(r[1] - nf::ANCHOR_Y);
        } else {
            r[0] = scaled(r[0]);
            r[1] = scaled(r[1]);
        }
        r[2] = scaled(r[2]);
        r[3] = scaled(r[3]);
        if (g == nf::GROUP_ROW) {
            r[0] = nf::ROOT_X_ROW;
        } else if (g == nf::GROUP_GLOW) {
            r[0] = nf::ROOT_X_GLOW;
        } else if (g == nf::GROUP_BAND) {
            r[0] = nf::ROOT_X_BAND;
        }
        put_rect(to, r);
        return true;
    }
    case KIND_GROUP_GAP: {
        int32_t gap = 0;
        memcpy(&gap, from.value, 4);
        gap = (g == nf::GROUP_ROW || g == nf::GROUP_GLOW) ? nf::ROW_GAP : scaled(gap);
        memcpy(to.value, &gap, 4);
        return true;
    }
    case KIND_ENTRY_RECT: {
        int32_t r[4];
        get_rect(from, r);
        for (uint32_t k = 0; k < 4; ++k) {
            r[k] = scaled(r[k]);
        }
        if (is_digit(g, from.entry)) {
            r[2] = digit_size(r[2]);
            r[3] = digit_size(r[3]);
            r[1] += nf::DIGIT_Y_SHIFT;
        }
        if (g == nf::GROUP_ROW && from.entry == nf::ROW_NAME_ENTRY) {
            r[1] = nf::ROW_NAME_Y;
        } else if (g == nf::GROUP_ESPER && from.entry == nf::ESPER_NAME_ENTRY) {
            r[1] = nf::ESPER_NAME_Y;
        }
        put_rect(to, r);
        return true;
    }
    case KIND_TEXT_SCALE: {
        int32_t s = scaled(from.value[0]);
        s = s < 1 ? 1 : (s > 255 ? 255 : s);
        if (g == nf::GROUP_ESPER && from.entry == nf::ESPER_NAME_ENTRY) {
            s = nf::ESPER_NAME_SCALE;
        }
        to.value[0] = static_cast<uint8_t>(s);
        return true;
    }
    default:
        return false;
    }
}

// The reduced values for a stock layout; false if its shape is not the one the reduced layout was reviewed on.
bool reduce(const layout& from, field* to)
{
    uint32_t action = 0;
    for (uint32_t i = 0; i < from.count; ++i) {
        if (!reduce_field(from.fields[i], to[i])) {
            return false;
        }
        action += from.fields[i].group == nf::GROUP_ACTION ? 1u : 0u;
    }
    return action == nf::ACTION_FIELDS;
}

// ---- writes -------------------------------------------------------------------------------------------------------

// Writes `to` over `from` at `address` only while the allocation is still the one at the slot and the bytes there are
// still `from`; then reads them back.
bool write_checked(uint64_t base, uint64_t address, const uint8_t* from, const uint8_t* to, uint32_t length)
{
    uint8_t now[8];
    if (read_slot() != base || !copy_from_caller(now, reinterpret_cast<const void*>(address), length) ||
        memcmp(now, from, length) != 0) {
        bump(g_counters.raced);
        return false;
    }
    if (!copy_to_caller(reinterpret_cast<void*>(address), to, length) ||
        !copy_from_caller(now, reinterpret_cast<const void*>(address), length) || memcmp(now, to, length) != 0) {
        bump(g_counters.failed);
        return false;
    }
    return true;
}

// Puts `stock` back wherever the live bytes are still `ours` (used after a partial or unverified apply).
void roll_back(uint64_t base, const field* stock, const field* ours, uint32_t count, uint64_t panel)
{
    for (uint32_t i = 0; i < count; ++i) {
        if (memcmp(stock[i].value, ours[i].value, stock[i].length) != 0) {
            write_checked(base, stock[i].address, ours[i].value, stock[i].value, stock[i].length);
        }
    }
    write_checked(base, panel, reinterpret_cast<const uint8_t*>(nf::PANEL_REDUCED),
                  reinterpret_cast<const uint8_t*>(nf::PANEL_STOCK), 8);
}

void publish(uint32_t state, const layout* l, const uint8_t* digest)
{
    g_state = state;
    if (l != nullptr) {
        g_base = l->base;
        g_fields = l->count;
    }
    if (digest != nullptr) {
        memcpy(g_fingerprint, digest, 32);
    }
}

void drop_ownership()
{
    if (g_owned_base != 0) {
        g_owned_base = 0;
        g_guard_address = 0;
        g_stock_count = 0;
        bump(g_counters.dropped);
    }
}

uint32_t apply(uint64_t base, uint64_t now_ms)
{
    if (g_owned_base != 0 && g_owned_base != base) {
        drop_ownership();  // the allocation we edited was replaced
    }
    // Still ours: the three-read guard (slot, group 40's rect, the panel).
    if (g_owned_base == base && g_guard_address != 0) {
        uint8_t guard[8];
        int16_t panel[4];
        const uint64_t panel_at = g_look.base == base ? g_look.panel : 0;
        if (panel_at != 0 && copy_from_caller(guard, reinterpret_cast<const void*>(g_guard_address), 8) &&
            memcmp(guard, g_guard_value, 8) == 0 &&
            copy_from_caller(panel, reinterpret_cast<const void*>(panel_at), sizeof(panel)) &&
            rect_is(panel, nf::PANEL_REDUCED)) {
            bump(g_counters.quick);
            publish(LAYOUT_REDUCED, nullptr, nullptr);
            return LAYOUT_REDUCED;
        }
    }
    bump(g_counters.looks);
    const uint32_t read_state = collect(g_look, base);
    if (read_state != READ_OK) {
        if (read_state == LAYOUT_INVALID) {
            bump(g_counters.invalid);
        }
        if (g_owned_base == base) {
            drop_ownership();
        }
        g_candidate = false;
        publish(read_state, &g_look, nullptr);
        return read_state;
    }
    uint8_t digest[32];
    fingerprint(g_look.fields, g_look.count, digest);
    const bool stock_panel = rect_is(g_look.panel_rect, nf::PANEL_STOCK);
    const bool reduced_panel = rect_is(g_look.panel_rect, nf::PANEL_REDUCED);

    if (digest_is(digest, nf::FINGERPRINT_REDUCED)) {
        if (g_owned_base == base && (reduced_panel || stock_panel)) {
            // Ours. A panel the game put back to stock (it is only a rect) is moved again.
            if (stock_panel && !write_checked(base, g_look.panel, reinterpret_cast<const uint8_t*>(nf::PANEL_STOCK),
                                              reinterpret_cast<const uint8_t*>(nf::PANEL_REDUCED), 8)) {
                publish(LAYOUT_REDUCED, &g_look, digest);
                return LAYOUT_REDUCED;
            }
            memcpy(g_guard_value, g_look.fields[0].value, 8);
            g_guard_address = g_look.fields[0].address;
            publish(LAYOUT_REDUCED, &g_look, digest);
            return LAYOUT_REDUCED;
        }
        if (g_owned_base == base) {
            drop_ownership();  // the panel was moved by someone else: the layout is no longer only ours
        }
        bump(g_counters.foreign);
        g_candidate = false;
        const uint32_t state = (reduced_panel || stock_panel) ? LAYOUT_REDUCED_OTHER : LAYOUT_FOREIGN;
        publish(state, &g_look, digest);
        return state;
    }
    if (g_owned_base == base) {
        drop_ownership();  // reloaded in place, or changed by someone else
    }
    if (base == g_failed_base) {
        publish(LAYOUT_FAILED, &g_look, digest);
        return LAYOUT_FAILED;
    }
    if (!digest_is(digest, nf::FINGERPRINT_STOCK) || !stock_panel) {
        bump(g_counters.foreign);
        g_candidate = false;
        publish(LAYOUT_FOREIGN, &g_look, digest);
        return LAYOUT_FOREIGN;
    }
    // Stock. Let a freshly loaded allocation settle first.
    if (!g_candidate || g_candidate_base != base) {
        g_candidate = true;
        g_candidate_base = base;
        g_candidate_since = now_ms;
    }
    if (now_ms - g_candidate_since < SETTLE_MS) {
        bump(g_counters.settling);
        publish(LAYOUT_SETTLING, &g_look, digest);
        return LAYOUT_SETTLING;
    }
    uint8_t target_digest[32];
    if (!reduce(g_look, g_target) || (fingerprint(g_target, g_look.count, target_digest),
                                      !digest_is(target_digest, nf::FINGERPRINT_REDUCED))) {
        g_failed_base = base;
        bump(g_counters.failed);
        publish(LAYOUT_FAILED, &g_look, digest);
        return LAYOUT_FAILED;
    }
    // The allocation must still be exactly what was read, right before the first write.
    if (read_slot() != base || collect(g_check, base) != READ_OK || !same_fields(g_check, g_look)) {
        bump(g_counters.raced);
        g_candidate = false;
        publish(LAYOUT_SETTLING, &g_look, digest);
        return LAYOUT_SETTLING;
    }
    memcpy(g_stock, g_look.fields, sizeof(field) * g_look.count);
    g_stock_count = g_look.count;
    bool ok = true;
    uint32_t written = 0;
    for (; written < g_look.count && ok; ++written) {
        const field& from = g_look.fields[written];
        if (memcmp(from.value, g_target[written].value, from.length) != 0) {
            ok = write_checked(base, from.address, from.value, g_target[written].value, from.length);
        }
    }
    ok = ok && write_checked(base, g_look.panel, reinterpret_cast<const uint8_t*>(nf::PANEL_STOCK),
                             reinterpret_cast<const uint8_t*>(nf::PANEL_REDUCED), 8);
    uint8_t verify[32];
    ok = ok && collect(g_check, base) == READ_OK && g_check.count == g_look.count &&
         (fingerprint(g_check.fields, g_check.count, verify), digest_is(verify, nf::FINGERPRINT_REDUCED)) &&
         rect_is(g_check.panel_rect, nf::PANEL_REDUCED);
    g_candidate = false;
    if (!ok) {
        roll_back(base, g_stock, g_target, g_stock_count, g_look.panel);
        g_stock_count = 0;
        g_failed_base = base;
        bump(g_counters.failed);
        publish(LAYOUT_FAILED, &g_look, digest);
        return LAYOUT_FAILED;
    }
    g_owned_base = base;
    g_guard_address = g_check.fields[0].address;
    memcpy(g_guard_value, g_check.fields[0].value, 8);
    memcpy(g_look.fields, g_check.fields, sizeof(field) * g_check.count);
    memcpy(g_look.panel_rect, g_check.panel_rect, sizeof(g_look.panel_rect));
    bump(g_counters.applied);
    publish(LAYOUT_REDUCED, &g_check, verify);
    return LAYOUT_REDUCED;
}

int32_t give_back()
{
    if (g_owned_base == 0) {
        return RESTORE_NOTHING;
    }
    const uint64_t base = read_slot();
    if (base != g_owned_base || collect(g_check, base) != READ_OK) {
        drop_ownership();
        publish(LAYOUT_UNKNOWN, nullptr, nullptr);
        return RESTORE_GONE;
    }
    uint8_t digest[32];
    fingerprint(g_check.fields, g_check.count, digest);
    if (digest_is(digest, nf::FINGERPRINT_STOCK) && rect_is(g_check.panel_rect, nf::PANEL_STOCK)) {
        drop_ownership();  // the game reloaded the layout in place
        publish(LAYOUT_STOCK, &g_check, digest);
        return RESTORE_GONE;
    }
    bool shape = g_check.count == g_stock_count && digest_is(digest, nf::FINGERPRINT_REDUCED) &&
                 rect_is(g_check.panel_rect, nf::PANEL_REDUCED);
    for (uint32_t i = 0; shape && i < g_check.count; ++i) {
        shape = g_check.fields[i].address == g_stock[i].address && g_check.fields[i].length == g_stock[i].length;
    }
    if (!shape) {
        drop_ownership();
        publish(LAYOUT_FOREIGN, &g_check, digest);
        return RESTORE_CHANGED;
    }
    bool ok = true;
    for (uint32_t i = 0; i < g_check.count && ok; ++i) {
        const field& now = g_check.fields[i];
        if (memcmp(now.value, g_stock[i].value, now.length) != 0) {
            ok = write_checked(base, now.address, now.value, g_stock[i].value, now.length);
        }
    }
    ok = ok && write_checked(base, g_check.panel, reinterpret_cast<const uint8_t*>(nf::PANEL_REDUCED),
                             reinterpret_cast<const uint8_t*>(nf::PANEL_STOCK), 8);
    uint8_t verify[32];
    ok = ok && collect(g_check, base) == READ_OK &&
         (fingerprint(g_check.fields, g_check.count, verify), digest_is(verify, nf::FINGERPRINT_STOCK)) &&
         rect_is(g_check.panel_rect, nf::PANEL_STOCK);
    g_owned_base = 0;
    g_guard_address = 0;
    g_stock_count = 0;
    g_candidate = false;
    if (!ok) {
        bump(g_counters.failed);
        publish(LAYOUT_FAILED, &g_check, nullptr);
        return RESTORE_FAILED;
    }
    bump(g_counters.restored);
    publish(LAYOUT_STOCK, &g_check, verify);
    return RESTORE_DONE;
}

}  // namespace

uint32_t ensure_reduced(uint64_t now_ms)
{
    if (!take()) {
        bump(g_counters.busy);
        return LAYOUT_BUSY;
    }
    const uint64_t base = read_slot();
    uint32_t state;
    if (base == 0) {
        drop_ownership();
        g_candidate = false;
        g_base = 0;
        g_state = LAYOUT_UNLOADED;
        state = LAYOUT_UNLOADED;
    } else {
        state = apply(base, now_ms);
    }
    give();
    return state;
}

int32_t restore()
{
    if (!take()) {
        bump(g_counters.busy);
        return RESTORE_BUSY;
    }
    const int32_t r = give_back();
    give();
    return r;
}

int32_t restore_waiting(uint32_t wait_ms)
{
    for (uint32_t waited = 0;; ++waited) {
        if (take()) {
            const int32_t r = give_back();
            give();
            return r;
        }
        if (waited >= wait_ms) {
            bump(g_counters.busy);
            return RESTORE_BUSY;
        }
        Sleep(1);
    }
}

bool owned()
{
    return __atomic_load_n(&g_owned_base, __ATOMIC_ACQUIRE) != 0;
}

status_view status()
{
    status_view v;
    memset(&v, 0, sizeof(v));
    v.state = g_state;
    v.fields = g_fields;
    v.base = g_base;
    v.owned_base = __atomic_load_n(&g_owned_base, __ATOMIC_ACQUIRE);
    memcpy(v.fingerprint, g_fingerprint, sizeof(v.fingerprint));
    return v;
}

const counters* stats()
{
    return &g_counters;
}

void test_reset()
{
    memset(&g_counters, 0, sizeof(g_counters));
    g_stock_count = 0;
    g_owned_base = 0;
    g_guard_address = 0;
    g_candidate = false;
    g_candidate_base = 0;
    g_candidate_since = 0;
    g_failed_base = 0;
    g_state = LAYOUT_UNKNOWN;
    g_fields = 0;
    g_base = 0;
    memset(g_fingerprint, 0, sizeof(g_fingerprint));
    g_look.base = 0;
    g_look.count = 0;
    g_busy = 0;
}

void test_set_slot(uint64_t va)
{
    g_slot_override = va;
}

bool test_hold(bool hold)
{
    if (hold) {
        return take();
    }
    give();
    return true;
}

}  // namespace ilc::hud::relayout
