// Ivalice Legacy co-op module: an action's help line from the game's own help text (see help.h).
#include "menu/help.h"

#include "menu/menu_facts.h"

namespace ilc::menu {

namespace {

constexpr uintptr_t HELP_BANK_TABLE = 0x2F973C0;     // VA: qword header pointer per bank
constexpr uintptr_t HELP_LOADED_TABLE = 0x2EC3D80;   // VA: qword per bank, 0 = not loaded
constexpr uint32_t HELP_COUNT = 0x04;                // u32 texts in the bank
constexpr uint32_t HELP_DIRECTORY = 0x14;            // u32 address of the bank's directory
constexpr uint32_t DIRECTORY_LIST = 0x04;            // u32 address of the text list
constexpr uint32_t LIST_FIRST = 0x04;                // u32 text addresses from here
constexpr uint8_t TOKEN = 0x0F;
constexpr uint8_t TOKEN_COLOUR_RUN = 0x29;
constexpr uint8_t TOKEN_COLOUR = 0x3C;
constexpr uint8_t TOKEN_ICON = 0x3F;

}  // namespace

uint32_t strip_help_tokens(const uint8_t* in, uint32_t n, uint8_t* out, uint32_t cap)
{
    if (cap == 0) {
        return 0;
    }
    uint32_t used = 0;
    uint32_t i = 0;
    while (i < n && in[i] != 0) {
        if (in[i] != TOKEN) {
            if (used + 1 < cap) {
                out[used++] = in[i];
            }
            ++i;
            continue;
        }
        if (i + 1 >= n) {
            break;   // a token cut off at the end: drop it
        }
        const uint8_t kind = in[i + 1];
        uint32_t length;
        if (kind == TOKEN_COLOUR_RUN) {
            const uint32_t values = i + 2 < n ? (in[i + 2] & 7u) : 0;
            length = 3 + (values == 0 ? 1 : values);
        } else if (kind == TOKEN_COLOUR || kind == TOKEN_ICON) {
            length = 4;
        } else {
            out[0] = 0;
            return 0;   // not a token we know: the text is not used
        }
        i += length;
    }
    out[used] = 0;
    return used;
}

bool action_help_id(session::game_reader& r, uint32_t action, uint32_t* id)
{
    uint64_t base = 1;
    uintptr_t header = 0;
    uint32_t count = 0;
    uint16_t stride = 0;
    uint32_t rows = 0;
    uint16_t value = 0;
    if (!r.u64(r.global(facts::RESOURCE_BASE_PTR), &base) || base != 0 ||
        !r.ptr(r.global(facts::ACTION_TABLE_PTR), &header) || header == 0 ||
        !r.u32(header + facts::TABLE_COUNT, &count) || action >= count ||
        !r.u16(header + facts::TABLE_STRIDE, &stride) || stride < facts::ACT_ROW_MIN ||
        !r.u32(header + facts::TABLE_ROWS, &rows)) {
        return false;
    }
    const uintptr_t row = static_cast<uint32_t>(rows + action * stride);
    if (!r.u16(row + facts::ACT_HELP, &value)) {
        return false;
    }
    *id = value;
    return true;
}

uintptr_t help_text_at(session::game_reader& r, uint32_t id)
{
    const uint32_t bank = id / HELP_BANK_SIZE;
    const uint32_t index = id % HELP_BANK_SIZE;
    uint64_t loaded = 0;
    uintptr_t header = 0;
    uint32_t count = 0;
    uint32_t directory = 0;
    uint32_t list = 0;
    uint32_t text = 0;
    if (bank >= HELP_BANKS || !r.u64(r.global(HELP_LOADED_TABLE + bank * 8), &loaded) || loaded == 0 ||
        !r.ptr(r.global(HELP_BANK_TABLE + bank * 8), &header) || header == 0 ||
        !r.u32(header + HELP_COUNT, &count) || index >= count || !r.u32(header + HELP_DIRECTORY, &directory) ||
        directory == 0 || !r.u32(uintptr_t{directory} + DIRECTORY_LIST, &list) || list == 0 ||
        !r.u32(uintptr_t{list} + LIST_FIRST + index * 4, &text)) {
        return 0;
    }
    return text;
}

uint32_t read_help(session::game_reader& r, uintptr_t at, uint8_t* out, uint32_t cap)
{
    if (cap == 0) {
        return 0;
    }
    out[0] = 0;
    if (at == 0) {
        return 0;
    }
    uint8_t raw[HELP_RAW_CAP];
    uint32_t got = 0;
    for (; got + 1 < sizeof(raw); ++got) {
        if (!r.u8(at + got, &raw[got])) {
            return 0;   // a text that does not end where it can be read is not used
        }
        if (raw[got] == 0) {
            break;
        }
    }
    return strip_help_tokens(raw, got, out, cap);
}

uint32_t action_help(session::game_reader& r, uint32_t action, uint8_t* out, uint32_t cap, uintptr_t* where)
{
    uint32_t id = 0;
    const uintptr_t at = action_help_id(r, action, &id) ? help_text_at(r, id) : 0;
    if (where != nullptr) {
        *where = at;
    }
    return read_help(r, at, out, cap);
}

uint32_t fit_text(const char* in, char* out, uint32_t cap)
{
    if (cap == 0) {
        return 0;
    }
    uint32_t n = 0;
    while (in[n] != '\0') {
        ++n;
    }
    if (n < cap) {
        for (uint32_t k = 0; k <= n; ++k) {
            out[k] = in[k];
        }
        return n;
    }
    if (cap < 5) {
        out[0] = '\0';
        return 0;
    }
    uint32_t cut = cap - 4;   // room for "..." and the NUL
    uint32_t space = cut;
    while (space > 0 && in[space] != ' ') {
        --space;
    }
    if (space > 0) {
        cut = space;
        while (cut > 0 && (in[cut - 1] == ' ' || in[cut - 1] == ',' || in[cut - 1] == '.')) {
            --cut;
        }
    }
    for (uint32_t k = 0; k < cut; ++k) {
        out[k] = in[k];
    }
    out[cut] = out[cut + 1] = out[cut + 2] = '.';
    out[cut + 3] = '\0';
    return cut + 3;
}

}  // namespace ilc::menu
