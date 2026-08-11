# EnnoAmelioration V2.4 — recherche scientifique typée

## Principe

Une recherche EnnoScholar n'exige plus systématiquement un verrou
EnnoDiagnostic. EnnoAmel choisit entre deux contrats :

- `research_targets` pour enrichir un contexte, un état de l'art, une méthode,
  un paramètre, un résultat, une limite, une contribution ou une synthèse ;
- `verrous` lorsque la cible est une incertitude ou une qualification CIR qui
  nécessite réellement EnnoDiagnostic.

## Recherche générale

`EnnoAmel -> LightweightResearchContext -> EnnoScholar`

Le contexte léger contient uniquement le texte source, le contexte local, la
fonction de section, l'objectif consultant, l'année et le résultat du
`domain_classifier`. Il n'exécute ni Frascati, ni RAG, ni EnnoDiagnostic et ne
génère aucun mot-clé.

EnnoScholar reste responsable de :

- l'objet technique et les phénomènes ;
- les méthodes et contraintes ;
- les ancres fortes et acronymes ;
- les mots-clés français/anglais ;
- les requêtes et le classement des articles.

## Recherche liée à un verrou

`EnnoAmel -> EnnoDiagnostic scoped -> EnnoScholar`

Cette voie reste utilisée pour une vraie incertitude, un verrou ou une demande
de qualification CIR. Si le diagnostic échoue alors qu'une nouvelle recherche
bibliographique a été explicitement demandée, EnnoAmel peut poursuivre avec un
`research_target` construit uniquement depuis le texte source. Aucun faux
verrou n'est créé.

## Validation humaine

Les publications trouvées restent candidates. Elles doivent être validées ou
rejetées avant toute rédaction qui ajoute de nouvelles affirmations
scientifiques.
