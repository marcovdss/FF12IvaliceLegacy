// Ivalice Legacy co-op module: seat commands on the input thread. See seat_control.h for the rules.
#include "input/seat_control.h"

#include <string.h>

namespace ilc::input {
namespace {

constexpr uint8_t NO_SLOT = 0xFF;

void emit_pairing(event_sink& out, uint8_t seat, uint32_t request, uint8_t outcome, uint8_t other_seat,
                  uint8_t learned_slot, uint32_t entry)
{
    control_event e;
    memset(&e, 0, sizeof(e));
    e.kind = ILC_EV_PAIRING_RESULT;
    e.seat = seat;
    e.generation = request;
    e.a = static_cast<uint32_t>(outcome) | (static_cast<uint32_t>(other_seat) << 8) |
          (static_cast<uint32_t>(learned_slot) << 16);
    e.b = entry;
    out.emit(e);
}

void emit_capture(event_sink& out, uint8_t seat, uint32_t request, uint8_t outcome, uint8_t action, uint8_t slot,
                  uint8_t kind, uint8_t code)
{
    control_event e;
    memset(&e, 0, sizeof(e));
    e.kind = ILC_EV_CAPTURE_RESULT;
    e.seat = seat;
    e.generation = request;
    e.a = static_cast<uint32_t>(outcome) | (static_cast<uint32_t>(action) << 8) | (static_cast<uint32_t>(slot) << 16);
    e.b = static_cast<uint32_t>(kind) | (static_cast<uint32_t>(code) << 8);
    out.emit(e);
}

void emit_command(event_sink& out, uint8_t seat, uint32_t request, uint16_t command, uint32_t b)
{
    control_event e;
    memset(&e, 0, sizeof(e));
    e.kind = ILC_EV_COMMAND_RESULT;
    e.seat = seat;
    e.generation = request;
    e.a = command;
    e.b = b;
    out.emit(e);
}

bool any_a_down(const core& c)
{
    for (uint32_t n = 0; n < CORE_NODES; ++n) {
        const node_view v = core_node(c, n);
        if (v.live && v.a_down) {
            return true;
        }
    }
    return false;
}

bool group_a_down(const core& c, uint8_t group)
{
    for (uint32_t n = 0; n < CORE_NODES; ++n) {
        const node_view v = core_node(c, n);
        if (v.live && v.a_down && v.group == group) {
            return true;
        }
    }
    return false;
}

uint32_t nodes_with_a(const core& c)
{
    uint32_t mask = 0;
    for (uint32_t n = 0; n < CORE_NODES; ++n) {
        const node_view v = core_node(c, n);
        if (v.live && v.a_down) {
            mask |= 1u << n;
        }
    }
    return mask;
}

bool pairing_active(const control_state& st)
{
    return st.pairing.stage != PAIR_IDLE;
}

// Ends the pairing session; a Player 1 provisional pad is dropped with it (the change is undone).
void end_pairing(control_state& st, event_sink& out, uint8_t outcome, uint8_t other_seat)
{
    pairing_session& p = st.pairing;
    emit_pairing(out, p.seat, p.request_id, outcome, other_seat, p.learned_slot, p.proof_entry);
    if (p.seat >= 1 && p.seat <= SEATS) {
        st.reseed_now[p.seat - 1] = 1;
    }
    st.override_active = 0;
    memset(&p, 0, sizeof(p));
}

void end_capture(control_state& st, event_sink& out, uint8_t outcome, uint8_t kind, uint8_t code)
{
    capture_session& k = st.capture;
    emit_capture(out, k.seat, k.request_id, outcome, k.action, k.slot, kind, code);
    if (k.seat >= 1 && k.seat <= SEATS) {
        st.reseed_now[k.seat - 1] = 1;
    }
    memset(&k, 0, sizeof(k));
}

// Does a configured device name a pad in this group (directly, or as a live SDL member of it)?
bool device_in_group(const core& c, const seat_config& dev, uint8_t group)
{
    if (dev.kind == ILC_DEVICE_XINPUT) {
        return dev.xinput_slot < XINPUT_SLOTS && c.group[dev.xinput_slot] == group;
    }
    if (dev.kind == ILC_DEVICE_SDL) {
        for (uint32_t k = 0; k < SDL_TRACKED_MAX; ++k) {
            const node_view v = core_node(c, XINPUT_SLOTS + k);
            if (v.live && v.group == group && identity_matches(dev.sdl, c.sdl[k].id)) {
                return true;
            }
        }
    }
    return false;
}

bool same_device(const seat_config& a, const seat_config& b)
{
    if (a.kind != b.kind) {
        return false;
    }
    if (a.kind == ILC_DEVICE_XINPUT) {
        return a.xinput_slot == b.xinput_slot;
    }
    if (a.kind == ILC_DEVICE_SDL) {
        return identity_equal(a.sdl, b.sdl);
    }
    return true;
}

// The proof window closed: decide from every pad that answered.
void decide(control_state& st, const core& c, uint8_t target_pad, int32_t target_node, event_sink& out)
{
    pairing_session& p = st.pairing;
    uint32_t others = 0;  // bit per other group root
    bool unknown_sdl = false;
    for (uint32_t n = 0; n < CORE_NODES; ++n) {
        if ((p.answered & (1u << n)) == 0) {
            continue;
        }
        const node_view v = core_node(c, n);
        if (v.group == target_pad) {
            continue;
        }
        others |= 1u << v.group;
        if (n >= XINPUT_SLOTS && v.auto_slot < 0) {
            unknown_sdl = true;  // an SDL pad that is not just SDL's own view of an XInput slot
        }
    }
    p.learned_slot = NO_SLOT;
    if (others != 0) {
        const bool single = (others & (others - 1u)) == 0;
        const uint32_t g = static_cast<uint32_t>(__builtin_ctz(others));
        if (single && g < XINPUT_SLOTS && target_pad >= XINPUT_SLOTS && !unknown_sdl) {
            p.learned_slot = static_cast<uint8_t>(g);  // the target's XInput mirror: learn the alias
        } else {
            end_pairing(st, out, ILC_PAIR_TWO_PADS, 0);
            return;
        }
    }
    const uint8_t target_owner = core_group_owner(c, target_pad);
    const uint8_t mirror_owner = p.learned_slot != NO_SLOT ? core_group_owner(c, p.learned_slot) : 0;
    const uint8_t seat = p.seat;
    if (seat >= 2) {
        if (target_owner == 1 || mirror_owner == 1) {
            end_pairing(st, out, ILC_PAIR_P1_LOCKED, 1);
        } else if (target_owner >= 2 && target_owner != seat) {
            end_pairing(st, out, ILC_PAIR_OWNED_BY_SEAT, target_owner);
        } else if (mirror_owner >= 2 && mirror_owner != seat) {
            end_pairing(st, out, ILC_PAIR_OWNED_BY_SEAT, mirror_owner);
        } else {
            end_pairing(st, out, ILC_PAIR_READY, 0);
        }
        return;
    }
    // Player 1.
    if (target_owner == 1 || mirror_owner == 1) {
        end_pairing(st, out, ILC_PAIR_IDENTIFIED, 0);
        return;
    }
    if (target_owner >= 2 || mirror_owner >= 2) {
        end_pairing(st, out, ILC_PAIR_OWNED_BY_SEAT, target_owner >= 2 ? target_owner : mirror_owner);
        return;
    }
    // The guarded change: the new pad drives Player 1 from the next tick; a fresh A on it keeps it.
    memset(&st.override_device, 0, sizeof(st.override_device));
    if (target_node < static_cast<int32_t>(XINPUT_SLOTS)) {
        st.override_device.kind = ILC_DEVICE_XINPUT;
        st.override_device.xinput_slot = static_cast<uint8_t>(target_node);
    } else {
        st.override_device.kind = ILC_DEVICE_SDL;
        st.override_device.sdl = c.sdl[target_node - XINPUT_SLOTS].id;
    }
    st.override_active = 1;
    p.stage = PAIR_CONFIRM_RELEASE;  // the caller stamps stage_us
    emit_pairing(out, p.seat, p.request_id, ILC_PAIR_PROVISIONAL, 0, p.learned_slot, p.proof_entry);
}

void pairing_step(control_state& st, const core& c, uint64_t now, event_sink& out)
{
    pairing_session& p = st.pairing;
    if (p.stage == PAIR_IDLE) {
        return;
    }
    const int32_t node = core_node_of_entry(c, p.proof_entry);
    if (node < 0) {
        end_pairing(st, out, ILC_PAIR_DISCONNECTED, 0);
        return;
    }
    const uint8_t group = core_node(c, static_cast<uint32_t>(node)).group;
    switch (p.stage) {
    case PAIR_WAIT_RELEASE:
    case PAIR_ARMED:
    case PAIR_PROOF:
        if (now - p.started_us >= PAIRING_TIMEOUT_US) {
            end_pairing(st, out, p.ever_armed ? ILC_PAIR_TIMEOUT : ILC_PAIR_HELD_A, 0);
            return;
        }
        if (p.stage == PAIR_WAIT_RELEASE) {
            if (!any_a_down(c)) {
                p.stage = PAIR_ARMED;
                p.ever_armed = 1;
            }
        } else if (p.stage == PAIR_ARMED) {
            if (group_a_down(c, group)) {
                p.stage = PAIR_PROOF;
                p.stage_us = now;
                p.answered = nodes_with_a(c);
            }
        } else {
            p.answered |= nodes_with_a(c);
            if (now - p.stage_us >= PROOF_WINDOW_US) {
                decide(st, c, group, node, out);
                if (p.stage == PAIR_CONFIRM_RELEASE) {
                    p.stage_us = now;
                }
            }
        }
        return;
    case PAIR_CONFIRM_RELEASE:
    case PAIR_CONFIRM_PRESS:
        if (now - p.stage_us >= CONFIRM_WINDOW_US) {
            end_pairing(st, out, ILC_PAIR_REVERTED, 0);
            return;
        }
        if (p.stage == PAIR_CONFIRM_RELEASE) {
            if (!group_a_down(c, group)) {
                p.stage = PAIR_CONFIRM_PRESS;
            }
        } else if (group_a_down(c, group)) {
            emit_pairing(out, p.seat, p.request_id, ILC_PAIR_CONFIRMED, 0, p.learned_slot, p.proof_entry);
            p.stage = PAIR_COMMIT_WAIT;
            p.stage_us = now;
        }
        return;
    case PAIR_COMMIT_WAIT:
        if (now - p.stage_us >= COMMIT_WINDOW_US) {
            end_pairing(st, out, ILC_PAIR_REVERTED, 0);
        }
        return;
    default:
        end_pairing(st, out, ILC_PAIR_CANCELLED, 0);
        return;
    }
}

void capture_step(control_state& st, const core& c, const key_state& keys, bool focused, uint8_t kbm_seat,
                  uint64_t now, event_sink& out)
{
    capture_session& k = st.capture;
    if (!k.active) {
        return;
    }
    const ilc_seat_input_frame& frame = c.frames[k.seat - 1];
    const bool pad_live = (frame.flags & ILC_FRAMEF_CONNECTED) != 0;
    // Hysteresis: a control counts as held at the start from 0.55, and a capture needs a firm press of 0.72, so
    // a resting trigger or a stick slightly off centre is neither captured nor blocks a capture forever.
    uint32_t held = 0, firm = 0;
    if (pad_live) {
        for (uint32_t i = 0; i < ILC_PAD_CONTROL_COUNT; ++i) {
            if (frame.strength[i] >= CAPTURE_REST_Q16) {
                held |= 1u << i;
            }
            if (frame.strength[i] >= CAPTURE_FIRM_Q16) {
                firm |= 1u << i;
            }
        }
    }
    const uint32_t usable = firm & ~frame.absorbed;
    const bool keys_on = kbm_seat == k.seat && focused;
    if (k.baseline_pending) {
        k.pad_baseline = held;
        for (uint32_t i = 0; i < 8; ++i) {
            k.key_baseline.down[i] = keys_on ? keys.down[i] : 0xFFFFFFFFu;
        }
        k.baseline_pending = 0;
        return;  // never capture on the start tick
    }
    if (now - k.started_us >= CAPTURE_TIMEOUT_US) {
        end_capture(st, out, ILC_CAPT_TIMEOUT, ILC_BIND_NONE, 0);
        return;
    }
    k.pad_baseline &= held;
    for (uint32_t i = 0; i < 8; ++i) {
        // Unfocused: nothing can be read, so whatever is down when the window comes back must be released first.
        k.key_baseline.down[i] = keys_on ? (k.key_baseline.down[i] & keys.down[i]) : 0xFFFFFFFFu;
    }
    const uint32_t fresh_pad = usable & ~k.pad_baseline;
    if (fresh_pad != 0) {
        const uint8_t code = static_cast<uint8_t>(__builtin_ctz(fresh_pad) + 1);
        end_capture(st, out, ILC_CAPT_CAPTURED, ILC_BIND_PAD, code);
        return;
    }
    if (!keys_on) {
        return;
    }
    for (uint32_t vk = 1; vk < 255; ++vk) {
        if (vk == 0x10 || vk == 0x11 || vk == 0x12) {
            continue;  // generic Shift/Ctrl/Alt: the left/right keys are captured instead
        }
        if (key_is_down(keys, vk) && !key_is_down(k.key_baseline, vk)) {
            const bool mouse = vk == 1 || vk == 2;
            end_capture(st, out, ILC_CAPT_CAPTURED, mouse ? ILC_BIND_MOUSE : ILC_BIND_KEY, static_cast<uint8_t>(vk));
            return;
        }
    }
}

}  // namespace

void control_reset(control_state& st)
{
    memset(&st, 0, sizeof(st));
    for (uint32_t s = 0; s < SEATS; ++s) {
        st.live_park[s] = -1;
    }
}

void control_on_settings(control_state& st, const core& c, const config& old_cfg, const config& new_cfg,
                         event_sink& out)
{
    for (uint32_t s = 0; s < SEATS; ++s) {
        if (old_cfg.seats[s].parked != new_cfg.seats[s].parked) {
            st.live_park[s] = -1;  // the settings take over again
        }
    }
    if (!st.override_active || same_device(old_cfg.seats[0], new_cfg.seats[0])) {
        return;
    }
    // Player 1's configured pad changed during the guarded change.
    const int32_t node = core_node_of_entry(c, st.pairing.proof_entry);
    const bool names_target = node >= 0 ? device_in_group(c, new_cfg.seats[0], core_node(c, node).group)
                                        : same_device(new_cfg.seats[0], st.override_device);
    if (names_target && st.pairing.stage == PAIR_COMMIT_WAIT) {
        // The commit: the settings now name the new pad. The session ends quietly (CONFIRMED was sent).
        st.override_active = 0;
        st.reseed_now[0] = 1;
        memset(&st.pairing, 0, sizeof(st.pairing));
        return;
    }
    end_pairing(st, out, ILC_PAIR_CANCELLED, 0);  // the settings chose otherwise: they win
}

void control_command(control_state& st, const core& c, const command_msg& m, uint32_t session, uint8_t kbm_seat,
                     uint64_t now, event_sink& out)
{
    switch (m.command) {
    case ILC_CMD_PARK: {
        for (uint32_t s = 1; s < SEATS; ++s) {
            if (m.seat == 0 || m.seat == s + 1) {
                st.live_park[s] = m.arg0 ? 1 : 0;
            }
        }
        uint32_t mask = 0;
        for (uint32_t s = 0; s < SEATS; ++s) {
            const bool parked = st.live_park[s] >= 0 ? st.live_park[s] != 0 : c.cfg.seats[s].parked != 0;
            if (s != 0 && parked) {
                mask |= 1u << s;
            }
        }
        emit_command(out, m.seat, m.request_id, m.command, mask);
        return;
    }
    case ILC_CMD_MOVE_SEAT_NOW:
        if (m.seat >= 1 && m.seat <= SEATS) {
            st.reseed_now[m.seat - 1] = 1;
            st.move_request[m.seat - 1] = m.request_id;
            st.move_character[m.seat - 1] = static_cast<uint8_t>(m.arg0);
        }
        emit_command(out, m.seat, m.request_id, m.command, (m.arg0 & 0xFFu) | MOVE_QUEUED);
        return;
    case ILC_CMD_PAIRING_START: {
        if (pairing_active(st)) {
            end_pairing(st, out, ILC_PAIR_CANCELLED, 0);  // a newer start wins
        }
        if (m.seat < 1 || m.seat > SEATS) {
            return;  // the bridge validated the seat
        }
        if (m.arg0 != session) {
            emit_pairing(out, m.seat, m.request_id, ILC_PAIR_STALE_SESSION, 0, NO_SLOT, m.arg1);
            return;
        }
        const int32_t node = core_node_of_entry(c, m.arg1);
        if (node < 0) {
            emit_pairing(out, m.seat, m.request_id, ILC_PAIR_NOT_FOUND, 0, NO_SLOT, m.arg1);
            return;
        }
        const uint8_t owner = core_group_owner(c, core_node(c, static_cast<uint32_t>(node)).group);
        if (m.seat >= 2 && owner == 1) {
            emit_pairing(out, m.seat, m.request_id, ILC_PAIR_P1_LOCKED, 1, NO_SLOT, m.arg1);
            return;
        }
        if (owner >= 2 && owner != m.seat) {
            emit_pairing(out, m.seat, m.request_id, ILC_PAIR_OWNED_BY_SEAT, owner, NO_SLOT, m.arg1);
            return;
        }
        pairing_session& p = st.pairing;
        memset(&p, 0, sizeof(p));
        p.stage = PAIR_WAIT_RELEASE;
        p.seat = m.seat;
        p.learned_slot = NO_SLOT;
        p.request_id = m.request_id;
        p.proof_entry = m.arg1;
        p.started_us = now;
        p.stage_us = now;
        return;
    }
    case ILC_CMD_PAIRING_CANCEL:
        if (pairing_active(st) && st.pairing.seat == m.seat) {
            end_pairing(st, out, ILC_PAIR_CANCELLED, 0);
        } else {
            emit_pairing(out, m.seat, m.request_id, ILC_PAIR_CANCELLED, 0, NO_SLOT, 0);
        }
        return;
    case ILC_CMD_CAPTURE_START: {
        if (st.capture.active) {
            end_capture(st, out, ILC_CAPT_CANCELLED, ILC_BIND_NONE, 0);
        }
        if (m.seat < 1 || m.seat > SEATS) {
            return;
        }
        const bool pad_live = (c.frames[m.seat - 1].flags & ILC_FRAMEF_CONNECTED) != 0;
        if (!pad_live && kbm_seat != m.seat) {
            emit_capture(out, m.seat, m.request_id, ILC_CAPT_NO_INPUT, static_cast<uint8_t>(m.arg0),
                         static_cast<uint8_t>(m.arg1), ILC_BIND_NONE, 0);
            return;
        }
        capture_session& k = st.capture;
        memset(&k, 0, sizeof(k));
        k.active = 1;
        k.seat = m.seat;
        k.action = static_cast<uint8_t>(m.arg0);
        k.slot = static_cast<uint8_t>(m.arg1);
        k.baseline_pending = 1;
        k.request_id = m.request_id;
        k.started_us = now;
        return;
    }
    case ILC_CMD_CAPTURE_CANCEL:
        if (st.capture.active && st.capture.seat == m.seat) {
            end_capture(st, out, ILC_CAPT_CANCELLED, ILC_BIND_NONE, 0);
        } else {
            emit_capture(out, m.seat, m.request_id, ILC_CAPT_CANCELLED, 0, 0, ILC_BIND_NONE, 0);
        }
        return;
    default:
        return;  // the bridge answers the other commands itself
    }
}

void control_step(control_state& st, const core& c, const key_state& keys, bool focused, uint8_t kbm_seat,
                  uint64_t now, event_sink& out)
{
    pairing_step(st, c, now, out);
    capture_step(st, c, keys, focused, kbm_seat, now, out);
}

void control_shutdown(control_state& st, event_sink& out)
{
    if (pairing_active(st)) {
        end_pairing(st, out, ILC_PAIR_CANCELLED, 0);
    }
    if (st.capture.active) {
        end_capture(st, out, ILC_CAPT_CANCELLED, ILC_BIND_NONE, 0);
    }
    st.override_active = 0;
}

void control_effective(const control_state& st, const config& settings, config* out)
{
    *out = settings;
    for (uint32_t s = 1; s < SEATS; ++s) {  // Player 1 is never parked
        if (st.live_park[s] >= 0) {
            out->seats[s].parked = static_cast<uint8_t>(st.live_park[s]);
        }
    }
    if (st.override_active) {
        out->seats[0].kind = st.override_device.kind;
        out->seats[0].xinput_slot = st.override_device.xinput_slot;
        out->seats[0].sdl = st.override_device.sdl;
    }
}

void control_suppressed(const control_state& st, uint8_t out[SEATS])
{
    memset(out, 0, SEATS);
    if (pairing_active(st) && st.pairing.seat >= 1 && st.pairing.seat <= SEATS) {
        out[st.pairing.seat - 1] = 1;
    }
    if (st.capture.active && st.capture.seat >= 1 && st.capture.seat <= SEATS) {
        out[st.capture.seat - 1] = 1;
    }
}

bool control_wants_all_keys(const control_state& st, uint8_t kbm_seat)
{
    return st.capture.active && st.capture.seat == kbm_seat;
}

}  // namespace ilc::input
