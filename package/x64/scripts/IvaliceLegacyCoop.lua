-- Ivalice Legacy: co-op settings page and the Lua side of the co-op module bridge.
-- Loaded once by the FF12 LUA Loader. Everything is guarded: if a part fails the rest of the game is untouched.
local DIR = 'scripts/IvaliceLegacy/coop/'

local function load(name)
  local chunk, err = loadfile(DIR .. name .. '.lua', 't', _ENV)
  if not chunk then error('co-op: ' .. name .. ' not loaded (' .. tostring(err) .. ')') end
  return chunk()
end

-- First run only: is Ivalice Together installed? (Then the co-op module starts Off, as native also decides at init.)
-- The files native's own check looks for (a leftover folder alone does not count). Paths are relative to x64, where the
-- loader runs scripts [U: the filesystem plugin resolves relative paths against the game's working directory, as the
-- settings path does; forward slashes are accepted].
local TOGETHER_PATHS = {'modules/ivalice-together-autostart.dll', 'IvaliceTogetherRuntime/IvaliceTogetherRuntime.exe',
                        'IvaliceTogetherConfig.exe'}
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
  -- Legacy's MP economy parameters (absent without Legacy's mp_economy.lua): the co-op menus show what a cast costs.
  local economy = load('economy').provider(loadfile, _ENV)
  local store = load('store').create({
    codec = codec, config = config, io = io,
    fs = rawget(_G, 'filesystem'), log = print, together = togetherPresent, economy = economy,
  })
  store.load()
  local dialogs = load('dialogs').create({dialog = rawget(_G, 'dialog'), log = print})
  local flows = load('flows').create({store = store, bridge = bridge, codec = codec, views = views, dialogs = dialogs,
                                      log = print})
  local page = load('page').create({
    modmenu = modmenu, store = store, bridge = bridge, codec = codec, views = views, flows = flows, dialogs = dialogs,
    statusview = load('statusview'), after = event.executeAfterMs, log = print,
    -- Insurgent's Vision's settings, read only (the Battle HUD page shows its minimap/overhead/combat-log toggles)
    vision = function()
      local path, fs = 'scripts/config/TheInsurgentsVisionConfig.json', rawget(_G, 'filesystem')
      if fs and fs.exists and fs.exists(path) ~= true then return nil end   -- not installed: no read at all
      return config.loadJson(path)
    end,
  })
  page.start()
  -- The Legacy script states the HUD overlay shows (Broken, Sunder's dent, Guard/Cadence/Flow, proc tags): the Family
  -- module lives in the Forge script's own Lua state, which publishes them into a memory block (global symbol
  -- ilc_hud_share); this state reads that block every 250 ms and pushes it through the bridge. Read only; absent
  -- pieces show nothing.
  local clock = function() return type(event.getClock) == 'function' and event.getClock() or 0 end
  local hudModule = load('hud_states')
  local hudStates = hudModule.create({
    bridge = bridge, codec = codec, after = event.executeAfterMs, log = print, clock = clock,
    view = hudModule.share_reader({memory = memory, clock = clock, codec = codec}),
  })
  event.registerEventAsync('onInitDone', function()
    bridge.start()
    if bridge.is_ready() then store.sync(bridge) end
    hudStates.start()
  end)
  event.registerEventAsync('exit', function()
    flows.cancel()
    store.flush()
  end)
  -- Keep this script alive: it exits once its one-shot callbacks have fired unless something else is registered, and the
  -- page timer alone is not documented as keeping it alive. "Definitions keep their owner script alive even without
  -- callbacks" (loader docs, event.defineEvent).
  if event.defineEvent then pcall(event.defineEvent, 'IvaliceLegacyCoop.alive', 'async') end
  rawset(_G, 'IvaliceLegacyCoop', {bridge = bridge, store = store, page = page, flows = flows, hud_states = hudStates})
end)
if not ok then print('Legacy co-op page: not started (' .. tostring(why) .. ')') end
