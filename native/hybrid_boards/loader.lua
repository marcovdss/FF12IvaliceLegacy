-- Process-lifetime pilot bootstrap. Loading this module does not install hooks.
-- Install/reconfigure only from initialization/save-load callbacks while the
-- license UI is inactive. There is deliberately no live-unload operation.
local M = {}
local function integer(value) return math.type(value) == 'integer' end
local function append(out, value, count)
  for _ = 1, count do out[#out+1] = value & 255; value = value >> 8 end
end
local function read(memory, address, count)
  local value = memory.readArray(address, count)
  if type(value) == 'string' then
    local bytes = {}; for i = 1, #value do bytes[i] = value:byte(i) end; value = bytes
  end
  assert(type(value) == 'table' and #value == count, 'memory read failed')
  return value
end
local function same(a,b)
  if #a ~= #b then return false end
  for i = 1, #b do if a[i] ~= b[i] then return false end end
  return true
end
local function scalar(memory,address,count)
  local bytes = read(memory,address,count); local result = 0
  for i = count,1,-1 do result = (result << 8) | bytes[i] end
  return result
end
local function write(memory,address,bytes)
  assert(memory.writeArray(address,bytes) == true, 'memory write failed')
  assert(same(read(memory,address,#bytes),bytes), 'memory write verification failed')
end

function M.install(memory,arg,bundle,hooks,event)
  if type(bundle) ~= 'table' or bundle.schemaVersion ~= 1 or bundle.completeHookSet ~= true then
    return nil, 'native integration has not passed the complete-hook gate'
  end
  -- This build intentionally supports only the verified preferred mapping.
  -- A different module mapping/version refuses before allocating or patching.
  local moduleBase = bundle.preferredBase
  if moduleBase ~= 0x120000 then return nil, 'unsupported module mapping' end
  local ok, errorText = pcall(function()
    assert(same(read(memory,moduleBase,2),{77,90}), 'game module not mapped at verified base')
    assert(type(bundle.hooks) == 'table' and #bundle.hooks > 0, 'missing hook plan')
    assert(type(bundle.image) == 'table' and #bundle.image > 0, 'missing native image')
    assert(type(bundle.layout) == 'table' and #bundle.layout == 576, 'invalid layout')
    assert(bundle.pair[1] == 0 and bundle.pair[2] == 4, 'unsupported pilot pair')
    assert(integer(bundle.adapterSize) and bundle.adapterSize > 0 and bundle.adapterSize < 0x100000,
      'invalid adapter ABI')
    assert(bundle.requestSize == 48 and bundle.objectCount == 9, 'unsupported configuration ABI')
  end)
  if not ok then return nil,tostring(errorText) end

  local allocations, code, state, request, input, transaction = {},nil,nil,nil,nil,nil
  local function alloc(size,executable)
    local address = executable and memory.allocExe(size) or memory.alloc(size)
    assert(integer(address) and address > 0, 'allocation failed')
    allocations[#allocations+1] = {address=address,executable=executable}
    return address
  end
  local function discardBeforeActivation()
    -- No callbacks/branches have been exposed yet, or startup rollback verified.
    for i = #allocations,1,-1 do
      local item = allocations[i]
      if item.executable then memory.deallocExe(item.address) else memory.dealloc(item.address) end
    end
    allocations = {}
  end
  local function call(name,ret,...)
    local rva = bundle.exports[name]
    assert(integer(rva) and rva >= 0 and rva < #bundle.image, 'missing native export')
    return memory.execute(code+rva,ret,...)
  end
  local function configure()
    local keep = scalar(memory,0x2EBF190,8)
    local root = scalar(memory,0x209AC30,8)
    if keep == 0 or root == 0 then return false end
    local bytes = {}
    append(bytes,0,8); append(bytes,0x282DF0,8); append(bytes,keep,8)
    append(bytes,0x209AC30,8); append(bytes,9,4); append(bytes,0,4); append(bytes,0x2EBF190,8)
    write(memory,request,bytes)
    return call('HBX_config',arg.u32,arg.pointer,state,arg.pointer,request,
      arg.pointer,0x2EBF1A0,arg.pointer,0x323600) == 1
  end
  local status
  ok,errorText = pcall(function()
    -- Guard every original instruction before copying/executing new code.
    for _,site in ipairs(bundle.hooks) do
      assert(same(read(memory,site.source,#site.expected),site.expected), 'hook bytes differ: '..site.name)
    end
    code = alloc(#bundle.image,true); write(memory,code,bundle.image)
    assert(call('HBX_size',arg.u32) == bundle.adapterSize, 'native state ABI differs')
    state = alloc(bundle.adapterSize,false)
    assert(call('HBX_init',arg.u32,arg.pointer,state,arg.u32,bundle.adapterSize) == 1, 'native initialization failed')
    request,input = alloc(48,false),alloc(1168,false)
    local data = {}; append(data,0,4); append(data,4,4); append(data,24,4); append(data,24,4)
    for _,value in ipairs(bundle.layout) do
      assert(integer(value) and (value == 65535 or value >= 0 and value <= 360), 'invalid license ID')
      append(data,value,2)
    end
    write(memory,input,data)
    local core = call('HBX_core',arg.pointer,arg.pointer,state)
    assert(core and core ~= 0 and call('HB_put',arg.u32,arg.pointer,core,arg.pointer,input) ~= 0,
      'native layout upload failed')
    if not configure() then error('game state is not ready; retry after loading a save') end
    local patches = {}
    for _,site in ipairs(bundle.hooks) do
      local target = alloc(#site.template,true)
      local bytes = {}; for i = 1,#site.template do bytes[i] = site.template[i] end
      for _,fixup in ipairs(site.fixups) do
        local value
        if fixup.kind == 'state' then value = state
        elseif fixup.kind == 'route' then value = code+bundle.exports.HBX_route
        elseif fixup.kind == 'continuation' then value = site.continuation
        elseif fixup.kind == 'reject' then value = site.reject or 0
        elseif fixup.kind == 'fallback' then value = target+fixup.targetOffset
        elseif fixup.kind == 'fallbackCall' then
          value = site.fallbackCall-(target+fixup.offset+4)
          assert(value >= -0x80000000 and value < 0x80000000, 'original call out of reach')
        else error('unknown thunk relocation') end
        assert(integer(fixup.offset) and fixup.offset >= 0 and fixup.offset+fixup.size <= #bytes,
          'invalid thunk relocation')
        local encoded = {}; append(encoded,value,fixup.size)
        for i = 1,#encoded do bytes[fixup.offset+i] = encoded[i] end
      end
      write(memory,target,bytes)
      local delta = target-(site.source+5)
      assert(delta >= -0x80000000 and delta < 0x80000000, 'thunk out of reach')
      local replacement = {0xe9}; append(replacement,delta,4)
      while #replacement < #site.expected do replacement[#replacement+1] = 0x90 end
      patches[#patches+1] = {address=site.source,original=site.expected,replacement=replacement}
    end
    local failure
    transaction,errorText,failure = hooks.install(memory,patches)
    if not transaction then transaction = failure; error(errorText) end
    status = {active=true,state=state,code=code,allocations=allocations}
    function status.reconfigure()
      -- Caller must guarantee license UI/callback quiescence at save load.
      return configure()
    end
  end)
  if not ok then
    if not transaction or transaction.patchesRestored then discardBeforeActivation() end
    return nil,tostring(errorText),transaction
  end
  -- No live unload/free API: ownership lasts until process exit. Save-load
  -- event registration belongs to the entry script and must use a sync callback.
  return status
end

return M
