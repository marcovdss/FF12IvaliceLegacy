// Ivalice Legacy co-op module: a guest's battle-menu model (pure state, one per seat).
//
// The model is where a guest is in its menu: closed, a page list with a cursor, or a pending action waiting for a
// target. It runs on the co-op thread from the guest's own pad edges; it never reads or writes game memory and never
// submits anything itself. Confirming an action yields a SUBMIT event, the Gambits row a TOGGLE_GAMBITS event; the
// caller turns those into game-thread operations.
//
// Catalog refresh keeps the guest where it was. A new catalog (the inventory, MP, a status or a licence changed) is
// committed in one step:
//  * each open page is kept while it still exists and its parent still links to it; deeper pages are dropped;
//  * on each kept page the cursor follows the same row (an action by its id and shown id, a page row by the page it
//    opens, the Gambits row, the empty label), else it stays at its position on the next selectable row;
//  * a pending action stays pending if the same action is still listed and enabled anywhere (its new data replaces
//    the old), else the menu returns to the list;
//  * an invalid catalog is refused and changes nothing; if no page is left the menu closes.
// A change of character closes the menu (the guest starts again from the root of its new character).
#pragma once

#include <stdint.h>

#include "menu/catalog.h"

namespace ilc::menu {

constexpr uint32_t MAX_DEPTH = 4;   // root -> Magicks & Technicks -> school, or root -> Mist -> Quickening

enum menu_mode : uint8_t {
    MODE_CLOSED = 0,
    MODE_LIST = 1,
    MODE_TARGET = 2,
};

enum event_kind : uint8_t {
    EV_IGNORED = 0,
    EV_OPENED = 1,
    EV_CLOSED = 2,
    EV_CURSOR = 3,
    EV_PAGE_OPENED = 4,
    EV_PAGE_CLOSED = 5,
    EV_TARGET_OPENED = 6,
    EV_TARGET_CLOSED = 7,
    EV_TARGET_MOVED = 8,
    EV_TARGET_GROUP = 9,
    EV_SUBMIT = 10,           // action + group + target index (the caller resolves the candidate), or self
    EV_TOGGLE_GAMBITS = 11,
    EV_INVALID_TARGET = 12,
};

struct menu_event {
    uint8_t kind;             // event_kind
    uint8_t page;             // the page now on top (PG_NONE when closed)
    uint16_t index;           // cursor, or target index
    uint16_t action;          // EV_SUBMIT / EV_TARGET_OPENED
    uint8_t group;            // EV_SUBMIT / EV_TARGET_*: target group; TG_COUNT = the character itself
    uint8_t reserved;
};
static_assert(sizeof(menu_event) == 8, "menu_event layout (host tests read it)");

struct frame {
    uint8_t page;
    uint8_t reserved;
    uint16_t index;
};

struct commit_result {
    uint8_t changed;          // the catalog differs from the one it replaced
    uint8_t depth;            // pages kept open
    uint8_t target_kept;
    uint8_t target_cancelled;
};
static_assert(sizeof(commit_result) == 4, "commit_result layout");

struct menu_model {
    uint8_t mode;             // menu_mode
    uint8_t depth;
    uint8_t target_group;     // target_group while MODE_TARGET
    uint8_t reserved;
    uint16_t target_index;
    uint16_t target_count;    // selectable candidates in target_group (set by the caller that gathers them)
    frame stack[MAX_DEPTH];
    entry pending;            // the action waiting for a target
    catalog pages;            // the committed catalog
};

// No catalog yet, closed.
void model_reset(menu_model* m);
// The selected list row, or nullptr (closed, target mode, empty page).
const entry* model_selected(const menu_model& m);
// A tree every page row of which opens a page it contains, with a root page.
bool catalog_is_valid(const catalog& c);

int32_t model_commit(menu_model* m, const catalog& c, commit_result* result);  // 0, or -1 (nothing changed)
menu_event model_open(menu_model* m);
menu_event model_close(menu_model* m);
menu_event model_toggle(menu_model* m);
menu_event model_navigate(menu_model* m, int32_t delta, bool wrap);
menu_event model_navigate_page(menu_model* m, int32_t direction, uint32_t page_size);
menu_event model_confirm(menu_model* m);
menu_event model_back(menu_model* m);
// Target mode: the caller sets how many candidates the current group has; the next group with candidates
// (`available`: bit = target_group) in the pending action's order.
void model_set_target_count(menu_model* m, uint16_t count);
// Target mode: the candidate the target picker chose (issuer, remedy, kept selection); ignored past the count.
void model_set_target_index(menu_model* m, uint16_t index);
menu_event model_cycle_group(menu_model* m, int32_t direction, uint8_t available);

}  // namespace ilc::menu
