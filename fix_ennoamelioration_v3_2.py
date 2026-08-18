from __future__ import annotations

import shutil
import sys
from pathlib import Path


REPO = Path.cwd()

WRITER = (
    REPO
    / "agents"
    / "EnnoAmelioration"
    / "application"
    / "writer_service.py"
)

TEST_FILE = (
    REPO
    / "agents"
    / "EnnoAmelioration"
    / "tests"
    / "test_natural_language_cir_style_v3.py"
)


def fail(message: str) -> None:
    print(f"\n[V3.2][ERREUR] {message}")
    sys.exit(1)


def backup(path: Path) -> None:
    backup_path = path.with_suffix(
        path.suffix + ".before_editorial_review_v3_2.bak"
    )

    if not backup_path.exists():
        shutil.copy2(path, backup_path)
        print(
            f"[BACKUP] "
            f"{backup_path.relative_to(REPO)}"
        )


def replace_once(
    text: str,
    old: str,
    new: str,
    label: str,
) -> str:
    count = text.count(old)

    if count != 1:
        fail(
            f"{label}: motif attendu 1 fois, "
            f"trouvé {count} fois."
        )

    return text.replace(old, new, 1)


def patch_writer() -> None:
    if not WRITER.exists():
        fail(
            "writer_service.py introuvable. "
            "Lance le script depuis C:\\EnnoSmart."
        )

    backup(WRITER)

    text = WRITER.read_text(
        encoding="utf-8"
    )

    # =========================================================
    # 1. Ajouter difflib
    # =========================================================

    if "import difflib\n" not in text:
        if "import json\n" not in text:
            fail(
                "Impossible de trouver import json."
            )

        text = text.replace(
            "import json\n",
            "import difflib\nimport json\n",
            1,
        )

    # =========================================================
    # 2. Ajouter la fonction de similarité
    # =========================================================

    if (
        "def _editorial_text_similarity("
        not in text
    ):
        anchor = "\ndef _bounded_json_value("

        if anchor not in text:
            fail(
                "Impossible de trouver "
                "_bounded_json_value."
            )

        similarity_function = r'''

def _editorial_text_similarity(
    left: str,
    right: str,
) -> float:
    """
    Compare deux versions en ignorant seulement
    les différences d'espacement et de retours
    à la ligne.

    Cette fonction permet notamment de détecter
    qu'un contrôle sémantique est revenu presque
    mot pour mot au texte source.
    """

    left_value = re.sub(
        r"\s+",
        " ",
        normalize_llm_markdown_output(left),
    ).strip().casefold()

    right_value = re.sub(
        r"\s+",
        " ",
        normalize_llm_markdown_output(right),
    ).strip().casefold()

    return difflib.SequenceMatcher(
        a=left_value,
        b=right_value,
    ).ratio()

'''

        text = text.replace(
            anchor,
            similarity_function + anchor,
            1,
        )

    # =========================================================
    # 3. Renforcer le prompt du semantic scope review
    # =========================================================

    old_rule = (
        "7. Corrige seulement les dérives signalées ; "
        "conserve les améliorations de fluidité "
        "qui restent fidèles à la source.\n"
    )

    new_rules = (
        "7. Corrige seulement les dérives signalées ; "
        "conserve les améliorations de fluidité "
        "qui restent fidèles à la source.\n"
        "8. Ne retourne JAMAIS simplement le texte "
        "source mot pour mot pour supprimer un risque.\n"
        "9. Corrige uniquement les phrases contenant "
        "les dérives signalées.\n"
        "10. Toutes les autres améliorations "
        "rédactionnelles de la candidate doivent être "
        "conservées.\n"
        "11. Une correction de temporalité, "
        "d'intensité ou de portée doit rester locale : "
        "elle ne doit pas annuler le travail "
        "rédactionnel réalisé sur le reste de la "
        "section.\n"
    )

    if new_rules not in text:
        if old_rule not in text:
            fail(
                "Règle 7 du prompt introuvable."
            )

        text = text.replace(
            old_rule,
            new_rules,
            1,
        )

    # =========================================================
    # 4. Corriger l'acceptation du deuxième passage
    # =========================================================

    old_block = '''                if reviewed and len(reviewed_scope_risks) < len(semantic_scope_risks):
                    improved = reviewed
                    remaining_scope_risks = reviewed_scope_risks
                    second_meta = dict(self.llm.get_last_generation_meta() or {})
                    meta = _merge_generation_meta(first_meta, second_meta)
                    review_applied = True
'''

    new_block = '''                reviewed_vs_source = _editorial_text_similarity(
                    target,
                    reviewed,
                )

                first_candidate_vs_source = _editorial_text_similarity(
                    target,
                    improved,
                )

                review_reduces_risk = (
                    len(reviewed_scope_risks)
                    < len(semantic_scope_risks)
                )

                review_collapsed_to_source = (
                    reviewed_vs_source >= 0.995
                    and first_candidate_vs_source < 0.995
                )

                # Le contrôleur sémantique ne doit jamais
                # annuler toute l'amélioration simplement
                # pour revenir au texte source.
                #
                # La seconde version est acceptée uniquement
                # si elle réduit réellement les risques ET
                # reste une vraie version améliorée.
                if (
                    reviewed
                    and review_reduces_risk
                    and not review_collapsed_to_source
                ):
                    improved = reviewed

                    remaining_scope_risks = (
                        reviewed_scope_risks
                    )

                    second_meta = dict(
                        self.llm.get_last_generation_meta()
                        or {}
                    )

                    meta = _merge_generation_meta(
                        first_meta,
                        second_meta,
                    )

                    review_applied = True

                else:
                    # Le contrôleur est revenu trop près de
                    # l'original ou n'a pas réellement réduit
                    # les risques.
                    #
                    # On garde donc la PREMIÈRE vraie
                    # amélioration visible au consultant.
                    remaining_scope_risks = (
                        semantic_scope_risks
                    )

                    meta = first_meta
'''

    if (
        "review_collapsed_to_source = ("
        not in text
    ):
        if old_block not in text:
            fail(
                "Bloc semantic_scope_review "
                "V3.1 introuvable."
            )

        text = text.replace(
            old_block,
            new_block,
            1,
        )

    WRITER.write_text(
        text,
        encoding="utf-8",
    )

    print(
        f"[OK] "
        f"{WRITER.relative_to(REPO)}"
    )


def patch_tests() -> None:
    if not TEST_FILE.exists():
        fail(
            "test_natural_language_cir_style_v3.py "
            "introuvable."
        )

    backup(TEST_FILE)

    text = TEST_FILE.read_text(
        encoding="utf-8"
    )

    # =========================================================
    # Import de _editorial_text_similarity
    # =========================================================

    if (
        "_editorial_text_similarity,"
        not in text
    ):
        old_import = '''from agents.EnnoAmelioration.application.writer_service import (
    ControlledWriter,
    _compact_json,
    _editorial_semantic_scope_risks,
    _preserve_leading_heading,
)
'''

        new_import = '''from agents.EnnoAmelioration.application.writer_service import (
    ControlledWriter,
    _compact_json,
    _editorial_semantic_scope_risks,
    _editorial_text_similarity,
    _preserve_leading_heading,
)
'''

        if old_import not in text:
            fail(
                "Bloc import writer_service "
                "des tests introuvable."
            )

        text = text.replace(
            old_import,
            new_import,
            1,
        )

    # =========================================================
    # Tests supplémentaires
    # =========================================================

    marker = (
        "def "
        "test_semantic_review_must_not_"
        "collapse_to_original_source():"
    )

    if marker not in text:
        text += r'''


def test_editorial_similarity_ignores_whitespace_only():
    source = (
        "1.2.1. Contexte de l’opération\n"
        "Le radar est un système actif."
    )

    visually_wrapped = (
        "1.2.1. Contexte de l’opération\n\n"
        "Le radar est un système actif."
    )

    similarity = _editorial_text_similarity(
        source,
        visually_wrapped,
    )

    assert similarity >= 0.995


def test_semantic_review_must_not_collapse_to_original_source():
    source = (
        "1.2.1. Contexte de l’opération\n\n"
        "Les radars sont des systèmes basés sur "
        "l’émission et la réception d’ondes "
        "électromagnétiques. Cette technique est "
        "aujourd’hui employée dans des systèmes "
        "imageurs tels que RADARSAT, TerraSAR-X "
        "ou le futur système Tandem-L."
    )

    first_candidate = (
        "1.2.1. Contexte de l’opération\n\n"
        "Les radars reposent sur l’émission et la "
        "réception d’ondes électromagnétiques. "
        "Cette technique est intégrée dans plusieurs "
        "systèmes imageurs actuels, notamment "
        "RADARSAT, TerraSAR-X, ainsi que le futur "
        "système Tandem-L."
    )

    collapsed_review = source

    first_similarity = (
        _editorial_text_similarity(
            source,
            first_candidate,
        )
    )

    collapsed_similarity = (
        _editorial_text_similarity(
            source,
            collapsed_review,
        )
    )

    assert first_similarity < 0.995
    assert collapsed_similarity >= 0.995

    review_collapsed_to_source = (
        collapsed_similarity >= 0.995
        and first_similarity < 0.995
    )

    assert review_collapsed_to_source is True
'''

    TEST_FILE.write_text(
        text,
        encoding="utf-8",
    )

    print(
        f"[OK] "
        f"{TEST_FILE.relative_to(REPO)}"
    )


def main() -> None:
    print("=" * 72)
    print(
        "EnnoAmelioration V3.2 "
        "- anti-retour au texte source"
    )
    print(
        f"Repo détecté : {REPO}"
    )
    print("=" * 72)

    patch_writer()
    patch_tests()

    print(
        "\n[SUCCÈS] "
        "Correction V3.2 appliquée."
    )

    print(
        "\nLance maintenant :"
    )

    print(
        r"python -m pytest -q "
        r"agents\EnnoAmelioration\tests\test_editorial_only_v2_7.py "
        r"agents\EnnoAmelioration\tests\test_natural_language_cir_style_v3.py "
        r"agents\EnnoAmelioration\tests\test_general_scientific_research_v2_4.py"
    )

    print(
        "\nPuis redémarre le backend "
        "et refais exactement le même Test 1."
    )


if __name__ == "__main__":
    main()