"""
modules/RAG/retriever.py — EnnoSmart RAG v1.1 ChromaDB + EnnoAmel
──────────────────────────────────────────────────────────────────────────────
Retriever metadata-aware pour EnnoSmart.

Rôle :
  - prendre une question utilisateur ;
  - l'embedder ;
  - chercher dans VectorStore ChromaDB ;
  - reranker les chunks avec bonus metadata/entities ;
  - fournir un contexte propre pour le LLM / EnnoAmel.

Architecture :
  Extraction → NLP → RAG → EnnoAmel Orchestrateur

Pourquoi metadata-aware ?
  Le NLP produit des métadonnées riches :
    - verrous_techniques
    - methodes_rd
    - outils_technologies
    - modeles_algorithmes
    - normes_techniques
    - materiaux_composants
    - entities

  Ces métadonnées doivent influencer le ranking, pas seulement le score vectoriel.

Usage :
  from modules.RAG.retriever import Retriever

  retriever = Retriever(store, embedder)
  results = retriever.search("Quels sont les verrous techniques ?")
  context = retriever.format_context(results)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_MIN_SCORE = -1.0
DEFAULT_TOP_K = 5
DEFAULT_FETCH_MULTIPLIER = 8


# Champs importants produits par ton router NLP.
RAG_IMPORTANT_METADATA_FIELDS = [
    # Clés métier de partition client / organisme.
    # Elles doivent rester visibles dans le contexte RAG et les sources.
    "organisme_name",
    "organisme_id",

    "mots_cles_high_confidence",
    "mots_cles_candidates",
    "objet_recherche",
    "sous_domaines",
    "verrous_techniques",
    "objectifs_rd",
    "hypotheses_rd",
    "methodes_rd",
    "protocoles_experimentaux",
    "outils_technologies",
    "modeles_algorithmes",
    "architectures_systeme",
    "jeux_donnees_benchmarks",
    "metriques_evaluation",
    "parametres_variables",
    "normes_techniques",
    "materiaux_composants",
    "limitations_perspectives",
    "resultats_rd",
    "technologies",
    "entities",
]


INTENT_KEYWORDS = {
    "summary": [
        "résume", "resume", "idée", "idee", "présente", "presente",
        "de quoi parle", "synthèse", "synthese", "vue globale",
    ],
    "eligibility": [
        "éligible", "eligible", "cir", "score", "risque",
        "verrou", "incertitude", "qualification", "diagnostic",
    ],
    "scholar": [
        "état de l'art", "etat de l'art", "article", "publication",
        "bibliographie", "citation", "gap", "scientifique",
    ],
    "valor": [
        "finance", "financier", "rh", "dépense", "depense",
        "cerfa", "excel", "montant", "personnel", "etp",
    ],
    "tools": [
        "outil", "outils", "technologie", "technologies",
        "logiciel", "framework", "simulateur", "plateforme",
    ],
    "methods": [
        "méthode", "methode", "approche", "protocole",
        "démarche", "demarche", "simulation", "modélisation",
        "modelisation", "prompting",
    ],
    "metrics": [
        "métrique", "metrique", "résultat", "resultat",
        "performance", "score", "couverture", "coverage",
        "tension", "courant", "mtbf", "temps",
    ],
    "components": [
        "composant", "composants", "matériau", "materiau",
        "condensateur", "transistor", "mosfet", "jfet",
        "diode", "résistance", "resistance",
    ],
    "standards": [
        "norme", "normes", "standard", "iec", "iso", "nf en",
        "rohs", "reach",
    ],
}


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _restore_json_if_needed(value: Any) -> Any:
    """
    Les métadonnées peuvent venir de ChromaDB sous forme de strings JSON.
    On restaure listes/dicts si possible.
    """
    if not isinstance(value, str):
        return value

    text = value.strip()

    if not text:
        return value

    if not (
        (text.startswith("[") and text.endswith("]"))
        or (text.startswith("{") and text.endswith("}"))
    ):
        return value

    try:
        return json.loads(text)
    except Exception:
        return value


def _restore_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        k: _restore_json_if_needed(v)
        for k, v in (metadata or {}).items()
    }


def _value_to_text(value: Any) -> str:
    """
    Convertit metadata/entities en texte searchable.
    """
    value = _restore_json_if_needed(value)

    if value is None:
        return ""

    if isinstance(value, str):
        return value

    if isinstance(value, (int, float, bool)):
        return str(value)

    if isinstance(value, list):
        return " ".join(_value_to_text(v) for v in value)

    if isinstance(value, dict):
        # Cas entité : {"text": "...", "type": "..."}
        if "text" in value:
            text = str(value.get("text", "") or "")
            typ = str(value.get("type", "") or "")
            status = str(value.get("status", "") or "")
            return " ".join(x for x in [typ, text, status] if x)

        return " ".join(
            f"{k} {_value_to_text(v)}"
            for k, v in value.items()
        )

    return str(value)


def _metadata_text(metadata: dict[str, Any]) -> str:
    meta = _restore_metadata(metadata or {})
    parts = []

    for field in RAG_IMPORTANT_METADATA_FIELDS:
        if field in meta:
            value = _value_to_text(meta.get(field))
            if value:
                parts.append(f"{field}: {value}")

    # On ajoute aussi des champs documentaires utiles.
    for field in [
        "organisme_name",
        "organisme_id",
        "file_name",
        "domaine_principal",
        "source_type",
        "chunk_source_type",
    ]:
        if field in meta:
            value = _value_to_text(meta.get(field))
            if value:
                parts.append(f"{field}: {value}")

    return "\n".join(parts)


def _extract_query_terms(query: str) -> list[str]:
    """
    Extrait des termes utiles depuis la question.
    """
    q = _norm(query)

    stopwords = {
        "quel", "quels", "quelle", "quelles", "donne", "moi",
        "dans", "avec", "pour", "sont", "est", "les", "des",
        "une", "sur", "par", "aux", "leur", "leurs", "plus",
        "projet", "document", "dossier", "utilisé", "utilises",
        "utilisée", "utilisees", "utilisés",
    }

    terms = [
        t for t in re.findall(r"[\wÀ-ÿ\-\+\.]+", q)
        if len(t) >= 3 and t not in stopwords
    ]

    # Garder ordre + unique.
    seen = set()
    out = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)

    return out


def _detect_light_intent(query: str) -> str:
    """
    Détection légère d'intention côté retriever.
    L'orchestrateur aura aussi son intent_router, mais ici on s'en sert
    seulement pour donner des bonus de ranking.
    """
    q = _norm(query)

    best_intent = "qa"
    best_score = 0

    for intent, keywords in INTENT_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in q)
        if score > best_score:
            best_score = score
            best_intent = intent

    return best_intent


def detect_intent(query: str) -> str:
    """
    Alias public utilisé par query_engine.py.

    Garde la compatibilité avec :
        from modules.RAG.retriever import detect_intent

    La logique reste exactement celle de _detect_light_intent().
    """
    return _detect_light_intent(query)


def _normalize_filter_meta(filter_meta: Optional[dict]) -> Optional[dict]:
    """
    Normalise les filtres envoyés au VectorStore.

    Règle importante :
      - organisme_id est une clé de séparation client ;
      - on la garde comme string exacte ;
      - les autres filtres restent libres et peuvent être traités en contains
        par VectorStore.
    """
    if not filter_meta:
        return None

    normalized: dict[str, Any] = {}

    for key, value in (filter_meta or {}).items():
        if value is None:
            continue

        k = str(key).strip()
        if not k:
            continue

        if k in {"organisme_id", "organisme_name", "file_name", "domaine_principal"}:
            v = str(value).strip()
            if v:
                normalized[k] = v
        else:
            normalized[k] = value

    return normalized or None


class Retriever:
    """
    Recherche sémantique metadata-aware sur le VectorStore ChromaDB.

    Fonctionnalités :
      - recherche vectorielle ;
      - filtre metadata souple ;
      - bonus metadata/entities ;
      - déduplication ;
      - formatage du contexte pour le LLM.
    """

    def __init__(
        self,
        store,           # VectorStore ChromaDB
        embedder,        # Embedder
        top_k: int = DEFAULT_TOP_K,
        min_score: float = DEFAULT_MIN_SCORE,
        fetch_multiplier: int = DEFAULT_FETCH_MULTIPLIER,
        metadata_bonus: float = 0.08,
        entity_bonus: float = 0.10,
        intent_bonus: float = 0.12,
    ):
        self.store = store
        self.embedder = embedder
        self.top_k = top_k
        self.min_score = min_score
        self.fetch_multiplier = fetch_multiplier
        self.metadata_bonus = metadata_bonus
        self.entity_bonus = entity_bonus
        self.intent_bonus = intent_bonus

    def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        min_score: Optional[float] = None,
        filter_meta: Optional[dict] = None,
        filter_mode: str = "contains",
        deduplicate: bool = True,
        intent: Optional[str] = None,
    ) -> list[dict]:
        """
        Recherche les chunks les plus pertinents pour une question.
        """
        k = top_k or self.top_k
        threshold = min_score if min_score is not None else self.min_score

        if not query or not query.strip():
            return []

        if self.store.is_empty:
            logger.warning("VectorStore vide. Aucun résultat possible.")
            return []

        query_clean = query.strip()
        query_vec = self.embedder.embed_query(query_clean)

        detected_intent = intent or _detect_light_intent(query_clean)

        normalized_filter = _normalize_filter_meta(filter_meta)

        raw_results = self.store.search(
            query_vector=query_vec,
            top_k=k,
            filter_meta=normalized_filter,
            filter_mode=filter_mode,
            fetch_multiplier=self.fetch_multiplier,
        )

        reranked = []

        for r in raw_results:
            chunk = r.get("chunk", {}) or {}
            metadata = _restore_metadata(r.get("metadata") or chunk.get("metadata", {}) or {})
            content = str(r.get("content") or chunk.get("content", "") or "")

            bonus_details = self._compute_bonus(
                query=query_clean,
                content=content,
                metadata=metadata,
                intent=detected_intent,
            )

            base_score = float(r.get("score", 0.0))
            bonus = float(bonus_details["total_bonus"])
            final_score = base_score + bonus

            if final_score < threshold:
                continue

            enriched = dict(r)
            enriched["chunk"] = {
                **chunk,
                "content": content,
                "metadata": metadata,
                "chunk_id": r.get("chunk_id") or chunk.get("chunk_id"),
            }
            enriched["metadata"] = metadata
            enriched["content"] = content
            enriched["score"] = base_score
            enriched["metadata_bonus"] = bonus
            enriched["bonus_details"] = bonus_details
            enriched["final_score"] = final_score
            enriched["query"] = query_clean
            enriched["intent"] = detected_intent

            reranked.append(enriched)

        reranked.sort(key=lambda x: x["final_score"], reverse=True)

        if deduplicate:
            reranked = self._deduplicate(reranked)

        reranked = reranked[:k]

        for i, r in enumerate(reranked):
            r["rank"] = i + 1

        logger.debug(
            "Retriever [%s] intent=%s filter=%s → %d résultats",
            query_clean[:60],
            detected_intent,
            normalized_filter,
            len(reranked),
        )

        return reranked

    def search_multi(
        self,
        queries: list[str],
        top_k: Optional[int] = None,
        min_score: Optional[float] = None,
        filter_meta: Optional[dict] = None,
        filter_mode: str = "contains",
        intent: Optional[str] = None,
    ) -> list[dict]:
        """
        Recherche multi-requêtes.
        """
        k = top_k or self.top_k
        threshold = min_score if min_score is not None else self.min_score

        all_results: dict[str, dict] = {}

        for query in queries:
            results = self.search(
                query=query,
                top_k=k,
                min_score=threshold,
                filter_meta=filter_meta,
                filter_mode=filter_mode,
                deduplicate=False,
                intent=intent,
            )

            for r in results:
                chunk = r.get("chunk", {}) or {}
                chunk_id = (
                    r.get("chunk_id")
                    or chunk.get("chunk_id")
                    or chunk.get("metadata", {}).get("chunk_id")
                    or str(hash(chunk.get("content", "")))
                )

                if (
                    chunk_id not in all_results
                    or r.get("final_score", r.get("score", 0)) > all_results[chunk_id].get("final_score", all_results[chunk_id].get("score", 0))
                ):
                    all_results[chunk_id] = r

        merged = list(all_results.values())
        merged.sort(key=lambda r: r.get("final_score", r.get("score", 0)), reverse=True)
        merged = merged[:k]

        for i, r in enumerate(merged):
            r["rank"] = i + 1

        return merged

    def _compute_bonus(
        self,
        query: str,
        content: str,
        metadata: dict[str, Any],
        intent: str,
    ) -> dict[str, Any]:
        """
        Calcule des bonus simples et explicables.
        """
        c = _norm(content)
        meta_text = _norm(_metadata_text(metadata))
        terms = _extract_query_terms(query)

        total = 0.0
        details: dict[str, Any] = {
            "term_matches_metadata": [],
            "term_matches_content": [],
            "intent_matches": [],
            "entity_match": False,
            "total_bonus": 0.0,
        }

        # 1. Termes de la question dans metadata.
        for term in terms:
            if term in meta_text:
                details["term_matches_metadata"].append(term)
                total += self.metadata_bonus

        # 2. Termes de la question dans content.
        for term in terms:
            if term in c:
                details["term_matches_content"].append(term)
                total += 0.03

        # 3. Bonus entities.
        entities_text = _norm(_value_to_text(metadata.get("entities")))
        if entities_text:
            for term in terms:
                if term in entities_text:
                    details["entity_match"] = True
                    total += self.entity_bonus
                    break

        # 4. Bonus par intention.
        intent_fields = self._fields_for_intent(intent)
        for field in intent_fields:
            value_text = _norm(_value_to_text(metadata.get(field)))
            if value_text:
                details["intent_matches"].append(field)
                total += self.intent_bonus

        # Limiter pour éviter que le bonus écrase complètement le vectoriel.
        total = min(total, 0.45)
        details["total_bonus"] = round(total, 4)

        # Dédupliquer détails.
        details["term_matches_metadata"] = sorted(set(details["term_matches_metadata"]))
        details["term_matches_content"] = sorted(set(details["term_matches_content"]))
        details["intent_matches"] = sorted(set(details["intent_matches"]))

        return details

    @staticmethod
    def _fields_for_intent(intent: str) -> list[str]:
        """
        Champs metadata à favoriser selon l'intention.
        """
        intent = intent or "qa"

        if intent == "summary":
            return [
                "objet_recherche",
                "mots_cles_high_confidence",
                "objectifs_rd",
                "verrous_techniques",
                "methodes_rd",
            ]

        if intent == "eligibility":
            return [
                "verrous_techniques",
                "objectifs_rd",
                "methodes_rd",
                "resultats_rd",
                "metriques_evaluation",
                "limitations_perspectives",
            ]

        if intent == "scholar":
            return [
                "verrous_techniques",
                "sous_domaines",
                "modeles_algorithmes",
                "methodes_rd",
                "jeux_donnees_benchmarks",
            ]

        if intent == "valor":
            return [
                "parametres_variables",
                "metriques_evaluation",
                "resultats_rd",
                "normes_techniques",
            ]

        if intent == "tools":
            return [
                "outils_technologies",
                "technologies",
                "entities",
            ]

        if intent == "methods":
            return [
                "methodes_rd",
                "protocoles_experimentaux",
                "modeles_algorithmes",
                "architectures_systeme",
            ]

        if intent == "metrics":
            return [
                "metriques_evaluation",
                "parametres_variables",
                "resultats_rd",
            ]

        if intent == "components":
            return [
                "materiaux_composants",
                "architectures_systeme",
                "entities",
            ]

        if intent == "standards":
            return [
                "normes_techniques",
                "parametres_variables",
                "entities",
            ]

        return [
            "mots_cles_high_confidence",
            "objet_recherche",
            "entities",
        ]

    def _deduplicate(self, results: list[dict]) -> list[dict]:
        """
        Supprime les résultats dont le contenu est quasiment identique.
        Garde le meilleur score final.
        """
        kept = []
        seen_content: list[str] = []

        for r in results:
            chunk = r.get("chunk", {}) or {}
            content = str(chunk.get("content", "") or r.get("content", "") or "")
            content_normalized = re.sub(r"\s+", " ", content.lower().strip())[:300]

            is_duplicate = any(
                self._content_similarity(content_normalized, seen) > 0.88
                for seen in seen_content
            )

            if not is_duplicate:
                kept.append(r)
                seen_content.append(content_normalized)

        return kept

    @staticmethod
    def _content_similarity(a: str, b: str) -> float:
        """
        Similarité simple basée sur Jaccard tokens.
        Suffisant pour éviter deux chunks quasi identiques.
        """
        if not a or not b:
            return 0.0

        tokens_a = set(a.split())
        tokens_b = set(b.split())

        if not tokens_a or not tokens_b:
            return 0.0

        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b

        return len(intersection) / len(union)

    def format_context(
        self,
        results: list[dict],
        max_chars: int = 6000,
        include_metadata: bool = True,
    ) -> str:
        """
        Formate les chunks récupérés en contexte textuel pour le LLM.

        Format :
          [S1] file=... | domaine=... | score=...
          Métadonnées utiles
          Contenu chunk

        Le contexte total est tronqué à max_chars.
        """
        if not results:
            return "(aucun contexte pertinent trouvé)"

        parts = []
        total_chars = 0

        for idx, r in enumerate(results, 1):
            chunk = r.get("chunk", {}) or {}
            meta = _restore_metadata(r.get("metadata") or chunk.get("metadata", {}) or {})
            content = str(chunk.get("content", "") or r.get("content", "") or "").strip()

            score = float(r.get("final_score", r.get("score", 0.0)))
            base_score = float(r.get("score", 0.0))
            bonus = float(r.get("metadata_bonus", 0.0))

            file_name = meta.get("file_name", "inconnu")
            organisme_name = meta.get("organisme_name", "")
            organisme_id = meta.get("organisme_id", "")
            domaine = meta.get("domaine_principal", "")
            chunk_id = (
                r.get("chunk_id")
                or chunk.get("chunk_id")
                or meta.get("chunk_id")
                or f"source_{idx}"
            )

            header_parts = [
                f"[S{idx}]",
                f"chunk_id={chunk_id}",
                f"file={file_name}",
                f"score={score:.3f}",
                f"vector={base_score:.3f}",
                f"bonus={bonus:.3f}",
            ]

            if organisme_name:
                header_parts.append(f"organisme={organisme_name}")
            if organisme_id:
                header_parts.append(f"organisme_id={organisme_id}")
            if domaine:
                header_parts.append(f"domaine={domaine}")

            header = " | ".join(header_parts)

            meta_block = ""
            if include_metadata:
                useful_meta_lines = self._format_useful_metadata(meta)
                if useful_meta_lines:
                    meta_block = "\n[MÉTADONNÉES]\n" + useful_meta_lines

            chunk_text = f"{header}{meta_block}\n[CONTENU]\n{content}"

            if total_chars + len(chunk_text) > max_chars:
                remaining = max_chars - total_chars
                if remaining > 300:
                    chunk_text = chunk_text[:remaining] + "\n..."
                    parts.append(chunk_text)
                break

            parts.append(chunk_text)
            total_chars += len(chunk_text)

        return "\n\n---\n\n".join(parts)

    @staticmethod
    def _format_useful_metadata(meta: dict[str, Any]) -> str:
        """
        Formate seulement les métadonnées utiles pour le LLM.

        Objectif :
          - afficher les champs NLP enrichis importants dans le contexte RAG ;
          - éviter que le LLM ne voie que le contenu brut du chunk ;
          - garder un contexte lisible sans surcharger avec trop d'entités.
        """
        fields = [
            # Identité / contexte document
            "organisme_name",
            "organisme_id",
            "file_name",
            "domaine_principal",

            # Champs NLP projet
            "mots_cles_high_confidence",
            "mots_cles_candidates",
            "objet_recherche",
            "sous_domaines",

            # Champs R&D principaux
            "verrous_techniques",
            "objectifs_rd",
            "hypotheses_rd",
            "methodes_rd",
            "protocoles_experimentaux",

            # Outils / architecture / données
            "outils_technologies",
            "modeles_algorithmes",
            "architectures_systeme",
            "jeux_donnees_benchmarks",

            # Évaluation / normes / composants
            "metriques_evaluation",
            "parametres_variables",
            "normes_techniques",
            "materiaux_composants",

            # Résultats / limites / livrables
            "limitations_perspectives",
            "resultats_rd",
            "livrables",

            # Entités locales
            "technologies",
            "entities",
        ]

        lines = []

        for field in fields:
            value = meta.get(field)
            text = _value_to_text(value)

            if not text:
                continue

            # Limiter les entités pour ne pas surcharger le prompt.
            if field == "entities" and len(text) > 700:
                text = text[:700] + "..."

            # Les mots-clés candidats peuvent être longs/bruités.
            if field == "mots_cles_candidates" and len(text) > 500:
                text = text[:500] + "..."

            lines.append(f"- {field}: {text}")

        return "\n".join(lines)