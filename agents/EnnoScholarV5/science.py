"""Request-scoped science over the existing V4 reading ledger and exact proofs.

No planner or model lives here. Sol chooses its questions, passages, reviews and
relations. Deterministic checks only describe receipts and validate provenance.
"""
from __future__ import annotations

import copy
import re
from collections import OrderedDict
from threading import RLock
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from .evidence import exact_source_valid, passages, snapshot, active_article, usable_proof
from .models import Article
from .research import DimensionReview as BaseDimensionReview
from .task_results import fingerprint

DIMENSIONS = {
    'problem': ('problem', 'objective'), 'contribution': ('contribution',),
    'methods': ('method',), 'mechanisms': ('technical_mechanism', 'equations'),
    'experimental_protocol': ('experimental_setup', 'validation_protocol', 'datasets_materials'),
    'parameters': ('parameters',), 'results': ('results',), 'metrics': ('metrics',),
    'conditions': ('conditions_of_validity',), 'limits': ('limitations',),
    'comparisons': ('comparisons',),
}
_STATUS_CACHE = OrderedDict()
_STATUS_LOCK = RLock()


class DimensionReview(BaseDimensionReview):
    dimension: Literal['problem', 'contribution', 'methods', 'mechanisms', 'experimental_protocol',
        'parameters', 'results', 'metrics', 'conditions', 'limits', 'comparisons', 'theory']
    essential: bool = True


class CoverageReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    question: str = Field(min_length=1, max_length=2000)
    sufficient: bool
    dimensions: list[DimensionReview] = Field(min_length=1, max_length=12)
    uncertainties: list[str] = Field(default_factory=list, max_length=20)


class Measurement(BaseModel):
    model_config = ConfigDict(extra='forbid')
    metric: str = Field(min_length=1, max_length=300)
    value_raw: str = Field(min_length=1, max_length=200)
    unit: str = Field(default='', max_length=100)
    experimental_conditions: str = Field(min_length=1, max_length=2000)
    measurement_method: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    origin: Literal['observed_result', 'author_interpretation', 'inference']
    source_kind: Literal['text', 'table'] = 'text'
    column: str | None = Field(default=None, max_length=300)


def request_id(state):
    return next((m.get('id') for m in reversed(state.get('messages', [])) if m.get('role') == 'user'), None)


def card_signature(article):
    return fingerprint(article.analysis)


def task_for(state, aid):
    tasks = state.setdefault('scientific_reading', {}).setdefault('tasks', [])
    task = next((t for t in tasks if t['article_id'] == aid), None)
    if task is None:
        task = {'article_id': aid, 'axes': [], 'status': 'queued', 'attempts': 0}
        tasks.append(task)
    return task


def valid_ids(state, aid, ids, *, presented=False):
    shown = set(state.get('shown_evidence_ids', []))
    evidence, snapshots = state.get('evidence', {}), state.get('source_snapshots', {})
    return {eid for eid in ids if eid in evidence and evidence[eid].get('article_id') == aid and
        exact_source_valid(evidence[eid], snapshots) and (not presented or eid in shown)}


def passage_states(state, article, rows=None):
    rows = passages(article) if rows is None else rows
    available = {r['evidence_id'] for r in rows}
    # A stored card is an analysis receipt, never a new presentation receipt.
    saved = {e['evidence_id'] for e in article.analysis.get('evidence', [])
        if usable_proof(state, e['evidence_id'], article_id=article.id)}
    reviewed = set()
    for review in state.get('scientific_reading_reviews', []):
        if review.get('article_id') == article.id:
            reviewed |= valid_ids(state, article.id, review.get('evidence_ids', []), presented=True)
    ledger = next((t for t in state.get('scientific_reading', {}).get('tasks', []) if t['article_id'] == article.id), {})
    for review in ledger.get('request_reviews', {}).values():
        reviewed |= valid_ids(state, article.id, [eid for d in review.get('dimensions', []) for eid in d.get('evidence_ids', [])], presented=True)
    return {'available': available, 'presented': valid_ids(state, article.id, state.get('shown_evidence_ids', [])),
        'examined': saved | reviewed, 'analyzed': saved, 'saved': saved}


def reading_status(state, article, *, rows=None, states=None):
    """Reuse V4's dimension ledger without its automatic reading scheduler."""
    from .scientific_reading import refresh_task
    existing = next((t for t in state.get('scientific_reading', {}).get('tasks', []) if t['article_id'] == article.id), {})
    rows = passages(article) if rows is None else rows
    states = passage_states(state, article, rows) if states is None else states
    source_fp = rows[0]['source_fingerprint'] if rows else snapshot(article)['fingerprint']
    signature = card_signature(article)
    key = fingerprint([article.id, source_fp, signature, request_id(state), existing.get('request_reviews'),
        {k:sorted(v) for k,v in states.items()}])
    with _STATUS_LOCK:
        if key in _STATUS_CACHE:
            _STATUS_CACHE.move_to_end(key)
            return copy.deepcopy(_STATUS_CACHE[key])
    task = copy.deepcopy(existing)
    task.setdefault('article_id', article.id)
    task.setdefault('status', 'queued')
    # Old source cursors or presented windows cannot claim an analysis.
    task.update(cursor=0, source_fingerprint=source_fp, examined_evidence_ids=sorted(states['examined']),
        reviewed_without_result_evidence_ids=sorted(states['examined']-states['analyzed']))
    refresh_task(task, active_article(state, article))
    fields = {name: 'documented' if any(c.get('evidence_ids') and set(c['evidence_ids']) <= states['saved']
        for f in fs for c in article.analysis.get(f, []) if isinstance(c,dict)) else 'not_examined'
        for name, fs in DIMENSIONS.items()}
    review = existing.get('request_reviews', {}).get(request_id(state))
    current = bool(review and review.get('card_signature') == signature and review.get('source_fingerprint') == source_fp
        and all(valid_ids(state, article.id, d.get('evidence_ids', []), presented=True) == set(d.get('evidence_ids', []))
            for d in review.get('dimensions', [])))
    if current:
        for d in review['dimensions']:
            fields[d['dimension']] = d['status']
    result = {'dimensions': fields, 'v4_dimensions': task.get('dimensions', {}),
        'states': {k: len(v) for k, v in states.items()},
        'review_current': current, 'question_sufficient': bool(current and review['sufficient']),
        'uncertainties': review.get('uncertainties', []) if current else [],
        'review_stale': bool(review and not current)}
    with _STATUS_LOCK:
        _STATUS_CACHE[key] = copy.deepcopy(result)
        while len(_STATUS_CACHE)>128:
            _STATUS_CACHE.popitem(last=False)
    return result


_TYPO = str.maketrans({'−': '-', '–': '-', '‐': '-', '‑': '-', '—': '-', '·': ' ',
    '⋅': ' ', ' ': ' ', ' ': ' ', ' ': ' '})
# What can continue a unit: an exponent (cm-2, h-1, g⁻¹), an area/volume power (cm2, m3), "/x".
_UNIT_CONTINUES = re.compile(r'^\s*(?:[A-Za-zµμΩ]{1,4}(?:-[0-9]|[⁻¹²³])|(?:[mcµμnkd]?m)[23](?![\d.,])|/\s*[A-Za-zµμ%])')
_RANGE = r'(?:\s*(?:-|to|à|and|et)\s*[\d.,]+)?'
# Not inside another number; a dash after a digit is a range (14-55 h), not a sign.
_START = r'(?<![\d.,+])(?<!(?<!\d)-)'


def _typo(text):
    return re.sub(r'[ \t]+', ' ', str(text or '').translate(_TYPO))


def _sentences(text):
    return [s for s in re.split(r'(?<=[.;!?])\s+(?=[A-Z(\[])|\n+', _typo(text)) if s.strip()]


def _pair(value, unit):
    return re.compile(_START + re.escape(_typo(value)) + r'(?!\d|[.,]\d)(' + _RANGE + r')\s*-?\s*'
        + re.escape(_typo(unit)) + r'(?![A-Za-z0-9µμ])')


def text_binding(measurement, quotes):
    """N10: the value and its complete unit stated together in one sentence of one proof.
    Returns the binding receipt; raises when no sentence states this exact pair."""
    pattern, found, incomplete = _pair(measurement.value_raw, measurement.unit), None, False
    for quote in quotes:
        for sentence in _sentences(quote):
            for match in pattern.finditer(sentence):
                if _UNIT_CONTINUES.match(sentence[match.end():]):
                    incomplete = True  # "1.45 mA cm-2" read as "1.45 mA"
                    continue
                found = found or (sentence, match)
    if found is None:
        if incomplete:
            raise ValueError('Unité incomplète : la source poursuit cette unité (exposant, puissance ou « / »). '
                'Recopier l’unité complète telle qu’écrite dans le passage.')
        raise ValueError('Valeur et unité ne sont pas associées dans une même phrase d’un passage exact.')
    sentence, match = found
    unit = re.escape(_typo(measurement.unit))
    same_unit = re.findall(_START + r'\d+(?:[.,]\d+)?' + _RANGE + r'\s*-?\s*' + unit + r'(?![A-Za-z0-9µμ])', sentence)
    issues = []
    if match.group(1) or re.search(r'\d\s*(?:-|to|à|and|et)\s*$', sentence[:match.start()]):
        issues.append('range_bound')
    if len(same_unit) > 1:
        issues.append('several_values_with_this_unit_in_sentence')
    return {'sentence': sentence[:600], 'issues': issues}


def _condition_issues(measurement, quotes):
    """Numbers Sol gives as conditions must exist in the cited proofs (not proven to apply)."""
    text = _typo(' '.join(quotes))
    numbers = re.findall(r'(?<![\w.,])\d+(?:[.,]\d+)?', _typo(measurement.experimental_conditions))
    missing = [n for n in numbers if not re.search(r'(?<![\d.,])' + re.escape(n) + r'(?!\d|[.,]\d)', text)]
    return ['condition_values_not_in_source'] if missing else []


def _header_unit(column):
    match = re.search(r'(?:/\s*|\[\s*|\(\s*)([^/\[\]()]{1,20}?)\s*[\])]?\s*$', column or '')
    return match.group(1).strip() if match else ''


def validate_measurements(state, article, measurements):
    """Literal values/units and source integrity, never automatic entailment.

    N10: a text value is bound to its complete unit in one sentence; a table value is the
    exact cell of its column (unit printed in the cell or in the column header). Ambiguous
    bindings (ranges, several values of the same unit in the sentence, condition numbers
    absent from the proofs) are saved with needs_verification=True and never support a
    numerical superiority."""
    from .scientific_review import validate_number, CELL
    from types import SimpleNamespace
    accepted = {}
    for measurement in measurements:
        ids = measurement.evidence_ids
        if valid_ids(state, article.id, ids, presented=True) != set(ids):
            raise ValueError('Mesure sans preuves exactes réellement présentées de cet article.')
        quotes = [state['evidence'][eid]['exact_quote'] for eid in ids]
        value = measurement.value_raw
        if not re.search(_START+re.escape(_typo(value))+r'(?!\d|[.,]\d)', _typo(' '.join(quotes))):
            raise ValueError('Valeur brute absente des preuves ; ne pas inventer ni convertir une ancienne affirmation.')
        binding = {'issues': []}
        if measurement.source_kind != 'table' and measurement.unit:
            binding = text_binding(measurement, quotes)
        if measurement.source_kind == 'table':
            fact = {'evidence_id':ids[0], 'column':measurement.column, 'value':value,
                'unit':measurement.unit, 'metric':measurement.metric}
            toolkit = SimpleNamespace(evidence=state['evidence'], shown_evidence=state.get('shown_evidence_ids', []),
                snapshots=state.get('source_snapshots', {}))
            table = (state['evidence'].get(ids[0]) or {}).get('table') or {}
            header = _header_unit(measurement.column)
            cells = dict(zip(table.get('headers', []), table.get('row', [])))
            parsed = CELL.match(str(cells.get(measurement.column, '')))
            printed = [u for u in [table.get('unit'), parsed.group(2) if parsed else '',
                *re.findall(r'\(([^()]{1,20})\)', measurement.column or '')] if u]
            if measurement.unit and not printed and _typo(header) == _typo(measurement.unit):
                # "R0/Ω", "Duration [h]": the unit is printed in the column header.
                validate_number({**fact, 'unit': ''}, toolkit)
                binding = {'issues': [], 'unit_source': 'column_header'}
            else:
                validate_number(fact, toolkit)
        issues = [*binding.get('issues', []), *_condition_issues(measurement, quotes)]
        data = {**measurement.model_dump(), 'source_fingerprints':sorted({state['evidence'][eid]['source_fingerprint'] for eid in ids}),
            'validation':'exact_table_cell' if measurement.source_kind=='table' else 'literal_value_and_unit',
            'semantic_support_status':'interpreted_by_sol; source_and_literal_value_checked'}
        key = fingerprint(data)  # unchanged identity: re-saving a known measurement adds nothing
        accepted[key] = {'id':'measurement_'+key, **data, 'binding_issues': issues,
            'needs_verification': bool(issues), **({'source_sentence': binding['sentence']} if binding.get('sentence') else {}),
            **({'unit_source': binding['unit_source']} if binding.get('unit_source') else {})}
    return accepted


def save_review(state, article, review):
    if not request_id(state):
        raise ValueError('Une revue de couverture exige une demande identifiée.')
    if len({d.dimension for d in review.dimensions}) != len(review.dimensions):
        raise ValueError('Dimensions de couverture dupliquées.')
    states = passage_states(state, article)
    for d in review.dimensions:
        ids = set(d.evidence_ids)
        if valid_ids(state, article.id, ids, presented=True) != ids:
            raise ValueError('Revue sans preuves exactes réellement présentées de cet article.')
        if d.status in {'verified', 'absent'} and not ids:
            raise ValueError('Un constat vérifié ou une absence exige des preuves.')
        if d.status == 'absent' and d.scope == 'examined_passages' and d.essential and review.sufficient:
            raise ValueError('Une absence dans des passages ciblés ne prouve pas une absence générale.')
        if d.scope == 'available_document' and (article.fulltext.get('truncated') or
                not article.fulltext.get('text') or not states['available'] <= states['examined'] | ids):
            raise ValueError('Le document entier n’a pas été examiné ; borner la revue aux passages.')
        if review.sufficient and d.essential and d.status in {'unread', 'ambiguous', 'unavailable'}:
            raise ValueError('Dimension essentielle encore incomplète : conserver une revue partielle.')
    saved = {**review.model_dump(), 'request_id': request_id(state), 'article_id': article.id,
        'card_signature': card_signature(article), 'source_fingerprint': snapshot(article)['fingerprint']}
    task_for(state, article.id).setdefault('request_reviews', {})[request_id(state)] = saved
    return saved


def passage_index(state, article, *, section=None, offset=0, limit=20):
    """Locators of the passages not yet examined, in document order.

    The cursor is a position in the stable list of passages matching the filter (it depends
    only on the document version), so reading, presenting or examining passages between two
    pages never shifts it: no passage is skipped or repeated. Examined passages are left out
    of the page, deferred and presented ones are flagged in place."""
    rows = passages(article)
    states = passage_states(state, article, rows)
    def matches(r):
        return not section or section.casefold() in str(r.get('section') or '').casefold()
    ordered = [r for r in rows if matches(r)]
    queue = set(task_for(state, article.id).get('deferred_evidence_ids', []))
    open_positions = [i for i, r in enumerate(ordered) if r['evidence_id'] not in states['examined']]
    page_positions = [i for i in open_positions if i >= offset][:max(0, limit)]
    following = [i for i in open_positions if i > (page_positions[-1] if page_positions else offset-1)]
    return {'article_id': article.id, 'source_fingerprint': snapshot(article)['fingerprint'], 'total': len(open_positions),
        'offset': offset, 'next_offset': following[0] if following else None,
        'remaining_after_page': len(following),
        'section_filter': section, 'sections': sorted({str(r.get('section') or '') for r in rows}),
        'passages': [{'position': i, 'evidence_id': ordered[i]['evidence_id'], 'section': ordered[i].get('section'),
            'page': ordered[i].get('page'), 'presented': ordered[i]['evidence_id'] in states['presented'], 'examined': False,
            'deferred': ordered[i]['evidence_id'] in queue, 'contains_numbers': bool(re.search(r'\d', ordered[i]['text'])),
            'preview': ' '.join(ordered[i]['text'].split())[:160]} for i in page_positions],
        'coverage': reading_status(state, article), 'analysis_depth': analysis_depth(state, article),
        'guidance': 'Index seulement, aucun passage présenté. Choisir les preuves utiles avec analyze_article(read, evidence_ids). '
            'Un passage déjà présenté peut être analysé/sauvegardé sans relecture ; revisit=true si sa relecture est nécessaire.'}


INTRODUCTORY_SECTION = re.compile(r'^\s*(?:\d+(?:\.\d+)*\.?\s*)?(?:abstract|r[ée]sum[ée]|summary|highlights|graphical abstract|'
    r'key\s*words?|mots[- ]cl[ée]s|introduction|background|contexte)\b', re.I)
DEPTH_DIMENSIONS = {'methods': ('method',), 'results': ('results', 'metrics'), 'limits': ('limitations',),
    'conditions': ('experimental_setup', 'validation_protocol', 'parameters', 'conditions_of_validity', 'datasets_materials')}
DEPTH_CORE = ('methods', 'results', 'limits')
DEPTH_LABELS = {'complete': 'analyse approfondie : méthodes, résultats et limites lus dans le corps du texte',
    'partial': 'lecture partielle du texte intégral', 'introductory': 'résumé ou introduction du texte intégral seulement',
    'abstract_only': 'résumé seulement', 'none': 'aucune analyse'}


def introductory(proof):
    """A full-text proof from the abstract, keywords or introduction is not a reading of the work."""
    section = str(proof.get('section') or '')
    return bool(INTRODUCTORY_SECTION.match(section.split(' / ')[0]) or INTRODUCTORY_SECTION.match(section.split(' / ')[-1]))


def analysis_depth(state, article):
    """Provenance, actually read coverage and sufficiency of a card, kept apart.

    V4 sets access_status='fulltext_analyzed' as soon as one saved proof comes from the
    document, even the abstract printed in the PDF: that value is provenance, kept for
    compatibility. Here a dimension is covered only by a usable proof from the body of
    the text; the abstract or introduction alone is 'introductory', never complete."""
    from .evidence import active_article as current_card
    analysis = current_card(state, article).analysis or {}
    evidence = state.get('evidence', {})
    bases, coverage = set(), {}
    for dimension, fields in DEPTH_DIMENSIONS.items():
        level = 'not_documented'
        for field in fields:
            for claim in analysis.get(field, []) or []:
                ids = [e for e in (claim.get('evidence_ids') or []) if usable_proof(state, e, article_id=article.id)] \
                    if isinstance(claim, dict) else []
                for eid in ids:
                    proof = evidence[eid]
                    bases.add(proof.get('basis'))
                    kind = 'body' if proof.get('basis') == 'fulltext' and not introductory(proof) else 'introductory'
                    if kind == 'body' or level == 'not_documented':
                        level = kind
        coverage[dimension] = level
    for proof in analysis.get('evidence', []) or []:
        if isinstance(proof, dict) and proof.get('evidence_id') in evidence:
            bases.add(evidence[proof['evidence_id']].get('basis'))
    bases.discard(None)
    provenance = 'none' if not bases else 'mixed' if len(bases) > 1 else next(iter(bases))
    sufficient = bool(analysis and reading_status(state, article)['question_sufficient'])
    claims = any(isinstance(v, list) and any(isinstance(c, dict) and c.get('statement') for c in v) for v in analysis.values())
    body = [d for d in DEPTH_CORE if coverage[d] == 'body']
    depth = ('none' if not claims else 'abstract_only' if provenance == 'abstract' else
        'introductory' if not any(v == 'body' for v in coverage.values()) else
        'complete' if len(body) == len(DEPTH_CORE) else 'partial')
    return {'provenance': provenance, 'coverage': coverage, 'depth': depth, 'question_sufficient': sufficient,
        'to_read': [d for d in DEPTH_DIMENSIONS if coverage[d] != 'body'] if article.fulltext.get('text') else [],
        'label': DEPTH_LABELS[depth] + ('' if depth in {'complete', 'none'} else
            ' ; à lire : ' + ', '.join(d for d in DEPTH_DIMENSIONS if coverage[d] != 'body')
            if article.fulltext.get('text') and depth != 'abstract_only' else '')}


def comparison_work(state):
    """Candidate groups chosen by the request/plan, never a Cartesian product."""
    goal = state.get('turn_goal') or {}
    if goal.get('deliverable') not in {'comparison', 'draft', 'plan', 'plan_revision'} and not goal.get('comparison_requested'):
        return []
    from .argumentation import relation_current
    from types import SimpleNamespace
    registry = {aid:Article.model_validate(a) for aid,a in state.get('articles', {}).items()}
    toolkit = SimpleNamespace(registry=registry, evidence=state.get('evidence', {}), snapshots=state.get('source_snapshots', {}))
    groups = []
    if goal.get('deliverable') in {'draft','plan','plan_revision'}:
        targets = (state.get('document', {}).get('writing_progress') or {}).get('target_ids')
        groups = [(s['id'],s.get('article_ids', [])) for s in state.get('outline', {}).get('sections', []) if not targets or s['id'] in targets]
    if not groups:
        ids = (goal.get('references') or {}).get('article_ids') or state.get('current_turn_article_ids') or state.get('displayed_article_ids', [])
        groups = [('request',ids)]
    result = []
    for group,ids in groups:
        ids = list(dict.fromkeys(aid for aid in ids if aid in registry and registry[aid].selection_override is not False))
        if len(ids)<2:
            continue
        ready = [aid for aid in ids if passage_states(state,registry[aid])['analyzed']]
        current = [r['id'] for r in state.get('scientific_relations', []) if set(r['article_ids']) <= set(ids) and relation_current(toolkit,r)]
        result.append({'group':group, 'candidate_article_ids':ready, 'missing_analyses':[aid for aid in ids if aid not in ready],
            'existing_relation_ids':current, 'status':'relations_available' if current else 'consider_comparison',
            'guidance':'Sol choisit les travaux utiles, méthodes, protocoles, conditions, mécanismes, résultats et limites. '
                'compare_articles conserve les preuves et incertitudes ; aucune supériorité de protocoles non comparables.'})
    return result


_CONTRAST = re.compile(r'\s+(?:alors que|tandis que|contre|mais|whereas|while|versus|vs\.?|but|compared with|compared to)\s+|\(', re.I)
_NUMBER = re.compile(r'(?<![A-Za-z\w.,+])(?<!(?<!\d)-)(\d+(?:[.,]\d+)?)(?!\d|[.,]\d)(?:\s*-?\s*([A-Za-zµμΩ°%][A-Za-z]{0,3}))?')


def _entity_terms(text):
    """Formula/acronym-like names (Cu-SiOx, YSZ/CuO, MEA, C2H4): never ordinary words."""
    terms = set()
    for raw in _typo(text).split():
        token = raw.strip('.,;:()[]{}"\'«»“”')
        if len(token) < 2 or not re.search(r'[A-Za-z]', token):
            continue
        if re.search(r'\d', token) or re.search(r'[A-Z]', token[1:]) or re.search(r'[A-Za-z][-/][A-Za-z]', token) and re.search(r'[A-Z]', token):
            terms.add(token.casefold())
    return terms


def _mentions(term, text):
    return bool(re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text))


def check_relation_attribution(toolkit, args):
    """F01: a number stated for one compared work must come from that work's cited proofs.

    Each clause is attributed to the single work it names through a distinctive term found only in
    that work's cited quotes or title (else to the work named earlier in the same segment). A number
    or value+unit of that clause found only in another work's proofs is an inverted attribution and
    is refused. Clauses naming no work, or several, are left to the existing union check. Condition
    numbers must exist in the cited proofs."""
    for relation in args.relations or []:
        claims = [(relation.statement, relation.evidence_ids, False), (relation.conditions or '', relation.evidence_ids, True),
            *[(c.statement, c.evidence_ids, False) for c in relation.experimental_results]]
        for statement, eids, conditions in claims:
            proofs = [toolkit.evidence.get(e) for e in eids]
            by_article = {}
            for proof in proofs:
                if proof:
                    by_article.setdefault(proof['article_id'], []).append(proof['exact_quote'])
            if not by_article:
                continue
            quotes = {aid: _typo(' '.join(q)) for aid, q in by_article.items()}
            hay = {aid: (quotes[aid] + ' ' + _typo(getattr(toolkit.registry.get(aid), 'title', '') or '')).casefold()
                for aid in quotes}
            titles = {aid: getattr(toolkit.registry.get(aid), 'title', '') or aid for aid in quotes}
            # Segments (; or sentence) > contrasted parts (a contrast word may switch work, so
            # nothing is carried across it) > comma clauses (the named work carries forward).
            parts = [p for s in re.split(r'\s*;\s*|(?<=[.!?])\s+', statement or '') for p in _CONTRAST.split(s)]
            for part in parts:
                named = None
                for clause in re.split(r',\s+', part):
                    terms = _entity_terms(clause)
                    own = {aid for aid in hay if any(_mentions(t, hay[aid]) and not any(_mentions(t, hay[o])
                        for o in hay if o != aid) for t in terms)}
                    if len(own) == 1:
                        named = next(iter(own))
                    elif len(own) > 1:
                        named = None
                    for match in _NUMBER.finditer(clause):
                        # Numbers inside names (CO2, Cu-SiOx-2.5) never match _NUMBER.
                        number, unit = match.group(1), match.group(2) or ''
                        numeric = re.compile(_START + re.escape(number.replace(',', '.')) + r'(?!\d|[.,]\d)')
                        has_number = {aid for aid, q in quotes.items() if numeric.search(q.replace(',', '.'))}
                        has_pair = {aid for aid, q in quotes.items() if unit and _pair(number, unit).search(q)}
                        if conditions and not has_number:
                            raise ValueError(f'Condition « {match.group(0).strip()} » absente des preuves citées : '
                                'ne pas ajouter de condition numérique non sourcée.')
                        if named is None:
                            continue
                        support = has_pair or has_number
                        if support and named not in support:
                            others = ', '.join(f'« {titles[a][:80]} »' for a in sorted(support))
                            raise ValueError(f'Attribution inversée : « {match.group(0).strip()} » est rattaché à '
                                f'« {titles[named][:80]} » mais n’apparaît que dans les preuves de {others}. '
                                'Corriger l’attribution ou citer le passage exact de chaque travail.')


def check_comparative_measurements(toolkit, args):
    for relation in args.relations or []:
        if relation.kind != 'conditional_superiority':
            continue
        protocols = []
        for aid in relation.article_ids:
            measurements = [m for m in toolkit.registry[aid].analysis.get('structured_measurements', {}).values()
                if set(m['evidence_ids']) & set(relation.evidence_ids)]
            unsure = [m for m in measurements if m.get('needs_verification')]
            if unsure:
                raise ValueError('Mesure à vérifier (' + ', '.join(sorted({i for m in unsure for i in m.get('binding_issues', [])}))
                    + ') : elle ne peut pas fonder une supériorité numérique. Relire la source ou enregistrer une '
                    'comparaison prudente avec cette incertitude.')
            protocols.append({tuple(' '.join(str(m[k]).casefold().split()) for k in
                ('metric','unit','experimental_conditions','measurement_method')) for m in measurements})
        if protocols and all(protocols) and not set.intersection(*protocols):
            raise ValueError('Conditions, unités ou protocoles des mesures différents : aucune supériorité numérique vérifiable. '
                'Enregistrer une comparaison partielle, alternative ou complémentarité avec ses incertitudes.')
