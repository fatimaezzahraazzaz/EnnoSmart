"""One active HTML extractor: never-extracted, previous-version, current-version and cross-session
articles; evidence and data preservation; no download and no needless re-extraction. Offline."""
import copy
import hashlib
import json
import time
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.tools import extraction as v4
from agents.EnnoScholarV5 import html_currency
from agents.EnnoScholarV5.evidence import SEGMENTATION, exact_source_valid, passages, snapshot
from agents.EnnoScholarV5.models import Article
from agents.EnnoScholarV5.toolkit import ScholarTools
from agents.EnnoScholarV5.tools import extraction as v5, fulltext
from .test_graph import case
from .test_html_floats import PMC_FIGURE, SECTION_WRAPPED, STANDARD_FIGURE, page
from .test_live_integration import app_env, call, run_sync, scientific_state

DOI = '10.9999/html-currency'
AID = 'paper_' + hashlib.sha256(DOI.encode()).hexdigest()[:20]
URL = 'https://fixture.invalid/articles/html-currency/'
CONFIG = ScholarV4Config(api_key='offline', web_search_enabled=False)
HTML = page(PMC_FIGURE, '<p>Between the floats.</p>', SECTION_WRAPPED, STANDARD_FIGURE, doi=DOI)
READ = {'article_ids': [AID], 'action': 'read', 'research_context': 'Légendes et références.',
    'passage_query': 'Figure 1', 'passage_offset': 0, 'passages_per_article': 4, 'analysis': None}


EXTRACTORS = {v4: v4.html_document, v5: v5.html_document}  # captured before any counting patch


def stored(extractor=v4, *, keep_html=True, marker=None):
    """A saved HTML record as get_fulltext stores it, produced by the given extractor."""
    from agents.EnnoScholarV5.provenance import document_identity
    _, chunks, truncated = EXTRACTORS[extractor](HTML, URL, CONFIG, allow_partial=True)
    version = document_identity(HTML, URL, 'html', chunks)
    record = {'access_status': 'fulltext_extracted', 'access_level': 'fulltext', 'text': '\n\n'.join(c.text for c in chunks),
        'url': URL, 'pdf_file': '', 'source_kind': 'html', 'document_version': version,
        'extraction_version': 'scientific_structures_v2', 'identity_verification': 'doi',
        'content_scope': 'scientific_body', 'source_html': HTML.decode() if keep_html else '',
        'linked_table_sources': [], 'extraction_limits': [], 'chunks': [c.model_dump() for c in chunks],
        'truncated': truncated, 'remaining_document_urls': [], 'segmentation': SEGMENTATION,
        'attempts': [{'url': URL, 'final_url': URL, 'stage': 'extraction', 'outcome': 'success', 'attempt_number': 1}],
        'reason': 'Corps scientifique extrait ; lecture et analyse restent à effectuer.'}
    if marker:
        record['html_extractor'] = marker
    return record


def article(record=None):
    a = Article(id=AID, canonical_work_id=AID, title='Currency fixture', doi=DOI, authors=['Ada Author'], year=2024,
        url=URL, verified=True, verification_sources=[URL], abstract='A short abstract.')
    if record:
        a.fulltext = copy.deepcopy(record)
        a.documents = {record['document_version']['version_id']: copy.deepcopy(record)}
        a.access_status, a.access_level, a.fulltext_available, a.fulltext_source = 'fulltext_extracted', 'fulltext', True, URL
    return a


def session_with(a, *, old_proof=True):
    state = copy.deepcopy(case('chemistry')['state'])
    state['articles'][AID] = a.durable()
    state['turn_goal'] = {'deliverable': 'deep_reading', 'article_ids': [AID], 'references': {'article_ids': [AID], 'scope': 'exclusive'}}
    proof = None
    if old_proof and a.fulltext.get('text'):
        row = next(r for r in passages(a) if '(a) Diffraction' in r['exact_quote'])
        source = snapshot(a)
        state.setdefault('source_snapshots', {})[source['fingerprint']] = source
        state.setdefault('evidence', {})[row['evidence_id']] = row
        state.setdefault('shown_evidence_ids', []).append(row['evidence_id'])
        proof = row['evidence_id']
    return state, proof


@pytest.fixture
def counted(monkeypatch):
    """Count V5 HTML extractions and forbid any download."""
    calls = []
    real = v5.html_document
    def counting(*args, **kwargs):
        calls.append(args[1])
        return real(*args, **kwargs)
    monkeypatch.setattr(v5, 'html_document', counting)
    monkeypatch.setattr(fulltext.base, 'html_document', counting)
    def no_download(*args, **kwargs):
        raise AssertionError('download attempted')
    monkeypatch.setattr(fulltext.base, 'fetch_public', no_download)
    return calls


def tools(state):
    sources = Mock()
    sources.verify.side_effect = lambda article, *a, **k: article
    return ScholarTools(state, sources, CONFIG)


def figure_chunks(record):
    return [c for c in record['chunks'] if c.get('figure')]


# --- One active path ---------------------------------------------------------------------------

def test_v5_has_a_single_html_extraction_path():
    from agents.EnnoScholarV5.tools import tables
    assert fulltext.base.html_document is v5.html_document is v5.base.html_document
    assert v5.html_document is not v4.html_document
    assert tables.html_table is not tables.base.html_table and tables.table_text is not tables.base.table_text
    # The base HTML walk now resolves to V5's walk; V4's own module is untouched.
    assert v4.html_document.__module__ == 'agents.EnnoScholarV4.tools.extraction'


def test_previous_marker_or_no_marker_is_stale_but_pdf_and_jats_are_not():
    assert html_currency.stale(stored()) and html_currency.stale(stored(marker='other_extractor'))
    assert not html_currency.stale(stored(v5, marker=html_currency.CURRENT))
    assert not html_currency.stale({'source_kind': 'pdf', 'text': 'x'}) and not html_currency.stale({'source_kind': 'jats', 'text': 'x'})


# --- 1. Never extracted ------------------------------------------------------------------------

def test_never_extracted_article_is_acquired_once_with_the_current_extractor(app_env, monkeypatch):
    make, factory, _ = app_env
    calls, fetched = [], []
    real = v5.html_document
    monkeypatch.setattr(fulltext.base, 'html_document', lambda *a, **k: calls.append(1) or real(*a, **k))
    monkeypatch.setattr(fulltext.base, 'fetch_public', lambda url, *a, **k: fetched.append(url) or (HTML, 'text/html', URL))
    state, _ = session_with(article())
    sources = Mock()
    sources.verify.side_effect = lambda article, *a, **k: article
    acquire = {'article_id': AID, 'candidate_urls': [URL], 'page_start': None, 'page_limit': None}
    svc, sid, _ = make([call('get_fulltext', acquire, 'acquire'), call('analyze_article', READ, 'read'),
        call('analyze_article', {**READ, 'passage_query': 'Table 1', 'revisit': True}, 'again'),
        call('finalize_response', {'answer': 'Lu.', 'article_ids': [], 'evidence_ids': [], 'coverage_sufficient': False,
            'remaining_uncertainties': ['x'], 'analyses': None, 'displayed_article_ids': None}, 'final')], state, sources=sources)
    run_sync(svc, sid, 'Lis la figure 1.')
    saved = Article.model_validate(scientific_state(factory, sid)['articles'][AID])
    assert fetched == [URL] and len(calls) == 1                      # one download, one extraction
    assert saved.fulltext['html_extractor'] == html_currency.CURRENT
    assert figure_chunks(saved.fulltext)[0]['figure']['graphic_refs'] == ['https://cdn.fixture.invalid/blobs/fig-a.webp']
    assert not scientific_state(factory, sid).get(html_currency.ARCHIVE)


# --- 2. Extracted with the previous version ----------------------------------------------------

def test_previous_extraction_is_reextracted_locally_once_and_old_proof_stays_valid(counted):
    state, proof = session_with(article(stored()))
    old_text = state['articles'][AID]['fulltext']['text']
    t = tools(state)
    result = t.execute('analyze_article', READ, time.monotonic()+60)
    current = Article.model_validate(state['articles'][AID]).fulltext
    assert counted == [URL]                                           # local, from the stored bytes
    assert current['html_extractor'] == html_currency.CURRENT and current['document_version'] == stored()['document_version']
    fig = figure_chunks(current)[0]['figure']
    assert (fig['label'], fig['graphic_refs']) == ('Figure 1', ['https://cdn.fixture.invalid/blobs/fig-a.webp'])
    assert any((c.get('table') or {}).get('number') == '1' for c in current['chunks'])
    shown = result['reading'][0]['passages']
    now = snapshot(Article.model_validate(state['articles'][AID]))['fingerprint']
    assert shown and all(state['evidence'][p['evidence_id']]['source_fingerprint'] == now for p in shown)
    assert any('fig-a.webp' in p['exact_quote'] for p in shown)
    # Preservation: old proof valid, old record archived, documents archive current.
    assert exact_source_valid(state['evidence'][proof], state['source_snapshots'])
    [archived] = state[html_currency.ARCHIVE][AID]
    assert archived['text'] == old_text and 'source_html' not in archived and archived['superseded_outcome'] == 'reextracted'
    assert state['articles'][AID]['documents'][current['document_version']['version_id']]['html_extractor'] == html_currency.CURRENT
    assert state['html_extraction_refreshes'] == [{'article_id': AID, 'outcome': 'reextracted'}]
    # No second processing: new toolkit, new calls.
    for _ in range(2):
        tools(state).execute('analyze_article', {**READ, 'revisit': True}, time.monotonic()+60)
    assert counted == [URL] and len(state[html_currency.ARCHIVE][AID]) == 1


def test_previous_extraction_without_stored_html_is_retired_not_used(counted):
    state, proof = session_with(article(stored(keep_html=False)))
    before = copy.deepcopy(state['articles'][AID]['fulltext'])
    t = tools(state)
    try:
        result = t.execute('analyze_article', READ, time.monotonic()+60)
    except ValueError:
        result = {}
    saved = Article.model_validate(state['articles'][AID])
    assert not saved.fulltext.get('text') and not saved.documents and counted == []
    assert saved.access_status == 'fulltext_available' and saved.fulltext['attempts'] == before['attempts']
    assert all(p['exact_quote'] not in before['text'] for r in result.get('reading', []) for p in r.get('passages', []))
    [archived] = state[html_currency.ARCHIVE][AID]
    assert archived['text'] == before['text'] and archived['superseded_outcome'] == 'retired'
    assert exact_source_valid(state['evidence'][proof], state['source_snapshots'])


def test_retired_article_is_reacquired_with_the_current_extractor(monkeypatch):
    state, _ = session_with(article(stored(keep_html=False)))
    fetched = []
    monkeypatch.setattr(fulltext.base, 'fetch_public', lambda url, *a, **k: fetched.append(url) or (HTML, 'text/html', URL))
    t = tools(state)
    t.execute('get_fulltext', {'article_id': AID, 'candidate_urls': [URL], 'page_start': None, 'page_limit': None}, time.monotonic()+60)
    saved = Article.model_validate(state['articles'][AID])
    assert fetched == [URL] and saved.fulltext['html_extractor'] == html_currency.CURRENT and figure_chunks(saved.fulltext)


# --- 3. Already extracted with the current version ---------------------------------------------

def test_current_extraction_is_never_reprocessed_or_modified(counted):
    state, proof = session_with(article(stored(v5, marker=html_currency.CURRENT)))
    before = copy.deepcopy(state['articles'][AID])
    tools(state).execute('analyze_article', READ, time.monotonic()+60)
    assert counted == [] and state['articles'][AID]['fulltext'] == before['fulltext']
    assert state['articles'][AID]['documents'] == before['documents'] and html_currency.ARCHIVE not in state


def test_pdf_and_jats_records_are_untouched(counted):
    a = article(stored(v5))
    a.fulltext = {**a.fulltext, 'source_kind': 'jats', 'source_html': ''}
    a.documents = {k: {**d, 'source_kind': 'jats'} for k, d in a.documents.items()}
    state, _ = session_with(a, old_proof=False)
    before = copy.deepcopy(state['articles'][AID])
    tools(state).execute('analyze_article', READ, time.monotonic()+60)
    assert counted == [] and state['articles'][AID]['fulltext'] == before['fulltext']


# --- 4. Reused from another session (the two real failures) ------------------------------------

def test_article_reused_from_another_session_is_reextracted_without_download(app_env, counted):
    make, factory, _ = app_env
    old_state, proof = session_with(article(stored()))
    _, sid_a, _ = make([], old_state)
    with factory() as db:
        from agents.EnnoScholarV5.session import ScholarV4Session
        before_a = json.dumps(db.get(ScholarV4Session, sid_a).data, sort_keys=True)
    fresh = copy.deepcopy(case('chemistry')['state'])
    sources = Mock()
    sources.verify.side_effect = lambda article, *a, **k: article
    seen = {}

    def figure_by_id(sent):
        rows = passages(Article.model_validate(scientific_state(factory, sid_b)['articles'][AID]))
        seen['ids'] = [r['evidence_id'] for r in rows if 'fig-a.webp' in r['exact_quote']]
        return call('analyze_article', {**READ, 'passage_query': '', 'table_number': '1', 'evidence_ids': seen['ids']}, 'fig')

    def finish(sent):
        out = {i['call_id']: json.loads(i['output']) for i in sent['input'] if i.get('type') == 'function_call_output'}
        seen['fig'] = out.get('fig')
        return call('finalize_response', {'answer': 'Figure lue.', 'article_ids': [], 'evidence_ids': [],
            'coverage_sufficient': False, 'remaining_uncertainties': ['x'], 'analyses': None, 'displayed_article_ids': None}, 'final')

    svc, sid_b, _ = make([call('get_article', {'identifiers': [DOI]}, 'meta'), figure_by_id, finish], fresh, sources=sources)
    run_sync(svc, sid_b, 'Donne la légende et la référence graphique de la figure 1.')
    state_b = scientific_state(factory, sid_b)
    current = state_b['articles'][AID]['fulltext']
    assert counted == [URL]                                           # one local re-extraction, no download
    assert current['html_extractor'] == html_currency.CURRENT
    fig = figure_chunks(current)[0]['figure']
    assert fig['caption'] == '(a) Diffraction patterns; (b) spectra.' and fig['graphic_refs']
    shown = (seen['fig'].get('output') or seen['fig'])['reading'][0]['passages']
    assert [p['evidence_id'] for p in shown] == seen['ids'] and 'fig-a.webp' in shown[0]['exact_quote']
    assert state_b['html_extraction_refreshes'] == [{'article_id': AID, 'outcome': 'reextracted'}]
    # The imported proof of session A remains exact in B; session A is unchanged.
    assert proof in state_b['evidence'] and exact_source_valid(state_b['evidence'][proof], state_b['source_snapshots'])
    with factory() as db:
        assert json.dumps(db.get(ScholarV4Session, sid_a).data, sort_keys=True) == before_a
