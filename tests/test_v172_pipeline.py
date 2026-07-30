from modules.NLP.candidates import classify_candidate
from modules.NLP.evidence_graph import build_technical_lock_groups
from modules.NLP.frascati_assessment import assess_project_frascati


def fake_encode(texts):
    vectors=[]
    for text in texts:
        t=text.lower()
        vectors.append([
            float("vibration" in t or "equilibrage" in t),
            float("bruit" in t or "acoust" in t),
            float("temperature" in t or "refroid" in t),
            float("segment" in t or "usure" in t),
        ])
    return vectors


def item(pid, text, role, features, score=.45, document="d"):
    return {
        "passage_id": pid,
        "text": text,
        "analysis_text": text,
        "semantic_role": role,
        "lock_candidate_features": features,
        "lock_candidate_score": score,
        "document": document,
    }


def test_supporting_evidence_is_kept():
    decision=classify_candidate(item("p1", "Vibration très forte à 1030 tr/min", "limite", {"technical": True}, .34))
    assert decision.supporting_lock_evidence is True
    assert decision.direct_lock_candidate is False


def test_cross_document_grouping_and_no_frascati_filter():
    candidates=[
        item("p1", "Equilibrage non réalisable à haute vitesse, vibration importante", "limite", {"technical": True, "open_validation": True}, .62, "a.pdf"),
        item("p2", "Le contrepoids modifie le niveau de vibration à 1030 tr/min", "parametre", {"technical": True, "dependency": True}, .48, "b.xlsx"),
        item("p3", "Le bruit reste élevé et les pics harmoniques doivent être analysés", "resultat", {"technical": True, "open_validation": True}, .58, "c.pdf"),
    ]
    report=build_technical_lock_groups(candidates, encode_texts=fake_encode, minimum_complete_link_score=.40)
    assert report["coverage"]["coverage_rate"] == 1.0
    assert sum(len(g["supporting_passages"]) for g in report["groups"]) == 3
    fr=assess_project_frascati(report["groups"])
    assert len(fr["group_assessments"]) == len(report["groups"])
    assert fr["decision"] is None
    assert fr["human_validation_required"] is True


def test_methodological_assumption_is_not_deleted():
    candidates=[item("p1", "L'écoulement est considéré isotherme pour la simulation", "parametre", {"technical": True}, .52)]
    report=build_technical_lock_groups(candidates, encode_texts=fake_encode)
    assert len(report["groups"]) == 1
    fr=assess_project_frascati(report["groups"])
    assert len(fr["group_assessments"]) == 1
