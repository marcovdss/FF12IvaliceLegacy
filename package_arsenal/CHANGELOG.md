# 0.5.0 candidate

- Weapon damage follows a gentler climb: the opening weapons are unchanged, and every later weapon has lower attack, so
  a typical hit grows from ~47 at the start to ~830 by the last shops instead of ~1,375. Each weapon keeps its place
  in its band (role, identity, tier); rewards are set for where they're found; the 13 legendary weapons still lead
  their families; story and guest weapons are unchanged. 157 weapons change.
- Still a test build: the Claymore keeps its 800-gil test price.
- Builds over an installed Arsenal 0.1.0 to 0.4.2.

# 0.4.2 candidate

- Test price: Claymore costs 800 gil (was 12,500) so the greatsword can be bought early for the Attack area
  experiment. Revert before any release that isn't a test.
- Builds over an installed Arsenal 0.1.0 to 0.4.1.

# 0.4.1 candidate

- Fix: Shemhazai summoned with permanent Protect and no Fire weakness. 0.4.0 gave Protect Gorget an effect block
  that is Shemhazai's own Esper gear; Protect Gorget now has a new block of its own and Shemhazai's is native again.
- The equipment table can grow: new effect blocks are appended after the native ones.

# 0.4.0 candidate

- Accessories: TPA identities merged where they add a new answer (23 renamed, 7 reworked under their name); no
  accessory copies a job's signature passive any more. Protect Gorget gets its own effect block (Auto-Protect).
- Equipment names: new word-table sub-table 1 (accessory names only).
- Builds over an installed Arsenal 0.1.0 to 0.3.1.

# 0.3.1 candidate

- Legendary weapons lead their family: Karkata 92 → 100; Great Trango 153 → 95 at normal sword speed (charge 25 → 32);
  Zodiac Spear 141 → 104; Seitengrat 224 → 90 at normal bow speed (charge 10 → 36).
- Kumbha is a katana (it already used the katana damage formula and model), so katana skills apply to it.
- Builds over an installed Arsenal 0.1.0, 0.2.0, 0.2.1 or 0.3.0.

# 0.3.0 candidate

- Effect identities: sword evade (20 → 40 by tier; poles and measures lowered), hammer Disable 15% and
  knockback 30, greatsword knockback 25, hand-bomb Slow/Oil 20%, ninja-sword Blind 10%, spear reach 25.
- Builds over an installed Arsenal 0.1.0, 0.2.0 or 0.2.1.

# 0.2.1 candidate

- Hornito, the Time Mage opening hand-bomb sold from the start, costs 500 gil (was 5,000; its attack is now 15).
- Builds over an installed Arsenal 0.1.0 or 0.2.0.

# 0.2.0 candidate

- Attack set per weapon for the story timing: Warrior axes and hammers and Time Mage hand-bombs brought into line,
  Longbow 48 → 35.
- Identities are damage-neutral: daggers, katana and bows hit softer for their speed, combo and critical rate;
  greatswords (charge time 35 → 40) hit harder.
- Katana combo, bow critical rate and greatsword charge time added.
- Builds over an installed Arsenal 0.1.0.

# 0.1.0 candidate

- Daggers: charge time 15, tiered combo rate and evade; Main Gauche keeps its parrying evade.
- Source-pinned equipment-table writer: every changed value is checked against the native value first.
