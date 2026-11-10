-- Diagnostic 0.1.4 candidate for config/functions/261.lua. Installed Forge's
-- default onCastEnd includes 261; onHitEnd does not. Preserve the original and
-- observe cast resolution only. No middleware replacement or native hook.
local original = assert(loadfile('scripts/TheInsurgentsForge/functions/261.lua', 't', _ENV))()

-- Legacy MP economy (mp_economy.lua): after a party Magick, the caster pays the Magick-scaled surcharge once per cast.
-- onCastEnd runs for each target; only the initial target's call charges (as Forge's own 261 shows its log once).
local okEconomy, Economy = pcall(function() return assert(loadfile('scripts/IvaliceLegacy/mp_economy.lua', 't', _ENV))() end)
if not okEconomy then
  print('Legacy MP: economy not loaded (' .. tostring(Economy) .. '); spell costs stay native')
  Economy = nil
end
local MP_LOG_LIMIT, mpLogged = 40, 0
-- The surcharge belongs to the Legacy campaign, not to Proficiency: 0.1.22 gated it on R.active() (a claimed Clan rank),
-- so a campaign paid native costs until Montblanc's first reward. 0.1.23 asks only for a loaded Legacy campaign.
local function legacyCampaign()
  local R = rawget(_G, 'IvaliceLegacyProficiency')
  if not R then return true end
  if type(R.inCampaign) == 'function' then return R.inCampaign() end
  return type(R.active) ~= 'function' or R.active()
end
local function surcharge(formula, caster, target, helpers)
  if not Economy or caster.type ~= 0 or caster.address == 0 or target.address == 0 then return end
  if formula.causeType == 2 or formula.initialTarget.address ~= target.address then return end
  if type(bpack) ~= 'table' or not legacyCampaign() then return end
  local record = bpack.section14[formula.action]
  if not Economy.priced(record) then return end
  -- The surcharge scales what the game charged this caster (Channeling, Parsimony, Spellspring, Offering), not the
  -- listed cost (mp_economy 0.2.0). Offering pays in gil and Spellspring is free: no MP surcharge for either.
  local paid = record.mpOrMistCost
  if type(Economy.paid) == 'function' then
    paid = Economy.paid(record.mpOrMistCost, Economy.reader({formula.caster.augments, caster.permanentAugments,
      caster.temporaryAugments}))
  end
  local before = caster.currentMp
  local extra = math.min(Economy.surcharge(record.mpOrMistCost, caster.magickPower, paid), math.max(0, before))
  if extra <= 0 then return end
  helpers.modifyMp(caster.address, -extra)
  if mpLogged < MP_LOG_LIMIT then
    mpLogged = mpLogged + 1
    print(string.format('Legacy MP: cast action=%d cost=%d paid=%d magick=%d surcharge=%d mpLeft=%d', formula.action,
      record.mpOrMistCost, paid, caster.magickPower, extra, before - extra))  -- currentMp is live: read it before
  end
end
local function priced(inner)
  return function(formula, caster, target, functions, classes, helpers)
    local result = table.pack(inner(formula, caster, target, functions, classes, helpers))
    local ok, err = pcall(surcharge, formula, caster, target, helpers)
    if not ok and mpLogged < MP_LOG_LIMIT then
      mpLogged = MP_LOG_LIMIT
      print('Legacy MP: surcharge error=' .. tostring(err):gsub('[%z\r\n]', ' '))
    end
    return table.unpack(result, 1, result.n)
  end
end
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
  mpLogged = 0
  return true
end
local loaded = event.registerEventSync('onSaveLoad', reset)
local newGame = event.registerEventSync('onNewGameStart', reset)
if loaded ~= true or newGame ~= true then
  print('Legacy action: callback registration failed; observer disabled; bonuses=off')
  return priced(original)
end
print('Legacy action: diagnostic ' .. Diagnostic.version .. ' Forge261 onCastEnd observer ready; bonuses=off')
if Economy then print('Legacy MP: economy ' .. Economy.version .. ' ready (cost x Magick/' .. Economy.DIVISOR .. ', cap x' ..
  Economy.CAP .. ', returns <= ' .. math.floor(Economy.RETURN_SHARE * 100) .. '% per cast)') end
return priced(observer:wrap_action(original))
