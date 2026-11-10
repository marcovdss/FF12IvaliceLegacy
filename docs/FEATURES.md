# Ivalice Legacy: feature guide

Everything Ivalice Legacy changes as of **0.1.23**, system by system. The [homepage](../README.md) has the short
version, requirements and installation.

Each section ends with its **play state**: *played* means it has been seen working in a real campaign, *offline*
means it is built and covered by tests but not yet confirmed in play.

- [Jobs](#jobs)
- [Hybrid jobs](#hybrid-jobs)
- [Clan Proficiency](#clan-proficiency)
- [Weapons](#weapons)
- [Armour and accessories](#armour-and-accessories)
- [Magicks, MP and Technicks](#magicks-mp-and-technicks)
- [Enemies](#enemies)
- [Kings of the zone](#kings-of-the-zone)
- [Shops, loot and the world](#shops-loot-and-the-world)
- [Interface and text](#interface-and-text)
- [Separate saves and settings](#separate-saves-and-settings)

---

## Jobs

Twelve standalone jobs replace the Zodiac Age roster. Each has a compact board of its own. The native job slots
keep their IDs, so saves continue and licenses you already own stay owned. There is no automatic respec.

| Job | Replaces | Proficiency line | Legendary families it masters |
|---|---|---|---|
| Knight | Knight | Bulwark | Sword |
| Lancer | Uhlan | Grounding | Spear |
| Warrior | Foebreaker | Cleave | Greatsword, Axe, Hammer |
| Samurai | Bushi | Zanshin | Katana, Ninja sword |
| Hunter | Archer | Pursuit | Bow, Dagger |
| Gunner | Machinist | Suppression | Gun |
| Chemist | Shikari | Contagion | Crossbow |
| Green Mage | Red Battlemage | Mana Leech | Mace |
| Time Mage | Time Battlemage | Detonate | Measure, Hand-bomb |
| Mystic | Monk | Ki Flow | Pole |
| White Mage | White Mage | Overflow | Rod |
| Black Mage | Black Mage | Resonance | Staff |

**Boards.** Each board is a compact region around Essentials. Gear sits above, accessories to the left, passive
augments to the right, and magicks and Technicks below, with the cheaper licenses nearest the centre. Espers and
Quickenings sit on the outer edge. An ordinary board costs 4,276 to 5,376 LP and has two Swiftness licenses, both
reachable before half that total. Connected opening kits cost 70 to 145 LP. Licenses never supply the weapon or
command itself.

**Shared basic tools.** Where another role needs a basic tool, its board has one. Knight and Warrior get Decoy and
Oil, Knight gets Cure and Blindna, and Gunner, Green Mage and Mystic get Libra. The schools and signature Technicks
stay with their own job.

**Legendary islands.** Every board carries the same thirteen islands. Each Esper gates the zodiac weapon of its sign,
and Zodiark gates Masamune. An island opens only for a character who owns that Esper.
[Summons For All](https://www.nexusmods.com/finalfantasy12/mods/381) lets anyone license any Esper.

| Esper | Weapon | Esper | Weapon |
|---|---|---|---|
| Belias | Mesa | Shemhazai | Kanya |
| Mateus | Mina | Chaos | Vrsabha |
| Adrammelech | Kumbha | Zeromus | Dhanusha |
| Hashmal | Simha | Exodus | Tula |
| Cuchulainn | Vrscika | Ultima | Karkata |
| Famfrit | Makara | Zodiark | Masamune |
| Zalera | Mithuna | | |

**Job stat trainings.** The deep, repeated Battle and Magick Lores are gone. Every job keeps the shared basics (HP
Lore 1-10, Battle Lore 1-4, Magick Lore 1-4) and has two trainings of its own: I at 50 LP and II at 100 LP. Totals
stay close to the base game's, but each job grows the stats its weapons and role use. This needs
[The Insurgent's Stat Lores](https://www.nexusmods.com/finalfantasy12/mods/299).

| Job | Training | I | II |
|---|---|---|---|
| Knight | Bulwark Lore | Defense +3, shield evasion +5 | Defense +4, magick shield evasion +5 |
| Lancer | Dragoon Lore | Strength +3, Magick Resist +3 | Strength +4, Magick Resist +4 |
| Warrior | Juggernaut Lore | HP +435, Strength +2, Vitality +4 | HP +500, Strength +3, Vitality +6 |
| Samurai | Bushido Lore | Strength +2, MP +40 | Strength +2, Magick +1, MP +60 |
| Hunter | Fleetfoot Lore | Strength +2, Speed +2 | Strength +3, Speed +3 |
| Gunner | Deadeye Lore | Speed +2, Attack +1 | Speed +2, Attack +1 |
| Chemist | Field Medic Lore | Vitality +3, Magick Resist +3, Strength +2 | Vitality +4, Magick Resist +4, Strength +2 |
| Green Mage | Hexcraft Lore | Magick +5, MP +30 | Magick +7, MP +50 |
| Time Mage | Chronomancy Lore | Magick +2, Speed +2 | Magick +4, Speed +2 |
| Mystic | Mysticism Lore | Strength +2, Magick +2 | Strength +2, Magick +2 |
| White Mage | Devotion Lore | Magick +4, Magick Resist +3 | Magick +5, Magick Resist +4 |
| Black Mage | Sorcery Lore | Magick +6 | Magick +8 |

*Play state: played in the early game, where it feels good.*

## Hybrid jobs

A character's second job (unlocked natively after Belias) turns the two license boards into **one board**, named for
the hybrid. Knight + White Mage is a **Paladin**, whichever job you picked first. All 66 pairs exist. Job IDs, license
meanings, prices, owned licenses and LP all stay native: only the board's layout and name change. If the mod is
removed, bought licenses stay bought and the two native tabs return.

On a fused board, the first job's gear goes on the left and the second job's on the right. Weapons sit in the centre,
then shields, then armour further out. Accessories form a block on the left, commands a block below and passives a
block on the right, with the cheapest licenses nearest Essentials. Every license stays reachable. Each hybrid has its
own Proficiency line instead of either parent's.

<details>
<summary>All 66 hybrids (names are provisional)</summary>

| Hybrid | Jobs | Line |
|---|---|---|
| Alchemist | Chemist + Green Mage | Transmute |
| Arc Knight | Knight + Time Mage | Tempo Guard |
| Arcane Archer | Hunter + Black Mage | Seeker |
| Arcanist | Time Mage + Black Mage | Entropy |
| Artillerist | Gunner + Black Mage | Barrage |
| Assassin | Samurai + Green Mage | Ansatsu |
| Astrologer | Time Mage + Mystic | Starfall |
| Bard | Hunter + White Mage | Ballad |
| Battlemage | Warrior + Black Mage | Alternation |
| Beastslayer | Lancer + Hunter | Big Game |
| Berserker | Warrior + Samurai | Frenzy |
| Bishop | Chemist + White Mage | Sacrament |
| Breaker | Lancer + Green Mage | Unmake |
| Cannoneer | Lancer + Gunner | Siege |
| Chaplain | Gunner + White Mage | Last Rites |
| Dark Knight | Knight + Mystic | Abyss |
| Defender | Knight + Warrior | Last Stand |
| Divine Knight | Samurai + Mystic | Mind Blade |
| Dragoon | Lancer + Samurai | Highwind |
| Elementalist | Lancer + Black Mage | Fracture |
| Engineer | Gunner + Chemist | Field Kit |
| Fell Knight | Warrior + Mystic | Blood Price |
| Flintlock | Warrior + Gunner | Point Blank |
| Fusilier | Knight + Gunner | Overwatch |
| Geomancer | Hunter + Mystic | Earthsense |
| Gladiator | Warrior + Chemist | Arena |
| Grenadier | Chemist + Black Mage | Ignite |
| Gunslinger | Samurai + Gunner | Quickdraw |
| Heaven Knight | Lancer + White Mage | Crusade |
| Hell Knight | Lancer + Mystic | Hellfire |
| Hexgunner | Gunner + Mystic | Hex Round |
| Kensei | Samurai + Time Mage | Ma |
| Monk | Warrior + White Mage | Chakra |
| Netherseer | Green Mage + Mystic | Umbra |
| Ninja | Samurai + Hunter | Kage |
| Oracle | Green Mage + Time Mage | Prophecy |
| Paladin | Knight + White Mage | Vow |
| Quartermaster | Knight + Chemist | Provision |
| Ranger | Hunter + Time Mage | Long Hunt |
| Ravager | Lancer + Warrior | Havoc |
| Reaver | Warrior + Green Mage | Leech |
| Red Mage | White Mage + Black Mage | Doublecast |
| Ronin | Samurai + Chemist | Wanderer |
| Rune Knight | Knight + Black Mage | Runeguard |
| Saboteur | Lancer + Chemist | Corrode |
| Sage | Mystic + Black Mage | Omniscience |
| Seer | Green Mage + White Mage | Foresight |
| Sentinel | Knight + Hunter | Watch |
| Shaman | Chemist + Mystic | Spirit Medicine |
| Sharpshooter | Gunner + Time Mage | Timing |
| Skyseer | Time Mage + White Mage | Dawnwind |
| Sniper | Hunter + Gunner | Scope |
| Sohei | Samurai + White Mage | Kaji |
| Spellblade | Samurai + Black Mage | Imbue |
| Stalker | Hunter + Green Mage | Quarry |
| Sword Saint | Knight + Samurai | Iai |
| Tempest | Warrior + Time Mage | Stormfront |
| Templar | Knight + Lancer | Phalanx |
| Tinker | Chemist + Time Mage | Clockwork |
| Trapper | Hunter + Chemist | Snaretrap |
| Trickster | Gunner + Green Mage | Dirty Trick |
| Uhlan | Lancer + Time Mage | Window |
| Viking | Warrior + Hunter | Raid |
| Warden | Knight + Green Mage | Snare |
| Warlock | Green Mage + Black Mage | Malediction |
| White Monk | Mystic + White Mage | Ki Healing |

</details>

Fused boards are tied to the supported game executable (Steam 1.0.4.0). If a game update changes the executable, the
boards switch themselves off and the game shows the two native tabs until Legacy is updated. The log records the reason.

*Play state: offline. Installed, but not yet played.*

## Clan Proficiency

Each job and each hybrid has a Clan line. **Montblanc** awards it, and it applies to whatever weapon the character
holds.

- **12 Clan ranks = 3 stages x 4 levels.** Ranks 1-4 raise stage I, 5-8 stage II and 9-12 stage III, so every new
  rank is a reward.
- **It follows the job.** A character uses the line of the job they have now, at the levels the Clan has earned.
  Changing jobs never loses progress.
- **Montblanc speaks.** When the party leaves the Clan hall with a rank he hasn't rewarded yet, he speaks in the
  game's own dialogue windows. Saves that were already ranked catch up once. His menu explains Proficiency too
  ("Tell me about Proficiency").
- **The Writs are key items.** He hands over an Apprentice's, Journeyman's and Master's Writ at ranks 1, 5 and 9, and
  stamps it at every other rank. A stage only works while its Writ is in Key Items.
- **A Clan Primer tip.** "Clan Proficiency" joins the Traveler's Tips once the Apprentice's Writ is in Key Items.
- **Shown on the gear.** In the Equipment menu, weapons show stage I, body armour stage II and helmets stage III,
  with that stage's current level and effect for the selected character. Locked stages point to Montblanc.
- **Fair statuses.** Statuses that Proficiency adds respect the same immunities as the game's own, and a shorter grant
  never cuts a longer one short. A spell that hits several foes gives its self, ally and party rewards once per cast.
- **Legendary mastery.** At rank 12, a character whose job owns a legendary weapon's family deals +25% with it. A
  hybrid counts as a master if either parent job owns the family. Everyone else wields it at base power.

| Job | Line | Stage I in short |
|---|---|---|
| Knight | Bulwark | A parry or block draws the attacker to the Knight |
| Lancer | Grounding | Hits strip Float and Haste, then Faith, Regen and Bravery |
| Warrior | Cleave | A kill grants Bravery |
| Samurai | Zanshin | Hits in a row on one target grant Haste |
| Hunter | Pursuit | More damage to targets hindered by an ailment |
| Gunner | Suppression | Hits push back the target's next action |
| Chemist | Contagion | Statuses it lands can spread to another foe |
| Green Mage | Mana Leech | Hits drain MP to her |
| Time Mage | Detonate | Hits can briefly Stop a Slowed target |
| Mystic | Ki Flow | Counterattacks heal the Mystic |
| White Mage | Overflow | Healing past full HP becomes Regen |
| Black Mage | Resonance | Casting the same element twice makes the next action charge faster |

Stages II and III add new facets of the same idea. Every one of the 144 base levels and all 66 hybrid lines have an
effect, built from statuses, HP and MP changes, damage scaling and charge pushback. Pushback has a budget: one foe
can be pushed back at most 25% of a turn between its own actions, or 12.5% for bosses, marks and rare game.

*Play state: stage I played at rank 1. Stages II and III and the hybrid lines are offline.*

## Weapons

**Damage formula.** Every hit keeps the game's attack minus defence, but never drops below a quarter of the attack.
Where attack minus defence is already larger, the hit is the game's own. Defence still matters in ordinary matchups;
it just can no longer erase a hit. The floor applies to foes' hits as well, so armoured party members feel pressure
too.

**A gentle attack curve.** The opening weapons keep their attack and every later weapon is lower than native. A
typical hit grows from about 47 at the start to about 830 by the last shops (native: about 1,375). Each weapon keeps
its place in its story stage. Rewards are set for where they're found. The 13 legendary weapons still lead their
families, and story and guest weapons are unchanged. A faster or more critical family hits a little softer, and a
slower one a little harder.

**Family identities.** Every weapon family has one style of play, always on for any party member who wields it. It
follows the weapon, while Proficiency follows the job.

| Family | Identity |
|---|---|
| Sword | **Guard**: the best weapon evade (20-40) |
| Greatsword | **Sweep**: Attack cleaves a half circle around the target for half damage; knockback 25 |
| Katana | **Chain**: the highest combo rate (30-45); a rolled combo runs 2 hits longer |
| Ninja sword | **Unseen**: hits ignore weapon and shield evasion (parries still work); Blind 10% |
| Spear | **Pierce**: every hit ignores 30% of Defence; long reach, hits flying foes |
| Pole | **Counterflow**: the wielder counters twice as often; strikes Magick Resist |
| Bow | **Volley**: critical rate 20-30, the longest reach, the anti-air answer |
| Crossbow | **Afflict**: on-hit status chance doubled (up to 100%) |
| Gun | **Marksman**: shots ignore Defence and can never be parried |
| Axe | **Sunder**: every hit dents Defence 5% for the rest of the fight (down to half) |
| Hammer | **Concussion**: Disable 15% on hit, knockback 30 |
| Dagger | **Swift**: charge time 15, frequent combos, a little evade |
| Rod | **Mend**: healing spells heal +20% while it is held (potions unchanged) |
| Staff | **Channel**: element potency and the most Magick of the caster weapons |
| Mace | **Spellbreak**: hits burn foe MP equal to 10% of the damage (at most 10% of its pool) |
| Measure | **Tempo**: Protect, Shell, Bravery, Haste, Invisible or Bubble on hit |
| Hand-bomb | **Barrage**: Slow or Oil 20% on hit |
| Unarmed | **Cadence**: each consecutive hit on the same target deals +6%, up to +30% |

There are four ways past armour, one per family: axes wear Defence down, spears ignore part of it, guns and measures
ignore all of it, and poles strike Magick Resist instead. Ammunition decides the element and status for bows,
crossbows, guns and hand-bombs.

**Legendary weapons** stay the strongest of their families even at base power. Great Trango and Seitengrat now swing
at normal greatsword and bow speed, and Kumbha counts as a katana.

See [package_arsenal/README.md](../package_arsenal/README.md) for every weapon's numbers.

*Play state: the attack curve, the data identities and the greatsword sweep have been played. The runtime identities
(Chain, Unseen, Pierce, Counterflow, Afflict, Marksman, Sunder, Mend, Spellbreak, Cadence) are offline.*

## Armour and accessories

**Armour rework.** 105 of the 140 armour and shield pieces gain one strategic property: an element ward or focus, or
a stat trade that costs something. Each one is chosen for what foes cast at that point in the story. The effects are
real item data, not description text.

- Defence, Magick Resist and augments never change.
- No new status immunities; those are reserved for future job armour lines.
- A stat gain always costs another stat. Shields trade evade for magick evade.
- Pieces of the same type, slot and story stage never share a property.

**Accessory identities.** Accessories are each character's wildcard edge: offence, defence or utility. Their
identities draw on established overhauls where that adds a new answer, and never copy a job's signature passive.
Renamed accessories rename the copies you already own. Prices and locations are unchanged.

*Play state: armour text seen in game; its effects and the accessories are not yet confirmed in play.*

## Magicks, MP and Technicks

**Attack magicks on the weapon climb.** Spells kept their native power while weapons were lowered, so by the last
shops a spell hit 4 to 7 times as hard as a swing. Party attack spells from Aqua to Scathe, plus Holy and the
Dark line, now each have their own power, with the reason written beside it in `magicks.lua`. Fire, Thunder and Blizzard
stay native, as do area and targeting. Foes cast the same spells at full power, because the change only applies to the party's casts. The
balance between spells and weapons is still being calibrated from play.

**MP economy.** A stronger caster pays more, and no spell can pay for itself.

- A party Magick costs what the game charges times Magick / 30, never less than the game's own cost and never more
  than three times it. The game's own discounts (Channeling, Parsimony, Spellspring, Offering) apply first.
- Everything one cast gives back as MP, to the caster or to allies, is capped at half of what that cast cost. MP
  drained from a foe's own pool and MP from items are not capped.
- MP gained from taking a hit is limited to 2% of the receiver's max MP. Combo follow-ups on the same foe give no extra
  HP or MP, and item heals restore MP only through lines that say "items".
- Foes' spells and the listed cost in the game's tables stay native.

**Technicks.** Expose and Shear now take 30% from a foe, like Wither and Addle (instead of 10%), so armoured and warded
foes have a real answer. The party's Decoy lasts about two minutes. All 24 Technicks were reviewed and three became
more reliable:

| Technick | Change |
|---|---|
| First Aid | Heals a critical ally for at least 10% max HP; better rolls still count |
| Horology | Deals at least level x 10 to each foe, so low clock readings no longer waste a turn |
| Achilles | Always gives an element the foe was not already weak to |

The rest keep their identity.

*Play state: offline. The MP economy's cost and charge have been confirmed in the game log, not yet in a full play
session.*

## Enemies

**Foundation.** Every foe has its own copy of its data
([Instanced Foes](https://www.nexusmods.com/finalfantasy12/mods/528)), and
[Dynamic Bosses](https://www.nexusmods.com/finalfantasy12/mods/503) crowns some common foes with an element, an aura and
a matching magick. Legacy's scaling gives each foe its own HP, MP, Vitality and Defence roll (x1 to x1.5), and a
stat pass raised foes' Strength, Magick, Speed and Attack without touching their HP or Defence. The damage floor means
no foe is ever unhurtable and no tank is ever untouchable.

**Story-boss crown.** All 29 main-story bosses are crowned every time. They get twice their own HP (times the Health
option), and their Strength, Magick and Attack rise to 1.25x the party's average where that is higher. They also get
permanent Bravery and Faith, and their kind's tiered spell in their AI. The crown's element never matches a native
weakness. Reward: the boss's rarest steal always drops, and LP x3. Optional bosses, marks and rare games follow
Dynamic Bosses' own settings, and Trial Mode is never crowned.

**Enemy strategies.** 1,132 behaviour branches across 610 AI scripts in 124 areas, plus 17 phase substitutions. The
game's own leading rules keep priority, every addition happens once per foe, and each uses only skills that foe's
model already performs.

| Behaviour | Branches | What it does |
|---|---|---|
| Teamwork | 23 | Support foes rescue an injured or afflicted ally with a matching cure |
| Reactions | 95 | Casters, or your buffs, provoke a fitting response |
| Positioning | 377 | Party members standing close together draw an area or cone attack, with wind-up and recovery |
| Escalation | 447 | A wounded foe escalates once below 30% HP, then recovers |
| Boss variety | 190 | Bosses, hunts and Kings switch to another move below 50% HP |

**Monster family signatures.** Families fight in their own way. Rats go for the lowest Defence, and wounded Mimics
turn on the party member furthest away.

**Foe skills and the answers to them.** 41 enemy skills across the game gain riders or wind-ups. Elemental beast
attacks carry a 10% rider, Sonic Fangs' KO becomes 20% Sap, Darkja's KO becomes Doom, and Self-Sacrifice and Kamikaze
get a wind-up. Ten consumables answer them:

| Item | Change |
|---|---|
| Gold Needle | Also cures Doom |
| Chronos Tear | Also cures Disable and Immobilize |
| Antidote | Also cures Sap |
| Echo Herbs | Also cures Berserk |
| Soleil / Rime / Lightning Fang | 10% Oil / Slow / Disable |
| Dark / Aero / Aquara Mote | 10% Blind / Slow / Silence |
| Serum | Costs 150 gil (was 700) |

**Estersand hunts.** Flowering Cactoid no longer uses 1000 Needles and casts Cure at most once per stage of the fight.
Ripe Rampager starts recovering below 60% HP (was 50%) and stops at 70% (was 60%).

*Play state: in the campaign; the Estersand hunts have been played. Most individual behaviours, the boss crown and the
latest stat pass have not been checked one by one.*

## Kings of the zone

Every rare game (the Bestiary's 80 entries) is the King of its zone. It is there on every visit until you defeat it
once. After that it follows the game's own conditions (a roll, play time, a chain and so on), so it can return, but
rarely. "Defeated" is the Bestiary's own count, so Kings you defeated before installing Legacy already count.

- Kings are stronger than ordinary foes in both offence and defence.
- Hunt Club Kings are there from the start, quest or no quest, and leave once you earn their trophy.
- Rain Dancer only exists in Giza Plains' rainy season. Aeros appears after Bull Croc falls.
- Where several spawners hold one King, the first one you trigger takes it, so that King appears as you reach the
  spot rather than when you enter the map. On the four maps with two Kings, each is checked separately.
- Keep Bountiful Bundle's "Always Spawn Rare Game" **off**: it answers "yes" everywhere, so every defeated King would
  return on every visit.

Each King's own fight and reward are planned.

*Play state: played on the Estersand. Other zones, and the latest spawner change, have not yet been checked.*

## Shops, loot and the world

**Opening stock.** Rabanastre's merchants sell a wider set of opening tools from the start. Items they would have
stocked later move to the start instead of appearing twice. Prices are unchanged.

| Merchant | New from the start |
|---|---|
| Amal's Weaponry | Bowgun, Iron Hammer, Rod, Oak Staff, Mace, Hornito |
| Panamis's Protectives | Bangle, Orrachea Armlet |
| Migelo's Sundries | Phoenix Down, Antidote, Onion Bolts, Onion Bombs |
| Yugri's Magicks | Thunder, Blizzard, Blind, Silence |
| Batahn's Technicks | First Aid, Steal |

**Story stock.** As the story advances, shops add the gear, ammunition, magicks and Technicks of that stage, so the
shelf grows with the journey instead of all at once. Items that replace a consumable come after it: Phoenix Down
before Raise, Gold Needle before Stona.

**Reliable ability chests.** Cleanse (Cerobi Steppe), Addle (Henne Mines) and Wither (Pharos) always appear.

**Early loot moves.** Three weapons that could be found far ahead of their time now wait until later. Seitengrat
moves from the Skyferry to a Pharos at Ridorana chest. Burning Bow moves from the desert Dive Talons to the Cerobi
Steppe Bellwyvern. Gladius moves from two early chests and the Westersand Lindbur Wolf to a steal from the Paramina
Rift Feral Retriever. Every early slot they leave keeps a weapon of its own tier.

**Guest gear.** Guest weapons that [The Insurgent's Companions](https://www.nexusmods.com/finalfantasy12/mods/217)
makes reachable get proper names and descriptions.

*Play state: played.*

## Interface and text

- **Equipment descriptions.** Status and element groups (Hit, Equip, Immune, Weak, Half, Absorb, Amplify) join the
  stats and are packed by width, so every description fits on screen even with the Proficiency lines. Notes explain
  hidden mechanics only, never visible stats.
- **Help boxes** keep their position even with long text.
- **Gambits.** All 255 gambit names and help texts are reworded with clear element labels. Conditions are untouched.
- **Cure items'** battle help lists their new cures.
- The native **TARGET INFO** window is drawn at 75% size.

*Play state: gear text played; the latest layout not yet.*

## Separate saves and settings

**Separate save files.** Ivalice Legacy lists and writes `LEGCY_000`, `LEGCY_001`, ... instead of `FFXII_###`, so a
Legacy campaign never mixes with your normal saves. Normal saves are left alone, and nothing is copied, renamed or
converted. Disabling Legacy and restarting the game restores the normal save names. If activation ever fails, the log
says **FATAL** and the script undoes its own changes: stop before loading or saving.

To continue a Legacy game started before 0.0.6, copy its `FFXII_###` file (and its `FFXII_###.dir` folder, if present)
to `LEGCY_###` in the same save folder.

**Separate settings.** While Legacy is enabled, Bountiful Bundle, Global Gameplay Settings, Vision and Orderkeeper keep
their own copies of their settings, so your normal game's settings stay as they were.

*Play state: played.*
