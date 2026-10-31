"""Bounded views of exact material received in the current request, never new science.

Only identifiers are recorded in request knowledge. Quotes and provenance remain
in the authoritative evidence/snapshot stores; invalid or historical-only reads
cannot leak into a new request's context or safety-stop answer.
"""
import json


def evidence_ids(state):
    from .instructions import current_request
    record = state.get('v5_request_knowledge') or {}
    if record.get('request_id') != (current_request(state) or {}).get('id'):
        return []
    known = {k.split(':', 1)[1] for k in record.get('keys', []) if k.startswith('passage:')}
    known.update(record.get('consulted_evidence_ids', []))
    ordered = [e for e in record.get('passage_order', []) if e in known]
    ordered.extend(sorted(known-set(ordered)))
    from .evidence import exact_source_valid
    shown = set(state.get('shown_evidence_ids', []))
    return [eid for eid in dict.fromkeys(ordered) if eid in shown
        and (e := state.get('evidence', {}).get(eid))
        and exact_source_valid(e, state.get('source_snapshots', {}))]


def packet(state, budget, *, exclude=()):
    """Whole projections only. Missing room is explicit, never an absent-source claim."""
    from .original import evidence_projection
    ids = [eid for eid in reversed(evidence_ids(state)) if eid not in set(exclude)]
    if not ids:
        return None
    # Most recent received window first, but retain the selector's order inside
    # each window. Reversing every passage put incidental rows ahead of the
    # first targeted hit, causing useful table cells to be evicted and reread.
    known = set(ids)
    ranked = [eid for window in reversed(state.get('reading_windows', []))
        if window.get('status') == 'presented' for eid in window.get('evidence_ids', []) if eid in known]
    ids = list(dict.fromkeys([*ranked, *ids]))
    result = {'kind': 'current_request_consulted_material', 'evidence': [],
        'available_total': len(ids), 'omitted_total': len(ids), 'omitted_evidence_ids': [],
        'use': 'Passages déjà reçus : les réutiliser sans recherche ni relecture. '
            'Une omission de ce contexte borné ne signifie pas une source absente ; '
            'les passages restent accessibles par evidence_ids avec revisit=true.'}
    size = lambda: len(json.dumps(result, ensure_ascii=False))
    if size() > budget:
        return None
    # Latest explicit reads first, then a round-robin across articles. No table,
    # numeric or figure heuristic is allowed to erase another scientific need.
    groups = {}
    for eid in ids:
        aid = state['evidence'][eid]['article_id']
        groups.setdefault(aid, []).append(eid)
    ordered = [rows[i] for i in range(max(map(len, groups.values())))
        for rows in groups.values() if i < len(rows)]
    kept = set()
    for eid in ordered:
        result['evidence'].append(evidence_projection(state['evidence'][eid], state.get('source_snapshots', {})))
        if size() > budget:
            result['evidence'].pop()
        else:
            kept.add(eid)
    result['omitted_total'] = len(ids)-len(kept)
    for eid in ids:
        if eid not in kept and len(result['omitted_evidence_ids']) < 8:
            result['omitted_evidence_ids'].append(eid)
            if size() > budget:
                result['omitted_evidence_ids'].pop()
    return result


def partial_response(state, budget=12000):
    """Factual safety-stop restitution; no comparison, inference or extra API call."""
    value = packet(state, budget)
    if not value or not value['evidence']:
        return {}
    from .models import public_link
    answer = ['Passages sources consultés pendant cette exécution (restitution documentaire partielle, sans nouvelle analyse) :']
    citations = []
    for e in value['evidence']:
        article = state.get('articles', {}).get(e['article_id'], {})
        where = e.get('section') or 'Passage source'
        if e.get('page_number') is not None:
            where += ', page PDF '+str(e['page_number'])
        url = e.get('source_url') or ''
        source = f'[{where}]({url})' if public_link(url) else where
        answer.append('**'+article.get('title', e['article_id']).replace('*', '')+'** — '+source)
        if e.get('table_structure_status') == 'ambiguous':
            answer.append('Réserve documentaire : association des cellules et colonnes non vérifiée.')
        answer.append('\n'.join('> '+line for line in e['exact_quote'].splitlines()))
        citations.append({'article_id': e['article_id'], 'evidence_id': e['evidence_id'],
            'title': article.get('title', where), 'url': url, 'page_number': e.get('page_number')})
    if value['omitted_total']:
        answer.append(f"{value['omitted_total']} autres passages consultés restent enregistrés et accessibles par identifiant.")
    return {'answer': '\n\n'.join(answer), 'citations': citations, 'partial': True,
        'coverage_sufficient': False, 'cited_evidence_ids': [e['evidence_id'] for e in value['evidence']],
        'remaining_uncertainties': ['Restitution des seuls passages consultés ; travail demandé non achevé.']}


def graphic_urls(toolkit, ids):
    """Only literal references from cited, presented, valid figure source chunks."""
    from urllib.parse import urljoin
    from .evidence import exact_source_valid
    from .models import public_link
    found = set()
    for eid in ids:
        e = toolkit.evidence.get(eid)
        if not e or eid not in toolkit.shown_evidence or not exact_source_valid(e, toolkit.snapshots):
            continue
        source = toolkit.snapshots.get(e.get('source_fingerprint'), {})
        if source.get('article_id') != e['article_id']:
            continue
        for chunk in source.get('chunks', []):
            figure = chunk.get('figure') or {}
            if not figure:
                continue
            if ' '.join(e['exact_quote'].split()) not in ' '.join(chunk.get('text', '').split()):
                continue
            for literal in figure.get('graphic_refs', []):
                url = urljoin(e.get('source_url') or '', literal)
                if public_link(url) and (literal in e['exact_quote'] or url in e['exact_quote']):
                    found.add(url)
    return found
