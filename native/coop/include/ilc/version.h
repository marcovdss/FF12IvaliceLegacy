// Ivalice Legacy co-op module: identity and version.
#pragma once

#include <stdint.h>

#define ILC_MODULE_NAME "Ivalice Legacy Co-op"

#define ILC_VERSION_MAJOR 0u
#define ILC_VERSION_MINOR 0u
#define ILC_VERSION_PATCH 2u

// build.py passes the first 16 bytes of the SHA-256 of the module sources as two 64-bit values, so a
// running module can say which sources it was built from. A build without them reports zero.
#ifndef ILC_SOURCE_ID0
#define ILC_SOURCE_ID0 0ull
#endif
#ifndef ILC_SOURCE_ID1
#define ILC_SOURCE_ID1 0ull
#endif
