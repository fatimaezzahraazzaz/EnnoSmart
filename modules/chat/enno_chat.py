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
    model: str = "ollama:llama3.2:3b"
    timeout: int = 30
    temperature: float = 0.2
    num_predict: int = 180
    debug: bool = True


class EnnoChat:
    """
    Module de chat intelligent indépendant de EnnoAmel.

    Rôle :
      - Comprendre le message utilisateur.
      - Répondre directement uniquement aux messages humains simples.
      - Ne jamais analyser le dossier à la place de EnnoAmel.
      - Dire à EnnoAmel quoi faire ensuite.

    EnnoAmel utilise ce module AVANT intent_router et AVANT le RAG.

    Version corrigée :
      - Les messages humains simples sont détectés localement.
      - Le LLM n'est appelé que si le message nécessite une vraie analyse d'intention.
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
        """
        Point d'entrée principal.

        Retourne :
          - handled=True si le chat répond directement.
          - handled=False si EnnoAmel doit continuer.
        """

        t0 = time.time()
        metadata = document_metadata or {}
        normalized = self._normalize(user_message)

        if not normalized:
            return ChatDecision(
                handled=True,
                answer="Je n’ai pas bien reçu votre message. Pouvez-vous reformuler ?",
                intent="clarification",
                action="local_empty_message",
                use_rag=False,
                use_llm=False,
                recommended_agent="EnnoAmel",
                needs_specialized_agent=False,
                confidence=0.95,
                normalized_question=normalized,
                debug={
                    "reason": "empty_message",
                    "processing_time": round(time.time() - t0, 3),
                },
            )

        # 1. Détection locale instantanée pour les messages humains simples.
        # Ici, on évite totalement l'appel LLM.
        local_decision = self._try_local_direct_chat(
            normalized=normalized,
            has_document=has_document,
            has_rag_index=has_rag_index,
        )

        if local_decision is not None:
            local_decision.debug["processing_time"] = round(time.time() - t0, 3)
            return local_decision

        # 2. Si ce n'est pas un message humain simple,
        # alors seulement on appelle le LLM pour comprendre l'intention.
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
            return ChatDecision(
                handled=False,
                answer="",
                intent="fallback_to_ennoamel",
                action="chat_fallback",
                use_rag=False,
                use_llm=True,
                recommended_agent="EnnoAmel",
                needs_specialized_agent=False,
                confidence=0.25,
                normalized_question=normalized,
                debug={
                    "error": str(exc),
                    "processing_time": round(time.time() - t0, 3),
                },
            )

    def _try_local_direct_chat(
        self,
        *,
        normalized: str,
        has_document: bool,
        has_rag_index: bool,
    ) -> Optional[ChatDecision]:
        """
        Détection locale des messages humains simples.

        Objectif :
          - répondre instantanément à bonjour, merci, ça va, très bien, etc. ;
          - éviter un appel LLM inutile ;
          - ne jamais traiter ici les questions liées au dossier.
        """

        greetings = {
            "bonjour",
            "bonsoir",
            "salut",
            "hello",
            "hi",
            "coucou",
            "salam",
            "salam alaykom",
            "salam alaykoum",
            "assalam alaykom",
            "assalam alaykoum",
            "salam alaikom",
            "salam alaikoum",
        }

        thanks = {
            "merci",
            "merci beaucoup",
            "thanks",
            "thank you",
            "chokran",
            "shukran",
            "baraka allah fik",
            "barakallah fik",
        }

        positive_followups = {
            "ok",
            "okay",
            "d accord",
            "daccord",
            "tres bien",
            "très bien",
            "parfait",
            "bien",
            "super",
            "c est bon",
            "cest bon",
            "oui",
            "yes",
        }

        how_are_you_patterns = [
            "ca va",
            "ça va",
            "cv",
            "tu vas bien",
            "vous allez bien",
            "comment ca va",
            "comment ça va",
            "et toi",
            "et vous",
            "labas",
            "labass",
            "labes",
        ]

        help_patterns = [
            "aide",
            "help",
            "tu peux faire quoi",
            "vous pouvez faire quoi",
            "que peux tu faire",
            "que pouvez vous faire",
            "comment tu peux m aider",
            "comment vous pouvez m aider",
        ]

        # Important :
        # Si le message contient déjà des mots métier,
        # on ne répond pas localement.
        # On laisse EnnoAmel / RAG / agents spécialisés gérer.
        dossier_keywords = [
            "projet",
            "dossier",
            "document",
            "resume",
            "résumé",
            "synthese",
            "synthèse",
            "mots cles",
            "mots clés",
            "keyword",
            "keywords",
            "entite",
            "entité",
            "entites",
            "entités",
            "verrou",
            "verrous",
            "incertitude",
            "incertitudes",
            "objectif",
            "objectifs",
            "methode",
            "méthode",
            "methodes",
            "méthodes",
            "protocole",
            "protocoles",
            "technologie",
            "technologies",
            "outil",
            "outils",
            "resultat",
            "résultat",
            "resultats",
            "résultats",
            "metrique",
            "métrique",
            "performance",
            "cir",
            "eligible",
            "éligible",
            "eligibilite",
            "éligibilité",
            "score",
            "taux",
            "diagnostic",
            "risque",
            "risques",
            "preuve",
            "preuves",
            "source",
            "sources",
            "passage",
            "passages",
            "etat de l art",
            "état de l art",
            "article",
            "articles",
            "publication",
            "publications",
            "bibliographie",
            "finance",
            "financier",
            "rh",
            "depense",
            "dépense",
            "depenses",
            "dépenses",
            "montant",
            "montants",
            "etp",
            "cerfa",
            "excel",
        ]

        if any(keyword in normalized for keyword in dossier_keywords):
            return None

        if normalized in greetings:
            return ChatDecision(
                handled=True,
                answer=(
                    "Bonjour, je suis là. Vous pouvez me demander un résumé, "
                    "les mots-clés, les verrous techniques ou une première lecture du dossier."
                ),
                intent="small_talk",
                action="local_direct_chat",
                use_rag=False,
                use_llm=False,
                recommended_agent="EnnoAmel",
                needs_specialized_agent=False,
                confidence=0.98,
                normalized_question=normalized,
                debug={"reason": "local_greeting"},
            )

        if normalized in thanks:
            return ChatDecision(
                handled=True,
                answer="Avec plaisir.",
                intent="thanks",
                action="local_direct_chat",
                use_rag=False,
                use_llm=False,
                recommended_agent="EnnoAmel",
                needs_specialized_agent=False,
                confidence=0.98,
                normalized_question=normalized,
                debug={"reason": "local_thanks"},
            )

        if normalized in positive_followups:
            if has_document or has_rag_index:
                answer = (
                    "Parfait. Dites-moi ce que vous souhaitez faire sur le dossier : "
                    "résumé, mots-clés, verrous, méthodes, résultats ou éligibilité CIR."
                )
            else:
                answer = (
                    "Parfait. Vous pouvez me transmettre un dossier ou me dire "
                    "ce que vous souhaitez analyser."
                )

            return ChatDecision(
                handled=True,
                answer=answer,
                intent="small_talk",
                action="local_direct_chat",
                use_rag=False,
                use_llm=False,
                recommended_agent="EnnoAmel",
                needs_specialized_agent=False,
                confidence=0.95,
                normalized_question=normalized,
                debug={"reason": "local_positive_followup"},
            )

        if any(pattern in normalized for pattern in how_are_you_patterns):
            return ChatDecision(
                handled=True,
                answer=(
                    "Ça va très bien, merci. Je suis prêt à vous aider "
                    "sur votre dossier EnnoSmart."
                ),
                intent="small_talk",
                action="local_direct_chat",
                use_rag=False,
                use_llm=False,
                recommended_agent="EnnoAmel",
                needs_specialized_agent=False,
                confidence=0.95,
                normalized_question=normalized,
                debug={"reason": "local_how_are_you"},
            )

        if any(pattern in normalized for pattern in help_patterns):
            return ChatDecision(
                handled=True,
                answer=(
                    "Je peux vous aider à comprendre un dossier, produire un résumé, "
                    "identifier les mots-clés, les verrous techniques, les objectifs R&D, "
                    "les méthodes, les technologies, les résultats, ou orienter vers "
                    "l’éligibilité CIR avec EnnoDiagnostic."
                ),
                intent="help",
                action="local_direct_chat",
                use_rag=False,
                use_llm=False,
                recommended_agent="EnnoAmel",
                needs_specialized_agent=False,
                confidence=0.96,
                normalized_question=normalized,
                debug={"reason": "local_help"},
            )

        return None

    def _build_system_prompt(self) -> str:
        return """
Tu es le module de chat intelligent d'EnnoSmart.

Tu n'es PAS EnnoAmel.
Tu es placé AVANT EnnoAmel pour comprendre l'intention utilisateur.

Objectif :
- répondre naturellement aux messages humains simples ;
- sinon rediriger vers EnnoAmel, le RAG ou l'agent spécialisé.

Tu dois répondre uniquement en JSON valide.

Intentions possibles :
- small_talk : salutation, discussion naturelle, "tu vas bien ?", "bonjour", "très bien", etc.
- thanks : remerciement simple.
- help : demande des capacités.
- project_summary : résumé simple ou idée générale du projet.
- keywords : mots-clés, entités, éléments extraits.
- verrous : verrous techniques, limites, incertitudes.
- objectives : objectifs R&D.
- methods : méthodes, protocoles, approches.
- technologies : outils, technologies, modèles, frameworks.
- results : résultats, métriques, performances.
- eligibility : taux, score, éligibilité CIR, diagnostic CIR.
- diagnostic_detail : analyse détaillée CIR, justification détaillée, risques, preuves, analyse approfondie.
- scholar : état de l'art, articles, publications, bibliographie.
- valorisation : finance, RH, dépenses, montants, ETP, Cerfa, Excel.
- source_proof : sources, preuves, passages exacts du document.
- document_question : question documentaire qui nécessite le dossier.
- clarification : message ambigu.

Règles strictes :
1. Si l'intention est small_talk, thanks ou help, tu peux répondre directement.
2. Pour small_talk, thanks et help : handled=true et answer doit être non vide.
3. Si l'utilisateur demande une petite vue simple du dossier :
   résumé simple, mots-clés, verrous, objectifs, méthodes, technologies ou résultats,
   alors handled=false, recommended_agent=EnnoAmel, needs_specialized_agent=false.
4. Si l'utilisateur demande une analyse détaillée, un diagnostic complet, des risques CIR,
   une justification approfondie, une analyse des preuves ou une validation d'éligibilité,
   alors intent=diagnostic_detail, handled=false, recommended_agent=EnnoDiagnostic,
   needs_specialized_agent=true.
5. Si l'utilisateur demande le taux, score ou l'éligibilité CIR,
   alors intent=eligibility, handled=false, recommended_agent=EnnoDiagnostic,
   needs_specialized_agent=true.
6. Si l'utilisateur demande l'état de l'art, des articles ou une bibliographie,
   alors intent=scholar, handled=false, recommended_agent=EnnoScholar,
   needs_specialized_agent=true.
7. Si l'utilisateur demande finance, RH, dépenses, montants, ETP, Cerfa ou Excel,
   alors intent=valorisation, handled=false, recommended_agent=EnnoValor,
   needs_specialized_agent=true.
8. Si l'utilisateur demande des sources ou preuves exactes, use_rag=true.
9. Tu ne dois jamais inventer des informations sur le dossier.
10. Tu ne dois jamais répondre en dehors du JSON.

Très important :
- Tu ne dois répondre directement que pour small_talk, thanks et help.
- Pour toutes les demandes liées au dossier, le champ answer doit être vide.
- Pour les demandes liées au dossier, handled doit être false.
- Ne donne jamais de résumé, de mots-clés, de verrous, de résultats ou de score toi-même.
- Pour une question comme "très bien", "ça va", "et toi", "que fais-tu aujourd'hui", intent=small_talk.

Format JSON obligatoire :
{
  "handled": true,
  "answer": "réponse naturelle si handled=true, sinon chaîne vide",
  "intent": "small_talk",
  "use_rag": false,
  "recommended_agent": "EnnoAmel",
  "needs_specialized_agent": false,
  "confidence": 0.95
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
            "domaine_principal": metadata.get("domaine_principal"),
            "metadata_keys_available": list(metadata.keys())[:40],
        }

        compact_history = chat_history[-4:] if chat_history else []

        return f"""
Message utilisateur :
{user_message}

Contexte technique disponible :
{json.dumps(context, ensure_ascii=False)}

Historique court :
{json.dumps(compact_history, ensure_ascii=False)}
""".strip()

    def _decision_from_json(
        self,
        *,
        parsed: dict[str, Any],
        raw: str,
        normalized: str,
        processing_time: float,
    ) -> ChatDecision:
        intent = str(parsed.get("intent") or "clarification").strip()
        answer = str(parsed.get("answer") or "").strip()

        handled = self._to_bool(parsed.get("handled", False))
        use_rag = self._to_bool(parsed.get("use_rag", False))
        needs_specialized_agent = self._to_bool(
            parsed.get("needs_specialized_agent", False)
        )

        recommended_agent = str(
            parsed.get("recommended_agent") or "EnnoAmel"
        ).strip()

        if recommended_agent not in {
            "EnnoAmel",
            "EnnoDiagnostic",
            "EnnoScholar",
            "EnnoValor",
        }:
            recommended_agent = "EnnoAmel"

        try:
            confidence = float(parsed.get("confidence", 0.75))
        except Exception:
            confidence = 0.75

        confidence = max(0.0, min(confidence, 1.0))

        direct_chat_intents = {
            "small_talk",
            "thanks",
            "help",
        }

        ennoamel_intents = {
            "project_summary",
            "keywords",
            "verrous",
            "objectives",
            "methods",
            "technologies",
            "results",
            "source_proof",
            "document_question",
        }

        specialized_diagnostic_intents = {
            "eligibility",
            "diagnostic_detail",
        }

        # 1. Chat humain : EnnoChat peut répondre directement.
        if intent in direct_chat_intents:
            recommended_agent = "EnnoAmel"
            needs_specialized_agent = False
            use_rag = False

            if answer:
                handled = True
            else:
                # Fallback propre si le petit modèle oublie la réponse.
                handled = True
                answer = self._fallback_direct_answer(intent, normalized)

        # 2. Vue simple dossier : EnnoAmel prend la main.
        elif intent in ennoamel_intents:
            handled = False
            answer = ""
            recommended_agent = "EnnoAmel"
            needs_specialized_agent = False

            if intent == "source_proof":
                use_rag = True

        # 3. Diagnostic / éligibilité : EnnoDiagnostic.
        elif intent in specialized_diagnostic_intents:
            handled = False
            answer = ""
            recommended_agent = "EnnoDiagnostic"
            needs_specialized_agent = True
            use_rag = False

        # 4. État de l'art : EnnoScholar.
        elif intent == "scholar":
            handled = False
            answer = ""
            recommended_agent = "EnnoScholar"
            needs_specialized_agent = True
            use_rag = False

        # 5. Finance / RH : EnnoValor.
        elif intent == "valorisation":
            handled = False
            answer = ""
            recommended_agent = "EnnoValor"
            needs_specialized_agent = True
            use_rag = False

        # 6. Clarification ou inconnu : EnnoChat peut demander une clarification.
        else:
            recommended_agent = "EnnoAmel"
            needs_specialized_agent = False
            use_rag = False

            if answer:
                handled = True
            else:
                handled = True
                intent = "clarification"
                answer = (
                    "Je ne suis pas sûr de comprendre votre demande. "
                    "Souhaitez-vous un résumé, les mots-clés, les verrous techniques, "
                    "les résultats ou le taux d’éligibilité CIR ?"
                )

        if recommended_agent == "EnnoAmel":
            needs_specialized_agent = False

        return ChatDecision(
            handled=handled,
            answer=answer,
            intent=intent,
            action="chat_understanding",
            use_rag=use_rag,
            use_llm=True,
            recommended_agent=recommended_agent,
            needs_specialized_agent=needs_specialized_agent,
            confidence=confidence,
            normalized_question=normalized,
            debug={
                "raw_llm_output": raw,
                "parsed": parsed,
                "processing_time": round(processing_time, 3),
            },
        )

    def _fallback_direct_answer(self, intent: str, normalized: str) -> str:
        if intent == "thanks":
            return "Avec plaisir."

        if intent == "help":
            return (
                "Je peux vous aider à comprendre un dossier, demander un résumé, "
                "identifier les mots-clés, les verrous techniques, les objectifs R&D, "
                "les technologies, les résultats ou orienter vers l’éligibilité CIR."
            )

        if "tres bien" in normalized or "très bien" in normalized:
            return "Parfait. Dites-moi ce que vous souhaitez faire sur le dossier."

        if "que fais" in normalized or "aujourd" in normalized:
            return "Je suis disponible pour vous aider sur votre dossier EnnoSmart."

        return "Oui, je vous écoute. Comment puis-je vous aider ?"

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
            "intent": "clarification",
            "use_rag": False,
            "recommended_agent": "EnnoAmel",
            "needs_specialized_agent": False,
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