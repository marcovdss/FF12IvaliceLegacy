-- Ivalice Legacy co-op: what the Status rows say, and which of the three "not running" views applies.
--
-- Inputs: the Lua bridge's own status (is the module there, does it match) and the module's GET_STATUS answer (its state
-- and boot reason id). Output: {kind, text, detail, advice}. kind is one of
--   ok           the module is running (any of INERT, STARTING, ARMED, READY)
--   starting     the bridge is still looking for the module
--   missing      ivalice-legacy-coop.dll is not loaded
--   unsupported  wrong game build, a module of another version, or the module found the game's code changed
--   blocked      another co-op mod (Ivalice Together) is installed, so Legacy co-op stays off
--   disabled     the Co-op module switch is Off
--   fault        the module is degraded or faulted (see the log), or the bridge cannot talk to it
-- The boot reason ids are the module's own (the schema's `reason` enum). Known names have a row in M.REASONS; any
-- other name is matched by keyword, so the view keeps working when a reason is added; an unknown id is shown by number.
local M = {}

M.STATE_TEXT = {
  INERT = 'Waiting for the game loader', DISABLED = 'Disabled (setting)',
  BLOCKED = 'Disabled (another co-op mod is installed)', STARTING = 'Starting', ARMED = 'Starting',
  READY = 'Ready', DEGRADED = 'Degraded: see log', FAULT = 'Fault: see log',
}

M.ADVICE = {
  missing = 'The co-op module file was not loaded. Check that Vortex deployed x64/modules/ivalice-legacy-coop.dll, then restart the game.',
  unsupported = 'Co-op supports only the Steam build the mod was made for. It stays off so the game is left untouched.',
  blocked = 'Ivalice Together is installed. Legacy co-op stays off to protect it. Remove or disable Together, then restart the game.',
  disabled = 'Turn the Co-op module switch On, then restart the game.',
  fault = 'Co-op stopped itself and the game carries on as normal. The log file has the details.',
  error = 'The co-op module did not answer the way this script expects. Reinstall Legacy so the script and module match.',
}

local function upper(s) return tostring(s):upper() end

-- Boot reasons the module reports (enum `reason`). kind picks the view, text is the Status label.
M.REASONS = {
  UNSUPPORTED_BUILD = {kind = 'unsupported', text = 'Unsupported game build'},
  DUPLICATE_INSTANCE = {kind = 'fault', text = 'Another copy of co-op is running'},
  INSTANCE_GUARD_FAILED = {kind = 'fault', text = 'Co-op could not start (guard)'},
  MODULE_OFF = {kind = 'disabled', text = 'Off (setting)'},
  SETTINGS_UNREADABLE = {kind = 'disabled', text = 'Off (settings file unreadable)'},
  SETTINGS_INVALID = {kind = 'disabled', text = 'Off (settings file not valid)'},
  TOGETHER_PRESENT = {kind = 'blocked', text = 'Off (Ivalice Together is installed)'},
  BOOTING = {kind = 'starting', text = 'Starting'},
  INPUT_FAILED = {kind = 'fault', text = 'Co-op input did not start'},
  PATCH_CONFLICT = {kind = 'unsupported', text = 'Game code differs (co-op stays off)'},
  PATCH_BUSY = {kind = 'fault', text = 'Co-op could not install (busy)'},
  PATCH_FAILED = {kind = 'fault', text = 'Co-op install failed and was undone'},
  DRY_ARMED = {kind = 'ok', text = 'Ready (dry-armed)'},
  THREAD_FAILED = {kind = 'fault', text = 'Co-op could not start (thread)'},
  SHUT_DOWN = {kind = 'ok', text = 'Shut down'},
  BOOT_ABORTED = {kind = 'fault', text = 'Co-op start-up was cut short'},
}
M.REASON_ADVICE = {
  SETTINGS_UNREADABLE = 'coop.json exists but could not be read, so the co-op module stayed off. Close other programs that may hold the file, then restart.',
  SETTINGS_INVALID = 'coop.json was not valid when the game started, so the co-op module stayed off. This page rewrites it once; restart the game to turn co-op on.',
  PATCH_CONFLICT = 'Another mod or a different game build changed code that co-op needs. Co-op stays off so the game is left untouched.',
  DUPLICATE_INSTANCE = 'Two copies of the co-op module were loaded. Remove the extra copy from the game folder.',
}

-- SETTINGS_INVALID with reason_detail 3: the file is from a newer version. It is left alone on purpose (read-only).
M.NEWER_SCHEMA_TEXT = 'Off (settings file is from a newer version)'
M.NEWER_SCHEMA_ADVICE = 'coop.json was written by a newer version of Legacy, so co-op stays off and the file is left untouched. Update Legacy, or delete coop.json to start from fresh settings.'

-- Which kind a boot reason name points at when the table does not know it (nil when it says nothing about a view).
local function reasonKind(name)
  local n = upper(name)
  if n == 'NONE' or n == '0' then return nil end
  if n:find('TOGETHER') or n:find('FOREIGN') or n:find('OTHER_COOP') then return 'blocked' end
  if n:find('BUILD') or n:find('TIMESTAMP') or n:find('IMAGE') or n:find('VERSION') or n:find('SIZE') or n:find('BASE')
     or n:find('SITE') or n:find('BYTES') or n:find('UNSUPPORTED') then return 'unsupported' end
  if n:find('SETTING') or n:find('DISABLED') or n:find('OFF') then return 'disabled' end
  if n:find('FAULT') or n:find('FAIL') or n:find('PATCH') or n:find('DUPLICATE') or n:find('INSTANCE') then return 'fault' end
  return nil
end

local function words(name)
  return (tostring(name):lower():gsub('_', ' '):gsub('^%l', string.upper))
end

-- bridgeStatus: bridge.status {state, reason, detail}. native: views.status(...) or nil. C: the codec (for enum names).
function M.classify(bridgeStatus, native, C)
  local b = bridgeStatus
  if b.state ~= 'ready' then
    local kind = ({starting = 'starting', missing = 'missing', unsupported = 'unsupported', mismatch = 'unsupported',
                   error = 'fault'})[b.state] or 'fault'
    local text = (b.reason or 'Unknown'):gsub('^%l', string.upper)
    local advice = M.ADVICE[kind]
    if b.state == 'mismatch' or b.state == 'error' then advice = M.ADVICE.error end
    if kind == 'starting' then advice = nil end
    return {kind = kind, text = text, detail = b.detail, advice = advice, bridge = b.state}
  end
  if not native then return {kind = 'ok', text = 'Module loaded'} end
  local E = C.enums
  local state = native.stateName or 'INERT'
  local reasonName
  if native.reason ~= 0 then
    local enum = C.enums.reason
    reasonName = enum and C.name_of('reason', native.reason) or ('reason ' .. native.reason)
  end
  local kind
  if state == 'BLOCKED' then kind = 'blocked'
  elseif state == 'DISABLED' then kind = 'disabled'
  elseif state == 'FAULT' or state == 'DEGRADED' then kind = 'fault'
  else kind = 'ok' end
  local text = M.STATE_TEXT[state] or 'Unknown state'
  local advice
  local known = reasonName and M.REASONS[reasonName]
  if known then
    kind, text = known.kind, known.text
    advice = M.REASON_ADVICE[reasonName]
    if reasonName == 'DRY_ARMED' and state ~= 'READY' then text = 'Starting' end      -- ready only after FF12HinitDone
    if reasonName == 'SETTINGS_INVALID' and native.reasonDetail == 3 then
      text, advice = M.NEWER_SCHEMA_TEXT, M.NEWER_SCHEMA_ADVICE
    end
  elseif reasonName then
    -- the reason can only sharpen the view: the build or the game's code is unsupported, or Together is the blocker
    local byReason = reasonKind(reasonName)
    if byReason == 'unsupported' or byReason == 'blocked' then kind = byReason end
    if kind == 'unsupported' then text = 'Unsupported (co-op stays off)' end
  end
  local detail = reasonName and ('reason: ' .. words(reasonName)) or nil
  if detail and native.reasonDetail and native.reasonDetail ~= 0 then
    if reasonName:sub(1, 6) == 'PATCH_' then           -- detail = patch result | (site row + 1) << 16
      detail = detail .. ' (site row ' .. ((native.reasonDetail >> 16) - 1) .. ')'
    else
      detail = detail .. ' (detail ' .. native.reasonDetail .. ')'
    end
  end
  if kind ~= 'ok' and advice == nil then advice = M.ADVICE[kind] end
  if kind == 'ok' then advice = nil end
  local residue = E.boot_flag and (native.bootFlags or 0) & E.boot_flag.TOGETHER_RESIDUE ~= 0 or false
  return {kind = kind, text = text, detail = detail, advice = advice, reason = reasonName, state = state, residue = residue}
end

-- A view that replaces the settings pages with an explanation.
function M.blocks_settings(kind)
  return kind == 'missing' or kind == 'unsupported' or kind == 'blocked' or kind == 'fault' or kind == 'starting'
end

return M
