-- Ivalice Legacy: co-op settings page and the Lua side of the co-op module bridge.
-- Loaded once by the FF12 LUA Loader. Everything is guarded: if a part fails the rest of the game is untouched.
local DIR = 'scripts/IvaliceLegacy/coop/'

local function load(name)
  local chunk, err = loadfile(DIR .. name .. '.lua', 't', _ENV)
  if not chunk then error('co-op: ' .. name .. ' not loaded (' .. tostring(err) .. ')') end
  return chunk()
end

-- First run only: is Ivalice Together installed? (Then the co-op module starts Off, as native also decides at init.)
-- Paths are relative to x64, where the loader runs scripts.
local TOGETHER_PATHS = {'IvaliceTogetherRuntime', 'IvaliceTogetherConfig.exe'}
local function togetherPresent()
  local fs = rawget(_G, 'filesystem')
  if not (fs and fs.exists) then return false end
  for _, p in ipairs(TOGETHER_PATHS) do
    local ok, found = pcall(fs.exists, p)
    if ok and found == true then return true end
  end
  return false
end

local ok, why = pcall(function()
  local schema = load('schema_data')
  local codec = load('codec').new(schema)
  local views = load('views').new(codec)
  local bridge = load('bridge').create({codec = codec, after = event.executeAfterMs})
  local store = load('store').create({
    codec = codec, config = config, io = io,
    fs = rawget(_G, 'filesystem'), log = print, together = togetherPresent,
  })
  store.load()
  local dialogs = load('dialogs').create({dialog = rawget(_G, 'dialog'), log = print})
  local flows = load('flows').create({store = store, bridge = bridge, codec = codec, views = views, dialogs = dialogs,
                                      log = print})
  local page = load('page').create({
    modmenu = modmenu, store = store, bridge = bridge, codec = codec, views = views, flows = flows, dialogs = dialogs,
    statusview = load('statusview'), after = event.executeAfterMs, log = print,
  })
  page.start()
  event.registerEventAsync('onInitDone', function()
    bridge.start()
    if bridge.is_ready() then store.sync(bridge) end
  end)
  event.registerEventAsync('exit', function()
    flows.cancel()
    store.flush()
  end)
  -- Keep this script alive: it exits once its one-shot callbacks have fired unless something else is registered, and the
  -- page timer alone is not documented as keeping it alive. "Definitions keep their owner script alive even without
  -- callbacks" (loader docs, event.defineEvent).
  if event.defineEvent then pcall(event.defineEvent, 'IvaliceLegacyCoop.alive', 'async') end
  rawset(_G, 'IvaliceLegacyCoop', {bridge = bridge, store = store, page = page, flows = flows})
end)
if not ok then print('Legacy co-op page: not started (' .. tostring(why) .. ')') end
