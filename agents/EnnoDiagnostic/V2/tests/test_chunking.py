from ennodiagnostic_v2.chunking import build_chunks
from ennodiagnostic_v2.config import ChunkConfig
from ennodiagnostic_v2.schemas import DocumentInput

def test_short_bullet_is_not_deleted():
    doc = DocumentInput(
        document_id="d1",
        name="test.txt",
        text=(
            "Contexte technique\n\n"
            "- Instabilité observée.\n"
            "- Résultat variable selon la configuration.\n\n"
            "Une analyse complémentaire est nécessaire pour comprendre le phénomène."
        ),
    )
    chunks = build_chunks(
        doc,
        ChunkConfig(max_chars=500, overlap_chars=50, context_chars=100, min_chunk_chars=20),
    )
    assert "Instabilité observée" in "\n".join(x.text for x in chunks)
