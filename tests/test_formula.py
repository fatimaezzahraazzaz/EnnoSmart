import logging
from modules.extraction.formula.formula import extract_formulas

# Configuration pour voir les logs d'Ollama
logging.basicConfig(level=logging.INFO)

def test_special_symbols():
    print("🧪 Test des symboles spéciaux (Σ, π, ρ) pour EnnoSmart\n")

    # On définit des cas avec des symboles grecs et des notations de somme
    test_cases = [
        {
            "nom": "SOMME MATHÉMATIQUE",
            "text": """Le calcul de la moyenne pondérée est défini par :
            S = Σ (xi * wi) / Σ wi
            pour i allant de 1 à n."""
        },
        {
            "nom": "GÉOMÉTRIE (PI)",
            "text": "La surface du cercle de rayon r est donnée par la formule A = π * r²."
        },
        {
            "nom": "PHYSIQUE (RHO / DENSITÉ)",
            "text": """Pour calculer la masse volumique du fluide dans le réservoir :
            ρ = m / V
            où ρ (rho) représente la densité en kg/m³."""
        },
        {
            "nom": "MIXTE (PI & SOMME)",
            "text": "Une approximation de la valeur est Σ (1/n²) = π² / 6."
        }
    ]

    for case in test_cases:
        print(f"--- Exécution : {case['nom']} ---")
        try:
            # Appel de ta fonction principale
            results = extract_formulas(text=case['text'])
            
            if not results:
                print("⚠️ Aucun symbole ou formule détecté.")
            
            for i, res in enumerate(results, 1):
                print(f"[Capture {i}]")
                print(f"Domaine    : {res.domain}")
                print(f"LaTeX      : {res.latex}")
                print(f"Explication: {res.explanation}")
                print("-" * 30)
        except Exception as e:
            print(f"❌ Erreur sur {case['nom']} : {e}")
        print("\n")

if __name__ == "__main__":
    test_special_symbols()