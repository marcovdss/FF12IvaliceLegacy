// Ivalice Legacy co-op module: one guest's battle menu (model + Gambits state + catalog refresh).
//
// The co-op thread refreshes a seat's catalog a few times a second from a fresh sample (live.h). A catalog is
// rebuilt only when something it is built from changed (its signature); a failed build or sample keeps the pages the
// guest is looking at. A change of character closes the menu first. Nothing here touches game memory.
#pragma once

#include <stdint.h>

#include "menu/catalog.h"
#include "menu/gambits.h"
#include "menu/model.h"
#include "menu/tables.h"

namespace ilc::menu {

struct seat_menu {
    menu_model model;
    gambit_state gambits;
    catalog scratch;              // where the next catalog is built
    uint64_t signature;           // of the committed catalog (0 = none)
    int32_t last_status;          // build_status of the last refresh
    uint32_t builds;              // catalogs committed
    uint32_t unchanged;           // refreshes that found the same signature
    uint32_t kept;                // refreshes that failed and kept the old pages
    uint32_t identity_closes;     // menus closed because the character changed
    uint16_t member;              // the character the pages belong to
    uint16_t member_known;        // 1 = a refresh has seen a character
};

void seat_menu_reset(seat_menu* s);

enum refresh_result : int32_t {
    REFRESH_COMMITTED = 1,
    REFRESH_UNCHANGED = 0,
    // < 0: build_status (the old pages stay)
};

// Refresh from a sample. Also mirrors the Gambits state from the character's record.
int32_t seat_menu_refresh(seat_menu* s, const catalog_tables& t, const shared_state& sh, const seat_state& st,
                          commit_result* result);

}  // namespace ilc::menu
