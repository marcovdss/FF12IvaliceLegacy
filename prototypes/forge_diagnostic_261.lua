-- Diagnostic 0.1.4 candidate for config/functions/261.lua. Installed Forge's
-- default onCastEnd includes 261; onHitEnd does not. Preserve the original and
-- observe cast resolution only. No middleware replacement or native hook.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/261.lua', 't', _ENV))()
local Diagnostic = assert(loadfile('scripts/IvaliceLegacy/foe_profile_diagnostic.lua', 't', _ENV))()

local function action(formula, caster, target)
  if caster.type ~= 1 then return nil end
  local work = formula.caster.battleUnitWork
  if work.address == 0 or caster.address == 0 then return nil end
  local targetName
  if target.type == 1 then
    local targetWork = formula.target.battleUnitWork
    if targetWork.address ~= 0 then targetName = targetWork.ardUnit.name end
  end
  return {foe = true, nameId = work.ardUnit.name, unitId = work.identifier,
    location = formula.location, action = formula.action, targetType = target.type,
    targetId = target.identifier, targetName = targetName,
    currentHp = caster.currentHp, maxHp = caster.maxHp,
    clock = type(event.getClock) == 'function' and event.getClock() or nil}
end

local observer = Diagnostic.new({profile = function() return nil end, action = action,
  emit = function(line) print(line) end})
local function reset()
  observer:reset()
  return true
end
local loaded = event.registerEventSync('onSaveLoad', reset)
local newGame = event.registerEventSync('onNewGameStart', reset)
if loaded ~= true or newGame ~= true then
  print('Legacy action: callback registration failed; observer disabled; bonuses=off')
  return original
end
print('Legacy action: diagnostic ' .. Diagnostic.version .. ' Forge261 onCastEnd observer ready; bonuses=off')
return observer:wrap_action(original)
