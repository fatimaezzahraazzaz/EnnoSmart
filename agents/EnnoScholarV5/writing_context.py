"""V4's whole-claim dossier, with request-local ranking by each source's role."""
from contextvars import ContextVar
import re
from .reuse import load

base = load('_writing_context_base', 'writing_context.py')
_roles = ContextVar('v5_section_claim_roles', default={})
_rank = base.rank_claims
_dossier = base.section_dossier


def rank_claims(claims, terms):
    ranked = _rank(claims, terms)
    roles = _roles.get()
    return sorted(ranked, key=lambda c: -len(roles.get(base.claim_key(c), set()) &
        set(re.findall(r'[a-zà-ÿ0-9]{5,}', str(c.get('statement', '')).casefold()))))


def section_dossier(state, toolkit, section, budget=7000):
    roles = {}
    for row in section.get('article_roles', []):
        article = toolkit.registry.get(row['article_id'])
        if article is None:
            continue
        terms = set(re.findall(r'[a-zà-ÿ0-9]{5,}', row.get('role', '').casefold()))
        for values in article.analysis.values():
            if isinstance(values, list):
                for claim in values:
                    if isinstance(claim, dict) and claim.get('statement'):
                        roles[base.claim_key(claim)] = terms
    token = _roles.set(roles)
    try:
        return _dossier(state, toolkit, section, budget)
    finally:
        _roles.reset(token)


# These are globals of a separately loaded V5 module, never the V4 module.
base.rank_claims = rank_claims
base.section_dossier = section_dossier
for name in dir(base):
    if not name.startswith('_'):
        globals()[name] = getattr(base, name)
