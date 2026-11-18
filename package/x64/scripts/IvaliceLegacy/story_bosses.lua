-- Ivalice Legacy: the main-story bosses that always take Legacy's crown (scripts/IvaliceLegacy/story_bosses.lua).
-- Keys are unit name ids (foe.unit.name). A listed foe is crowned only when its own unit row carries the game's boss or
-- hunt flag, so ordinary units sharing a name stay ordinary (the plain "Judge" rows of Sky Fortress Bahamut and
-- Draklor's 68th floor have neither flag). Allies and Trial Mode are never crowned. Optional bosses (non-story Espers,
-- Omega, the Necrohol, Sochen's Hell Wyrm and Mandragoras, the Great Crystal, Feywood and Ridorana extras), marks and
-- rare games take no crown.
local S = {}

S.IDS = {
  [16445] = "Nalbina Dungeons arena (Seeq)",
  [16876] = "Firemane (Garamsythe Waterway)",
  [16877] = "Mimic Queen (Barheim Passage)",
  [16885] = "Judge Ghis (Leviathan)",
  [16451] = "Judge (Leviathan, Mt Bur-Omisace; flagged rows only)",
  [16887] = "Ba'Gamnan (Lhusu Mines, Nam-Yensa)",
  [16461] = "Bwagi (Ba'Gamnan's gang)",
  [16462] = "Gijuk (Ba'Gamnan's gang)",
  [16463] = "Rinok (Ba'Gamnan's gang)",
  [16666] = "Garuda (Tomb of Raithwall)",
  [16875] = "Demon Wall (Tomb of Raithwall, both)",
  [16402] = "Belias (Tomb of Raithwall)",
  [16399] = "Vossler (Leviathan)",
  [16400] = "Vossler (second row)",
  [16860] = "Tiamat (Henne Mines)",
  [16874] = "Elder Wyrm (Golmore Jungle)",
  [16883] = "Judge Bergan (Mt Bur-Omisace)",
  [16878] = "Vinuskar (Stilshrine of Miriam)",
  [16409] = "Mateus (Stilshrine of Miriam)",
  [16763] = "Rook (Draklor Laboratory)",
  [16888] = "Doctor Cid (Draklor, Pharos)",
  [16873] = "Daedalus (Giruvegan)",
  [16857] = "Tyrant (Giruvegan)",
  [16412] = "Shemhazai (Giruvegan)",
  [16406] = "Hashmal (Pharos)",
  [16882] = "Gabranth (Pharos, Bahamut)",
  [16879] = "The Undying (Bahamut)",
  [16889] = "Vayne (Bahamut)",
  [16886] = "Vayne Novus (Bahamut)",
}

-- context: {trial = true | false | nil (unknown)}. Unknown fails closed: no crown.
function S.is(foe, context)
  local unit = foe.unit
  if not unit or not S.IDS[unit.name] then return false end
  if context.trial ~= false then return false end
  if not foe.keep or foe.keep.isFoe ~= 1 then return false end
  return unit.isBoss == 1 or unit.unitType7 == 1
end

return S
