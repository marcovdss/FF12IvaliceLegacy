// Ivalice Legacy co-op module: what an action costs, the way the game charges it, plus Legacy's MP economy (pure).
//
// The game's own cost functions, restated from their code (FFXII_TZA.exe 1.0.4.0), so a co-op menu greys exactly what
// the game's admission check would refuse and shows the MP a cast really takes:
//   * cost_helper  = the augment-adjusted cost (0x2F95D0): base u8 action +0x0A; for the party side the Quickening and
//     Esper ids pay the base; Offering turns it into a gil price trunc(base * 64 / 50) * 10; otherwise the Channeling
//     values (augments 91..93, u16 at augment row +4) lower it, base - (base / 100) * sum, in single precision and
//     truncated; Spellspring makes it 0 and Parsimony halves it (toward zero);
//   * mp_cost      = the MP the admission compares (0x309110): 0 for ids past 0x21E and for menu kinds 5/6 (paid with
//     Mist charges), 0 when the caster pays in gil (Offering, not on side 1), else cost_helper;
//   * gil_cost     = the gil price (0x309060): only for Offering casters not on side 1, else 0;
//   * charge_cost  = the Mist charges (0x308FF0): the base cost for menu kinds 5/6, else 0.
// The admission check (0x303BD0) refuses, in this order: 0x13 when s16 MP < mp_cost; 0x14 when the charges (u8 record
// +0x4E) < charge_cost; 0x15 for a party member (record +5 = 0) when gil_cost is not 0 and both the party's gil and
// the gil this actor already put aside for its queued action (actor +0xE58) are below it. Its first check (0x12, an
// action whose content the party no longer holds) is not about cost and is left to the catalog's listing rules.
//
// Legacy's MP economy (scripts/IvaliceLegacy/mp_economy.lua). Versions 0.1.x-0.2.x charged a party member's Magick
// (menu kind 1, listed cost > 0) a surcharge after the cast: the game's own charge times Magick / DIVISOR, never below
// it, at most CAP times it, rounded half up (0.1.x scaled the listed cost instead). Since 0.3.0 (the surcharge removed,
// 2026-11-16) CAP is 1, so the surcharge is always 0 and the shown number is the game's own price; the rule below is
// kept for the parameters the bridge still carries. The game admits the cast on its own price, so greying follows the
// game. The parameters reach the module through the bridge (Lua reads Legacy's file); without them the economy is off
// and every number is the game's.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"
#include "menu/catalog.h"
#include "menu/tables.h"

namespace ilc::menu {

// ---- the game's cost functions ------------------------------------------------------------------------------------
// -1 = unknown: the action is not in the table, or a Channeling augment is present and its value was not read.
int32_t cost_helper(const catalog_tables& t, uint32_t action, const seat_state& s);
int32_t mp_cost(const catalog_tables& t, uint32_t action, const seat_state& s);
int32_t gil_cost(const catalog_tables& t, uint32_t action, const seat_state& s);
int32_t charge_cost(const catalog_tables& t, uint32_t action);

enum cost_refusal : int32_t {
    COST_OK = 0,
    COST_MP = 0x13,           // not enough MP
    COST_CHARGES = 0x14,      // not enough Mist charges
    COST_GIL = 0x15,          // not enough gil (Offering)
    COST_UNKNOWN = -1,        // the MP cost is unknown and nothing else refuses
};
// The admission check's verdict on cost alone (its codes 0x13..0x15, in its order). `gil` = the party's gil.
int32_t cost_admission(const catalog_tables& t, uint32_t action, const seat_state& s, int32_t gil);

// ---- Legacy's MP economy ------------------------------------------------------------------------------------------
using economy_params = ilc_economy;   // present, version, DIVISOR x100, CAP x100 (all 0 = off)

// Is this action a Magick the economy prices (menu kind 1, listed cost > 0)?
bool economy_priced(const catalog_tables& t, uint32_t action);
// What Legacy's economy takes as "what the game charged" (its own restatement of the game's rule: Offering and
// Spellspring 0, Channeling, Parsimony; never below 0). -1 unknown.
int32_t economy_paid(const catalog_tables& t, uint32_t action, const seat_state& s);
// Total = round-half-up(base * factor), factor = 1 up to DIVISOR Magick, else min(CAP, Magick / DIVISOR).
int32_t economy_total(int32_t base, uint32_t magick, const economy_params& p);
// The MP added after the cast (0 when the economy is off, the caster is not a party member, the action is not
// priced or nothing was paid). -1 unknown.
int32_t economy_surcharge(const catalog_tables& t, uint32_t action, const seat_state& s, const economy_params& p);
// What the menu shows: mp_cost + surcharge. -1 unknown.
int32_t shown_mp_cost(const catalog_tables& t, uint32_t action, const seat_state& s, const economy_params& p);

// The parameters in force (co-op thread; set from the player's settings). All zero until settings arrive.
void set_economy(const economy_params& p);
economy_params current_economy();
void configure_from_settings(const ilc_settings& s);

// ---- one catalog entry ----------------------------------------------------------------------------------------------
// Prices an action entry and greys it as the admission would: cost (Mist charges for kinds 5/6, else the shown MP),
// cost_flags, and EF_MP_SHORT / CF_CHARGES_SHORT / CF_GIL_SHORT when it is still enabled. An unknown MP cost is
// shown as -1 with EF_COST_UNKNOWN and not greyed for MP.
void price_entry(const catalog_tables& t, const shared_state& sh, const seat_state& s, const economy_params& p,
                 entry* e);

}  // namespace ilc::menu
