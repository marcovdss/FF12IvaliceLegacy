-- Ivalice Legacy co-op: bridge record codec, driven entirely by schema_data.lua (generated from the native schema).
-- No layout is written here: offsets, sizes, ranges and enum values all come from the schema table.
local M = {}

local PACK = {
  uint8_t = '<I1', uint16_t = '<I2', uint32_t = '<I4', int32_t = '<i4', uint64_t = '<I8',
}

function M.new(schema)
  local C = {schema = schema, enums = {}, names = {}}
  for ename, items in pairs(schema.enums) do
    C.enums[ename] = items
    local rev = {}
    for k, v in pairs(items) do rev[v] = k end
    C.names[ename] = rev
  end
  for sname, st in pairs(schema.structs) do          -- layouts must be contiguous (the generator checks the same)
    local at = 0
    for _, f in ipairs(st.fields) do
      if f.kind == 'VAL' or f.kind == 'ARR' then
        assert(PACK[f.type], sname .. '.' .. f.name .. ': unknown scalar type ' .. tostring(f.type))
      elseif f.kind == 'REC' or f.kind == 'RECS' then
        assert(schema.structs[f.type], sname .. '.' .. f.name .. ': unknown record type ' .. tostring(f.type))
      else
        assert(f.kind == 'PAD' or f.kind == 'BYTES', sname .. '.' .. f.name .. ': unknown kind ' .. tostring(f.kind))
      end
      assert(f.offset == at, sname .. '.' .. f.name .. ': layout gap')
      at = at + f.size
    end
    assert(at == st.size, sname .. ': size mismatch')
  end

  function C.size(sname) return schema.structs[sname].size end

  function C.name_of(ename, value) return C.names[ename][value] end

  local byId = {}
  for sname, st in pairs(schema.structs) do byId[st.id] = sname end

  -- 'ilc_settings.<field>' for a native fieldId ((struct id << 8) | field index), or '?0x...' when unknown.
  function C.field_name(fid)
    local sname = byId[fid >> 8]
    local f = sname and schema.structs[sname].fields[(fid & 0xFF) + 1]
    if not f then return string.format('?0x%x', fid) end
    return sname .. '.' .. f.name
  end

  -- Name of the field at a 0-based index (the schema keeps field order stable across renames).
  function C.field_at(sname, index)
    local f = schema.structs[sname].fields[index + 1]
    return f and f.name, f
  end

  function C.field(sname, fname)
    for _, f in ipairs(schema.structs[sname].fields) do
      if f.name == fname then return f end
    end
    error('unknown field ' .. sname .. '.' .. tostring(fname), 2)
  end

  local function encode(sname, value, out)
    for _, f in ipairs(schema.structs[sname].fields) do
      local v, kind = value[f.name], f.kind
      if kind == 'PAD' or kind == 'BYTES' then
        v = v or ''
        assert(#v <= f.size, f.name .. ': too long')
        out[#out + 1] = v .. string.rep('\0', f.size - #v)
      elseif kind == 'REC' then
        encode(f.type, v or {}, out)
      elseif kind == 'RECS' then
        v = v or {}
        for i = 1, f.count do encode(f.type, v[i] or {}, out) end
      elseif kind == 'VAL' then
        out[#out + 1] = string.pack(PACK[f.type], v or 0)
      else -- ARR
        v = v or {}
        for i = 1, f.count do out[#out + 1] = string.pack(PACK[f.type], v[i] or 0) end
      end
    end
  end

  -- value (nested table, 1-based arrays) -> binary string. Missing fields are zero.
  function C.encode(sname, value)
    local out = {}
    encode(sname, value or {}, out)
    local s = table.concat(out)
    assert(#s == schema.structs[sname].size, 'encode size')
    return s
  end

  local function decode(sname, s, base)
    local r = {}
    for _, f in ipairs(schema.structs[sname].fields) do
      local at, kind = base + f.offset + 1, f.kind
      if kind == 'PAD' or kind == 'BYTES' then
        r[f.name] = s:sub(at, at + f.size - 1)
      elseif kind == 'REC' then
        r[f.name] = decode(f.type, s, base + f.offset)
      elseif kind == 'RECS' then
        local t = {}
        for i = 1, f.count do t[i] = decode(f.type, s, base + f.offset + (i - 1) * f.elem) end
        r[f.name] = t
      elseif kind == 'VAL' then
        r[f.name] = string.unpack(PACK[f.type], s, at)
      else
        local t = {}
        for i = 1, f.count do t[i] = string.unpack(PACK[f.type], s, at + (i - 1) * f.elem) end
        r[f.name] = t
      end
    end
    return r
  end

  function C.decode(sname, s)
    assert(#s >= schema.structs[sname].size, 'decode: short record')
    return decode(sname, s, 0)
  end

  function C.header(record_type, size, generation)
    return {magic = schema.const.ABI_MAGIC, abi_version = schema.const.ABI_VERSION,
            record_type = C.enums.record_type[record_type], size = size, generation = generation or 0}
  end

  -- Range check against the schema, the same ranges native enforces. Returns a list of 'struct.field[i]' strings.
  function C.range_errors(sname, value, prefix, errs)
    errs = errs or {}
    prefix = prefix or sname
    for _, f in ipairs(schema.structs[sname].fields) do
      local v = value[f.name]
      if f.kind == 'REC' and v then
        C.range_errors(f.type, v, prefix .. '.' .. f.name, errs)
      elseif f.kind == 'RECS' and v then
        for i = 1, f.count do
          if v[i] then C.range_errors(f.type, v[i], prefix .. '.' .. f.name .. '[' .. i .. ']', errs) end
        end
      elseif (f.kind == 'VAL' or f.kind == 'ARR') and v ~= nil then
        local list = f.kind == 'VAL' and {v} or v
        for i, x in ipairs(list) do
          if math.type(x) ~= 'integer' or x < f.min or x > f.max then
            errs[#errs + 1] = prefix .. '.' .. f.name .. (f.kind == 'ARR' and '[' .. i .. ']' or '')
          end
        end
      end
    end
    return errs
  end

  -- FNV-1a 32 over the settings body (everything after the header), as the native digest.
  function C.digest(record)
    local h = 0x811C9DC5
    for i = schema.structs.ilc_header.size + 1, #record do
      h = ((h ~ record:byte(i)) * 0x01000193) & 0xFFFFFFFF
    end
    return h
  end

  function C.defaults()
    return (schema.defaults_hex:gsub('%x%x', function(x) return string.char(tonumber(x, 16)) end))
  end

  return C
end

return M
