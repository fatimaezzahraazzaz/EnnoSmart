import sys
import os
import json

# Ajoute le dossier racine au path pour que Python trouve le dossier 'modules'
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from modules.extraction.pdf_native import PDFNativeExtractor

def run_test():
    # 1. Configuration du fichier de test
    pdf_filename = "DT_CIR_OFFROAD_Navigation_2023_VF.pdf" 
    input_path = os.path.join("data", "raw_documents", pdf_filename)
    
    if not os.path.exists(input_path):
        print(f"❌ Erreur : Le fichier {input_path} n'existe pas.")
        print(f"Vérifie la présence du PDF dans : C:\\EnnoSmart\\{input_path}")
        return

    print(f"\n🚀 [ENNOSMART] Lancement de l'extraction R&D : {pdf_filename}")
    print("="*60)
    
    # 2. Initialisation de l'extracteur
    extractor = PDFNativeExtractor()
    
    try:
        # 3. Extraction
        results = extractor.extract(input_path)
        
        print(f"✅ Extraction réussie : {len(results)} blocs détectés.\n")
        
        # 4. Affichage détaillé des résultats
        print("--- APERÇU DES 10 PREMIERS BLOCS ---")
        for i, block in enumerate(results[:10]):
            b_type = block['type'].upper()
            page = block['page']
            bbox = [round(v, 1) for v in block['bbox']] # Arrondi pour la lisibilité
            
            # Entête du bloc
            print(f"🔹 [BLOC {i}] | PAGE {page} | TYPE: {b_type}")
            print(f"📍 BBox (x0, top, x1, bottom): {bbox}")
            
            # Affichage selon le type
            if b_type == "TABLE":
                print(f"📊 Structure: {block['metadata']['rows']} lignes x {block['metadata']['cols']} colonnes")
                # Affiche la première ligne du tableau (souvent les headers)
                if block['text']:
                    print(f"   Contenu (Haut): {block['text'][0]}")
            
            else:
                # Texte classique, Bullet ou Header
                text_preview = block['text'][:120] + "..." if len(block['text']) > 120 else block['text']
                font_info = f"{block['metadata']['font_name']} ({block['metadata']['font_size']}pt)"
                is_bold = "GRAS" if block['metadata']['is_bold'] else "Normal"
                
                print(f"   Fonte: {font_info} | Style: {is_bold}")
                print(f"   Texte: {text_preview}")
            
            print("-" * 60)

        # 5. Export de contrôle (Optionnel - utile pour vérifier le JSON complet)
        with open("tests/last_result.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=4)
        print("\n💾 Résultat complet sauvegardé dans 'tests/last_result.json'")

    except Exception as e:
        print(f"❌ Une erreur est survenue durant le test : {e}")
        # Affiche la ligne de l'erreur pour débugger plus vite
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    run_test()