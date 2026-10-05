from ennodiagnostic_v2.config import FusionConfig
from ennodiagnostic_v2.fusion import fuse_items
from ennodiagnostic_v2.schemas import EvidenceRef, ExtractionItem

def item(i, doc, statement, quote):
    return ExtractionItem(
        item_id=i,
        kind="lock",
        statement=statement,
        evidence=EvidenceRef(
            evidence_id=f"ev_{i}",
            document_id=doc,
            document_name=f"{doc}.pdf",
            chunk_id=f"ch_{i}",
            quote=quote,
            quote_verified=True,
        ),
        explicitness="implicit",
    )

def test_merge_keeps_both_documents():
    items = [
        item("1","d1",
             "La stabilité géométrique n'est pas maîtrisée avec des données clairsemées",
             "La stabilité se dégrade lorsque la densité des points diminue."),
        item("2","d2",
             "La stabilité géométrique reste difficile à maîtriser avec des données clairsemées",
             "Avec des données clairsemées, la géométrie obtenue reste instable."),
    ]
    entities = fuse_items(items, FusionConfig())
    assert len(entities) == 1
    assert sorted(entities[0].document_ids) == ["d1", "d2"]
    assert len(entities[0].evidences) == 2
