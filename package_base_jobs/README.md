# Ivalice Legacy: twelve base jobs candidate 0.5.0

Experimental offline build for the English Steam game, version 1.0.4.0.
Twelve custom boards, English job names and descriptions, the job stat trainings
(license prices, names, board help and a config for The Insurgent's Stat Lores),
six exclusive weapon-license LP price increases, four split weapon licenses and
their new names are included. No hooks, save redirection, equipment-stat changes
or Together files are included.
This candidate has not been validated in the game UI or in combat.

Boards are compact regions around Essentials: gear above, accessories to the
left, passive augments to the right and magicks/Technicks below, cheaper licenses
nearest the centre. Espers and Quickenings sit on the outer edge, each next to one
core license. Every board carries the same thirteen legendary islands: each
Esper gates the zodiac weapon of its sign (Belias Mesa, Mateus Mina, Adrammelech
Kumbha, Hashmal Simha, Cuchulainn Vrscika, Famfrit Makara, Zalera Mithuna,
Shemhazai Kanya, Chaos Vrsabha, Zeromus Dhanusha, Exodus Tula, Ultima Karkata)
and Zodiark gates Masamune. An island opens only for a character who owns its
Esper; play it with Summons For All so every character can license every Esper.
Mesa, Mina, Tula and Simha now stand alone: Yagyu Darkblade joins Ninja Swords 3,
Shikari Nagasa Daggers 6, Gastrophetes Crossbows 3 and Durandal Swords 8, and the
four licenses are renamed after their zodiac weapon. Ordinary job boards cost
4,276-5,376 LP; the islands add 2,140 LP. Every board has two Swiftness licenses;
both are reachable before half the ordinary total. Connected opening license
kits cost 70–145 LP. Acquisition still matters: licenses do not supply a weapon
or command. Measures need later shop placement; Hornito costs 5,000 gil.
Several signature commands are not opening shop purchases.

Basic tools are shared where another role needs them: Decoy and Oil for Knight and
Warrior, Cure and Blindna for Knight, Protect and Shell for White Mage, Vox and Poisona
for Chemist, Dispel and Stona for Lancer, Charge for Black and Time Mage, Libra for
Gunner, Green Mage and Mystic, First Aid for Warrior, Samurai and Hunter. Schools and
signature Technicks stay with their job.

**Job stat trainings.** Every job keeps the shared basics (HP Lore 1–10, Battle Lore 1–4, Magick Lore 1–4) and
holds one training of its own instead of the deep shared Battle and Magick Lores: two licenses (I at 50 LP, II at 100
LP; Warrior's are its HP Lore 11 and 12 at their native prices) that raise the stats its weapons and role use. The
package carries a config for The Insurgent's Stat Lores (required) that gives these licenses their effects; let it win
over Stat Lores' own config. Totals stay close to the base game's; what changes is which stats grow.

| Job | Training | I | II |
|---|---|---|---|
| Knight | Bulwark | Defence +3, shield evasion +5 | Defence +4, magick shield evasion +5 |
| Lancer | Dragoon | Strength +3, Magick Resist +3 | Strength +4, Magick Resist +4 |
| Warrior | Juggernaut | HP +435, Strength +2, Vitality +4 | HP +500, Strength +3, Vitality +6 |
| Samurai | Bushido | Strength +2, MP +40 | Strength +2, Magick +1, MP +60 |
| Hunter | Fleetfoot | Strength +2, Speed +2 | Strength +3, Speed +3 |
| Gunner | Deadeye | Speed +2, Attack +1 | Speed +2, Attack +1 |
| Chemist | Field Medic | Vitality +3, Magick Resist +3, Strength +2 | Vitality +4, Magick Resist +4, Strength +2 |
| Green Mage | Hexcraft | Magick +5, MP +30 | Magick +7, MP +50 |
| Time Mage | Chronomancy | Magick +2, Speed +2 | Magick +4, Speed +2 |
| Mystic | Mysticism | Strength +2, Magick +2 | Strength +2, Magick +2 |
| White Mage | Devotion | Magick +4, Magick Resist +3 | Magick +5, Magick Resist +4 |
| Black Mage | Sorcery | Magick +6 | Magick +8 |

Magick Lore 13 keeps its effect and leaves the boards (guests still carry it); Magick Lore 16 is unused. Boards hold
171 stat tiles instead of 223.

Gunner uses light armor and guns. Chemist has crossbows.
Excalibur belongs to Warrior. Native command bundles
and item/action records are unchanged. The license-table override changes only
LP bytes (Guns 2–6, Crossbows 3 and the training prices), sixteen content bytes in eight licenses and license
names (the four split weapon licenses and the 24 trainings, a separate word-table file). The manifest lists each
before/after price. Previously owned licenses are never charged again.

## Saved slot identities

Existing job slot IDs remain unchanged. Their proposed new curricula are:

| Original slot | Candidate job |
|---|---|
| White Mage | White Mage |
| Uhlan | Lancer |
| Machinist | Gunner |
| Red Battlemage | Green Mage |
| Knight | Knight |
| Monk | Mystic |
| Time Battlemage | Time Mage |
| Foebreaker | Warrior |
| Archer | Hunter |
| Black Mage | Black Mage |
| Bushi | Samurai |
| Shikari | Chemist |

Already owned licenses remain owned, including licenses outside the new board.
No automatic respec, job reassignment or save migration is performed. Spell and
Technick inventory acquisition still applies. Native Quickening, Esper and
second-job restrictions remain in place; only island licenses are gated by them.
Islands behind Espers or Quickenings a character already owns open at once.
Base boards retain the native two-board interface. No hybrid class is enabled.

## Installation and rollback

This is a review candidate, not an instruction to deploy into an ongoing campaign.
Before an approved installation, inventory the exact deployed winners and back up
every existing target with SHA-256 hashes. Use the mod manager's supported
installation/deployment path. Do not stack this with another board or English
menu-command/menu-message or license-table replacement, or with the experimental
Paladin pilot. Other mods replacing battle-pack section 12 require review;
separate section overrides such as shop section 39 remain independent.

For rollback, disable/remove this candidate through the same manager and restore
the previous winners from that installation's backups. Delete a candidate-owned
file only if its current hash still matches this package; preserve changed or
unknown files. The included manifest lists payload hashes and excludes its own
hash. Removing these files restores prior layouts/labels; it does not undo LP
spending or licenses acquired in a save while the candidate was active. Rollback
must restore the prior section 12 provider as well as board and text resources.

Ivalice Together's files and settings must remain unchanged. Native labels are
patched for English only; compatibility of any separate co-op job-name display
must be checked independently.
