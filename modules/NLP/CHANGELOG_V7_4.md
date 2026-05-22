# Corrections finales V7.4.0

Objectif : garder l'architecture existante et ajouter les garde-fous universels nécessaires pour réduire le bruit.

Modules modifiés :
- final_taxonomy_mapper.py : titre officiel, brevets structurés, filtres objectifs/verrous/etat_art, personnes/organismes propres, termes sans fragments.
- technical_terms_extractor.py : filtres finaux entités/termes, suppression fragments tableau/parenthèses ouvertes, organismes/personnes plus stricts.
- evidence_validator.py : suppression double appel _should_stay_etat_art, reclassification état de l'art, rejet faux objectifs/verrous et brevet en etat_art.
- quality_reporter.py : score plus réaliste, pénalise faux objectifs, faux verrous, brevet dans etat_art, bruit entités et fragments techniques.
- router.py : propage title, conserve brevets en dict, donne priorité finale à final_taxonomy nettoyé.

Autres fichiers conservés tels que fournis.
