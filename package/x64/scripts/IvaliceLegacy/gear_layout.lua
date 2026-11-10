-- Ivalice Legacy: shorter equipment descriptions.
--
-- Dynamic Description (DD) draws every status group (Hit, Equip, Immune) and element group (Weak, Immune, Half,
-- Absorb, Amplify) as "multi" blocks, which always start a line of their own and leave status and elements on
-- separate lines, often under a half-empty stats line. With the Proficiency line under them the text reached the
-- bottom of the screen. Legacy's equipment templates (icons 0-36) drop those rows and list one DD custom attribute,
-- legacyExtras, after the stats. This module fills it: DD's own renderer draws the stats and the groups exactly as
-- before, and the groups are packed by width - on the last stats line when they fit, then on as few lines as
-- possible. Values come from DD's own parser reading the live equipment table; the text goes in through DD's public
-- API (dd_api.lua). DD clears customAttributes.json each time it loads, so this runs after every script has loaded
-- (onInitDone) and asks DD to re-render once. Nothing in game memory is written.

local M = {}

M.version = '0.2.0'
M.ID = 'legacyExtras'
M.LIMIT = 80              -- characters per line (icons count 2, a wide space 2); the widest stock lines are 84
M.TEMPLATES = {0, 36}     -- DD template keys equipment_0..36 (the item's icon)

local DD = 'scripts/DynamicDescription/'
local DD_CONFIG = 'scripts/config/DynamicDescription/'

-- Visible width of DD text: tags are free, an icon counts 2, a wide space 2, a low space 1.
function M.width(s)
  s = s:gsub('{widespace}', '  '):gsub('{lowspace}', ' '):gsub('{icon:%d+}', '##'):gsub('{[^}]*}', '')
  return #s
end

local function plainLabel(head)
  return (head:gsub('{[^}]*}', ''):gsub(':%s*$', ''))
end

-- stats: the stats row's rendered parts (DD strings); maxitems: that row's limit; groups: DD's multi groups
-- ({_groupLabel = label text, values = {...}}). Returns the custom attribute's label and value, or nil.
-- sp = {low, wide, newline}.
function M.pack(stats, maxitems, groups, sp, limit)
  if #groups == 0 then return nil end
  limit = limit or M.LIMIT
  local cur = 0
  local n = #stats
  if n > 0 and n % maxitems ~= 0 then
    local first = n - (n % maxitems) + 1
    for i = first, n do cur = cur + M.width(stats[i]) + (i > first and 2 or 0) end
    cur = cur + 2 -- DD's wide space before this attribute
  end
  local label, out = nil, {}
  for gi, g in ipairs(groups) do
    local head = g._groupLabel
    local whole = M.width(head) + 1
    for _, v in ipairs(g.values) do whole = whole + M.width(v) + 1 end
    if gi == 1 then
      local text = plainLabel(head)
      if cur > 0 and cur + whole - 1 > limit then
        label, cur = sp.newline .. text, 0
      else
        label = text
      end
      cur = cur + M.width(head) + 1
    else
      if cur + 2 + whole - 1 > limit then
        out[#out + 1] = sp.newline
        cur = 0
      else
        out[#out + 1] = sp.wide
        cur = cur + 2
      end
      out[#out + 1] = head .. sp.low
      cur = cur + M.width(head) + 1
    end
    for vi, v in ipairs(g.values) do
      local w = M.width(v)
      if vi > 1 then
        if cur + 1 + w > limit then
          out[#out + 1] = sp.newline
          cur = 0
        else
          out[#out + 1] = sp.low
          cur = cur + 1
        end
      end
      out[#out + 1] = v
      cur = cur + w
    end
  end
  return label, table.concat(out)
end

local function load(path, env)
  local chunk, err = loadfile(path, 't', env or setmetatable({}, {__index = _G}))
  if not chunk then error(path .. ': ' .. tostring(err), 0) end
  local value = chunk()
  if value == nil then error(path .. ': returned nothing', 0) end
  return value
end

-- DD's modules, initialised the way DynamicDescription.lua does it (own copies; DD's state is not touched).
function M.dd()
  local m = {}
  for _, k in ipairs({'cache', 'parser', 'renderer', 'template', 'utils', 'mappings', 'schemas'}) do
    m[k] = load(DD .. k .. '.lua')
  end
  for _, k in ipairs({'tags', 'i18n', 'settings'}) do m[k] = load(DD_CONFIG .. k .. '.lua') end
  m.api = load(DD .. 'dd_api.lua')
  m.cache.initialize({mappings = m.mappings, schemas = m.schemas})
  m.utils.initialize({rtl = m.settings.rtl == true})
  m.parser.initialize({mappings = m.mappings})
  m.renderer.initialize({i18n = m.i18n, tags = m.tags, mappings = m.mappings, schemas = m.schemas, utils = m.utils,
                         cache = m.cache, customData = {}, settings = m.settings})
  m.template.initialize({schemas = m.schemas, utils = m.utils, cache = m.cache})
  return m
end

-- The row of a template that lists legacyExtras, or nil.
local function extrasRow(rows)
  for _, row in ipairs(rows or {}) do
    for _, block in ipairs(row.blocks) do
      if block.type == 'customAttributes' then
        for _, id in ipairs(block.content) do if id == M.ID then return row end end
      end
    end
  end
end

local STATUS = {section = 'equipment', type = 'statusEffect', size = 'md', content = {'hit', 'equip', 'immune'},
                args = {showLabel = true, showIcon = true}}
local ELEMENT = {section = 'equipment', type = 'elementalAffinity', size = 'md',
                 content = {'weak', 'immune', 'half', 'absorb', 'potency'}, args = {showLabel = true, showIcon = true}}

-- {bitid, label, value} for one parsed item, or nil when its template has no legacyExtras or it has no groups.
function M.entry(m, item, rows, limit)
  local row = extrasRow(rows)
  if not row then return nil end
  local stats = {}
  for _, block in ipairs(row.blocks) do
    if block.type == 'attributes' then
      for _, p in ipairs(m.renderer.renderAttributes(item, block, row.args)) do stats[#stats + 1] = p end
    elseif block.type == 'common' then
      for _, p in ipairs(m.renderer.renderCommon(item, block, row.args)) do stats[#stats + 1] = p end
    end
  end
  local groups = {}
  for _, g in ipairs(m.renderer.renderStatusOrAffinity(item, STATUS, 'statusEffect', row.args)) do groups[#groups + 1] = g end
  for _, g in ipairs(m.renderer.renderStatusOrAffinity(item, ELEMENT, 'elementalAffinity', row.args)) do groups[#groups + 1] = g end
  local sp = {low = m.tags.space.low or ' ', wide = m.tags.space.wide or '  ', newline = m.tags.char.newline or '\n'}
  local label, value = M.pack(stats, row.args.maxitems or 999, groups, sp, limit)
  if not label then return nil end
  return {bitid = item._bitid, label = label, value = value}
end

function M.templates(m)
  local rows = {}
  for i = M.TEMPLATES[1], M.TEMPLATES[2] do
    local key = 'equipment_' .. i
    local ok, text = pcall(load, DD_CONFIG .. 'templates/equipment/' .. i .. '.lua')
    if ok and type(text) == 'string' then
      m.template.validate(text, key)
      rows[key] = m.template.get(key).rows
    end
  end
  return rows
end

-- Returns the number of descriptions given extras, or nil plus a reason.
function M.apply()
  local m = M.dd()
  if type(m.api.customAttribute) ~= 'table' or type(m.api.refresh) ~= 'function' then return nil, 'DD API not found' end
  local limit = m.settings.equipment and m.settings.equipment.itemLimit or 0
  if type(limit) ~= 'number' or limit <= 0 then return nil, 'no equipment limit in DD settings' end
  local rows = M.templates(m)
  local count = 0
  for i = 0, limit - 1 do
    local data = bpack.section13[i]
    local item = data and m.parser.parseItem(data, i, 13)
    local e = item and M.entry(m, item, rows[item._templateKey])
    if e then
      m.api.customAttribute.create(e.bitid, M.ID, e.label, e.value, false)
      count = count + 1
    end
  end
  m.api.refresh()
  return count
end

function M.register()
  event.registerEventAsync('onInitDone', function()
    local ok, count, why = pcall(M.apply)
    if ok and count then
      print(('Legacy gear layout %s: %d descriptions packed'):format(M.version, count))
    else
      print('Legacy gear layout: not applied (' .. tostring(ok and why or count) .. ')')
    end
  end)
  return true
end

return M
