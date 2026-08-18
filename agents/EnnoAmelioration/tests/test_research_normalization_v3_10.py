from types import SimpleNamespace

from agents.EnnoAmelioration.application.research_text_normalizer import (
    normalize_research_section_text,
)
from agents.EnnoAmelioration.application.research_context_bridge_v310 import (
    enrich_direct_research_context,
)

SECTION = """
1.3.1.1. Donnée SAR publiquement disponibles

La base de données MSTAR3 (Moving and Stationary Target Acquisition and Recognition) publiquement disponible contient des mesures réelles SAR de dix cibles différentes (2S1, BMP2, BRDM2, BTR60, BTR70, D7, T72, T62, ZIL131, ZSU23) prises à différents angles de dépression et d’azimut par un radar aérien et collectées entre 1995 et 1997. La base contient de mesures prises sur 360° de gisement approximativement tous les degrés. Les 3671 images prises selon un angle de 17° de dépression sont traditionnellement utilisées pour l’entrainement, alors que les 3203 images à 15°

1 LeCun, Y., Kavukcuoglu, K., and Farabet, C. (2010). Convolutional networks and applications in vision. In Circuits and Systems (ISCAS), Proceedings of 2010 IEEE International Symposium on, pages 253–256. IEEE.

2 K. He, X. Zhang, S. Ren, and J. Sun, “Deep residual learning for image recognition,” CoRR, vol. abs/1512.03385, 2015.

3 https://www.sdms.afrl.af.mil/index.php?collection=mstar

de dépression sont utilisées pour le test4. Pour deux classes (BMP2 et T72), trois véhicules différents sont disponibles.

La base SAMPLE5 (Synthetic and Measured Paired Labeled Experiment) contient des paires d’images mesurées et simulées. Comparé à MSTAR, le secteur angulaire en gisement de SAMPLE est plus restreint car il s’étale uniquement sur l’intervalle [10°, 80°]. Les 806 images prises selon des angles de dépressions de 14°, 15° et 16° sont utilisées pour l’entrainement et les 539 images à 17° pour le test. Les données publiquement disponibles contiennent dix cibles différentes dont cinq sont communes à MSTAR. Pour ces cinq cibles, les mesures SAMPLE correspondent en fait à un sous-ensemble de MSTAR. Les mesures des autres cibles ont été prises en même temps que la campagne de mesure MSTAR et avec le même instrument. Les images synthétiques reproduisent en simulation les mesures en se plaçant au mêmes angles résolution échantillonnage que ces dernières. Les simulations ont été effectuées avec le simulateur XPatch de l’U.S. Air Force Research Laboratory (AFRL), c’est-à-dire le même laboratoire qui a effectué les mesures SAMPLE/MSTAR. Les véhicules ont été modélisés en se basant directement sur les vrais véhicules imagés. Les modèles CAO sont de plus configurés de la même manière que la réalité.
"""


def _direct():
    return {
        "research_context": {
            "research_objective": "MESSAGE CONSULTANT",
            "local_context": "AUTRE SECTION",
        },
        "research_targets": [{
            "research_target_id": "1.3.1.1",
            "title": "Donnée SAR publiquement disponibles",
            "raw_item": {
                "text": SECTION,
                "source_text": SECTION,
                "consultant_instruction": "MESSAGE CONSULTANT",
                "supporting_passages": [{"text": "AUTRE SECTION"}],
            },
        }],
    }


def _request():
    return SimpleNamespace(
        target_text=SECTION,
        target_section_title="Donnée SAR publiquement disponibles",
        target_section_id="1.3.1.1",
        instruction="Renforce scientifiquement cette section.",
    )


def test_normalizer_repairs_footnote_markers_but_preserves_target_codes():
    cleaned, report = normalize_research_section_text(SECTION)

    assert "MSTAR3" not in cleaned
    assert "SAMPLE5" not in cleaned
    assert "test4" not in cleaned

    assert "MSTAR (Moving and Stationary Target Acquisition and Recognition)" in cleaned
    assert "SAMPLE (Synthetic and Measured Paired Labeled Experiment)" in cleaned
    assert "pour le test." in cleaned

    for code in (
        "2S1", "BMP2", "BRDM2", "BTR60", "BTR70",
        "D7", "T72", "T62", "ZIL131", "ZSU23",
    ):
        assert code in cleaned

    assert "LeCun" not in cleaned
    assert "Deep residual learning" not in cleaned
    assert "sdms.afrl.af.mil" not in cleaned
    assert "ISCAS" not in cleaned
    assert "IEEE" not in cleaned

    assert report["removed_reference_blocks"] == 3
    repairs = {row["before"]: row["after"] for row in report["inline_marker_repairs"]}
    assert repairs["MSTAR3"] == "MSTAR"
    assert repairs["SAMPLE5"] == "SAMPLE"
    assert repairs["test4"] == "test"


def test_active_version_text_is_never_mutated():
    original = str(SECTION)
    cleaned, report = normalize_research_section_text(SECTION)

    assert SECTION == original
    assert cleaned != original
    assert report["active_version_modified"] is False
    assert report["semantic_rewriting"] is False


def test_primary_entities_are_datasets_not_instance_code_list():
    enriched = enrich_direct_research_context(
        _request(),
        _direct(),
        conversation_context={
            "base_version_id": "v2",
            "base_version_number": 2,
            "base_version_status": "accepted",
            "base_version_is_active": True,
        },
    )

    contract = enriched["v3_10_research_normalization_contract"]
    primary = contract["primary_entities"]

    assert "MSTAR" in primary
    assert "SAMPLE" in primary
    assert "SAR" in primary

    for code in ("BMP2", "BRDM2", "BTR60", "BTR70", "T72", "ZIL131", "ZSU23"):
        assert code not in primary

    target = enriched["research_targets"][0]
    assert "MSTAR3" not in target["text"]
    assert "SAMPLE5" not in target["text"]
    assert "LeCun" not in target["text"]


def test_queries_focus_on_main_entities_not_reference_noise_or_code_list():
    enriched = enrich_direct_research_context(
        _request(),
        _direct(),
    )

    queries = [
        row["query"]
        for row in enriched["v3_10_research_normalization_contract"]["queries"]
    ]

    assert queries
    assert any("MSTAR" in query for query in queries)
    assert any("SAMPLE" in query for query in queries)

    forbidden = (
        "MSTAR3", "SAMPLE5", "BMP2", "BRDM2", "BTR60",
        "BTR70", "ZIL131", "ZSU23", "ISCAS", "IEEE", "LeCun",
    )
    for query in queries:
        for token in forbidden:
            assert token not in query


def test_query_contains_expanded_dataset_identity():
    enriched = enrich_direct_research_context(
        _request(),
        _direct(),
    )

    queries = [
        row["query"]
        for row in enriched["v3_10_research_normalization_contract"]["queries"]
    ]

    mstar_query = next(query for query in queries if "MSTAR" in query)
    sample_query = next(query for query in queries if "SAMPLE" in query)

    assert "Moving" in mstar_query
    assert "Stationary" in mstar_query
    assert "Target" in mstar_query

    assert "Synthetic" in sample_query
    assert "Measured" in sample_query
    assert "Paired" in sample_query
