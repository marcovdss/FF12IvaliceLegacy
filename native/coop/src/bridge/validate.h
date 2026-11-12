// Ivalice Legacy co-op module: validation of inbound bridge records.
//
// Every function works on the module's own aligned copy of the record (never on the caller's buffer) and
// reports the first failure in a fixed order: header, then every field in table order (generic ranges and
// zero reserved bytes from bridge_abi.h), then the cross-field rules.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"

namespace ilc {

struct reject {
    int32_t code;    // ilc_error
    uint32_t field;  // (struct id << 8) | field index, 0 = none
    uint32_t outer;  // ilc_location
    uint32_t inner;  // element index within the field
};

// magic -> BAD_HEADER, abi_version -> ABI_MISMATCH, record_type -> BAD_TYPE, size != in_len -> BAD_LENGTH.
bool validate_header(const ilc_header& header, uint32_t in_len, uint16_t record_type, reject* out);

// The settings body: every field, then the cross-field rules. The header must already have passed.
bool validate_settings(const ilc_settings& settings, reject* out);

// A command record body (header already passed): ranges, request id, and the per-command seat/arg rules.
bool validate_command(const ilc_command& command, reject* out);

// Field id of ilc_header.generation (generation 0 is refused where a generation is required).
uint32_t header_generation_field();

// Field id of ilc_settings.kbm_seat (a refused seat is reported at that seat's location).
uint32_t kbm_seat_field();

// FNV-1a 32 over bytes [16, size) of a settings record: the body without the header.
uint32_t settings_digest(const ilc_settings& settings);

}  // namespace ilc
