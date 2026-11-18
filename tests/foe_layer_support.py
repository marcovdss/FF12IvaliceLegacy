"""A stand-in for the Lua Loader, Instanced Foes and a game's memory, to run the real foe layer files in Lupa.

Nothing here touches a game. The Instanced Foes stub follows its documented API (foes.list, foes.party, foes.onSpawn,
foe.claim, foe.refresh, sets with has/add/remove/list, lifeId and isPresent); a foe is a plain Lua table whose fields a test
can read back after the pass.
"""
import struct
from pathlib import Path

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'package/x64/scripts'
MODULES = SCRIPTS / 'IvaliceLegacy'

BANK_PTR, RESOURCE_BASE = 0x02EBF168, 0x01F83530
LOCATION, AREA_TABLE = 0x021654C4, 0x02ADD100
STORY_FLAG, STORY_TABLE = 0x02098E20, 0x02098E58
ACTIVE_SCRIPT = 0x6E0
BANK_AT = 0x30000000
SCRIPT_AT = 0x31000000
AREA_AT = 0x32000000
STORY_AT = 0x33000000

ELEMENTS = ['fire', 'lightning', 'ice', 'earth', 'water', 'wind', 'holy', 'dark']
STATUSES = ['ko', 'stone', 'petrify', 'stop', 'sleep', 'confuse', 'doom', 'blind', 'poison', 'silence', 'sap', 'oil',
            'reverse', 'disable', 'immobilize', 'slow', 'disease', 'lure', 'protect', 'shell', 'haste', 'bravery',
            'faith', 'reflect', 'invisible', 'regen', 'float', 'berserk', 'bubble', 'hpCritical', 'libra', 'xZone']

STUB = r'''
local mem, heap = {}, 0x70000000
local function read(a, n) local v = 0 for i = n - 1, 0, -1 do v = (v << 8) | (mem[a + i] or 0) end return v end
local function write(a, n, v) for i = 0, n - 1 do mem[a + i] = (v >> (8 * i)) & 0xFF end end
local function typed(n, signed)
  return setmetatable({}, {
    __index = function(_, a)
      local v = read(a, n)
      if signed and v >= (1 << (8 * n - 1)) then v = v - (1 << (8 * n)) end
      return v
    end,
    __newindex = function(_, a, v) write(a, n, v) end,
  })
end
memory = {u8 = typed(1), u16 = typed(2), u32 = typed(4), u64 = typed(8), s32 = typed(4, true)}
function memory.readArray(a, n) local t = {} for i = 1, n do t[i] = mem[a + i - 1] or 0 end return t end
function memory.writeArray(a, t) for i = 1, #t do mem[a + i - 1] = t[i] end return true end
function memory.alloc(n) local at = heap; heap = heap + ((n + 15) // 16) * 16 + 16; return at end

printed = {}
function print(...) local t = {} for i = 1, select('#', ...) do t[#t + 1] = tostring((select(i, ...))) end printed[#printed + 1] = table.concat(t, '\t') end

timers = {}
flagCallbacks, events, menus = {}, {}, {}
event = {
  executeAfterMs = function(ms, fn) timers[#timers + 1] = fn end,
  registerFlagCallback = function(name, cb) flagCallbacks[name] = cb; if name == 'fehdead_instancedfoes.ready' then cb(1, true) end return true end,
  registerEventAsync = function(name, cb) events[name] = cb return true end,
  getFlag = function() return true end,
}
function runTimers() local t = timers; timers = {} for _, fn in ipairs(t) do fn() end end
files = {}
config = {loadJson = function(p) return files[p] end, saveJson = function(p, d) files[p] = d; saved = d; return true end}
modmenu = {addTable = function(t) menus[#menus + 1] = t return true end}

-- Instanced Foes stand-in ---------------------------------------------------------------------------------------
local STATUS_IDS, ELEMENT_IDS = {}, {}
for i, n in ipairs(STATUSES) do STATUS_IDS[n] = i - 1 end
for i, n in ipairs(ELEMENTS) do ELEMENT_IDS[n] = i - 1 end
local function newSet(ids, bits)
  local on = {}
  local set = {}
  local function id(k) local v = ids[k] or k; assert(type(v) == 'number' and v >= 0 and v < bits, 'no such name ' .. tostring(k)) return v end
  local function each(keys, fn) if type(keys) ~= 'table' then keys = {keys} end for _, k in ipairs(keys) do fn(id(k)) end end
  function set.has(keys) local ok = true each(keys, function(i) if not on[i] then ok = false end end) return ok end
  function set.add(keys) each(keys, function(i) on[i] = true end) end
  function set.remove(keys) each(keys, function(i) on[i] = nil end) end
  function set.list() local t = {} for i = 0, bits - 1 do if on[i] then t[#t + 1] = i end end return t end
  return set
end
function makeSet(ids, bits, list) local s = newSet(ids, bits) if list then s.add(list) end return s end
function newElements(list) return makeSet(ELEMENT_IDS, 8, list) end
function newStatuses(list) return makeSet(STATUS_IDS, 32, list) end
function newDrops() return makeSet({drop1 = 0, drop2 = 1, drop3 = 2, drop4 = 3, drop5 = 4, monograph = 5, canopicJar = 6}, 7) end

foeList, listeners, claimed, refreshes = {}, {}, {}, 0
foes = {READY_FLAG = 'fehdead_instancedfoes.ready', SPAWN = 1, RESPAWN = 2, RESPAWN_NEW_UNIT = 3, autoRefresh = true,
        present = {}, partyStats = nil, reserved = 0}
function foes.ready() return true end
function foes.list() return foeList end
function foes.onSpawn(fn) listeners[#listeners + 1] = fn end
function foes.party() return foes.partyStats end
function foes.isPresent(id) return foes.present[id] == true end
function foes.reserve(name, bytes) foes.reserved = foes.reserved + 1 return foes.noReserve and -1 or 16 end
require = function(name) assert(name == 'scripts.InstancedFoes.Helper') return foes end

function makeFoe(o)
  o = o or {}
  local f = {lifeId = o.lifeId or 1, foeId = -1, buw = o.buw or 0x50000000}
  f.unit = {name = o.name or 16447, isBoss = o.isBoss or 0, unitType7 = o.hunt or 0, sizeX = o.size or 100, sizeY = o.size or 100,
            sizeZ = o.size or 100, drops = o.drops or {0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF},
            steals = o.steals or {0x2001, 0x2002, 0x2003}, battleLogicIdentifiers = o.links or {0, 0, 0, 0}}
  f.defaultStats = o.stats or {maxHp = 1000, maxMp = 50, strength = 10, magickPower = 10, vitality = 10, speed = 10,
                               defense = 10, magickResist = 10, attackPower = 10, licensePoints = 10}
  f.additiveStats = o.additive or {maxHp = 100, maxMp = 10, strength = 0, magickPower = 0, vitality = 1, speed = 0,
                                   defense = 1, magickResist = 0, attackPower = 0, licensePoints = 0}
  f.keep = {isFoe = o.isFoe == nil and 1 or o.isFoe, currentHp = 0, currentMp = 0}
  f.class = {affinityWeak = newElements(o.weak), affinityAbsorb = newElements(o.absorb)}
  f.abilities = {statuses = newStatuses(o.statuses), immunities = newStatuses(), alwaysDrops = newDrops()}
  f.battleLogicSlots = {slots = o.slots or {0, 0, 0, 0}}
  f.color = o.color or {r = 1, g = 1, b = 1, a = 1}
  f.efx, f.efxColor = -1, nil
  f.names = {}
  function f.setName(n) f.names[#f.names + 1] = n end
  function f.claim(at) if claimed[f.lifeId] then return false end claimed[f.lifeId] = true return true end
  function f.refresh() refreshes = refreshes + 1 return 1 end
  foeList[#foeList + 1] = f
  foes.present[f.lifeId] = true
  return f
end
function spawn(foe, reason) for _, fn in ipairs(listeners) do fn(foe, reason or 1) end end

-- the real files, loaded from the repository
function loadfile(path, mode, env)
  local name = path:match('([%w_]+)%.lua$')
  local src = SOURCES[name]
  if not src then return nil, 'no such file ' .. path end
  return load(src, name, 't', env)
end
'''


def lua(sources=None, **globals_):
    """A Lupa runtime with the stand-in installed. sources: {module name: lua text} (defaults to the repository files)."""
    if LuaRuntime is None:
        raise RuntimeError('lupa is not installed')
    rt = LuaRuntime(unpack_returned_tuples=True)
    g = rt.globals()
    g.ELEMENTS = rt.table_from(ELEMENTS)
    g.STATUSES = rt.table_from(STATUSES)
    files = {}
    for name in ('foe_layer', 'foe_crown', 'story_bosses', 'foe_tint', 'crown_table'):
        files[name] = (MODULES / f'{name}.lua').read_text(encoding='utf-8')
    files['Foes'] = 'return {[16447] = "common", [16448] = "mark", [16449] = "boss", [16450] = "rare", [16451] = "ally", ' \
                    '[16402] = "boss", [16445] = "boss"}'
    if sources:
        files.update(sources)
    g.SOURCES = rt.table_from(files)
    rt.execute(STUB)
    return rt


def script(groups):
    """Bytes of a Battle Logic script. groups: a list of up to 32 lists of rules; a rule is
    (action, parameter, flags, [(target, condition, parameter), ...]) with up to three cases."""
    header = bytearray(32)
    body = bytearray()
    for index, rules in enumerate(groups):
        header[index] = len(rules)
        for action, parameter, flags, cases in rules:
            cases = list(cases) + [(0, 0, 0)] * (3 - len(cases))
            body += struct.pack('<HhH', action, parameter, flags)
            for target, condition, p in cases:
                body += struct.pack('<HHh', target, condition, p)
    return bytes(header + body)


def parse_rules(raw):
    """[(group, action, parameter, flags, cases)] of a script, to compare what an insert did."""
    counts, rules, at = list(raw[:32]), [], 32
    for group, count in enumerate(counts):
        for _ in range(count):
            action, parameter, flags = struct.unpack_from('<HhH', raw, at)
            cases = [struct.unpack_from('<HHh', raw, at + 6 + 6 * i) for i in range(3)]
            rules.append((group, action, parameter, flags, cases))
            at += 24
    return counts, rules


def install_bank(rt, scripts):
    """Write a bank of scripts into the fake memory the way the game holds them: count, unknown, then offsets that are
    absolute addresses (the resource base is 0). Returns each script's address."""
    g = rt.globals()
    mem = g.memory
    addresses, at = [], SCRIPT_AT
    for raw in scripts:
        addresses.append(at)
        mem.writeArray(at, rt.table_from(list(raw)))
        at += (len(raw) + 15) // 16 * 16 + 16
    bank = bytearray(struct.pack('<HH', len(scripts), 0))
    for address in addresses + [at]:
        bank += struct.pack('<I', address)
    mem.writeArray(BANK_AT, rt.table_from(list(bank)))
    mem.u64[BANK_PTR] = BANK_AT
    mem.u64[RESOURCE_BASE] = 0
    return addresses


def read_bytes(rt, address, length):
    return bytes(rt.globals().memory.readArray(address, length).values())


def set_world(rt, location=100, town_locations=(), story=None):
    g = rt.globals()
    g.memory.u32[LOCATION] = location & 0xFFFFFFFF
    count = 2000
    table = bytearray(8) + bytearray(3 * count)
    struct.pack_into('<I', table, 4, count)
    for town in town_locations:
        table[8 + 3 * town + 1] |= 0x80
    g.memory.writeArray(AREA_AT, rt.table_from(list(table)))
    g.memory.u64[AREA_TABLE] = AREA_AT
    if story is not None:
        g.memory.u8[STORY_FLAG] = 1
        g.memory.writeArray(STORY_AT, rt.table_from(list(struct.pack('<H', story))))
        g.memory.u64[STORY_TABLE] = STORY_AT
