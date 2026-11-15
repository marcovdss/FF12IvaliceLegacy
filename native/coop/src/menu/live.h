// Ivalice Legacy co-op module: reading a battle menu's inputs from the running game (co-op thread).
//
// Every read goes through the session's fault-safe reader. A sample is fenced: the pointers it was read through are
// read again at the end and must not have moved (a load, a map change or a rebuilt actor discards the sample, and
// the caller keeps the catalog it had). Nothing here writes game memory.
#pragma once

#include <stdint.h>

#include "menu/catalog.h"
#include "menu/tables.h"
#include "session/reader.h"

namespace ilc::menu {

// Where each table's header pointer lives (VAs of the pinned build); 0 = not located, the table is missing.
struct table_globals {
    uintptr_t va[T_COUNT];
};
// The globals the battle-pack loader registers (all seven tables).
table_globals known_table_globals();

// The game's loaded tables. TABLES_OK, or why not (missing, unreadable, bad layout, a non-zero resource base, moved
// while read). Rows are read where the game reads them: (uint32_t)(rows + id * stride), resource base 0.
int32_t read_tables(session::game_reader& r, const table_globals& g, catalog_tables* out);

enum sample_status : int32_t {
    SAMPLE_OK = 0,
    SAMPLE_NO_STATE = -1,       // the party block or the location block is not there
    SAMPLE_UNREADABLE = -2,
    SAMPLE_MOVED = -3,          // a pointer changed while the sample was read
    SAMPLE_STALE_ACTOR = -4,    // the actor's handle or record changed, or the record is not the member's
};

// The inventory, ownership, menu flags, seals and the saved item order (shared by every seat).
int32_t read_shared(session::game_reader& r, shared_state* out);

// One character: its record through the bound actor. `handle` is the actor handle the session resolved; the record
// must be the party block's record for `member` (party ids 0..0x27).
int32_t read_seat(session::game_reader& r, uintptr_t actor, uint32_t handle, uint32_t member, uint8_t esper_owner,
                  seat_state* out);

}  // namespace ilc::menu
