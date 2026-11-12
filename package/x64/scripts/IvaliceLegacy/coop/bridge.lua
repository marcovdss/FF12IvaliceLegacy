-- Ivalice Legacy co-op: the Lua side of the native bridge (ABI v1).
--
-- ivalice-legacy-coop.dll exports one function, ILC_Bridge(op, in, inLen, out, outCap) -> int32. This module finds it
-- with GetModuleHandleW + GetProcAddress (their addresses come from the game exe's import table), checks the module
-- with HELLO (ABI version and schema hash), and wraps each op. Every record is encoded and decoded by codec.lua from
-- the generated schema. When the module is absent or does not match, the status says why and no other call is made.
--
-- Rules this module keeps:
--   * calls go through the synchronous memory.execute only (the async variant starts a thread per call);
--   * the module is looked up from onInitDone and retried with backoff (it loads after the Lua scripts start);
--   * names go through one allocData buffer; the in/out buffers are allocated once and reused;
--   * call only from script-thread contexts (timers, async events, Mod Menu callbacks), never from sync events or hooks.
local M = {}

M.MODULE = 'ivalice-legacy-coop.dll'
M.EXPORT = 'ILC_Bridge'
-- The game build this was written for (x64/FFXII_TZA.exe 1.0.4.0). Addresses are absolute: DYNAMIC_BASE is off.
M.GAME = {base = 0x120000, timestamp = 0x5DCA7FD7, iat_gmhw = 0x8C2360, iat_gmha = 0x8C2290, iat_gpa = 0x8C2130}
M.MAX_CONSECUTIVE_FAILURES = 3
M.MAX_ATTEMPTS = 40
M.MAX_TRANSIENT_RETRIES = 1

-- Delay before attempt n+1 (ms): 100 x10, then 250, 500, 1000, 2000, 5000 capped.
function M.backoff_ms(attempt)
  if attempt <= 10 then return 100 end
  local steps = {250, 500, 1000, 2000, 5000}
  return steps[math.min(attempt - 10, #steps)]
end

-- opts: memory (default the loader's), after(ms, fn) (default event.executeAfterMs), codec (required),
-- module (override the DLL name, tests only), game (override M.GAME, tests only), on_change(status) (optional).
function M.create(opts)
  local memory = opts.memory or _G.memory
  local after = opts.after or (_G.event and _G.event.executeAfterMs)
  local C = opts.codec
  local game = opts.game or M.GAME
  local moduleName = opts.module or M.MODULE
  local schema = C.schema
  local E = C.enums
  local B = {attempts = 0, calls = 0}

  local IN_CAP = 4096
  local OUT_CAP = schema.const.OUT_CAP_RECOMMENDED
  assert(IN_CAP >= C.size('ilc_settings'), 'in buffer too small for the settings record')
  assert(OUT_CAP >= C.size('ilc_settings') and OUT_CAP >= C.size('ilc_devices_reply')
         and OUT_CAP >= C.size('ilc_events_reply'), 'out buffer too small for the largest reply')

  local fn, nameBuf, inBuf, outBuf = 0, 0, 0, 0
  local failures, stopped, timerArmed, transient = 0, false, false, 0

  -- state: starting | missing | unsupported | mismatch | error | ready
  B.status = {state = 'starting', reason = 'waiting for the game to finish loading'}
  B.hello = nil

  local function set(state, reason, detail)
    local s = B.status
    if s.state == state and s.reason == reason and s.detail == detail then return end
    B.status = {state = state, reason = reason, detail = detail}
    if opts.on_change then opts.on_change(B.status) end
  end

  function B.is_ready() return B.status.state == 'ready' end

  local function isInt(v) return math.type(v) == 'integer' end

  local function toTable(bytes)
    local t, n = {}, #bytes
    for i = 1, n, 512 do
      local chunk = {bytes:byte(i, math.min(i + 511, n))}
      for j = 1, #chunk do t[i + j - 1] = chunk[j] end
    end
    return t
  end

  local function toString(t, n)
    local parts = {}
    for i = 1, n, 512 do
      parts[#parts + 1] = string.char(table.unpack(t, i, math.min(i + 511, n)))
    end
    return table.concat(parts)
  end

  local function buffers()
    if inBuf ~= 0 then return true end
    local n, i, o = memory.allocData(128), memory.allocData(IN_CAP), memory.allocData(OUT_CAP)
    if not (isInt(n) and isInt(i) and isInt(o)) or n == 0 or i == 0 or o == 0 then
      for _, p in ipairs({n or 0, i or 0, o or 0}) do
        if isInt(p) and p ~= 0 then memory.deallocData(p) end
      end
      return false
    end
    nameBuf, inBuf, outBuf = n, i, o
    return true
  end

  local function exeOk()
    if memory.u16[game.base] ~= 0x5A4D then return false end
    local pe = memory.u32[game.base + 0x3C]
    if pe < 0x40 or pe > 0x1000 then return false end
    if memory.u32[game.base + pe] ~= 0x4550 then return false end
    return memory.u32[game.base + pe + 8] == game.timestamp
  end

  local function apiOk()
    for _, k in ipairs({'execute', 'allocData', 'deallocData', 'writeString', 'writeWString', 'writeArray', 'readArray'}) do
      if type(memory[k]) ~= 'function' then return false end
    end
    return memory.u64 ~= nil and memory.u32 ~= nil and memory.u16 ~= nil and type(memory.arg) == 'table'
  end

  -- Returns the export address, or nil, state, reason.
  local function lookup()
    local A = memory.arg
    local gmh, wide = memory.u64[game.iat_gmhw], true
    if not (isInt(gmh) and gmh > 0) then gmh, wide = memory.u64[game.iat_gmha], false end
    local gpa = memory.u64[game.iat_gpa]
    if not (isInt(gmh) and gmh > 0 and isInt(gpa) and gpa > 0) then
      return nil, 'unsupported', 'the game exe does not import the lookup functions'
    end
    if wide then
      memory.writeWString(nameBuf, moduleName, 64)
    else
      memory.writeString(nameBuf, moduleName, 128)
    end
    local h = memory.execute(gmh, A.pointer, A.pointer, nameBuf)
    if not isInt(h) then return nil, 'error', 'the loader refused the module lookup' end
    if h == 0 then return nil, 'missing', 'co-op module not loaded' end
    memory.writeString(nameBuf, M.EXPORT, 128)
    local p = memory.execute(gpa, A.pointer, A.pointer, h, A.pointer, nameBuf)
    if not isInt(p) then return nil, 'error', 'the loader refused the export lookup' end
    if p == 0 then return nil, 'missing', 'co-op module has no bridge export' end
    return p
  end

  -- Raw call. Returns the reply bytes as a string (maybe empty) or nil, errcode|string.
  local function rawCall(op, bytes, outCap)
    if fn == 0 then return nil, 'no bridge' end
    bytes = bytes or ''
    if #bytes > IN_CAP then return nil, 'record too large' end
    if #bytes > 0 and not memory.writeArray(inBuf, toTable(bytes)) then return nil, 'write failed' end
    local A = memory.arg
    local st = memory.execute(fn, A.s32, A.u32, op, A.pointer, inBuf, A.u32, #bytes, A.pointer, outBuf, A.u32,
                              outCap or OUT_CAP)
    B.calls = B.calls + 1
    if not isInt(st) then                      -- a loader refusal returns nil: the call did not happen
      failures = failures + 1
      if failures >= M.MAX_CONSECUTIVE_FAILURES then
        stopped = true
        set('error', 'bridge calls are being refused', 'stopped after repeated loader refusals')
      end
      return nil, 'refused'
    end
    failures = 0
    if st < 0 then return nil, st end
    if st == 0 then return '' end
    if st > (outCap or OUT_CAP) then return nil, 'bad reply length' end   -- never decode a length the buffer cannot hold
    local t = memory.readArray(outBuf, st)
    if type(t) ~= 'table' or #t ~= st then return nil, 'read failed' end
    return toString(t, st)
  end

  local function fail(state, reason, detail)
    fn = 0
    stopped = true
    set(state, reason, detail)
    return false
  end

  -- One discovery attempt. Returns true when ready. A permanent failure stops further attempts.
  function B.attempt()
    if B.is_ready() then return true end
    if stopped then return false end
    B.attempts = B.attempts + 1
    if not memory or not apiOk() then return fail('unsupported', 'Lua Loader memory API unavailable') end
    if not exeOk() then
      return fail('unsupported', 'unsupported game build', 'the exe header does not match the supported build')
    end
    if not buffers() then return fail('error', 'out of script memory') end
    local p, state, why = lookup()
    if not p then
      if state == 'missing' then
        set('missing', why == 'co-op module not loaded' and 'co-op module missing' or why, why)
        if B.attempts >= M.MAX_ATTEMPTS then stopped = true end
        return false
      end
      if state == 'error' and transient < M.MAX_TRANSIENT_RETRIES then
        transient = transient + 1
        set('starting', 'retrying the co-op module', why)
        return false
      end
      return fail(state, why)
    end
    fn = p
    local reply, err = rawCall(E.op.HELLO, C.encode('ilc_header', C.header('HELLO', C.size('ilc_header'))))
    if not reply then
      -- A loader refusal or a FAULT may be a moment's trouble: allow one bounded retry before giving up.
      if (err == 'refused' or err == E.error.FAULT) and transient < M.MAX_TRANSIENT_RETRIES and not stopped then
        transient = transient + 1
        fn = 0
        set('starting', 'retrying the co-op module', tostring(B.describe_error(err)))
        return false
      end
      return fail('error', 'co-op module refused HELLO', tostring(B.describe_error(err)))
    end
    if #reply < C.size('ilc_header') then return fail('mismatch', 'co-op module answered HELLO oddly') end
    local head = C.decode('ilc_header', reply)
    if head.magic ~= schema.const.ABI_MAGIC or head.abi_version ~= schema.abi_version
       or #reply ~= C.size('ilc_hello_reply') then
      return fail('mismatch', 'co-op module ABI mismatch',
                  'module ABI ' .. tostring(head.abi_version) .. ', expected ' .. schema.abi_version)
    end
    local hello = C.decode('ilc_hello_reply', reply)
    if hello.schema_id[1] ~= schema.schema_id[1] or hello.schema_id[2] ~= schema.schema_id[2] then
      return fail('mismatch', 'co-op module schema mismatch',
                  string.format('module %016x%016x, expected %016x%016x', hello.schema_id[1], hello.schema_id[2],
                                schema.schema_id[1], schema.schema_id[2]))
    end
    if hello.settings_size ~= C.size('ilc_settings') then
      return fail('mismatch', 'co-op module settings size mismatch', tostring(hello.settings_size))
    end
    B.hello = hello
    set('ready', 'bridge ready')
    return true
  end

  -- Start discovery and retry with backoff until ready or a permanent failure. Safe to call more than once.
  function B.start()
    if timerArmed or B.is_ready() or stopped then return end
    local function step()
      timerArmed = false
      if B.attempt() or stopped then return end
      if after then
        timerArmed = true
        after(M.backoff_ms(B.attempts), step)
      end
    end
    step()
  end

  -- Forget a permanent failure (tests and a manual retry).
  function B.reset()
    stopped, failures, fn, B.attempts, B.hello, transient = false, 0, 0, 0, nil, 0
    set('starting', 'waiting for the game to finish loading')
  end

  function B.close()
    for _, p in ipairs({nameBuf, inBuf, outBuf}) do
      if p ~= 0 then memory.deallocData(p) end
    end
    nameBuf, inBuf, outBuf, fn = 0, 0, 0, 0
  end

  function B.describe_error(code)
    if isInt(code) then return C.name_of('error', code) or tostring(code) end
    return tostring(code)
  end

  -- Typed wrappers. Each returns the decoded reply, or nil and an error name or reason.
  local function call(op, bytes, outStruct, minReply)
    if not B.is_ready() then return nil, 'bridge not ready' end
    local reply, err = rawCall(op, bytes)
    if not reply then return nil, B.describe_error(err), err end
    if outStruct then
      if #reply < (minReply or C.size(outStruct)) then return nil, 'short reply' end
      return C.decode(outStruct, reply .. string.rep('\0', C.size(outStruct) - #reply))
    end
    return {}
  end

  function B.get_hello()
    return call(E.op.HELLO, C.encode('ilc_header', C.header('HELLO', C.size('ilc_header'))), 'ilc_hello_reply')
  end

  -- settings: a table (header.generation required) or an already-encoded string.
  function B.set_settings(settings)
    local bytes = type(settings) == 'string' and settings or C.encode('ilc_settings', settings)
    return call(E.op.SET_SETTINGS, bytes, 'ilc_settings_ack')
  end

  function B.get_settings() return call(E.op.GET_SETTINGS, nil, 'ilc_settings') end

  -- name: a command enum name (PARK, MOVE_SEAT_NOW, ...); request_id must be non-zero.
  function B.command(name, request_id, seat, arg0, arg1)
    local id = E.command[name]
    if not id then return nil, 'unknown command ' .. tostring(name) end
    local rec = {header = C.header('COMMAND', C.size('ilc_command'), request_id), command = id,
                 seat = seat or 0, arg0 = arg0 or 0, arg1 = arg1 or 0}
    return call(E.op.COMMAND, C.encode('ilc_command', rec), nil)
  end

  function B.get_status() return call(E.op.GET_STATUS, nil, 'ilc_status') end
  function B.get_seats() return call(E.op.GET_SEATS, nil, 'ilc_seats_reply') end

  function B.get_devices()
    local r, err = call(E.op.GET_DEVICES, nil, 'ilc_devices_reply', 32)
    if r and r.count > schema.const.DEVICES_MAX then return nil, 'bad device count' end
    return r, err
  end

  function B.pop_events() return call(E.op.POP_EVENTS, nil, 'ilc_events_reply', 32) end

  -- One page poll: status, plus queued events when there are any (at most `rounds` pops).
  function B.poll(rounds)
    local status, err = B.get_status()
    if not status then return nil, err end
    local events = {}
    for _ = 1, status.events_pending > 0 and (rounds or 2) or 0 do
      local r = B.pop_events()
      if not r then break end
      for i = 1, math.min(r.count, schema.const.EVENT_QUEUE) do events[#events + 1] = r.events[i] end
      if r.pending_after == 0 then break end
    end
    return {status = status, events = events}
  end

  -- Test hook: the raw call, for ops this module does not wrap (the page never uses it).
  B._raw = rawCall
  return B
end

return M
