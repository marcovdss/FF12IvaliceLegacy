// Ivalice Legacy co-op module: SHA-256 (FIPS 180-4), used to turn a device path into a stable device uid.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace ilc {

void sha256(const void* data, size_t size, uint8_t digest[32]);

}  // namespace ilc
