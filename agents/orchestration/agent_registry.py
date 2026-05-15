"""
modules/orchestration/agent_registry.py — EnnoSmart / EnnoAmel POC
──────────────────────────────────────────────────────────────────────────────
Registre central des agents EnnoSmart.

Rôle :
  - Déclarer les agents disponibles dans l'architecture.
  - Décrire leur rôle, statut et capacités.
  - Permettre à EnnoAmel de rediriger proprement l'utilisateur.
  - Fournir des informations affichables dans Streamlit.

Agents :
  - EnnoAmel        : orchestrateur, résumé, chat documentaire, amélioration ciblée.
  - EnnoDiagnostic : score CIR, risques, preuves, diagnostic d'éligibilité.
  - EnnoScholar    : état de l'art, articles scientifiques, citations.
  - EnnoValor      : finance/RH, Excel, Cerfa, livrables administratifs.

Ce registre ne lance pas les agents.
Il décrit leur disponibilité et leur rôle dans le POC.
"""

from __future__ import annotations

from typing import Any, Optional

from agents.orchestration.schemas import (
    AgentCapability,
    AgentInfo,
    AgentKind,
    AgentRoute,
    AgentStatus,
)


# ══════════════════════════════════════════════════════════════════════════════
# AGENT DEFINITIONS
# ══════════════════════════════════════════════════════════════════════════════

AGENT_REGISTRY: dict[str, AgentInfo] = {
    "EnnoAmel": AgentInfo(
        name="EnnoAmel",
        kind=AgentKind.ORCHESTRATOR,
        status=AgentStatus.AVAILABLE,
        role="Orchestrateur central intelligent",
        description=(
            "Agent central du POC EnnoSmart. Il coordonne Extraction, NLP et RAG, "
            "répond aux questions documentaires simples, produit des résumés stratégiques "
            "et redirige vers les agents spécialisés selon le besoin."
        ),
        supported_intents=[
            "chat",
            "small_talk",
            "thanks",
            "help",
            "summary",
            "project_summary",
            "qa",
            "keywords",
            "verrous",
            "objectives",
            "methods",
            "technologies",
            "results",
            "source_proof",
            "document_question",
            "improve",
            "extraction",
            "nlp",
            "rag_debug",
        ],
        capabilities=[
            AgentCapability(
                name="Résumé stratégique",
                description="Produit une idée générale du projet à partir des sources RAG.",
                examples=[
                    "Donne-moi une idée générale du projet.",
                    "Résume ce dossier CIR.",
                    "Présente le projet en quelques points.",
                ],
            ),
            AgentCapability(
                name="Chat documentaire sourcé",
                description="Répond aux questions utilisateur à partir des chunks indexés.",
                examples=[
                    "Quels sont les verrous techniques ?",
                    "Quels outils sont utilisés ?",
                    "Quels résultats sont mentionnés ?",
                ],
            ),
            AgentCapability(
                name="Orchestration",
                description="Détecte l'intention et recommande EnnoDiagnostic, EnnoScholar ou EnnoValor si nécessaire.",
                examples=[
                    "Est-ce que ce projet est éligible CIR ?",
                    "Fais l'état de l'art.",
                    "Extrais les montants et les ETP.",
                ],
            ),
            AgentCapability(
                name="Amélioration ciblée",
                description="Aide à reformuler ou améliorer un texte en contexte CIR.",
                examples=[
                    "Améliore ce paragraphe.",
                    "Reformule ce verrou technique.",
                    "Rends ce texte plus professionnel.",
                ],
            ),
        ],
        poc_message=(
            "EnnoAmel est disponible dans le POC. Il peut déjà utiliser Extraction, NLP et RAG "
            "pour répondre avec sources et orienter l'utilisateur vers les bons modules."
        ),
        available_in_poc=True,
    ),

    "EnnoDiagnostic": AgentInfo(
        name="EnnoDiagnostic",
        kind=AgentKind.DIAGNOSTIC,
        status=AgentStatus.IN_PROGRESS,
        role="Agent de diagnostic CIR",
        description=(
            "Module spécialisé pour analyser l'éligibilité CIR d'un dossier. "
            "Il doit identifier les verrous technologiques, les incertitudes scientifiques, "
            "les preuves, les risques et produire un score d'éligibilité avec justification."
        ),
        supported_intents=[
            "eligibility",
            "diagnostic",
        ],
        capabilities=[
            AgentCapability(
                name="Score d'éligibilité CIR",
                description="Produit un score d'éligibilité basé sur les verrous, la démarche R&D, les preuves et les résultats.",
                examples=[
                    "Est-ce que ce projet est éligible CIR ?",
                    "Donne-moi un score CIR.",
                    "Quel est le niveau de risque du dossier ?",
                ],
            ),
            AgentCapability(
                name="Analyse des verrous",
                description="Analyse les verrous techniques et les incertitudes scientifiques du projet.",
                examples=[
                    "Quels sont les verrous technologiques ?",
                    "Les incertitudes sont-elles suffisantes pour le CIR ?",
                ],
            ),
            AgentCapability(
                name="Preuves et passages clés",
                description="Met en avant les passages justifiant l'analyse CIR.",
                examples=[
                    "Montre les preuves dans le document.",
                    "Quels passages justifient l'éligibilité ?",
                ],
            ),
            AgentCapability(
                name="Niveau de risque",
                description="Évalue les risques de rejet ou de faiblesse du dossier.",
                examples=[
                    "Quels sont les risques CIR ?",
                    "Quels éléments manquent pour sécuriser le dossier ?",
                ],
            ),
        ],
        poc_message=(
            "EnnoDiagnostic est en cours de construction. Dans le POC, EnnoAmel peut fournir "
            "une estimation préliminaire basée sur le RAG, mais pas encore un diagnostic CIR définitif."
        ),
        available_in_poc=False,
    ),

    "EnnoScholar": AgentInfo(
        name="EnnoScholar",
        kind=AgentKind.SCHOLAR,
        status=AgentStatus.PLANNED,
        role="Agent d'état de l'art scientifique",
        description=(
            "Module spécialisé dans la recherche scientifique et la construction de l'état de l'art. "
            "Il interrogera des bases comme Semantic Scholar, ArXiv ou OpenAlex, classera les articles "
            "et générera une synthèse scientifique sourcée."
        ),
        supported_intents=[
            "scholar",
        ],
        capabilities=[
            AgentCapability(
                name="Recherche scientifique",
                description="Recherche des articles pertinents à partir des verrous et mots-clés du projet.",
                examples=[
                    "Trouve des articles scientifiques sur ce verrou.",
                    "Cherche l'état de l'art.",
                    "Quels papiers sont proches de ce projet ?",
                ],
            ),
            AgentCapability(
                name="Classement des articles",
                description="Classe les articles selon leur pertinence : direct, connexe ou fondamental.",
                examples=[
                    "Classe ces articles par pertinence.",
                    "Quels articles sont directement liés au verrou ?",
                ],
            ),
            AgentCapability(
                name="Gap analysis",
                description="Compare les verrous du projet avec les limites de l'état de l'art.",
                examples=[
                    "Quel est le gap scientifique ?",
                    "Qu'est-ce qui distingue ce projet des travaux existants ?",
                ],
            ),
            AgentCapability(
                name="Génération d'état de l'art",
                description="Génère un état de l'art structuré avec citations.",
                examples=[
                    "Génère l'état de l'art avec citations.",
                    "Fais une synthèse scientifique.",
                ],
            ),
        ],
        poc_message=(
            "EnnoScholar n'est pas encore implémenté dans le POC. EnnoAmel peut identifier "
            "les verrous et mots-clés utiles, puis recommander le passage à EnnoScholar."
        ),
        available_in_poc=False,
    ),

    "EnnoValor": AgentInfo(
        name="EnnoValor",
        kind=AgentKind.VALOR,
        status=AgentStatus.PLANNED,
        role="Agent de valorisation administrative et financière",
        description=(
            "Module spécialisé dans l'extraction des données financières et RH, le mapping vers Excel/Cerfa, "
            "la traçabilité des valeurs et la production des livrables administratifs."
        ),
        supported_intents=[
            "valor",
        ],
        capabilities=[
            AgentCapability(
                name="Extraction financière",
                description="Identifie les dépenses, montants, budgets et indicateurs financiers.",
                examples=[
                    "Extrais les montants du dossier.",
                    "Quels budgets sont mentionnés ?",
                    "Quelles dépenses sont éligibles ?",
                ],
            ),
            AgentCapability(
                name="Extraction RH",
                description="Identifie les ETP, personnels, rôles, périodes et données RH.",
                examples=[
                    "Extrais les ETP.",
                    "Quels personnels sont associés au projet ?",
                    "Quelles périodes sont mentionnées ?",
                ],
            ),
            AgentCapability(
                name="Mapping Excel / Cerfa",
                description="Associe les valeurs extraites aux modèles cibles.",
                examples=[
                    "Remplis le fichier Excel.",
                    "Prépare le Cerfa.",
                    "Mappe les données financières.",
                ],
            ),
            AgentCapability(
                name="Traçabilité des valeurs",
                description="Lie chaque valeur extraite à sa source documentaire.",
                examples=[
                    "Montre la source du montant.",
                    "Où est mentionné cet ETP ?",
                ],
            ),
        ],
        poc_message=(
            "EnnoValor n'est pas encore implémenté dans le POC. EnnoAmel peut repérer certaines "
            "informations financières/RH dans les sources, mais la valorisation finale devra passer par EnnoValor."
        ),
        available_in_poc=False,
    ),
}


# ══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════════════════════════════════

def get_agent(agent_name: str) -> Optional[AgentInfo]:
    """
    Retourne les informations d'un agent.
    """
    return AGENT_REGISTRY.get(str(agent_name or "").strip())


def list_agents() -> list[AgentInfo]:
    """
    Retourne tous les agents déclarés.
    """
    return list(AGENT_REGISTRY.values())


def list_agents_dict() -> list[dict[str, Any]]:
    """
    Retourne tous les agents sous forme JSON-serializable.
    Utile pour Streamlit/API.
    """
    return [agent.to_dict() for agent in list_agents()]


def get_available_agents() -> list[AgentInfo]:
    """
    Retourne les agents réellement disponibles dans le POC.
    """
    return [
        agent for agent in AGENT_REGISTRY.values()
        if agent.available_in_poc and agent.status == AgentStatus.AVAILABLE
    ]


def get_available_agents_dict() -> list[dict[str, Any]]:
    return [agent.to_dict() for agent in get_available_agents()]


def get_agent_status(agent_name: str) -> str:
    agent = get_agent(agent_name)
    return agent.status.value if agent else AgentStatus.DISABLED.value


def is_agent_available(agent_name: str) -> bool:
    agent = get_agent(agent_name)
    if not agent:
        return False
    return bool(agent.available_in_poc and agent.status == AgentStatus.AVAILABLE)


def get_agent_poc_message(agent_name: str) -> str:
    agent = get_agent(agent_name)
    if not agent:
        return "Agent inconnu dans le registre EnnoSmart."
    return agent.poc_message


def get_agent_role(agent_name: str) -> str:
    agent = get_agent(agent_name)
    if not agent:
        return "Agent inconnu."
    return agent.role


def get_agents_for_intent(intent: str) -> list[AgentInfo]:
    """
    Retourne les agents qui supportent une intention donnée.
    """
    intent = str(intent or "").strip()

    return [
        agent for agent in AGENT_REGISTRY.values()
        if intent in agent.supported_intents
    ]


def recommend_agent_for_intent(intent: str) -> AgentInfo:
    """
    Recommande un agent à partir de l'intention.

    Cette fonction reste cohérente avec intent_router.py.
    """
    intent = str(intent or "").strip()

    if intent in {"eligibility", "diagnostic"}:
        return AGENT_REGISTRY["EnnoDiagnostic"]

    if intent == "scholar":
        return AGENT_REGISTRY["EnnoScholar"]

    if intent == "valor":
        return AGENT_REGISTRY["EnnoValor"]

    return AGENT_REGISTRY["EnnoAmel"]


def build_agent_route(
    *,
    intent: str,
    action: str,
    confidence: float,
    reason: str = "",
    preferred_agent: Optional[str] = None,
) -> AgentRoute:
    """
    Construit une décision de routage enrichie avec statut de l'agent.

    Exemple :
      route = build_agent_route(
          intent="eligibility",
          action="preliminary_cir_estimation_then_redirect",
          confidence=0.82,
      )
    """
    agent = get_agent(preferred_agent) if preferred_agent else recommend_agent_for_intent(intent)

    if agent is None:
        agent = AGENT_REGISTRY["EnnoAmel"]

    return AgentRoute(
        agent_name=agent.name,
        intent=intent,
        action=action,
        confidence=confidence,
        requires_specialized_agent=agent.name != "EnnoAmel",
        reason=reason,
        agent_status=agent.status.value,
        available_in_poc=agent.available_in_poc,
    )


def get_registry_summary() -> dict[str, Any]:
    """
    Résumé compact pour logs ou sidebar Streamlit.
    """
    agents = list_agents()

    return {
        "total_agents": len(agents),
        "available_agents": [
            a.name for a in agents
            if a.status == AgentStatus.AVAILABLE and a.available_in_poc
        ],
        "in_progress_agents": [
            a.name for a in agents
            if a.status == AgentStatus.IN_PROGRESS
        ],
        "planned_agents": [
            a.name for a in agents
            if a.status == AgentStatus.PLANNED
        ],
        "agents": [a.to_dict() for a in agents],
    }


def format_agent_sidebar_markdown() -> str:
    """
    Produit un bloc Markdown simple pour Streamlit sidebar.
    """
    lines = ["### Agents EnnoSmart"]

    for agent in AGENT_REGISTRY.values():
        if agent.status == AgentStatus.AVAILABLE:
            emoji = "✅"
        elif agent.status == AgentStatus.IN_PROGRESS:
            emoji = "🚧"
        elif agent.status == AgentStatus.PLANNED:
            emoji = "🕓"
        else:
            emoji = "⛔"

        lines.append(f"- {emoji} **{agent.name}** — {agent.role}")

    return "\n".join(lines)


def format_agent_card(agent_name: str) -> str:
    """
    Produit une fiche agent Markdown.
    """
    agent = get_agent(agent_name)

    if not agent:
        return f"### {agent_name}\nAgent inconnu."

    status_label = {
        AgentStatus.AVAILABLE: "Disponible",
        AgentStatus.IN_PROGRESS: "En cours de construction",
        AgentStatus.PLANNED: "Prévu",
        AgentStatus.DISABLED: "Désactivé",
    }.get(agent.status, agent.status.value)

    lines = [
        f"### {agent.name}",
        f"**Statut :** {status_label}",
        f"**Rôle :** {agent.role}",
        "",
        agent.description,
        "",
        "**Capacités :**",
    ]

    for cap in agent.capabilities:
        lines.append(f"- **{cap.name}** : {cap.description}")

    if agent.poc_message:
        lines.extend(["", f"**Note POC :** {agent.poc_message}"])

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# TEST LOCAL RAPIDE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import json

    print("\n── REGISTRY SUMMARY ─────────────────────────")
    print(json.dumps(get_registry_summary(), ensure_ascii=False, indent=2))

    print("\n── SIDEBAR MARKDOWN ─────────────────────────")
    print(format_agent_sidebar_markdown())

    print("\n── ROUTES ───────────────────────────────────")
    for intent in ["summary", "eligibility", "scholar", "valor", "qa"]:
        route = build_agent_route(
            intent=intent,
            action="test_action",
            confidence=0.8,
            reason="test",
        )
        print(json.dumps(route.to_dict(), ensure_ascii=False, indent=2))