"""
test_gliner_debug.py — Diagnostic GLiNER direct
Usage: python test_gliner_debug.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

print("Vérification installation GLiNER...")
try:
    from gliner import GLiNER
    print("✅ GLiNER importé")
except ImportError as e:
    print(f"❌ GLiNER non installé : {e}")
    print("   → pip install gliner")
    sys.exit(1)

print("Chargement modèle urchade/gliner_multi-v2.1...")
try:
    model = GLiNER.from_pretrained("urchade/gliner_multi-v2.1")
    print("✅ Modèle chargé")
except Exception as e:
    print(f"❌ Erreur chargement : {e}")
    sys.exit(1)

# Texte de test
text = """La verrou ici réside dans la complexité à mettre en oeuvre
la fusion des mesures issues d'un récepteur GNSS, d'un Lidar et
d'une centrale inertielle. Les filtres de Kalman et les graphes 
de facteurs. GPS RTK. SLAM. GTSAM. L'entreprise CEVAA travaille
sur ce projet CIR. Kimberlie EKOMBA, ingénieur R&D, a réalisé 
2.5 ETP de recherche en 2023."""

# Labels qu'on passe à GLiNER
labels = [
    "verrou technologique", "technologie", "algorithme",
    "organisme de recherche", "chercheur", "ingénieur R&D",
    "projet de recherche", "domaine scientifique",
    "financement R&D", "brevet", "matériau",
]

print(f"\nTest sur texte de {len(text)} chars avec {len(labels)} labels...")
results = model.predict_entities(text, labels, threshold=0.3)

print(f"\n✅ {len(results)} entités trouvées (seuil=0.3) :")
for e in results:
    print(f"  [{e['label']}] {e['text']!r}  score={e['score']:.3f}")

if not results:
    print("  ❌ Aucune entité — le modèle ne reconnaît rien sur ce texte")
    print("  Essai avec seuil plus bas (0.1)...")
    results2 = model.predict_entities(text, labels, threshold=0.1)
    print(f"  {len(results2)} entités à seuil=0.1 :")
    for e in results2[:10]:
        print(f"    [{e['label']}] {e['text']!r}  score={e['score']:.3f}")