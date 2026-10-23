-- Offline bootstrap experiment. Loading this module performs no native calls.
-- The caller supplies a trusted, validated own-code image and matching exports.
-- No game hooks or resolver configuration are installed by this module.
local M = {}

function M.prepare(memory, arg, bytes, exports, expectedStateSize)
  local code, state
  local function release()
    if state and state ~= 0 then memory.dealloc(state) end
    if code and code ~= 0 then memory.deallocExe(code) end
    state, code = nil, nil
  end
  local ok, result = pcall(function()
    assert(type(bytes) == 'table' and #bytes > 0 and #bytes <= 0x100000, 'image size')
    assert(math.type(expectedStateSize) == 'integer' and expectedStateSize > 0
      and expectedStateSize <= 0x30000, 'state size')
    for _, name in ipairs({'HB_size', 'HB_init'}) do
      local rva = exports[name]
      assert(math.type(rva) == 'integer' and rva >= 0 and rva < #bytes, 'export RVA')
    end
    for i = 1, #bytes do
      assert(math.type(bytes[i]) == 'integer' and bytes[i] >= 0 and bytes[i] <= 255, 'image byte')
    end
    code = memory.allocExe(#bytes)
    assert(code and code ~= 0, 'code allocation')
    assert(memory.writeArray(code, bytes) == true, 'image write')
    local copied = memory.readArray(code, #bytes)
    assert(#copied == #bytes, 'image read length')
    for i = 1, #bytes do assert(copied[i] == bytes[i], 'image readback') end
    local size = memory.execute(code + exports.HB_size, arg.u32, {}, {})
    assert(size == expectedStateSize, 'native state ABI')
    state = memory.alloc(size)
    assert(state and state ~= 0, 'state allocation')
    assert(memory.execute(code + exports.HB_init, arg.u32,
      {arg.pointer, arg.u32}, {state, size}) == 1, 'native initialization')
    local handle = {code = code, state = state, stateSize = size}
    -- Only safe while this isolated, unconfigured core has no external users.
    function handle.close()
      release()
      handle.code, handle.state = nil, nil
    end
    return handle
  end)
  if not ok then release(); return nil, tostring(result) end
  return result
end

return M
