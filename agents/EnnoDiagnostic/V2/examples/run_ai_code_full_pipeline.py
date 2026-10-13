from __future__ import annotations

import importlib
import inspect
import json
import sys
from collections import Counter
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional


# =============================================================================
# CONFIGURATION
# =============================================================================

ROOT = Path(r"C:\EnnoSmart")
DOCX_PATH = ROOT / "AI_CODE_CIR_FINAL.docx"
OUTPUT_PATH = (
    ROOT
    / "agents"
    / "EnnoDiagnostic"
    / "V2"
    / "examples"
    / "ai_code_full_result.json"
)

PACKAGE = "agents.EnnoDiagnostic.V2.ennodiagnostic_v2"
PACKAGE_DIR = (
    ROOT
    / "agents"
    / "EnnoDiagnostic"
    / "V2"
    / "ennodiagnostic_v2"
)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

V2_ROOT = ROOT / "agents" / "EnnoDiagnostic" / "V2"
if str(V2_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ROOT))


# =============================================================================
# IMPORTS ENNODIAGNOSTIC V2
# =============================================================================

from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.audit import build_audit
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.chunking import build_chunks
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.config import PipelineConfig
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.consolidation import GlobalLockConsolidator
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.fusion import fuse_items
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.lock_family_synthesis import (
    LockFamilySynthesizer,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.project_frascati import (
    ProjectFrascatiEvaluator,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.qualification import LockQualifier
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.review import build_review_queue
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.rnd_approach import (
    RnDApproachAnalyzer,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.runtime_adapters import (
    EnnoSmartLLMAdapter,
)
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput
from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.semantic_extractor import (
    SemanticExtractor,
)


# =============================================================================
# OUTILS
# =============================================================================

def banner(title: str) -> None:
    print("\n" + "=" * 96)
    print(title)
    print("=" * 96)


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Path):
        return str(value)

    if is_dataclass(value):
        return {
            key: jsonable(val)
            for key, val in asdict(value).items()
        }

    if isinstance(value, dict):
        return {
            str(key): jsonable(val)
            for key, val in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [jsonable(x) for x in value]

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return jsonable(to_dict())

    if hasattr(value, "__dict__"):
        return {
            str(key): jsonable(val)
            for key, val in vars(value).items()
            if not str(key).startswith("_")
        }

    return str(value)


def _extract_docx_text(path: Path) -> str:
    """
    Extraction DOCX simple et non destructive.
    Conserve l'ordre des paragraphes et tableaux.
    Aucun filtrage sémantique n'est appliqué.
    """
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
    except Exception as exc:
        raise RuntimeError(
            "python-docx est nécessaire pour lire AI_CODE_CIR_FINAL.docx. "
            "Installe-le avec: pip install python-docx"
        ) from exc

    doc = Document(str(path))
    blocks = []

    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            paragraph = Paragraph(child, doc)
            text = str(paragraph.text or "").strip()
            if text:
                blocks.append(text)

        elif isinstance(child, CT_Tbl):
            table = Table(child, doc)
            for row in table.rows:
                cells = []
                seen = set()

                for cell in row.cells:
                    value = " ".join(
                        str(cell.text or "").split()
                    ).strip()

                    if value and value not in seen:
                        seen.add(value)
                        cells.append(value)

                if cells:
                    blocks.append(" | ".join(cells))

    return "\n\n".join(blocks).strip()


def _new_llm(
    request_name: str,
    max_output_tokens: int,
) -> EnnoSmartLLMAdapter:
    return EnnoSmartLLMAdapter(
        request_name=request_name,
        temperature=0.0,
        max_output_tokens=max_output_tokens,
        retries=2,
    )


# =============================================================================
# SCORE CIR : UTILISE LE MODULE LOCAL ACTUEL, SANS REIMPLEMENTER LA FORMULE
# =============================================================================

_SCORE_CONTEXT_ALIASES = {
    "entities": "entities",
    "canonical_entities": "entities",
    "lock_entities": "lock_entities",
    "locks": "lock_entities",

    "extractions": "extractions",
    "items": "extractions",
    "extraction_items": "extractions",

    "rnd": "rnd",
    "rnd_assessment": "rnd",
    "rnd_approach": "rnd",
    "rnd_approach_assessment": "rnd",

    "frascati": "frascati",
    "project_frascati": "frascati",
    "frascati_assessment": "frascati",
    "project_frascati_assessment": "frascati",

    "chunks": "chunks",
}


def _module_name_from_path(path: Path) -> Optional[str]:
    try:
        rel = path.resolve().relative_to(ROOT.resolve())
    except Exception:
        return None

    if rel.suffix.lower() != ".py":
        return None

    parts = list(rel.with_suffix("").parts)

    if parts and parts[-1] == "__init__":
        parts = parts[:-1]

    return ".".join(parts)


def _find_score_modules() -> list[str]:
    """
    Cherche le scorer déterministe déjà présent dans la copie locale.
    On ne réécrit PAS une nouvelle formule de score dans ce runner.
    """
    roots = [
        PACKAGE_DIR,
        ROOT / "agents" / "EnnoDiagnostic" / "V2",
    ]

    candidates = []

    strong_markers = (
        "Verrous / incertitude",
        "Qualit",
        "raw_score",
        "risk_level",
        "potential_level",
    )

    for search_root in roots:
        if not search_root.exists():
            continue

        for path in search_root.rglob("*.py"):
            name_lower = path.name.lower()

            if path.name == Path(__file__).name:
                continue

            try:
                source = path.read_text(
                    encoding="utf-8",
                    errors="ignore",
                )
            except Exception:
                continue

            score_name_hint = (
                "score" in name_lower
                or "scor" in name_lower
                or "eligib" in name_lower
            )

            marker_hits = sum(
                marker.lower() in source.lower()
                for marker in strong_markers
            )

            if score_name_hint or marker_hits >= 3:
                module_name = _module_name_from_path(path)
                if module_name:
                    candidates.append(
                        (
                            marker_hits,
                            score_name_hint,
                            module_name,
                        )
                    )

    candidates.sort(
        key=lambda x: (x[0], x[1]),
        reverse=True,
    )

    out = []
    for _, _, module_name in candidates:
        if module_name not in out:
            out.append(module_name)

    return out


def _build_kwargs(
    callable_obj,
    context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    try:
        signature = inspect.signature(callable_obj)
    except Exception:
        return None

    kwargs = {}

    for name, parameter in signature.parameters.items():
        if name in {"self", "cls"}:
            continue

        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue

        context_key = _SCORE_CONTEXT_ALIASES.get(name)

        if context_key is not None:
            kwargs[name] = context[context_key]
            continue

        if parameter.default is not inspect.Parameter.empty:
            continue

        # Paramètre obligatoire inconnu : on ne devine pas.
        return None

    return kwargs


def _looks_like_score_result(value: Any) -> bool:
    data = jsonable(value)

    if not isinstance(data, dict):
        return False

    keys = {str(k).lower() for k in data.keys()}

    return bool(
        {"score", "raw_score", "risk_level", "potential_level"}
        & keys
    )


def _call_score_candidate(
    callable_obj,
    context: Dict[str, Any],
) -> Optional[Any]:
    kwargs = _build_kwargs(callable_obj, context)

    if kwargs is None:
        return None

    try:
        result = callable_obj(**kwargs)
    except TypeError:
        return None

    if _looks_like_score_result(result):
        return result

    return None


def compute_existing_cir_score(
    *,
    entities,
    extractions,
    rnd,
    frascati,
    chunks,
):
    """
    Trouve et appelle le scorer CIR déjà existant dans EnnoDiagnostic V2.

    IMPORTANT :
    - le score utilise les entités précises qualifiées ;
    - les familles d'affichage ne sont volontairement PAS passées au scorer.
    """
    context = {
        "entities": entities,
        "lock_entities": [
            entity
            for entity in entities
            if getattr(entity, "kind", None) == "lock"
        ],
        "extractions": extractions,
        "rnd": rnd,
        "frascati": frascati,
        "chunks": chunks,
    }

    module_names = _find_score_modules()

    if not module_names:
        raise RuntimeError(
            "Aucun module local de score CIR n'a été trouvé dans "
            "agents/EnnoDiagnostic/V2. "
            "Le runner refuse d'inventer une nouvelle formule."
        )

    preferred_function_names = [
        "compute_cir_score",
        "calculate_cir_score",
        "score_cir",
        "score_project",
        "score_diagnostic_cir",
        "build_cir_score",
        "evaluate_cir_score",
        "compute_score",
    ]

    preferred_method_names = [
        "score",
        "compute",
        "evaluate",
        "assess",
        "calculate",
        "run",
    ]

    errors = []

    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            errors.append(
                f"{module_name}: import {type(exc).__name__}: {exc}"
            )
            continue

        # 1) fonctions connues/préférées
        for name in preferred_function_names:
            obj = getattr(module, name, None)

            if callable(obj) and inspect.isfunction(obj):
                result = _call_score_candidate(
                    obj,
                    context,
                )
                if result is not None:
                    print(
                        f"[Runner] Score CIR : "
                        f"{module_name}.{name}"
                    )
                    return result

        # 2) autres fonctions du module dont le nom évoque le score
        for name, obj in vars(module).items():
            if not inspect.isfunction(obj):
                continue

            if getattr(obj, "__module__", "") != module.__name__:
                continue

            low = name.lower()
            if "score" not in low and "eligib" not in low:
                continue

            result = _call_score_candidate(
                obj,
                context,
            )
            if result is not None:
                print(
                    f"[Runner] Score CIR : "
                    f"{module_name}.{name}"
                )
                return result

        # 3) classes de scoring
        for class_name, cls in vars(module).items():
            if not inspect.isclass(cls):
                continue

            if getattr(cls, "__module__", "") != module.__name__:
                continue

            low = class_name.lower()
            if "score" not in low and "eligib" not in low:
                continue

            init_kwargs = _build_kwargs(
                cls,
                context,
            )

            if init_kwargs is None:
                continue

            try:
                instance = cls(**init_kwargs)
            except Exception as exc:
                errors.append(
                    f"{module_name}.{class_name}: "
                    f"{type(exc).__name__}: {exc}"
                )
                continue

            for method_name in preferred_method_names:
                method = getattr(
                    instance,
                    method_name,
                    None,
                )

                if not callable(method):
                    continue

                result = _call_score_candidate(
                    method,
                    context,
                )

                if result is not None:
                    print(
                        f"[Runner] Score CIR : "
                        f"{module_name}.{class_name}."
                        f"{method_name}"
                    )
                    return result

    raise RuntimeError(
        "Un module de scoring a été trouvé, mais le runner "
        "n'a pas pu identifier automatiquement son API publique.\n"
        "Modules essayés:\n- "
        + "\n- ".join(module_names)
        + (
            "\nErreurs:\n- " + "\n- ".join(errors)
            if errors
            else ""
        )
    )


# =============================================================================
# PIPELINE COMPLET
# =============================================================================

def main() -> None:
    if not DOCX_PATH.exists():
        raise FileNotFoundError(
            f"Fichier introuvable : {DOCX_PATH}"
        )

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    banner("ENNODIAGNOSTIC V2 - AI-CODE - PIPELINE COMPLET")
    print(f"FICHIER : {DOCX_PATH}")

    # -------------------------------------------------------------------------
    # 0. Lecture document
    # -------------------------------------------------------------------------
    text = _extract_docx_text(DOCX_PATH)

    if not text:
        raise RuntimeError(
            "Aucun texte extrait du DOCX."
        )

    print(f"CARACTERES EXTRAITS : {len(text)}")

    document = DocumentInput(
        document_id="ai_code_full",
        name=DOCX_PATH.name,
        text=text,
        declared_mode="raw",
        metadata={
            "source_path": str(DOCX_PATH),
            "runner": "run_ai_code_full_pipeline",
        },
    )

    config = PipelineConfig()

    # -------------------------------------------------------------------------
    # 1. CHUNKING
    # -------------------------------------------------------------------------
    banner("1/8 - CHUNKING")

    chunks = build_chunks(
        document,
        config.chunk,
    )

    print(f"CHUNKS : {len(chunks)}")

    section_counts = Counter(
        chunk.section_title or "(sans section)"
        for chunk in chunks
    )

    print("SECTIONS / CHUNKS :")
    for section, count in section_counts.items():
        print(f"  {count:>3}  {section}")

    # -------------------------------------------------------------------------
    # LLMs
    # -------------------------------------------------------------------------
    semantic_llm = _new_llm(
        "ennodiagnostic:v2:ai_code:semantic",
        4500,
    )

    consolidation_llm = _new_llm(
        "ennodiagnostic:v2:ai_code:consolidation",
        6000,
    )

    qualification_llm = _new_llm(
        "ennodiagnostic:v2:ai_code:qualification",
        9000,
    )

    family_llm = _new_llm(
        "ennodiagnostic:v2:ai_code:main_families",
        5000,
    )

    rnd_llm = _new_llm(
        "ennodiagnostic:v2:ai_code:rnd",
        5500,
    )

    frascati_llm = _new_llm(
        "ennodiagnostic:v2:ai_code:project_frascati",
        6000,
    )

    # -------------------------------------------------------------------------
    # 2. EXTRACTION
    # -------------------------------------------------------------------------
    banner("2/8 - EXTRACTION SEMANTIQUE")

    extractions = SemanticExtractor(
        semantic_llm
    ).extract(chunks)

    extraction_counts = Counter(
        item.kind
        for item in extractions
    )

    print(f"EXTRACTIONS TOTAL : {len(extractions)}")

    for kind in [
        "lock",
        "objective",
        "method",
        "parameter",
        "result",
    ]:
        print(
            f"  {kind:<10}: "
            f"{extraction_counts.get(kind, 0)}"
        )

    raw_lock_count = extraction_counts.get(
        "lock",
        0,
    )

    # -------------------------------------------------------------------------
    # 3. CONSOLIDATION
    # -------------------------------------------------------------------------
    banner("3/8 - CONSOLIDATION GLOBALE")

    lock_entities = GlobalLockConsolidator(
        consolidation_llm
    ).consolidate(extractions)

    non_lock_items = [
        item
        for item in extractions
        if item.kind != "lock"
    ]

    non_lock_entities = fuse_items(
        non_lock_items,
        config.fusion,
    )

    entities = (
        list(lock_entities)
        + list(non_lock_entities)
    )

    consolidated_locks = [
        entity
        for entity in entities
        if entity.kind == "lock"
    ]

    print(
        f"VERROUS BRUTS EXTRAITS : "
        f"{raw_lock_count}"
    )

    print(
        f"VERROUS CONSOLIDES : "
        f"{len(consolidated_locks)}"
    )

    # -------------------------------------------------------------------------
    # 4. QUALIFICATION
    # -------------------------------------------------------------------------
    banner("4/8 - QUALIFICATION DES VERROUS")

    entities = LockQualifier(
        qualification_llm
    ).qualify(
        entities,
        chunks=chunks,
    )

    qualified_locks = [
        entity
        for entity in entities
        if entity.kind == "lock"
    ]

    qualification_counts = Counter(
        str(
            (entity.metadata or {}).get(
                "qualification_status",
                "NOT_EVALUATED",
            )
        ).upper()
        for entity in qualified_locks
    )

    for status in [
        "VALID_LOCK",
        "NEEDS_REFORMULATION",
        "REQUIREMENT_ONLY",
        "ENGINEERING_DIFFICULTY",
        "INSUFFICIENT_EVIDENCE",
        "NOT_EVALUATED",
    ]:
        print(
            f"  {status:<24}: "
            f"{qualification_counts.get(status, 0)}"
        )

    # -------------------------------------------------------------------------
    # 5. FAMILLES PRINCIPALES
    # -------------------------------------------------------------------------
    banner("5/8 - FAMILLES PRINCIPALES")

    family_synthesis = LockFamilySynthesizer(
        family_llm
    ).synthesize(entities)

    print(
        f"FAMILLES PRINCIPALES : "
        f"{len(family_synthesis.families)}"
    )

    print(
        f"LITTERATURE UNIQUEMENT : "
        f"{len(family_synthesis.literature_only_entity_ids)}"
    )

    print(
        f"AUTRES SIGNAUX CONSERVES / NON ASSIGNES : "
        f"{len(family_synthesis.unassigned_entity_ids)}"
    )

    for index, family in enumerate(
        family_synthesis.families,
        start=1,
    ):
        print("\n" + "-" * 96)
        print(
            f"FAMILLE {index} - "
            f"{family.title}"
        )
        print(
            f"STATUT : {family.status}"
        )
        print(
            f"SOUS-VERROUS : "
            f"{len(family.member_entity_ids)}"
        )
        print(
            f"SYNTHESE : {family.statement}"
        )
        print(
            "MEMBRES : "
            + ", ".join(
                family.member_entity_ids
            )
        )

    # -------------------------------------------------------------------------
    # 6. DEMARCHE R&D
    # -------------------------------------------------------------------------
    banner("6/8 - DEMARCHE R&D")

    rnd = RnDApproachAnalyzer(
        rnd_llm
    ).assess(
        extractions,
        chunks=chunks,
    )

    print(f"STATUS : {rnd.status}")
    print(
        f"HYPOTHESES       : "
        f"{rnd.hypothesis_status}"
    )
    print(
        f"EXPERIMENTATION  : "
        f"{rnd.experimentation_status}"
    )
    print(
        f"PARAMETRES       : "
        f"{rnd.parameter_study_status}"
    )
    print(
        f"ITERATIONS       : "
        f"{rnd.iteration_status}"
    )
    print(
        f"ECHECS/APPRENT.  : "
        f"{rnd.failure_learning_status}"
    )
    print(
        f"ANALYSE RESULTAT : "
        f"{rnd.result_analysis_status}"
    )
    print(
        f"GAIN CONNAISS.   : "
        f"{rnd.knowledge_gain_status}"
    )

    # -------------------------------------------------------------------------
    # 7. FRASCATI PROJET
    # -------------------------------------------------------------------------
    banner("7/8 - FRASCATI PROJET")

    project_frascati = ProjectFrascatiEvaluator(
        frascati_llm
    ).assess(
        entities,
        extractions,
        rnd,
        chunks=chunks,
    )

    for criterion in project_frascati.criteria:
        print(
            f"{criterion.criterion.upper():<18}: "
            f"{criterion.status}"
        )

    # -------------------------------------------------------------------------
    # 8. SCORE CIR DETERMINISTE
    # -------------------------------------------------------------------------
    banner("8/8 - SCORE CIR")

    cir_score = compute_existing_cir_score(
        entities=entities,
        extractions=extractions,
        rnd=rnd,
        frascati=project_frascati,
        chunks=chunks,
    )

    score_dict = jsonable(cir_score)

    print(
        f"SCORE FINAL : "
        f"{score_dict.get('score', '?')} / 100"
    )

    print(
        f"SCORE BRUT  : "
        f"{score_dict.get('raw_score', '?')}"
    )

    print(
        f"POTENTIEL   : "
        f"{score_dict.get('potential_level', '?')}"
    )

    print(
        f"RISQUE      : "
        f"{score_dict.get('risk_level', '?')}"
    )

    # -------------------------------------------------------------------------
    # REVIEW + AUDIT
    # -------------------------------------------------------------------------
    review = build_review_queue(
        entities,
        require_all_locks=(
            config.require_human_review_for_locks
        ),
    )

    audit = build_audit(
        chunks,
        extractions,
        entities,
    )

    # -------------------------------------------------------------------------
    # JSON FINAL
    # -------------------------------------------------------------------------
    payload = {
        "source_file": str(DOCX_PATH),
        "runner": "run_ai_code_full_pipeline",
        "pipeline": {
            "version": "ennodiagnostic_v2_ai_code_full_runner_1",
            "documents": [
                {
                    "document_id": document.document_id,
                    "name": document.name,
                    "declared_mode": document.declared_mode,
                    "detected_mode": (
                        chunks[0].document_mode
                        if chunks
                        else "unknown"
                    ),
                    "chunks": len(chunks),
                }
            ],
            "chunks": jsonable(chunks),
            "extractions": jsonable(extractions),
            "entities": jsonable(entities),
            "lock_families": jsonable(
                family_synthesis
            ),
            "review_queue": jsonable(review),
            "audit": jsonable(audit),
        },
        "rnd_approach": jsonable(rnd),
        "project_frascati": jsonable(
            project_frascati
        ),
        "cir_score": score_dict,
        "summary_counts": {
            "chunks": len(chunks),
            "extractions_total": len(extractions),
            "extractions_by_kind": dict(
                extraction_counts
            ),
            "raw_lock_extractions": raw_lock_count,
            "consolidated_locks": len(
                consolidated_locks
            ),
            "qualification_counts": dict(
                qualification_counts
            ),
            "main_lock_families": len(
                family_synthesis.families
            ),
            "literature_only_locks": len(
                family_synthesis.literature_only_entity_ids
            ),
            "other_preserved_signals": len(
                family_synthesis.unassigned_entity_ids
            ),
        },
    }

    OUTPUT_PATH.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    banner("TERMINE")
    print(f"RESULTAT JSON : {OUTPUT_PATH}")

    print("\nRESUME RAPIDE")
    print(
        f"  chunks               : "
        f"{len(chunks)}"
    )
    print(
        f"  verrous bruts        : "
        f"{raw_lock_count}"
    )
    print(
        f"  verrous consolides   : "
        f"{len(consolidated_locks)}"
    )
    print(
        f"  VALID_LOCK           : "
        f"{qualification_counts.get('VALID_LOCK', 0)}"
    )
    print(
        f"  NEEDS_REFORMULATION  : "
        f"{qualification_counts.get('NEEDS_REFORMULATION', 0)}"
    )
    print(
        f"  familles principales : "
        f"{len(family_synthesis.families)}"
    )
    print(
        f"  demarche R&D         : "
        f"{rnd.status}"
    )
    print(
        f"  score CIR            : "
        f"{score_dict.get('score', '?')}"
    )


if __name__ == "__main__":
    main()
