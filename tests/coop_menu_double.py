"""A recording Mod Menu double for the co-op page tests that ENFORCES the rules the real Mod Menu plugin
(ff12-modmenu.lua.dll) keeps, so a page bug of that class fails a test instead of filling the game log.

Rules enforced (read from the plugin's own strings and the page's use; nothing here is invented to be convenient):

* ``setActive`` only on a row the Mod Menu owns as a value row (toggle or carousel). On a status row, a button, a
  submenu or a divider the plugin answers "cannot set active state for '<mod>.<id>': it is not an owned value row" (124
  such lines filled the first session's log). The double records the refusal in ``calls.refusals`` and raises.
* ``setValue`` / ``getValue`` only on value rows, and a value is an integer inside the row's option list.
* ``setStatus`` only on status rows.
* ``setVisible`` and every other call name only rows that were registered.
* ``addTable`` rejects a row without a string id and label, an unknown row type, a toggle or carousel whose option list
  or integer default is not valid, a submenu without rows, and an id used twice.

``getValue`` returns what the page last set (the row's default at first), so a page that redraws every tick calls
``setValue`` only when a value really changed. Use ``MENU_DOUBLE + <harness>``; the harness calls ``newMenu(calls)``.
"""

MENU_DOUBLE = r"""
local VALUE_TYPES = {toggle = true, carousel = true}
local ROW_TYPES = {toggle = true, carousel = true, statusButton = true, button = true, submenu = true, divider = true}

local function newMenu(calls)
  calls.visible, calls.tables, calls.status = calls.visible or {}, calls.tables or {}, calls.status or {}
  calls.refusals, calls.counts = {}, {setValue = 0, setActive = 0, setStatus = 0, setVisible = 0}
  local mm, rows = {}, {}
  mm.rows = rows
  local function refuse(text)
    calls.refusals[#calls.refusals + 1] = text
    error(text, 0)
  end
  local function find(mod, id, call)
    local r = rows[id]
    if not r then refuse(string.format("%s: '%s.%s' is not a registered row", call, tostring(mod), tostring(id))) end
    return r
  end
  local function register(list, where)
    if type(list) ~= 'table' then refuse(where .. ': a page has no row list') end
    for i = 1, #list do
      local row = list[i]
      if type(row.id) ~= 'string' or row.id == '' then refuse(where .. ': row ' .. i .. ' has no id') end
      if type(row.label) ~= 'string' then refuse(row.id .. ' has no label') end
      if not ROW_TYPES[row.type] then refuse(row.id .. ' has an unknown type ' .. tostring(row.type)) end
      if rows[row.id] then refuse('row id used twice: ' .. row.id) end
      rows[row.id] = {type = row.type, row = row, value = row.default}
      if VALUE_TYPES[row.type] then
        if type(row.options) ~= 'table' or #row.options < 2 then refuse(row.id .. ': a value row needs two options or more') end
        for k = 1, #row.options do
          if type(row.options[k]) ~= 'string' then refuse(row.id .. ': option ' .. k .. ' is not text') end
        end
        if math.type(row.default) ~= 'integer' or row.default < 0 or row.default >= #row.options then
          refuse(row.id .. ': default ' .. tostring(row.default) .. ' is not an option index')
        end
        if type(row.callback) ~= 'function' then refuse(row.id .. ': a value row needs a callback') end
      end
      if row.type == 'submenu' then
        if type(row.sub) ~= 'table' or #row.sub == 0 then refuse(row.id .. ': a submenu needs rows') end
        register(row.sub, row.id)
      end
    end
  end
  function mm.addTable(t)
    calls.tables[#calls.tables + 1] = t
    register(t.schema, tostring(t.id))
    return true
  end
  function mm.setActive(mod, id, flag)
    calls.counts.setActive = calls.counts.setActive + 1
    local r = find(mod, id, 'setActive')
    if not VALUE_TYPES[r.type] then
      refuse(string.format("Mod Menu: cannot set active state for '%s.%s': it is not an owned value row.", mod, id))
    end
    if type(flag) ~= 'boolean' then refuse('setActive: the state of ' .. id .. ' is not a boolean') end
    r.active = flag
  end
  function mm.setValue(mod, id, v)
    calls.counts.setValue = calls.counts.setValue + 1
    local r = find(mod, id, 'setValue')
    if not VALUE_TYPES[r.type] then refuse("setValue: '" .. id .. "' is not a value row") end
    if math.type(v) ~= 'integer' or v < 0 or v >= #r.row.options then
      refuse("setValue: '" .. id .. "' has no option " .. tostring(v))
    end
    r.value = v
  end
  function mm.getValue(mod, id)
    local r = find(mod, id, 'getValue')
    if not VALUE_TYPES[r.type] then refuse("getValue: '" .. id .. "' is not a value row") end
    return r.value
  end
  function mm.setStatus(mod, id, text)
    calls.counts.setStatus = calls.counts.setStatus + 1
    local r = find(mod, id, 'setStatus')
    if r.type ~= 'statusButton' then refuse("setStatus: '" .. id .. "' is not a status row") end
    if type(text) ~= 'string' then refuse("setStatus: the text of '" .. id .. "' is not text") end
    calls.status[id] = text
  end
  function mm.setVisible(mod, changes)
    calls.counts.setVisible = calls.counts.setVisible + 1
    for id, flag in pairs(changes) do
      find(mod, id, 'setVisible')
      if type(flag) ~= 'boolean' then refuse("setVisible: the state of '" .. id .. "' is not a boolean") end
      rows[id].visible = flag
    end
    calls.visible[#calls.visible + 1] = changes
  end
  return mm
end
"""
