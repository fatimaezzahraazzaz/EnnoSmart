"""Conversation memory policy, separate from scientific discovery instructions."""

CONVERSATION_INSTRUCTION = """Priorité conversationnelle à la mémoire de session :
si la demande revient explicitement sur les résultats déjà trouvés (« parmi les articles
que tu viens de trouver », « ces articles », « les précédents », ou une formulation
équivalente), raisonne d'abord à partir des notices et analyses présentes dans la
mémoire serveur et de l'historique. Ne recommence pas une recherche par titre pour
retrouver une notice déjà disponible. Si un détail stocké manque dans la mémoire
active, consulte get_articles en batch plutôt que search_scientific.
Pour ce follow-up, une nouvelle search_scientific se justifie seulement si une
information essentielle manque réellement dans les données disponibles, si l'utilisateur
demande explicitement d'autres articles, ou si tu identifies une lacune scientifique
nécessitant de nouvelles sources. C'est à toi d'apprécier ce besoin selon la demande
et les données, sans règle de mots-clés ni recherche obligatoire. Si ces données
suffisent, réponds directement à la nouvelle question avec leurs preuves et limites.
Cette priorité ne limite pas une nouvelle recherche explicitement demandée."""
