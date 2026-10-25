"""Offline Clan progression contract. No game hooks, stats, payments or save I/O.

Callers supply the current campaign's ledger on every load (None means empty).
Fixture rules are deliberately kept out of the shipped catalogue.
"""
from dataclasses import dataclass
import re


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


@dataclass(frozen=True, order=True)
class ClassIdentity:
    jobs: tuple[int, ...]

    def __post_init__(self):
        if (type(self.jobs) is not tuple or len(self.jobs) not in (1, 2)
                or not all(_integer(j, 0, 11) for j in self.jobs)
                or tuple(sorted(set(self.jobs))) != self.jobs):
            raise ValueError('Expected one job or two distinct canonical job IDs')

    @classmethod
    def from_jobs(cls, first, second=None):
        jobs = (first,) if second is None or type(second) is int and second == 255 else (first, second)
        if not all(_integer(j, 0, 11) for j in jobs) or len(set(jobs)) != len(jobs):
            raise ValueError('Invalid native job IDs')
        return cls(tuple(sorted(jobs)))

    @property
    def key(self):
        return ':'.join(map(str, self.jobs))


def _class_key(key):
    if not isinstance(key, str) or not re.fullmatch(r'(?:[0-9]|1[01])(?::(?:[0-9]|1[01]))?', key):
        raise ValueError('Invalid class key')
    result = ClassIdentity(tuple(map(int, key.split(':'))))
    if result.key != key:
        raise ValueError('Noncanonical class key')
    return result


def _rule_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{0,95}', value):
        raise ValueError('Invalid rule ID')
    return value


@dataclass(frozen=True)
class Gear:
    slot: str
    kind: str
    item_id: int

    @property
    def trainable(self):
        return (_integer(self.item_id, 0, 65534)
                and (self.slot, self.kind) in {
                    ('weapon', 'weapon'), ('offhand', 'shield'),
                    ('head', 'armor'), ('body', 'armor')})


@dataclass(frozen=True)
class Rule:
    rule_id: str
    identity: ClassIdentity
    slot: str
    item_ids: frozenset[int]
    minimum_rank: int = 0
    quests: frozenset[str] = frozenset()

    def __post_init__(self):
        _rule_id(self.rule_id)
        if (not isinstance(self.identity, ClassIdentity)
                or self.slot not in {'weapon', 'offhand', 'head', 'body'}
                or type(self.item_ids) is not frozenset or not self.item_ids
                or not all(_integer(i, 0, 65534) for i in self.item_ids)
                or not _integer(self.minimum_rank, 0, 11)
                or type(self.quests) is not frozenset
                or not all(isinstance(q, str) and q for q in self.quests)):
            raise ValueError('Invalid Clan rule')

    def available(self, rank, completed_quests):
        """Unlock eligibility only; this does not purchase or revoke ownership."""
        return (_integer(rank, 0, 11) and rank >= self.minimum_rank
                and self.quests.issubset(completed_quests))


@dataclass(frozen=True)
class Ledger:
    # Immutable entries: stable main-character ID, full class, owned rule IDs.
    entries: tuple = ()

    def __post_init__(self):
        if type(self.entries) is not tuple:
            raise ValueError('Expected immutable ledger entries')
        seen = set()
        for entry in self.entries:
            if type(entry) is not tuple or len(entry) != 3:
                raise ValueError('Expected immutable three-part ledger entry')
            actor, identity, owned = entry
            if (not _integer(actor, 0, 5) or not isinstance(identity, ClassIdentity)
                    or type(owned) is not frozenset or not owned
                    or (actor, identity) in seen):
                raise ValueError('Invalid or duplicate ledger entry')
            for rule_id in owned:
                _rule_id(rule_id)
            seen.add((actor, identity))

    @classmethod
    def from_document(cls, document):
        """Parse a supplied document; never retain a previous campaign's data."""
        if document is None:
            return cls()
        if (type(document) is not dict or set(document) != {'schema', 'characters'}
                or type(document['schema']) is not int or document['schema'] != 1
                or type(document['characters']) is not dict):
            raise ValueError('Invalid Clan ledger schema')
        entries = []
        for actor, classes in document['characters'].items():
            if actor not in tuple(map(str, range(6))) or type(classes) is not dict:
                raise ValueError('Invalid character ledger')
            for key, owned in classes.items():
                if type(owned) is not list or not all(isinstance(r, str) for r in owned):
                    raise ValueError('Expected owned rule IDs')
                if len(set(owned)) != len(owned):
                    raise ValueError('Duplicate owned rule')
                entries.append((int(actor), _class_key(key), frozenset(owned)))
        return cls(tuple(entries))

    def to_document(self):
        characters = {}
        for actor, identity, owned in sorted(self.entries):
            characters.setdefault(str(actor), {})[identity.key] = sorted(owned)
        return {'schema': 1, 'characters': characters}

    def owned(self, actor, identity):
        return next((owned for a, c, owned in self.entries
                     if a == actor and c == identity), frozenset())


def resolve(ledger, actor, identity, equipment, rules):
    """Return applicable owned rule IDs, without applying any numerical effect.

    Unknown owned IDs remain in the ledger but confer nothing. All catalogue
    IDs must be unique. Native selected-board state is intentionally no input.
    """
    rules = tuple(rules)
    if len({r.rule_id for r in rules}) != len(rules):
        raise ValueError('Duplicate catalogue rule ID')
    equipment = tuple(equipment)
    if len({g.slot for g in equipment}) != len(equipment):
        raise ValueError('Duplicate equipment slot')
    if not _integer(actor, 0, 5) or not isinstance(identity, ClassIdentity):
        return frozenset()
    owned = ledger.owned(actor, identity)
    return frozenset(r.rule_id for r in rules
                     if r.rule_id in owned and r.identity == identity
                     and any(g.trainable and g.slot == r.slot
                             and g.item_id in r.item_ids for g in equipment))
