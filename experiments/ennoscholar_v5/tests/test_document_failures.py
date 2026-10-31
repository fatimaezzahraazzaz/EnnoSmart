"""Real V4 recovery path with simulated HTTP outcomes, never a live download."""
import copy
from unittest.mock import Mock
from xml.sax.saxutils import escape
import httpx

from .test_graph import setup,FANG,LI
from experiments.ennoscholar_v5.adapter import Decision,ScriptedSol,V4Adapter
from experiments.ennoscholar_v5.graph import Runner
from experiments.ennoscholar_v5.checkpoints import sqlite_checkpoints


def test_inaccessible_representations_are_persisted_and_not_automatically_retried(tmp_path,monkeypatch):
    from agents.EnnoScholarV4.tools import fulltext
    c,s,scope,repo=setup(tmp_path);attempted=[];after_first=[]
    def failed(url,*args,**kwargs):
        attempted.append(url);request=httpx.Request('GET',url)
        response=httpx.Response(404,request=request)
        raise httpx.HTTPStatusError('Offline fixture: unavailable',request=request,response=response)
    monkeypatch.setattr(fulltext,'fetch_public',failed)
    sources=Mock();sources.metadata.return_value=[]
    d=Decision('get_fulltext',{'article_id':FANG,'candidate_urls':[
        'https://example.org/author.pdf','https://example.org/repository.html'],'page_start':None,'page_limit':None})
    def repeat(inputs,progress):
        after_first.append(len(attempted));return d
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        out=Runner(scope,repo,V4Adapter(sources=sources,fixture_sources=True),
            ScriptedSol([d,repeat,Decision('pause')]),saver).start()
    final=repo.load(scope)[1];ft=final['articles'][FANG]['fulltext']
    assert len(attempted)==after_first[0] and len(attempted)>=2
    assert {a['http_status'] for a in ft['attempts'] if a.get('http_status')}=={404}
    assert not ft['text'] and not final['articles'][FANG]['analysis']
    assert any(t['kind']=='recover_document' and t['article_id']==FANG for t in out['progress']['tasks'].values())
    assert repo.completed_actions(scope)[1]['response']['reuse_reason']=='blocked_failure'


def test_temporary_failure_can_use_a_verified_alternative_in_the_same_operation(tmp_path,monkeypatch):
    from agents.EnnoScholarV4.tools import fulltext
    c,s,scope,repo=setup(tmp_path);body=s['articles'][LI]['fulltext']['text'][:8000]
    title=s['articles'][LI]['title'];doi=s['articles'][LI]['doi']
    s['articles'][LI]['fulltext']={};s['articles'][LI]['documents']={}
    # A new synthetic representation containing an exact archived body passage;
    # its provenance remains the synthetic fixture, never the original bytes.
    xml=(f'<article><front><article-meta><article-id pub-id-type="doi">{escape(doi)}</article-id>'
        f'<title-group><article-title>{escape(title)}</article-title></title-group>'
        '<contrib-group><contrib contrib-type="author"><name><surname>Li</surname></name></contrib></contrib-group>'
        f'</article-meta></front><body><sec><title>Methods</title><p>{escape(body)}</p></sec></body></article>').encode()
    calls=[]
    def fetch(url,*args,**kwargs):
        calls.append(url)
        if len(calls)==1:
            req=httpx.Request('GET',url);response=httpx.Response(429,headers={'retry-after':'2'},request=req)
            raise httpx.HTTPStatusError('Offline fixture: transient rate limit',request=req,response=response)
        return xml,'application/xml',url
    monkeypatch.setattr(fulltext,'fetch_public',fetch)
    sources=Mock();sources.metadata.return_value=[]
    # Separate isolated repository; do not alter the seeded real fixture in-place.
    from experiments.ennoscholar_v5.repository import Repository
    repo=Repository(tmp_path/'alternative-science.sqlite3',writable_root=tmp_path);repo.seed(scope,s)
    d=Decision('get_fulltext',{'article_id':LI,'candidate_urls':[
        'https://example.org/rate-limited.xml','https://example.org/author.xml'],'page_start':None,'page_limit':None})
    with sqlite_checkpoints(tmp_path/'graph.sqlite3') as saver:
        out=Runner(scope,repo,V4Adapter(sources=sources,fixture_sources=True),ScriptedSol([d,Decision('pause')]),saver).start()
    ft=repo.load(scope)[1]['articles'][LI]['fulltext']
    assert len(calls)==2 and ft['text'] and ft['document_version']['identifiers']['doi']==doi
    assert any(a.get('http_status')==429 for a in ft['attempts'])
    assert any(a.get('outcome')=='success' for a in ft['attempts'])
    assert not out['progress']['articles'][LI]['analysis_saved']
    assert any(t['kind']=='read_local' and t['article_id']==LI for t in out['progress']['tasks'].values())
