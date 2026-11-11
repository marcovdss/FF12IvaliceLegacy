-- Hybrid Proficiency: one Clan line per job pair (66), three stages of four levels, like the base jobs.
-- A character with two jobs uses its pair's line instead of either parent's (the Clan stage carries over).
-- Every stage is data: the event it listens to, a condition the engine can observe, and effects that grow by level.
-- The in-game text is generated from the same data, so the description always says what the rule does.
--
-- Stage  = {event, condition list (all must hold) or nil, trigger phrase, 's'|'p' (verb number), effects}
-- Effect = {op, ..., f = first level (default 1)}; a value may be a number or a table of four (levels 1-4).
-- Events: hit (weapon hit on a foe), magick (damage spell on a foe), heal (heal on an ally, items included),
--         guard (a foe's attack on you was parried, blocked or dodged), counter (you counterattack),
--         struck (a foe's hit damaged you), ally (an ally dropped to critical HP), status (an ailment you landed with
--         an attack or a spell), cast (a status spell you cast landed: castFoe on a foe, castAlly on an ally, cast on either).
local H = {}

local h, t, x = 0.5, 0.75, 1  -- status durations: half, 3/4, full

H.LINES = {
  -- 0 Heaven Knight (Lancer + White Mage)
  {line = 'Crusade',
   {'hit', {'buffed'}, 'Hits on a buffed foe', 'p', {{'strip', {'protect', 'shell'}}, {'ally', 'regen', {h, h, h, t}},
     {'ally', 'protect', h, f = 2}, {'strip', {'haste', 'faith'}, f = 3}}},
   {'heal', {'allyAiling'}, 'Healing an ally with an ailment', 's', {{'cureAlly', {1, 1, 2, 2}}, {'ally', 'faith', h},
     {'ally', 'bravery', h, f = 2}, {'mp', 3, f = 4}}},
   {'hit', {'kill'}, 'A kill', 's', {{'party', 'bravery', {h, h, h, t}}, {'party', 'faith', h, f = 2},
     {'mp', 5, f = 3}}}},
  -- 1 Chaplain (Gunner + White Mage)
  {line = 'Last Rites',
   {'hit', {'afterHeal'}, 'Your first hit after a heal', 's', {{'push', {10, 10, 10, 15}}, {'dmg', 15, f = 2},
     {'allyHeal', 10, f = 3}}},
   {'heal', nil, 'Healing an ally', 's', {{'ally', 'protect', {h, h, h, t}}, {'ally', 'bravery', h, f = 2},
     {'allyMp', 5, f = 3}}},
   {'ally', nil, 'An ally dropping to critical HP', 's', {{'ally', 'haste', {h, h, h, t}}, {'ally', 'regen', h},
     {'self', 'faith', h, f = 2}, {'quick', 20, f = 3}}}},
  -- 2 Seer (Green Mage + White Mage)
  {line = 'Foresight',
   {'hit', nil, 'Your hits', 'p', {{'drain', {5, 5, 5, 8}}, {'allyHeal', 10, f = 2}, {'heal', 5, f = 3}}},
   {'heal', {'overflow'}, 'Healing past full HP', 's', {{'ally', 'bubble', {h, h, h, t}}, {'overflowMp', 5, f = 2},
     {'ally', 'faith', h, f = 3}}},
   {'ally', nil, 'An ally dropping to critical HP', 's', {{'ally', 'bubble', {h, h, h, t}}, {'ally', 'regen', h, f = 2},
     {'ally', 'shell', h, f = 3}, {'mp', 5, f = 4}}}},
  -- 3 Paladin (Knight + White Mage)
  {line = 'Vow',
   {'heal', nil, 'Healing an ally', 's', {{'self', 'lure', {h, h, t, t}}, {'allyRemove', {'slow', 'immobilize', 'disable'}},
     {'ally', 'haste', h, f = 2}, {'ally', 'protect', h, f = 4}}},
   {'hit', {'lured'}, 'While you have Lure, your hits', 'p', {{'allyHeal', {10, 10, 10, 20}}, {'ally', 'shell', h, f = 2},
     {'push', 10, f = 3}}},
   {'heal', {'allyCritical'}, 'Healing an ally at critical HP', 's', {{'ally', 'protect', h}, {'ally', 'shell', h},
     {'ally', 'regen', h, f = 2}, {'self', 'faith', h, f = 3}, {'quick', 20, f = 4}}}},
  -- 4 White Monk (Mystic + White Mage)
  {line = 'Ki Healing',
   {'counter', nil, 'Each counter', 's', {{'hp', {5, 5, 5, 8}}, {'cureAlly', 1, f = 2}, {'mp', 3, f = 3}}},
   {'hit', {'chain2'}, '2 hits in a row on one foe', 'p', {{'cureAlly', {1, 1, 1, 2}}, {'mp', 3, f = 2},
     {'ally', 'protect', h, f = 3}}},
   {'counter', nil, 'Each counter', 's', {{'ally', 'regen', {h, h, h, t}}, {'self', 'protect', h, f = 2},
     {'party', 'shell', h, f = 3}}}},
  -- 5 Cleric (Time Mage + White Mage)
  {line = 'Dawnwind',
   {'heal', {'overflow'}, 'Healing past full HP', 's', {{'ally', 'haste', {h, h, h, t}}, {'other', 'slow', h, f = 2},
     {'overflowMp', 5, f = 3}}},
   {'heal', {'allyHasted'}, 'Healing a Hasted ally', 's', {{'ally', 'regen', {h, h, h, t}}, {'ally', 'shell', h, f = 2},
     {'quick', 15, f = 3}}},
   {'ally', nil, 'An ally dropping to critical HP', 's', {{'ally', 'haste', {h, h, h, t}}, {'party', 'regen', h, f = 2},
     {'ally', 'bravery', h, f = 3}}}},
  -- 6 Crusader (Warrior + White Mage)
  {line = 'Chakra',
   {'hit', {'low50'}, 'Below half HP, your hits', 'p', {{'heal', {10, 10, 10, 15}}, {'self', 'regen', h, f = 2},
     {'cure', 1, f = 3}}},
   {'hit', {'chain2'}, '2 hits in a row on one foe', 'p', {{'self', 'regen', {h, h, h, t}}, {'drain', 5, f = 2},
     {'ally', 'bravery', h, f = 3}}},
   {'heal', nil, 'Healing an ally', 's', {{'hp', {5, 5, 5, 8}}, {'self', 'bravery', h, f = 2},
     {'ally', 'regen', h, f = 3}}}},
  -- 7 Bard (Hunter + White Mage)
  {line = 'Ballad',
   {'hit', {'ailing'}, 'Hits on a foe with an ailment', 'p', {{'allyHeal', {10, 10, 10, 15}}, {'ally', 'protect', h, f = 2},
     {'push', 10, f = 3}}},
   {'heal', nil, 'Healing an ally', 's', {{'ally', 'faith', {h, h, h, t}}, {'self', 'libra', x, f = 2},
     {'allyMp', 5, f = 3}}},
   {'heal', {'afterHeal'}, 'A second heal in a row', 's', {{'party', 'haste', {h, h, h, t}}, {'mp', 3, f = 2},
     {'quick', 15, f = 3}}}},
  -- 8 Red Mage (White Mage + Black Mage)
  {line = 'Doublecast',
   {'magick', {'afterHeal'}, 'A damage spell right after a heal', 's', {{'dmg', {15, 15, 15, 25}}, {'mp', 3, f = 2},
     {'foe', 'oil', h, f = 3}}},
   {'heal', {'afterMagick'}, 'A heal right after another spell', 's', {{'ally', 'regen', {h, h, h, t}},
     {'quick', 15, f = 2}, {'ally', 'bravery', h, f = 3}}},
   {'magick', {'sameElement'}, 'The same element twice in a row', 's', {{'quick', {20, 20, 20, 30}}, {'self', 'faith', h, f = 2},
     {'party', 'shell', h, f = 3}}}},
  -- 9 Sohei (Samurai + White Mage)
  {line = 'Kaji',
   {'heal', nil, 'Healing an ally', 's', {{'self', 'haste', {h, h, h, t}}, {'ally', 'bravery', h, f = 2},
     {'overflowMp', 5, f = 3}}},
   {'hit', {'afterHeal'}, 'Your first hit after a heal', 's', {{'dmg', {20, 20, 20, 30}}, {'foe', 'blind', h, f = 2},
     {'allyHeal', 10, f = 3}}},
   {'hit', {'hasted', 'kill'}, 'A kill while Hasted', 's', {{'party', 'regen', {h, h, h, t}}, {'mp', 5, f = 2},
     {'allyRemove', {'poison', 'stop'}, f = 3}}}},
  -- 10 Bishop (Chemist + White Mage)
  {line = 'Sacrament',
   {'heal', nil, 'Healing an ally', 's', {{'nearHeal', {20, 20, 20, 35}}, {'mp', 2, f = 2}, {'ally', 'bravery', h, f = 3}}},
   {'heal', {'afterHeal'}, 'A second heal in a row', 's', {{'quick', {15, 15, 15, 25}}, {'ally', 'protect', h, f = 2},
     {'allyMp', 5, f = 3}}},
   {'heal', {'allyAiling'}, 'Healing an ally with an ailment', 's', {{'cureAlly', {1, 1, 2, 2}},
     {'allyRemove', {'disease'}, f = 2}, {'ally', 'haste', {h, h, h, t}, f = 3}}}},
  -- 11 Cannoneer (Lancer + Gunner)
  {line = 'Siege',
   {'hit', {'firstHit'}, 'Your first hit on each foe', 's', {{'strip', {'protect'}}, {'push', {10, 10, 15, 15}},
     {'dmg', 15, f = 2}, {'strip', {'shell'}, f = 4}}},
   {'hit', {'buffless'}, 'Hits on a foe with no buffs', 'p', {{'dmg', {15, 15, 15, 25}}, {'ally', 'bravery', h, f = 2},
     {'foe', 'silence', h, f = 3}}},
   {'hit', {'kill'}, 'A kill', 's', {{'quick', {20, 20, 20, 30}}, {'self', 'haste', h, f = 2},
     {'pushOther', 15, f = 3}}}},
  -- 12 Breaker (Lancer + Green Mage)
  {line = 'Unmake',
   {'hit', {'ailing'}, 'Hits on a foe with an ailment', 'p', {{'strip', {'protect', 'shell'}}, {'strip', {'haste'}, f = 2},
     {'drain', 5, f = 3}, {'strip', {'regen', 'faith'}, f = 4}}},
   {'hit', {'buffless'}, 'Hits on a foe with no buffs', 'p', {{'dmg', {15, 15, 15, 25}}, {'self', 'shell', h, f = 2},
     {'allyHeal', 10, f = 3}}},
   {'hit', {'buffless', 'ailing'}, 'Hits on a bare foe with an ailment', 'p', {{'foe', 'berserk', {h, h, h, t}},
     {'mp', 3, f = 2}, {'party', 'protect', h, f = 3}}}},
  -- 13 Templar (Knight + Lancer)
  {line = 'Phalanx',
   {'guard', {'parryBlock'}, 'A parry or block', 's', {{'self', 'lure', {h, h, t, t}}, {'foe', 'slow', {h, h, h, t}, f = 2},
     {'strip', {'bravery', 'protect'}, f = 3}}},
   {'hit', {'buffless'}, 'Hits on a foe with no buffs', 'p', {{'ally', 'protect', {h, h, h, t}}, {'dmg', 15, f = 2},
     {'drain', 5, f = 3}}},
   {'guard', {'parry'}, 'A parry', 's', {{'foe', 'immobilize', {h, h, h, t}}, {'self', 'bravery', h, f = 2},
     {'party', 'protect', h, f = 3}}}},
  -- 14 Hell Knight (Lancer + Mystic)
  {line = 'Hellfire',
   {'counter', nil, 'Each counter', 's', {{'strip', {'protect', 'haste'}}, {'hp', {5, 5, 5, 8}},
     {'foe', 'immobilize', h, f = 2}, {'strip', {'bravery', 'faith'}, f = 3}}},
   {'magick', nil, 'Your damage spells', 'p', {{'strip', {'shell'}}, {'drain', {5, 5, 5, 8}, f = 2},
     {'foe', 'silence', h, f = 3}}},
   {'hit', {'buffless', 'ailing'}, 'Hits on a bare foe with an ailment', 'p', {{'dmg', {20, 20, 20, 30}},
     {'self', 'faith', h, f = 2}, {'allyHeal', 10, f = 3}}}},
  -- 15 Chrono Lancer (Lancer + Time Mage)
  {line = 'Window',
   {'castFoe', {'slowCast'}, 'Casting Slow on a foe', 's', {{'strip', {'haste', 'protect'}}, {'strip', {'float', 'faith'}, f = 2},
     {'mp', {3, 3, 3, 5}, f = 3}}},
   {'hit', {'buffless'}, 'Hits on a foe with no buffs', 'p', {{'foe', 'slow', {h, h, h, t}}, {'mp', 2, f = 2},
     {'ally', 'bravery', h, f = 3}}},
   {'hit', {'slowed', 'buffless'}, 'Hits on a bare Slowed foe', 'p', {{'dmg', {20, 20, 20, 30}}, {'extend', 'slow', 10, f = 2},
     {'foe', 'disable', h, f = 3}}}},
  -- 16 Ravager (Lancer + Warrior)
  {line = 'Havoc',
   {'hit', {'low50'}, 'Below half HP, your hits', 'p', {{'strip', {'protect'}}, {'dmg', {15, 15, 15, 25}, f = 2},
     {'strip', {'shell', 'regen'}, f = 3}}},
   {'hit', {'buffless', 'kill'}, 'Killing a foe with no buffs', 's', {{'other', 'slow', h}, {'heal', {10, 10, 10, 15}, f = 2},
     {'self', 'bravery', h, f = 3}}},
   {'hit', {'critical'}, 'At critical HP, your hits', 'p', {{'dmg', {20, 20, 20, 30}}, {'allyHeal', 10, f = 2},
     {'self', 'shell', h, f = 3}}}},
  -- 17 Beastslayer (Lancer + Hunter)
  {line = 'Big Game',
   {'hit', {'healthy'}, 'Hits on a foe above half HP', 'p', {{'dmg', {15, 15, 15, 25}}, {'strip', {'regen'}},
     {'self', 'libra', x, f = 2}, {'foe', 'disease', h, f = 3}}},
   {'hit', {'wounded'}, 'Hits on a foe below half HP', 'p', {{'strip', {'protect', 'shell'}}, {'heal', 10, f = 2},
     {'push', {10, 10, 10, 15}, f = 3}}},
   {'hit', {'kill', 'ailing'}, 'Killing a foe with an ailment', 's', {{'party', 'bravery', {h, h, h, t}},
     {'party', 'haste', h, f = 2}, {'mp', 5, f = 3}}}},
  -- 18 Dragonkin (Lancer + Black Mage)
  {line = 'Fracture',
   {'magick', nil, 'Your damage spells', 'p', {{'strip', {'shell'}}, {'strip', {'reflect'}, f = 2},
     {'push', 10, f = 3}, {'strip', {'protect', 'faith'}, f = 4}}},
   {'magick', {'sameElement'}, 'The same element twice in a row', 's', {{'dmg', {15, 15, 15, 25}}, {'mp', 3, f = 2},
     {'allyHeal', 10, f = 3}}},
   {'magick', {'buffless'}, 'Spells on a foe with no buffs', 'p', {{'dmg', {15, 15, 15, 25}}, {'other', 'oil', h, f = 2},
     {'party', 'faith', h, f = 3}}}},
  -- 19 Dragoon (Lancer + Samurai)
  {line = 'Highwind',
   {'hit', {'chain2'}, '2 hits in a row on one foe', 'p', {{'strip', {'float', 'haste'}}, {'self', 'float', h, f = 2},
     {'drain', 5, f = 3}, {'strip', {'protect'}, f = 4}}},
   {'hit', {'floating'}, 'While you Float, your hits', 'p', {{'dmg', {15, 15, 15, 25}}, {'push', 10, f = 2},
     {'allyMp', 5, f = 3}}},
   {'hit', {'floating', 'buffless'}, 'Floating hits on a bare foe', 'p', {{'foe', 'immobilize', {h, h, h, t}},
     {'quick', 20, f = 2}, {'party', 'bravery', h, f = 3}}}},
  -- 20 Despoiler (Lancer + Chemist)
  {line = 'Corrode',
   {'status', nil, 'An ailment you land', 's', {{'strip', {'shell'}}, {'strip', {'protect'}, f = 2},
     {'self', 'faith', h, f = 3}, {'strip', {'regen', 'haste'}, f = 4}}},
   {'hit', {'ailing', 'buffless'}, 'Hits on a bare foe with an ailment', 'p', {{'foe', 'disease', {h, h, h, t}},
     {'dmg', 15, f = 2}, {'allyMp', 5, f = 3}}},
   {'hit', {'diseased'}, 'Hits on a Diseased foe', 'p', {{'renew'}, {'foe', 'silence', h, f = 2},
     {'self', 'haste', h, f = 3}, {'push', 10, f = 4}}}},
  -- 21 Trickster (Gunner + Green Mage)
  {line = 'Dirty Trick',
   {'status', nil, 'An ailment you land', 's', {{'push', {10, 10, 10, 15}}, {'drain', 5, f = 2}, {'self', 'haste', h, f = 3}}},
   {'hit', {'ailing', 'buffed'}, 'Hits on a buffed foe with an ailment', 'p', {{'steal'}, {'foe', 'silence', h, f = 2},
     {'heal', {10, 10, 10, 15}, f = 3}}},
   {'hit', {'ailing'}, 'Hits on a foe with an ailment', 'p', {{'dmg', {15, 15, 15, 25}}, {'otherCopy', h, f = 2},
     {'party', 'bravery', h, f = 3}}}},
  -- 22 Gunbreaker (Knight + Gunner)
  {line = 'Overwatch',
   {'hit', nil, 'Your hits', 'p', {{'push', {10, 10, 10, 15}}, {'self', 'lure', h}, {'self', 'protect', h, f = 2},
     {'dmg', 10, f = 3}}},
   {'struck', {'lured'}, 'Being struck while you have Lure', 's', {{'self', 'protect', {h, h, h, t}},
     {'foe', 'slow', h, f = 2}, {'drainFoe', 5, f = 3}}},
   {'hit', {'about'}, 'Hits on a foe about to act', 'p', {{'dmg', {20, 20, 20, 30}}, {'ally', 'shell', h, f = 2},
     {'party', 'protect', h, f = 3}}}},
  -- 23 Ki Gunner (Gunner + Mystic)
  {line = 'Hex Round',
   {'counter', nil, 'Each counter', 's', {{'push', {10, 10, 10, 15}}, {'self', 'faith', h, f = 2},
     {'strip', {'shell'}, f = 3}}},
   {'hit', {'afterMagick'}, 'Your first hit after a spell', 's', {{'dmg', {20, 20, 20, 30}}, {'drain', 5, f = 2},
     {'ally', 'bravery', h, f = 3}}},
   {'counter', nil, 'Each counter', 's', {{'foe', 'immobilize', {h, h, h, t}}, {'mp', 3, f = 2},
     {'party', 'faith', h, f = 3}}}},
  -- 24 Sharpshooter (Gunner + Time Mage)
  {line = 'Timing',
   {'hit', {'slowed'}, 'Hits on a Slowed foe', 'p', {{'push', {10, 10, 10, 15}}, {'self', 'bravery', h, f = 2},
     {'foe', 'silence', h, f = 3}}},
   {'hit', {'about'}, 'Hits on a foe about to act', 'p', {{'dmg', {15, 15, 15, 25}}, {'extend', 'slow', 10, f = 2},
     {'foe', 'immobilize', h, f = 3}}},
   {'hit', {'stopped'}, 'Hits on a Stopped foe', 'p', {{'extend', 'stop', 10}, {'mp', 2, f = 2}, {'dmg', 15, f = 3},
     {'party', 'haste', h, f = 4}}}},
  -- 25 Corsair (Warrior + Gunner)
  {line = 'Point Blank',
   {'hit', {'near', 'low50'}, 'Up close below half HP, your hits', 'p', {{'dmg', {15, 15, 15, 25}}, {'push', 10, f = 2},
     {'foe', 'slow', h, f = 3}}},
   {'hit', {'kill'}, 'A kill', 's', {{'quick', {20, 20, 20, 30}}, {'heal', 10, f = 2}, {'pushOther', 15, f = 3}}},
   {'hit', {'critical'}, 'At critical HP, your hits', 'p', {{'dmg', {25, 25, 25, 35}}, {'self', 'protect', h, f = 2},
     {'foe', 'immobilize', h, f = 3}}}},
  -- 26 Sniper (Hunter + Gunner)
  {line = 'Scope',
   {'hit', {'ailing'}, 'Hits on a foe with an ailment', 'p', {{'push', {10, 10, 10, 15}}, {'self', 'libra', x, f = 2},
     {'self', 'bravery', h, f = 3}}},
   {'hit', {'chain2'}, '2 hits in a row on one foe', 'p', {{'dmg', {15, 15, 15, 25}}, {'self', 'shell', h, f = 2},
     {'allyHeal', 10, f = 3}}},
   {'hit', {'chain3'}, 'Every 3rd hit in a row on one foe', 's', {{'dmg', {30, 30, 30, 40}}, {'foe', 'disable', h, f = 2},
     {'mp', 5, f = 3}}}},
  -- 27 Artillerist (Gunner + Black Mage)
  {line = 'Barrage',
   {'magick', nil, 'Your damage spells', 'p', {{'push', {5, 5, 10, 10}}, {'foe', 'blind', h, f = 2},
     {'mp', 2, f = 4}}},
   {'hit', {'afterMagick'}, 'Your first hit after a spell', 's', {{'dmg', {15, 15, 15, 25}}, {'mp', 3},
     {'foe', 'immobilize', h, f = 2}, {'heal', 10, f = 3}}},
   {'magick', {'oiled'}, 'Spells on an Oiled foe', 'p', {{'dmg', {15, 15, 15, 25}}, {'self', 'faith', h, f = 2},
     {'foe', 'sap', h, f = 3}}}},
  -- 28 Gunslinger (Samurai + Gunner)
  {line = 'Quickdraw',
   {'hit', {'chain2'}, '2 hits in a row on one foe', 'p', {{'push', {10, 10, 10, 15}}, {'mp', 2}, {'self', 'haste', h, f = 2},
     {'foe', 'silence', h, f = 3}}},
   {'hit', {'hasted'}, 'While Hasted, your hits', 'p', {{'evade'}, {'drain', 5, f = 2}, {'foe', 'slow', h, f = 3},
     {'dmg', 10, f = 4}}},
   {'hit', {'fullMp'}, 'At full MP, your hits', 'p', {{'dmg', {20, 20, 20, 30}}, {'allyMp', 5, f = 2},
     {'party', 'haste', h, f = 3}}}},
  -- 29 Engineer (Gunner + Chemist)
  {line = 'Field Kit',
   {'status', nil, 'An ailment you land', 's', {{'spread', {35, 35, 35, 50}}, {'push', 10, f = 2}, {'dmg', 10, f = 3}}},
   {'heal', {'item'}, 'Using a healing item on an ally', 's', {{'ally', 'protect', {h, h, h, t}}, {'allyMp', 5, f = 2},
     {'ally', 'regen', h, f = 3}}},
   {'hit', {'ailing2'}, 'Hits on a foe with 2+ ailments', 'p', {{'allyHeal', {10, 10, 10, 15}}, {'foe', 'oil', h, f = 2},
     {'party', 'shell', h, f = 3}}}},
  -- 30 Warden (Knight + Green Mage)
  {line = 'Snare',
   {'status', nil, 'An ailment you land', 's', {{'self', 'lure', {h, h, t, t}}, {'drain', {5, 5, 5, 8}, f = 2},
     {'foe', 'oil', h, f = 3}}},
   {'struck', {'lured'}, 'Being struck while you have Lure', 's', {{'foe', 'blind', {h, h, h, t}},
     {'strip', {'bravery'}, f = 2}, {'drainFoe', 5, f = 3}}},
   {'struck', {'low50'}, 'Being struck below half HP', 's', {{'foe', 'disable', {h, h, h, t}}, {'self', 'regen', h, f = 2},
     {'self', 'shell', h, f = 3}}}},
  -- 31 Monk (Green Mage + Mystic)
  {line = 'Umbra',
   {'counter', nil, 'Each counter', 's', {{'renewFoe'}, {'drainFoe', {5, 5, 5, 8}, f = 2}, {'ally', 'shell', h, f = 3}}},
   {'magick', {'ailing'}, 'Spells on a foe with an ailment', 'p', {{'drain', {5, 5, 5, 8}}, {'renew', f = 2},
     {'foe', 'sap', h, f = 3}}},
   {'hit', {'kill', 'ailing'}, 'Killing a foe with an ailment', 's', {{'otherCopy', {h, h, h, t}}, {'self', 'faith', h, f = 2},
     {'party', 'haste', h, f = 3}}}},
  -- 32 Oracle (Green Mage + Time Mage)
  {line = 'Prophecy',
   {'hit', {'slowed'}, 'Hits on a Slowed foe', 'p', {{'drain', {5, 5, 5, 8}}, {'dmg', 10, f = 2}, {'self', 'faith', h, f = 3}}},
   {'status', {'slowed'}, 'An ailment you land on a Slowed foe', 's', {{'foe', 'disable', {h, h, h, t}}, {'mp', 3, f = 2},
     {'ally', 'shell', h, f = 3}}},
   {'hit', {'stopped'}, 'Hits on a Stopped foe', 'p', {{'foe', 'silence', {h, h, h, t}}, {'allyHeal', 10, f = 2},
     {'party', 'faith', h, f = 3}}}},
  -- 33 Lanista (Warrior + Green Mage)
  {line = 'Leech',
   {'hit', {'low50'}, 'Below half HP, your hits', 'p', {{'drain', {5, 5, 5, 8}}, {'heal', 10, f = 2},
     {'self', 'bravery', h, f = 3}}},
   {'hit', {'oiled'}, 'Hits on an Oiled foe', 'p', {{'dmg', {15, 15, 15, 25}}, {'drain', 5, f = 2},
     {'foe', 'disable', h, f = 3}}},
   {'status', {'critical'}, 'An ailment you land at critical HP', 's', {{'self', 'regen', {h, h, h, t}},
     {'self', 'haste', h, f = 2}, {'party', 'regen', h, f = 3}}}},
  -- 34 Stalker (Hunter + Green Mage)
  {line = 'Quarry',
   {'hit', {'ailing'}, 'Hits on a foe with an ailment', 'p', {{'drain', {5, 5, 5, 8}}, {'self', 'libra', x, f = 2},
     {'self', 'protect', h, f = 3}}},
   {'magick', {'ailing'}, 'Spells on a foe with an ailment', 'p', {{'dmg', {15, 15, 15, 25}}, {'self', 'bravery', h, f = 2},
     {'drain', 5, f = 3}}},
   {'hit', {'blinded'}, 'Hits on a Blinded foe', 'p', {{'push', {10, 10, 10, 15}}, {'heal', 10, f = 2},
     {'party', 'protect', h, f = 3}}}},
  -- 35 Warlock (Green Mage + Black Mage)
  {line = 'Malediction',
   {'magick', {'ailing'}, 'Spells on a foe with an ailment', 'p', {{'drain', {5, 5, 5, 8}}, {'foe', 'poison', h, f = 2},
     {'allyHeal', 10, f = 3}}},
   {'magick', {'oiled'}, 'Spells on an Oiled foe', 'p', {{'dmg', {15, 15, 15, 25}}, {'self', 'faith', h, f = 2},
     {'push', 10, f = 3}}},
   {'magick', {'ailing2'}, 'Spells on a foe with 2+ ailments', 'p', {{'dmg', {20, 20, 20, 30}}, {'foe', 'disable', h, f = 2},
     {'party', 'shell', h, f = 3}}}},
  -- 36 Assassin (Samurai + Green Mage)
  {line = 'Ansatsu',
   {'hit', {'chain2'}, '2 hits in a row on one foe', 'p', {{'drain', {5, 5, 5, 8}}, {'self', 'haste', h, f = 2},
     {'heal', 10, f = 3}}},
   {'hit', {'disabled'}, 'Hits on a Disabled foe', 'p', {{'extend', 'disable', 10}, {'push', 10, f = 2},
     {'ally', 'haste', {h, h, h, t}, f = 3}}},
   {'hit', {'wounded', 'ailing'}, 'Hits on a wounded foe with an ailment', 'p', {{'dmg', {25, 25, 25, 40}},
     {'foe', 'disable', h, f = 2}, {'party', 'faith', h, f = 3}}}},
  -- 37 Alchemist (Chemist + Green Mage)
  {line = 'Transmute',
   {'status', nil, 'An ailment you land', 's', {{'spread', {35, 35, 35, 50}}, {'drain', 5, f = 2}, {'ally', 'faith', h, f = 3}}},
   {'heal', {'item'}, 'Using a healing item on an ally', 's', {{'cureAlly', {1, 1, 2, 2}}, {'ally', 'regen', h, f = 2},
     {'drainNear', {5, 5, 5, 8}, f = 3}}},
   {'status', nil, 'An ailment you land', 's', {{'ally', 'shell', {h, h, h, t}}, {'allyHeal', 10, f = 2},
     {'party', 'regen', h, f = 3}}}},
  -- 38 Dark Knight (Knight + Mystic)
  {line = 'Abyss',
   {'counter', nil, 'Each counter', 's', {{'self', 'lure', {h, h, t, t}}, {'hp', {5, 5, 5, 8}, f = 2}, {'drainFoe', 5, f = 3}}},
   {'magick', {'foeFaith'}, 'Spells on a foe with Faith', 'p', {{'strip', {'faith'}}, {'self', 'faith', h},
     {'push', {10, 10, 10, 15}, f = 2}, {'ally', 'shell', h, f = 3}}},
   {'struck', nil, 'Being struck', 's', {{'quick', {10, 10, 10, 20}}, {'self', 'protect', h, f = 2}, {'self', 'haste', h, f = 3}}}},
  -- 39 Chrono Knight (Knight + Time Mage)
  {line = 'Tempo Guard',
   {'castFoe', {'slowCast'}, 'Casting Slow on a foe', 's', {{'self', 'lure', {h, h, t, t}}, {'self', 'protect', h, f = 2},
     {'mp', {3, 3, 3, 5}, f = 3}}},
   {'guard', {'parryBlock'}, 'A parry or block', 's', {{'foe', 'slow', {h, h, h, t}}, {'self', 'haste', h, f = 2},
     {'pushOther', 10, f = 3}}},
   {'hit', {'stopped'}, 'Hits on a Stopped foe', 'p', {{'heal', {10, 10, 10, 15}}, {'extend', 'stop', 10, f = 2},
     {'party', 'bravery', h, f = 3}}}},
  -- 40 Defender (Knight + Warrior)
  {line = 'Last Stand',
   {'struck', {'low50'}, 'Being struck below half HP', 's', {{'self', 'lure', {h, h, t, t}}, {'self', 'bravery', h},
     {'push', 10, f = 2}, {'drainFoe', {5, 5, 5, 8}, f = 3}}},
   {'hit', {'low50'}, 'Below half HP, your hits', 'p', {{'foe', 'blind', {h, h, h, t}}, {'dmg', 10, f = 2},
     {'ally', 'protect', h, f = 3}}},
   {'struck', {'dropCritical'}, 'Dropping to critical HP', 's', {{'party', 'bravery', {h, h, h, t}}, {'self', 'protect', h},
     {'self', 'shell', h, f = 2}, {'foe', 'disable', h, f = 3}}}},
  -- 41 Riskbreaker (Knight + Hunter)
  {line = 'Watch',
   {'hit', {'ailing'}, 'Hits on a foe with an ailment', 'p', {{'self', 'lure', h}, {'dmg', {10, 10, 15, 20}, f = 2},
     {'pushOther', 10, f = 3}}},
   {'guard', nil, 'A parry, block or dodge', 's', {{'self', 'libra', x}, {'ally', 'shell', {h, h, h, t}, f = 2},
     {'foe', 'blind', h, f = 3}}},
   {'ally', nil, 'An ally dropping to critical HP', 's', {{'self', 'lure', {h, h, h, t}}, {'self', 'bravery', h},
     {'ally', 'regen', h, f = 2}, {'quick', 20, f = 3}}}},
  -- 42 Rune Knight (Knight + Black Mage)
  {line = 'Runeguard',
   {'magick', nil, 'Your damage spells', 'p', {{'self', 'lure', h}, {'self', 'protect', {h, h, h, t}, f = 2},
     {'foe', 'blind', h, f = 3}}},
   {'magick', {'lured'}, 'While you have Lure, your spells', 'p', {{'quick', {15, 15, 15, 25}}, {'heal', 10, f = 2},
     {'self', 'faith', h, f = 3}}},
   {'guard', {'parryBlock'}, 'A parry or block', 's', {{'self', 'shell', {h, h, h, t}}, {'mp', 3, f = 2},
     {'foe', 'immobilize', h, f = 3}}}},
  -- 43 Sword Saint (Knight + Samurai)
  {line = 'Iai',
   {'guard', {'parryBlock'}, 'A parry or block', 's', {{'self', 'lure', h}, {'self', 'haste', {h, h, h, t}}, {'mp', 3, f = 2},
     {'foe', 'disable', h, f = 3}}},
   {'hit', {'afterGuard'}, 'Your first hit after a parry or block', 's', {{'dmg', {20, 20, 20, 30}}, {'heal', 10, f = 2},
     {'push', 10, f = 3}}},
   {'hit', {'chain2'}, '2 hits in a row on one foe', 'p', {{'foe', 'immobilize', {h, h, h, t}}, {'self', 'faith', h, f = 2},
     {'allyHeal', 10, f = 3}}}},
  -- 44 Quartermaster (Knight + Chemist)
  {line = 'Provision',
   {'heal', {'item'}, 'Using a healing item on an ally', 's', {{'self', 'lure', {h, h, t, t}}, {'ally', 'protect', h},
     {'ally', 'haste', h, f = 2}, {'mp', {3, 3, 3, 5}, f = 3}}},
   {'heal', {'item', 'lured'}, 'A healing item while you have Lure', 's', {{'ally', 'shell', {h, h, h, t}}, {'hp', 5, f = 2},
     {'quick', 15, f = 3}}},
   {'heal', {'allyWarded'}, 'Healing an ally with Bubble or Regen', 's', {{'ally', 'bravery', {h, h, h, t}}, {'mp', 3, f = 2},
     {'party', 'regen', h, f = 3}}}},
  -- 45 Astrologer (Time Mage + Mystic)
  {line = 'Starfall',
   {'counter', {'slowed'}, 'A counter on a Slowed foe', 's', {{'stopChance', {15, 15, 25, 25}}, {'hp', 5, f = 2},
     {'self', 'protect', h, f = 3}, {'push', 10, f = 4}}},
   {'magick', {'stopped'}, 'Spells on a Stopped foe', 'p', {{'extend', 'stop', 10}, {'foe', 'oil', h, f = 2},
     {'drain', 5, f = 3}, {'dmg', 15, f = 4}}},
   {'magick', {'slowed'}, 'Spells on a Slowed foe', 'p', {{'dmg', {15, 15, 15, 25}}, {'party', 'faith', h, f = 2},
     {'pushOther', 10, f = 3}}}},
  -- 46 Fell Knight (Warrior + Mystic)
  {line = 'Blood Price',
   {'counter', nil, 'Each counter', 's', {{'hp', {5, 5, 5, 8}}, {'self', 'bravery', h, f = 2}, {'dmgNext', 15, f = 3}}},
   {'magick', {'low50'}, 'Below half HP, your spells', 'p', {{'strip', {'shell'}}, {'heal', {10, 10, 15, 20}, f = 2},
     {'self', 'bravery', h, f = 3}}},
   {'magick', {'critical'}, 'At critical HP, your spells', 'p', {{'mp', {3, 3, 3, 5}}, {'foe', 'immobilize', h, f = 2},
     {'party', 'faith', h, f = 3}}}},
  -- 47 Geomancer (Hunter + Mystic)
  {line = 'Earthsense',
   {'counter', {'ailing'}, 'A counter on a foe with an ailment', 's', {{'hp', {5, 5, 5, 8}}, {'foe', 'silence', h, f = 2},
     {'mp', 3, f = 3}}},
   {'hit', {'ailing'}, 'Hits on a foe with an ailment', 'p', {{'renew'}, {'self', 'faith', h, f = 2}, {'heal', 10, f = 3},
     {'dmg', 10, f = 4}}},
   {'counter', nil, 'Each counter', 's', {{'self', 'regen', {h, h, h, t}}, {'ally', 'faith', h, f = 2},
     {'party', 'protect', h, f = 3}}}},
  -- 48 Spellfist (Mystic + Black Mage)
  {line = 'Omniscience',
   {'counter', nil, 'Each counter', 's', {{'hp', {5, 5, 5, 8}}, {'quick', 15, f = 2}, {'mp', 3, f = 3}}},
   {'magick', {'newElement'}, 'A spell of a different element', 's', {{'dmg', {10, 10, 15, 20}}, {'mp', 2, f = 2},
     {'self', 'haste', h, f = 3}, {'foe', 'disable', h, f = 4}}},
   {'magick', {'sameElement'}, 'Repeating an element', 's', {{'quick', {20, 20, 20, 30}},
     {'ally', 'protect', h, f = 2}, {'party', 'faith', h, f = 3}}}},
  -- 49 Divine Knight (Samurai + Mystic)
  {line = 'Mind Blade',
   {'counter', nil, 'Each counter', 's', {{'mp', {3, 3, 3, 5}}, {'self', 'haste', h, f = 2}, {'dmgNext', 15, f = 3}}},
   {'hit', nil, 'Your hits', 'p', {{'drain', {4, 4, 6, 6}}, {'zeroBonus', 15, f = 2}, {'self', 'haste', h, f = 4}}},
   {'hit', {'fullMp'}, 'At full MP, your hits', 'p', {{'strip', {'shell'}}, {'self', 'faith', {h, h, h, t}, f = 2},
     {'party', 'shell', h, f = 3}}}},
  -- 50 Shaman (Chemist + Mystic)
  {line = 'Spirit Medicine',
   {'counter', nil, 'Each counter', 's', {{'otherCopy', {h, h, h, t}}, {'self', 'protect', h, f = 2},
     {'hp', 5, f = 3}}},
   {'heal', {'item'}, 'Using a healing item on an ally', 's', {{'ally', 'regen', {h, h, h, t}}, {'hp', 5, f = 2},
     {'ally', 'bravery', h, f = 3}}},
   {'heal', {'item'}, 'Using a healing item on an ally', 's', {{'drainNear', {5, 5, 5, 8}}, {'ally', 'faith', h, f = 2},
     {'party', 'regen', h, f = 3}}}},
  -- 51 Tempest (Warrior + Time Mage)
  {line = 'Stormfront',
   {'hit', {'hasted', 'low50'}, 'Hasted hits below half HP', 'p', {{'dmg', {15, 15, 15, 25}}, {'allyHeal', 10, f = 2},
     {'self', 'protect', h, f = 3}}},
   {'hit', {'hasted', 'slowed'}, 'Hasted hits on a Slowed foe', 'p', {{'extend', 'slow', 10}, {'dmg', 10, f = 2},
     {'drain', 5, f = 3}, {'pushOther', 10, f = 4}}},
   {'struck', {'dropCritical'}, 'Dropping to critical HP', 's', {{'self', 'haste', {h, h, h, t}}, {'other', 'slow', h},
     {'self', 'protect', h, f = 2}, {'party', 'shell', h, f = 3}}}},
  -- 52 Ranger (Hunter + Time Mage)
  {line = 'Long Hunt',
   {'hit', {'far', 'slowed'}, 'Hits from range on a Slowed foe', 'p', {{'mp', 2}, {'dmg', {10, 10, 15, 20}, f = 2},
     {'push', 10, f = 3}, {'self', 'bravery', h, f = 4}}},
   {'hit', {'held'}, 'Hits on a Slowed or Immobilized foe', 'p', {{'foe', 'poison', {h, h, h, t}}, {'quick', 10, f = 2},
     {'drain', 5, f = 3}}},
   {'hit', {'immobilized'}, 'Hits on an Immobilized foe', 'p', {{'dmg', {20, 20, 20, 30}}, {'self', 'faith', h, f = 2},
     {'party', 'shell', h, f = 3}}}},
  -- 53 Arithmetician (Time Mage + Black Mage)
  {line = 'Entropy',
   {'magick', {'slowed', 'sameElement'}, 'The same element twice on a Slowed foe', 's', {{'stopChance', {20, 20, 30, 30}},
     {'quick', 15, f = 2}, {'mp', 3, f = 4}}},
   {'magick', {'slowed'}, 'Spells on a Slowed foe', 'p', {{'extend', 'slow', 10}, {'dmg', {10, 10, 15, 20}, f = 2},
     {'foe', 'immobilize', h, f = 3}}},
   {'magick', {'stopped'}, 'Spells on a Stopped foe', 'p', {{'dmg', {20, 20, 20, 30}}, {'self', 'faith', h, f = 2},
     {'party', 'haste', h, f = 3}}}},
  -- 54 Swiftblade (Samurai + Time Mage)
  {line = 'Ma',
   {'hit', {'slowed'}, 'Hits on a Slowed foe', 'p', {{'self', 'haste', {h, h, h, t}}, {'push', 10, f = 2},
     {'dmg', 15, f = 3}}},
   {'hit', {'hasted', 'chain2'}, 'While Hasted, 2 hits in a row on one foe', 'p', {{'push', {10, 10, 10, 15}},
     {'self', 'haste', h, f = 2}, {'ally', 'protect', h, f = 3}}},
   {'hit', {'slowed', 'kill'}, 'Killing a Slowed foe', 's', {{'other', 'slow', {h, h, h, t}}, {'party', 'haste', h, f = 2},
     {'allyHeal', 10, f = 3}}}},
  -- 55 Tinker (Chemist + Time Mage)
  {line = 'Clockwork',
   {'castAlly', {'hasteCast'}, 'Casting Haste on an ally', 's', {{'otherAlly', 'haste', {h, h, h, t}},
     {'ally', 'regen', h, f = 2}, {'mp', 3, f = 3}}},
   {'heal', {'item'}, 'Using a healing item on an ally', 's', {{'ally', 'haste', {h, h, h, t}}, {'ally', 'bravery', h, f = 2},
     {'hp', 5, f = 3}}},
   {'heal', {'afterHeal'}, 'A second heal in a row', 's', {{'quick', {25, 25, 25, 35}}, {'pushOther', 10, f = 2},
     {'party', 'faith', h, f = 3}}}},
  -- 56 Viking (Warrior + Hunter)
  {line = 'Raid',
   {'hit', {'ailing'}, 'Hits on a foe with an ailment', 'p', {{'dmg', {10, 10, 15, 20}}, {'self', 'bravery', h, f = 2},
     {'steal', f = 3}}},
   {'hit', {'wounded'}, 'Hits on a foe below half HP', 'p', {{'dmg', {15, 15, 15, 25}}, {'foe', 'silence', h, f = 2},
     {'allyHeal', 10, f = 3}}},
   {'hit', {'wounded', 'kill'}, 'Killing a wounded foe', 's', {{'quick', {20, 20, 20, 30}}, {'self', 'haste', h, f = 2},
     {'heal', 10, f = 3}}}},
  -- 57 Battlemage (Warrior + Black Mage)
  {line = 'Alternation',
   {'hit', {'afterMagick'}, 'Your first hit after a spell', 's', {{'dmg', {15, 15, 15, 25}}, {'self', 'bravery', h, f = 2},
     {'drain', 5, f = 3}}},
   {'magick', {'afterHit'}, 'A spell right after a weapon hit', 's', {{'dmg', {15, 15, 15, 25}}, {'self', 'haste', h, f = 2},
     {'allyHeal', 10, f = 3}}},
   {'magick', {'low50'}, 'Below half HP, your spells', 'p', {{'quick', {15, 15, 15, 25}}, {'heal', 10, f = 2},
     {'party', 'faith', h, f = 3}}}},
  -- 58 Berserker (Warrior + Samurai)
  {line = 'Frenzy',
   {'hit', {'low50'}, 'Below half HP, your hits', 'p', {{'evade'}, {'drain', 5, f = 2}, {'self', 'bravery', h, f = 3},
     {'dmg', 10, f = 4}}},
   {'hit', {'chain2'}, '2 hits in a row on one foe', 'p', {{'dmg', {10, 10, 15, 20}}, {'push', 10, f = 2},
     {'self', 'haste', h, f = 3}}},
   {'hit', {'critical'}, 'At critical HP, your hits', 'p', {{'dmg', {30, 30, 30, 40}}, {'ally', 'bravery', h, f = 2},
     {'foe', 'disable', h, f = 3}}}},
  -- 59 Gladiator (Warrior + Chemist)
  {line = 'Arena',
   {'heal', {'selfTarget', 'low50'}, 'Healing yourself below half HP', 's', {{'self', 'protect', {h, h, h, t}},
     {'dmgNext', 15, f = 2}, {'self', 'bravery', h, f = 3}}},
   {'hit', {'low50'}, 'Below half HP, your hits', 'p', {{'cure', {1, 1, 1, 2}}, {'foe', 'disable', h, f = 2}, {'allyHeal', 10, f = 3}}},
   {'hit', {'kill'}, 'A kill', 's', {{'self', 'regen', {h, h, h, t}}, {'self', 'shell', h, f = 2},
     {'party', 'protect', h, f = 3}}}},
  -- 60 Spellbow (Hunter + Black Mage)
  {line = 'Seeker',
   {'magick', {'ailing'}, 'Spells on a foe with an ailment', 'p', {{'mp', 2}, {'foe', 'silence', h, f = 2},
     {'self', 'bravery', h, f = 3}, {'dmg', 10, f = 4}}},
   {'hit', {'afterMagick'}, 'Your first hit after a spell', 's', {{'dmg', {20, 20, 20, 30}}, {'evade', f = 2},
     {'drain', 5, f = 3}}},
   {'hit', {'chain3'}, 'Every 3rd hit in a row on one foe', 's', {{'dmg', {25, 25, 25, 35}}, {'self', 'faith', h, f = 2},
     {'party', 'shell', h, f = 3}}}},
  -- 61 Ninja (Hunter + Samurai)
  {line = 'Kage',
   {'hit', {'chain2', 'ailing'}, '2 hits in a row on a foe with an ailment', 'p', {{'self', 'haste', {h, h, h, t}},
     {'poisonHasted', f = 2}, {'dmgPoisoned', 15, f = 3}}},
   {'hit', {'kill'}, 'A kill', 's', {{'self', 'invisible', {h, h, h, t}}, {'mp', 5}, {'self', 'regen', h, f = 2},
     {'other', 'slow', h, f = 3}}},
   {'hit', {'invisible'}, 'Your hit from Invisible', 's', {{'dmg', {25, 25, 25, 40}}, {'push', 20, f = 2},
     {'drain', 10, f = 3}}}},
  -- 62 Trapper (Hunter + Chemist)
  {line = 'Snaretrap',
   {'status', nil, 'An ailment you land', 's', {{'spread', {50, 50, 50, 65}},
     {'self', 'bravery', h, f = 2}, {'heal', 10, f = 3}}},
   {'hit', {'ailing3'}, 'Hits on a foe with 3+ ailments', 'p', {{'dmg', {20, 20, 20, 30}}, {'drain', 5, f = 2},
     {'foe', 'disable', h, f = 3}}},
   {'hit', {'ailing2'}, 'Hits on a foe with 2+ ailments', 'p', {{'renew'}, {'push', 10, f = 2}, {'foe', 'sap', h, f = 3},
     {'party', 'faith', h, f = 4}}}},
  -- 63 Spellblade (Samurai + Black Mage)
  {line = 'Imbue',
   {'hit', {'afterMagick'}, 'Your first hit after a spell', 's', {{'dmg', {15, 15, 15, 25}}, {'mp', 3},
     {'foe', 'silence', h, f = 2}, {'heal', 10, f = 3}}},
   {'hit', {'imbued'}, 'Your 3 hits after a spell', 'p', {{'quick', {10, 10, 10, 15}}, {'dmg', 10, f = 2},
     {'ally', 'faith', h, f = 3}}},
   {'magick', {'sameElement'}, 'The same element twice in a row', 's', {{'splash', {25, 25, 25, 35}}, {'self', 'faith', h, f = 2},
     {'party', 'protect', h, f = 3}}}},
  -- 64 Grenadier (Chemist + Black Mage)
  {line = 'Ignite',
   {'magick', nil, 'Your damage spells', 'p', {{'splash', {20, 20, 20, 30}}, {'dmgAiling', 15, f = 2}, {'self', 'bravery', h, f = 3}}},
   {'magick', {'ailing'}, 'Spells on a foe with an ailment', 'p', {{'consume', {30, 30, 30, 45}}, {'mp', 3, f = 2},
     {'foe', 'immobilize', h, f = 3}}},
   {'magick', {'kill'}, 'A spell that kills', 's', {{'otherCopy', {h, h, h, t}},
     {'self', 'faith', h, f = 2}, {'party', 'haste', h, f = 3}}}},
  -- 65 Ronin (Samurai + Chemist)
  {line = 'Wanderer',
   {'hit', {'chain2'}, '2 hits in a row on one foe', 'p', {{'allyHeal', {10, 10, 10, 15}}, {'self', 'haste', h, f = 2},
     {'mp', 3, f = 3}}},
   {'hit', {'ailing'}, 'Hits on a foe with an ailment', 'p', {{'dmg', {10, 10, 15, 20}}, {'foe', 'disable', h, f = 2},
     {'allyMp', 5, f = 3}}},
   {'hit', {'kill'}, 'A kill', 's', {{'mp', {5, 5, 5, 8}}, {'pushOther', 10, f = 2}, {'party', 'bravery', h, f = 3}}}},
}


-- ---------- pairs ----------
-- Index of the unordered job pair (a ~= b, both 0-11) in patches/hybrid_names.json order: (0,1), (0,2) ... (10,11).
function H.pairIndex(a, b)
  if type(a) ~= 'number' or type(b) ~= 'number' or a == b or a < 0 or b < 0 or a > 11 or b > 11
    or a % 1 ~= 0 or b % 1 ~= 0 then return nil end
  if a > b then a, b = b, a end
  return a * 11 - a * (a - 1) // 2 + (b - a - 1)
end
H.FIRST_ID = 12        -- hybrid Proficiency ids follow the twelve base jobs: 12 + pair index
H.COUNT = 66
function H.id(a, b)
  local i = H.pairIndex(a, b)
  return i and H.FIRST_ID + i or nil
end
H.PARENTS = {}
for a = 0, 10 do for b = a + 1, 11 do H.PARENTS[H.id(a, b)] = {a, b} end end

-- ---------- values ----------
local function at(v, L) if type(v) == 'table' then return v[L] end return v end
local function active(fx, L) return L >= (fx.f or 1) end

-- ---------- text ----------
local NAMES = {protect = 'Protect', shell = 'Shell', haste = 'Haste', bravery = 'Bravery', faith = 'Faith',
  regen = 'Regen', float = 'Float', bubble = 'Bubble', invisible = 'Invisible', berserk = 'Berserk', lure = 'Lure',
  libra = 'Libra', reflect = 'Reflect', stop = 'Stop', blind = 'Blind', poison = 'Poison', silence = 'Silence',
  sap = 'Sap', oil = 'Oil', disable = 'Disable', immobilize = 'Immobilize', slow = 'Slow', disease = 'Disease'}
local function scaleText(v)
  if v == 0.5 then return ' (half)' elseif v == 0.75 then return ' (3/4)' elseif v == 1.5 then return ' (+50%)' end
  return ''
end
local function list(items)
  if #items == 1 then return items[1] end
  return table.concat(items, ', ', 1, #items - 1) .. ' and ' .. items[#items]
end
local VERB = {deal = 'deals', give = 'gives', inflict = 'inflicts', strip = 'strips', push = 'pushes', heal = 'heals',
  drain = 'drains', restore = 'restores', ['return'] = 'returns', free = 'frees', cure = 'cures', speed = 'speeds',
  restart = 'restarts', add = 'adds', extend = 'extends', steal = 'steals', spread = 'spreads', have = 'has', make = 'makes',
  consume = 'consumes', hit = 'hits', hurt = 'hurts'}
local function conj(phrase, number)
  if number ~= 's' then return phrase end
  local first, rest = phrase:match('^(%S+)(.*)$')
  return (VERB[first] or first) .. rest
end
local NUM = {'one', 'two', 'three'}
-- Events without a formula of their own: the foe is the attacker, and statuses go out through Forge helpers.
local REACTION = {guard = true, counter = true, struck = true, ally = true}
local GROUPED = {self = true, foe = true, ally = true, party = true, strip = true, allyRemove = true, other = true}

local function groupPhrase(op, ev, items)
  local same, names = true, {}
  for i, it in ipairs(items) do if it.scale ~= items[1].scale then same = false end end
  for _, it in ipairs(items) do names[#names + 1] = NAMES[it.id] .. (same and '' or scaleText(it.scale)) end
  local tail = same and scaleText(items[1].scale) or ''
  local own = ev == 'heal' or ev == 'ally' or ev == 'castAlly'
  if op == 'self' then return 'give you ' .. list(names) .. tail
  elseif op == 'foe' then return 'inflict ' .. list(names) .. tail .. (REACTION[ev] and ' on the foe' or '')
  elseif op == 'other' then return 'inflict ' .. list(names) .. tail .. ' on another foe'
  elseif op == 'ally' then return 'give ' .. (own and 'them' or 'the most hurt ally') .. ' ' .. list(names) .. tail
  elseif op == 'party' then return 'give the party ' .. list(names) .. tail
  elseif op == 'strip' then return 'strip ' .. (REACTION[ev] and "the foe's " or 'its ') .. list(names)
  elseif op == 'allyRemove' then return 'free ' .. (own and 'them' or 'the most hurt ally') .. ' of ' .. list(names) end
end

local function phrase(fx, ev, L)
  local op, v = fx[1], at(fx[2], L)
  local attacker = REACTION[ev]
  if op == 'dmg' then return 'deal +' .. v .. '% damage'
  elseif op == 'push' then return attacker and ('push the foe back ' .. v .. '%') or ("push its charge back " .. v .. '%')
  elseif op == 'pushOther' then return 'push another foe back ' .. v .. '%'
  elseif op == 'heal' then return 'heal you for ' .. v .. '% of the damage'
  elseif op == 'allyHeal' then return 'heal the most hurt ally for ' .. v .. '% of the damage'
  elseif op == 'drain' then return 'drain ' .. v .. '% of the damage as MP'
  elseif op == 'mp' then return 'restore ' .. v .. '% of max MP'
  elseif op == 'hp' then return 'heal you for ' .. v .. '% of max HP'
  elseif op == 'overflowMp' then return 'return ' .. v .. '% of the overflow as MP'
  elseif op == 'allyMp' then
    return ev == 'heal' and ('add ' .. v .. '% of the heal to their MP')
      or ('give the lowest-MP ally ' .. v .. '% of the damage as MP')
  elseif op == 'cure' then return 'cure ' .. NUM[v] .. ' of your ailments'
  elseif op == 'cureAlly' then
    return ev == 'heal' and ('cure ' .. NUM[v] .. ' of their ailments') or ('cure ' .. NUM[v] .. ' ailment' .. (v > 1 and 's' or '') .. ' on an ally')
  elseif op == 'quick' then return 'make your next action ' .. v .. '% faster'
  elseif op == 'renew' then return 'restart its ailment timers'
  elseif op == 'renewFoe' then return "restart the foe's ailment timers"
  elseif op == 'extend' then return 'extend its ' .. NAMES[fx[2]]
  elseif op == 'steal' then return 'steal one of its buffs'
  elseif op == 'otherCopy' then return 'spread one of its ailments to another foe' .. scaleText(v)
  elseif op == 'stopChance' then return 'have a ' .. v .. '% chance to Stop ' .. 'it'
  elseif op == 'dmgNext' then return 'make your next hit deal +' .. v .. '%'
  elseif op == 'zeroBonus' then return 'deal +' .. v .. '% to a foe with no MP'
  elseif op == 'poisonHasted' then return 'Poison the foe while you are Hasted'
  elseif op == 'dmgPoisoned' then return 'deal +' .. v .. '% to a Poisoned foe'
  elseif op == 'dmgAiling' then return 'deal +' .. v .. '% to a foe with an ailment'
  elseif op == 'consume' then return 'consume an ailment for +' .. v .. '% damage'
  elseif op == 'splash' then return 'hit another foe for ' .. v .. '% of the damage'
  elseif op == 'spread' then return 'have a ' .. v .. '% chance to spread to another foe'
  elseif op == 'evade' then return 'cannot be parried, blocked or dodged'
  elseif op == 'nearHeal' then return 'heal another ally for ' .. v .. '% of it'
  elseif op == 'drainNear' then return 'hurt a foe for ' .. v .. '% of the heal'
  elseif op == 'drainFoe' then return 'drain ' .. v .. '% of max MP from the foe'
  elseif op == 'otherAlly' then return 'give another ally ' .. NAMES[fx[2]] .. scaleText(at(fx[3], L) or 1) end
  error('no phrase for ' .. tostring(op))
end

-- The help line holds about 80 characters: the first shares it with the stage header ("Kage III 4/4: ").
H.ROW = 80
H.EXTRA_LIMIT = 78
function H.firstLimit(line) return H.ROW - (#line + #' III 4/4: ') end
-- Break one sentence into at most two lines, at a ', ' or before ' and ': the first fits beside the header.
function H.wrap(text, first, keep)
  first, keep = first or 60, keep or 0
  if #text <= first then return text end
  local best
  for pos = 1, #text do
    local cut
    if text:sub(pos, pos + 1) == ', ' then cut = {pos, pos + 2}
    elseif text:sub(pos, pos + 4) == ' and ' then cut = {pos - 1, pos + 1} end
    if cut and cut[1] > keep then
      local a, b = text:sub(1, cut[1]), text:sub(cut[2])
      if #a <= first and #b <= H.EXTRA_LIMIT then best = a .. '\n' .. b end
    end
  end
  if best then return best end
  -- No clause boundary fits: break at the last space that does.
  for pos = #text, 1, -1 do
    if pos > keep and text:sub(pos, pos) == ' ' and pos - 1 <= first and #text - pos <= H.EXTRA_LIMIT then
      return text:sub(1, pos - 1) .. '\n' .. text:sub(pos + 1)
    end
  end
  return nil
end

-- MP a heal hands back comes from healing spells only, unless the stage is about items (no gil-for-MP exchange).
local HEAL_MP = {mp = true, overflowMp = true, allyMp = true}
local function itemStage(stage)
  for _, c in ipairs(stage[2] or {}) do if c == 'item' then return true end end
  return false
end
H.HEAL_MP, H.itemStage = HEAL_MP, itemStage

function H.stageText(stage, L, first)
  local ev, trigger, number, effects = stage[1], stage[3], stage[4], stage[5]
  local spellsOnly = ev == 'heal' and not itemStage(stage)
  local parts, groups = {}, {}
  for _, fx in ipairs(effects) do
    if active(fx, L) then
      if GROUPED[fx[1]] then
        local key = fx[1]
        if not groups[key] then
          groups[key] = {}
          parts[#parts + 1] = {group = key}
        end
        if fx[1] == 'strip' or fx[1] == 'allyRemove' then
          for _, id in ipairs(fx[2]) do groups[key][#groups[key] + 1] = {id = id, scale = 1} end
        else
          groups[key][#groups[key] + 1] = {id = fx[2], scale = at(fx[3], L) or 1}
        end
      else
        parts[#parts + 1] = {fx = fx}
      end
    end
  end
  local phrases = {}
  for _, p in ipairs(parts) do
    local text = p.group and groupPhrase(p.group, ev, groups[p.group]) or phrase(p.fx, ev, L)
    if spellsOnly and p.fx and HEAL_MP[p.fx[1]] then
      text = p.fx[1] == 'allyMp' and text:gsub('of the heal', 'of spell heals', 1) or text .. ' (spells)'
    end
    phrases[#phrases + 1] = conj(text, number)
  end
  -- Name a recipient once; later mentions become pronouns ("heal the most hurt ally ... and give them Shell").
  local text = trigger .. ' ' .. list(phrases)
  local function once(name, object, possessive)
    local at1 = text:find(name, 1, true)
    if not at1 then return end
    local head, tail = text:sub(1, at1 + #name - 1), text:sub(at1 + #name)
    if possessive then tail = tail:gsub(name:gsub('%p', '%%%0') .. "'s", possessive) end
    tail = tail:gsub(name:gsub('%p', '%%%0'), object)
    text = head .. tail
  end
  once('the most hurt ally', 'them', 'their')
  once('the foe', 'it', 'its')
  return H.wrap(text, first, #trigger)
end

-- ---------- rules ----------
-- Per-character memory lives in the base module's slot (cleared with it on load and map change).
local function cond(name, s, m, ev)
  local ts, cs = s.targetStatus or {}, s.casterStatus or {}
  if name == 'kill' then return s.kill == true
  elseif name == 'buffed' or name == 'buffless' then
    local any = false
    for _, b in ipairs(H.M.BUFFS) do if ts[b] then any = true end end
    return (name == 'buffed') == any
  elseif name == 'ailing' or name == 'ailing2' or name == 'ailing3' then
    local n = 0
    for _, a in ipairs(H.M.HINDERED) do if ts[a] then n = n + 1 end end
    return n >= (name == 'ailing' and 1 or (name == 'ailing2' and 2 or 3))
  elseif name == 'slowed' then return ts.slow == true
  elseif name == 'stopped' then return ts.stop == true
  elseif name == 'immobilized' then return ts.immobilize == true
  elseif name == 'held' then return ts.slow == true or ts.immobilize == true
  elseif name == 'oiled' then return ts.oil == true
  elseif name == 'diseased' then return ts.disease == true
  elseif name == 'blinded' then return ts.blind == true
  elseif name == 'disabled' then return ts.disable == true
  elseif name == 'foeFaith' then return ts.faith == true
  elseif name == 'wounded' then return type(s.targetHpPct) == 'number' and s.targetHpPct < 50
  elseif name == 'healthy' then return type(s.targetHpPct) == 'number' and s.targetHpPct >= 50
  elseif name == 'about' then
    local c = s.charge
    return c ~= nil and type(c.current) == 'number' and type(c.required) == 'number' and c.required > 0
      and c.current < c.required and c.current * 4 >= c.required * 3
  elseif name == 'hasted' then return cs.haste == true
  elseif name == 'lured' then return cs.lure == true
  elseif name == 'floating' then return cs.float == true
  elseif name == 'invisible' then return cs.invisible == true
  elseif name == 'low50' then return (s.casterHpPct or 100) < 50
  elseif name == 'critical' then return cs.hpCritical == true or (s.casterHpPct or 100) <= 25
  elseif name == 'dropCritical' then return s.dropCritical == true
  elseif name == 'fullMp' then return (s.casterMaxMp or 0) > 0 and (s.casterMp or 0) >= s.casterMaxMp
  elseif name == 'overflow' then return (s.overflow or 0) > 0
  elseif name == 'item' then return s.item == true
  elseif name == 'selfTarget' then return s.target ~= nil and s.target == s.self
  elseif name == 'allyAiling' then
    for _, a in ipairs(H.M.HINDERED) do if ts[a] then return true end end
    return false
  elseif name == 'allyWarded' then return ts.bubble == true or ts.regen == true
  elseif name == 'allyCritical' then return type(s.targetHpPct) == 'number' and s.targetHpPct <= 25
  elseif name == 'allyHasted' then return ts.haste == true
  elseif name == 'parry' then return s.outcome == 'parry'
  elseif name == 'parryBlock' then return s.outcome == 'parry' or s.outcome == 'block'
  elseif name == 'afterHeal' then return m.last == 'heal'
  elseif name == 'afterMagick' then return m.last == 'magick' or m.last == 'cast'
  elseif name == 'afterHit' then return m.last == 'hit'
  elseif name == 'afterGuard' then return m.last == 'guard'
  elseif name == 'firstHit' then return s.targetId ~= nil and not (m.first and m.first[s.targetId])
  elseif name == 'chain2' then return (m.count or 0) >= 2 and m.count % 2 == 0
  elseif name == 'chain3' then return (m.count or 0) >= 3 and m.count % 3 == 0
  elseif name == 'imbued' then return (m.imbue or 0) > 0
  elseif name == 'sameElement' then return s.sameElement == true
  elseif name == 'newElement' then return s.newElement == true
  elseif name == 'slowCast' or name == 'hasteCast' or name == 'slowOrHaste' then
    local slow, haste = false, false
    for _, id in ipairs(s.landed or {}) do
      if id == H.M.STATUS.slow then slow = true elseif id == H.M.STATUS.haste then haste = true end
    end
    if name == 'slowCast' then return slow elseif name == 'hasteCast' then return haste end
    return slow or haste
  -- Distance between you and the target, as Ultima's Seitengrat reads it: 15 world units or more counts as far.
  elseif name == 'far' then return type(s.distance) == 'number' and s.distance >= H.FAR
  elseif name == 'near' then return type(s.distance) == 'number' and s.distance < H.FAR end
  error('no condition ' .. tostring(name))
end
H.cond = cond
H.FAR = 15.0

local function hurtAlly(s, exclude)
  local best
  for _, p in ipairs(s.party or {}) do
    if p.address ~= s.self and p.address ~= exclude and p.hpPct < 100 and (not best or p.hpPct < best.hpPct) then best = p end
  end
  return best
end
local function ailingAlly(s)
  for _, p in ipairs(s.party or {}) do
    for _, n in ipairs(H.M.AILMENTS) do if p.status[n] then return p end end
  end
end
local function lowMpAlly(s)
  local best
  for _, p in ipairs(s.party or {}) do
    if p.address ~= s.self and p.maxMp > 0 and (not best or p.mp / p.maxMp < best.mp / best.maxMp) then best = p end
  end
  return best
end

-- Apply one effect. `formula` = the event has its own formula (hit, magick, status: the target is a foe; heal: the
-- target is the healed ally). Otherwise statuses go out through helpers ('others').
local function apply(fx, ev, s, m, a, L, add, push, other)
  local S = H.M.STATUS
  local op, v = fx[1], at(fx[2], L)
  local reaction = REACTION[ev]
  local onAlly = ev == 'heal' or ev == 'castAlly' or (ev == 'cast' and s.allyTarget)
  local foe = reaction and s.attacker or (not onAlly and s.target or nil)
  local foeStatus = reaction and (s.attackerStatus or {}) or (s.targetStatus or {})
  local function allyUnit()
    if onAlly then return s.target, true end
    if ev == 'ally' then return s.ally, false end
    local p = hurtAlly(s)
    return p and p.address, false
  end
  if op == 'dmg' then a.damage = (a.damage or 1) * (1 + v / 100)
  elseif op == 'self' then
    local scale = at(fx[3], L) or 1
    if reaction then other(a, {op = 'add', unit = s.self, id = S[fx[2]], scale = scale})
    else add(a, 'casterAdd', S[fx[2]], scale) end
  elseif op == 'foe' then
    local scale = at(fx[3], L) or 1
    if foe and not foeStatus[fx[2]] then
      if reaction then other(a, {op = 'add', unit = foe, id = S[fx[2]], scale = scale})
      else add(a, 'targetAdd', S[fx[2]], scale) end
    end
  elseif op == 'other' then
    local f = s.foes and s.foes[1]
    if f and not f.status[fx[2]] then other(a, {op = 'add', unit = f.address, id = S[fx[2]], scale = at(fx[3], L) or 1}) end
  elseif op == 'otherCopy' then
    local f = s.foes and s.foes[1]
    if f then
      for _, n in ipairs(H.M.HINDERED) do
        if foeStatus[n] and not f.status[n] and n ~= 'stop' then
          other(a, {op = 'add', unit = f.address, id = S[n], scale = v}); break
        end
      end
    end
  elseif op == 'strip' then
    for _, n in ipairs(fx[2]) do
      if foeStatus[n] then
        if reaction then other(a, {op = 'remove', unit = foe, id = S[n]}) else push(a, 'targetRemove', S[n]) end
      end
    end
  elseif op == 'steal' then
    if not reaction then
      for _, n in ipairs(H.M.BUFFS) do
        if foeStatus[n] and n ~= 'reflect' and n ~= 'berserk' then
          push(a, 'targetRemove', S[n]); add(a, 'casterAdd', S[n], 0.5); break
        end
      end
    end
  elseif op == 'push' then
    if reaction then
      if foe then other(a, {op = 'push', unit = foe, fraction = v / 100}) end
    elseif ev == 'hit' and s.charge then
      local c = s.charge
      if type(c.current) == 'number' and type(c.required) == 'number' and c.current > 0 and c.current < c.required
          and c.last == c.current then
        a.chargeTo = math.max(1, math.min(a.chargeTo or c.current, c.current) - math.floor(c.required * v / 100))
      end
    elseif foe then other(a, {op = 'push', unit = foe, fraction = v / 100}) end
  elseif op == 'pushOther' then
    local f = s.foes and s.foes[1]
    if f then other(a, {op = 'push', unit = f.address, fraction = v / 100}) end
  elseif op == 'heal' then a.healPct = (a.healPct or 0) + v
  elseif op == 'allyHeal' then
    local p = hurtAlly(s)
    if p then other(a, {op = 'hp', unit = p.address, damagePct = v}) end
  elseif op == 'splash' then
    local f = s.foes and s.foes[1]
    if f then other(a, {op = 'hp', unit = f.address, damagePct = -v}) end
  elseif op == 'drain' then a.drainPct = (a.drainPct or 0) + v
  elseif op == 'mp' then
    if (s.casterMaxMp or 0) > 0 then other(a, {op = 'mp', unit = s.self, amount = math.max(1, s.casterMaxMp * v // 100)}) end
  elseif op == 'hp' then
    if (s.selfMaxHp or 0) > 0 then other(a, {op = 'hp', unit = s.self, amount = math.max(1, s.selfMaxHp * v // 100)}) end
  elseif op == 'overflowMp' then
    if (s.overflow or 0) > 0 then other(a, {op = 'mp', unit = s.self, amount = math.max(1, s.overflow * v // 100)}) end
  elseif op == 'allyMp' then
    if ev == 'heal' then
      if s.target and (s.healAmount or 0) > 0 then other(a, {op = 'mp', unit = s.target, amount = math.max(1, s.healAmount * v // 100)}) end
    else
      local p = lowMpAlly(s)
      if p then other(a, {op = 'mp', unit = p.address, mpPct = v}) end
    end
  elseif op == 'cure' then
    local n = 0
    for _, name in ipairs(H.M.AILMENTS) do
      if (s.casterStatus or {})[name] and n < v then other(a, {op = 'remove', unit = s.self, id = S[name]}); n = n + 1 end
    end
  elseif op == 'cureAlly' then
    local n = 0
    if ev == 'heal' then
      for _, name in ipairs(H.M.AILMENTS) do
        if (s.targetStatus or {})[name] and n < v then push(a, 'targetRemove', S[name]); n = n + 1 end
      end
    else
      local p = ailingAlly(s)
      if p then
        for _, name in ipairs(H.M.AILMENTS) do
          if p.status[name] and n < v then other(a, {op = 'remove', unit = p.address, id = S[name]}); n = n + 1 end
        end
      end
    end
  elseif op == 'ally' then
    local unit, healed = allyUnit()
    local scale = at(fx[3], L) or 1
    if unit then
      if healed then add(a, 'targetAdd', S[fx[2]], scale) else other(a, {op = 'add', unit = unit, id = S[fx[2]], scale = scale}) end
    end
  elseif op == 'allyRemove' then
    local unit, healed = allyUnit()
    if unit then
      for _, n in ipairs(fx[2]) do
        if healed then
          if (s.targetStatus or {})[n] then push(a, 'targetRemove', S[n]) end
        else other(a, {op = 'remove', unit = unit, id = S[n]}) end
      end
    end
  elseif op == 'party' then
    for _, p in ipairs(s.party or {}) do other(a, {op = 'add', unit = p.address, id = S[fx[2]], scale = at(fx[3], L) or 1}) end
  elseif op == 'quick' then a.casterCharge = math.max(a.casterCharge or 0, v / 100)
  elseif op == 'renew' then
    for _, n in ipairs(H.M.TIMED) do if foeStatus[n] then push(a, 'targetRenew', S[n]) end end
  elseif op == 'renewFoe' then
    for _, n in ipairs(H.M.TIMED) do
      if foe and foeStatus[n] then other(a, {op = 'add', unit = foe, id = S[n], scale = 1}) end
    end
  elseif op == 'extend' then
    if foeStatus[fx[2]] then a.targetExtend = a.targetExtend or {}; a.targetExtend[S[fx[2]]] = fx[3] / 100 end
  elseif op == 'stopChance' then
    if foe and not foeStatus.stop and s.roll(v) then
      if reaction then other(a, {op = 'add', unit = foe, id = S.stop, scale = 0.5}) else add(a, 'targetAdd', S.stop, 0.5) end
    end
  elseif op == 'dmgNext' then m.boost = math.max(m.boost or 0, v)
  elseif op == 'zeroBonus' then
    if s.targetMp == 0 then a.damage = (a.damage or 1) * (1 + v / 100) end
  elseif op == 'poisonHasted' then
    if (s.casterStatus or {}).haste and not foeStatus.poison then add(a, 'targetAdd', S.poison, 0.5) end
  elseif op == 'dmgPoisoned' then
    if foeStatus.poison then a.damage = (a.damage or 1) * (1 + v / 100) end
  elseif op == 'dmgAiling' then
    for _, n in ipairs(H.M.HINDERED) do
      if foeStatus[n] then a.damage = (a.damage or 1) * (1 + v / 100); break end
    end
  elseif op == 'consume' then
    for _, n in ipairs(H.M.AILMENTS) do
      if foeStatus[n] then push(a, 'targetRemove', S[n]); a.damage = (a.damage or 1) * (1 + v / 100); break end
    end
  elseif op == 'spread' then
    local f = s.foes and s.foes[1]
    if f then
      for _, id in ipairs(s.landed or {}) do
        if s.canLand(f.address, id) and s.roll(v) then other(a, {op = 'add', unit = f.address, id = id, scale = 1}) end
      end
    end
  elseif op == 'evade' then -- the aim event applies it
  elseif op == 'nearHeal' then
    local p = hurtAlly(s, s.target)
    if p and (s.healAmount or 0) > 0 then other(a, {op = 'hp', unit = p.address, amount = math.max(1, s.healAmount * v // 100)}) end
  elseif op == 'drainNear' then
    local f = s.foes and s.foes[1]
    if f and (s.healAmount or 0) > 0 then other(a, {op = 'hp', unit = f.address, amount = -math.max(1, s.healAmount * v // 100)}) end
  elseif op == 'otherAlly' then
    for _, p in ipairs(s.party or {}) do
      if p.address ~= s.self and p.address ~= s.target and not p.status[fx[2]] then
        other(a, {op = 'add', unit = p.address, id = S[fx[2]], scale = at(fx[3], L) or 1}); break
      end
    end
  elseif op == 'drainFoe' then
    if foe and (s.casterMaxMp or 0) > 0 then
      local amount = math.min(math.max(1, s.casterMaxMp * v // 100), s.attackerMp or 0)
      if amount > 0 then
        other(a, {op = 'mp', unit = foe, amount = -amount}); other(a, {op = 'mp', unit = s.self, amount = amount})
      end
    end
  else error('no effect ' .. tostring(op)) end
end

-- A spell reaches each of its targets separately: these effects belong to the cast, so only its first target gives them.
local PER_CAST = {self = true, mp = true, hp = true, quick = true, party = true, ally = true, allyHeal = true,
  allyMp = true, cure = true, cureAlly = true, pushOther = true, other = true, otherCopy = true, splash = true,
  drainNear = true, dmgNext = true, otherAlly = true}

-- Events a spell can raise once per target: per-cast effects come only from its first target.
local SPELL_EVENT = {magick = true, cast = true, heal = true, status = true}

-- Conditions an aim (evasion setup) can test: no hit outcome is known yet.
local AIM_OK = {hasted = true, low50 = true, critical = true, afterMagick = true, afterHit = true, afterHeal = true,
  lured = true, floating = true, invisible = true, fullMp = true}

local function makeRule(line)
  local rule = {}
  local function run(ev, s, a, helpers)
    local m = helpers.slot(s.actorId)
    for k = 1, 3 do
      local stage, L = line[k], s.L[k]
      local listens = stage[1] == ev or (ev == 'guard' and stage[1] == 'counter' and s.outcome == 'counter')
      if stage[1] == 'guard' and s.outcome == 'counter' then listens = false end
      local evName = (ev == 'guard' and s.outcome == 'counter') and 'counter' or ev
      if ev == 'cast' then
        -- A status spell: 'cast' hears every one, castAlly/castFoe by target, and 'status' hears ailments on foes.
        listens = stage[1] == 'cast' or (s.allyTarget and stage[1] == 'castAlly')
          or (not s.allyTarget and (stage[1] == 'castFoe' or stage[1] == 'status'))
        evName = stage[1] == 'status' and 'status' or stage[1]
      end
      if L >= 1 and listens then
        local ok = true
        for _, c in ipairs(stage[2] or {}) do if not cond(c, s, m, ev) then ok = false; break end end
        local itemHeal = ev == 'heal' and s.item and not itemStage(stage)
        if ok then
          for _, fx in ipairs(stage[5]) do
            if active(fx, L) and not (SPELL_EVENT[ev] and s.repeatTarget and PER_CAST[fx[1]])
                and not (itemHeal and HEAL_MP[fx[1]]) then
              apply(fx, evName, s, m, a, L, helpers.add, helpers.push, helpers.other)
            end
          end
        end
      end
    end
  end
  local CAST = {cast = true, castAlly = true, castFoe = true, status = true}
  for _, ev in ipairs({'hit', 'magick', 'heal', 'guard', 'struck', 'ally', 'status', 'cast'}) do
    local used = false
    for k = 1, 3 do
      if line[k][1] == ev or (ev == 'guard' and line[k][1] == 'counter') or (ev == 'cast' and CAST[line[k][1]]) then
        used = true
      end
    end
    if used or ev == 'hit' or ev == 'magick' or ev == 'heal' then
      rule[ev] = function(s, a, helpers)
        local m = helpers.slot(s.actorId)
        -- Memory every hybrid keeps: chains on one foe, the first hit on each foe, the action before this one.
        if ev == 'hit' and s.foe then
          if m.boost then a.damage = (a.damage or 1) * (1 + m.boost / 100); m.boost = nil end
          if m.target == s.targetId then m.count = (m.count or 0) + 1 else m.target, m.count = s.targetId, 1 end
        elseif ev == 'cast' then
          -- One spell reaching several targets is one cast (the runtime's cast id; the time window is the fallback).
          if s.castId ~= nil then
            s.repeatTarget = m.spellCast == s.castId and m.spellAction == s.action
          else
            s.repeatTarget = m.spellAt ~= nil and s.now ~= nil and s.now - m.spellAt < (H.M.CAST_WINDOW or 0)
              and m.spellAction == s.action
          end
          if not s.repeatTarget then m.spellAt, m.spellCast, m.spellAction = s.now, s.castId, s.action end
        elseif ev == 'status' and s.castId ~= nil then
          -- A damage spell's status rider landing on several foes is one cast (Forge 182 runs per target).
          s.repeatTarget = m.statusCast == s.castId and m.statusAction == s.action
          if not s.repeatTarget then m.statusCast, m.statusAction = s.castId, s.action end
        elseif ev == 'heal' and s.castId ~= nil then
          -- A healing spell on the whole party is one cast: its per-cast effects come once (items have no cast id).
          s.repeatTarget = m.healCast == s.castId and m.healAction == s.action
          if not s.repeatTarget then m.healCast, m.healAction = s.castId, s.action end
        elseif ev == 'magick' and s.element then
          local fresh
          if s.castId ~= nil then
            fresh = not (m.castId == s.castId and m.castElement == s.element)
          else
            fresh = not (m.castAt and s.now and s.now - m.castAt < (H.M.CAST_WINDOW or 0) and m.castElement == s.element)
          end
          if fresh then
            s.sameElement = m.element ~= nil and m.element == s.element and s.element ~= 0
            s.newElement = m.element ~= nil and m.element ~= s.element and s.element ~= 0
            m.prevSame, m.prevNew = s.sameElement, s.newElement
            m.element, m.castAt, m.castId, m.castElement = s.element, s.now, s.castId, s.element
          else
            s.sameElement, s.newElement, s.repeatTarget = m.prevSame, m.prevNew, true
          end
        end
        run(ev, s, a, helpers)
        if ev == 'hit' and s.foe then
          if s.targetId ~= nil then
            local first = {}
            for id in pairs(m.first or {}) do first[id] = true end
            first[s.targetId] = true
            m.first = first
          end
          if (m.imbue or 0) > 0 then m.imbue = m.imbue - 1 end
          m.last = 'hit'
        elseif ev == 'magick' then m.last, m.imbue = 'magick', 3
        elseif ev == 'cast' then m.last, m.imbue = 'cast', 3
        elseif ev == 'heal' then m.last = 'heal'
        elseif ev == 'guard' and (s.outcome == 'parry' or s.outcome == 'block') then m.last = 'guard' end
      end
    end
  end
  rule.aim = function(s, a, helpers)
    local m = helpers.slot(s.actorId)
    for k = 1, 3 do
      local stage, L = line[k], s.L[k]
      if L >= 1 and stage[1] == 'hit' then
        for _, fx in ipairs(stage[5]) do
          if fx[1] == 'evade' and active(fx, L) then
            local ok = true
            for _, c in ipairs(stage[2] or {}) do if not AIM_OK[c] or not cond(c, s, m, 'aim') then ok = false end end
            if ok then a.evade = {weapon = true, shield = true, parry = true} end
          end
        end
      end
    end
  end
  return rule
end

-- Hook every line into the base module: rules, gear text and the parents (legendary weapon mastery).
function H.attach(M)
  H.M = M
  for i, line in ipairs(H.LINES) do
    local id = H.FIRST_ID + i - 1
    M.rules[id] = makeRule(line)
    local gear = {line = line.line}
    for k = 1, 3 do
      gear[k] = {}
      for L = 1, 4 do gear[k][L] = H.stageText(line[k], L, H.firstLimit(line.line)) end
    end
    M.GEAR[id] = gear
    M.PARENTS[id] = H.PARENTS[id]
  end
  return M
end

return H
