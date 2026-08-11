# EnnoAmelioration V2.3 — Scoped CIR Diagnostic Orchestration

## Correction principale

V2.2 appelait le pipeline backend `run_ennodiagnostic(db, project)` lorsqu'un
contexte Diagnostic manquait. Ce pipeline reconstruit et analyse les documents
bruts attachés au projet. Or une section/CIR chargé directement dans EnnoAmel
peut ne pas exister dans ces documents.

V2.3 change le périmètre :

1. EnnoAmel prend `request.target_text` comme source primaire.
2. Si disponible, un contexte local autour de la section est extrait de
   `request.full_text` (le CIR courant chargé dans EnnoAmel).
3. Ces textes deviennent des documents virtuels `pre_cir_client`.
4. Le vrai `run_nlp_pipeline_routed` est exécuté sur ce corpus uniquement.
5. Le résultat est indexé dans un ProjectStore/RAG isolé et stable par hash.
6. Le vrai `EnnoDiagnosticAgent.generate_diagnostic()` reformule les signaux.
7. EnnoAmel sélectionne le verrou lié à la section.
8. EnnoScholar reçoit ce verrou + domaine NLP pour sa recherche.

Les documents bruts PostgreSQL du projet ne sont jamais reconstruits dans ce
flux. Un diagnostic projet existant peut être lu comme contexte secondaire,
mais ses verrous ne sont pas injectés dans la recherche ciblée.

## Log attendu

`[EnnoAmel][ScopedDiagnostic] ... source=ennoamel_current_cir ... project_raw_documents_used=0`

Il ne doit plus apparaître, pour ce flux EnnoAmel :

`[prepare-sources][1/6] Reconstruction des documents`
