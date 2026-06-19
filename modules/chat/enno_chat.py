# modules/chat/enno_chat.py

from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional

from modules.chat.ollama_client import ollama_chat
from modules.chat.schemas import ChatDecision


@dataclass
class EnnoChatConfig:
    # LLM léger conseillé pour comprendre l'intention.
    model: str = "ollama:qwen3:4b-instruct"
    timeout: int = 45
    temperature: float = 0.15
    num_predict: int = 350
    debug: bool = True


class EnnoChat:
    """
    Module de compréhension conversationnelle par LLM.

    Version intelligente :
    - pas de routing documentaire par mots-clés ;
    - même bonjour/merci/ok/ça va passent par le LLM ;
    - le LLM produit une décision JSON structurée ;
    - l'orchestrateur utilise cette décision pour répondre directement
      ou transmettre une consigne précise au RAG.

    Le module ne fait pas lui-même l'analyse documentaire.
    """

    def __init__(self, config: Optional[EnnoChatConfig] = None):
        self.config = config or EnnoChatConfig()

    def understand(
        self,
        user_message: str,
        *,
        has_document: bool = False,
        has_rag_index: bool = False,
        document_metadata: Optional[dict[str, Any]] = None,
        chat_history: Optional[list[dict[str, str]]] = None,
    ) -> ChatDecision:
        t0 = time.time()
        metadata = document_metadata or {}
        normalized = self._normalize(user_message)

        if not normalized:
            return ChatDecision(
                handled=True,
                answer="Je n’ai pas bien reçu votre message. Pouvez-vous reformuler ?",
                intent="clarification",
                action="chat_empty_message",
                use_rag=False,
                use_llm=False,
                recommended_agent="Orchestrateur",
                needs_specialized_agent=False,
                confidence=0.95,
                normalized_question=normalized,
                debug={"reason": "empty_message", "processing_time": round(time.time() - t0, 3)},
            )

        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(
            user_message=user_message,
            has_document=has_document,
            has_rag_index=has_rag_index,
            metadata=metadata,
            chat_history=chat_history or [],
        )

        try:
            raw = ollama_chat(
                model=self.config.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=self.config.temperature,
                num_predict=self.config.num_predict,
                timeout=self.config.timeout,
            )

            parsed = self._safe_json_loads(raw)

            return self._decision_from_json(
                parsed=parsed,
                raw=raw,
                normalized=normalized,
                processing_time=time.time() - t0,
            )

        except Exception as exc:
            # Fallback sûr : on laisse l'orchestrateur/RAG répondre au document.
            return ChatDecision(
                handled=False,
                answer="",
                intent="document_question",
                action="chat_fallback_to_rag",
                use_rag=bool(has_rag_index),
                use_llm=True,
                recommended_agent="Orchestrateur",
                needs_specialized_agent=False,
                topic="general",
                answer_style="natural",
                requested_format="free_text",
                detail_level="normal",
                rag_search_query=user_message,
                rag_instruction=(
                    "Réponds naturellement à la question utilisateur, uniquement à partir des sources RAG. "
                    "Respecte le niveau de détail demandé par l'utilisateur."
                ),
                confidence=0.25,
                normalized_question=normalized,
                debug={"error": str(exc), "processing_time": round(time.time() - t0, 3)},
            )

    # ═══════════════════════════════════════════════════════════════════════
    # PROMPTS
    # ═══════════════════════════════════════════════════════════════════════

    def _build_system_prompt(self) -> str:
        return """
Tu es l’Orchestrateur intelligent d’un assistant R&D/CIR.

Ton rôle :
- comprendre précisément l’intention utilisateur ;
- comprendre le sujet demandé ;
- comprendre le format attendu ;
- décider s’il faut répondre directement ou utiliser le RAG ;
- transmettre une instruction claire au RAG.

Tu es expert en dossiers R&D/CIR :
objectifs R&D, verrous scientifiques/techniques, état de l’art, méthodes expérimentales,
résultats, matériaux, technologies, personnes, organismes, preuves documentaires.

Tu dois raisonner sur le sens global de la demande, pas par simple mots-clés.

Tu dois répondre UNIQUEMENT en JSON valide.

CRITIQUE : si le message utilisateur est uniquement social ou conversationnel
(exemples : "bonjour", "salut", "merci", "ok", "ça va", "d'accord", "très bien"),
tu dois répondre :
- handled=true
- use_rag=false
- intent="small_talk" ou "thanks"
- answer courte, humaine et naturelle
- ne parle jamais du document
- ne cite jamais de sources
- ne mentionne jamais le projet, le CIR, EnnoDiagnostic, EnnoScholar ou EnnoValor

────────────────────────────────
Cas où tu réponds directement
────────────────────────────────
1. Salutation, remerciement, "ça va", "ok", discussion simple :
   handled=true, use_rag=false, réponse courte et naturelle.
   Ne parle jamais du document, ne cite jamais de sources, ne mentionne jamais EnnoDiagnostic, EnnoScholar ou EnnoValor.

2. Si l'utilisateur demande un score CIR, un taux d’éligibilité, une validation d’éligibilité,
   un diagnostic CIR complet, ou une analyse détaillée des risques :
   handled=true
   answer="EnnoDiagnostic est encore en cours de construction pour produire le score d’éligibilité CIR, l’analyse des risques et la validation détaillée. Pour le moment, je peux seulement vous aider à lire le dossier, résumer les objectifs, verrous, méthodes et résultats à partir des sources indexées."
   recommended_agent="EnnoDiagnostic"
   needs_specialized_agent=true

3. Si l'utilisateur demande la rédaction d’un état de l’art complet, une recherche d’articles,
   une bibliographie, des citations scientifiques externes ou un gap analysis :
   handled=true
   answer="EnnoScholar est encore en cours de construction pour rédiger un état de l’art complet avec recherche d’articles, bibliographie et citations. Pour le moment, je peux seulement résumer l’état de l’art présent dans le document indexé."
   recommended_agent="EnnoScholar"
   needs_specialized_agent=true

Ces réponses spécialisées ne nécessitent pas de document préparé.
Même si has_document=false ou has_rag_index=false, tu dois répondre directement
que l'agent spécialisé est en cours de construction.

4. Si l'utilisateur demande finance/RH/Cerfa/Excel/valorisation :
   handled=true
   answer="EnnoValor est encore en cours de construction pour traiter la partie financière, RH, Excel et Cerfa. Pour le moment, je peux vous aider sur la compréhension documentaire du dossier."
   recommended_agent="EnnoValor"
   needs_specialized_agent=true

────────────────────────────────
Cas où il faut utiliser le RAG
────────────────────────────────
Si la demande concerne le document chargé :
- résumé du projet ;
- résumé d’une partie précise ;
- verrous ;
- objectifs ;
- méthodes ;
- résultats ;
- technologies ;
- matériaux ;
- personnes ;
- organismes ;
- sections ;
- état de l’art présent dans le document ;
- preuves/sources ;
alors handled=false, use_rag=true, recommended_agent="Orchestrateur".

Tu dois générer :
- intent : le sujet principal.
- topic : sujet exact demandé.
- answer_style : ex. short_summary, detailed_explanation, extraction, reformulation.
- requested_format : ex. bullet_points, paragraph, table, list.
- max_points : nombre demandé si l'utilisateur en donne un, sinon null.
- detail_level : short, normal, detailed.
- rag_search_query : requête courte pour récupérer les bonnes sources.
- rag_instruction : consigne claire pour le RAG.

Règles importantes :
- Si l’utilisateur demande les verrous, la réponse finale doit parler uniquement des verrous.
- Si l’utilisateur demande les objectifs, la réponse finale doit parler uniquement des objectifs.
- Si l’utilisateur demande "en 3 points", max_points=3 et la consigne doit dire exactement 3 points.
- Si l’utilisateur demande "petit résumé", "simple", "court", ou une forme courte, detail_level="short".
- Si l’utilisateur veut une réponse naturelle, éviter de recopier tout le JSON.
- Ne mélange pas objectifs, verrous, méthodes et résultats sauf si l’utilisateur demande une synthèse globale.
- Si l’utilisateur demande seulement un sujet précis, interdis les autres sujets dans rag_instruction.
- Si aucun document n'est disponible, use_rag=false et demande d'abord d'importer/préparer un document.


Résumé court projet — règle prioritaire :
Si l'utilisateur demande un petit résumé clair du projet, un résumé simple, ou une synthèse courte du projet :
- handled=false
- use_rag=true
- intent="project_summary"
- topic="project_summary"
- answer_style="short_summary"
- requested_format="single_paragraph"
- detail_level="short"
- rag_instruction doit demander :
  "Faire un petit résumé clair du projet en un seul paragraphe. Inclure uniquement le domaine, l'objectif principal, les verrous importants, les mots-clés et technologies importantes. Ne pas parler des agents, du score CIR, du rescrit, de la JEI, de l’agrément, de la DGA ou des informations administratives sauf si l'utilisateur le demande. Ne pas citer [S1], [S2] et ne pas afficher de sources."

Valeurs possibles d'intent :
small_talk, thanks, help,
project_summary, verrous, objectives, methods, results,
etat_art, technologies, materials, people, organisms, sections,
keywords, source_proof, document_question,
eligibility, diagnostic_detail, scholar, valorisation, clarification.

Format JSON obligatoire :
{
  "handled": false,
  "answer": "",
  "intent": "verrous",
  "topic": "verrous",
  "answer_style": "short_summary",
  "requested_format": "bullet_points",
  "max_points": 3,
  "detail_level": "short",
  "use_rag": true,
  "recommended_agent": "Orchestrateur",
  "needs_specialized_agent": false,
  "rag_search_query": "verrous techniques incertitudes limites scientifiques techniques du projet",
  "rag_instruction": "Répondre uniquement sur les verrous techniques du document, en exactement 3 points courts. Ne pas détailler les objectifs, méthodes, technologies ou résultats.",
  "confidence": 0.9
}
""".strip()

    def _build_user_prompt(
        self,
        *,
        user_message: str,
        has_document: bool,
        has_rag_index: bool,
        metadata: dict[str, Any],
        chat_history: list[dict[str, str]],
    ) -> str:
        context = {
            "has_document": has_document,
            "has_rag_index": has_rag_index,
            "file_name": metadata.get("file_name"),
            "title": metadata.get("title"),
            "domaine_principal": metadata.get("domaine_principal"),
            "domaine_applicatif": metadata.get("domaine_applicatif"),
            "metadata_keys_available": list(metadata.keys())[:50],
        }

        compact_history = chat_history[-4:] if chat_history else []

        return f"""
Message utilisateur :
{user_message}

Contexte technique disponible :
{json.dumps(context, ensure_ascii=False)}

Historique court :
{json.dumps(compact_history, ensure_ascii=False)}

Analyse la demande et retourne uniquement le JSON de décision.
""".strip()

    # ═══════════════════════════════════════════════════════════════════════
    # JSON → ChatDecision
    # ═══════════════════════════════════════════════════════════════════════

    def _decision_from_json(
        self,
        *,
        parsed: dict[str, Any],
        raw: str,
        normalized: str,
        processing_time: float,
    ) -> ChatDecision:
        intent = str(parsed.get("intent") or "document_question").strip()
        answer = str(parsed.get("answer") or "").strip()

        handled = self._to_bool(parsed.get("handled", False))
        use_rag = self._to_bool(parsed.get("use_rag", False))
        needs_specialized_agent = self._to_bool(parsed.get("needs_specialized_agent", False))

        recommended_agent = str(parsed.get("recommended_agent") or "Orchestrateur").strip()

        # Compatibilité avec les anciens noms.
        if recommended_agent in {"EnnoAmel", "Orchestrateur"}:
            recommended_agent = "Orchestrateur"

        if recommended_agent not in {
            "Orchestrateur",
            "EnnoDiagnostic",
            "EnnoScholar",
            "EnnoValor",
        }:
            recommended_agent = "Orchestrateur"

        try:
            confidence = float(parsed.get("confidence", 0.75))
        except Exception:
            confidence = 0.75
        confidence = max(0.0, min(confidence, 1.0))

        max_points = parsed.get("max_points")
        try:
            max_points = int(max_points) if max_points is not None else None
        except Exception:
            max_points = None

        topic = str(parsed.get("topic") or intent or "general").strip()
        answer_style = str(parsed.get("answer_style") or "natural").strip()
        requested_format = str(parsed.get("requested_format") or "free_text").strip()
        detail_level = str(parsed.get("detail_level") or "normal").strip()
        rag_search_query = str(parsed.get("rag_search_query") or "").strip()
        rag_instruction = str(parsed.get("rag_instruction") or "").strip()

        # Garde-fou résumé projet : doit rester court et centré projet.
        forced_summary = self._force_project_summary_if_needed(normalized)
        if forced_summary is not None:
            return ChatDecision(
                handled=False,
                answer="",
                intent=forced_summary["intent"],
                action="chat_forced_project_summary",
                use_rag=True,
                use_llm=True,
                recommended_agent="Orchestrateur",
                needs_specialized_agent=False,
                topic=forced_summary["topic"],
                answer_style=forced_summary["answer_style"],
                requested_format=forced_summary["requested_format"],
                detail_level=forced_summary["detail_level"],
                max_points=None,
                rag_search_query=forced_summary["rag_search_query"],
                rag_instruction=forced_summary["rag_instruction"],
                confidence=forced_summary["confidence"],
                normalized_question=normalized,
                debug={
                    "forced_summary": True,
                    "raw_llm_output": raw,
                    "parsed": parsed,
                    "processing_time": round(processing_time, 3),
                },
            )

        # Garde-fou spécialisé avant toute logique documentaire.
        forced_specialized = self._force_specialized_if_needed(normalized)
        if forced_specialized is not None:
            return ChatDecision(
                handled=True,
                answer=forced_specialized["answer"],
                intent=forced_specialized["intent"],
                action="chat_specialized_agent_under_construction",
                use_rag=False,
                use_llm=True,
                recommended_agent=forced_specialized["recommended_agent"],
                needs_specialized_agent=True,
                topic=forced_specialized["topic"],
                answer_style="direct_notice",
                requested_format="free_text",
                detail_level="short",
                max_points=None,
                rag_search_query="",
                rag_instruction="",
                confidence=forced_specialized["confidence"],
                normalized_question=normalized,
                debug={
                    "forced_specialized": True,
                    "raw_llm_output": raw,
                    "parsed": parsed,
                    "processing_time": round(processing_time, 3),
                },
            )

        # Garde-fou : même si le LLM se trompe, une salutation pure ne doit jamais partir au RAG.
        if self._is_pure_conversation(normalized):
            return ChatDecision(
                handled=True,
                answer=answer or self._fallback_direct_answer("small_talk"),
                intent="small_talk" if intent not in {"thanks", "help"} else intent,
                action="chat_guard_pure_conversation",
                use_rag=False,
                use_llm=True,
                recommended_agent="Orchestrateur",
                needs_specialized_agent=False,
                topic="conversation",
                answer_style="natural",
                requested_format="free_text",
                detail_level="short",
                max_points=None,
                rag_search_query="",
                rag_instruction="",
                confidence=max(confidence, 0.95),
                normalized_question=normalized,
                debug={
                    "raw_llm_output": raw,
                    "parsed": parsed,
                    "guard": "pure_conversation_no_rag",
                    "processing_time": round(processing_time, 3),
                },
            )

        direct_intents = {"small_talk", "thanks", "help", "clarification", "general_chat"}
        specialized_direct = {"eligibility", "diagnostic_detail", "scholar", "valorisation"}

        if intent in direct_intents:
            handled = True
            use_rag = False
            needs_specialized_agent = False
            recommended_agent = "Orchestrateur"
            if not answer:
                answer = self._fallback_direct_answer(intent)

        elif intent in specialized_direct:
            handled = True
            use_rag = False
            needs_specialized_agent = True

            if intent in {"eligibility", "diagnostic_detail"}:
                recommended_agent = "EnnoDiagnostic"
                if not answer:
                    answer = (
                        "EnnoDiagnostic est encore en cours de construction pour produire le score "
                        "d’éligibilité CIR, l’analyse des risques et la validation détaillée. "
                        "Pour le moment, je peux seulement vous aider à lire le dossier, résumer les objectifs, "
                        "verrous, méthodes et résultats à partir des sources indexées."
                    )

            elif intent == "scholar":
                recommended_agent = "EnnoScholar"
                if not answer:
                    answer = (
                        "EnnoScholar est encore en cours de construction pour rédiger un état de l’art complet "
                        "avec recherche d’articles, bibliographie et citations. Pour le moment, je peux seulement "
                        "résumer l’état de l’art présent dans le document indexé."
                    )

            elif intent == "valorisation":
                recommended_agent = "EnnoValor"
                if not answer:
                    answer = (
                        "EnnoValor est encore en cours de construction pour traiter la partie financière, RH, "
                        "Excel et Cerfa. Pour le moment, je peux vous aider sur la compréhension documentaire du dossier."
                    )

        else:
            # Toute vraie demande documentaire passe par le RAG.
            handled = False
            answer = ""
            use_rag = True
            recommended_agent = "Orchestrateur"
            needs_specialized_agent = False

            if not rag_search_query:
                rag_search_query = normalized

            if not rag_instruction:
                rag_instruction = (
                    "Réponds naturellement à la question posée, uniquement avec les sources RAG. "
                    "Respecte le sujet, le format et le niveau de détail demandés par l'utilisateur."
                )

        return ChatDecision(
            handled=handled,
            answer=answer,
            intent=intent,
            action="llm_intent_planning",
            use_rag=use_rag,
            use_llm=True,
            recommended_agent=recommended_agent,
            needs_specialized_agent=needs_specialized_agent,
            topic=topic,
            answer_style=answer_style,
            requested_format=requested_format,
            detail_level=detail_level,
            max_points=max_points,
            rag_search_query=rag_search_query,
            rag_instruction=rag_instruction,
            confidence=confidence,
            normalized_question=normalized,
            debug={
                "raw_llm_output": raw,
                "parsed": parsed,
                "processing_time": round(processing_time, 3),
            },
        )


    def _is_pure_conversation(self, normalized: str) -> bool:
        """
        Garde-fou minimal pour empêcher le RAG de répondre à une salutation pure.
        Le LLM reste utilisé en priorité, mais si le JSON LLM se trompe, on corrige.
        """
        simple = {
            "bonjour", "bonsoir", "salut", "hello", "hi", "coucou", "salam",
            "merci", "merci beaucoup", "ok", "okay", "d accord", "daccord",
            "ca va", "ça va", "cv", "tres bien", "très bien", "parfait"
        }
        if normalized in simple:
            return True
        # Messages très courts sans terme documentaire.
        doc_terms = {
            "document", "projet", "resume", "résumé", "verrou", "objectif",
            "etat", "art", "methode", "résultat", "resultat", "score", "cir",
            "eligibilite", "éligibilité", "source", "preuve"
        }
        parts = normalized.split()
        return len(parts) <= 3 and not any(t in normalized for t in doc_terms)


    def _force_specialized_if_needed(self, normalized: str) -> dict[str, Any] | None:
        """
        Garde-fou : les demandes réservées aux agents spécialisés doivent répondre
        directement que l'agent est en construction, même sans document préparé.
        """
        q = normalized

        scholar_signals = [
            "rediger un etat de lart", "rediger etat de lart",
            "rédiger un état de lart", "rédiger état de lart",
            "etat de lart complet", "état de lart complet",
            "bibliographie", "article scientifique", "articles scientifiques",
            "recherche d articles", "recherche articles", "gap analysis",
        ]
        diagnostic_signals = [
            "score eligibilite", "score d eligibilite", "score d éligibilité",
            "taux eligibilite", "taux d eligibilite",
            "eligible cir", "éligible cir", "eligibilite cir", "éligibilité cir",
            "diagnostic cir", "analyse cir complete", "risque cir", "risques cir",
        ]
        valor_signals = [
            "cerfa", "finance", "financier", "depense", "dépense",
            "depenses", "dépenses", "excel", "rh", "ressources humaines",
            "valorisation", "etp",
        ]

        if any(s in q for s in scholar_signals):
            return {
                "handled": True,
                "answer": (
                    "EnnoScholar est encore en cours de construction pour rédiger un état de l’art complet "
                    "avec recherche d’articles, bibliographie, citations et gap analysis. Pour le moment, "
                    "je peux seulement résumer l’état de l’art présent dans un document déjà indexé."
                ),
                "intent": "scholar",
                "topic": "etat_art_externe",
                "use_rag": False,
                "recommended_agent": "EnnoScholar",
                "needs_specialized_agent": True,
                "confidence": 0.98,
            }

        if any(s in q for s in diagnostic_signals):
            return {
                "handled": True,
                "answer": (
                    "EnnoDiagnostic est encore en cours de construction pour produire le score "
                    "d’éligibilité CIR, l’analyse des risques et la validation détaillée. Pour le moment, "
                    "je peux seulement aider à lire le dossier et résumer les objectifs, verrous, méthodes "
                    "et résultats à partir des sources indexées."
                ),
                "intent": "eligibility",
                "topic": "score_eligibilite_cir",
                "use_rag": False,
                "recommended_agent": "EnnoDiagnostic",
                "needs_specialized_agent": True,
                "confidence": 0.98,
            }

        if any(s in q for s in valor_signals):
            return {
                "handled": True,
                "answer": (
                    "EnnoValor est encore en cours de construction pour traiter la partie financière, RH, "
                    "Excel, Cerfa et livrables administratifs. Pour le moment, je peux vous aider sur "
                    "la compréhension documentaire du dossier."
                ),
                "intent": "valorisation",
                "topic": "valorisation",
                "use_rag": False,
                "recommended_agent": "EnnoValor",
                "needs_specialized_agent": True,
                "confidence": 0.98,
            }

        return None



    def _force_project_summary_if_needed(self, normalized: str) -> dict[str, Any] | None:
        """
        Garde-fou résumé projet.
        Objectif : éviter que "petit résumé clair du projet" devienne
        une analyse administrative ou un message sur les agents.
        """
        q = normalized

        summary_signals = [
            "petit resume", "petit résumé",
            "resume clair", "résumé clair",
            "resumer le projet", "résumer le projet",
            "resume du projet", "résumé du projet",
            "synthese du projet", "synthèse du projet",
            "presente le projet", "présente le projet",
            "explique le projet",
        ]

        if any(s in q for s in summary_signals):
            return {
                "handled": False,
                "answer": "",
                "intent": "project_summary",
                "topic": "project_summary",
                "answer_style": "short_summary",
                "requested_format": "single_paragraph",
                "detail_level": "short",
                "use_rag": True,
                "recommended_agent": "Orchestrateur",
                "needs_specialized_agent": False,
                "rag_search_query": (
                    "domaine objectif principal verrous importants mots clés technologies importantes projet"
                ),
                "rag_instruction": (
                    "Faire un petit résumé clair du projet en un seul paragraphe. "
                    "Inclure uniquement le domaine, l'objectif principal, les verrous importants, "
                    "les mots-clés et technologies importantes. "
                    "Ne pas parler des agents, du score CIR, du rescrit, de la JEI, de l'agrément, "
                    "de la DGA ou des informations administratives sauf si l'utilisateur le demande. "
                    "Ne pas citer [S1], [S2] et ne pas afficher de sources."
                ),
                "confidence": 0.98,
            }

        return None


    def _fallback_direct_answer(self, intent: str) -> str:
        if intent == "thanks":
            return "Avec plaisir."
        if intent == "help":
            return (
                "Je peux vous aider à interroger le dossier : résumé, verrous, objectifs, "
                "méthodes, résultats, technologies, matériaux, personnes, organismes ou sources."
            )
        if intent == "clarification":
            return "Je peux vous aider. Pouvez-vous préciser ce que vous voulez obtenir du dossier ?"
        return "Bonjour, je suis là. Que souhaitez-vous faire ?"

    def _safe_json_loads(self, text: str) -> dict[str, Any]:
        text = str(text or "").strip()

        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except Exception:
            pass

        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                return data if isinstance(data, dict) else {}
            except Exception:
                pass

        return {
            "handled": False,
            "answer": "",
            "intent": "document_question",
            "topic": "general",
            "answer_style": "natural",
            "requested_format": "free_text",
            "detail_level": "normal",
            "use_rag": True,
            "recommended_agent": "Orchestrateur",
            "needs_specialized_agent": False,
            "rag_search_query": "",
            "rag_instruction": "Réponds naturellement à la question utilisateur depuis les sources RAG.",
            "confidence": 0.4,
        }

    def _to_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "1", "yes", "oui"}
        return bool(value)

    def _normalize(self, text: str) -> str:
        value = str(text or "").strip().lower()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        value = re.sub(r"[^\w\s%/-]+", " ", value)
        value = re.sub(r"\s+", " ", value).strip()
        return value
