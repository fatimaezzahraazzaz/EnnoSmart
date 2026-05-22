"""
modules/orchestration/response_builder.py — EnnoSmart / Orchestrateur POC
──────────────────────────────────────────────────────────────────────────────
Builder de réponses pour l'orchestrateur Orchestrateur.

Rôle :
  - Construire une réponse finale propre à partir :
      - de la décision d'intention ;
      - de la réponse RAG ;
      - du registre des agents ;
      - de l'état du workflow.
  - Ajouter les notes POC :
      - EnnoDiagnostic en cours ;
      - EnnoScholar prévu ;
      - EnnoValor prévu.
  - Formater les sources pour Streamlit/API.
  - Éviter de mettre toute la logique de présentation dans ennoamel.py.

Architecture :
  intent_router.py
      → agent_registry.py
      → rag_pipeline.py / query_engine.py
      → response_builder.py
      → Streamlit / API
"""

from __future__ import annotations

import time
from typing import Any, Optional

from agents.orchestration.schemas import (
    BuiltResponse,
    SourceRef,
    WorkflowReport,
    StepStatus,
)
from agents.orchestration.agent_registry import (
    get_agent,
    get_agent_poc_message,
    build_agent_route,
    format_agent_card,
)


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def normalize_sources(raw_sources: list[dict[str, Any]] | None) -> list[SourceRef]:
    """
    Convertit les sources retournées par le RAG en SourceRef standard.

    Le RAG peut retourner :
      {
        "ref": "S1",
        "chunk_id": "...",
        "file_name": "...",
        "score": 0.82,
        "vector_score": 0.72,
        "metadata_bonus": 0.10
      }

    Retour :
      list[SourceRef]
    """
    sources: list[SourceRef] = []

    for i, src in enumerate(raw_sources or [], 1):
        if not isinstance(src, dict):
            continue

        sources.append(
            SourceRef(
                ref=str(src.get("ref") or f"S{i}"),
                chunk_id=src.get("chunk_id"),
                file_name=src.get("file_name"),
                domaine_principal=src.get("domaine_principal"),
                score=_safe_float(src.get("score")),
                vector_score=_safe_float(src.get("vector_score")),
                metadata_bonus=_safe_float(src.get("metadata_bonus")),
                source=src.get("source"),
                excerpt=src.get("excerpt"),
            )
        )

    return sources


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return round(float(value), 4)
    except Exception:
        return None


def format_sources_markdown(sources: list[SourceRef]) -> str:
    """
    Format Markdown compact des sources.
    """
    if not sources:
        return "Aucune source disponible."

    lines = ["### Sources utilisées"]

    for src in sources:
        score = f"{src.score:.3f}" if src.score is not None else "n/a"
        file_name = src.file_name or "document inconnu"
        chunk_id = src.chunk_id or "chunk inconnu"

        line = f"- **[{src.ref}]** `{file_name}` — `{chunk_id}` — score={score}"

        if src.domaine_principal:
            line += f" — domaine={src.domaine_principal}"

        lines.append(line)

        if src.excerpt:
            excerpt = src.excerpt.replace("\n", " ").strip()
            if len(excerpt) > 350:
                excerpt = excerpt[:350] + "..."
            lines.append(f"  > {excerpt}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# AGENT / POC NOTES
# ══════════════════════════════════════════════════════════════════════════════

def build_agent_note(
    intent: str,
    recommended_agent: str,
    *,
    force: bool = False,
) -> str:
    """
    Construit une note courte selon l'agent recommandé.

    force=False :
      ajoute une note seulement pour les agents spécialisés.

    force=True :
      ajoute aussi une note pour Orchestrateur.
    """
    agent = get_agent(recommended_agent)

    if not agent:
        return ""

    if recommended_agent == "Orchestrateur" and not force:
        return ""

    status = agent.status.value
    available = agent.available_in_poc

    if recommended_agent == "EnnoDiagnostic":
        return (
            "\n\n---\n"
            "### Orientation agent\n"
            "**Agent recommandé : EnnoDiagnostic**\n\n"
            "Cette réponse est une **analyse préliminaire POC** basée sur les sources RAG. "
            "Pour un score CIR complet, une analyse détaillée des verrous, des preuves, "
            "du niveau de risque et une validation humaine, il faudra passer par **EnnoDiagnostic**.\n\n"
            f"**Statut actuel :** {status}."
        )

    if recommended_agent == "EnnoScholar":
        return (
            "\n\n---\n"
            "### Orientation agent\n"
            "**Agent recommandé : EnnoScholar**\n\n"
            "La demande concerne l'état de l'art ou les articles scientifiques. "
            "Dans l'architecture finale, **EnnoScholar** interrogera Semantic Scholar, ArXiv ou OpenAlex, "
            "classera les articles et générera un état de l'art structuré avec citations.\n\n"
            f"**Statut actuel :** {status}."
        )

    if recommended_agent == "EnnoValor":
        return (
            "\n\n---\n"
            "### Orientation agent\n"
            "**Agent recommandé : EnnoValor**\n\n"
            "La demande concerne les données financières/RH, le mapping Excel/Cerfa "
            "ou les livrables administratifs. Dans l'architecture finale, ces tâches seront traitées "
            "par **EnnoValor** avec traçabilité des valeurs extraites.\n\n"
            f"**Statut actuel :** {status}."
        )

    if recommended_agent == "Orchestrateur":
        return (
            "\n\n---\n"
            "### Agent utilisé\n"
            "**Orchestrateur** a traité cette demande directement avec le RAG documentaire."
        )

    return get_agent_poc_message(recommended_agent)


def build_poc_warning(intent: str, recommended_agent: str) -> str:
    """
    Message court affichable séparément dans l'UI.
    """
    if recommended_agent == "EnnoDiagnostic":
        return (
            "Analyse préliminaire uniquement : le score CIR définitif doit être produit "
            "par EnnoDiagnostic avec validation humaine."
        )

    if recommended_agent == "EnnoScholar":
        return (
            "État de l'art complet non encore généré : il faudra passer par EnnoScholar."
        )

    if recommended_agent == "EnnoValor":
        return (
            "Valorisation administrative/financière non encore exécutée : il faudra passer par EnnoValor."
        )

    return ""


def should_add_agent_note(intent: str, recommended_agent: str) -> bool:
    """
    Décide si on ajoute une note agent à la fin de la réponse.
    """
    if recommended_agent in {"EnnoDiagnostic", "EnnoScholar", "EnnoValor"}:
        return True

    if intent in {"eligibility", "diagnostic", "scholar", "valor"}:
        return True

    return False


# ══════════════════════════════════════════════════════════════════════════════
# MAIN BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

def build_response_from_rag(
    *,
    question: str,
    decision: Any,
    rag_response: Any,
    start_time: Optional[float] = None,
    include_agent_note: bool = True,
    include_debug: bool = False,
    extra_debug: Optional[dict[str, Any]] = None,
) -> BuiltResponse:
    """
    Construit une BuiltResponse à partir de :
      - IntentDecision ;
      - réponse RAG ;
      - question utilisateur.

    decision doit avoir :
      - intent.value
      - recommended_agent.value
      - action
      - confidence
      - explanation
      - is_specialized_agent_required

    rag_response doit avoir :
      - answer
      - sources
      - chunks_used
      - error
    """
    t0 = start_time or time.time()

    intent = _value(decision.intent)
    recommended_agent = _value(decision.recommended_agent)

    answer = str(getattr(rag_response, "answer", "") or "").strip()

    if not answer:
        answer = (
            "Le RAG a récupéré des informations, mais aucune réponse exploitable "
            "n'a été générée par le modèle."
        )

    agent_note = ""
    if include_agent_note and should_add_agent_note(intent, recommended_agent):
        agent_note = build_agent_note(intent, recommended_agent)
        if agent_note and agent_note not in answer:
            answer += agent_note

    sources = normalize_sources(getattr(rag_response, "sources", []) or [])

    debug = {}
    if include_debug:
        debug = {
            "question": question,
            "decision": decision.to_dict() if hasattr(decision, "to_dict") else str(decision),
            "rag_response": (
                rag_response.to_dict()
                if hasattr(rag_response, "to_dict")
                else {}
            ),
        }
        if extra_debug:
            debug.update(extra_debug)

    return BuiltResponse(
        answer=answer,
        intent=intent,
        recommended_agent=recommended_agent,
        action=str(getattr(decision, "action", "")),
        confidence=float(getattr(decision, "confidence", 0.0) or 0.0),
        sources=sources,
        rag_used=True,
        chunks_used=int(getattr(rag_response, "chunks_used", len(sources)) or 0),
        needs_specialized_agent=bool(
            getattr(decision, "is_specialized_agent_required", False)
        ),
        route_explanation=str(getattr(decision, "explanation", "") or ""),
        agent_note=agent_note,
        poc_warning=build_poc_warning(intent, recommended_agent),
        processing_time=time.time() - t0,
        error=getattr(rag_response, "error", None),
        debug=debug,
    )


def build_direct_response(
    *,
    answer: str,
    decision: Any,
    start_time: Optional[float] = None,
    sources: Optional[list[dict[str, Any]]] = None,
    rag_used: bool = False,
    chunks_used: int = 0,
    include_debug: bool = False,
    debug: Optional[dict[str, Any]] = None,
) -> BuiltResponse:
    """
    Construit une réponse directe sans appel RAG/LLM.

    Utilisé pour :
      - help ;
      - document manquant ;
      - statut pipeline ;
      - debug simple.
    """
    t0 = start_time or time.time()

    intent = _value(decision.intent)
    recommended_agent = _value(decision.recommended_agent)

    srcs = normalize_sources(sources or [])

    return BuiltResponse(
        answer=answer,
        intent=intent,
        recommended_agent=recommended_agent,
        action=str(getattr(decision, "action", "")),
        confidence=float(getattr(decision, "confidence", 0.0) or 0.0),
        sources=srcs,
        rag_used=rag_used,
        chunks_used=chunks_used,
        needs_specialized_agent=bool(
            getattr(decision, "is_specialized_agent_required", False)
        ),
        route_explanation=str(getattr(decision, "explanation", "") or ""),
        agent_note="",
        poc_warning=build_poc_warning(intent, recommended_agent),
        processing_time=time.time() - t0,
        error=None,
        debug=debug if include_debug and debug else {},
    )


def build_error_response(
    *,
    error: str,
    decision: Any,
    start_time: Optional[float] = None,
    user_message: str = "Une erreur est survenue.",
    include_debug: bool = False,
) -> BuiltResponse:
    """
    Construit une réponse erreur standard.
    """
    t0 = start_time or time.time()

    intent = _value(getattr(decision, "intent", "unknown"))
    recommended_agent = _value(getattr(decision, "recommended_agent", "Orchestrateur"))

    return BuiltResponse(
        answer=user_message,
        intent=intent,
        recommended_agent=recommended_agent,
        action=str(getattr(decision, "action", "error")),
        confidence=float(getattr(decision, "confidence", 0.0) or 0.0),
        sources=[],
        rag_used=False,
        chunks_used=0,
        needs_specialized_agent=False,
        route_explanation=str(getattr(decision, "explanation", "") or ""),
        agent_note="",
        poc_warning="",
        processing_time=time.time() - t0,
        error=error,
        debug={
            "error": error,
            "decision": decision.to_dict() if hasattr(decision, "to_dict") else str(decision),
        } if include_debug else {},
    )


# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW / STATUS FORMATTERS
# ══════════════════════════════════════════════════════════════════════════════

def build_workflow_status_answer(workflow: Optional[WorkflowReport]) -> str:
    """
    Produit une réponse texte sur l'état du pipeline.
    """
    if workflow is None:
        return (
            "Aucun document n'a encore été préparé.\n\n"
            "Importe un document ou un fichier `.nlp.json`, puis lance la préparation."
        )

    lines = [
        "### État du pipeline Orchestrateur",
        "",
        f"- **Fichier** : {workflow.file_name or 'inconnu'}",
        f"- **Mode** : {workflow.mode.value}",
        f"- **Statut global** : {'OK' if workflow.ok else 'incomplet / erreur'}",
        f"- **Chunks NLP** : {workflow.total_chunks}",
        f"- **Chunks indexés RAG** : {workflow.indexed_chunks}",
        f"- **Temps total** : {workflow.processing_time:.2f}s",
        "",
        _format_step_line("Extraction", workflow.extraction),
        _format_step_line("NLP", workflow.nlp),
        _format_step_line("RAG", workflow.rag),
    ]

    domain = workflow.document_metadata.get("domaine_principal") if workflow.document_metadata else None
    if domain:
        lines.append(f"- **Domaine principal** : {domain}")

    if workflow.output_json_path:
        lines.append(f"- **JSON NLP** : `{workflow.output_json_path}`")

    if workflow.warnings:
        lines.append("")
        lines.append("### Warnings")
        for w in workflow.warnings[:6]:
            lines.append(f"- {w}")

    if workflow.errors:
        lines.append("")
        lines.append("### Erreurs")
        for e in workflow.errors[:6]:
            lines.append(f"- {e}")

    return "\n".join(lines)


def _format_step_line(label: str, step: Any) -> str:
    status = getattr(step, "status", None)

    emoji = {
        StepStatus.OK: "✅",
        StepStatus.WARNING: "⚠️",
        StepStatus.ERROR: "❌",
        StepStatus.SKIPPED: "⏭️",
        StepStatus.RUNNING: "⏳",
        StepStatus.NOT_STARTED: "○",
    }.get(status, "○")

    message = getattr(step, "message", "") or ""
    duration = float(getattr(step, "duration", 0.0) or 0.0)

    return f"- {emoji} **{label}** : {message} ({duration:.2f}s)"


def build_missing_document_answer() -> str:
    return (
        "Je dois d'abord préparer un document avant de répondre.\n\n"
        "Tu peux importer :\n"
        "- un document brut : PDF, DOCX, PPTX, Excel, email, image ;\n"
        "- ou un fichier `.nlp.json` déjà généré par le pipeline NLP.\n\n"
        "Ensuite, Orchestrateur pourra lancer : Extraction → NLP → RAG → réponse sourcée."
    )


def build_missing_rag_answer() -> str:
    return (
        "Le document semble chargé, mais l'index RAG n'est pas prêt.\n\n"
        "Relance la préparation du document ou vérifie que `rag.ingest()` a bien indexé les chunks."
    )


def build_help_answer() -> str:
    return (
        "Je suis **Orchestrateur**, l'orchestrateur POC d'EnnoSmart.\n\n"
        "Je peux :\n"
        "- préparer un document avec Extraction → NLP → RAG ;\n"
        "- donner une idée générale du projet ;\n"
        "- répondre à des questions documentaires avec sources ;\n"
        "- donner une estimation préliminaire d'éligibilité CIR ;\n"
        "- rediriger vers EnnoDiagnostic, EnnoScholar ou EnnoValor selon le besoin.\n\n"
        "Exemples :\n"
        "- `Donne-moi une idée générale du projet`\n"
        "- `Quels sont les verrous techniques ?`\n"
        "- `Est-ce que ce projet semble éligible CIR ?`\n"
        "- `Quels outils, méthodes et résultats sont mentionnés ?`\n"
        "- `Montre les chunks récupérés par le RAG`\n"
    )


# ══════════════════════════════════════════════════════════════════════════════
# STREAMLIT FORMATTERS
# ══════════════════════════════════════════════════════════════════════════════

def response_to_markdown(response: BuiltResponse) -> str:
    """
    Convertit BuiltResponse en Markdown complet.
    """
    parts = [response.answer.strip()]

    if response.poc_warning:
        parts.append(f"\n> **POC :** {response.poc_warning}")

    if response.sources:
        parts.append("\n" + format_sources_markdown(response.sources))

    parts.append(
        "\n---\n"
        f"**Intent :** `{response.intent}`  \n"
        f"**Agent recommandé :** `{response.recommended_agent}`  \n"
        f"**Confiance routage :** `{response.confidence:.2f}`"
    )

    return "\n\n".join(parts).strip()


def response_sidebar_summary(response: BuiltResponse) -> dict[str, Any]:
    """
    Résumé compact pour Streamlit sidebar ou logs.
    """
    return {
        "intent": response.intent,
        "recommended_agent": response.recommended_agent,
        "confidence": round(response.confidence, 3),
        "rag_used": response.rag_used,
        "chunks_used": response.chunks_used,
        "sources": len(response.sources),
        "needs_specialized_agent": response.needs_specialized_agent,
        "processing_time": round(response.processing_time, 2),
        "error": response.error,
    }


def build_agent_panel_markdown(agent_name: str) -> str:
    """
    Fiche agent affichable dans Streamlit.
    """
    return format_agent_card(agent_name)


# ══════════════════════════════════════════════════════════════════════════════
# INTERNAL UTILS
# ══════════════════════════════════════════════════════════════════════════════

def _value(value: Any) -> str:
    """
    Récupère .value si Enum, sinon str.
    """
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


# ══════════════════════════════════════════════════════════════════════════════
# TEST LOCAL RAPIDE
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    from agents.orchestration.intent_router import detect_intent

    decision = detect_intent(
        "Est-ce que ce projet est éligible CIR ?",
        has_document=True,
        has_rag_index=True,
    )

    class FakeRAGResponse:
        answer = "Le projet présente plusieurs signaux favorables : verrous techniques, démarche expérimentale et résultats mesurés. [S1]"
        sources = [
            {
                "ref": "S1",
                "chunk_id": "doc_chunk_0001",
                "file_name": "demo.nlp.json",
                "score": 0.88,
                "vector_score": 0.72,
                "metadata_bonus": 0.16,
            }
        ]
        chunks_used = 1
        error = None

        def to_dict(self):
            return {
                "answer": self.answer,
                "sources": self.sources,
                "chunks_used": self.chunks_used,
                "error": self.error,
            }

    built = build_response_from_rag(
        question="Est-ce que ce projet est éligible CIR ?",
        decision=decision,
        rag_response=FakeRAGResponse(),
        include_debug=True,
    )

    print(response_to_markdown(built))