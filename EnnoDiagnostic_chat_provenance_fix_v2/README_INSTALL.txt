EnnoDiagnostic Chat RAG - provenance passage par passage V2

Fichiers modifiés :
- modules/RAG/diagnostic_chat_service.py
- modules/RAG/retriever.py
- modules/RAG/vector_store.py

Aucun fichier de l'agent EnnoDiagnostic, des verrous, démarches, résultats ou scores n'est modifié.
Aucun nom de projet, modèle, verrou, méthode ou score propre à AI-CODE n'est codé en dur.

Installation PowerShell :
cd C:\EnnoSmart
python "<DOSSIER_DEZIPPE>\install_chat_provenance_fix_v2.py" --repo "C:\EnnoSmart"
python "<DOSSIER_DEZIPPE>\verify_chat_provenance_fix_v2.py" --repo "C:\EnnoSmart"

Ensuite :
1. Redémarrer uniquement le backend.
2. Ne pas relancer EnnoDiagnostic.
3. Au premier message du chat, le companion raw index est automatiquement reconstruit car CHAT_SERVICE_VERSION a changé.
4. Rejouer les deux questions de test fournies dans la conversation.
