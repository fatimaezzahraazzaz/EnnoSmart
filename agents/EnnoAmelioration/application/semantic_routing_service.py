from __future__ import annotations

import json
import re
from typing import Any, Iterable

from ..domain.models import (
    ImprovementIntent,
    ParsedSection,
    RoutingDecision,
    SectionFunction,
    SectionRoutingPlan,
    SpecialistRoute,
    TargetScope,
)
from .intention_service import understand_instruction


_EDITORIAL_INTENTS = {
    ImprovementIntent.CLARITY,
    ImprovementIntent.STYLE,
    ImprovementIntent.STRUCTURE,
    ImprovementIntent.CONCISION,
}

_FASTJUDGE_FUNCTIONS = {
    "objectif": SectionFunction.CONTEXT,
    "verrou": SectionFunction.UNCERTAINTY,
    "methode": SectionFunction.METHOD,
    "parametre": SectionFunction.PARAMETER,
    "resultat": SectionFunction.RESULT,
    "limite": SectionFunction.LIMITATION,
    "contribution": SectionFunction.CONTRIBUTION,
    "bruit": SectionFunction.OTHER,
}


def _json_object(value: str) -> dict[str, Any]:
    raw = str(value or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.I | re.S)
    if fenced:
        raw = fenced.group(1).strip()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("La classification sémantique n'est pas un objet JSON.")
    return payload


def _route_value(needs_diagnostic: bool, needs_scholar: bool) -> SpecialistRoute:
    if needs_diagnostic and needs_scholar:
        return SpecialistRoute.DIAGNOSTIC_SCHOLAR
    if needs_diagnostic:
        return SpecialistRoute.DIAGNOSTIC
    if needs_scholar:
        return SpecialistRoute.SCHOLAR
    return SpecialistRoute.WRITER


class SemanticRoutingService:
    """Route une demande par intention et fonction sémantique.

    Le classifieur FastJudge du projet fournit le rôle R&D principal. Quand le
    client LLM du writer est disponible, un classement zero-shot complète ce
    rôle pour reconnaître notamment une synthèse ou un paysage scientifique,
    indépendamment du titre choisi par le consultant.
    """

    def __init__(self, llm: Any | None = None) -> None:
        self.llm = llm

    @staticmethod
    def _fastjudge(sections: list[ParsedSection]) -> dict[str, dict[str, Any]]:
        if not sections:
            return {}
        try:
            from modules.NLP.models import judge_passages_batch

            inputs = [
                "\n".join(part for part in (section.title, section.content[:6000]) if part)
                for section in sections
            ]
            predictions = judge_passages_batch(inputs)
        except Exception:
            return {}
        output: dict[str, dict[str, Any]] = {}
        for section, prediction in zip(sections, predictions):
            role = str(prediction.get("role") or prediction.get("label") or "bruit").casefold()
            output[section.section_id] = {
                "function": _FASTJUDGE_FUNCTIONS.get(role, SectionFunction.OTHER),
                "confidence": float(prediction.get("confidence") or prediction.get("score") or 0.0),
                "classifier": "fastjudge_8_roles",
                "model_role": role,
            }
        return output

    def _semantic_functions(self, sections: list[ParsedSection]) -> dict[str, dict[str, Any]]:
        if self.llm is None or not sections:
            return {}
        labels = ", ".join(item.value for item in SectionFunction)
        rows = [
            {
                "section_id": section.section_id,
                "title": section.title[:240],
                "content": section.content[:3600],
            }
            for section in sections[:80]
        ]
        prompt = f"""Classe la FONCTION SÉMANTIQUE de chaque section d'un dossier R&D.

Valeurs autorisées : {labels}

Définitions :
- context : objectif, contexte ou besoin du projet ;
- scientific_landscape : connaissances antérieures, comparaison à l'existant ou revue scientifique ;
- uncertainty : difficulté non résolue, obstacle ou incertitude scientifique/technique ;
- method : démarche, protocole, expérimentation ou travaux ;
- parameter : variables, conditions, contraintes ou réglages ;
- result : mesure, observation ou résultat ;
- limitation : limite, échec, validité restreinte ou manque ;
- contribution : apport ou connaissance produite ;
- synthesis : conclusion ou synthèse transversale ;
- other : contenu administratif ou fonction indéterminée.

Consignes : juge surtout le contenu, pas les mots du titre. N'ajoute aucun fait.
Retourne uniquement :
{{"sections":[{{"section_id":"...","function":"...","confidence":0.0}}]}}

SECTIONS
{json.dumps(rows, ensure_ascii=False, separators=(',', ':'))}
"""
        try:
            raw = self.llm.generate(
                prompt,
                temperature=0.0,
                max_output_tokens=min(4000, max(500, len(rows) * 80)),
                max_input_tokens=30000,
                retries=0,
                json_mode=True,
                request_name="ennoamelioration:semantic_section_routing",
            )
            payload = _json_object(raw)
        except Exception:
            return {}

        allowed_ids = {section.section_id for section in sections}
        output: dict[str, dict[str, Any]] = {}
        for item in payload.get("sections") or []:
            if not isinstance(item, dict):
                continue
            section_id = str(item.get("section_id") or "")
            if section_id not in allowed_ids:
                continue
            try:
                function = SectionFunction(str(item.get("function") or "other"))
            except ValueError:
                function = SectionFunction.OTHER
            try:
                confidence = min(1.0, max(0.0, float(item.get("confidence") or 0.0)))
            except (TypeError, ValueError):
                confidence = 0.0
            output[section_id] = {
                "function": function,
                "confidence": confidence,
                "classifier": "semantic_zero_shot",
            }
        return output

    def classify_sections(self, sections: Iterable[ParsedSection]) -> list[dict[str, Any]]:
        rows = list(sections)
        fastjudge = self._fastjudge(rows)
        semantic = self._semantic_functions(rows)
        output: list[dict[str, Any]] = []
        for section in rows:
            fast = fastjudge.get(section.section_id) or {}
            sem = semantic.get(section.section_id) or {}
            # Le classifieur sémantique complète FastJudge. Une réponse faible
            # conserve le rôle du modèle NLP spécialisé déjà entraîné.
            chosen = sem if float(sem.get("confidence") or 0.0) >= 0.55 else fast
            output.append(
                {
                    "section": section,
                    "function": chosen.get("function") or SectionFunction.OTHER,
                    "confidence": float(chosen.get("confidence") or 0.0),
                    "classifier": str(chosen.get("classifier") or "safe_fallback"),
                    "model_role": fast.get("model_role"),
                }
            )
        return output

    @staticmethod
    def _specialists(
        intents: list[ImprovementIntent],
        function: SectionFunction,
        *,
        document_mode: bool,
        forbids_scholar: bool = False,
        candidate_revision: bool = False,
        revision_allows_evidence_enrichment: bool = False,
    ) -> tuple[bool, bool, list[str]]:
        intent_set = set(intents)
        explicit_editorial = bool(intent_set & _EDITORIAL_INTENTS) and not bool(
            intent_set
            & {
                ImprovementIntent.ARGUMENTATION,
                ImprovementIntent.CIR_ELIGIBILITY,
                ImprovementIntent.SCIENTIFIC_ENRICHMENT,
                ImprovementIntent.RESEARCH,
            }
        )
        wants_rd_evidence = bool(
            intent_set
            & {ImprovementIntent.ARGUMENTATION, ImprovementIntent.CIR_ELIGIBILITY}
        )
        wants_external_science = bool(
            intent_set
            & {ImprovementIntent.SCIENTIFIC_ENRICHMENT, ImprovementIntent.RESEARCH}
        )
        # Le besoin général de renforcer le CIR ne signifie pas que toute section
        # doit recevoir les verrous du Diagnostic. Le spécialiste est mobilisé
        # uniquement pour les rôles où des faits projet R&D peuvent réellement
        # étayer le contenu. OTHER conserve le fallback historique lorsque le
        # rôle n'a pas pu être déterminé avec confiance.
        diagnostic = bool(
            wants_rd_evidence
            and (
                ImprovementIntent.CIR_ELIGIBILITY in intent_set
                or not wants_external_science
            )
            and function
            not in {SectionFunction.CONTEXT, SectionFunction.SCIENTIFIC_LANDSCAPE}
        )
        scholar = wants_external_science
        rationale: list[str] = []

        if candidate_revision and not revision_allows_evidence_enrichment:
            diagnostic = False
            scholar = False
            rationale.append(
                "Correction de candidate ciblée : aucun spécialiste n'est mobilisé sans demande explicite d'enrichissement."
            )
            return diagnostic, scholar, rationale

        if not explicit_editorial and function == SectionFunction.SCIENTIFIC_LANDSCAPE:
            scholar = True
            rationale.append("La fonction sémantique appelle des preuves scientifiques traçables.")

        if document_mode and not explicit_editorial and not scholar and function in {
            SectionFunction.UNCERTAINTY,
            SectionFunction.METHOD,
            SectionFunction.PARAMETER,
            SectionFunction.RESULT,
            SectionFunction.LIMITATION,
            SectionFunction.CONTRIBUTION,
        }:
            diagnostic = True
            rationale.append("La section porte un contenu R&D qui doit rester ancré dans le dossier.")

        if scholar and function == SectionFunction.UNCERTAINTY:
            diagnostic = True
            rationale.append("Les sources scientifiques doivent être reliées au diagnostic du projet.")

        if forbids_scholar and scholar:
            scholar = False
            rationale.append(
                "Les références externes sont exclues conformément au corpus autorisé par le consultant."
            )

        return diagnostic, scholar, rationale

    def route(
        self,
        instruction: str,
        scope: TargetScope,
        target_text: str,
        *,
        section_id: str | None = None,
        section_title: str | None = None,
        sections: list[ParsedSection] | None = None,
    ) -> RoutingDecision:
        base = understand_instruction(instruction, scope)
        is_document = base.target_scope in {TargetScope.MULTI_SECTION, TargetScope.FULL_DOCUMENT}
        if is_document:
            parsed = list(sections or [])
        else:
            parsed = [
                ParsedSection(
                    section_id=section_id or "target",
                    title=section_title or "",
                    level=1,
                    start=0,
                    end=len(target_text),
                    content=target_text,
                )
            ]

        classified = self.classify_sections(parsed)
        plans: list[SectionRoutingPlan] = []
        for item in classified:
            section = item["section"]
            function = item["function"]
            needs_diagnostic, needs_scholar, reasons = self._specialists(
                base.intents,
                function,
                document_mode=is_document,
                forbids_scholar=base.forbids_scholar,
                candidate_revision=base.candidate_revision,
                revision_allows_evidence_enrichment=base.revision_allows_evidence_enrichment,
            )
            plans.append(
                SectionRoutingPlan(
                    section_id=section.section_id,
                    title=section.title,
                    function=function,
                    confidence=item["confidence"],
                    classifier=item["classifier"],
                    route=_route_value(needs_diagnostic, needs_scholar),
                    needs_diagnostic=needs_diagnostic,
                    needs_scholar=needs_scholar,
                    rationale=reasons,
                )
            )

        if base.editorial_only:
            # V2.7 : la fonction sémantique de la section ne peut jamais
            # réactiver un spécialiste lorsque le consultant a demandé une
            # réécriture purement éditoriale. On conserve toutefois la fonction
            # METHOD/RESULT/etc. pour donner au writer le bon contrat de style.
            plans = [
                plan.model_copy(
                    update={
                        "route": SpecialistRoute.WRITER,
                        "needs_diagnostic": False,
                        "needs_scholar": False,
                        "rationale": [
                            *plan.rationale,
                            "Mode éditorial strict : Writer uniquement.",
                        ],
                    }
                )
                for plan in plans
            ]

        primary = plans[0] if len(plans) == 1 else None
        needs_diagnostic = (
            any(item.needs_diagnostic for item in plans) if plans else base.needs_diagnostic
        )
        needs_scholar = bool(
            not base.forbids_scholar
            and (any(item.needs_scholar for item in plans) if plans else base.needs_scholar)
        )
        if base.editorial_only:
            needs_diagnostic = False
            needs_scholar = False
        return base.model_copy(
            update={
                "needs_diagnostic": needs_diagnostic,
                "needs_scholar": needs_scholar,
                "needs_project_evidence": bool(
                    needs_diagnostic
                ),
                "specialist_route": _route_value(needs_diagnostic, needs_scholar),
                "section_function": primary.function if primary else SectionFunction.OTHER,
                "section_confidence": primary.confidence if primary else 0.0,
                "semantic_classifier": primary.classifier if primary else "section_plan",
                "section_plan": plans,
                "rationale": [
                    *base.rationale,
                    *[reason for plan in plans for reason in plan.rationale],
                ],
            }
        )


def route_for_section(plan: SectionRoutingPlan, parent: RoutingDecision) -> RoutingDecision:
    """Construit la décision minimale transmise au writer d'une section."""

    return parent.model_copy(
        update={
            "target_scope": TargetScope.SECTION,
            "needs_diagnostic": plan.needs_diagnostic,
            "needs_scholar": plan.needs_scholar,
            "needs_project_evidence": plan.needs_diagnostic,
            "specialist_route": plan.route,
            "section_function": plan.function,
            "section_confidence": plan.confidence,
            "semantic_classifier": plan.classifier,
            "section_plan": [plan],
            "rationale": [*parent.rationale, *plan.rationale],
        }
    )
