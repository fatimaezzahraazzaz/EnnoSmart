"""V5 dependency checks; unchanged validators and bounded packet format reused."""
import copy
from types import SimpleNamespace
from .reuse import load
from .evidence import usable_proof, active_article

base = load('_argumentation_base', 'argumentation.py')
for name in dir(base):
    if not name.startswith('_'):
        globals()[name] = getattr(base, name)

_relation_current = base.relation_current
_argument_packet = base.argument_packet
_article_reading_status = base.article_reading_status
_source_signature = base.source_signature


def relation_current(toolkit, relation):
    if not _relation_current(toolkit, relation):
        return False
    state = {'evidence': toolkit.evidence, 'source_snapshots': toolkit.snapshots}
    allowed = set(relation['article_ids'])
    return all(usable_proof(state, eid) and toolkit.evidence[eid]['article_id'] in allowed
        for claim in relation.get('experimental_results', []) for eid in claim.get('evidence_ids', []))


def article_reading_status(state, article):
    return _article_reading_status(state, active_article(state, article))


def gap_current(state, bundle):
    from .comparison_memory import identity
    if bundle.get('proof_identity') != identity(state, bundle.get('article_ids') or []):
        return False
    return all(usable_proof(state, eid) and state['evidence'][eid]['article_id'] in gap.get('article_ids', [])
        for gap in bundle.get('gaps', []) for eid in gap.get('evidence_ids', []))


def argument_packet(state, toolkit, ids=None, **kwargs):
    # Filter before applying a cursor, so its positions count the same valid
    # claims as bounded_argument_packet. The canonical articles stay untouched.
    view = copy.copy(toolkit)
    view.registry = {aid: active_article(state, a) for aid, a in toolkit.registry.items()}
    packet = _argument_packet(state, view, ids, **kwargs)
    for paper in packet.get('papers', []):
        original = toolkit.registry[paper['id']]
        current = view.registry[paper['id']]
        from .context import ANALYSIS_FIELDS
        def count(article):
            return sum(isinstance(c, dict) and bool(c.get('statement'))
                for field in ANALYSIS_FIELDS if isinstance(article.analysis.get(field), list)
                for c in article.analysis[field])
        unavailable = count(original)-count(current)
        paper['unavailable_claims'] += max(0, unavailable)
    packets = packet.get('research_gaps', [])
    packet['research_gaps'] = [g for g in packets if gap_current(state, g)]
    packet['historical_gap_bundles'] = len(packets)-len(packet['research_gaps'])
    return packet


base.relation_current = relation_current
base.article_reading_status = article_reading_status


def source_signature(state, registry):
    from .task_results import fingerprint
    signature = _source_signature(state, registry)
    ids = {eid for a in registry.values() for values in a.analysis.values() if isinstance(values, list)
        for c in values if isinstance(c, dict) and c.get('statement') for eid in c.get('evidence_ids', [])}
    ids.update(eid for r in state.get('scientific_relations', []) for c in r.get('experimental_results', [])
        for eid in c.get('evidence_ids', []))
    ids.update(eid for r in state.get('scientific_relations', []) for eid in r.get('evidence_ids', []))
    ids.update(eid for bundle in state.get('research_gaps', []) for gap in bundle.get('gaps', [])
        for eid in gap.get('evidence_ids', []))
    unusable = sorted(eid for eid in ids if not usable_proof(state, eid))
    return fingerprint([signature, unusable]) if unusable else signature


base.source_signature = source_signature


def relation_key(relation):
    """Literal scientific content; only unordered collections are canonicalised."""
    from .task_results import fingerprint
    def canonical(value, key=None):
        if isinstance(value, dict):
            return {k: canonical(v, k) for k, v in value.items()
                if k not in {'id', 'source_signature', 'stale', 'comparison_partial'}}
        if isinstance(value, list):
            rows = [canonical(v) for v in value]
            if key in {'article_ids', 'evidence_ids', 'experimental_results', 'uncertainties'}:
                rows = sorted({fingerprint(v): v for v in rows}.values(), key=fingerprint)
            return rows
        return value
    return fingerprint(canonical(relation))
