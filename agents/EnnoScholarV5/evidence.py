"""V5 proof lifecycle over unchanged source snapshots; history is never deleted."""
import copy
from contextvars import ContextVar
from .reuse import load

base = load('_evidence_base', 'evidence.py')
for name in dir(base):
    if not name.startswith('_'):
        globals()[name] = getattr(base, name)

source_integrity_valid = base.exact_source_valid
_reusable_science = base.reusable_science
proof_scope = ContextVar('v5_proof_authority', default=None)


def exact_source_valid(proof, snapshots):
    # Legacy records without a lifecycle field retain their existing contract.
    if proof.get('lifecycle_status', 'usable') != 'usable':
        return False
    try:
        return source_integrity_valid(proof, snapshots)
    except (KeyError, TypeError, IndexError):
        return False


def proof_status(state, eid):
    proof = state.get('evidence', {}).get(eid)
    if not proof:
        event = next((e for e in reversed(state.get('evidence_lifecycle_history', []))
            if e.get('evidence_id') == eid), None)
        if event and event.get('status') != 'usable':
            return event['status']
        return 'missing'
    status = proof.get('lifecycle_status', 'usable')
    if status != 'usable':
        return status
    return 'usable' if exact_source_valid(proof, state.get('source_snapshots', {})) else 'invalid'


def usable_proof(state, eid, *, article_id=None, presented=False):
    proof = state.get('evidence', {}).get(eid)
    return bool(proof and proof_status(state, eid) == 'usable'
        and (article_id is None or proof.get('article_id') == article_id)
        and (not presented or eid in state.get('shown_evidence_ids', [])))


def set_proof_status(state, eid, status, *, reason='', replacement=None):
    if status not in {'usable', 'withdrawn', 'replaced', 'historical'}:
        raise ValueError('Unknown proof lifecycle status')
    proof = state.get('evidence', {}).get(eid)
    if proof is None:
        raise ValueError('Unknown proof')
    if status == 'replaced' and (replacement == eid or not usable_proof(state, replacement,
            article_id=proof['article_id'])):
        raise ValueError('A replacement must be a usable proof of the same article')
    if status == 'usable' and not source_integrity_valid(proof, state.get('source_snapshots', {})):
        raise ValueError('An invalid source cannot be made usable')
    event = {'evidence_id': eid, 'previous_status': proof.get('lifecycle_status', 'usable'),
        'status': status, 'reason': reason, 'replacement': replacement}
    if event['previous_status'] == status and proof.get('replacement_evidence_id') == replacement:
        return
    state.setdefault('evidence_lifecycle_history', []).append(event)
    proof['lifecycle_status'] = status
    proof['replacement_evidence_id'] = replacement


def active_article(state, article):
    """Read-only current view. Embedded copies are historical, never authority."""
    result = article.model_copy(deep=True)
    if not article.analysis:
        return result
    for field, value in list(result.analysis.items()):
        if isinstance(value, list) and value and isinstance(value[0], dict) and 'statement' in value[0]:
            result.analysis[field] = [claim for claim in value if claim.get('evidence_ids') and
                all(usable_proof(state, eid, article_id=article.id) for eid in claim['evidence_ids'])]
    result.analysis['evidence'] = [copy.deepcopy(state['evidence'][p['evidence_id']])
        for p in article.analysis.get('evidence', [])
        if usable_proof(state, p['evidence_id'], article_id=article.id)]
    if not any(isinstance(v, list) and any(isinstance(c, dict) and c.get('statement') for c in v)
            for v in result.analysis.values()):
        result.analysis = {}
        result.analysis_basis = None
        result.access_status = 'fulltext_extracted' if result.fulltext.get('text') else 'abstract_only'
    return result


def reusable_science(article, snapshots, evidence=None):
    if evidence is not None:
        # A stale embedded copy cannot resurrect a missing/withdrawn registry entry.
        article = active_article({'evidence': evidence, 'source_snapshots': snapshots}, article)
    result = _reusable_science(article, snapshots, evidence)
    if result and isinstance(evidence, dict):
        data, sources, proofs = result
        state = {'evidence': evidence, 'source_snapshots': snapshots}
        for eid, proof in evidence.items():
            if proof.get('article_id') == article.id and proof_status(state, eid) != 'usable':
                # Transfer withdrawal/invalidation markers, never delivery credit.
                marker = copy.deepcopy(proof)
                marker['lifecycle_status'] = proof_status(state, eid)
                proofs[eid] = marker
                fp = proof.get('source_fingerprint')
                if fp in snapshots:
                    sources[fp] = copy.deepcopy(snapshots[fp])
    return result


base.exact_source_valid = exact_source_valid


# --- N05: identity of an unchanged passage when the same PDF's extraction grows --------------
# V4 IDs hash the whole extracted snapshot, so adding pages renamed every passage. IDs of an
# extraction that never grew stay the V4 IDs. When the saved extraction of the same verified
# document (same SHA-256) changes, a passage found again at the same page, page offset and
# quotation keeps its historical ID (fulltext["evidence_aliases"]: computed ID -> historical
# ID); its registry entry is pointed at the current snapshot, the previous entry is archived
# in state["evidence_versions"]. A changed document or passage gets a new ID, as before.
_base_passages = base.passages


def passages(article):
    rows = _base_passages(article)
    aliases = (article.fulltext or {}).get('evidence_aliases') or {}
    if not aliases:
        return rows
    for row in rows:
        historical = aliases.get(row['evidence_id'])
        if historical:
            row['evidence_id'] = historical
    return rows


base.passages = passages


def _physical_key(row, chunks):
    chunk = chunks[row['chunk_index']] if row.get('chunk_index') is not None and row['chunk_index'] < len(chunks) else {}
    return (row.get('page'), int(chunk.get('page_char_offset') or 0) + int(row.get('char_offset') or 0),
        row['exact_quote'], row.get('table_row_index'), row.get('section', ''))


def _paged_document(fulltext):
    return bool(fulltext.get('text') and (fulltext.get('document_version') or {}).get('document_sha256')
        and any(c.get('page') is not None for c in fulltext.get('chunks', [])))


def carry_passage_identities(state, previous, article):
    """Keep historical IDs of unchanged passages after the same document's extraction changed.
    Returns the number of historical IDs carried; never deletes a proof."""
    current = article.fulltext
    if not (previous and _paged_document(previous) and _paged_document(current)):
        return 0
    if previous['document_version']['document_sha256'] != current['document_version']['document_sha256']:
        return 0  # another document: no equivalence
    if (previous.get('text') == current.get('text') and len(previous.get('chunks', [])) == len(current.get('chunks', []))
            or previous.get('chunks') == current.get('chunks')):
        return 0
    old_view = article.model_copy(deep=True)
    old_view.fulltext = copy.deepcopy(previous)
    old_rows = {_physical_key(r, previous['chunks']): r for r in passages(old_view)}
    fresh = article.model_copy(deep=True)
    fresh.fulltext = {k: v for k, v in copy.deepcopy(current).items() if k != 'evidence_aliases'}
    registry = state.setdefault('evidence', {})
    aliases, current_source = {}, None
    for row in _base_passages(fresh):
        old = old_rows.get(_physical_key(row, current['chunks']))
        if old is None or old['evidence_id'] not in registry or old['evidence_id'] == row['evidence_id']:
            continue
        historical = old['evidence_id']
        aliases[row['evidence_id']] = historical
        entry = registry[historical]
        if entry.get('lifecycle_status', 'usable') != 'usable':
            continue  # a withdrawn/replaced proof keeps its status and content
        if current_source is None:
            current_source = snapshot(fresh)
            state.setdefault('source_snapshots', {})[current_source['fingerprint']] = current_source
        if entry.get('source_fingerprint') == row['source_fingerprint']:
            continue
        state.setdefault('evidence_versions', {}).setdefault(historical, []).append(copy.deepcopy(entry))
        registry[historical] = {**row, 'evidence_id': historical}
    if aliases:
        current['evidence_aliases'] = aliases
        version = current['document_version'].get('version_id')
        if version and version in article.documents:
            article.documents[version]['evidence_aliases'] = copy.deepcopy(aliases)
    return len(aliases)
