// Ivalice Legacy co-op module: the co-op battle menus' pure rules (see rules.h).
#include "frontend/rules.h"

#include <string.h>

#include "ilc/bridge_abi.h"
#include "menu/model.h"

namespace ilc::frontend {

// ---- commands ---------------------------------------------------------------------------------------------------
uint32_t commands(bool player1, uint8_t mode, bool prompt_ready, const seat_edges& e)
{
    const auto pressed = [&](uint32_t a) { return (e.pressed & (1u << a)) != 0; };
    const auto again = [&](uint32_t a) { return ((e.pressed | e.repeated) & (1u << a)) != 0; };
    const auto held = [&](uint32_t a) { return (e.held & (1u << a)) != 0; };
    uint32_t c = 0;
    if (mode == menu::MODE_CLOSED) {
        if (pressed(ILC_ACTION_BATTLE_MENU) || (player1 && pressed(ILC_ACTION_CONFIRM) && !prompt_ready)) {
            c |= CMD_OPEN;
        }
        if (pressed(ILC_ACTION_PARTY)) {
            c |= CMD_PARTY;
        }
        // UP or DOWN alone (a fresh press, never a repeat; the other one not held) opens the character selector.
        const bool up = pressed(ILC_ACTION_MENU_UP) && !held(ILC_ACTION_MENU_DOWN);
        const bool down = pressed(ILC_ACTION_MENU_DOWN) && !held(ILC_ACTION_MENU_UP);
        if (up != down) {
            c |= CMD_SELECT;
        }
        return c;
    }
    if (pressed(ILC_ACTION_BATTLE_MENU)) {
        return CMD_CLOSE;  // the open button closes again; nothing else this beat
    }
    // Vertical movement: a held direction repeats; the opposite direction held cancels it.
    uint32_t vertical = 0;
    if (again(ILC_ACTION_MENU_UP) && !held(ILC_ACTION_MENU_DOWN) && !again(ILC_ACTION_MENU_DOWN)) {
        vertical = CMD_UP;
    } else if (again(ILC_ACTION_MENU_DOWN) && !held(ILC_ACTION_MENU_UP) && !again(ILC_ACTION_MENU_UP)) {
        vertical = CMD_DOWN;
    }
    const bool left = again(ILC_ACTION_MENU_LEFT);
    const bool right = again(ILC_ACTION_MENU_RIGHT);
    if (mode == menu::MODE_LIST) {
        // Guests page with LB/RB and with left/right; P1's LB/RB are bound to its MENU_LEFT/RIGHT.
        const bool back_page = left || (!player1 && pressed(ILC_ACTION_TARGET_PREV));
        const bool next_page = right || (!player1 && pressed(ILC_ACTION_TARGET_NEXT));
        if (back_page && !next_page) {
            c |= CMD_PAGE_UP;
        } else if (next_page && !back_page) {
            c |= CMD_PAGE_DOWN;
        } else {
            c |= vertical;  // a page jump wins over a step in the same beat
        }
    } else {
        c |= vertical;
        const bool prev = pressed(ILC_ACTION_TARGET_PREV);
        const bool next = pressed(ILC_ACTION_TARGET_NEXT);
        if (prev && !next) {
            c |= CMD_GROUP_PREV;
        } else if (next && !prev) {
            c |= CMD_GROUP_NEXT;
        } else if (!prev && !next) {
            if (left && !right) {
                c |= CMD_GROUP_PREV;
            } else if (right && !left) {
                c |= CMD_GROUP_NEXT;
            }
        }
    }
    if (pressed(ILC_ACTION_CONFIRM)) {
        c |= CMD_CONFIRM;
    }
    if (pressed(ILC_ACTION_CANCEL)) {
        c |= CMD_BACK;
    }
    return c;
}

// ---- the character selectors --------------------------------------------------------------------------------------
uint32_t selector_build(selector* s, uint8_t kind, const selector_in& in)
{
    const uint16_t keep = s->kind == kind && s->count > 0 && s->index < s->count ? s->member[s->index] : SEL_NO_MEMBER;
    const bool fresh = s->kind != kind;
    s->kind = kind;
    s->count = 0;
    s->index = 0;
    memset(s->member, 0xFF, sizeof(s->member));
    if (kind == SEL_NONE) {
        return 0;
    }
    for (uint32_t k = 0; k < SELECTOR_SLOTS; ++k) {
        const selector_member& m = in.slot[k];
        if (m.member >= SEL_REGULARS || m.able == 0) {
            continue;  // story guests, empty slots and members that cannot lead (KO, Stone) are never offered
        }
        const bool mine = m.member == in.own;
        const bool held_by_other = m.holder != SEL_NO_SEAT && m.holder != in.seat;
        bool ok = true;
        if (kind == SEL_KO) {
            ok = !mine && !held_by_other;  // a KO body is never handed to another seat
        } else if (held_by_other) {
            ok = in.own_able != 0;  // an exchange hands the seat's own character over: it must be able to lead
        }
        if (ok) {
            s->member[s->count++] = m.member;
        }
    }
    const uint16_t start = fresh ? in.own : keep;
    for (uint8_t k = 0; k < s->count; ++k) {
        if (s->member[k] == start) {
            s->index = k;
        }
    }
    return s->count;
}

int32_t selector_command(selector* s, uint32_t cmds, uint16_t* chosen)
{
    *chosen = SEL_NO_MEMBER;
    if (s->kind == SEL_NONE) {
        return SELE_NONE;
    }
    if ((cmds & (CMD_CLOSE | CMD_BACK)) != 0) {
        if (s->kind == SEL_KO) {
            return SELE_NONE;  // the KO recovery cannot be dismissed: a living character must be chosen
        }
        selector_close(s);
        return SELE_CLOSED;
    }
    if ((cmds & CMD_CONFIRM) != 0) {
        if (s->count == 0) {
            return SELE_NONE;
        }
        *chosen = s->member[s->index];
        if (s->kind != SEL_KO) {
            selector_close(s);
        }
        return SELE_CHOSEN;
    }
    if (s->count > 0 && (cmds & (CMD_UP | CMD_DOWN)) != 0) {
        const uint32_t n = s->count;
        s->index = static_cast<uint8_t>((cmds & CMD_UP) != 0 ? (s->index + n - 1) % n : (s->index + 1u) % n);
        return SELE_MOVED;
    }
    return SELE_NONE;
}

void selector_close(selector* s)
{
    s->kind = SEL_NONE;
    s->count = 0;
    s->index = 0;
    memset(s->member, 0xFF, sizeof(s->member));
}

// ---- external frontends -----------------------------------------------------------------------------------------
void suspension_begin(suspension* s, uint8_t source, uint32_t roles, uint64_t now)
{
    s->suspended = 1;
    s->source = source;
    s->observed_open = 0;
    s->roles = roles;
    s->since = now;
    s->open_since = 0;
    s->close_since = 0;
}

void suspension_clear(suspension* s)
{
    memset(s, 0, sizeof(*s));
}

int32_t suspension_step(suspension* s, const screen_in& in, uint64_t now)
{
    if (in.settling != 0) {
        s->open_since = 0;
        if (s->suspended != 0) {
            suspension_clear(s);
            return SE_ABORTED;
        }
        return SE_NONE;
    }
    if (s->suspended != 0 && in.roles != s->roles) {
        suspension_clear(s);  // someone else controls a character now: where each menu was means nothing
        return SE_ABORTED;
    }
    if (in.native_open != 0) {
        if (in.party_transfer != 0) {
            return SE_NONE;  // the Party request decides whether this screen is the one it asked for
        }
        if (s->suspended != 0) {
            s->observed_open = 1;
            s->close_since = 0;
            return SE_NONE;
        }
        if (s->open_since == 0) {
            s->open_since = now;
            return SE_NONE;
        }
        if (now - s->open_since < ADOPT_MS) {
            return SE_NONE;
        }
        suspension_begin(s, SRC_EXTERNAL, in.roles, now);
        s->observed_open = 1;
        return SE_SUSPENDED;
    }
    s->open_since = 0;
    if (s->suspended == 0 || in.party_transfer != 0 || s->observed_open == 0) {
        return SE_NONE;
    }
    if (in.modal != 0 || in.control == 0) {
        s->close_since = 0;  // the screen closed into a modal or a scripted moment: not back to play yet
        return SE_NONE;
    }
    if (s->close_since == 0) {
        s->close_since = now;
        return SE_NONE;
    }
    if (now - s->close_since >= RESUME_MS) {
        suspension_clear(s);
        return SE_RESUMED;
    }
    return SE_NONE;
}

// ---- the presenter guard while the menus are suspended -------------------------------------------------------------
uint8_t presenter_mode_of(const presenter_in& in)
{
    if (in.live != 0) {
        return PM_RUN;
    }
    if (in.settling != 0 || in.down != 0 || in.party_busy != 0) {
        return PM_IDLE;
    }
    if (in.suspended != 0) {
        return in.source != SRC_PARTY || in.observed_open != 0 ? PM_HOLD : PM_IDLE;
    }
    return in.native_open != 0 ? PM_HOLD : PM_IDLE;
}

// ---- the native Party request -------------------------------------------------------------------------------------
bool party_begin(party_request* p, uint8_t seat, uint64_t now)
{
    if (p->state != PR_IDLE || seat == 0) {
        return false;
    }
    p->state = PR_PENDING;
    p->seat = seat;
    p->drain = 0;
    p->started = now;
    p->deadline = now + PARTY_TIMEOUT_MS;
    return true;
}

void party_cancel(party_request* p, uint8_t reason)
{
    if (p->state == PR_IDLE) {
        return;
    }
    p->state = PR_IDLE;
    p->drain = 0;
    p->last = reason;
}

party_out party_step(party_request* p, const party_in& in, uint64_t now)
{
    party_out out = {};
    if (p->state == PR_IDLE) {
        return out;
    }
    if (in.tree_active != 0) {
        p->drain = 1;
    } else if (p->drain != 0 && in.native_open == 0) {
        p->drain = 0;
    }
    if (p->state == PR_PULSED && in.native_open != 0 && in.paused == 0 && in.tree_active == 0 && p->drain == 0) {
        p->state = PR_IDLE;
        p->last = PRR_OPENED;
        out.result = PRR_OPENED;
        return out;
    }
    uint8_t cancel = PRR_NONE;
    if (in.paused != 0) {
        cancel = PRR_CANCEL_PAUSE;
    } else if (in.native_open != 0 && in.tree_active == 0 && p->drain == 0) {
        cancel = PRR_CANCEL_UNEXPECTED;
    } else if (in.modal != 0) {
        cancel = PRR_CANCEL_MODAL;
    } else if (in.gate_ok == 0) {
        cancel = PRR_CANCEL_GATE;
    } else if (now >= p->deadline) {
        cancel = PRR_CANCEL_TIMEOUT;
    }
    if (cancel != PRR_NONE) {
        party_cancel(p, cancel);
        out.result = cancel;
        return out;
    }
    if (in.tree_active != 0 || p->drain != 0 || p->state == PR_PULSED || in.wait_owned != 0) {
        return out;  // the command tree closes first, then Wait gives its pause back, then one pulse
    }
    p->state = PR_PULSED;
    out.pulse = 1;
    return out;
}

// ---- the party-down recovery ------------------------------------------------------------------------------------------
bool recovery_step(recovery* r, bool party_down, bool native_open, uint64_t now)
{
    if (party_down) {
        r->latched = 1;
        r->up_since = 0;
        r->close_since = 0;
        r->observed = static_cast<uint8_t>(r->observed | (native_open ? 1 : 0));
        return true;
    }
    if (r->latched == 0) {
        return false;
    }
    if (r->up_since == 0) {
        r->up_since = now;
    }
    if (native_open) {
        r->observed = 1;
        r->close_since = 0;
        return true;
    }
    if (r->observed == 0) {
        if (now - r->up_since >= RECOVERY_QUIET_MS) {
            memset(r, 0, sizeof(*r));
            return false;
        }
        return true;
    }
    if (r->close_since == 0) {
        r->close_since = now;
        return true;
    }
    if (now - r->close_since >= RESUME_MS) {
        memset(r, 0, sizeof(*r));
        return false;
    }
    return true;
}

}  // namespace ilc::frontend
