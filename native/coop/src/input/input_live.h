// Ivalice Legacy co-op module: the live device backends (System32 XInput 1.4 and the static SDL2).
//
// live_open/live_close run on the input service thread only, never from DllMain. live_open loads
// %SystemRoot%\System32\xinput1_4.dll by its full path (directory from GetSystemDirectoryW, not from the
// environment) and preloads hid.dll and cfgmgr32.dll from System32, so that SDL's own LoadLibrary calls by base
// name resolve to those already-loaded System32 modules and never to a file in the game folder. It then sets
// SDL's hints at override priority (environment variables cannot change them) and initializes only SDL's
// game controller subsystem.
#pragma once

#include <stdint.h>

#include "input/input_api.h"

namespace ilc::input {

struct live_backends {
    xinput_api* xinput;  // null if XInput 1.4 did not load
    sdl_api* sdl;        // null if SDL did not initialize
};

live_backends live_open(uint32_t* issues);
void live_close();

}  // namespace ilc::input
