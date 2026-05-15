"""
modules/RAG/query_engine.py — EnnoSmart RAG v1.2 EnnoAmel POC
──────────────────────────────────────────────────────────────────────────────
Query Engine : transforme les chunks récupérés par le Retriever en réponse LLM.

Architecture :
  Extraction → NLP → RAG → EnnoAmel

Rôle dans le POC :
  - répondre aux questions documentaires simples ;
  - produire un résumé clair et naturel du projet ;
  - donner une estimation préliminaire si la question concerne le CIR ;
  - rediriger proprement vers EnnoDiagnostic / EnnoScholar / EnnoValor
    quand la demande dépasse le POC ;
  - agir comme un chat intelligent, naturel et professionnel.

Backends supportés :
  - Ollama local :
      "ollama:mistral:7b-instruct"
      "ollama:qwen3:4b-instruct"
      "ollama:qwen2.5:7b-instruct"
  - OpenRouter :
      modèles compatibles OpenAI API

Modèle recommandé pour ce POC :
  - "ollama:mistral:7b-instruct"
  - ou "ollama:qwen3:4b-instruct" si tu veux tester ton nouveau modèle local

Important :
  - Le QueryEngine ne doit pas halluciner.
  - Il répond uniquement depuis les sources récupérées.
  - Il cite les sources [S1], [S2], etc.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LOCAL_MODEL_PREFIXES = ("ollama:", "local:")

DEFAULT_MODEL = "ollama:mistral:7b-instruct"

MAX_CONTEXT_CHARS = 7000
TIMEOUT_SECONDS = 120


ENNOAMEL_SYSTEM_PROMPT = """Tu es EnnoAmel, l'orchestrateur intelligent de la plateforme EnnoSmart.

Contexte général :
EnnoSmart est une plateforme multi-agents pour automatiser, analyser et améliorer le traitement des dossiers de Crédit d'Impôt Recherche (CIR).

Modules de la plateforme :
- EnnoAmel : orchestrateur central, chat documentaire, compréhension de l'intention utilisateur, résumé et orientation.
- EnnoDiagnostic : analyse CIR complète, score d'éligibilité, risques, preuves, conformité et validation humaine.
- EnnoScholar : état de l'art scientifique, articles, citations, gap analysis et positionnement scientifique.
- EnnoValor : données financières/RH, mapping Excel/Cerfa, valorisation des dépenses et livrables administratifs.

Ton rôle dans ce POC :
1. Comprendre naturellement la demande de l'utilisateur.
2. Répondre à partir des sources RAG fournies.
3. Donner une idée claire du projet si l'utilisateur demande un résumé ou une vue globale.
4. Extraire et expliquer les éléments importants : domaine, objectif, verrous, méthodes, outils, résultats, limites.
5. Donner une estimation préliminaire si la question concerne l'éligibilité CIR.
6. Rediriger vers l'agent spécialisé lorsque la demande nécessite un module complet.
7. Ne jamais inventer d'information absente des sources.

Style de réponse attendu :
- Réponds en français.
- Réponds comme un assistant professionnel, naturel et clair.
- Ne sois pas robotique.
- Va directement à l'essentiel.
- Structure la réponse avec des titres courts si utile.
- Utilise des listes seulement quand cela aide vraiment la compréhension.
- Explique les choses simplement, même si le contenu est technique.
- Si l'utilisateur pose une question simple, réponds simplement.
- Si l'utilisateur demande une analyse, donne une réponse plus structurée.
- Si l'utilisateur demande "de quoi parle ce projet", donne une synthèse claire.
- Si l'utilisateur demande les mots-clés, verrous, méthodes ou outils, extrais-les clairement depuis les sources.

Règles strictes :
- Base-toi uniquement sur le contexte fourni.
- Cite les sources sous forme [S1], [S2], etc.
- Si une information n'est pas présente dans le contexte, dis-le clairement.
- Ne donne pas un score CIR définitif dans ce POC.
- Ne présente jamais une hypothèse comme une certitude.
- Ne crée pas de fausses sources.
- Ne mentionne pas des sources qui ne sont pas utilisées.
- Pour une analyse CIR complète, indique qu'il faut passer par EnnoDiagnostic.
- Pour un état de l'art complet, indique qu'il faut passer par EnnoScholar.
- Pour finance/RH/Cerfa/Excel, indique qu'il faut passer par EnnoValor.

Comportement en cas de contexte insuffisant :
- Si le contexte est faible, dis que les informations disponibles sont limitées.
- Donne quand même ce qui peut être déduit des passages fournis.
- Explique ce qu'il faudrait comme document ou agent pour aller plus loin.

Tu dois toujours rester fidèle aux sources RAG.
"""


RAG_USER_TEMPLATE = """## Question utilisateur
{question}

## Intention détectée
{intent}

## Agent recommandé
{recommended_agent}

## Contexte disponible
{context}

## Consigne de réponse

Réponds uniquement à partir du contexte fourni.

Tu dois produire une réponse naturelle, claire et utile, comme un vrai assistant de dialogue.

Règles générales :
- Cite les sources avec [S1], [S2], etc.
- Ne cite que les sources réellement utiles.
- Si une information est absente du contexte, dis-le clairement.
- Ne fais pas de conclusion définitive si les preuves sont insuffisantes.
- Ne répète pas tout le contexte brut.
- Reformule les informations importantes de manière compréhensible.

Si l'intention est "summary" :
- présente l'idée générale du projet ;
- donne le domaine principal si disponible ;
- explique l'objet de recherche ;
- liste les objectifs R&D, verrous, méthodes, outils et résultats s'ils sont disponibles ;
- termine par une phrase de synthèse claire ;
- cite les sources.

Si l'intention est "eligibility" :
- donne une estimation préliminaire, pas un verdict final ;
- distingue les signaux favorables et les points à vérifier ;
- mentionne les verrous techniques, incertitudes, méthodes et résultats disponibles ;
- précise que le score détaillé doit être produit par EnnoDiagnostic ;
- ne donne pas une conclusion définitive.

Si l'intention est "scholar" :
- réponds avec les éléments scientifiques disponibles ;
- identifie les domaines, méthodes, modèles, jeux de données ou gaps si présents ;
- précise que l'état de l'art complet doit être construit par EnnoScholar.

Si l'intention est "valor" :
- réponds avec les informations disponibles ;
- précise que l'extraction financière/RH et les livrables doivent être traités par EnnoValor.

Si l'intention est "tools" :
- liste les outils, technologies, frameworks, plateformes ou logiciels identifiés ;
- explique brièvement leur rôle si le contexte le permet.

Si l'intention est "methods" :
- liste les méthodes, protocoles, approches ou démarches R&D ;
- explique comment elles semblent être utilisées dans le projet.

Si l'intention est "metrics" :
- liste les métriques, résultats, paramètres ou critères d'évaluation disponibles ;
- précise si les résultats sont incomplets.

Si l'intention est "components" :
- liste les composants, matériaux, équipements ou éléments techniques disponibles ;
- précise leur rôle si le contexte le permet.

Si l'intention est "standards" :
- liste les normes, standards ou contraintes techniques disponibles ;
- précise si aucune norme claire n'est trouvée.

Sinon :
- réponds directement à la question avec sources.
"""


@dataclass
class RAGResponse:
    answer: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    chunks_used: int = 0
    query: str = ""
    model: str = ""
    backend: str = ""
    intent: str = "qa"
    recommended_agent: Optional[str] = None
    processing_time: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "sources": self.sources,
            "chunks_used": self.chunks_used,
            "query": self.query,
            "model": self.model,
            "backend": self.backend,
            "intent": self.intent,
            "recommended_agent": self.recommended_agent,
            "processing_time": round(self.processing_time, 2),
            "error": self.error,
        }


def _is_local_model(model: str) -> bool:
    return str(model or "").startswith(LOCAL_MODEL_PREFIXES)


def _clean_local_model_name(model: str) -> str:
    model = str(model or "").strip()
    for prefix in LOCAL_MODEL_PREFIXES:
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


def _call_ollama(system: str, user: str, model: str) -> tuple[str, str]:
    """
    Appel Ollama local.

    Exemple :
      - ollama:mistral:7b-instruct
      - ollama:qwen3:4b-instruct
      - ollama:qwen2.5:7b-instruct
    """
    local_model = _clean_local_model_name(model)

    try:
        import ollama

        response = ollama.chat(
            model=local_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            options={
                "temperature": 0.15,
                "top_p": 0.35,
                "num_ctx": 9000,
            },
        )

        content = response.get("message", {}).get("content", "")
        return content, "ollama"

    except ImportError as exc:
        raise ImportError("ollama non installé : pip install ollama") from exc


def _call_openrouter(system: str, user: str, model: str) -> tuple[str, str]:
    """
    Appel OpenRouter.

    À utiliser seulement si tu veux tester un modèle cloud.
    Pour ton POC local, préfère Ollama.
    """
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise ImportError("openai non installé : pip install openai") from exc

    api_key = os.environ.get("OPENROUTER_API_KEY")

    if not api_key:
        raise ValueError("OPENROUTER_API_KEY non définie dans les variables d'environnement.")

    client = OpenAI(base_url=OPENROUTER_BASE_URL, api_key=api_key)

    completion = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.15,
        max_tokens=2200,
        timeout=TIMEOUT_SECONDS,
    )

    content = completion.choices[0].message.content or ""
    return content, "openrouter"


def _extract_sources(results: list[dict]) -> list[dict[str, Any]]:
    """
    Extrait les sources utilisées dans un format exploitable par Streamlit.

    Retour :
      [
        {
          "ref": "S1",
          "chunk_id": "...",
          "file_name": "...",
          "score": 0.82,
          "source": "text"
        }
      ]
    """
    sources = []

    for i, r in enumerate(results, 1):
        chunk = r.get("chunk", {}) or {}
        meta = r.get("metadata") or chunk.get("metadata", {}) or {}

        sources.append(
            {
                "ref": f"S{i}",
                "chunk_id": (
                    r.get("chunk_id")
                    or chunk.get("chunk_id")
                    or meta.get("chunk_id")
                ),
                "file_name": meta.get("file_name", ""),
                "domaine_principal": meta.get("domaine_principal", ""),
                "source": meta.get("source", chunk.get("source", "")),
                "score": round(float(r.get("final_score", r.get("score", 0.0))), 4),
                "vector_score": round(float(r.get("score", 0.0)), 4),
                "metadata_bonus": round(float(r.get("metadata_bonus", 0.0)), 4),
            }
        )

    return sources


def _fallback_answer_from_context(
    question: str,
    context: str,
    intent: str,
    recommended_agent: Optional[str],
) -> str:
    """
    Réponse de secours si le LLM n'est pas disponible.

    Elle évite de bloquer l'utilisateur : le RAG affiche au moins
    les passages pertinents récupérés.
    """
    agent_note = ""

    if intent == "eligibility":
        agent_note = (
            "\n\nNote : cette demande relève d'une analyse CIR complète. "
            "Dans l'architecture EnnoSmart, elle doit être traitée par EnnoDiagnostic "
            "pour produire un score, des risques et une validation plus fiable."
        )
    elif intent == "scholar":
        agent_note = (
            "\n\nNote : cette demande relève de l'état de l'art scientifique. "
            "Dans l'architecture EnnoSmart, elle doit être traitée par EnnoScholar "
            "pour construire une analyse bibliographique complète."
        )
    elif intent == "valor":
        agent_note = (
            "\n\nNote : cette demande relève de la valorisation administrative ou financière. "
            "Dans l'architecture EnnoSmart, elle doit être traitée par EnnoValor."
        )

    return (
        "Le LLM n'est pas disponible pour générer une réponse complète.\n\n"
        "Voici les passages les plus pertinents trouvés par le RAG :\n\n"
        f"{context}"
        f"{agent_note}"
    )


class QueryEngine:
    """
    QueryEngine EnnoSmart.

    Il orchestre :
      retriever → contexte → LLM → réponse sourcée.

    Le routage agent complet peut être fait dans orchestration/ennoamel.py.
    Mais ce QueryEngine accepte déjà intent/recommended_agent pour produire
    une réponse cohérente avec ton POC.
    """

    def __init__(
        self,
        retriever,
        model: str = DEFAULT_MODEL,
        top_k: int = 5,
        min_score: float = -1.0,
        max_context_chars: int = MAX_CONTEXT_CHARS,
    ):
        self.retriever = retriever
        self.model = model
        self.top_k = top_k
        self.min_score = min_score
        self.max_context_chars = max_context_chars

    def ask(
        self,
        question: str,
        filter_meta: Optional[dict] = None,
        top_k: Optional[int] = None,
        intent: str = "qa",
        recommended_agent: Optional[str] = "EnnoAmel",
    ) -> RAGResponse:
        """
        Répond à une question en utilisant le RAG.

        Étapes :
          1. Retriever : récupérer les chunks pertinents.
          2. Formater le contexte avec sources [S1], [S2].
          3. Appeler le LLM.
          4. Retourner réponse + sources + agent recommandé.

        Paramètres :
          question          : question utilisateur.
          filter_meta       : filtre metadata optionnel.
          top_k             : nombre de chunks à récupérer.
          intent            : intention détectée par EnnoAmel.
          recommended_agent : agent cible si besoin.
        """
        t0 = time.time()
        k = top_k or self.top_k

        response = RAGResponse(
            query=question,
            model=self.model,
            intent=intent,
            recommended_agent=recommended_agent,
        )

        if not question or not question.strip():
            response.answer = "Question vide."
            response.error = "empty_query"
            response.processing_time = time.time() - t0
            return response

        try:
            results = self.retriever.search(
                query=question,
                top_k=k,
                min_score=self.min_score,
                filter_meta=filter_meta,
                intent=intent,
            )

        except TypeError:
            # Compatibilité si un ancien Retriever ne supporte pas intent.
            results = self.retriever.search(
                query=question,
                top_k=k,
                min_score=self.min_score,
                filter_meta=filter_meta,
            )

        except Exception as exc:
            logger.error("Erreur retriever : %s", exc)
            response.answer = "Erreur lors de la recherche dans la base documentaire."
            response.error = str(exc)
            response.processing_time = time.time() - t0
            return response

        if not results:
            response.answer = (
                "Je n'ai trouvé aucun passage pertinent dans la base documentaire. "
                "Vérifie que le document NLP a bien été indexé dans le RAG."
            )
            response.chunks_used = 0
            response.processing_time = time.time() - t0
            return response

        response.chunks_used = len(results)
        response.sources = _extract_sources(results)

        try:
            context = self.retriever.format_context(
                results,
                max_chars=self.max_context_chars,
                include_metadata=True,
            )
        except TypeError:
            # Compatibilité si un ancien Retriever ne supporte pas include_metadata.
            context = self.retriever.format_context(
                results,
                max_chars=self.max_context_chars,
            )

        user_prompt = RAG_USER_TEMPLATE.format(
            question=question,
            intent=intent,
            recommended_agent=recommended_agent or "EnnoAmel",
            context=context,
        )

        try:
            if _is_local_model(self.model):
                answer, backend = _call_ollama(
                    ENNOAMEL_SYSTEM_PROMPT,
                    user_prompt,
                    self.model,
                )
            else:
                answer, backend = _call_openrouter(
                    ENNOAMEL_SYSTEM_PROMPT,
                    user_prompt,
                    self.model,
                )

            response.answer = answer.strip()
            response.backend = backend

        except Exception as exc:
            logger.error("Erreur LLM QueryEngine : %s", exc)

            response.answer = _fallback_answer_from_context(
                question=question,
                context=context,
                intent=intent,
                recommended_agent=recommended_agent,
            )
            response.error = str(exc)
            response.backend = "fallback_context"

        response.processing_time = time.time() - t0

        logger.info(
            "QueryEngine [%s] intent=%s → %d chunks | %.2fs | backend=%s",
            question[:60],
            intent,
            response.chunks_used,
            response.processing_time,
            response.backend,
        )

        return response

    def ask_multi(
        self,
        questions: list[str],
        filter_meta: Optional[dict] = None,
        intent: str = "qa",
        recommended_agent: Optional[str] = "EnnoAmel",
    ) -> list[RAGResponse]:
        """
        Répond à plusieurs questions.
        """
        return [
            self.ask(
                question=q,
                filter_meta=filter_meta,
                intent=intent,
                recommended_agent=recommended_agent,
            )
            for q in questions
        ]