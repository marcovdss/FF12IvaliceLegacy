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
| Warrior | Foebreaker | Cleave | Greatsword, Axe |
| Samurai | Bushi | Zanshin | Katana, Ninja sword |
| Hunter | Archer | Pursuit | Bow, Dagger |
| Gunner | Machinist | Suppression | Gun |
| Chemist | Shikari | Contagion | Crossbow, Measure |
| Green Mage | Red Battlemage | Mana Leech | Mace |
| Time Mage | Time Battlemage | Detonate | Hand-bomb, Hammer |
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

**Licenses that changed boards (0.1.35).** Telekinesis moves from the Knight to the Warrior. Achilles moves from the
Mystic to the Gunner, who can load the shot of the element it opens. Protectga and Shellga (White Magick 12) move from
the Knight to the White Mage. The Lancer no longer carries White Magick 5 (Dispel and Stona), which stay with the White
Mage. A character who already learned one of these keeps it, and hybrid boards follow the base boards they fuse.

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
| Arithmetician | Time Mage + Black Mage | Entropy |
| Artillerist | Gunner + Black Mage | Barrage |
| Assassin | Samurai + Green Mage | Ansatsu |
| Astrologer | Time Mage + Mystic | Starfall |
| Bard | Hunter + White Mage | Ballad |
| Battlemage | Warrior + Black Mage | Alternation |
| Beastslayer | Lancer + Hunter | Big Game |
| Berserker | Warrior + Samurai | Frenzy |
| Bishop | Chemist + White Mage | Sacrament |
| Magebreaker | Lancer + Green Mage | Unmake |
| Cannoneer | Lancer + Gunner | Siege |
| Chaplain | Gunner + White Mage | Last Rites |
| Chrono Knight | Knight + Time Mage | Tempo Guard |
| Chrono Lancer | Lancer + Time Mage | Window |
| Cleric | Time Mage + White Mage | Dawnwind |
| Corsair | Warrior + Gunner | Point Blank |
| Crusader | Warrior + White Mage | Chakra |
| Dark Knight | Knight + Mystic | Abyss |
| Defender | Knight + Warrior | Last Stand |
| Despoiler | Lancer + Chemist | Corrode |
| Divine Knight | Samurai + Mystic | Mind Blade |
| Dragonkin | Lancer + Black Mage | Fracture |
| Dragoon | Lancer + Samurai | Highwind |
| Engineer | Gunner + Chemist | Field Kit |
| Fell Knight | Warrior + Mystic | Blood Price |
| Geomancer | Hunter + Mystic | Earthsense |
| Gladiator | Warrior + Chemist | Arena |
| Detonator | Chemist + Black Mage | Ignite |
| Gunbreaker | Knight + Gunner | Overwatch |
| Gunslinger | Samurai + Gunner | Quickdraw |
| Heaven Knight | Lancer + White Mage | Crusade |
| Hell Knight | Lancer + Mystic | Hellfire |
| Ki Gunner | Gunner + Mystic | Hex Round |
| Lanista | Warrior + Green Mage | Leech |
| Monk | Green Mage + Mystic | Umbra |
| Ninja | Samurai + Hunter | Kage |
| Oracle | Green Mage + Time Mage | Prophecy |
| Paladin | Knight + White Mage | Vow |
| Quartermaster | Knight + Chemist | Provision |
| Ranger | Hunter + Time Mage | Long Hunt |
| Ravager | Lancer + Warrior | Havoc |
| Red Mage | White Mage + Black Mage | Doublecast |
| Riskbreaker | Knight + Hunter | Watch |
| Ronin | Samurai + Chemist | Wanderer |
| Rune Knight | Knight + Black Mage | Runeguard |
| Seer | Green Mage + White Mage | Foresight |
| Shaman | Chemist + Mystic | Spirit Medicine |
| Sharpshooter | Gunner + Time Mage | Timing |
| Sniper | Hunter + Gunner | Scope |
| Sohei | Samurai + White Mage | Kaji |
| Spellblade | Samurai + Black Mage | Imbue |
| Spellbow | Hunter + Black Mage | Seeker |
| Spellfist | Mystic + Black Mage | Omniscience |
| Stalker | Hunter + Green Mage | Quarry |
| Swiftblade | Samurai + Time Mage | Ma |
| Sword Saint | Knight + Samurai | Iai |
| Tempest | Warrior + Time Mage | Stormfront |
| Templar | Knight + Lancer | Phalanx |
| Tinker | Chemist + Time Mage | Clockwork |
| Trapper | Hunter + Chemist | Snaretrap |
| Trickster | Gunner + Green Mage | Dirty Trick |
| Viking | Warrior + Hunter | Raid |
| Warden | Knight + Green Mage | Snare |
| Warlock | Green Mage + Black Mage | Malediction |
| White Monk | Mystic + White Mage | Ki Healing |

</details>

**Hybrid names (0.1.36).** Sixteen hybrids were renamed so that each name fits its two jobs: Uhlan is now Chrono Lancer,
Monk (Warrior + White Mage) Crusader, Netherseer (Green Mage + Mystic) Monk, Skyseer Cleric, Arc Knight Chrono Knight,
Fusilier Gunbreaker, Flintlock Corsair, Sentinel Riskbreaker, Reaver Lanista, Kensei Swiftblade, Arcanist
Arithmetician, Hexgunner Ki Gunner, Sage Spellfist, Elementalist Dragonkin, Arcane Archer Spellbow and Saboteur
Despoiler. Boards, licenses and Proficiency lines are unchanged, so nothing needs to be respecced.

**Two more hybrid renames (0.1.37).** Grenadier (Chemist + Black Mage) is now **Detonator** and Breaker (Lancer + Green
Mage) is now **Magebreaker**. Boards, licenses and line names are unchanged.

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
- **Montblanc speaks.** Ask him in the Clan hall: "About Proficiency." in his menu opens a talk in the game's own
  dialogue windows with five choices. "Have I earned anything new?" hands over a rank he hasn't rewarded yet, with its
  Writ or stamp; "Where do I stand?" names your rank, your stages and what the next rank needs; "Tell me about the
  Writs." and "What is Proficiency?" explain; "That's all." ends the talk. Saves that were already ranked catch up
  once. Leaving the hall hands nothing out: with a rank waiting, he reminds you once per visit. Until a Legacy save is
  loaded (a new game too, until you save it and load it), he asks you to save your journey and load it, and hands
  nothing out.
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
| Samurai | Zanshin | A combo grants Haste, and a critical hit too from level 2, but only if the Samurai is not already Hasted |
| Hunter | Pursuit | More damage to targets hindered by an ailment or Broken |
| Gunner | Suppression | Hits push back the target's next action, more for a slow weapon: hits push the charge back 10% x weapon charge time / 24, at most double, so the gun carries it best |
| Chemist | Contagion | A status your attack lands has a 35% chance to spread to another foe, and a measure buff it lands spreads to a second ally at the same chance |
| Green Mage | Mana Leech | Hits leech the foe's MP into the wielder; once the foe has no MP left, the same share heals the wielder's HP instead (see Leech, below) |
| Time Mage | Detonate | Hits on a Slowed or Disabled foe have a 15% chance to Stop it; from level 3 a Confused foe counts too (Chaos Bombs set it up), and stage I-3 and I-4 name Confused in the gear text |
| Mystic | Ki Flow | Counterattacks heal the Mystic; a complete fists Cadence streak counts as one counter |
| White Mage | Overflow | Healing past full HP becomes Regen |
| Black Mage | Resonance | Casting the same element twice makes the next action charge faster; another member's hit or spell of that element on the same foe in the last 5 seconds counts as the first cast |

Two 0.1.35 changes to the later stages: Zanshin's first Haste window can only be opened while the Samurai is not
already Hasted, so a kill while Hasted (stage III) is the way to extend it; and Zanshin stage II now removes the dodge
first (level 1: cannot be dodged) and the parry and block second (level 2: dodged, parried or blocked), so a ninja sword, which already cannot be parried or blocked,
is unevadable from level 1.

Stages II and III add new facets of the same idea. A hybrid's line mixes both jobs: its Stage I takes its trigger from
one parent and its payoff from the other, and a parent's MP or HP sustain carries over (a Green Mage who adds White
Mage keeps draining MP with every hit). Every one of the 144 base levels and all 66 hybrid lines have an
effect, built from statuses, HP and MP changes, damage scaling and charge pushback. Pushback has a budget: one foe
can be pushed back at most 25% of a turn between its own actions, or 12.5% for bosses, marks and rare game.

**Hybrid lines (0.1.37).** All 66 hybrid lines were rebuilt on one rule: a stage takes its trigger from one parent and
its payoff from the other, and no two stages share a trigger and a first effect. The rules the lines now keep:

- No Proficiency stage grants Libra. Pursuit stage II now stretches restarted ailment timers by 10%, and Contagion stage
  III strips Regen from the foes its spread reaches.
- Protect belongs to Knight, White Mage and Mystic parents, in one stage per line, for you or one ally. Only a line with
  a Knight parent gives the party Protect. Contagion II cures one ailment on each healed ally, and Cleave II-2 to II-4
  now Break the foe your hit lands on (the shared Broken state; Reckless is gone, see below).
- A line has at most one Shell stage. A line has at most one party-wide effect, at stage III.
- No Bravery from a stage that only spells or gun shots benefit, and no Faith for yourself on a job that does not cast.
- A Haste you give yourself from a frequent trigger applies only when you are not already Hasted.
- Stages on a hit you take say "physical hit": foes' spells and status never set them off. A parry or block that
  answers with a counter also counts as a parry or block, so Counter gear does not remove guard stages.
- "Big game" is a boss, a mark or a rare foe. "About to act" is a foe at 75% or more of its charge; the hook log counts how
  often it fires so play can tune it.
- A full Cadence streak counts as a counter for every line with a counter stage. A counter parries at 30% on any weapon
  but fists whose own evade is lower (a roll under 30 turns the blow aside; it is not guaranteed).

**Shared states (0.1.43 and 0.1.44).** The pillars (weapon identities, Proficiency, hybrids, armour and accessories)
talk to each other through a few states the party sets up on a foe: one pillar writes the state and another reads it,
so a fight is a team effort. Every payoff fires by itself, so gambit allies run it too. No state adds a status or a
new item.

- **Broken.** A foe is Broken for 10 seconds after a party member cuts its Defense or Magick Resist: any axe Sunder
  hit (a fresh dent, or, since 0.1.46, a hit on a foe already at the Sunder floor, which renews Broken without a deeper
  dent), the party's Expose or Shear, a spear hit by a Brave wielder, or a damage spell from a staff caster who has
  Faith. Lancer Grounding II (rank 5 and up) strips Protect or Shell from a Broken foe exactly as
  it does from an ailing one. Broken also counts wherever a line asks for "a foe with an ailment": Hunter Pursuit I and
  its Haste on a kill, every hybrid stage that reads "a foe with an ailment" (its text says "a foe ailing or
  Broken"; it counts as one ailment, so "2+ ailments" still needs real ones) and Hyper Wrist. It is not a status:
  Contagion's spread and Pursuit II's timer restarts ignore it, and so does the "Foe: any ailment" gambit, which
  reads only the game's own statuses. It lives in the script only, is forgotten on load, new game and map change, and
  belongs to one foe (a later foe at the same memory address does not inherit it).
- **Staggered.** A foe whose charge push-back budget for its current turn is used up (25%, or 12.5% for bosses, marks
  and rare game; the budget itself did not change) is Staggered until it acts. Sunder dents twice as deep on a
  Staggered foe (the floor stays at half), Hyper Wrist fires on a foe an ally Staggered, and three hybrid stages read it
  and no longer push on their own: Chaplain Last Rites I (heals the most hurt ally and adds damage), Artillerist
  Barrage I (returns MP) and Ki Gunner Hex Round I (a counter on a Staggered foe). Engineer Field Kit I read it too until
  2026-11-17 and no longer does (see "Field Kit pays on any ailment" below).
  Any turn a foe takes ends it, whatever the foe did: an attack that hit, missed or was evaded, a status or buff spell,
  a damage spell or a heal (0.1.45; before, an evaded attack or a status spell left a spent record to be read
  stale). The record belongs to one foe (its maxHp and level), so a later foe at the same memory address does not
  inherit it. An area attack or a multi-hit is one action: every target and every hit of it hears the state the action
  started with (Hex Round I on each), and the record ends with the action (0.1.46; before, only the first target
  heard it).
- **Brave and Faithful.** While the wielder holds Bravery, spear hits Break the foe. Hybrid Frenzy I and Havoc I
  now read "while Brave" instead of "below half HP", and Frenzy I-3 gives the most hurt ally Bravery, not the
  wielder. Faith is the caster's key: a staff damage spell cast with Faith Breaks the foe, and Spellblade Imbue II and
  Rune Knight Runeguard II fire on Faith or on their old trigger (an imbued blade; Lure from Knight lines), not both (0.1.45), and give
  their Faith grant to an ally.
- **Foes tire too.** The guard rule below applies to every foe a party member attacks with a melee, bow or crossbow
  roll: each attack it avoids in a row leaves its next evasion 25% lower, four in a row leave none, and a landed hit,
  ten seconds without being attacked, or an avoid against a Blinded attacker resets or skips it. Evasion is a delay,
  not a wall.
- **Unseen reads a held or tired foe.** A ninja sword's hits also ignore the dodge against a foe that is Slowed,
  Immobilized or Stopped, or has avoided two attacks in a row.
- **True aim interrupts.** A gun hit on a foe about to act (charge at 75% or more) pushes it back 10%, inside the
  shared budget.
- **Regen steadies the guard.** A party member holding Regen has a fresh guard five seconds after the last attack on
  them, not ten.
- **Party element rhythm.** Black Mage Resonance I also counts another member's hit or spell of the same element on
  that foe within the last 5 seconds, so an elemental archer or gunner opens the element and the Black Mage bursts it.
- **Magebreaker reads low MP.** Unmake I and III now read "a foe with a quarter of its MP or less" instead of "no MP
  left", which a boss never reaches. Mana Leech's Silence on a foe drained to 0 is unchanged.
- **Leech (2026-11-17, Proficiency 0.5.9).** Every MP drain against a foe already known to have 0 MP heals the drainer's HP
  instead, at the same share, and the MP economy does not change. It covers the base Green Mage's Mana Leech and every
  hybrid `drain` (the damage share: Foresight I, Hellfire II, Dirty Trick II, Prophecy I, Leech I, Quarry I, Malediction I,
  Ansatsu I, Transmute I and the later levels that carry one) and `drainFoe` (a counter or a hit taken takes a share of
  YOUR max MP from the attacker: Umbra I, Snare II levels 3-4, Abyss I levels 3-4, Last Stand I levels 3-4, now worded
  "leech 5% of your max MP from the foe"; the text used to say the foe's max MP, and the code never did). A weapon hit
  leeches at most 2% of your max HP, like the 2% MP cap; a spell has no cap, and a counter or a hit taken pays its share of
  your max HP. A foe counts as dry only with a real pool (max MP above 0) and 0 MP left; an unknown MP pays nothing, a combo follow-up pays nothing, and the Silence on a foe drained to
  0 needs MP to drain. The Clan Primer's Proficiency tip defines the word once.
- **A counter line hears a hit taken (Hell Knight, 2026-11-17).** Hellfire I now triggers on "a counter or a physical hit
  you take". A foe's multi-hit attack is one blow, for every struck and counter stage: a stage that pays its per-action rewards
  (HP, MP, statuses to you or allies, the counter drain) pays them on the first hit of the foe action on which its
  conditions held, and later hits of that action pay only the per-hit effects (strips, pushes). A hit on which the
  conditions failed holds nothing back, and a counter and a later hit of one action count once for a stage that hears
  both (Hellfire I). One foe action is the runtime's foe action record (the same record hook 240 keeps for Staggered, told
  apart by the foe's charge); without a record the same foe and action id within one second of the hit before.
  The other ten counter-only hybrid stages stay as they are until Marco has played Hellfire.
- **Entropy builds its own chain (2026-11-17).** Arithmetician Entropy I is now "repeating an element" (Slows the foe, rank
  1-4) and Entropy II "the same element twice on a Slowed foe" (the Stop chance, from rank 5). They were the other way
  round, so rank 4 needed a hand-cast Slow first.
- **Field Kit pays on any ailment (2026-11-17).** Engineer Field Kit I is "an ailment you land", with no Staggered gate:
  it heals the most hurt ally for 10% of the damage (15% at level 4), pushes the foe's charge back 10% from level 2 and
  cures an ally's ailment from level 3. The push now writes Staggered progress for the partners that read it.

*Play state: Stage I played to Clan rank 4, and the first hybrid Stage I lines have fired in play. Stages II and
III, and most of the 66 hybrid lines, are built but not yet confirmed in play. The shared states above are built and
covered by tests, and not yet played.*

## Weapons

**Damage formula.** Every hit keeps the game's attack minus defence, but never drops below a quarter of the attack.
Where attack minus defence is already larger, the hit is the game's own. Defense still matters in ordinary matchups;
it just can no longer erase a hit. A foe's hit on a party member keeps at least half of its attack, so armoured party
members feel real pressure. The floor reaches the hammer and hand-bomb formula too. **Glancing:** daggers and ninja
swords keep only an eighth of the attack against heavy armour, so they are the wrong answer to an armoured foe.

**A gentle attack curve.** The opening weapons keep their attack and every later weapon is lower than native. A
typical hit grows from about 47 at the start to about 830 by the last shops (native: about 1,375). Each weapon keeps
its place in its story stage. Rewards are set for where they're found. The 13 legendary weapons still lead their
families, and story and guest weapons are unchanged. A faster or more critical family hits a little softer, and a
slower one a little harder.

**Family identities.** Every weapon family has one style of play for any party member who wields it, and most pay a
price for it. It follows the weapon, while Proficiency follows the job. The Clan Primer's Traveler's Tips carries the
identities and their costs as four Weapon Families entries (Heavy, Light, Ranged, Magick). The entries also define
Broken and Staggered, say who loses the dodge to a ninja sword, and explain how foes (and allies) tire (0.1.45).
Broken counts as an ailment for job and hybrid triggers, not for gambits (0.1.46).

| Family | Identity |
|---|---|
| Sword | **Guard**: high evade on most swords (17-38, above the poles' 25-30 on average), so a sword parries often. It has no answer to armour or to a foe's evasion: among the lowest damage per second of the melee families |
| Greatsword | **Sweep**: Attack hits its target in full and every foe the half circle reaches for half of that hit (a landed hit never drops below 1); charge 40 and knockback 25, and most greatswords cannot hit flying foes (no flying icon; Nightmare II can). Against a lone armored foe a Sundered axe or a spear does more, so swapping for a boss is a real choice. Claymore and Ultima Blade are trimmed to 63 and 57 attack |
| Katana | **Chain**: the highest combo rate (30-45); a rolled combo runs 1 hit longer (the engine caps a combo at 11). Every combo hit meets the foe's full Defense, the evade sits below the sword's, and a katana leaves no hand for a shield |
| Ninja sword | **Unseen**: hits can't be parried or blocked (the dodge still applies, except against a Slowed, Immobilized or Stopped foe, or one that has avoided two attacks in a row); every blade carries Blind 10%, and five share that roll with a second status at 10% (Slow, Disable, Doom or Poison); **Glancing** against armour |
| Spear | **Pierce**: every hit ignores 30% of Defense, and while the wielder is Brave each hit also Breaks the foe; long reach. Most spears show evade 0 on the grid and so have no parry of their own (the Vrsabha keeps 40 and parries at it) |
| Pole | **Counterflow**: the wielder counters even without the Counter augment (Speed / 2; the Battle Harness and the Magepower Shishak carry it), twice as often with it; Counter Boost (Genji Armor, Black Belt) quadruples the chance, with the augment or without (Speed x 2 instead of Speed / 2); every blow taken without answering adds +10 to the next counter, up to 75 (100 with the augment); strikes Magick Resist. Its evade (25 at the third band, rising to 30 from the sixth) is what lets the counter swing |
| Bow | **Volley**: critical rate 20-30, the keenest of the ranged weapons |
| Crossbow | **Afflict**: the bolt's status chance is doubled (up to 100%); charge time 30, 24 on the Gastrophetes |
| Gun | **True aim**: a party gunner's shots never miss, and Blind does not spoil them; shots ignore Defense; Strength does not raise them, only the gun's own power; the slowest charge of any weapon (50); a hit on a foe about to act pushes its charge back 10% (inside the push budget) |
| Axe | **Sunder**: every hit dents the foe's Defense by 5% of its starting value for the rest of the fight (down to half; twice as deep on a Staggered foe), and every hit Breaks the foe, at the floor too (0.1.46). Swings land unevenly (native variance) and the axe barely parries (evade 6); swinging more never costs the wielder anything |
| Hammer | **Concussion**: Disable 15% on hit (20% on the Scorpion Tail), a 30% knockback chance; charge time 40 |
| Dagger | **Swift**: charge time 15, frequent combos, a little evade (more on the Main Gauche); **Glancing** against armour |
| Rod | **Mend**: healing spells heal +20% while it is held (potions unchanged). The weakest attack and the least Magick of the caster weapons; an overflowing heal costs the caster nothing |
| Staff | **Channel**: element potency and the most Magick of the caster weapons; a damage spell cast with Faith Breaks the foe |
| Mace | **Spellbreak**: hits burn foe MP equal to 10% of the damage (at most 10% of its pool) |
| Measure | **Tempo**: each measure grants one status to the ally it strikes (Protect, Shell, Bravery, Invisible, Haste or Bubble, by tier); the Gilt Measure is now sold from Amal's Weaponry in the third band, and a measure buff a Chemist lands can spread to a second ally (Contagion) |
| Hand-bomb | **Barrage**: the bombs you load decide the status, 25% on hit (Poison, Stop, Oil, Confuse or Sap; some carry none). Chaos Bombs (Confuse) and Stun Bombs (Stop) set up a Time Mage's Detonate |
| Unarmed | **Cadence**: each Attack in a row on the same target deals +6% more (up to +30%, five steps) and charges the next action 5% faster per step; switching targets or ten seconds of stillness restarts. A complete streak counts as one counter for Ki Flow and for every hybrid line with a counter stage, whichever two jobs the character holds |

There are four ways past armour: axes wear Defense down, spears ignore part of it, guns and measures ignore all of it,
and poles strike Magick Resist instead. Daggers and ninja swords have none, which is what Glancing costs them. Ammunition decides the element and status for bows,
crossbows, guns and hand-bombs.

**The guard itself tires.** Each attack a party member blocks, parries or dodges in a row leaves them 25% less
evasion against the next, so a streak of avoids ends in a clean hit; a landed hit, or ten seconds without being
attacked, restores the guard. The first roll against a fresh guard is always full, and a plain miss changes nothing.
An avoid against a Blinded attacker wears nothing down and resets nothing, so Blind keeps a tank's guard working
(0.1.43). A party member holding Regen has a fresh guard after five seconds instead of ten (0.1.44). Foes tire by
the same rule when a party member attacks them (0.1.44).

**Families have downsides, never punishments.** Marco's rule (2026-11-16): a downside is a fixed trade or a matchup that
the player answers with a hybrid job, an accessory, a partner or a weapon swap, and it never fires on the wielder's
success, grows with use or skill, or hurts the wielder. Braced (a sword parry pushed your next charge back), Open (a
katana combo left you unguarded), Reckless (axe swings made foes hit you harder), the rod's Lure (an overflowing heal
drew foes) and the spear's run-time parry zeroing (Long haft) were removed on that rule. What stays is on the grid or in the
matchup: slow charges (greatsword, hammer, crossbow, gun), low damage per second (sword), no shield (katana, spear),
no parry (spear), a hit that meets full Defense (katana), and a dagger or ninja sword that loses its edge against
armour (**Glancing**: never worse than the native formula). A foe's physical hit also keeps a floor by the body armour worn:
at least half of its attack in heavy armour, 60% in light and 70% in mystic, but never more than the same hit would deal with that piece taken off (an armour piece cannot make a hit bigger than wearing none). A pole's evade is back to 25-30 (by band) so that Counterflow can swing. Any weapon but fists that rolled a counter parries it at 30% when its own weapon evade is lower (a roll under 30 turns the blow aside and answers it; it is not a guaranteed parry). The crossbow (charge 30, the Gastrophetes 24)
and the hammer (charge 40, trimmed to about 1.05 of its band) are slower than before, and the hand-bomb's attack is
trimmed from the fifth band on.

**Shields choose a side.** The Flame Shield (evade 0, magick evade 15), the Diamond Shield (10 / 10) and the Dragon
Shield (0 / 20) now guard against magick instead of blows and keep their element wards; the other shields are
unchanged.

**Legendary weapons** stay the strongest of their families even at base power. Great Trango and Seitengrat now swing
at normal greatsword and bow speed, and Kumbha counts as a katana.

See [package_arsenal/README.md](../package_arsenal/README.md) for every weapon's numbers.

*Play state: the attack curve, the data identities and the greatsword sweep are played. The runtime identities fire
since 0.1.24: Spellbreak, Afflict, Marksman (replaced in 0.1.34 by True aim) and the pole counters (lent Counter
augment) are seen in play; Sunder, Unseen, Chain, Mend and Cadence's speed ramp have not yet been seen to fire. Guard
fatigue is in the campaign since 0.1.26-0.1.28 and has been measured once. The 0.1.34 weapons pillar (True aim,
Glancing, the spear's counter, the shield triangle and the weapon trims) is built and covered by tests, and not yet
played. The 2026-11-16 changes (the four punishments removed, the new greatsword sweep, Counter Boost on a pole,
Cadence through the shared charge queue) are offline: the sweep order of the aimed and swept foes, `initialTargetPointer`
at stage 165 and the Counter Boost field name each print one log line in game to confirm them.*

## Armour and accessories

**Armour rework.** 105 of the 140 armour and shield pieces gain one strategic property: an element ward or focus, or
a stat trade that costs something. Each one is chosen for what foes cast at that point in the story. The effects are
real item data, not description text.

- Defense, Magick Resist and augments never change.
- No new status immunities; those are reserved for future job armour lines.
- A stat gain always costs another stat. Shields trade evade for magick evade.
- Pieces of the same type, slot and story stage never share a property.

**The armour triangle.** Heavy armour defends hardest against weapons and weakest against magick; mystic armour is
the reverse; light armour sits between. 103 pieces carry their side of the trade: heavy body defence is 14% above
native and its helm magick resist 38% below; light stays a few points either side of native; mystic body defence
sits 38% below native and its helm magick resist 20% above.

**Accessory identities.** Accessories are each character's wildcard edge: offence, defence or utility. Their
identities draw on established overhauls where that adds a new answer, and never copy a job's signature passive.
Renamed accessories rename the copies you already own. Prices and locations are unchanged.

**Accessories that read the shared states.** Cameo Belt's Spotter marks a foe on a combo follow-up or a critical hit,
not on any hit (0.1.43): the next attack on it by another member that rolls a dodge cannot be dodged. Hyper Wrist's
Opportunist (0.1.44) quickens your next action by 10% when you hit a foe an ally afflicted, Broke or Staggered; the
wearer's own cuts do not count. Nishijin Belt keeps reading the element another member just used.

**House rules for accessories (2026-11-16).** A worn status counts everywhere the game asks for it: no accessory hides
its status from a job or hybrid (the Hermes and Pixie guards are gone). An accessory grants a status at most once per
fight, never refreshes it, and never grants Bravery. No accessory harms or debuffs its own wearer, whatever the role.
Five items changed on those rules: **Hermes Sandals** no longer wear Haste and instead give a real, full-length Haste at the
start of each fight (a fight is 30 seconds without touching a foe; skipped while already Hasted). **Pixie Shoes** keep
Float, which now counts for Highwind, and the wearer is weak to Wind. **Genji Gloves** are the vanilla Combo Boost again
(combo chance x 1.8 instead of x 0.7); the Double Edged recoil is gone. **Elmdore Fang** loses its auto-Sap and instead
heals the wearer 3% of max HP for each attack landed on a Sapped or Poisoned foe (once per attack; combo follow-ups do not
count); absorbing Dark and weakness to Holy stay. **Miracle Shoes** keep Protect and Shell and lose the Slow. **Ensanguined Shield** (2026-11-17) keeps evade 60 and no magick evade and loses its Equip Poison and Slow.

*Play state: armour text seen in game; the per-piece properties and the accessories are not yet measured in play.
The armour triangle is installed (0.1.31) and not yet measured.*

**Battle Harness (0.1.35).** The Battle Harness gives the Counter augment again, with Strength +2, and no longer
Safety. It is a second source of Counter beside the Magepower Shishak, which makes a spear's counter and the pole's twice-as-
often counter reachable. Genji Armor's and the Black Belt's Counter Boost quadruple the counter chance: with the Counter augment, or on a pole, which now takes
Speed x 2 instead of Speed / 2 as its base.

## Magicks, MP and Technicks

**Attack magicks on the weapon climb.** Spells kept their native power while weapons were lowered, so by the last
shops a spell hit 4 to 7 times as hard as a swing. Party attack spells from Aqua to Scathe, plus Holy and the
Dark line, now each have their own power, with the reason written beside it in `magicks.lua`. Fire, Thunder and Blizzard
stay native, as do area and targeting. Foes cast the same spells at full power, because the change only applies to the party's casts. The
balance between spells and weapons is still being calibrated from play.

**MP economy.** No spell can pay for itself.

- A party Magick costs what the game charges (Channeling, Parsimony, Spellspring and Offering apply as usual). The
  Magick-scaled surcharge of earlier builds is gone (2026-11-16): it grew with Magick, so it taxed build investment.
  `mp_economy.lua` keeps CAP at 1 so the co-op menu, which reads it, shows the game's own price.
- Everything one cast gives back as MP, to the caster or to allies, is capped at half of what that cast cost. MP
  drained from a foe's own pool and MP from items are not capped.
- MP that a weapon hit hands out from its damage (a drain, or MP shared to an ally) is limited to 2% of the receiver's max MP. Combo follow-ups on the same foe give no extra
  HP or MP, and item heals restore MP only through lines that say "items".
- Leech: against a foe with no MP left, the same drain heals HP instead, and a weapon hit leeches at most 2% of the wielder's
  max HP (see "Leech" under the shared states). The MP economy itself is untouched.
- Foes' spells and the listed cost in the game's tables stay native.

**Chain rewards.** Legacy ships its own settings for
[The Insurgent's Chain Benefits](https://www.nexusmods.com/finalfantasy12/mods/296), so the chain bonus works for a party
of four. At chain level 3 the Protect and Shell rewards go to the whole party instead of only the leader. The MP a chain
restores stays at 10% of max MP at every level, which keeps it inside the MP rules above.

**Technicks.** Expose and Shear now take 30% from a foe, like Wither and Addle (instead of 10%), so armoured and warded
foes have a real answer. The party's Decoy lasts about 45 seconds. All 24 Technicks were reviewed and three became
more reliable:

| Technick | Change |
|---|---|
| First Aid | Heals a critical ally for at least 10% max HP; better rolls still count |
| Horology | Deals at least level x 10 to each foe, so low clock readings no longer waste a turn |
| Achilles | Always gives an element the foe was not already weak to; a Gunner Technick since 0.1.35, so the matching shot follows at once |

The rest keep their identity.

*Play state: offline. The MP economy's cost and charge have been confirmed in the game log, not yet in a full play
session.*

## Enemies

**Foundation.** Every foe has its own copy of its data
([Instanced Foes](https://www.nexusmods.com/finalfantasy12/mods/528)), and
[Dynamic Bosses](https://www.nexusmods.com/finalfantasy12/mods/503), installed exactly as its author ships it, crowns
some ordinary foes with an element, an aura and a matching magick. Legacy's own enemy layer, built on Instanced Foes,
does the rest. Every foe it does not crown gets its own HP and MP roll (x1 to x1.5) and its own size (x0.95 to x1.10);
only ordinary foes also roll Vitality and Defense, while marks, bosses and Kings keep both as hand-made. A King hits at
least as hard as 1.25x the party average, and ordinary foes of one
species differ slightly in brightness (never in hue). A foe Dynamic Bosses crowned has its Strength, Magick and Attack
set to 1.25x the party average, and its AI keeps its one-shot rules: a spell is never inserted in front of a rule that
tests an AI flag. A data pass also raised foes' Strength, Magick, Speed and Attack without touching their HP or Defense.
The damage floor means no foe is ever unhurtable and no tank is ever untouchable.

Legacy ships Dynamic Bosses' settings file so that it only crowns ordinary foes, and its layer rolls a kind only when
Dynamic Bosses does not, so nothing is rolled twice. If you change those settings, Legacy follows them.

**Story-boss crown.** All 29 main-story bosses are crowned every time (Mod Config > Ivalice Legacy > Foes, on by
default). They get twice their own HP (times the Health option), and their Strength, Magick and Attack rise to 1.25x the
party's average where that is higher. They also get permanent Bravery and Faith, and their kind's tiered spell in their
AI. The crown's element never matches a native weakness. Reward: the boss's rarest steal always drops, and LP x3.
Optional bosses, marks and rare games take the ordinary roll instead. Foes in Trial Mode are left exactly as they are: no roll, crown, size change or tint.

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

**Monster family signatures.** Families fight in their own way. Rats go for the lowest Defense, and wounded Mimics
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
- **Gambits.** All 255 gambit names and help texts are reworded with clear element labels. Conditions are untouched,
  except for one row (0.1.45): the native "Foe: targeting Vaan" row, a gambit in Vaan's starting inventory, is now **Foe: any
  ailment**, which uses the game's own any-ailment condition on a foe (a status the game tracks, so Broken does not
  count). A save that already owns that gambit, or has it set, sees the new meaning. "Foe: targeting leader" is
  unchanged. From 0.1.60 Legacy ships its own gambit list, so Gambit Rework is not needed: every gambit costs 100 gil and
  the list is sorted into 15 pages by kind of choice. Gambits are stored by number, so check each character's gambits
  after loading an older save.
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
their own copies of their settings, so your normal game's settings stay as they were. Legacy also ships its own settings
for The Insurgent's Companions (the whole party in towns, guest equipment collected), Hunter's Insight (a foe's drop
names show only once you have obtained them) and Chain Benefits (see Chain rewards).

**Safety page.** Mod Config > Ivalice Legacy > Co-op > Safety warns when another mod's option breaks co-op: Vision's No
Combat Log removes the call the co-op HUD is drawn from, and Manifesto's No Auto Pause changes the part of the game that
co-op reads controllers from. While either is on, a warning row also appears on the first co-op page. **Restore Legacy
settings** (press it twice to confirm) switches off any developer override for co-op, and puts the Legacy values back
in the settings files of Vision, Manifesto, Companions and Hunter's Insight. Whether co-op is on, and your seats,
controls and HUD choices, are not changed, and the other mods' files apply when you restart the game. Nothing here opens a window.

*Play state: played.*
