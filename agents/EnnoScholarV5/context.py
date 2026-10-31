"""Current model views use the registry; archived scientific cards stay intact."""
import copy
from .reuse import load
from .evidence import proof_scope, active_article, usable_proof

base = load('_context_base', 'context.py')
for name in dir(base):
    if not name.startswith('_'):
        globals()[name] = getattr(base, name)

_article_projection = base.article_projection
_analysis_projection = base.analysis_projection


def analysis_projection(analysis, **kwargs):
    state = proof_scope.get()
    if state is None and kwargs.get('evidence') is not None and kwargs.get('snapshots') is not None:
        state = {'evidence': kwargs['evidence'], 'source_snapshots': kwargs['snapshots']}
    if state is not None:
        analysis = copy.deepcopy(analysis)
        for field, values in list(analysis.items()):
            if isinstance(values, list) and values and isinstance(values[0], dict) and 'statement' in values[0]:
                analysis[field] = [c for c in values if c.get('evidence_ids') and
                    all(usable_proof(state, eid) for eid in c['evidence_ids'])]
    return _analysis_projection(analysis, **kwargs)


def article_projection(article, *args, **kwargs):
    state = proof_scope.get()
    if state is not None:
        article = active_article(state, article)
    return _article_projection(article, *args, **kwargs)


base.article_projection = article_projection
base.analysis_projection = analysis_projection
