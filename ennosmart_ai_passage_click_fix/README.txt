EnnoSmart — correction des citations [1] [2] [3] du Contrôle IA
=================================================================

Corrige le message :
"Document de la preuve X non résolu"

Aucun hardcoding Vecame / organisme / projet / document.

Le script modifie :
- frontend/components/ennosmart/source-documents-dialog.tsx
- backend_api/routers/source_highlight.py
- frontend/components/ennosmart/diagnosis-page.tsx

INSTALLATION

Copie les fichiers du ZIP dans C:\EnnoSmart puis :

cd C:\EnnoSmart
.\.venv\Scripts\Activate.ps1
python .\fix_ai_passage_click.py C:\EnnoSmart

Des backups sont créés avec :
.before-ai-click-fix

APRÈS

Redémarre le backend FastAPI et actualise/redémarre le frontend.
Clique sur [1], [2] ou [3].

La correction réutilise le même SourceDocumentDialog que les verrous.
Le backend retrouve la source via passage_id / extrait dans rag/chunks.json,
puis utilise source-highlight pour afficher et surligner le passage.

Normalement, il n'est pas nécessaire de relancer tout EnnoDiagnostic juste
pour tester le clic, tant que rag/chunks.json existe déjà.
