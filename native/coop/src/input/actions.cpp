// Ivalice Legacy co-op module: the action layer. See actions.h for the rules.
#include "input/actions.h"

#include <string.h>

namespace ilc::input {
namespace {

constexpr uint32_t FACE_AB = (1u << (ILC_PAD_A - 1)) | (1u << (ILC_PAD_B - 1));

uint16_t max_strength(uint16_t a, uint16_t b)
{
    return a > b ? a : b;
}

void seat_neutral(ilc_seat_action_frame& f, uint32_t seat, uint32_t seq, uint64_t now)
{
    memset(&f, 0, sizeof(f));
    f.seq = seq;
    f.time_us = now;
    f.seat = static_cast<uint8_t>(seat + 1);
}

}  // namespace

ilc_binding effective_binding(const ilc_binding& b, uint32_t action, bool decide_swap)
{
    ilc_binding e = b;
    if (decide_swap && b.kind == ILC_BIND_PAD && (action == ILC_ACTION_CONFIRM || action == ILC_ACTION_CANCEL)) {
        if (b.code == ILC_PAD_A) {
            e.code = ILC_PAD_B;
        } else if (b.code == ILC_PAD_B) {
            e.code = ILC_PAD_A;
        }
    }
    return e;
}

void actions_reset(action_layer& st)
{
    memset(&st, 0, sizeof(st));
}

void actions_wanted_keys(const config& cfg, key_state* out)
{
    memset(out, 0, sizeof(*out));
    if (cfg.kbm_seat < 1 || cfg.kbm_seat > SEATS) {
        return;
    }
    for (const ilc_binding& b : cfg.bindings[cfg.kbm_seat - 1]) {
        if (b.kind == ILC_BIND_KEY || b.kind == ILC_BIND_MOUSE) {
            key_set(*out, b.code);  // MOUSE 1/2 are VK_LBUTTON/VK_RBUTTON
        }
    }
}

void actions_step(action_layer& st, const action_inputs& in, uint64_t now, uint32_t seq,
                  ilc_seat_action_frame out[SEATS])
{
    const config& cfg = *in.cfg;
    const uint8_t kbm = cfg.kbm_seat <= SEATS ? cfg.kbm_seat : 0;
    const bool focus_regained = st.started && in.focused && !st.last_focused;
    const bool kbm_moved = st.started && kbm != st.last_kbm;
    const bool swap_changed = st.started && (in.decide_swap != 0) != (st.last_swap != 0);

    for (uint32_t s = 0; s < SEATS; ++s) {
        seat_action_state& ss = st.seats[s];
        const ilc_seat_input_frame& frame = in.frames[s];
        ilc_seat_action_frame& f = out[s];
        seat_neutral(f, s, seq, now);
        f.source_generation = frame.source_generation;

        const bool pad_live = (frame.flags & ILC_FRAMEF_CONNECTED) != 0;
        const bool owns_kbm = kbm == s + 1;
        const bool keys_on = owns_kbm && in.focused;
        const bool tables_changed = memcmp(ss.bindings, cfg.bindings[s], sizeof(ss.bindings)) != 0;
        const bool suppressed = in.suppress[s] != 0;
        const bool unsuppressed = ss.suppressed && !suppressed;

        const bool reseed_all = !st.started || tables_changed || in.reseed[s] || unsuppressed;
        const bool reseed_keys = reseed_all || (kbm_moved && (owns_kbm || st.last_kbm == s + 1)) ||
                                 (focus_regained && owns_kbm);

        // Absorption is per control and per key: what is held at a discontinuity produces no edge until it is
        // released. The pad frame already absorbed device changes (frame.absorbed); this layer adds its own.
        const uint32_t controls = pad_live ? frame.held : 0u;
        if (reseed_all) {
            ss.pad_absorbed |= controls;
        } else if (swap_changed) {
            ss.pad_absorbed |= controls & FACE_AB;  // a confirm held across a decide swap
        }
        ss.pad_absorbed &= controls;
        const uint32_t edge_controls = controls & ~frame.absorbed & ~ss.pad_absorbed;
        key_state keys;
        key_state edge_keys;
        for (uint32_t i = 0; i < 8; ++i) {
            keys.down[i] = keys_on ? in.keys.down[i] : 0u;
            if (reseed_keys) {
                ss.key_absorbed.down[i] |= keys.down[i];
            }
            ss.key_absorbed.down[i] &= keys.down[i];
            edge_keys.down[i] = keys.down[i] & ~ss.key_absorbed.down[i];
        }

        // Bindings: held (raw, for steering and other continuous use) and the parts that may make edges.
        uint32_t held = 0, pad_edge = 0, key_edge = 0, key_held = 0;
        uint16_t pad_strength[ILC_ACTIONS];
        memset(pad_strength, 0, sizeof(pad_strength));
        for (uint32_t a = 0; a < ILC_ACTIONS; ++a) {
            const uint32_t bit = 1u << a;
            for (uint32_t k = 0; k < ILC_BINDINGS_PER_ACTION; ++k) {
                const ilc_binding b =
                    effective_binding(cfg.bindings[s][a * ILC_BINDINGS_PER_ACTION + k], a, in.decide_swap != 0);
                if (b.kind == ILC_BIND_PAD) {
                    if (b.code < 1 || b.code > ILC_PAD_CONTROL_COUNT) {
                        continue;
                    }
                    const uint32_t control = ilc_pad_bit(static_cast<ilc_pad_control>(b.code));
                    if (controls & control) {
                        held |= bit;
                        pad_strength[a] = max_strength(pad_strength[a], frame.strength[b.code - 1]);
                        if (edge_controls & control) {
                            pad_edge |= bit;
                        }
                    }
                } else if (b.kind == ILC_BIND_KEY || b.kind == ILC_BIND_MOUSE) {
                    if (key_is_down(keys, b.code)) {
                        held |= bit;
                        key_held |= bit;
                        if (key_is_down(edge_keys, b.code)) {
                            key_edge |= bit;
                        }
                    }
                }
            }
        }

        uint8_t flags = 0;
        if (pad_live) {
            flags |= ILC_ACTF_PAD;
        }
        if (owns_kbm) {
            flags |= ILC_ACTF_KBM;
        }
        if (in.focused) {
            flags |= ILC_ACTF_FOCUSED;
        }
        if (reseed_all || reseed_keys || swap_changed || (frame.flags & ILC_FRAMEF_RESEEDED)) {
            flags |= ILC_ACTF_RESEEDED;
        }
        const bool fast = in.fast[s] != 0;
        if (fast) {
            flags |= ILC_ACTF_FAST;
        }

        if (suppressed) {
            // Pairing or capture owns the seat: nothing leaks. Timers stop; the end of the suppression reseeds.
            flags |= ILC_ACTF_SUPPRESSED;
            f.flags = flags;
            f.prompt_device = ss.prompt_device;
            ss.prev_held = 0;
            ss.prev_pad = 0;
            ss.prev_key = 0;
            ss.prev_edge = 0;
            memset(ss.pulsed, 0, sizeof(ss.pulsed));
            ss.suppressed = 1;
            ss.fast = fast ? 1 : 0;
            memcpy(ss.bindings, cfg.bindings[s], sizeof(ss.bindings));
            continue;
        }

        const uint32_t edge = pad_edge | key_edge;
        const uint32_t pressed = edge & ~ss.prev_edge;
        const uint32_t released = ss.prev_held & ~held;
        if ((pad_edge & ~ss.prev_pad) != 0) {
            ss.prompt_device = ILC_PROMPT_PAD;
        } else if ((key_edge & ~ss.prev_key) != 0) {
            ss.prompt_device = ILC_PROMPT_KEYBOARD;
        }

        uint32_t repeated = 0;
        const bool fast_turned_on = fast && !ss.fast;
        for (uint32_t a = 0; a < ILC_ACTIONS; ++a) {
            const uint32_t bit = 1u << a;
            if ((REPEAT_ACTIONS & bit) == 0) {
                continue;
            }
            if (pressed & bit) {
                ss.pulsed[a] = 0;
                ss.repeat_due_us[a] = now + (fast ? FAST_FIRST_US : REPEAT_FIRST_US);
                continue;
            }
            if ((edge & bit) == 0) {
                ss.pulsed[a] = 0;
                ss.repeat_due_us[a] = 0;
                continue;
            }
            if (fast_turned_on && now + FAST_NEXT_US < ss.repeat_due_us[a]) {
                ss.repeat_due_us[a] = now + FAST_NEXT_US;  // fast on caps the pending slow timer
            }
            if (now >= ss.repeat_due_us[a]) {
                repeated |= bit;
                ss.pulsed[a] = 1;
                const uint64_t interval = fast ? FAST_NEXT_US : REPEAT_NEXT_US;
                ss.repeat_due_us[a] += interval;
                if (ss.repeat_due_us[a] <= now) {
                    ss.repeat_due_us[a] = now + interval;  // after a stall: one pulse, then the normal cadence
                }
            }
        }

        f.flags = flags;
        f.prompt_device = ss.prompt_device;
        f.held = held;
        f.pressed = pressed;
        f.released = released;
        f.repeated = repeated;
        f.absorbed = held & ~edge;
        for (uint32_t a = 0; a < ILC_ACTIONS; ++a) {
            const uint32_t bit = 1u << a;
            if (key_held & bit) {
                f.strength[a] = 65535;
            } else if (held & bit) {
                f.strength[a] = pad_strength[a];
            }
        }

        ss.prev_held = held;
        ss.prev_edge = edge;
        ss.prev_pad = pad_edge;
        ss.prev_key = key_edge;
        ss.suppressed = 0;
        ss.fast = fast ? 1 : 0;
        memcpy(ss.bindings, cfg.bindings[s], sizeof(ss.bindings));
    }
    st.started = 1;
    st.last_focused = in.focused;
    st.last_kbm = kbm;
    st.last_swap = in.decide_swap;
}

}  // namespace ilc::input
