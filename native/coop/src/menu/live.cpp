// Ivalice Legacy co-op module: reading a battle menu's inputs from the running game (see live.h).
#include "menu/live.h"

#include <string.h>

#include "menu/menu_facts.h"

namespace ilc::menu {

namespace {

// Rows are copied a chunk at a time and parsed from the copy (the largest table, actions, is about 32 KiB).
constexpr uint32_t CHUNK = 2048;
constexpr uint32_t TABLE_BYTES_MAX = 256 * 1024;

}  // namespace

table_globals known_table_globals()
{
    table_globals g{};
    g.va[T_ACTIONS] = facts::ACTION_TABLE_PTR;
    g.va[T_LICENCES] = facts::LICENCE_TABLE_PTR;
    g.va[T_MEMBERS] = facts::MEMBER_TABLE_PTR;
    g.va[T_ITEMS] = facts::ITEM_TABLE_PTR;
    g.va[T_MAGICKS] = facts::MAGICK_TABLE_PTR;
    g.va[T_TECHNICKS] = facts::TECHNICK_TABLE_PTR;
    g.va[T_AUGMENTS] = facts::AUGMENT_TABLE_PTR;
    return g;
}

int32_t read_tables(session::game_reader& r, const table_globals& g, catalog_tables* out)
{
    memset(out, 0, sizeof(*out));
    // The resource base is 0 in the game (.bss, never written); anything else means the rows are not where the
    // game itself reads them, so nothing is trusted.
    uintptr_t resource = 0;
    if (!r.ptr(r.global(facts::RESOURCE_BASE_PTR), &resource)) {
        out->status = static_cast<uint32_t>(TABLES_UNREADABLE);
        return TABLES_UNREADABLE;
    }
    if (resource != 0) {
        out->status = static_cast<uint32_t>(TABLES_BAD_BASE);
        return TABLES_BAD_BASE;
    }
    uint32_t parsed = 0;
    int32_t worst = TABLES_OK;
    int32_t optional = TABLES_OK;  // the first error of a table the catalog can do without (members, augments)
    const auto note = [&](uint32_t which, int32_t error) {
        if (which == T_MEMBERS || which == T_AUGMENTS) {
            optional = optional == TABLES_OK ? error : optional;
        } else {
            worst = error;
        }
    };
    for (uint32_t which = 0; which < T_COUNT; ++which) {
        if (g.va[which] == 0) {
            continue;  // not located: missing (finish_tables decides whether that matters)
        }
        uintptr_t header = 0;
        uint32_t count = 0;
        uint16_t stride = 0;
        uint32_t rows = 0;
        if (!r.ptr(r.global(g.va[which]), &header) || header == 0) {
            continue;
        }
        if (!r.u32(header + facts::TABLE_COUNT, &count) || !r.u16(header + facts::TABLE_STRIDE, &stride) ||
            !r.u32(header + facts::TABLE_ROWS, &rows)) {
            note(which, TABLES_UNREADABLE);
            continue;
        }
        const table_id id = static_cast<table_id>(which);
        // Rows are formed in 32 bits (rows + id * stride): the whole table must sit below 4 GiB without wrapping.
        if (count == 0 || rows == 0 || static_cast<uint64_t>(count) * stride > TABLE_BYTES_MAX ||
            uint64_t{rows} + static_cast<uint64_t>(count) * stride > 0xFFFFFFFFull ||  // NOTADDR: 4 GiB
            !begin_table(out, id, count, stride)) {
            note(which, TABLES_BAD_LAYOUT);
            continue;
        }
        uint8_t chunk[CHUNK];
        const uint32_t per_chunk = CHUNK / stride;  // >= 2: begin_table caps the row size at 1 KiB
        bool readable = true;
        for (uint32_t first = 0; first < count && readable; first += per_chunk) {
            const uint32_t n = count - first < per_chunk ? count - first : per_chunk;
            const uint32_t at = rows + first * static_cast<uint32_t>(stride);  // 32-bit row arithmetic
            readable = r.read(static_cast<uintptr_t>(at), chunk, n * stride);
            if (readable) {
                parse_rows(out, id, first, chunk, n, stride);
            }
        }
        if (!readable) {
            begin_table(out, id, count, stride);  // leave nothing half-parsed
            note(which, TABLES_UNREADABLE);
            continue;
        }
        uintptr_t header_again = 0;
        uintptr_t resource_again = 0;
        if (!r.ptr(r.global(g.va[which]), &header_again) || header_again != header ||
            !r.ptr(r.global(facts::RESOURCE_BASE_PTR), &resource_again) || resource_again != 0) {
            begin_table(out, id, count, stride);
            note(which, TABLES_MOVED);
            continue;
        }
        end_table(out, id, count);
        parsed |= 1u << which;
    }
    const int32_t status = finish_tables(out, parsed);
    out->optional_status = optional;
    if (status != TABLES_OK && worst != TABLES_OK) {
        out->status = static_cast<uint32_t>(worst);
        out->ready = 0;
        return worst;
    }
    return status;
}

int32_t read_shared(session::game_reader& r, shared_state* out)
{
    memset(out, 0, sizeof(*out));
    uintptr_t location = 0;
    uintptr_t party = 0;
    if (!r.ptr(r.global(facts::LOCATION_PTR), &location) || !r.ptr(r.global(facts::BATTLE_STATE_PTR), &party) ||
        location == 0 || party == 0) {
        return SAMPLE_NO_STATE;
    }
    uint32_t gil = 0;
    if (!r.u32(location + facts::MENU_FLAGS, &out->menu_flags) || !r.u32(location + facts::PARTY_GIL, &gil) ||
        !r.read(party + facts::ITEM_COUNTS, out->item_counts, sizeof(out->item_counts)) ||
        !r.read(party + facts::MAGICK_OWNED, out->magick_owned, sizeof(out->magick_owned)) ||
        !r.read(party + facts::TECHNICK_OWNED, out->technick_owned, sizeof(out->technick_owned)) ||
        !r.read(party + facts::MIST_OWNED, out->mist_owned, sizeof(out->mist_owned)) ||
        !r.u8(r.global(facts::COMMAND_SEALS), &out->seals)) {
        memset(out, 0, sizeof(*out));
        return SAMPLE_UNREADABLE;
    }
    out->gil = static_cast<int32_t>(gil);
    // The player's saved item order: optional (without it, items are listed by sort key). Its data sits at a 32-bit
    // address relative to the resource base, which must be 0 (see read_tables).
    uintptr_t owner = 0;
    uint32_t relative = 0;
    uintptr_t resource = 1;
    uint32_t count = 0;
    if (r.ptr(r.global(facts::ITEM_ORDER_OWNER_PTR), &owner) && owner != 0 &&
        r.u32(r.global(facts::ITEM_ORDER_OFFSET), &relative) && relative != 0 &&
        r.ptr(r.global(facts::RESOURCE_BASE_PTR), &resource) && resource == 0 &&
        r.u32(owner + facts::ITEM_ORDER_COUNT, &count) && count <= ITEM_CAP &&
        (count == 0 || r.read(static_cast<uintptr_t>(relative), out->item_order, count * 2))) {
        uintptr_t owner_again = 0;
        if (r.ptr(r.global(facts::ITEM_ORDER_OWNER_PTR), &owner_again) && owner_again == owner) {
            out->order_known = 1;
            out->order_count = static_cast<uint8_t>(count);
        }
    }
    if (!out->order_known) {
        memset(out->item_order, 0, sizeof(out->item_order));
    }
    uintptr_t location_again = 0;
    uintptr_t party_again = 0;
    if (!r.ptr(r.global(facts::LOCATION_PTR), &location_again) ||
        !r.ptr(r.global(facts::BATTLE_STATE_PTR), &party_again) || location_again != location ||
        party_again != party) {
        memset(out, 0, sizeof(*out));
        return SAMPLE_MOVED;
    }
    return SAMPLE_OK;
}

int32_t read_seat(session::game_reader& r, uintptr_t actor, uint32_t handle, uint32_t member, uint8_t esper_owner,
                  seat_state* out)
{
    memset(out, 0, sizeof(*out));
    uintptr_t party = 0;
    uint32_t handle_now = 0;
    uintptr_t record = 0;
    if (actor == 0 || !r.ptr(r.global(facts::BATTLE_STATE_PTR), &party) || party == 0) {
        return SAMPLE_NO_STATE;
    }
    if (!r.u32(actor + facts::ACTOR_HANDLE, &handle_now) || !r.ptr(actor + facts::ACTOR_BATTLE, &record)) {
        return SAMPLE_UNREADABLE;
    }
    if (handle_now != handle || record == 0) {
        return SAMPLE_STALE_ACTOR;
    }
    // Every party id's record is the party block's (the game's getter 0x320A40 covers ids 0..0x27).
    if (member >= facts::MEMBER_ROWS ||
        record != party + facts::MEMBER_RECORDS + uintptr_t{member} * facts::MEMBER_RECORD_SIZE) {
        return SAMPLE_STALE_ACTOR;
    }
    uint32_t timed = 0;
    uint32_t lasting = 0;
    uint16_t mp = 0;
    uint32_t gil_reserved = 0;
    uint8_t aug_timed[12];
    uint8_t aug_lasting[12];
    if (!r.u8(record + facts::REC_STATE, &out->record_state) || !r.u8(record + facts::REC_SIDE, &out->side) ||
        !r.u16(record + facts::REC_MP, &mp) || !r.u8(record + facts::REC_CHARGES, &out->charges) ||
        !r.u32(record + facts::REC_STATUS_TIMED, &timed) || !r.u32(record + facts::REC_STATUS_LASTING, &lasting) ||
        !r.read(record + facts::REC_AUGMENTS_TIMED, aug_timed, sizeof(aug_timed)) ||
        !r.read(record + facts::REC_AUGMENTS_LASTING, aug_lasting, sizeof(aug_lasting)) ||
        !r.read(record + facts::REC_LICENCES, out->licences, sizeof(out->licences)) ||
        !r.read(record + facts::REC_MAGICK_LICENCES, out->magick_licences, sizeof(out->magick_licences)) ||
        !r.u8(record + facts::REC_MAGICK, &out->magick) || !r.u32(actor + facts::ACTOR_GIL_RESERVED, &gil_reserved)) {
        memset(out, 0, sizeof(*out));
        return SAMPLE_UNREADABLE;
    }
    uint32_t handle_again = 0;
    uintptr_t record_again = 0;
    uintptr_t party_again = 0;
    if (!r.u32(actor + facts::ACTOR_HANDLE, &handle_again) || !r.ptr(actor + facts::ACTOR_BATTLE, &record_again) ||
        !r.ptr(r.global(facts::BATTLE_STATE_PTR), &party_again) || handle_again != handle ||
        record_again != record || party_again != party) {
        memset(out, 0, sizeof(*out));
        return SAMPLE_STALE_ACTOR;
    }
    out->member = static_cast<uint16_t>(member);
    out->mp = static_cast<int16_t>(mp);
    out->status = timed | lasting;
    for (uint32_t k = 0; k < 12; ++k) {
        out->augments[k] = static_cast<uint8_t>(aug_timed[k] | aug_lasting[k]);
    }
    out->gil_reserved = static_cast<int32_t>(gil_reserved);
    out->esper_owner = esper_owner ? 1 : 0;
    return SAMPLE_OK;
}

}  // namespace ilc::menu
