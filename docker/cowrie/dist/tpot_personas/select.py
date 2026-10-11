"""COWRIE_PERSONA: empty is the whole pool, otherwise persona IDs separated by
whitespace (the choice_list of env.schema.yml: no alias). The IDs are path parts,
so only catalog IDs pass."""
from . import PersonaError


def parse_selection(value, ids):
    entries = (value or "").split()
    if not entries:
        return list(ids)
    unknown = [e for e in entries if e not in ids]
    if unknown:
        raise PersonaError(f'unknown COWRIE_PERSONA "{unknown[0]}", allowed: {" ".join(ids)}')
    twice = [e for i, e in enumerate(entries) if e in entries[:i]]
    if twice:
        raise PersonaError(f'COWRIE_PERSONA names "{twice[0]}" twice, allowed: {" ".join(ids)}')
    return entries


def choose(value, ids, rng):
    return rng.choice(parse_selection(value, ids))
