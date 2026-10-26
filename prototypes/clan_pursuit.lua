-- Pilot Clan line "Pursuit": a base Hunter's ordinary Attack with a dagger
-- deals more damage to a target already hindered by an ailment.
-- Wraps the dagger/bow damage function (Forge66): the original runs first,
-- then only the current formula's scratch power changes. Actor and shared
-- item records are never written.
local M = {}

M.HUNTER = 8
M.NO_JOB = 255
M.DAGGER_CATEGORY = 12
M.ATTACK = 150
M.POWER_SCALE = 1.25
-- Sleep already scales physical damage natively and stays separate.
M.AILMENTS = {"blind", "poison", "silence", "slow", "sap", "disease", "immobilize", "disable", "oil"}

-- First hindering ailment on the formula's target, or nil.
function M.ailment(formula)
  local statuses = formula.target and formula.target.statusEffects
  if not statuses then return nil end
  for _, name in ipairs(M.AILMENTS) do
    if statuses[name] == 1 then return name end
  end
  return nil
end

-- deps.active() -> true while a Legacy campaign is loaded
-- deps.context(caster) -> {kind = "main-party", actorId = n} for a canonical party actor
-- deps.weaponCategory(caster) -> native weapon category, or nil
function M.applies(formula, caster, deps)
  if deps.active() ~= true or formula.action ~= M.ATTACK then return nil end
  local context = deps.context(caster)
  if type(context) ~= "table" or context.kind ~= "main-party" or context.actorId ~= caster.identifier then
    return nil
  end
  -- Base Hunter only; hybrids receive their own authored lines.
  if caster.job1 ~= M.HUNTER or caster.job2 ~= M.NO_JOB then return nil end
  if deps.weaponCategory(caster) ~= M.DAGGER_CATEGORY then return nil end
  return M.ailment(formula)
end

function M.wrap66(original, deps)
  assert(type(original) == "function", "original function required")
  return function(formula, caster, target, ...)
    local result = table.pack(original(formula, caster, target, ...))
    -- A reader failure must never interrupt Forge or change its result.
    local ok, ailment = pcall(M.applies, formula, caster, deps)
    if ok and ailment then
      local before = formula.power
      formula.power = before * M.POWER_SCALE
      if deps.report then pcall(deps.report, caster, ailment, before, formula.power) end
    end
    return table.unpack(result, 1, result.n)
  end
end

return M
