"""COWRIE_PERSONA: empty (or "random") is the whole pool, otherwise persona IDs
separated by whitespace. The IDs are path parts, so only catalog IDs pass."""
from . import PersonaError


def parse_selection(value, ids):
    entries = (value or "").split()
    if not entries or entries == ["random"]:
        return list(ids)
    unknown = [e for e in entries if e not in ids]
    if unknown:
        raise PersonaError(f'unknown COWRIE_PERSONA "{unknown[0]}", allowed: {" ".join(ids)}')
    if len(set(entries)) != len(entries):
        raise PersonaError("COWRIE_PERSONA names a persona twice")
    return entries


def choose(value, ids, rng):
    return rng.choice(parse_selection(value, ids))
