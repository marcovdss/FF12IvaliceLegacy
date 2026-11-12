-- Ivalice Legacy co-op: the native message windows the page uses (notices, questions, the pairing panel).
--
-- Everything goes through the loader's `dialog` service and is guarded: when the service is missing or refuses, the text
-- is logged and the flow carries on without a window. External text (controller names) is cleaned first, because the
-- game's message markup treats braces, brackets and backslashes as tags.
local M = {}

M.SCREEN = {x = 960, y = 540, align = 4}          -- the middle of the 1920x1080 virtual canvas
M.PANEL = {x = 960, y = 300, align = 4}
M.NAME_MAX = 40
M.WRAP = 62                                        -- characters per line in a native message window

-- deps: dialog (the loader table, optional), log(text)
function M.create(deps)
  local dlg = deps.dialog
  local log = deps.log or function() end
  local D = {opened = 0}

  function D.available() return type(dlg) == 'table' and type(dlg.show) == 'function' end

  -- Printable ASCII only, no markup characters, at most NAME_MAX characters.
  function D.clean(text, max)
    local t = tostring(text or ''):gsub('[^ -~]', ' '):gsub('[{}%[%]\\]', ' '):gsub('%s+', ' ')
    t = t:gsub('^ ', ''):gsub(' $', '')
    if t == '' then t = 'Controller' end
    return t:sub(1, max or M.NAME_MAX)
  end

  -- Break long paragraphs at spaces (the window does not wrap by itself). Existing line breaks are kept.
  function D.wrap(text, width)
    width = width or M.WRAP
    local out = {}
    for para in (tostring(text) .. '\n'):gmatch('(.-)\n') do
      local line = ''
      for word in para:gmatch('%S+') do
        if line ~= '' and #line + 1 + #word > width then
          out[#out + 1] = line
          line = word
        else
          line = line == '' and word or (line .. ' ' .. word)
        end
      end
      out[#out + 1] = line
    end
    return table.concat(out, '\n')
  end

  local function show(cfg)
    if not D.available() then return nil, 'no dialog service' end
    cfg.body = '{speed:0}' .. D.wrap(cfg.body)
    cfg.queue = true
    local ok, handle, why = pcall(dlg.show, cfg)
    if not ok then
      log('dialog failed: ' .. tostring(handle))
      return nil, tostring(handle)
    end
    if not handle then log('dialog refused: ' .. tostring(why)) end
    D.opened = D.opened + 1
    return handle, why
  end

  -- A notice with one OK choice.
  function D.notify(text)
    log('dialog: ' .. tostring(text))
    show({body = text, choice = {'OK'}, cursor = 0, cancel = 0, closeOnCancel = true, x = M.SCREEN.x,
          y = M.SCREEN.y, align = M.SCREEN.align, dim = true, lockInterface = true})
  end

  -- A question. choices: labels; the last one is the cancel choice. onPick(index) gets the 0-based index of the choice
  -- the player took, or nil when the window was dismissed some other way. Returns true when a window was requested.
  function D.ask(body, choices, onPick)
    log('dialog: ' .. tostring(body))
    local handle = show({body = body, choice = choices, cursor = 0, cancel = #choices - 1,
                         closeOnCancel = true, x = M.SCREEN.x, y = M.SCREEN.y, align = M.SCREEN.align, dim = true,
                         lockInterface = true,
                         onClose = function(index) if onPick then onPick(type(index) == 'number' and index or nil) end end})
    if not handle and onPick then onPick(nil) end
    return handle ~= nil
  end

  -- An information panel that stays beside the Mod Menu and never takes the controller: it is not dismissed by a
  -- Confirm press (a Confirm on Player 1's own pad is part of the pairing proof). The page's "Cancel" row ends it.
  -- Returns {setText(text), close(), alive()} or nil.
  function D.panel(body, lines)
    log('dialog: ' .. tostring(body))
    local handle = show({body = body, lines = lines or 3, x = M.PANEL.x, y = M.PANEL.y,
                         align = M.PANEL.align, focus = false, dismissible = false})
    if not handle then return nil end
    local P = {}
    function P.setText(text)
      log('dialog: ' .. tostring(text))
      local ok, accepted = pcall(handle.setText, handle, '{speed:0}' .. D.wrap(text))
      return ok and accepted == true
    end
    function P.close()
      local ok = pcall(handle.close, handle)
      return ok
    end
    function P.alive()
      local ok, alive = pcall(handle.alive, handle)
      return ok and alive == true
    end
    return P
  end

  return D
end

return M
