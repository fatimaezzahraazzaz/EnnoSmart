from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.runtime_pipeline import (
    build_runtime_pipeline,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput


text = """
L'objectif du projet est de développer un système capable de générer
automatiquement des tests unitaires Java à partir du code source.

La difficulté principale réside dans l'obtention de résultats reproductibles.
Pour un même code source, les modèles de langage peuvent produire des tests
différents et il n'est pas établi comment garantir leur déterminisme tout en
maintenant une couverture fonctionnelle suffisante.

Une expérimentation a été menée avec plusieurs modèles de langage.
La température a été fixée à 0,2 et chaque génération a été répétée cinq fois.

Les premiers essais montrent une couverture moyenne de 72 %, mais des écarts
importants persistent entre les différentes générations.
"""

pipeline = build_runtime_pipeline(run_frascati=False)

result = pipeline.run([
    DocumentInput(
        document_id="test_v2_001",
        name="test_semantique.txt",
        text=text,
        declared_mode="raw",
    )
])

print("\n=== EXTRACTIONS V2 ===")

for item in result.extractions:
    print(
        "\nTYPE:", item.kind,
        "\nSTATEMENT:", item.statement,
        "\nPREUVE:", item.evidence.quote,
        "\nPREUVE VERIFIEE:", item.evidence.quote_verified,
        "\nEXPLICITE:", item.explicitness,
        "\nA VALIDER:", item.needs_review,
    )

print("\n=== ENTITES APRES FUSION ===")
for entity in result.entities:
    print(entity.kind, "=>", entity.statement)

print("\n=== AUDIT ===")
print(result.audit)
