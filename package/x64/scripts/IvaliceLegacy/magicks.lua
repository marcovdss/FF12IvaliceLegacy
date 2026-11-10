-- Magicks: the party's attack spells, on the same climb as the weapons.
--
-- Arsenal took weapons from a typical hit of about 1,375 by the last shops to about 830. Spells kept their native power,
-- so by then a spell hit 4 to 7 times as hard as a weapon. These are the numbers each spell is set to, one at a time.
-- A spell costs MP and a charge, so it stays stronger than a swing: more where it hits one foe, less where it hits many.
--
-- Only the party's casts use this. Foes cast the same action rows, and their spells stay as they are, which is why this is
-- a Forge function and not an edit to the action table. MP cost, area and targeting stay native.
local M = {version = '0.2.1'}

-- [action] = {name, native power, power here, why}
M.SPELLS = {
  [21] = {'Aqua', 37, 27, 'Single target and cheap at its stage: stays the best damage per MP there, not 3 weapon hits'},
  [22] = {'Aero', 51, 28, 'First area spell: its reach is the reward, so each foe takes about a strong swing'},
  [23] = {'Fira', 67, 40, 'Area, 16 MP: the middle tier the party lives on from Ozmone on; keeps the order of its siblings'},
  [24] = {'Thundara', 68, 41, 'Fira\'s sibling, 2 MP dearer, one point more as in the base game'},
  [25] = {'Blizzara', 70, 43, 'Fira\'s sibling, 4 MP dearer, and it arrives a stage later'},
  [26] = {'Bio', 87, 52, 'Area and adds its ailment, so it sits above the -ra tier but needs a fight long enough to pay off'},
  [27] = {'Aeroga', 103, 60, 'The first of the -ga tier: area, and wind is rarely resisted'},
  [28] = {'Firaga', 120, 68, 'Area for 38 MP: strong against many, and against one a full weapon round beats it'},
  [29] = {'Thundaga', 122, 70, 'Firaga\'s sibling, arriving a stage later'},
  [30] = {'Blizzaga', 125, 72, 'Firaga\'s sibling, dearest of the three'},
  [31] = {'Shock', 133, 92, 'Single target for only 34 MP: the efficient late nuke, at about twice a weapon hit'},
  [32] = {'Scourge', 142, 84, 'Late area spell for 48 MP'},
  [33] = {'Flare', 163, 98, 'Single-target capstone: nearly 3 weapon hits for 46 MP. Powerful, as the end of the game should be'},
  [34] = {'Ardor', 173, 84, 'Late Arcane area spell, 60 MP: below Scathe, which costs more'},
  [35] = {'Scathe', 190, 92, 'The biggest area spell at the highest cost, 70 MP: a full bar buys about five'},
  [17] = {'Holy', 157, 100, 'The White Mage\'s one attack: single target, 48 MP, kept close to Flare'},
  [76] = {'Dark', 46, 24, 'Area for 10 MP, and native it hits 3 times a weapon at its stage, far above its Black Magick peers'},
  [77] = {'Darkra', 91, 40, 'Same tier as Fira for 24 MP; dark is rarely resisted, so it does not need more'},
  [78] = {'Darkga', 130, 66, 'Same tier as Firaga, 38 MP'},
}
-- Fire, Thunder and Blizzard stay native: at the start they are already a little above a swing, and low is the design.
-- Drain, Syphon and the Gravity spells work on other numbers and are not spell damage here. Cures stay native.

-- Technicks: what Expose and Shear take from a foe. Wither and Addle take 30%, and these took 10%, too little to answer
-- an armoured or warded foe. Foes' own uses stay native.
M.BREAK = 0.7

-- Decoy (action 70, the Lure status): about 45 seconds when the party casts it. Native is the status table's 20 plus a
-- level share, gone too fast to plan around. 0.2.0's two minutes made a shield Knight a set-and-forget wall (Marco,
-- 2026-11-10): 45 s keeps Decoy a choice the tank renews. Not a table edit: Knight's Proficiency Lure scales from that 20, and foes use
-- it. The game turns a table value into ticks as value * 2 * 300, so 45 is 27000; that the value reads as seconds is inferred
-- from the table (Stop 30, Bubble 240), not yet timed in game.
M.DECOY_ACTION, M.DECOY_STATUS, M.DECOY_TICKS = 70, 17, 45 * 2 * 300

function M.power(action)
  local s = M.SPELLS[action]
  return s and s[3] or nil
end

return M
