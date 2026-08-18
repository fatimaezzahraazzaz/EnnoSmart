from __future__ import annotations

from .visible_candidate_policy_v3181 import (
    review_summary,
    visible_candidate_message,
)

from .source_integrity_v318 import (
    hard_conservation_issues,
    integrity_issues_from_protection,
    prepare_writer_request,
    restore_protected_candidate,
)
from .revision_integrity_v318 import (
    render_integrity_retry_instruction,
    revision_block_message,
    verify_revision_integrity,
)

from typing import Any
import hashlib

from ..domain.models import (
    ImprovementIntent,
    ImprovementRequest,
    ImprovementResult,
    ImprovementState,
    RoutingDecision,
    SectionFunction,
    SectionRoutingPlan,
    SpecialistRoute,
    TargetScope,
)
from .agent_adapters import scholar_context
from .diagnostic_orchestration_service import (
    DiagnosticOrchestrationError,
    ensure_diagnostic_context,
)
from .audit_service import audit_text
from .cir_style_context import cir_style_context
from .intention_service import understand_instruction
from .section_parser import parse_sections, replace_target, resolve_target, infer_section_from_instruction
from .semantic_routing_service import SemanticRoutingService, route_for_section
from .research_orchestration_service import (
    RESEARCH_LAUNCH_TARGETED,
    RESEARCH_USE_EXISTING,
    detect_research_choice,
    explicitly_forbids_research,
    explicitly_forbids_scholar,
    format_research_candidates_message,
    launch_targeted_guided_research,
    research_choice_actions,
)
from .fresh_research_policy_v314 import (
    MODE_FRESH as FRESH_RESEARCH_MODE,
    MODE_REUSE as REUSE_RESEARCH_MODE,
    POLICY_VERSION as FRESH_RESEARCH_POLICY_VERSION,
    resolve_fresh_research_policy,
)
from .traceability_service import build_revision_trace
from .writer_service import (
    ControlledWriter,
    _allowed_citation_ids,
    validate_conservative_revision,
)
from .evidence_coverage_v315 import build_coverage_report




def _source_fingerprint(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", errors="ignore")).hexdigest()[:12]


def _restore_editorial_source_from_full_text(
    request: ImprovementRequest,
    routing: RoutingDecision,
) -> tuple[ImprovementRequest, dict[str, Any]]:
    """Restaure la vraie source d'une section si un ancien candidat est renvoyé comme cible.

    Une nouvelle session peut parfois transporter un `target_text` issu d'une
    candidate précédente alors que `full_text` contient encore le document actif.
    En mode éditorial strict, la source active doit toujours être le texte du
    document tant qu'aucune validation humaine n'a eu lieu.
    """

    target = str(request.target_text or "").strip()
    full = str(request.full_text or "")
    meta: dict[str, Any] = {
        "mode": "request_target",
        "target_sha": _source_fingerprint(target),
        "full_sha": _source_fingerprint(full),
        "target_chars": len(target),
        "full_chars": len(full),
    }
    if not (routing.editorial_only or routing.strict_fact_preservation):
        return request, meta
    if request.target_scope != TargetScope.SECTION or not full.strip() or not target:
        return request, meta
    if target in full:
        meta["mode"] = "target_verified_in_full_text"
        return request, meta

    sections = parse_sections(full)
    restored: str | None = None
    restored_section = None

    candidate, section = resolve_target(
        full,
        sections,
        section_id=request.target_section_id,
        section_title=request.target_section_title,
        selected_text=None,
    )
    if section is not None:
        restored, restored_section = candidate, section
    else:
        # Les titres transmis par le frontend peuvent contenir le numéro de
        # section alors que `ParsedSection.title` ne contient que l'intitulé.
        hint = str(request.target_section_title or "").strip()
        inferred = infer_section_from_instruction(hint, sections) if hint else None
        if inferred is not None:
            restored, restored_section = resolve_target(
                full, sections, section_id=inferred.section_id, selected_text=None
            )
        elif len(sections) == 1:
            # Cas où le backend fournit déjà la section seule comme full_text.
            restored = full.strip()
            restored_section = sections[0]

    if not restored or restored.strip() == target:
        meta["mode"] = "target_not_verified_no_safe_restore"
        return request, meta

    restored = restored.strip()
    meta.update(
        {
            "mode": "restored_from_full_text",
            "restored_sha": _source_fingerprint(restored),
            "restored_chars": len(restored),
            "restored_section_id": getattr(restored_section, "section_id", None),
            "restored_section_title": getattr(restored_section, "title", None),
        }
    )
    print(
        "[EnnoAmel][EditorialSource] "
        f"mode=restored_from_full_text target_sha={meta['target_sha']} "
        f"restored_sha={meta['restored_sha']} chars={len(restored)} "
        f"preview={restored[:120].replace(chr(10), ' ')!r}"
    )
    return request.model_copy(
        update={
            "target_text": restored,
            "target_section_id": getattr(restored_section, "section_id", request.target_section_id),
            "target_section_title": getattr(restored_section, "title", request.target_section_title),
        }
    ), meta

class UnsafeRevisionError(RuntimeError):
    def __init__(self, issues: list[str], generation: dict[str, Any]) -> None:
        self.issues = issues
        self.generation = generation
        super().__init__("; ".join(issues))


class EnnoAmeliorationAgent:
    """Orchestrateur de révision CIR contrôlée et traçable."""

    def __init__(
        self,
        writer: ControlledWriter | None = None,
        routing_service: SemanticRoutingService | None = None,
    ) -> None:
        self.writer = writer or ControlledWriter()
        # En production, le même client déjà configuré sert au classement
        # zero-shot. Les writers de tests sans LLM utilisent FastJudge/fallback.
        self.routing_service = routing_service or SemanticRoutingService(
            llm=getattr(self.writer, "llm", None)
        )

    @staticmethod
    def _is_state_of_art_target(request: ImprovementRequest) -> bool:
        """Fallback de compatibilité basé sur l'intention, pas sur un plan figé."""

        decision = understand_instruction(
            "\n".join(
                part
                for part in (
                    request.instruction,
                    request.target_section_title or "",
                    request.target_text[:1600],
                )
                if part
            ),
            request.target_scope,
        )
        return ImprovementIntent.SCIENTIFIC_ENRICHMENT in decision.intents

    @classmethod
    def _is_state_of_art_revision(
        cls,
        request: ImprovementRequest,
        routing: RoutingDecision,
        evidence: dict[str, Any],
    ) -> bool:
        scholar = evidence.get("scholar") if isinstance(evidence, dict) else None
        if not routing.needs_scholar or not isinstance(scholar, dict) or not scholar.get("available"):
            return False
        return bool(
            routing.section_function == SectionFunction.SCIENTIFIC_LANDSCAPE
            or cls._is_state_of_art_target(request)
        )

    @staticmethod
    def _merge_scholar_additions(
        target_text: str,
        sections: list[Any],
        additions: list[dict[str, Any]],
    ) -> str:
        """Fusionne les compléments Scholar par ancres sans remplacer la source."""

        section_map = {section.section_id: section for section in sections}
        insertions: list[tuple[int, str]] = []
        for addition in additions:
            section_id = str(addition.get("section_id") or "")
            section = section_map.get(section_id)
            if section is None:
                raise UnsafeRevisionError(
                    [f"section_scholar_inconnue:{section_id}"], {"agent": "EnnoScholar"}
                )
            section_start = target_text.find(section.content)
            anchor = str(addition.get("anchor") or "")
            anchor_start = section.content.find(anchor)
            if section_start < 0 or anchor_start < 0 or section.content.count(anchor) != 1:
                raise UnsafeRevisionError(
                    [f"ancre_scholar_invalide:{section_id}"], {"agent": "EnnoScholar"}
                )
            insertions.append(
                (
                    section_start + anchor_start + len(anchor),
                    "\n\n" + str(addition.get("content") or "").strip(),
                )
            )
        improved = target_text
        for position, content in sorted(insertions, key=lambda item: item[0], reverse=True):
            improved = improved[:position] + content + improved[position:]
        return improved

    def _rewrite_once_with_conservation(
        self,
        request: ImprovementRequest,
        routing: RoutingDecision,
        audit: Any,
        evidence: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        allow_reduction = ImprovementIntent.CONCISION in routing.intents
        enrichment_requested = any(
            intent in routing.intents
            for intent in (
                ImprovementIntent.ARGUMENTATION,
                ImprovementIntent.SCIENTIFIC_ENRICHMENT,
            )
        )

        # V3.25 — les contrôles sont un audit destiné au consultant, pas un
        # pare-feu de génération. Une seule rédaction est produite : aucune
        # alerte d'intégrité ne déclenche une réécriture automatique susceptible
        # d'effacer une bonne première candidate ou ses sources scientifiques.
        max_attempts = 1
        attempts: list[dict[str, Any]] = []
        attempt_request = request
        last_issues: list[str] = []
        last_integrity: dict[str, Any] = {}
        improved = request.target_text
        meta: dict[str, Any] = {}

        for attempt in range(1, max_attempts + 1):
            protected_request, protected_fragments = prepare_writer_request(
                attempt_request
            )
            improved_masked, meta = self.writer.rewrite(
                protected_request,
                routing,
                audit,
                evidence,
            )
            improved, protection_report = restore_protected_candidate(
                improved_masked,
                protected_fragments,
            )

            last_issues = validate_conservative_revision(
                request.target_text,
                improved,
                allowed_citation_ids=_allowed_citation_ids(evidence),
                allow_reduction=allow_reduction,
                enrichment_requested=enrichment_requested,
            )

            protection_issues = integrity_issues_from_protection(
                protection_report
            )
            for issue in protection_issues:
                if issue not in last_issues:
                    last_issues.append(issue)

            coverage = build_coverage_report(evidence, improved)
            missing_required = list(
                coverage.get("missing_required_ids") or []
            )
            if missing_required:
                last_issues.append(
                    "preuves_validees_non_utilisees:"
                    + ",".join(missing_required)
                )

            last_integrity = {}
            hard_before_semantic = hard_conservation_issues(last_issues)

            # Le contrôle sémantique coûte un appel LLM. On ne le lance que si
            # les contrôles déterministes sont déjà propres et si le consultant
            # a demandé un enrichissement/renforcement scientifique.
            if (
                enrichment_requested
                and not missing_required
                and not hard_before_semantic
            ):
                try:
                    last_integrity = verify_revision_integrity(
                        request.target_text,
                        improved,
                        evidence,
                    )
                    for issue in last_integrity.get("issues") or []:
                        if issue not in last_issues:
                            last_issues.append(str(issue))
                except Exception as exc:
                    last_integrity = {
                        "complete": False,
                        "error": str(exc),
                        "issues": [
                            "integrity_verifier_unavailable:"
                            + exc.__class__.__name__
                        ],
                    }
                    last_issues.extend(last_integrity["issues"])

            attempts.append(
                {
                    "attempt": attempt,
                    "issues": list(last_issues),
                    "llm": dict(meta or {}),
                    "protected_source_integrity": protection_report,
                    "accepted_source_coverage": coverage,
                    "semantic_integrity": last_integrity,
                }
            )

            # Mode purement éditorial : une candidate reste visible tant qu'elle
            # ne perd aucun élément protégé. Les alertes lexicales non bloquantes
            # restent consultables par le consultant.
            if routing.editorial_only or routing.strict_fact_preservation:
                if not hard_conservation_issues(last_issues):
                    return improved, {
                        **dict(meta),
                        "strategy": "visible_editorial_candidate_v318",
                        "conservation_validation": (
                            "passed" if not last_issues else "consultant_review"
                        ),
                        "conservation_issues": list(last_issues),
                        "requires_consultant_review": bool(last_issues),
                        "attempt_count": attempt,
                        "call_count": attempt,
                        "attempts": attempts,
                        "protected_source_integrity": protection_report,
                    }

            if not last_issues:
                return improved, {
                    **dict(meta),
                    "strategy": (
                        "scientific_integrity_verified_v318"
                        if enrichment_requested
                        else "automatic_conservative_repair_v318"
                    ),
                    "conservation_validation": "passed",
                    "scientific_integrity_validation": (
                        "passed" if enrichment_requested else "not_required"
                    ),
                    "accepted_source_coverage": coverage,
                    "semantic_integrity": last_integrity,
                    "protected_source_integrity": protection_report,
                    "attempt_count": attempt,
                    "call_count": attempt,
                    "attempts": attempts,
                }

            retry_instruction = (
                request.instruction
                + "\n\nCORRECTION AUTOMATIQUE OBLIGATOIRE V3.18\n"
                + "Repars du texte source actif. Corrige toutes les violations "
                  "sans supprimer de fait, référence, figure, mesure ou citation "
                  "déjà correctement intégrée.\n"
                + "Violations détectées : "
                + "; ".join(last_issues)
            )

            if last_integrity:
                retry_instruction += render_integrity_retry_instruction(
                    last_integrity
                )

            if missing_required:
                retry_instruction += (
                    "\nToutes les preuves acceptées restent obligatoires. "
                    "Intègre chaque citation manquante uniquement derrière une "
                    "affirmation réellement soutenue par sa preuve. Si nécessaire, "
                    "utilise un fait minimal mais exact."
                )

            attempt_request = request.model_copy(
                update={"instruction": retry_instruction}
            )

        final_coverage = build_coverage_report(evidence, improved)
        final_missing = list(
            final_coverage.get("missing_required_ids") or []
        )

        scientific_failures = [
            issue
            for issue in last_issues
            if str(issue).startswith(
                (
                    "citation_non_etayee:",
                    "integrity_verifier_unavailable:",
                )
            )
        ]

        # V3.25 — politique « audit consultatif uniquement ». Les anomalies
        # détectées restent détaillées dans le comparatif, mais ne deviennent
        # jamais des statuts bloquants et ne provoquent aucun retry automatique.
        review_issues = list(
            dict.fromkeys(
                [*last_issues]
            )
        )

        return improved, {
            **dict(meta),
            "strategy": "visible_candidate_with_advisory_warnings_v325",
            "conservation_validation": (
                "passed" if not review_issues else "consultant_review_required"
            ),
            "scientific_integrity_validation": (
                "passed"
                if not scientific_failures
                else "consultant_review_required"
            ),
            "accepted_source_coverage": final_coverage,
            "semantic_integrity": last_integrity,
            "quality_warnings": review_issues,
            "conservation_issues": review_issues,
            "requires_consultant_review": bool(review_issues),
            "candidate_visibility_policy": "always_visible",
            "quality_control_mode": "advisory_only",
            "automatic_integrity_retry": False,
            "controls_blocked_candidate": False,
            "accepted_source_coverage_warning": final_missing,
            "active_version_mutated": False,
            "attempt_count": len(attempts),
            "call_count": len(attempts),
            "attempts": attempts,
        }




    @staticmethod
    def _collect_conservation_issues(generation: Any) -> list[str]:
        """Collecte récursivement les alertes de conservation d'une génération."""

        found: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, dict):
                for issue in value.get("conservation_issues") or []:
                    text = str(issue or "").strip()
                    if text and text not in found:
                        found.append(text)
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(generation)
        return found

    @staticmethod
    def _section_batches(request: ImprovementRequest, max_chars: int = 22000) -> list[str]:
        batches: list[str] = []
        current = ""
        for section in parse_sections(request.target_text):
            content = section.content
            if current and len(current) + len(content) > max_chars:
                batches.append(current)
                current = ""
            if len(content) > max_chars and not current:
                batches.append(content)
            else:
                current += content
        if current:
            batches.append(current)
        return batches or [request.target_text]

    def _rewrite_target(
        self,
        request: ImprovementRequest,
        routing: RoutingDecision,
        audit: Any,
        evidence: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]]:
        """Compatibilité pour une cible uniforme ou un très long passage."""

        if len(request.target_text) <= 24000:
            improved, generation = self._rewrite_once_with_conservation(
                request, routing, audit, evidence
            )
            return (
                improved,
                replace_target(request.full_text, request.target_text, improved),
                generation,
            )

        improved_target = request.target_text
        calls: list[dict[str, Any]] = []
        batches = self._section_batches(request)
        for index, batch in enumerate(batches, start=1):
            batch_request = request.model_copy(
                update={
                    "full_text": batch,
                    "target_text": batch,
                    "target_scope": TargetScope.MULTI_SECTION,
                    "target_section_id": None,
                    "target_section_title": None,
                    "sections": parse_sections(batch),
                }
            )
            improved_batch, meta = self._rewrite_once_with_conservation(
                batch_request, routing, audit, evidence
            )
            improved_target = replace_target(improved_target, batch, improved_batch)
            calls.append(meta)
        return (
            improved_target,
            replace_target(request.full_text, request.target_text, improved_target),
            {
                "strategy": "complete_document_section_batches",
                "call_count": sum(int(row.get("call_count") or 1) for row in calls),
                "calls": calls,
                "total_tokens": sum(int(row.get("total_tokens") or 0) for row in calls),
                "prompt_tokens": sum(int(row.get("prompt_tokens") or 0) for row in calls),
                "completion_tokens": sum(
                    int(row.get("completion_tokens") or 0) for row in calls
                ),
            },
        )

    @staticmethod
    def _document_groups(
        request: ImprovementRequest,
        plans: list[SectionRoutingPlan],
        evidence: dict[str, Any],
        max_chars: int = 22000,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        section_by_id = {section.section_id: section for section in request.sections}
        groups: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        scholar_available = bool((evidence.get("scholar") or {}).get("available"))
        for plan in plans:
            section = section_by_id.get(plan.section_id)
            if section is None:
                continue
            if plan.needs_scholar and not scholar_available:
                skipped.append(
                    {
                        "section_id": plan.section_id,
                        "title": plan.title,
                        "route": plan.route.value,
                        "reason": "scientific_enrichment_unavailable_editorial_revision_only",
                    }
                )
            can_extend = bool(
                current
                and current["route"] == plan.route
                and current["function"] == plan.function
                and current["end"] == section.start
                and section.end - current["start"] <= max_chars
            )
            if can_extend:
                current["plans"].append(plan)
                current["sections"].append(section)
                current["end"] = section.end
            else:
                current = {
                    "route": plan.route,
                    "function": plan.function,
                    "plans": [plan],
                    "sections": [section],
                    "start": section.start,
                    "end": section.end,
                }
                groups.append(current)
        return groups, skipped

    @staticmethod
    def _evidence_for_section_ids(
        evidence: dict[str, Any],
        section_ids: list[str],
    ) -> dict[str, Any]:
        """Isole les preuves Scholar reliees au groupe de sections courant."""

        scholar = evidence.get("scholar")
        if not isinstance(scholar, dict):
            return evidence
        evidence_items = [
            row for row in (scholar.get("evidence_items") or []) if isinstance(row, dict)
        ]
        evidence_rows = [
            row for row in (scholar.get("evidence") or []) if isinstance(row, dict)
        ]
        has_section_mapping = any(row.get("section_ids") for row in evidence_items)
        if not has_section_mapping:
            return evidence
        wanted = {str(value) for value in section_ids if str(value or "").strip()}

        def belongs(row: dict[str, Any]) -> bool:
            return bool(wanted.intersection(str(value) for value in (row.get("section_ids") or [])))

        scoped_items = [row for row in evidence_items if belongs(row)]
        scoped_rows = [row for row in evidence_rows if belongs(row)]
        scoped_scholar = {
            **scholar,
            "available": bool(scoped_items),
            "evidence_items": scoped_items,
            "evidence": scoped_rows,
            "writing_ready_card_count": len(scoped_items),
            "evidence_scope_section_ids": sorted(wanted),
        }
        return {**evidence, "scholar": scoped_scholar}

    def _rewrite_document_by_plan(
        self,
        request: ImprovementRequest,
        routing: RoutingDecision,
        evidence: dict[str, Any],
    ) -> tuple[str, str, dict[str, Any]]:
        groups, skipped = self._document_groups(request, routing.section_plan, evidence)
        improved_target = request.target_text
        calls: list[dict[str, Any]] = []
        document_patches: list[dict[str, Any]] = []
        for group in groups:
            batch = request.target_text[group["start"] : group["end"]]
            first_plan = group["plans"][0]
            group_routing = route_for_section(first_plan, routing)
            group_request = request.model_copy(
                update={
                    "full_text": batch,
                    "target_text": batch,
                    "target_scope": (
                        TargetScope.SECTION
                        if len(group["sections"]) == 1
                        else TargetScope.MULTI_SECTION
                    ),
                    "target_section_id": (
                        group["sections"][0].section_id
                        if len(group["sections"]) == 1
                        else None
                    ),
                    "target_section_title": (
                        group["sections"][0].title
                        if len(group["sections"]) == 1
                        else None
                    ),
                    "sections": group["sections"],
                }
            )
            group_audit = audit_text(batch, group_routing)
            group_section_ids = [plan.section_id for plan in group["plans"]]
            group_evidence = self._evidence_for_section_ids(
                evidence,
                group_section_ids,
            )
            improved_batch, meta = self._rewrite_once_with_conservation(
                group_request, group_routing, group_audit, group_evidence
            )
            improved_target = replace_target(improved_target, batch, improved_batch)
            document_patches.append(
                {
                    "patch_id": f"patch-{len(document_patches) + 1:03d}",
                    "section_ids": group_section_ids,
                    "source_start": group["start"],
                    "source_end": group["end"],
                    "source_sha256": hashlib.sha256(
                        batch.encode("utf-8", errors="ignore")
                    ).hexdigest(),
                    "replacement_sha256": hashlib.sha256(
                        improved_batch.encode("utf-8", errors="ignore")
                    ).hexdigest(),
                    "before_chars": len(batch),
                    "after_chars": len(improved_batch),
                    "scope": "editable_text_blocks_only",
                    "source_document_assets_untouched": True,
                }
            )
            calls.append(
                {
                    **meta,
                    "route": first_plan.route.value,
                    "section_ids": group_section_ids,
                    "section_functions": [plan.function.value for plan in group["plans"]],
                }
            )
        return (
            improved_target,
            replace_target(request.full_text, request.target_text, improved_target),
            {
                "strategy": "semantic_section_routing",
                "call_count": sum(int(row.get("call_count") or 1) for row in calls),
                "calls": calls,
                "skipped_sections": skipped,
                "section_plan": [item.model_dump(mode="json") for item in routing.section_plan],
                "document_patch_contract": {
                    "layout_master": "original_document",
                    "apply_mode": "text_patches_only",
                    "figures_tables_headers_footers_untouched": True,
                },
                "document_patches": document_patches,
                "total_tokens": sum(int(row.get("total_tokens") or 0) for row in calls),
                "prompt_tokens": sum(int(row.get("prompt_tokens") or 0) for row in calls),
                "completion_tokens": sum(
                    int(row.get("completion_tokens") or 0) for row in calls
                ),
            },
        )

    @staticmethod
    def _evidence_package(
        db: Any,
        project: Any,
        request: ImprovementRequest,
        routing: RoutingDecision,
    ) -> dict[str, Any]:
        package: dict[str, Any] = {
            "package_version": "ennoamelioration_evidence_v2",
            "project_context": {
                "project_id": getattr(project, "id", None),
                "organisme": getattr(project, "organisme", None),
                "project_name": getattr(project, "project_name", None),
                "year": getattr(project, "year", None),
                "domain": getattr(project, "domain_label", None),
            },
            "constraints": {
                "no_hallucination": True,
                "preserve_verified_facts": True,
                "citations_required_when_scientific": True,
                "memory_is_never_factual_evidence": True,
                "no_official_cir_eligibility_claim": True,
            },
            "cir_style": cir_style_context(project),
        }
        cached_diagnostic = request.diagnostic_context_override
        if isinstance(cached_diagnostic, dict):
            package["diagnostic"] = dict(cached_diagnostic)
            package["diagnostic_orchestration"] = {
                **dict(request.diagnostic_orchestration_override or {}),
                "mode": "reuse_initial_cir_diagnostic",
                "executed": False,
                "cache_hit": True,
            }
        needs_diagnostic_context = bool(
            routing.needs_diagnostic
            or routing.needs_project_evidence
        )
        if needs_diagnostic_context and "diagnostic" not in package:
            if not request.allow_scoped_diagnostic:
                package["diagnostic"] = {
                    "available": False,
                    "completed": False,
                    "agent": "EnnoDiagnostic",
                    "reason": (
                        "Le diagnostic initial du CIR n'est pas disponible et "
                        "la section n'est pas ambiguë : aucun diagnostic ciblé "
                        "supplémentaire n'est autorisé."
                    ),
                    "evidence_items": [],
                    "verrous": [],
                    "domain_detection": {},
                }
                package["diagnostic_orchestration"] = {
                    "agent": "EnnoDiagnostic",
                    "mode": "scoped_not_authorized_for_unambiguous_section",
                    "executed": False,
                }
            else:
                try:
                    diagnostic, orchestration = ensure_diagnostic_context(
                        db, project, request
                    )
                    package["diagnostic"] = diagnostic
                    package["diagnostic_orchestration"] = orchestration
                except DiagnosticOrchestrationError as exc:
                    package["diagnostic"] = {
                        "available": False,
                        "completed": False,
                        "agent": "EnnoDiagnostic",
                        "reason": str(exc),
                        "evidence_items": [],
                        "verrous": [],
                        "domain_detection": {},
                    }
                    package["diagnostic_orchestration"] = {
                        "agent": "EnnoDiagnostic",
                        "mode": "failed",
                        "executed": False,
                        "error": str(exc),
                    }
        if routing.needs_scholar:
            package["scholar"] = scholar_context(
                db,
                project,
                request.target_text,
                request.instruction,
                allowed_article_ids=request.evidence_article_ids,
                evidence_scope_id=request.evidence_scope_id,
            )
        gaps: list[dict[str, str]] = []
        for key in ("diagnostic", "scholar"):
            value = package.get(key)
            if isinstance(value, dict) and not value.get("available"):
                gaps.append({"source": key, "reason": str(value.get("reason") or "indisponible")})
        package["gaps"] = gaps
        return package

    def improve(self, db: Any, project: Any, request: ImprovementRequest) -> ImprovementResult:
        target_sections = (
            parse_sections(request.target_text)
            if request.target_scope in {TargetScope.MULTI_SECTION, TargetScope.FULL_DOCUMENT}
            else []
        )
        if target_sections:
            request = request.model_copy(update={"sections": target_sections})
        routing = self.routing_service.route(
            request.instruction,
            request.target_scope,
            request.target_text,
            section_id=request.target_section_id,
            section_title=request.target_section_title,
            sections=target_sections,
        )
        request, source_resolution = _restore_editorial_source_from_full_text(
            request, routing
        )
        if source_resolution.get("mode") == "restored_from_full_text":
            # Le routage sémantique doit lui aussi être recalculé sur la source
            # restaurée et non sur une candidate résiduelle.
            routing = self.routing_service.route(
                request.instruction,
                request.target_scope,
                request.target_text,
                section_id=request.target_section_id,
                section_title=request.target_section_title,
                sections=[],
            )
        else:
            print(
                "[EnnoAmel][EditorialSource] "
                f"mode={source_resolution.get('mode')} "
                f"target_sha={source_resolution.get('target_sha')} "
                f"full_sha={source_resolution.get('full_sha')} "
                f"chars={source_resolution.get('target_chars')} "
                f"preview={str(request.target_text or '')[:120].replace(chr(10), ' ')!r}"
            )
        # Fallback sémantique pour les anciens appels qui ciblent explicitement
        # une revue scientifique mais ne disposent pas du classifieur zero-shot.
        # La détection passe par le classifieur d'intention générique et ne
        # dépend d'aucun intitulé de plan imposé.
        purely_editorial = bool(
            set(routing.intents)
            & {
                ImprovementIntent.CLARITY,
                ImprovementIntent.STYLE,
                ImprovementIntent.STRUCTURE,
                ImprovementIntent.CONCISION,
            }
        ) and not bool(
            set(routing.intents)
            & {
                ImprovementIntent.ARGUMENTATION,
                ImprovementIntent.SCIENTIFIC_ENRICHMENT,
                ImprovementIntent.RESEARCH,
                ImprovementIntent.CIR_ELIGIBILITY,
            }
        )
        if (
            not target_sections
            and not purely_editorial
            and not routing.candidate_revision
            and not routing.forbids_scholar
            and self._is_state_of_art_target(request)
            and not routing.needs_scholar
        ):
            routing = routing.model_copy(
                update={
                    "needs_scholar": True,
                    "needs_project_evidence": routing.needs_diagnostic,
                    "specialist_route": (
                        SpecialistRoute.DIAGNOSTIC_SCHOLAR
                        if routing.needs_diagnostic
                        else SpecialistRoute.SCHOLAR
                    ),
                    "section_function": SectionFunction.SCIENTIFIC_LANDSCAPE,
                    "rationale": [
                        *routing.rationale,
                        "La fonction scientifique de la cible requiert des sources traçables.",
                    ],
                }
            )
        hard_forbid_research = explicitly_forbids_research(request.instruction)
        hard_forbid_scholar = explicitly_forbids_scholar(request.instruction)

        # V2.8 : l'instruction courante est souveraine. Un ancien
        # research_choice conservé par la session ou le frontend ne peut jamais
        # réactiver Scholar si le consultant vient d'interdire la recherche.
        research_choice = (
            None
            if (hard_forbid_research or hard_forbid_scholar)
            else detect_research_choice(request.research_choice or request.instruction)
        )

        # BEGIN ENNOAMEL_FRESH_RESEARCH_V3_14
        fresh_policy = resolve_fresh_research_policy(
            instruction=request.instruction,
            current_choice=research_choice,
            intents=routing.intents,
            needs_scholar=bool(routing.needs_scholar),
            editorial_only=bool(routing.editorial_only),
            hard_forbid_research=hard_forbid_research,
            hard_forbid_scholar=hard_forbid_scholar,
        )

        if fresh_policy.mode == FRESH_RESEARCH_MODE:
            fresh_needs_diagnostic = bool(
                ImprovementIntent.CIR_ELIGIBILITY in routing.intents
                and routing.needs_diagnostic
            )
            fresh_route = (
                SpecialistRoute.DIAGNOSTIC_SCHOLAR
                if fresh_needs_diagnostic
                else SpecialistRoute.SCHOLAR
            )
            fresh_section_plan = [
                plan.model_copy(
                    update={
                        "route": fresh_route,
                        "needs_diagnostic": fresh_needs_diagnostic,
                        "needs_scholar": True,
                        "rationale": [
                            *plan.rationale,
                            f"{FRESH_RESEARCH_POLICY_VERSION}: renforcement du fond -> recherche ciblée",
                        ],
                    }
                )
                for plan in routing.section_plan
            ]
            research_choice = RESEARCH_LAUNCH_TARGETED
            routing = routing.model_copy(
                update={
                    "needs_diagnostic": fresh_needs_diagnostic,
                    "needs_project_evidence": fresh_needs_diagnostic,
                    "needs_scholar": True,
                    "needs_new_research": True,
                    "forbids_new_research": False,
                    "forbids_scholar": False,
                    "specialist_route": fresh_route,
                    "section_plan": fresh_section_plan,
                    "rationale": [
                        *routing.rationale,
                        f"{FRESH_RESEARCH_POLICY_VERSION}: {fresh_policy.reason}",
                    ],
                }
            )

        elif fresh_policy.mode == REUSE_RESEARCH_MODE:
            reuse_section_plan = [
                plan.model_copy(
                    update={
                        "route": SpecialistRoute.SCHOLAR,
                        "needs_diagnostic": False,
                        "needs_scholar": True,
                        "rationale": [
                            *plan.rationale,
                            f"{FRESH_RESEARCH_POLICY_VERSION}: réutilisation explicite",
                        ],
                    }
                )
                for plan in routing.section_plan
            ]
            research_choice = RESEARCH_USE_EXISTING
            routing = routing.model_copy(
                update={
                    "needs_diagnostic": False,
                    "needs_project_evidence": False,
                    "needs_scholar": True,
                    "needs_new_research": False,
                    "forbids_new_research": True,
                    "forbids_scholar": False,
                    "specialist_route": SpecialistRoute.SCHOLAR,
                    "section_plan": reuse_section_plan,
                    "rationale": [
                        *routing.rationale,
                        f"{FRESH_RESEARCH_POLICY_VERSION}: {fresh_policy.reason}",
                    ],
                }
            )
        # END ENNOAMEL_FRESH_RESEARCH_V3_14

        # BEGIN ENNOAMEL_FRESH_RESEARCH_V3_13
        fresh_policy = resolve_fresh_research_policy(
            instruction=request.instruction,
            current_choice=research_choice,
            intents=routing.intents,
            needs_scholar=bool(routing.needs_scholar),
            editorial_only=bool(routing.editorial_only),
            hard_forbid_research=hard_forbid_research,
            hard_forbid_scholar=hard_forbid_scholar,
        )
        if fresh_policy.mode == FRESH_RESEARCH_MODE:
            research_choice = RESEARCH_LAUNCH_TARGETED
            routing = routing.model_copy(
                update={
                    "needs_scholar": True,
                    "needs_new_research": True,
                    "forbids_new_research": False,
                    "forbids_scholar": False,
                    "needs_project_evidence": routing.needs_diagnostic,
                    "specialist_route": (
                        SpecialistRoute.DIAGNOSTIC_SCHOLAR
                        if routing.needs_diagnostic
                        else SpecialistRoute.SCHOLAR
                    ),
                    "rationale": [
                        *routing.rationale,
                        f"{FRESH_RESEARCH_POLICY_VERSION}: {fresh_policy.reason}",
                    ],
                }
            )
        elif fresh_policy.mode == REUSE_RESEARCH_MODE:
            research_choice = RESEARCH_USE_EXISTING
            routing = routing.model_copy(
                update={
                    "needs_scholar": True,
                    "needs_new_research": False,
                    "forbids_new_research": True,
                    "forbids_scholar": False,
                    "needs_project_evidence": routing.needs_diagnostic,
                    "specialist_route": (
                        SpecialistRoute.DIAGNOSTIC_SCHOLAR
                        if routing.needs_diagnostic
                        else SpecialistRoute.SCHOLAR
                    ),
                    "rationale": [
                        *routing.rationale,
                        f"{FRESH_RESEARCH_POLICY_VERSION}: {fresh_policy.reason}",
                    ],
                }
            )
        # END ENNOAMEL_FRESH_RESEARCH_V3_13

        if hard_forbid_scholar:
            routing = routing.model_copy(
                update={
                    "needs_scholar": False,
                    "needs_new_research": False,
                    "forbids_new_research": True,
                    "forbids_scholar": True,
                    "specialist_route": (
                        SpecialistRoute.DIAGNOSTIC
                        if routing.needs_diagnostic
                        else SpecialistRoute.WRITER
                    ),
                    "rationale": [
                        *routing.rationale,
                        "Garde-fou V2.8 : l'interdiction explicite de recherche/EnnoScholar annule tout choix de recherche résiduel.",
                    ],
                }
            )
        elif hard_forbid_research:
            # Interdire une nouvelle recherche n'interdit pas l'utilisation de
            # publications deja choisies par le consultant.
            routing = routing.model_copy(
                update={
                    "needs_new_research": False,
                    "forbids_new_research": True,
                    "specialist_route": (
                        SpecialistRoute.DIAGNOSTIC_SCHOLAR
                        if routing.needs_diagnostic and routing.needs_scholar
                        else SpecialistRoute.DIAGNOSTIC
                        if routing.needs_diagnostic
                        else SpecialistRoute.SCHOLAR
                        if routing.needs_scholar
                        else SpecialistRoute.WRITER
                    ),
                    "rationale": [
                        *routing.rationale,
                        "Aucune nouvelle recherche ne sera lancee ; les preuves deja validees restent utilisables.",
                    ],
                }
            )
        if research_choice == RESEARCH_USE_EXISTING:
            routing = routing.model_copy(
                update={
                    "needs_scholar": True,
                    "needs_new_research": False,
                    "forbids_new_research": True,
                    "forbids_scholar": False,
                    "needs_project_evidence": routing.needs_diagnostic,
                    "specialist_route": (
                        SpecialistRoute.DIAGNOSTIC_SCHOLAR
                        if routing.needs_diagnostic
                        else SpecialistRoute.SCHOLAR
                    ),
                    "rationale": [
                        *routing.rationale,
                        "Le consultant a choisi d'utiliser uniquement les sources scientifiques déjà validées.",
                    ],
                }
            )
        elif research_choice == RESEARCH_LAUNCH_TARGETED:
            routing = routing.model_copy(
                update={
                    "needs_scholar": True,
                    "needs_new_research": True,
                    "forbids_new_research": False,
                    "forbids_scholar": False,
                    "needs_project_evidence": routing.needs_diagnostic,
                    "specialist_route": (
                        SpecialistRoute.DIAGNOSTIC_SCHOLAR
                        if routing.needs_diagnostic
                        else SpecialistRoute.SCHOLAR
                    ),
                    "rationale": [
                        *routing.rationale,
                        "Le consultant a explicitement autorisé une recherche scientifique ciblée.",
                    ],
                }
            )

        resume_with_validated_sources = bool(
            request.evidence_article_ids
            and not hard_forbid_scholar
            and not routing.needs_new_research
        )
        if resume_with_validated_sources:
            # La recherche et la validation sont deja terminees. Le texte cible
            # porte les faits projet et les fiches Scholar portent les apports
            # scientifiques : aucune relance Diagnostic ou Scholar n'est utile.
            section_plan = [
                plan.model_copy(
                    update={
                        "route": SpecialistRoute.SCHOLAR,
                        "needs_diagnostic": False,
                        "needs_scholar": True,
                        "rationale": [
                            *plan.rationale,
                            "Redaction depuis les preuves scientifiques deja validees dans cette conversation.",
                        ],
                    }
                )
                for plan in routing.section_plan
            ]
            intents = list(routing.intents)
            if ImprovementIntent.SCIENTIFIC_ENRICHMENT not in intents:
                intents.append(ImprovementIntent.SCIENTIFIC_ENRICHMENT)
            routing = routing.model_copy(
                update={
                    "intents": intents,
                    "needs_diagnostic": False,
                    "needs_project_evidence": False,
                    "needs_scholar": True,
                    "needs_new_research": False,
                    "forbids_new_research": True,
                    "forbids_scholar": False,
                    "specialist_route": SpecialistRoute.SCHOLAR,
                    "section_plan": section_plan,
                    "editorial_only": False,
                    "strict_fact_preservation": False,
                    "rationale": [
                        *routing.rationale,
                        "Les sources sont validees : EnnoAmelioration redige avec ce corpus ferme, sans nouvelle recherche ni EnnoDiagnostic.",
                    ],
                }
            )

        if routing.editorial_only:
            # Défense en profondeur : même une valeur résiduelle provenant d'un
            # ancien choix de recherche ou d'un rôle sémantique ne peut lancer
            # EnnoDiagnostic/EnnoScholar sur une réécriture explicitement
            # limitée au style/à la structure.
            routing = routing.model_copy(
                update={
                    "needs_diagnostic": False,
                    "needs_scholar": False,
                    "needs_new_research": False,
                    "forbids_new_research": True,
                    "forbids_scholar": True,
                    "needs_project_evidence": False,
                    "specialist_route": SpecialistRoute.WRITER,
                    "rationale": [
                        *routing.rationale,
                        "Garde-fou V2.7 : aucun spécialiste n'est autorisé en mode éditorial strict.",
                    ],
                }
            )

        if ImprovementIntent.SMALL_TALK in routing.intents:
            return ImprovementResult(
                ok=True,
                state=ImprovementState.TARGET_IDENTIFICATION,
                assistant_message=(
                    "Bonjour ! Sélectionnez un passage, une section ou le document, "
                    "puis indiquez l'amélioration souhaitée."
                ),
                routing=routing,
            )

        audit = audit_text(request.target_text, routing)
        if any(item.severity == "blocking" for item in audit):
            return ImprovementResult(
                ok=False,
                state=ImprovementState.TARGET_IDENTIFICATION,
                assistant_message=audit[0].recommendation,
                routing=routing,
                audit=audit,
            )

        evidence = self._evidence_package(db, project, request, routing)
        existing_sources_available = bool((evidence.get("scholar") or {}).get("available"))

        diagnostic_required = bool(
            routing.needs_diagnostic
            or routing.needs_project_evidence
        )
        diagnostic_payload = evidence.get("diagnostic") or {}
        diagnostic_available = bool(
            diagnostic_payload.get("available")
            or diagnostic_payload.get("completed")
        )
        if diagnostic_required and not diagnostic_available:
            reason = str((evidence.get("diagnostic") or {}).get("reason") or "Contexte EnnoDiagnostic indisponible.")
            if routing.needs_scholar and routing.needs_new_research:
                # Un échec du diagnostic ne transforme jamais la section en faux
                # verrou, mais il ne doit pas non plus empêcher une recherche
                # bibliographique explicitement demandée. EnnoScholar repart du
                # texte réel via un research_target typé.
                evidence["diagnostic_fallback_to_direct_research"] = {
                    "allowed": True,
                    "reason": reason,
                    "policy": "no_fake_lock_direct_scholar_from_source_text",
                }
                diagnostic_required = False
            else:
                return ImprovementResult(
                    ok=False,
                    state=ImprovementState.REVIEW,
                    assistant_message=(
                        "EnnoAmel a besoin d'EnnoDiagnostic pour analyser la section/CIR courant avant cette amélioration, "
                        "mais l'analyse n'a pas pu être obtenue. Aucune réécriture scientifique "
                        "n'est lancée afin d'éviter un contexte inventé. " + reason
                    ),
                    routing=routing,
                    audit=audit,
                    evidence=evidence,
                    agents_used=["EnnoAmelioration", "EnnoDiagnostic"],
                    requires_confirmation=True,
                )

        # Invariant de sécurité V2.8 : aucune branche de recherche ne peut
        # s'exécuter si l'interdiction est active, même en cas d'état de session
        # incohérent.
        if routing.forbids_scholar:
            research_choice = None
            if routing.needs_new_research or routing.needs_scholar:
                routing = routing.model_copy(
                    update={
                        "needs_new_research": False,
                        "needs_scholar": False,
                        "specialist_route": (
                            SpecialistRoute.DIAGNOSTIC
                            if routing.needs_diagnostic
                            else SpecialistRoute.WRITER
                        ),
                    }
                )
        elif routing.forbids_new_research and routing.needs_new_research:
            research_choice = None
            routing = routing.model_copy(update={"needs_new_research": False})

        if routing.needs_new_research and research_choice == RESEARCH_LAUNCH_TARGETED:
            try:
                research_request = request.model_copy(
                    update={
                        "research_target_type": routing.section_function.value,
                        "research_section_plan": routing.section_plan,
                    }
                )
                research = launch_targeted_guided_research(
                    db,
                    project,
                    research_request,
                    diagnostic_package=(evidence.get("diagnostic") or {}),
                    diagnostic_orchestration=(evidence.get("diagnostic_orchestration") or {}),
                )
            except Exception as exc:
                return ImprovementResult(
                    ok=False,
                    state=ImprovementState.REVIEW,
                    assistant_message=(
                        "La recherche ciblée n'a pas pu être lancée par EnnoScholar. "
                        f"Détail : {exc}"
                    ),
                    routing=routing,
                    audit=audit,
                    evidence=evidence,
                    agents_used=(
                        ["EnnoAmelioration", "EnnoDiagnostic", "EnnoScholar"]
                        if diagnostic_required
                        else ["EnnoAmelioration", "EnnoScholar"]
                    ),
                    actions=research_choice_actions(
                        existing_sources_available=existing_sources_available
                    ),
                    requires_confirmation=True,
                )
            return ImprovementResult(
                ok=True,
                state=ImprovementState.AWAITING_EVIDENCE,
                assistant_message=format_research_candidates_message(research),
                routing=routing,
                audit=audit,
                evidence=evidence,
                research=research,
                agents_used=(
                    ["EnnoAmelioration", "EnnoDiagnostic", "EnnoScholar"]
                    if diagnostic_required
                    else ["EnnoAmelioration", "EnnoScholar"]
                ),
                questions_for_consultant=[
                    "Validez ou rejetez les sources candidates avant leur utilisation dans la rédaction."
                ],
                requires_confirmation=True,
            )

        if routing.needs_new_research:
            actions = research_choice_actions(
                existing_sources_available=existing_sources_available
            )
            if existing_sources_available:
                message = (
                    "Cette amélioration peut être renforcée scientifiquement. Choisissez comment continuer :\n\n"
                    "1. Utiliser les sources déjà validées — aucune nouvelle recherche ne sera lancée.\n"
                    "2. Lancer une recherche ciblée — EnnoScholar recherchera de nouvelles publications, "
                    "qui resteront candidates jusqu'à votre validation.\n\n"
                    "Le texte reste inchangé tant que ce choix n'est pas fait."
                )
            else:
                message = (
                    "Aucune source scientifique déjà validée n'est suffisamment disponible pour ce flux. "
                    "Vous pouvez lancer une recherche ciblée avec EnnoScholar. Les résultats resteront "
                    "candidats jusqu'à votre validation et le texte reste inchangé."
                )
            return ImprovementResult(
                ok=True,
                state=ImprovementState.AWAITING_EVIDENCE,
                assistant_message=message,
                routing=routing,
                audit=audit,
                evidence=evidence,
                agents_used=["EnnoAmelioration", "EnnoDiagnostic"] if diagnostic_required else ["EnnoAmelioration"],
                actions=actions,
                questions_for_consultant=[
                    "Souhaitez-vous utiliser les sources déjà validées ou lancer une recherche ciblée ?"
                ],
                requires_confirmation=True,
            )

        if research_choice == RESEARCH_USE_EXISTING and not existing_sources_available:
            actions = research_choice_actions(existing_sources_available=False)
            return ImprovementResult(
                ok=True,
                state=ImprovementState.AWAITING_EVIDENCE,
                assistant_message=(
                    "Aucune source scientifique déjà validée n'est disponible pour étayer cette section. "
                    "Le texte reste inchangé. Vous pouvez lancer une recherche ciblée avec EnnoScholar."
                ),
                routing=routing,
                audit=audit,
                evidence=evidence,
                agents_used=["EnnoAmelioration", "EnnoDiagnostic"] if diagnostic_required else ["EnnoAmelioration"],
                actions=actions,
                requires_confirmation=True,
            )

        if (
            routing.needs_scholar
            and not routing.needs_new_research
            and not existing_sources_available
            and not routing.forbids_new_research
        ):
            return ImprovementResult(
                ok=True,
                state=ImprovementState.AWAITING_EVIDENCE,
                assistant_message=(
                    "La demande nécessite de nouveaux arguments scientifiques, mais aucune "
                    "source validée n'est disponible pour les étayer. Le texte reste inchangé. "
                    "Vous pouvez lancer une recherche ciblée EnnoScholar sur cette section ; "
                    "elle n'exigera un diagnostic de verrou que si la cible est réellement une "
                    "incertitude ou une demande de qualification CIR."
                ),
                routing=routing,
                audit=audit,
                evidence=evidence,
                agents_used=(
                    ["EnnoAmelioration", "EnnoDiagnostic"]
                    if diagnostic_required
                    else ["EnnoAmelioration"]
                ),
                actions=research_choice_actions(existing_sources_available=False),
                questions_for_consultant=[
                    "Souhaitez-vous lancer la recherche ciblée avant la réécriture ?"
                ],
                requires_confirmation=True,
            )

        scholar_missing = routing.needs_scholar and not (
            evidence.get("scholar") or {}
        ).get("available")
        document_mode = request.target_scope in {
            TargetScope.MULTI_SECTION,
            TargetScope.FULL_DOCUMENT,
        }
        # L'absence de preuve Scholar bloque uniquement l'ajout de nouveaux faits
        # scientifiques. Elle ne doit jamais empêcher une amélioration éditoriale
        # à faits constants d'une section existante. Une recherche explicite est
        # déjà traitée plus haut via AWAITING_EVIDENCE.

        print('[EnnoAmel][WriterOwnership] final_writer=EnnoAmelioration ' + f'intents={[getattr(i, "value", str(i)) for i in routing.intents]} ' + f'accepted_articles={list(request.evidence_article_ids or [])}')
        # EnnoScholar fournit uniquement les preuves scientifiques. La redaction
        # de toute section, y compris un etat de l'art, reste la responsabilite
        # d'EnnoAmelioration afin de conserver un seul comparatif et un seul
        # contrat de revision dans l'interface Agent 3.
        # V3.5 — état de l'art existant + sources déjà validées :
        # EnnoAmelioration reste l'orchestrateur et le propriétaire de la version,
        # tandis qu'EnnoScholar ne produit que des AJOUTS SCIENTIFIQUES ANCRÉS.
        # Le texte source n'est jamais réécrit/remplacé dans ce chemin.
        delegated_to_scholar = False  # V3.17.1 Scholar=evidence, EnnoAmel=final writer
        try:
            if document_mode and routing.section_plan:
                improved_target, improved_full_text, generation = (
                    self._rewrite_document_by_plan(request, routing, evidence)
                )
                if not generation.get("call_count"):
                    no_research = routing.forbids_new_research or routing.forbids_scholar
                    return ImprovementResult(
                        ok=True,
                        state=ImprovementState.AWAITING_EVIDENCE,
                        assistant_message=(
                            "Aucune section n'a pu être révisée de façon sûre avec les éléments disponibles. "
                            + (
                                "Aucune recherche n'est lancée conformément à l'instruction."
                                if no_research
                                else (
                                    "Cette révision scientifique nécessite d'abord des "
                                    "sources exploitables. Une recherche doit produire des "
                                    "sources candidates avant que le consultant puisse les "
                                    "valider ; aucune source inexistante n'est considérée "
                                    "comme déjà sélectionnée."
                                )
                            )
                        ),
                        routing=routing,
                        audit=audit,
                        evidence=evidence,
                        generation=generation,
                        requires_confirmation=True,
                    )
            elif delegated_to_scholar:
                from agents.EnnoScholar.state_of_art.existing_review_enrichment_service import (
                    generate_state_of_art_additions,
                )

                parsed = parse_sections(request.target_text)
                additions, generation = generate_state_of_art_additions(
                    target_text=request.target_text,
                    sections=[item.model_dump(mode="json") for item in parsed],
                    instruction=request.instruction,
                    project_name=request.project_name,
                    project_domain=request.project_domain,
                    evidence_rows=(evidence.get("scholar") or {}).get("evidence") or [],
                )
                improved_target = self._merge_scholar_additions(
                    request.target_text, parsed, additions
                )
                issues = validate_conservative_revision(
                    request.target_text,
                    improved_target,
                    allowed_citation_ids=_allowed_citation_ids(evidence),
                    allow_reduction=False,
                    enrichment_requested=True,
                )
                improved_full_text = replace_target(
                    request.full_text, request.target_text, improved_target
                )
                generation = {
                    **generation,
                    "orchestrator": "EnnoAmelioration",
                    "merge_strategy": "anchored_additions_into_existing_version",
                    "scientific_workflow_v3_5": "source_immutable_plus_validated_additions",
                    "original_source_rewritten": False,
                    "conservation_validation": "warning" if issues else "passed",
                    "conservation_issues": issues,
                    "requires_consultant_review": bool(issues),
                }
            else:
                improved_target, improved_full_text, generation = self._rewrite_target(
                    request, routing, audit, evidence
                )
        except UnsafeRevisionError as exc:
            return ImprovementResult(
                ok=False,
                state=ImprovementState.REVIEW,
                assistant_message=revision_block_message(exc.issues),
                routing=routing,
                audit=audit,
                evidence=evidence,
                generation={
                    **exc.generation,
                    "conservation_validation": "rejected",
                    "issues": exc.issues,
                },
            )
        except (RuntimeError, ValueError) as exc:
            return ImprovementResult(
                ok=False,
                state=ImprovementState.REVIEW,
                assistant_message=(
                    "La rédaction n'a pas produit de proposition entièrement traçable. "
                    "Aucun texte n'a été remplacé et la version active reste inchangée."
                ),
                routing=routing,
                audit=audit,
                evidence=evidence,
                generation={
                    "agent": "EnnoScholar" if delegated_to_scholar else "EnnoAmelioration",
                    "orchestrator": "EnnoAmelioration",
                    "status": "not_publishable",
                    "error_type": type(exc).__name__,
                },
            )

        generation = {
            **generation,
            "agent": "EnnoAmelioration",
            "writing_owner": "EnnoAmelioration",
            "source_resolution": source_resolution,
        }
        trace = build_revision_trace(
            request.target_text, improved_target, routing, evidence
        )
        generation = {**generation, "trace": trace}

        # Human-in-the-loop : aucune alerte de traçabilité ou de conservation ne
        # supprime la candidate. Elles sont exposées au consultant dans le comparatif.
        conservation_issues = self._collect_conservation_issues(generation)
        for issue_index, issue in enumerate(conservation_issues, start=1):
            trace["unsupported_claims"].append(
                {
                    "change_id": f"conservation-{issue_index}",
                    "claim": "",
                    "reason": (
                        "Alerte de conservation à vérifier par le consultant : " + issue
                    ),
                    "markers": [],
                    "severity": "warning",
                }
            )
        if conservation_issues:
            trace["questions_for_consultant"].append(
                "La proposition comporte des alertes de conservation. Vous pouvez la "
                "valider, la rejeter ou demander une correction ciblée."
            )
        trace["blocking"] = False
        trace["has_warnings"] = bool(trace.get("unsupported_claims"))
        generation = {**generation, "trace": trace}

        skipped = generation.get("skipped_sections") or []
        if trace.get("has_warnings"):
            message = (
                "J'ai préparé une nouvelle version sans remplacer l'original. "
                "La proposition reste visible malgré les alertes détectées : consultez "
                "le comparatif, puis validez-la ou demandez-moi une correction ciblée."
            )
        else:
            message = (
                "J'ai préparé une nouvelle version sans remplacer l'original. "
                "Consultez le comparatif et la justification de chaque modification."
            )
        if skipped:
            message += (
                " Certaines sections ne disposaient pas de preuve scientifique validée : "
                "elles ont été améliorées uniquement à faits constants, sans ajout scientifique nouveau."
            )
        elif scholar_missing:
            message += (
                " La cible a été améliorée à faits constants ; aucun enrichissement scientifique nouveau "
                "n'a été ajouté faute de source validée."
            )
        return ImprovementResult(
            ok=True,
            state=ImprovementState.CANDIDATE_READY,
            assistant_message=message,
            routing=routing,
            audit=audit,
            improved_target=improved_target,
            improved_full_text=improved_full_text,
            evidence=evidence,
            generation=generation,
            changes=trace["changes"],
            sources_used=trace["sources_used"],
            agents_used=trace["agents_used"],
            unsupported_claims=trace["unsupported_claims"],
            questions_for_consultant=trace["questions_for_consultant"],
            requires_confirmation=bool(skipped or trace["unsupported_claims"]),
        )
