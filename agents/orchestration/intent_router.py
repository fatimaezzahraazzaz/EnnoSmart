"""
modules/orchestration/intent_router.py — EnnoSmart / Orchestrateur POC
──────────────────────────────────────────────────────────────────────────────
Détection d'intention utilisateur pour l'orchestrateur Orchestrateur.

Rôle :
  - Comprendre ce que l'utilisateur veut faire.
  - Choisir l'action principale.
  - Recommander l'agent EnnoSmart correspondant.
  - Fournir une requête RAG adaptée à l'intention.

Architecture :
  Streamlit / API
      → Orchestrator
      → intent_router.py
      → RAG / agent spécialisé

Agents :
  - Orchestrateur       : résumé, chat documentaire, amélioration ciblée, orchestration.
  - EnnoDiagnostic : score CIR, éligibilité, verrous, risques, preuves.
  - EnnoScholar   : état de l'art, articles scientifiques, citations.
  - EnnoValor     : données financières/RH, Excel, Cerfa, livrables admin.

Important :
  Ce routeur est volontairement simple et explicable.
  Pour un POC, c'est mieux qu'un classifieur opaque.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ══════════════════════════════════════════════════════════════════════════════

class Intent(str, Enum):
    SUMMARY = "summary"
    QA = "qa"
    ELIGIBILITY = "eligibility"
    DIAGNOSTIC = "diagnostic"
    SCHOLAR = "scholar"
    VALOR = "valor"
    IMPROVE = "improve"
    EXTRACTION = "extraction"
    NLP = "nlp"
    RAG_DEBUG = "rag_debug"
    HELP = "help"
    CHAT = "chat"
    UNKNOWN = "unknown"


class AgentName(str, Enum):
    ORCHESTRATEUR = "Orchestrateur"
    ENNOAMEL = "Orchestrateur"  # alias compatibilité
    ENNODIAGNOSTIC = "EnnoDiagnostic"
    ENNOSCHOLAR = "EnnoScholar"
    ENNOVALOR = "EnnoValor"


# ══════════════════════════════════════════════════════════════════════════════
# DATACLASSES
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class IntentScore:
    intent: Intent
    score: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "score": round(float(self.score), 3),
            "matched_keywords": self.matched_keywords,
        }


@dataclass
class IntentDecision:
    intent: Intent
    recommended_agent: AgentName
    confidence: float
    action: str
    rag_query: str
    needs_rag: bool = True
    needs_document: bool = True
    is_specialized_agent_required: bool = False
    explanation: str = ""
    matched_keywords: list[str] = field(default_factory=list)
    scores: list[IntentScore] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "recommended_agent": self.recommended_agent.value,
            "confidence": round(float(self.confidence), 3),
            "action": self.action,
            "rag_query": self.rag_query,
            "needs_rag": self.needs_rag,
            "needs_document": self.needs_document,
            "is_specialized_agent_required": self.is_specialized_agent_required,
            "explanation": self.explanation,
            "matched_keywords": self.matched_keywords,
            "scores": [s.to_dict() for s in self.scores],
        }


# ══════════════════════════════════════════════════════════════════════════════
# KEYWORDS
# ══════════════════════════════════════════════════════════════════════════════

INTENT_KEYWORDS: dict[Intent, list[str]] = {
    Intent.SUMMARY: [
        "résume",
        "resume",
        "résumé",
        "resumé",
        "synthèse",
        "synthese",
        "idée générale",
        "idee generale",
        "vue globale",
        "overview",
        "présente le projet",
        "presente le projet",
        "de quoi parle",
        "compréhension du projet",
        "comprehension du projet",
        "explique le projet",
        "donne moi une idée",
        "donne-moi une idée",
        "donne une idée",
        "description du projet",
        "contexte du projet",
    ],

    Intent.QA: [
        "quels sont",
        "quelles sont",
        "quel est",
        "quelle est",
        "explique",
        "montre",
        "liste",
        "donne",
        "trouve",
        "cherche",
        "où",
        "ou",
        "comment",
        "pourquoi",
        "combien",
        "identifier",
        "identifie",
        "extrais",
        "extrait",
    ],

    Intent.ELIGIBILITY: [
        "éligible",
        "eligible",
        "éligibilité",
        "eligibilite",
        "score cir",
        "score d'éligibilité",
        "score d’eligibilite",
        "score d'éligibilite",
        "risque cir",
        "niveau de risque",
        "qualification cir",
        "crédit d'impôt recherche",
        "credit d'impot recherche",
        "cir",
        "incertitude scientifique",
        "incertitude technique",
        "verrou technologique",
        "verrous technologiques",
        "verrou technique",
        "verrous techniques",
        "démarche scientifique",
        "demarche scientifique",
        "démarche expérimentale",
        "demarche experimentale",
    ],

    Intent.DIAGNOSTIC: [
        "diagnostic",
        "analyse complète",
        "analyse complete",
        "analyse détaillée",
        "analyse detaillee",
        "preuves",
        "preuve",
        "justification",
        "justifie",
        "passages importants",
        "passages clés",
        "passages cles",
        "risques",
        "verrous",
        "audit cir",
        "rapport diagnostic",
        "évaluation cir",
        "evaluation cir",
    ],

    Intent.SCHOLAR: [
        "état de l'art",
        "etat de l'art",
        "état de l’art",
        "state of the art",
        "articles scientifiques",
        "article scientifique",
        "publication",
        "publications",
        "papier scientifique",
        "papers",
        "semantic scholar",
        "arxiv",
        "openalex",
        "bibliographie",
        "citations",
        "citation",
        "références scientifiques",
        "references scientifiques",
        "gap analysis",
        "gap",
        "travaux existants",
        "recherche scientifique",
        "littérature",
        "litterature",
    ],

    Intent.VALOR: [
        "finance",
        "financier",
        "financière",
        "financiere",
        "rh",
        "ressources humaines",
        "dépenses",
        "depenses",
        "dépense",
        "depense",
        "montant",
        "montants",
        "budget",
        "coût",
        "cout",
        "etp",
        "personnel",
        "salaires",
        "cerfa",
        "excel",
        "template",
        "livrable administratif",
        "livrables administratifs",
        "mapping",
        "remplir le fichier",
        "remplir l'excel",
        "remplir cerfa",
        "déclaration",
        "declaration",
    ],

    Intent.IMPROVE: [
        "améliore",
        "ameliore",
        "améliorer",
        "ameliorer",
        "réécris",
        "reecris",
        "réécrire",
        "reecrire",
        "reformule",
        "reformuler",
        "corrige ce texte",
        "corriger ce texte",
        "optimise",
        "optimiser",
        "rends plus clair",
        "rendre plus clair",
        "rends professionnel",
        "style cir",
        "amélioration du texte",
        "amelioration du texte",
    ],

    Intent.EXTRACTION: [
        "extraction",
        "extraire le texte",
        "extrais le texte",
        "ocr",
        "pdf",
        "docx",
        "pptx",
        "excel",
        "email",
        "image",
        "formule",
        "formules",
        "tableau",
        "tableaux",
        "convertir",
        "parser",
        "parse",
    ],

    Intent.NLP: [
        "nlp",
        "ner",
        "gliner",
        "entités",
        "entites",
        "mots clés",
        "mots cles",
        "normalisation",
        "cleaner",
        "nettoyage",
        "domaine principal",
        "metadata",
        "métadonnées",
        "metadonnees",
        "classification",
    ],

    Intent.RAG_DEBUG: [
        "debug rag",
        "recherche sans llm",
        "chunks récupérés",
        "chunks recuperes",
        "sources récupérées",
        "sources recuperees",
        "top k",
        "top-k",
        "score vectoriel",
        "vector score",
        "metadata bonus",
        "retriever",
        "chroma",
        "chromadb",
        "embedding",
        "bge",
    ],

    Intent.CHAT: [
        "bonjour",
        "bonsoir",
        "salut",
        "hello",
        "merci",
        "merci beaucoup",
        "au revoir",
        "ça va",
        "ca va",
        "comment vas",
        "comment allez",
        "qui es-tu",
        "qui es tu",
        "tu es quoi",
        "présente-toi",
        "presente toi",
        "c'est quoi ennoamel",
        "c est quoi ennoamel",
    ],

    Intent.HELP: [
        "aide",
        "help",
        "que peux-tu faire",
        "que peut faire",
        "comment utiliser",
        "mode d'emploi",
        "mode emploi",
        "guide",
        "commande",
        "commandes",
    ],
}


# Poids par intention.
# Les intentions spécialisées reçoivent un poids plus fort que QA.
INTENT_WEIGHTS: dict[Intent, float] = {
    Intent.SUMMARY: 1.20,
    Intent.QA: 0.70,
    Intent.ELIGIBILITY: 1.55,
    Intent.DIAGNOSTIC: 1.45,
    Intent.SCHOLAR: 1.45,
    Intent.VALOR: 1.45,
    Intent.IMPROVE: 1.30,
    Intent.EXTRACTION: 1.10,
    Intent.NLP: 1.10,
    Intent.RAG_DEBUG: 1.20,
    Intent.HELP: 1.10,
    Intent.CHAT: 1.50,
}


# Si ces mots apparaissent, l'intention est très probablement spécialisée.
STRONG_HINTS: dict[Intent, list[str]] = {
    Intent.ELIGIBILITY: [
        "éligible",
        "eligible",
        "éligibilité",
        "eligibilite",
        "score cir",
        "niveau de risque",
        "qualification cir",
    ],
    Intent.SCHOLAR: [
        "état de l'art",
        "etat de l'art",
        "semantic scholar",
        "arxiv",
        "openalex",
        "articles scientifiques",
    ],
    Intent.VALOR: [
        "cerfa",
        "etp",
        "dépenses",
        "depenses",
        "montants",
        "mapping",
        "excel",
    ],
    Intent.IMPROVE: [
        "améliore",
        "ameliore",
        "réécris",
        "reecris",
        "reformule",
        "corrige ce texte",
    ],
    Intent.RAG_DEBUG: [
        "debug rag",
        "recherche sans llm",
        "score vectoriel",
        "metadata bonus",
    ],
}


# ══════════════════════════════════════════════════════════════════════════════
# UTILS
# ══════════════════════════════════════════════════════════════════════════════

def _normalize(text: str) -> str:
    """
    Normalisation simple pour matching.
    On garde les accents possibles, mais on unifie apostrophes/espaces.
    """
    text = str(text or "").lower()
    text = text.replace("’", "'").replace("`", "'")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _contains_keyword(text: str, keyword: str) -> bool:
    """
    Détection keyword souple :
      - phrase exacte si keyword contient espace/apostrophe/tiret ;
      - sinon boundary approximative.
    """
    text = _normalize(text)
    keyword = _normalize(keyword)

    if not keyword:
        return False

    if " " in keyword or "'" in keyword or "-" in keyword:
        return keyword in text

    pattern = r"(?<![\wÀ-ÿ])" + re.escape(keyword) + r"(?![\wÀ-ÿ])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _matched_keywords(text: str, keywords: list[str]) -> list[str]:
    matches = []
    for kw in keywords:
        if _contains_keyword(text, kw):
            matches.append(kw)
    return matches


def _score_intents(user_message: str) -> list[IntentScore]:
    scores: list[IntentScore] = []

    for intent, keywords in INTENT_KEYWORDS.items():
        matches = _matched_keywords(user_message, keywords)
        if not matches:
            scores.append(IntentScore(intent=intent, score=0.0, matched_keywords=[]))
            continue

        base = len(matches)
        weight = INTENT_WEIGHTS.get(intent, 1.0)

        score = base * weight

        # Bonus pour indices forts.
        for strong in STRONG_HINTS.get(intent, []):
            if _contains_keyword(user_message, strong):
                score += 1.5

        # Bonus phrase longue exacte.
        score += sum(0.25 for kw in matches if len(kw.split()) >= 3)

        scores.append(IntentScore(intent=intent, score=score, matched_keywords=matches))

    scores.sort(key=lambda s: s.score, reverse=True)
    return scores


def _agent_for_intent(intent: Intent) -> AgentName:
    if intent in {Intent.ELIGIBILITY, Intent.DIAGNOSTIC}:
        return AgentName.ENNODIAGNOSTIC

    if intent == Intent.SCHOLAR:
        return AgentName.ENNOSCHOLAR

    if intent == Intent.VALOR:
        return AgentName.ENNOVALOR

    return AgentName.ORCHESTRATEUR


def _specialized_required(intent: Intent) -> bool:
    return intent in {
        Intent.ELIGIBILITY,
        Intent.DIAGNOSTIC,
        Intent.SCHOLAR,
        Intent.VALOR,
    }


def _needs_document(intent: Intent) -> bool:
    if intent in {Intent.HELP, Intent.CHAT}:
        return False
    return True


def _needs_rag(intent: Intent) -> bool:
    if intent in {Intent.HELP, Intent.CHAT, Intent.EXTRACTION, Intent.NLP}:
        return False
    # improve peut utiliser RAG si amélioration d'une section documentaire,
    # mais pas obligatoire. Pour le POC, on le laisse True si document chargé.
    return True


def _confidence_from_score(best_score: float, second_score: float) -> float:
    if best_score <= 0:
        return 0.45

    margin = best_score - second_score

    # Score absolu + marge.
    confidence = 0.55 + min(best_score * 0.06, 0.25) + min(margin * 0.08, 0.20)

    return max(0.45, min(confidence, 0.96))


def _build_action(intent: Intent, agent: AgentName) -> str:
    if intent == Intent.SUMMARY:
        return "answer_summary_with_rag"

    if intent == Intent.QA:
        return "answer_question_with_rag"

    if intent == Intent.ELIGIBILITY:
        return "preliminary_cir_estimation_then_redirect"

    if intent == Intent.DIAGNOSTIC:
        return "diagnostic_preview_then_redirect"

    if intent == Intent.SCHOLAR:
        return "answer_available_context_then_redirect_scholar"

    if intent == Intent.VALOR:
        return "answer_available_context_then_redirect_valor"

    if intent == Intent.IMPROVE:
        return "improve_text_or_section"

    if intent == Intent.EXTRACTION:
        return "run_extraction_or_show_extraction_status"

    if intent == Intent.NLP:
        return "run_nlp_or_show_nlp_metadata"

    if intent == Intent.RAG_DEBUG:
        return "debug_retrieval_sources"

    if intent == Intent.HELP:
        return "show_capabilities"

    if intent == Intent.CHAT:
        return "chat_direct_response"

    return "fallback_rag_answer"


def _build_explanation(intent: Intent, agent: AgentName, matches: list[str]) -> str:
    if intent == Intent.SUMMARY:
        return "La demande porte sur une vue globale du projet. Orchestrateur peut répondre directement avec le RAG."

    if intent == Intent.QA:
        return "La demande est une question documentaire générale. Orchestrateur répond à partir des sources RAG."

    if intent == Intent.ELIGIBILITY:
        return "La demande concerne l'éligibilité CIR ou le score. Orchestrateur donne une estimation préliminaire, puis recommande EnnoDiagnostic."

    if intent == Intent.DIAGNOSTIC:
        return "La demande nécessite une analyse CIR détaillée. L'agent cible est EnnoDiagnostic."

    if intent == Intent.SCHOLAR:
        return "La demande concerne l'état de l'art ou la recherche scientifique. L'agent cible est EnnoScholar."

    if intent == Intent.VALOR:
        return "La demande concerne les données financières, RH ou livrables administratifs. L'agent cible est EnnoValor."

    if intent == Intent.IMPROVE:
        return "La demande concerne l'amélioration ou la reformulation d'un texte. Orchestrateur peut traiter cette action."

    if intent == Intent.EXTRACTION:
        return "La demande concerne l'extraction documentaire. Orchestrateur doit lancer ou afficher l'étape Extraction."

    if intent == Intent.NLP:
        return "La demande concerne les entités, métadonnées ou résultats NLP. Orchestrateur doit utiliser ou afficher l'étape NLP."

    if intent == Intent.RAG_DEBUG:
        return "La demande concerne le debug RAG, les chunks ou les scores. Orchestrateur doit afficher les sources récupérées."

    if intent == Intent.HELP:
        return "La demande concerne les capacités disponibles du POC."

    if intent == Intent.CHAT:
        return "La demande est une conversation humaine simple. Orchestrateur peut répondre sans document ni RAG."

    return "Intention incertaine. Orchestrateur utilisera une réponse RAG générale si un document est chargé."


def _build_rag_query(user_message: str, intent: Intent) -> str:
    """
    Reformule légèrement la requête pour mieux orienter le RAG.
    On ne change pas trop le sens : la question originale reste principale.
    """
    msg = str(user_message or "").strip()

    if intent == Intent.SUMMARY:
        return (
            msg
            + "\nRésumé stratégique du projet : domaine, objet de recherche, objectifs, verrous, méthodes, outils, résultats."
        )

    if intent == Intent.ELIGIBILITY:
        return (
            msg
            + "\nÉléments CIR : verrous techniques, incertitudes, objectifs R&D, démarche scientifique, résultats, preuves, risques."
        )

    if intent == Intent.DIAGNOSTIC:
        return (
            msg
            + "\nDiagnostic CIR : verrous, preuves, risques, incertitudes, démarche R&D, justification."
        )

    if intent == Intent.SCHOLAR:
        return (
            msg
            + "\nÉtat de l'art : travaux existants, références scientifiques, méthodes comparées, limites, gap analysis."
        )

    if intent == Intent.VALOR:
        return (
            msg
            + "\nValorisation CIR : dépenses, ETP, personnel, montants, RH, Excel, Cerfa, données financières."
        )

    if intent == Intent.IMPROVE:
        return (
            msg
            + "\nContexte utile pour améliorer le texte selon le dossier CIR."
        )

    return msg


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def detect_intent(
    user_message: str,
    has_document: bool = True,
    has_rag_index: bool = True,
) -> IntentDecision:
    """
    Détecte l'intention utilisateur.

    Paramètres :
      user_message : message utilisateur.
      has_document : document déjà préparé par l'orchestrateur.
      has_rag_index : RAG déjà indexé.

    Retour :
      IntentDecision
    """
    msg = str(user_message or "").strip()

    if not msg:
        return IntentDecision(
            intent=Intent.UNKNOWN,
            recommended_agent=AgentName.ORCHESTRATEUR,
            confidence=0.0,
            action="empty_message",
            rag_query="",
            needs_rag=False,
            needs_document=False,
            is_specialized_agent_required=False,
            explanation="Message vide.",
            matched_keywords=[],
            scores=[],
        )

    scores = _score_intents(msg)
    best = scores[0] if scores else IntentScore(Intent.UNKNOWN, 0.0, [])
    second = scores[1] if len(scores) > 1 else IntentScore(Intent.UNKNOWN, 0.0, [])

    # Si aucun score, fallback QA.
    if best.score <= 0:
        intent = Intent.QA
        matched = []
        confidence = 0.50
    else:
        intent = best.intent
        matched = best.matched_keywords
        confidence = _confidence_from_score(best.score, second.score)

    # Cas particulier :
    # Si QA gagne seulement avec "donne/explique/liste", mais eligibility/scholar/valor
    # a un score proche, on favorise l'agent spécialisé.
    specialized_candidates = [
        s for s in scores
        if s.intent in {Intent.ELIGIBILITY, Intent.DIAGNOSTIC, Intent.SCHOLAR, Intent.VALOR}
        and s.score > 0
    ]

    if specialized_candidates:
        spec_best = max(specialized_candidates, key=lambda x: x.score)
        if intent == Intent.QA and spec_best.score >= best.score * 0.75:
            intent = spec_best.intent
            matched = spec_best.matched_keywords
            confidence = max(confidence, 0.72)

    agent = _agent_for_intent(intent)
    action = _build_action(intent, agent)
    explanation = _build_explanation(intent, agent, matched)
    rag_query = _build_rag_query(msg, intent)

    needs_doc = _needs_document(intent)
    needs_rag = _needs_rag(intent)

    # Si pas de document/index, on garde l'intention mais action adaptée.
    if needs_doc and not has_document:
        action = "need_document_first"
        needs_rag = False
        explanation += " Aucun document n'est encore préparé : il faut d'abord lancer l'ingestion documentaire."

    elif needs_rag and not has_rag_index:
        action = "need_rag_index_first"
        explanation += " Le document existe mais le RAG n'est pas encore indexé."

    return IntentDecision(
        intent=intent,
        recommended_agent=agent,
        confidence=confidence,
        action=action,
        rag_query=rag_query,
        needs_rag=needs_rag,
        needs_document=needs_doc,
        is_specialized_agent_required=_specialized_required(intent),
        explanation=explanation,
        matched_keywords=matched,
        scores=scores,
    )


def route_question(
    user_message: str,
    has_document: bool = True,
    has_rag_index: bool = True,
) -> dict[str, Any]:
    """
    Helper simple pour Streamlit/API.
    Retourne directement un dict JSON-serializable.
    """
    return detect_intent(
        user_message=user_message,
        has_document=has_document,
        has_rag_index=has_rag_index,
    ).to_dict()


def is_specialized_request(user_message: str) -> bool:
    decision = detect_intent(user_message)
    return decision.is_specialized_agent_required


def recommended_agent_for(user_message: str) -> str:
    decision = detect_intent(user_message)
    return decision.recommended_agent.value


# ══════════════════════════════════════════════════════════════════════════════
# TEST LOCAL RAPIDE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [
        "Donne-moi une idée générale du projet",
        "Est-ce que ce projet est éligible CIR ?",
        "Quels sont les verrous techniques ?",
        "Fais-moi l'état de l'art scientifique",
        "Extrais les montants et les ETP",
        "Améliore ce paragraphe pour le dossier CIR",
        "Montre les chunks récupérés et les scores",
        "Que peux-tu faire ?",
    ]

    for t in tests:
        d = detect_intent(t)
        print("\nQUESTION:", t)
        print(d.to_dict())