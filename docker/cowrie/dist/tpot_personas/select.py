"""COWRIE_PERSONA: empty is the whole pool, otherwise persona IDs separated by
whitespace (the choice_list of env.schema.yml: no alias but the earlier IDs of its
legacy, ALIASES). The IDs are path parts, so only catalog IDs pass."""
from . import PersonaError

# the earlier IDs of renamed personas, as the legacy of COWRIE_PERSONA in env.schema.yml: an existing
#   .env keeps its selection
ALIASES = {"tplink-wr841n": "tplink-archer-c7"}


def parse_selection(value, ids):
    entries = (value or "").split()
    if not entries:
        return list(ids)
    unknown = [e for e in entries if e not in ids and ALIASES.get(e) not in ids]
    if unknown:
        raise PersonaError(f'unknown COWRIE_PERSONA "{unknown[0]}", allowed: {" ".join(ids)}')
    twice = [e for i, e in enumerate(entries) if e in entries[:i]]
    if twice:
        raise PersonaError(f'COWRIE_PERSONA names "{twice[0]}" twice, allowed: {" ".join(ids)}')
    # an earlier ID is the persona of its new one (once, also next to the new one)
    return list(dict.fromkeys(e if e in ids else ALIASES[e] for e in entries))


def choose(value, ids, rng):
    return rng.choice(parse_selection(value, ids))
