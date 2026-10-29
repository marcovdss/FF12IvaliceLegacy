# Ivalice Legacy: Arsenal 0.5.0 (experimental)

Weapon attack and weapon family identities for the English Steam game, version 1.0.4.0.

## What changes

**Test build:** the Claymore costs 800 gil instead of 12,500, so it can be bought early for the Attack area
experiment. This price is for testing only.

Attack follows the story: modest at the start, growing toward a powerful endgame. The climb is gentle: the opening weapons keep their attack and every later weapon is lower than native, so a typical hit grows from about 47 at the start to about 830 by the last shops (native: about 1,375). Rewards are set for where they are found, and the 13 legendary weapons still lead their families. Each family's identity changes how
its weapons play, not how much damage they deal over time: a faster or more critical weapon hits a little softer, a
slower one a little harder.

- **Daggers** (fastest): charge time 21 → 15, combo rate 15 → 20 by tier, evade 5 → 10 by tier (Main Gauche keeps
  its parrying evade and combo). Attack lowered to match.
- **Katana** (longest chains): combo rate 20 → 30–45 by tier, attack lowered to match.
- **Bows** (highest critical rate): critical rate 5 → 20–30 by tier, attack lowered to match. Charge time unchanged.
- **Greatswords** (slow and heavy): charge time 35 → 40, attack raised to match.
- **Axes and hammers** hit about twice as hard as other weapons of their time; their attack is brought into line.
  Axes and hammers share one curve, one new weapon per stage of the story.
- **Hand-bombs**: attack brought into line (Hornito, sold from the start, 66 → 15). Hornito, the Time Mage opening
  hand-bomb, costs 500 gil (was 5,000) like the other opening weapons.
- **Longbow** 48 → 35 (a large jump over the Killer Bow sold just before it).

Families with an effect identity gain it on every weapon (a weapon's own native status stays):

- **Swords** (guard): evade 20 → 40 by tier, the highest of any weapon. Poles (10–15) and measures (10) give up their
  very high evade so swords own it.
- **Hammers** (concussion): knockback 30, the strongest, and a 15% chance to Disable on hit.
- **Greatswords** (overwhelm): knockback 25.
- **Hand-bombs** (barrage): a 20% chance to Slow or Oil on hit, alternating by tier.
- **Ninja swords** (unseen): a 10% chance to Blind on hit.
- **Spears** (pierce): reach 25.

Story and joke weapons keep their native stats. Other prices are unchanged.

**Legendary weapons** (the twelve zodiac weapons and Masamune) are the strongest of their family; each job's
Proficiency unlocks their real potential. At base power each still leads its family: Great Trango and Seitengrat swing at normal sword and bow speed (charge 32 and 36), and Kumbha already swung as a katana (damage formula and model) and now counts as one. Their attack is in the table below.

| Family | Attack (native → Arsenal) |
|---|---|
| Sword | Longsword 20 → 18, Iron Sword 25 → 23, Zwill Blade 30 → 26, Ancient Sword 35 → 30, Blood Sword 38 → 32, Lohengrin 40 → 33, Flametongue 45 → 36, Demonsbane 51 → 39, Icebrand 55 → 41, Platinum Sword 61 → 43, Bastard Sword 67 → 50, Diamond Sword 73 → 53, Runeblade 78 → 57, Deathbringer 83 → 59, Durandal 99 → 65, Great Trango 153 → 74, Stoneblade 95 → 74, Karkata 92 → 76, Simha 103 → 78, Kumbha 117 → 84 |
| Greatsword | Claymore 83 → 61, Defender 90 → 66, Save the Queen 92 → 72, Ultima Blade 109 → 76, Excalibur 127 → 91, Ragnarok 117 → 91, Wyrmhero Blade 131 → 91, Tournesol 138 → 102 |
| Katana | Kotetsu 16 → 14, Osafune 26 → 21, Kogarasumaru 40 → 29, Magoroku 50 → 35, Murasame 62 → 40, Kiku-ichimonji 71 → 46, Yakei 83 → 52, Ame-no-Murakumo 96 → 60, Muramasa 104 → 63, Masamune 111 → 73 |
| Ninja sword | Ashura 72 → 48, Sakura-saezuri 77 → 55, Kagenui 81 → 56, Iga Blade 87 → 59, Koga Blade 87 → 59, Yagyu Darkblade 98 → 65, Orochi 94 → 74, Mesa 102 → 77 |
| Spear | Spear 26 → 23, Partisan 34 → 29, Heavy Lance 42 → 35, Storm Spear 53 → 41, Obelisk 68 → 46, Halberd 75 → 54, Trident 85 → 60, Holy Lance 91 → 67, Gungnir 95 → 69, Dragon Whisker 100 → 76, Zodiac Spear 141 → 78, Vrsabha 108 → 80 |
| Pole | Cypress Pole 22 → 20, Battle Bamboo 34 → 29, Musk Stick 39 → 33, Iron Pole 46 → 37, Six-fluted Pole 59 → 43, Gokuu Pole 72 → 52, Zephyr Pole 77 → 57, Ivory Pole 83 → 59, Eight-fluted Pole 91 → 61, Sweep 88 → 66, Whale Whisker 108 → 80, Kanya 112 → 82 |
| Bow | Shortbow 15 → 14, Silver Bow 19 → 16, Aevis Killer 25 → 20, Killer Bow 36 → 28, Longbow 48 → 30, Elfin Bow 54 → 38, Loxley Bow 60 → 40, Giant Stonebow 73 → 48, Burning Bow 77 → 53, Traitor's Bow 82 → 54, Yoichi Bow 89 → 61, Perseus Bow 92 → 62, Artemis Bow 94 → 63, Sagittarius 97 → 64, Seitengrat 224 → 72, Dhanusha 109 → 78 |
| Crossbow | Crossbow 31 → 27, Paramina Crossbow 42 → 35, Recurve Crossbow 57 → 43, Hunting Crossbow 70 → 47, Penetrator Crossbow 79 → 58, Gastrophetes 84 → 64, Tula 91 → 67 |
| Gun | Capella 8 → 7, Vega 13 → 11, Sirius 17 → 14, Betelgeuse 22 → 17, Ras Algethi 29 → 20, Aldebaran 33 → 24, Spica 38 → 27, Antares 41 → 29, Arcturus 45 → 33, Fomalhaut 52 → 38, Mithuna 66 → 48 |
| Axe | Handaxe 17 → 15, Broadaxe 36 → 22, Slasher 54 → 35, Hammerhead 81 → 40, Francisca 88 → 48, Greataxe 110 → 60, Golden Axe 120 → 86 |
| Hammer | Iron Hammer 24 → 15, War Hammer 42 → 30, Sledgehammer 66 → 40, Morning Star 100 → 53, Scorpion Tail 124 → 88, Vrscika 133 → 92 |
| Dagger | Dagger 14 → 11, Mage Masher 18 → 14, Assassin's Dagger 31 → 23, Chopper 40 → 28, Main Gauche 46 → 33, Gladius 56 → 37, Avenger 66 → 44, Zwill Crossblade 87 → 46, Orichalcum Dirk 79 → 50, Platinum Dagger 83 → 52, Shikari Nagasa 90 → 58, Mina 104 → 63 |
| Rod | Rod of Faith 21 → 18, Serpent Rod 25 → 23, Gaia Rod 43 → 35, Power Rod 56 → 41, Empyrean Rod 60 → 47, Holy Rod 63 → 56 |
| Staff | Cherry Staff 20 → 18, Wizard's Staff 26 → 25, Flame Staff 34 → 31, Storm Staff 36 → 32, Glacial Staff 38 → 35, Cloud Staff 49 → 36, Golden Staff 42 → 41, Judicer's Staff 48 → 43 |
| Mace | Bronze Mace 24 → 22, Bhuj 34 → 29, Miter 42 → 35, Thorned Mace 53 → 40, Chaos Mace 69 → 51, Doom Mace 75 → 56, Zeus Mace 81 → 63, Bone of Byblos 100 → 71, Grand Mace 88 → 71 |
| Measure | Gilt Measure 5 → 4, Arc Scale 10 → 7, Multiscale 13 → 9, Cross Scale 16 → 12, Caliper 19 → 14, Euclid's Sextant 24 → 18 |
| Hand-bomb | Hornito 66 → 15, Fumarole 71 → 39, Caldera 83 → 49, Tumulus 76 → 56, Volcano 90 → 72, Makara 96 → 75 |

## Package

The package replaces one section of `battle_pack.bin` (the equipment table, section 13) through the FF12 External File
Loader. It is built from your own game files and refuses to build if that table differs from the supported game
version or another mod (other than an earlier Arsenal release) replaces it. No Lua scripts, hooks, saves or co-op
files are included. Removing the package restores native weapons.

Item descriptions that list weapon stats may show the new values only after one restart.

## Accessories (0.4.0)

Accessories are each character's wildcard edge: offence, defence or utility. They take their identity from The
Planetary Age where it adds a new answer, and never copy a job's signature passive. Renamed (owned copies rename
too): Cagnazzo Ring (Magicks ignore Reflect; boosts Ice, Water), Rubicante Ring (Auto-Reflect; boosts Fire, Holy),
Barbariccia Ring (Weatherproof; Poison and Sap ward; boosts Lightning, Wind), Scarmiglione Ring (halves MP costs;
absorbs Holy; boosts Earth, Dark), Mistflow Ring (Regen; Silence ward), Dampening Ring (30% less damage; half
Fire), Scholar Bangle (Libra; half Dark), Cachusha (Sleep and Confuse ward), Hyper Wrist (Stop and Slow ward),
Rancor Bracelet (Berserk, no knockback; half Ice), Reflect Bracers (Reflect, returns some damage), Magnus Gloves
(Faith), Braver Gloves (Weatherproof), Hale Gauntlets (instant-KO and Disease ward), Shell Gorget (Shell; Silence
ward), Zephyr Collar (Anti-Libra; half Wind), Protect Gorget (Protect), Moonsilver Amulet (double EXP; Silence
ward), Elmdore Fang (Sap, absorbs Dark, weak to Holy; Confuse ward), Shinobi Tabi (Invisible, Libra; Immobilize
ward), Pixie Shoes (Float; Confuse ward), Miracle Shoes (Protect, Shell; Sap ward), Iron Duke (Petrify ward; half
Fire, Ice, Lightning). Reworked under their names: Genji Gloves, Turtleshell Choker, Bubble Belt, Black Belt,
Battle Harness, Hermes Sandals, Ribbon (every status and instant KO). Prices and where they are found are unchanged.

