from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_uncertainty_policy_separates_project_and_scholar_evidence():
    text = (ROOT / "application" / "section_improvement_policy.py").read_text(encoding="utf-8")
    assert "Une source Scholar ne peut pas prouver qu'un événement s'est produit dans le projet" in text
    assert "un protocole expérimental détaillé" in text
    assert "augmentation standard" in text


def test_writer_has_uncertainty_evidence_mode():
    text = (ROOT / "application" / "writer_service.py").read_text(encoding="utf-8")
    assert "uncertainty_evidence_mode" in text
    assert '"uncertainty_evidence"' in text
    assert "MODE VERROU / INCERTITUDE — ARGUMENTATION ÉTAYÉE" in text


def test_writer_does_not_authorize_unproved_standard_method_failures():
    text = (ROOT / "application" / "writer_service.py").read_text(encoding="utf-8")
    assert "n'invente jamais l'échec de méthodes standards" in text
    assert "N'ajoute pas spontanément des exemples de solutions prétendument insuffisantes" in text


def test_writer_bans_guarantee_language_for_uncertainty():
    text = (ROOT / "application" / "writer_service.py").read_text(encoding="utf-8")
    assert "robustesse garantie" in text
    assert "efficacité en conditions réelles" in text
