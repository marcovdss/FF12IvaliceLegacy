// Ivalice Legacy co-op module: the keyboard and mouse seam of the input service.
//
// Like the pad APIs (input_api.h), the service reads the keyboard only through this interface, so host tests can
// script it. The live implementation asks Windows whether this process owns the foreground window and reads the
// asynchronous key state of single virtual keys; it never installs a hook, never registers raw input and never
// reads game memory. The service reads keys only while the game is in the foreground, only the keys the kbm
// seat's bindings name (all keys while a capture runs for that seat), and never for a seat that does not own the
// keyboard.
#pragma once

#include <stdint.h>

namespace ilc::input {

class keyboard_api {
public:
    virtual bool focused() = 0;          // this process owns the foreground window
    virtual bool down(uint8_t vk) = 0;   // virtual key vk (1/2 = left/right mouse button) is down now

protected:
    ~keyboard_api() = default;
};

// The live keyboard (keyboard_live.cpp). Safe to call from the service thread.
keyboard_api* live_keyboard();

}  // namespace ilc::input
