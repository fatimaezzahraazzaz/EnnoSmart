"""Question/version/proof-aware memory, and progress based on scientific receipts."""
import copy
import pytest
from agents.EnnoScholarV5 import workflow
from agents.EnnoScholarV5.writing_job import science_signature
from agents.EnnoScholarV5.comparison_memory import remembered
from experiments.ennoscholar_v5.adapter import Decision
from experiments.ennoscholar_v5.policy import semantic_key,reusable
from .test_plan_edit_scope import lithium,ask


def comparison_state():
    state=ask(lithium(),'Compare les méthodes et leurs conditions expérimentales.')
    ids=[aid for aid,a in state['articles'].items() if (a.get('analysis') or {}).get('results')][:2]
    assert len(ids)==2
    state['turn_goal']={'deliverable':'comparison','references':{'article_ids':ids}}
    return state,ids


def record(state,ids,relations=None):
    workflow.after_comparison(state,{'article_ids':ids,'relations':relations or [],'assessment':'Protocoles non comparables.'},
        {'saved':True,'scientific_relations':[]})


@pytest.mark.parametrize('change',['question','card','proof','articles','legacy'])
def test_block3_negative_comparison_is_invalidated_by_scientific_changes(change):
    state,ids=comparison_state();record(state,ids)
    assert remembered(state,ids) and not workflow.comparison_required(state)
    if change=='question':state['messages'][-1]['content']='Compare maintenant les seuils de température.'
    if change=='card':state['articles'][ids[0]]['analysis']['results'][0]['statement']+=' Résultat précisé.'
    if change=='proof':
        eid=next(eid for eid,e in state['evidence'].items() if e['article_id'] in ids)
        state['evidence'][eid]['exact_quote']+=' Texte modifié.'
    if change=='articles':ids=list(state['articles'])[:3]
    if change=='legacy':state['v5_comparison_decisions'][-1].pop('identity')
    assert not remembered(state,ids)
    if change!='articles':assert workflow.comparison_required(state)


def test_block3_same_question_across_resume_reuses_comparison_without_new_judgement():
    state,ids=comparison_state();record(state,ids)
    before=copy.deepcopy(state)
    state['messages'][-1]['id']='new-id-same-question'
    assert remembered(state,ids)
    assert science_signature(state)['comparison_decisions']==1
    assert before['v5_comparison_decisions']==state['v5_comparison_decisions']


def test_block3_drafting_resume_preserves_question_but_a_qualified_question_changes_it():
    state=ask(lithium(),'Rédige la section 3.')
    section=workflow.next_writing_section(state)
    ids=section['article_ids']
    record(state,ids)
    assert remembered(state,ids,section)
    state['messages'].append({'id':'resume','role':'user','content':"Continue la rédaction de l'état de l'art."})
    workflow.begin(state,state['messages'][-1]['content'])
    assert remembered(state,ids,workflow.next_writing_section(state))
    state['messages'].append({'id':'newq','role':'user','content':'Continue la rédaction en comparant les températures.'})
    workflow.begin(state,state['messages'][-1]['content'])
    assert not remembered(state,ids,workflow.next_writing_section(state))


def test_block3_metadata_and_alias_lists_are_not_scientific_progress():
    state,ids=comparison_state();before=science_signature(state)
    analysis=state['articles'][ids[0]]['analysis']
    analysis.setdefault('analysis_limits',[]).append('Une limite administrative ajoutée.')
    analysis['aliases_for_ui']=[{'statement':'Alias','evidence_ids':list(state['evidence'])[:1]}]
    unchanged=science_signature(state)
    assert unchanged['claims']==before['claims'] and unchanged['evidence']==before['evidence']
    assert not set(unchanged['work_ids'])-set(before['work_ids'])
    # An actual new, proof-backed statement is progress even at equal list length.
    analysis['results'][0]['statement']+=' Précision scientifique.'
    after=science_signature(state)
    assert after['claims']==before['claims'] and set(after['work_ids'])-set(before['work_ids'])


def test_block3_same_card_window_ignores_wording_but_allows_new_question_and_offset():
    state,ids=comparison_state()
    args={'article_id':ids[0],'fields':['results'],'offset':0,'limit':4,'research_context':'Résultats'}
    first=Decision('get_analysis_claims',args,purpose='première formulation')
    key=semantic_key(state,first)
    claim={**state['articles'][ids[0]]['analysis']['results'][0],'field':'results'}
    from agents.EnnoScholarV5.workflow import claim_receipt
    state['v5_request_knowledge']={'request_id':workflow.current_request(state)['id'],'cards':{ids[0]:[claim_receipt('results',claim)]},
        'in_context_claims':{ids[0]:[claim_receipt('results',claim)]}}
    completed=[{'response':{'semantic_key':key,'output':{'article_id':ids[0],'claims':[claim],'next_offset':None}}}]
    repeat=Decision('get_analysis_claims',{**args,'research_context':'Autre formulation'},purpose='autres mots')
    _,output,reason=reusable(state,repeat,completed)
    assert output['reused_card'] and reason=='same_operation'
    state['v5_request_knowledge']['in_context_claims']={}
    assert reusable(state,repeat,completed)[1]['claims']==[claim]  # Dropped context: exact text can be consulted again.
    assert semantic_key(state,Decision('get_analysis_claims',{**args,'offset':4}))!=key
    state['messages'][-1]['id']='new-question'
    assert reusable(state,repeat,completed)[1] is None
