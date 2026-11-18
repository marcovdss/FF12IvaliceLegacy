# Ivalice Legacy

![Game](https://img.shields.io/badge/FINAL%20FANTASY%20XII-The%20Zodiac%20Age%20(PC)-1f4e79)
![Version](https://img.shields.io/badge/version-0.1.60-2e7d32)
![Status](https://img.shields.io/badge/status-early%20development-c77c02)

**An overhaul for FINAL FANTASY XII THE ZODIAC AGE where preparation wins.**

Jobs combine instead of overlapping, gear changes how you play, and every enemy in the game is carved by hand to test
the party you brought. Rare monsters rule their zones, the Clan is worth climbing, and progression can't be steamrolled.
Legacy is balanced as a hard game for a party of four, aiming for the spirit of FINAL FANTASY XI: slow, strategic combat
that rewards teamwork, job combinations, varied items and a unique run each playthrough.

It plays solo, or as couch co-op for up to four players with drop-in and drop-out, set up entirely in game from the
Mod Menu (Mod Config > Ivalice Legacy > Co-op). Co-op runs on Legacy's own HUD, is optional, and solo play is unchanged;
LAN and online are planned. Legacy does not use Ivalice Together.

> **Early development.** Expect large changes between versions. Updates are meant to continue your existing Legacy
> campaign; any restart a version needs will be stated before you install it. There is no public download yet; see
> [Building from source](#building-from-source).

---

## Highlights

### Twelve jobs, sixty-six hybrids
Knight, Lancer, Warrior, Samurai, Hunter, Gunner, Chemist, Green Mage, Time Mage, Mystic, White Mage and Black Mage,
each with a compact board of its own and job stat trainings that grow the stats its role uses. When a character
takes a second job, both boards fuse into **one board named for the hybrid**: Knight + White Mage is a Paladin.
Every board carries the thirteen legendary weapons, each behind its Esper.

### A Clan worth climbing
**Montblanc** awards each job its Clan **Proficiency** line, which works with whatever weapon the character holds.
Twelve Clan ranks raise three stages of four levels, so every rank is a reward. Writs arrive as key items, and each
stage's current effect is shown on the gear. Lines follow the job, so changing jobs never loses progress. At rank 12,
a master of a legendary weapon's family deals +25% with it.

### Weapons with a style of play
A gentle damage climb replaces the late-game spike. Every weapon family has its own way of fighting, and most pay a
downside to answer with a hybrid job, an accessory or a partner: axes wear Defense down (**Sunder**) but swing unevenly,
spears pierce it but most cannot parry, katanas chain longer combos that meet full Defense, greatswords hit the target in full and everyone
around it for half but charge slowly, guns never miss (**True aim**) and charge slowest, daggers and
ninja swords glance off heavy armour (**Glancing**), poles counter on their own evade, rods heal 20% more but have the weakest
attack, crossbows and hammers trade tempo for their status, and fists build up Attack by Attack. Downsides are
matchups, never punishments: nothing fires on the wielder's success or grows with use. Shields choose a side: the
Flame, Diamond and Dragon shields now ward magick instead of blows. No hit can be reduced to nothing by Defense, for
foes or for the party — and every guard, the party's and the foes', tires when it dodges too often.

### Armour that asks questions
105 of the 140 armour pieces gain one strategic property, such as an element ward or a stat trade, chosen for what
foes cast at that point in the story — and the heavy, light and mystic armour triangle is real: each side defends
hard against one threat and softly against the other. Accessories are each character's wildcard edge, and none of
them harms its wearer: Hermes Sandals open every fight with a real Haste, Genji Gloves are a pure Combo Boost,
Miracle Shoes no longer come with Slow.

### Magick has a price
Party attack spells are set individually so they keep pace with weapons. A stronger caster pays more MP, and no spell
can pay for itself. Expose and Shear now take a real 30%, and the Technicks that wasted turns became more reliable.

### Every enemy hand-carved
All 911 main-game foes — commons, bosses, hunts and the 80 rare-game Kings — were reshaped one by one: their numbers,
weaknesses, immunities, statuses, skills, AI and loot, banded by each foe's own level and set against the party's whole
toolkit. Bosses are memorable, phased fights, never a longer HP bar: all 29 story bosses are crowned, scale to your
party, and reward you with their rarest steal and triple LP, while inflated pools came back down to earth. More than
1,100 added behaviours across 610 AI scripts: support foes rescue their allies, packs punish clustered parties,
wounded foes escalate, bosses switch their moves midway, and monster families fight in their own way. Enemy skills
carry new riders, and everyday items answer them.

### Kings of the zone
Every rare game is the King of its zone. It is there on every visit until you defeat it once, and only rarely
returns after that.

### Couch co-op for four
Up to four players share one game: the host's save owns the world, and every seat drives its own party member —
its own command window, its own target cursor, its own gambits — with a pad or keyboard and mouse. Guests join and
leave on their own keys, walk the towns with the party, and answer the world with their own interact prompt.
Everything is configured in game: seats, controls, HUD and play options live under Mod Config > Ivalice Legacy >
Co-op; there is no external configurator and no hand-edited files. Controls are rebindable with clash checks, a
kill-switch ends co-op at once, pads keep working when the window loses focus, and the Safety page warns when another
mod's options would break co-op and can restore Legacy's settings.

### A HUD of its own
Legacy draws its own HUD over the game's: one identical command window per seat at a fixed bottom slot, the party
list with HP warning and critical steps, Target Info and the battle log. Long names scroll instead of truncating,
rare drops carry their own marker, and treasure is announced in the combat log, so opening a chest never pulls you
out of the fight. The whole HUD comes in themes — Bold Modern, Refined Vanilla and Crisp Vanilla are built in, and
player-made themes drop into a folder — chosen from the Mod Menu. A theme changes the look, never the layout.

### A world that grows with you
Rabanastre opens with a wider set of tools. Shops add stock as the story advances. Key ability chests always appear,
and weapons that could be found far too early now wait their turn.

### Your own save files and gambits
Legacy saves are `LEGCY_###` files, kept apart from your normal `FFXII_###` saves, with separate settings for several
companion mods. Legacy also ships its own gambit list — all 255 gambits reworded, every gambit 100 gil, sorted into
15 pages — so Gambit Rework is not needed. Gambits are stored by number, so check each character's gambits after
loading an older save.

**[Read the full feature guide &rarr;](docs/FEATURES.md)**

---

## Requirements

Install these with Legacy. Legacy never edits another mod's scripts or data. Where it wins a file, that file is the other
mod's own settings file.

- **FINAL FANTASY XII THE ZODIAC AGE**, Steam version 1.0.4.0, with the game language set to **English**.
- [FF12 External File Loader](https://www.nexusmods.com/finalfantasy12/mods/170) and
  [FF12 LUA Loader](https://www.nexusmods.com/finalfantasy12/mods/171) 2.0.1 with its dialog plugin.
- [The Insurgent's Forge](https://www.nexusmods.com/finalfantasy12/mods/386): Proficiency, weapon identities, damage,
  magicks and MP.
- [The Insurgent's Stat Lores](https://www.nexusmods.com/finalfantasy12/mods/299): job stat trainings.
- [Dynamic Description](https://www.nexusmods.com/finalfantasy12/mods/466) and
  [The Insurgent's Descriptive Inventory](https://www.nexusmods.com/finalfantasy12/mods/319): item and gear text.
- [Dynamic Bosses](https://www.nexusmods.com/finalfantasy12/mods/503) 1.0.2 and
  [Instanced Foes](https://www.nexusmods.com/finalfantasy12/mods/528) 1.0.1: the enemy foundation. Legacy's own enemy
  layer is built on them and edits none of their files.

**Settings owners, also required.** Legacy ships its own copy of the settings file for each of these mods, so your
normal game's settings stay as they were, and they only take effect with the mod installed:
[The Insurgent's Bountiful Bundle](https://www.nexusmods.com/finalfantasy12/mods/185),
[Global Gameplay Settings](https://www.nexusmods.com/finalfantasy12/mods/396),
[The Insurgent's Vision](https://www.nexusmods.com/finalfantasy12/mods/220),
[Orderkeeper](https://www.nexusmods.com/finalfantasy12/mods/527) and
[The Insurgent's Manifesto](https://www.nexusmods.com/finalfantasy12/mods/218). Two of their options break Legacy's
co-op: Vision's No Combat Log and Manifesto's No Auto Pause. Keep both off. The Safety page in the Co-op menu warns if
either is on and can restore Legacy's settings.

**Recommended**, and what Legacy is tested with: [The Insurgent's Companions](https://www.nexusmods.com/finalfantasy12/mods/217)
for a party of four, [The Insurgent's Chain Benefits](https://www.nexusmods.com/finalfantasy12/mods/296),
[Hunter's Insight](https://www.nexusmods.com/finalfantasy12/mods/400),
[Second Accessory](https://www.nexusmods.com/finalfantasy12/mods/436) and
[Summons For All](https://www.nexusmods.com/finalfantasy12/mods/381). Legacy ships its own settings for Companions,
Chain Benefits and Hunter's Insight.

**Not needed:** [Gambit Rework](https://www.nexusmods.com/finalfantasy12/mods/471). Legacy ships its own gambit list in
the same game files, so do not install both. **Do not install** [Ivalice Together](https://www.nexusmods.com/finalfantasy12/mods/548)
either: Legacy's co-op replaces it, and starts switched off when it finds Together.

**Not compatible** with other content overhauls (Struggle for Freedom, The Planetary Age, Horizontal Progression
Overhaul and similar): they replace the same game data. Keep Bountiful Bundle's "Always Spawn Rare Game" **off**, or
defeated Kings return on every visit.

## Installing

- **Vortex (recommended):** install the package as a mod in its own profile, so your other setups stay intact. Let
  Ivalice Legacy win every file it shares with the mods it builds on (Stat Lores, Dynamic Description,
  Descriptive Inventory and the settings files of Bountiful Bundle, Global Gameplay Settings, Vision, Orderkeeper,
  Manifesto, Companions, Chain Benefits and Hunter's Insight), then deploy. Dynamic Bosses and Instanced Foes stay
  exactly as their authors ship them: Legacy replaces none of their scripts, and the only file of Dynamic Bosses' that
  Legacy wins is its settings file (`config/DynamicFoes/modMenu.json`), which keeps Dynamic Bosses to ordinary foes.
- **Manually:** copy the package's `mods` and `x64` folders into the game folder, merging them with the existing ones.

**Uninstalling:** disable the mod in Vortex, or delete only the files that came from the package, keeping the rest of
the `mods` and `x64` folders. Your normal saves were never touched. Licenses bought on Legacy boards stay bought.

## Building from source

This repository contains only Ivalice Legacy's own code and patch definitions. No game files are included: packages
are built from your own installed game. You need Python 3.10 or newer, with no other packages.

```
python -m ivalice_legacy build --game "<game folder>"              # shops, loot, Kings, magicks, saves
python -m ivalice_legacy build-base-jobs --game "<game folder>"    # the twelve jobs
python -m ivalice_legacy build-arsenal --game "<game folder>"      # weapons and armour
python -m ivalice_legacy build-proficiency --game "<game folder>"  # Clan Proficiency
```

Each command writes a zip plus a manifest of file hashes to `dist/`. A build stops with an error if your game files
differ from the supported version, or if another mod already replaces the same data.

These commands build the systems as separate component packages. The combined 0.1.x release, which also holds the
enemy rework and the co-op module, is not yet reproducible from this repository alone. A single build command for the
whole release is planned before the first public download.

The co-op module (`native/coop/`) builds separately with its own pinned MSYS2 UCRT64 toolchain; see
[native/coop/README.md](native/coop/README.md).

Run the tests with `python -m unittest discover -s tests`. Set `IVALICE_LEGACY_GAME` to your game folder to include
the full build tests. The Lua tests need [Lupa](https://pypi.org/project/lupa/).

<details>
<summary>Repository layout</summary>

| Path | Contents |
|---|---|
| `patches/` | Readable patch definitions: what changes, plus hashes of the expected source and result |
| `ivalice_legacy/` | Archive reader, file formats, writers and the build commands |
| `package/` | Lua runtime and configuration shipped with the game package |
| `package_base_jobs/`, `package_arsenal/` | Documentation and changelogs for jobs and weapons |
| `prototypes/` | Lua for Proficiency, weapon identities, the MP economy and the damage formula |
| `native/hybrid_boards/` | The fused hybrid-board core and its build tools |
| `native/coop/` | The co-op module: input, session, menus, Wait, the overlay HUD, themes (C++, its own README) |
| `tests/` | Tests for the writers, the Lua modules and the full builds |
| `docs/` | The feature guide and the HUD theme documentation |

</details>

## Planned

- LAN and online co-op, with drop-in joining over a network; the couch build leaves the seams open.
- Each King's own fight and reward, and a reworked Clan Centurio with its hunts.
- Job-tied stat growth: level-ups that follow the current job, in the FF11 style (decided, in study).
- Job armour lines with their own immunities, and more gear with real trade-offs.
- A signature command for every hybrid.
- Boss phases and group roles beyond the current strategies.
- Extra packs and placements from the enemy rework's plus layer, once they are proven in play.
- Espers, exploration and the Bazaar revisited.
- Progression paced so preparation matters more than grinding.

## Credits

File formats build on the FFXII modding community's work, including The Insurgent's Workshop and Toolkit (Xeavin),
the FF12 External File Loader and Lua Loader (ffgriever), FehDead's dialog system, and VBFTool (topher-au). Thanks to
the authors of the mods Legacy builds on, listed under [Requirements](#requirements). The co-op module statically
links SDL2 and MinHook; their licences ship with it in `native/coop/THIRD_PARTY_NOTICES.txt`.

FINAL FANTASY XII is a trademark of Square Enix. This is an unofficial fan project, and no game files are distributed
here.
