// Ivalice Legacy co-op module: the device APIs the input service reads through.
//
// The service never calls XInput or SDL directly. It calls these two small interfaces, so the same service code
// runs on real devices (input_live.cpp) and on a scripted or recorded device world in host tests
// (input_replay.cpp). Every value here is plain data so a recorder can store the answers and a replay can give
// them back call for call.
#pragma once

#include <stdint.h>

#include "ilc/seat_input.h"

namespace ilc::input {

inline constexpr uint32_t XINPUT_OK = 0;                     // ERROR_SUCCESS
inline constexpr uint32_t XINPUT_NOT_CONNECTED = 1167;       // ERROR_DEVICE_NOT_CONNECTED
inline constexpr uint32_t SDL_PATH_MAX = 260;
inline constexpr uint32_t SDL_NAME_MAX = 128;

struct xinput_reading {
    uint32_t packet;
    ilc_xinput_gamepad pad;
};

class xinput_api {
public:
    // XInputGetState for user index 0..3. Returns the Win32 result code (XINPUT_OK on success).
    virtual uint32_t get_state(uint32_t slot, xinput_reading* out) = 0;

protected:
    ~xinput_api() = default;
};

// SDL's game controller buttons and axes, by SDL's own numbering (SDL_GameControllerButton/Axis).
enum sdl_button : uint32_t {
    SDLB_A = 0, SDLB_B, SDLB_X, SDLB_Y, SDLB_BACK, SDLB_GUIDE, SDLB_START, SDLB_LEFTSTICK, SDLB_RIGHTSTICK,
    SDLB_LEFTSHOULDER, SDLB_RIGHTSHOULDER, SDLB_DPAD_UP, SDLB_DPAD_DOWN, SDLB_DPAD_LEFT, SDLB_DPAD_RIGHT,
    SDLB_COUNT_USED,
};
enum sdl_axis : uint32_t {
    SDLA_LEFTX = 0, SDLA_LEFTY, SDLA_RIGHTX, SDLA_RIGHTY, SDLA_TRIGGERLEFT, SDLA_TRIGGERRIGHT, SDLA_COUNT,
};

struct sdl_device_info {
    int32_t instance_id;        // SDL joystick instance id
    uint8_t guid[16];           // SDL joystick GUID
    uint8_t is_controller;      // SDL has a game controller mapping for it
    uint8_t reserved[3];
    char path[SDL_PATH_MAX];    // OS device path, NUL-terminated ("" when SDL has none)
    char name[SDL_NAME_MAX];    // NUL-terminated, external text: sanitized before use
};

struct sdl_reading {
    uint32_t buttons;           // bit n = sdl_button n is down
    int16_t axes[SDLA_COUNT];   // SDL's raw values: sticks -32768..32767 (y down positive), triggers 0..32767
};

class sdl_api {
public:
    virtual bool pump() = 0;                                         // false: the global update failed
    virtual int32_t count() = 0;                                     // devices enumerated now; < 0 failure
    virtual bool device_info(int32_t index, sdl_device_info* out) = 0;  // before opening; false = unreadable
    virtual uint64_t open(int32_t index) = 0;                        // handle id, 0 = failed to open
    virtual bool handle_info(uint64_t handle, sdl_device_info* out) = 0;  // what was actually opened
    virtual bool attached(uint64_t handle) = 0;
    virtual bool read(uint64_t handle, sdl_reading* out) = 0;        // false = this controller's read failed
    virtual void close(uint64_t handle) = 0;

protected:
    ~sdl_api() = default;
};

}  // namespace ilc::input
