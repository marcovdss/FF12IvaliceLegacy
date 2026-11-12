// Ivalice Legacy co-op module: the settings the module runs on until the Lua page sends its first record.
//
// They match the Mod Menu defaults: P1 on XInput 0, guests without a pad, keyboard and mouse on
// P2, P2 Basch, P3 Balthier, P4 and P1 automatic, the canonical colour set, every battle sound on, and the
// follower binding tables. constexpr, so the module holds them as constant data (no start-up code) and the
// schema generator emits the same bytes into schema/bridge-abi-v1.json for the Lua seed to compare against.
#pragma once

#include "ilc/bridge_abi.h"

namespace ilc {

constexpr void put_binding(ilc_seat_settings& seat, uint32_t action, uint8_t kind, uint8_t code)
{
    for (uint32_t slot = 0; slot < ILC_BINDINGS_PER_ACTION; ++slot) {
        ilc_binding& b = seat.bindings[action * ILC_BINDINGS_PER_ACTION + slot];
        if (b.kind == ILC_BIND_NONE) {
            b.kind = kind;
            b.code = code;
            return;
        }
    }
}

constexpr void key(ilc_seat_settings& seat, uint32_t action, uint8_t vk)
{
    put_binding(seat, action, ILC_BIND_KEY, vk);
}

constexpr void pad(ilc_seat_settings& seat, uint32_t action, uint8_t control)
{
    put_binding(seat, action, ILC_BIND_PAD, control);
}

constexpr void mouse(ilc_seat_settings& seat, uint32_t action, uint8_t button)
{
    put_binding(seat, action, ILC_BIND_MOUSE, button);
}

// Guest layout: keyboard (shared; only the kbm_seat's keys count) and the seat's own pad.
constexpr void guest_bindings(ilc_seat_settings& seat, uint8_t toggle_vk, uint8_t quickening_pad)
{
    key(seat, ILC_ACTION_TOGGLE_CONTROL, toggle_vk);
    key(seat, ILC_ACTION_MOVE_UP, 'W');
    key(seat, ILC_ACTION_MOVE_DOWN, 'S');
    key(seat, ILC_ACTION_MOVE_LEFT, 'A');
    key(seat, ILC_ACTION_MOVE_RIGHT, 'D');
    pad(seat, ILC_ACTION_MOVE_UP, ILC_PAD_LS_UP);
    pad(seat, ILC_ACTION_MOVE_DOWN, ILC_PAD_LS_DOWN);
    pad(seat, ILC_ACTION_MOVE_LEFT, ILC_PAD_LS_LEFT);
    pad(seat, ILC_ACTION_MOVE_RIGHT, ILC_PAD_LS_RIGHT);
    key(seat, ILC_ACTION_WALK_RUN, 0xA0);  // left Shift
    pad(seat, ILC_ACTION_WALK_RUN, ILC_PAD_L3);
    key(seat, ILC_ACTION_FLEE, 0xA2);  // left Ctrl
    pad(seat, ILC_ACTION_FLEE, ILC_PAD_RT);
    key(seat, ILC_ACTION_BATTLE_MENU, 'F');
    pad(seat, ILC_ACTION_BATTLE_MENU, ILC_PAD_X);
    key(seat, ILC_ACTION_MENU_UP, 'I');
    key(seat, ILC_ACTION_MENU_UP, 0x68);  // numpad 8
    pad(seat, ILC_ACTION_MENU_UP, ILC_PAD_DPAD_UP);
    key(seat, ILC_ACTION_MENU_DOWN, 'K');
    key(seat, ILC_ACTION_MENU_DOWN, 0x62);  // numpad 2
    pad(seat, ILC_ACTION_MENU_DOWN, ILC_PAD_DPAD_DOWN);
    key(seat, ILC_ACTION_MENU_LEFT, 'J');
    key(seat, ILC_ACTION_MENU_LEFT, 0x64);  // numpad 4
    key(seat, ILC_ACTION_MENU_LEFT, 'Q');
    pad(seat, ILC_ACTION_MENU_LEFT, ILC_PAD_DPAD_LEFT);
    key(seat, ILC_ACTION_MENU_RIGHT, 'L');
    key(seat, ILC_ACTION_MENU_RIGHT, 0x66);  // numpad 6
    key(seat, ILC_ACTION_MENU_RIGHT, 'E');
    pad(seat, ILC_ACTION_MENU_RIGHT, ILC_PAD_DPAD_RIGHT);
    key(seat, ILC_ACTION_TARGET_PREV, '1');
    pad(seat, ILC_ACTION_TARGET_PREV, ILC_PAD_LB);
    key(seat, ILC_ACTION_TARGET_NEXT, '3');
    pad(seat, ILC_ACTION_TARGET_NEXT, ILC_PAD_RB);
    key(seat, ILC_ACTION_QUICKENING, 'F');
    pad(seat, ILC_ACTION_QUICKENING, quickening_pad);
    key(seat, ILC_ACTION_CONFIRM, 0x20);  // Space
    key(seat, ILC_ACTION_CONFIRM, 0x0D);  // Enter
    mouse(seat, ILC_ACTION_CONFIRM, 1);
    pad(seat, ILC_ACTION_CONFIRM, ILC_PAD_A);
    key(seat, ILC_ACTION_CANCEL, 'C');
    key(seat, ILC_ACTION_CANCEL, 0x08);  // Backspace
    mouse(seat, ILC_ACTION_CANCEL, 2);
    pad(seat, ILC_ACTION_CANCEL, ILC_PAD_B);
    key(seat, ILC_ACTION_PARTY, 'R');
    pad(seat, ILC_ACTION_PARTY, ILC_PAD_Y);
    key(seat, ILC_ACTION_PAUSE, 0x1B);  // Esc
    pad(seat, ILC_ACTION_PAUSE, ILC_PAD_START);
}

constexpr ilc_settings default_settings()
{
    ilc_settings s{};
    s.header.magic = ILC_ABI_MAGIC;
    s.header.abi_version = ILC_ABI_VERSION;
    s.header.record_type = ILC_REC_SETTINGS;
    s.header.size = sizeof(ilc_settings);
    s.header.generation = 0;  // 0 = native defaults; every record Lua sends is >= 1
    s.module_enabled_at_start = 1;
    s.all_characters_at_start = 1;
    s.kbm_seat = 2;
    s.colour_mode = ILC_COLOUR_MODE_CHARACTER;
    s.character_colour[ILC_CHAR_VAAN] = ILC_COLOUR_SAPPHIRE;
    s.character_colour[ILC_CHAR_ASHE] = ILC_COLOUR_PINK;
    s.character_colour[ILC_CHAR_FRAN] = ILC_COLOUR_AMETHYST;
    s.character_colour[ILC_CHAR_BALTHIER] = ILC_COLOUR_BRONZE;
    s.character_colour[ILC_CHAR_BASCH] = ILC_COLOUR_EMERALD;
    s.character_colour[ILC_CHAR_PENELO] = ILC_COLOUR_GOLD;
    s.subsystems_armed = static_cast<uint32_t>(ILC_ENUM_OR(ILC_SUBSYSTEMS));  // all armed; bisect by clearing
    s.kill_switch[0].kind = ILC_BIND_KEY;
    s.kill_switch[0].code = 0x77;  // F8

    const uint8_t characters[ILC_SEATS] = {ILC_CHAR_AUTO, ILC_CHAR_BASCH, ILC_CHAR_BALTHIER, ILC_CHAR_AUTO};
    const uint8_t colours[ILC_SEATS] = {ILC_COLOUR_SAPPHIRE, ILC_COLOUR_EMERALD, ILC_COLOUR_BRONZE,
                                        ILC_COLOUR_AMETHYST};
    for (uint32_t i = 0; i < ILC_SEATS; ++i) {
        s.seats[i].character = characters[i];
        s.seats[i].colour = colours[i];
        s.seats[i].seat_ui_sounds = 1;
    }

    ilc_seat_settings& p1 = s.seats[0];
    p1.device.kind = ILC_DEVICE_XINPUT;
    p1.device.xinput_slot = 0;
    pad(p1, ILC_ACTION_MENU_UP, ILC_PAD_DPAD_UP);
    pad(p1, ILC_ACTION_MENU_DOWN, ILC_PAD_DPAD_DOWN);
    pad(p1, ILC_ACTION_MENU_LEFT, ILC_PAD_DPAD_LEFT);
    pad(p1, ILC_ACTION_MENU_LEFT, ILC_PAD_LB);
    pad(p1, ILC_ACTION_MENU_RIGHT, ILC_PAD_DPAD_RIGHT);
    pad(p1, ILC_ACTION_MENU_RIGHT, ILC_PAD_RB);
    pad(p1, ILC_ACTION_CONFIRM, ILC_PAD_A);
    pad(p1, ILC_ACTION_CANCEL, ILC_PAD_B);
    pad(p1, ILC_ACTION_PARTY, ILC_PAD_Y);
    pad(p1, ILC_ACTION_PAUSE, ILC_PAD_START);

    // FF12's Quickening slot buttons: P2 Square (X), P3 Cross (A). P4 keeps no bindings: only
    // ILC_ACTIVE_SEATS_MAX seats play in this release.
    guest_bindings(s.seats[1], 0x75, ILC_PAD_X);  // F6
    guest_bindings(s.seats[2], 0x76, ILC_PAD_A);  // F7
    static_assert(ILC_ACTIVE_SEATS_MAX == 3, "defaults give bindings to exactly the active seats");
    return s;
}

}  // namespace ilc
