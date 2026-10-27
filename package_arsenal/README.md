# Ivalice Legacy: Arsenal 0.3.0 (experimental)

Weapon attack and weapon family identities for the English Steam game, version 1.0.4.0.

## What changes

Attack follows the story: modest at the start, growing toward a powerful endgame. Each family's identity changes how
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

Story and joke weapons, Seitengrat and Wyrmhero Blade keep their native stats. Other prices are unchanged.

| Family | Attack |
|---|---|
| Dagger | Dagger 14 → 11, Mage Masher 18 → 14, Assassin's Dagger 31 → 25, Chopper 40 → 32, Main Gauche 46 → 38, Gladius 56 → 44, Avenger 66 → 52, Orichalcum Dirk 79 → 61, Platinum Dagger 83 → 64, Zwill Crossblade 87 → 67, Shikari Nagasa 90 → 69, Mina 104 → 81 |
| Katana | Kotetsu 16 → 14, Osafune 26 → 23, Kogarasumaru 40 → 34, Magoroku 50 → 42, Murasame 62 → 51, Kiku-ichimonji 71 → 58, Yakei 83 → 66, Ame-no-Murakumo 96 → 74, Muramasa 104 → 81, Masamune 111 → 93 |
| Bow | Shortbow 15 → 14, Silver Bow 19 → 17, Aevis Killer 25 → 22, Killer Bow 36 → 32, Longbow 48 → 35, Elfin Bow 54 → 48, Loxley Bow 60 → 53, Giant Stonebow 73 → 63, Burning Bow 77 → 67, Traitor's Bow 82 → 71, Yoichi Bow 89 → 77, Perseus Bow 92 → 79, Artemis Bow 94 → 81, Sagittarius 97 → 83, Dhanusha 109 → 103 |
| Greatsword | Claymore 83 → 91, Defender 90 → 99, Save the Queen 92 → 101, Ragnarok 117 → 130, Ultima Blade 109 → 121, Excalibur 127 → 141, Tournesol 138 → 153 |
| Axe | Handaxe 17 → 15, Broadaxe 36 → 24, Slasher 54 → 43, Hammerhead 81 → 53, Francisca 88 → 61, Greataxe 110 → 74 |
| Hammer | Iron Hammer 24 → 15, War Hammer 42 → 35, Sledgehammer 66 → 53, Morning Star 100 → 69 |
| Hand-bomb | Hornito 66 → 15, Fumarole 71 → 49, Tumulus 76 → 63, Caldera 83 → 57 |

## Package

The package replaces one section of `battle_pack.bin` (the equipment table, section 13) through the FF12 External File
Loader. It is built from your own game files and refuses to build if that table differs from the supported game
version or another mod (other than an earlier Arsenal release) replaces it. No Lua scripts, hooks, saves or co-op
files are included. Removing the package restores native weapons.

Item descriptions that list weapon stats may show the new values only after one restart.
