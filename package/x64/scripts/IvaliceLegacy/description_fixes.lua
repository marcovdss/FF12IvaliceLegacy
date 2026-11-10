-- Legacy owns these notes; DD's labels, layout and native calculations remain.
local M = {}

function M.decorate(base, corrections)
  assert(type(base) == 'table' and type(base.database) == 'table', 'Missing description database')
  assert(type(corrections) == 'table', 'Missing description corrections')
  -- Validate the entire update before touching shared metadata. A failed load
  -- must leave the original database intact for the caller's fallback.
  for group, entries in pairs(corrections) do
    assert(type(entries) == 'table', 'Invalid description entries')
    local target = base.database[group]
    assert(type(target) == 'table', 'Missing description group: ' .. group)
    for id, entry in pairs(entries) do
      assert(type(id) == 'number' and id % 1 == 0 and id >= 0 and type(entry) == 'table'
        and type(entry.notes) == 'string' and (entry.notes == '' or entry.notes:match('%S')), 'Invalid description correction')
      assert(entry.name == nil or (type(entry.name) == 'string' and entry.name:match('%S')), 'Invalid description name')
      assert(entry.license == nil or type(entry.license) == 'string', 'Invalid description license')
    end
  end
  for group, entries in pairs(corrections) do
    local target = base.database[group]
    for id, entry in pairs(entries) do
      -- Preserve any third-party fields and all consumers of the same entry.
      local row = target[id]
      if type(row) ~= 'table' then row = {} target[id] = row end
      row.notes = entry.notes
      if entry.name then row.name = entry.name end
      if entry.license then row.license = entry.license end
    end
  end
  return base
end

return M
