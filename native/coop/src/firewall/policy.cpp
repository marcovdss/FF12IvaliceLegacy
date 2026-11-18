// Ivalice Legacy co-op module: the input firewall's pure rules (see policy.h).
#include "firewall/policy.h"

#include "ilc/bridge_abi.h"

namespace ilc::firewall {

contract derive(const policy_inputs& in)
{
    contract c = {};
    if (in.active == 0) {
        return c;
    }
    const bool native = in.passthrough != 0;
    const bool custom = !native && in.suppressed == 0;  // the co-op menus may own input this beat
    // Nothing the player cannot see may own P1's frontends: without a live presenter the co-op menus own nothing.
    const bool shown = in.presenter != 0;
    const bool menus = shown && in.p1_menus != 0;
    const bool pending = shown && in.leader_pending != 0;
    c.p1_replacement = (menus && in.control != 0 && custom) || pending ? 1 : 0;
    c.other_command = pending || !native ? 1 : 0;
    c.directional_owner = shown && custom && in.any_open != 0 ? 1 : 0;
    c.p1_lb = shown && custom && in.p1_open != 0 && in.leader_selector == 0 ? 1 : 0;
    c.party_blocked = shown && custom && (in.any_open != 0 || in.party_request != 0) ? 1 : 0;
    c.quickening_command = (in.quickening & QB_COMMAND) != 0 ? 1 : 0;
    c.quickening_confirm = (in.quickening & QB_CONFIRM) != 0 ? 1 : 0;
    const bool leader = menus && in.leader_gate != 0;  // the co-op leader selector can be shown
    c.leader_replacement = (pending && leader) || (leader && !native) ? 1 : 0;
    c.mouse_look = in.desktop_guest != 0 ? 1 : 0;
    c.gate = menus ? 1 : 0;
    c.gate_leader = leader ? 1 : 0;
    c.p1_stock = shown && custom && !menus && in.p1_open == 0 && in.party_request == 0 ? 1 : 0;
    return c;
}

policy masks(const contract& c, uint32_t rule)
{
    policy p = {};
    p.flags = FWF_ACTIVE;
    if (c.p1_replacement != 0) {
        p.p1_mask |= M_COMMAND | M_CONFIRM | M_CANCEL;
        p.flags |= FWF_SUPPRESS_P1_AXES;
    }
    if (c.other_command != 0) {
        p.other_mask |= M_COMMAND;
    }
    if (c.directional_owner != 0) {
        // P1's LB is its own switch (p1_lb) so the Change Leader selector can leave the game's turbo native. P1 on the
        // stock frontends keeps its directions and RB: a guest's menu takes only the guests' pads.
        if (c.p1_stock == 0) {
            p.p1_mask |= M_DIRECTIONS | M_RB;
        }
        p.other_mask |= M_DIRECTIONS | M_LB | M_RB;
    }
    if (c.leader_replacement != 0) {
        p.p1_mask |= rule == LEADER_ALL_DIRECTIONS ? M_DIRECTIONS : M_VERTICAL;
        p.other_mask |= M_DIRECTIONS;
    }
    if (c.p1_lb != 0) {
        p.p1_mask |= M_LB;
    }
    if (c.party_blocked != 0) {
        if (c.p1_stock == 0) {
            p.p1_mask |= M_PARTY;
        }
        p.other_mask |= M_PARTY;
    }
    if (c.quickening_command != 0) {
        p.p1_mask |= M_COMMAND;
        p.other_mask |= M_COMMAND;
    }
    if (c.quickening_confirm != 0) {
        p.p1_mask |= M_CONFIRM;
        p.other_mask |= M_CONFIRM;
    }
    if (c.leader_replacement != 0 || c.directional_owner != 0) {
        p.flags |= FWF_SUPPRESS_WHEEL;
    }
    if (c.mouse_look != 0) {
        p.flags |= FWF_SUPPRESS_MOUSE_LOOK;
    }
    if (c.gate != 0) {
        p.flags |= FWF_GATE;
        p.refused = GATE_REFUSED_BATTLE | (c.gate_leader != 0 ? GATE_REFUSED_LEADER : 0u);
    }
    return p;
}

uint32_t p1_prompt_mask(uint32_t p1_mask, const contract& c, bool prompt_ready)
{
    if (!prompt_ready || c.p1_replacement == 0 || c.quickening_confirm != 0) {
        return p1_mask;
    }
    return p1_mask & ~M_CONFIRM;
}

uint32_t p1_pulse(const p1_inputs& in, uint32_t* kind)
{
    uint32_t k = P1P_NONE;
    uint32_t id = 0;
    if (in.driver == 0 && in.replacement != 0) {
        if (in.menu_open != 0) {
            // P1's co-op menu owns CONFIRM and CANCEL. On a target page the native TARGET INFO window would cover
            // co-op's own target card: close it with the cancel the keyboard sends, at most every 0.5 s.
            if (in.target_page != 0 && in.leader_selector == 0 && in.target_info != 0 && in.target_info_due != 0) {
                k = P1P_TARGET_INFO;
                id = facts::SEM_CANCEL;
            }
        } else if (in.cancel_edge != 0) {
            k = P1P_CANCEL;  // B closes native panels (the focus panel opened with LEFT) as the keyboard does
            id = facts::SEM_CANCEL;
        } else if (in.confirm_edge != 0 && in.prompt_state >= facts::PROMPT_HIGHLIGHTED && in.confirm_native == 0) {
            // A on a highlighted prompt interacts, as the keyboard's Enter does. Normally the game read A itself (the
            // prompt was ready last beat, CONFIRM unmasked); the pulse covers only the beat the prompt appeared on,
            // when the record the game read still withheld CONFIRM.
            k = P1P_CONFIRM;
            id = facts::SEM_CONFIRM;
        }
    }
    if (kind != nullptr) {
        *kind = k;
    }
    return id;
}

uint32_t guest_pulse(uint32_t act_pressed, uint32_t act_held, bool native_screen, bool may_pause)
{
    const auto pressed = [&](uint32_t action) { return (act_pressed & (1u << action)) != 0; };
    const auto held = [&](uint32_t action) { return (act_held & (1u << action)) != 0; };
    if (pressed(ILC_ACTION_PAUSE)) {
        // Settings guest_pause 0: a guest's PAUSE does nothing (not even inside a native screen it drives).
        return may_pause ? facts::SEM_PAUSE : 0u;
    }
    if (!native_screen) {
        return 0;
    }
    struct entry {
        uint32_t action;
        uint32_t opposite;  // ILC_ACTIONS = none
        uint32_t id;
    };
    static constexpr entry ORDER[] = {
        {ILC_ACTION_CANCEL, ILC_ACTIONS, facts::SEM_CANCEL},
        {ILC_ACTION_PARTY, ILC_ACTIONS, facts::SEM_PARTY},
        {ILC_ACTION_CONFIRM, ILC_ACTIONS, facts::SEM_CONFIRM},
        {ILC_ACTION_TARGET_PREV, ILC_ACTIONS, facts::SEM_LB},
        {ILC_ACTION_TARGET_NEXT, ILC_ACTIONS, facts::SEM_RB},
        {ILC_ACTION_MENU_UP, ILC_ACTION_MENU_DOWN, facts::SEM_UP},
        {ILC_ACTION_MENU_DOWN, ILC_ACTION_MENU_UP, facts::SEM_DOWN},
        {ILC_ACTION_MENU_LEFT, ILC_ACTION_MENU_RIGHT, facts::SEM_LEFT},
        {ILC_ACTION_MENU_RIGHT, ILC_ACTION_MENU_LEFT, facts::SEM_RIGHT},
    };
    for (const entry& e : ORDER) {
        if (pressed(e.action) && (e.opposite == ILC_ACTIONS || !held(e.opposite))) {
            return e.id;
        }
    }
    return 0;
}

}  // namespace ilc::firewall
