from __future__ import annotations

import argparse
import datetime as _dt
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PATCH_ID = "ENNODIAG_FINAL_FIX_V4_20260829"

CONSULTANT = Path("agents/EnnoDiagnostic/consultant_verrou_synthesizer.py")
AXIS = Path("agents/EnnoDiagnostic/scientific_axis_synthesizer.py")
PRESENTER = Path("agents/EnnoDiagnostic/diagnostic_static_presenter.py")
AGENT = Path("agents/EnnoDiagnostic/ennodiagnostic_agent.py")
WRITER = Path("agents/EnnoDiagnostic/structured_eligibility_writer.py")
FRASCATI = Path("modules/NLP/frascati_assessment.py")

TARGETS = [CONSULTANT, AXIS, PRESENTER, AGENT, WRITER, FRASCATI]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def replace_once(text: str, old: str, new: str, label: str, *, optional: bool = False) -> str:
    count = text.count(old)
    if count == 0 and optional:
        return text
    if count != 1:
        raise RuntimeError(f"[{label}] ancre attendue 1 fois, trouvée {count} fois")
    return text.replace(old, new, 1)


def sub_once(
    text: str,
    pattern: str,
    repl: str,
    label: str,
    *,
    flags: int = 0,
    optional: bool = False,
    literal_replacement: bool = False,
) -> str:
    # re.sub interprète les backslashes du remplacement (\1, \g<...>, etc.).
    # Pour les gros blocs de code contenant leurs propres regex (\s, \b, \w...),
    # il faut injecter le texte LITTÉRALEMENT, sinon Python lève par ex.
    # « bad escape \s ». Les remplacements qui utilisent réellement \1 restent
    # en mode normal avec literal_replacement=False.
    replacement = (lambda _match: repl) if literal_replacement else repl
    out, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count == 0 and optional:
        return text
    if count != 1:
        raise RuntimeError(f"[{label}] ancre regex attendue 1 fois, trouvée {count} fois")
    return out


def add_marker(text: str, module_name: str) -> str:
    if PATCH_ID in text:
        return text
    marker = f'\n# {PATCH_ID} — {module_name}\n'
    pos = text.find("\n", text.find("from __future__ import annotations"))
    if pos >= 0:
        return text[:pos + 1] + marker + text[pos + 1:]
    return marker + text


DECLARED_LOCK_BLOCK = r'''_DECLARED_LOCK_SIGNAL_RE = re.compile(
    r"\b(?:verrou(?:s)? (?:important|majeur|technique|scientifique)?(?:\s+\w+){0,4}\s+"
    r"(?:tient|reside|concerne|provient|est lie)|forte contrainte|contrainte non negociable|"
    r"exigence non negociable|difficulte majeure|impossibilite de|non maitrise|"
    r"reste a demontrer|aucune solution (?:connue|satisfaisante)|limitation structurelle|"
    # Contraintes de protection/souveraineté exprimées dans le corps d'un document.
    # Vocabulaire générique : aucune technologie, aucun client, aucun projet codé en dur.
    r"(?:confidentialite|souverainete|protection|secret)\s+(?:des?\s+)?(?:donnees|informations|code|sources?)|"
    r"(?:donnees|informations|code|sources?)\s+(?:confidentielles?|sensibles?|proprietaires?)|"
    r"(?:(?:ne|n)\s+(?:peut|peuvent|pouvons|doit|doivent)|on\s+(?:ne\s+)?peut)\s+pas\s+"
    r"(?:partager|transmettre|externaliser|envoyer|exposer)\w*\s+(?:(?:les?|ces?|ce)\s+)?(?:type\s+de\s+)?"
    r"(?:donnees|informations|code|sources?)|"
    r"(?:interdit|impossible)\s+(?:de|d)\s+(?:partager|transmettre|externaliser|envoyer|exposer)\w*\s+"
    r"(?:les?\s+|ces?\s+|ce\s+)?(?:donnees|informations|code|sources?)|"
    r"(?:donnees|informations|code|sources?).{0,90}\b(?:rester|resteront|heberge|traite|execute)\w*\b.{0,50}"
    r"\b(?:local|interne|on premise|sur site)\b|"
    r"(?:cloud|saas|service externe|fournisseur externe).{0,80}\b(?:exclu|interdit|incompatible|non autorise)\w*\b"
    r")\b",
    re.I,
)
'''

SELF_INVALIDATING_BLOCK = r'''
_SELF_INVALIDATING_LOCK_RE = re.compile(
    r"\b(?:"
    r"aucun(?:e)?\s+(?:element|preuve|information).{0,140}(?:justifier|etablir|demontrer).{0,100}"
    r"(?:verrou|incertitude|investigation|complexite)|"
    r"ne\s+(?:permet|permettent)\s+pas\s+de\s+(?:justifier|etablir|demontrer).{0,100}"
    r"(?:verrou|incertitude|investigation)|"
    r"aucun\s+besoin.{0,60}investigation\s+(?:scientifique|technique)|"
    r"absence\s+de\s+(?:preuve|donnee|element).{0,100}(?:verrou|incertitude)"
    r")\b",
    re.I,
)
'''


def patch_consultant(text: str) -> str:
    if PATCH_ID in text:
        return text
    text = add_marker(text, "consultant_verrou_synthesizer")

    # 1) récupération du verrou souveraineté/confidentialité sans hardcoding projet.
    text = sub_once(
        text,
        r'_DECLARED_LOCK_SIGNAL_RE\s*=\s*re\.compile\([\s\S]*?\n\)\n(?=_EXTERNAL_SECTION_RE)',
        DECLARED_LOCK_BLOCK,
        "consultant:declared-lock-signals",
        literal_replacement=True,
    )
    text = replace_once(
        text,
        "_EXTERNAL_SECTION_RE = re.compile(",
        SELF_INVALIDATING_BLOCK + "\n_EXTERNAL_SECTION_RE = re.compile(",
        "consultant:self-invalidating-regex",
    )

    # 2) le chemin de récupération transversal ne doit pas convertir TOUT role=limite en verrou.
    old = '''            declared = role in {
                "verrou", "verrou scientifique", "verrou a verifier", "limite",
                "incertitude", "constraint", "lock",
            } or bool(_DECLARED_LOCK_SIGNAL_RE.search(_norm(text_value)))
            if not declared or _EXTERNAL_SECTION_RE.search(section_title):
                continue
'''
    new = '''            # Les groupes NLP déjà sélectionnés sont présents dans `preferred`.
            # Le parcours transversal sert UNIQUEMENT à récupérer un verrou oublié
            # lorsqu'une contrainte/incertitude est explicitement formulée dans le texte.
            # Un simple role=limite ne crée donc plus un nouveau verrou par passage.
            declared_by_text = bool(_DECLARED_LOCK_SIGNAL_RE.search(_norm(text_value)))
            content_origin = _norm(meta.get("content_origin") or source.get("content_origin"))
            external_flag = bool(
                meta.get("is_state_of_art") or source.get("is_state_of_art")
                or meta.get("is_external_literature") or source.get("is_external_literature")
                or meta.get("literature_only") or source.get("literature_only")
                or "external_literature" in content_origin
            )
            if not declared_by_text or external_flag or _EXTERNAL_SECTION_RE.search(section_title):
                continue
'''
    text = replace_once(text, old, new, "consultant:supplemental-declared-only")

    # 3) helper qui élimine une fiche qui affirme elle-même qu'aucune incertitude n'est justifiée.
    anchor = '''def _contains_internal_marker(value: Any) -> bool:
    normalized = _norm(value)
    return any(_norm(marker) in normalized for marker in INTERNAL_MARKERS)
'''
    helper = anchor + r'''


def _analysis_self_invalidates_lock(value: Mapping[str, Any]) -> bool:
    """Rejette uniquement une fiche qui nie explicitement l'existence du verrou.

    Exemple générique : « aucun élément ne permet de justifier une incertitude ».
    On ne déduit jamais ce rejet d'un thème (embedding, IA, mécanique, etc.).
    """
    if not isinstance(value, Mapping):
        return False
    blob = _norm(" ".join(
        _clean(value.get(key))
        for key in (
            "title", "scientific_uncertainty", "why_lock",
            "why_not_simple_engineering", "evidence_summary", "justification", "text",
        )
    ))
    return bool(_SELF_INVALIDATING_LOCK_RE.search(blob))
'''
    text = replace_once(text, anchor, helper, "consultant:self-invalidating-helper")

    # 4) fast mode : 1 génération initiale par batch ; pas de cascade retry/final/title par défaut.
    counter_anchor = '''    prompt_sizes: List[int] = []

    if llm is not None:
'''
    counter_new = '''    prompt_sizes: List[int] = []
    fast_repair_mode = str(
        os.getenv("ENNOSMART_DIAG_VERROU_FAST_REPAIR", "1")
    ).strip().lower() in {"1", "true", "yes", "oui", "on"}

    if llm is not None:
'''
    text = replace_once(text, counter_anchor, counter_new, "consultant:fast-mode-init")
    text = replace_once(text, "            if invalid:\n", "            if invalid and not fast_repair_mode:\n", "consultant:skip-batch-retry")
    text = replace_once(
        text,
        "    if llm is not None and remaining_ids:\n",
        "    if llm is not None and remaining_ids and not fast_repair_mode:\n",
        "consultant:skip-final-repair",
    )
    # only the title-repair block occurrence after title_repair_ids; use a scoped regex.
    text = sub_once(
        text,
        r'(title_repair_ids: List\[str\] = \[\][\s\S]*?\n\s*)if llm is not None:\n(\s*for cluster_id in title_repair_ids:)',
        r'\1if llm is not None and not fast_repair_mode:\n\2',
        "consultant:skip-title-repair",
    )

    # LLM client retries are unnecessary when the outer pipeline already has safe completion.
    old_gen = 'retries=int(os.getenv("ENNOSMART_DIAG_VERROU_LLM_RETRIES", "1")),'
    new_gen = 'retries=int(os.getenv("ENNOSMART_DIAG_VERROU_LLM_RETRIES", "0" if str(os.getenv("ENNOSMART_DIAG_VERROU_FAST_REPAIR", "1")).strip().lower() in {"1", "true", "yes", "oui", "on"} else "1")),'
    text = replace_once(text, old_gen, new_gen, "consultant:llm-inner-retries")

    # 5) Ne pas publier la fiche auto-invalidante ; la conserver dans l'audit.
    text = replace_once(
        text,
        '''    deterministic_completion_count = 0

    for cluster in display_clusters:
''',
        '''    deterministic_completion_count = 0
    rejected_self_invalidating_locks: List[Dict[str, Any]] = []

    for cluster in display_clusters:
''',
        "consultant:rejected-list",
    )
    append_anchor = '''        if generated:
            llm_generated_count += 1
        final_items.append(_candidate_output(cluster, analysis, llm_generated=generated))
        covered_cluster_ids.append(cluster_id)
'''
    append_new = '''        if _analysis_self_invalidates_lock(analysis):
            rejected_self_invalidating_locks.append({
                "cluster_id": cluster_id,
                "title": _safe_visible((analysis or {}).get("title")),
                "reason": "candidate_explicitly_denies_scientific_or_technical_uncertainty",
            })
            covered_cluster_ids.append(cluster_id)
            continue
        if generated:
            llm_generated_count += 1
        final_items.append(_candidate_output(cluster, analysis, llm_generated=generated))
        covered_cluster_ids.append(cluster_id)
'''
    text = replace_once(text, append_anchor, append_new, "consultant:drop-self-invalidating")

    text = replace_once(
        text,
        '''        "deterministic_completion_count": deterministic_completion_count,
''',
        '''        "deterministic_completion_count": deterministic_completion_count,
        "fast_repair_mode": fast_repair_mode,
        "rejected_self_invalidating_locks_count": len(rejected_self_invalidating_locks),
        "rejected_self_invalidating_locks": rejected_self_invalidating_locks,
''',
        "consultant:report-fast-and-rejected",
    )
    return text


def patch_axis(text: str) -> str:
    if PATCH_ID in text:
        return text
    text = add_marker(text, "scientific_axis_synthesizer")
    text = sub_once(
        text,
        r'_DECLARED_LOCK_SIGNAL_RE\s*=\s*re\.compile\([\s\S]*?\n\)\n(?=_GENERIC_LOCK_SECTION_RE)',
        DECLARED_LOCK_BLOCK,
        "axis:declared-lock-signals",
        literal_replacement=True,
    )
    text = replace_once(
        text,
        "_GENERIC_LOCK_SECTION_RE = re.compile(",
        SELF_INVALIDATING_BLOCK + "\n_GENERIC_LOCK_SECTION_RE = re.compile(",
        "axis:self-invalidating-regex",
    )
    helper_anchor = '''def _is_placeholder_lock(item: Mapping[str, Any]) -> bool:
    text = _norm(" ".join([
        _clean(item.get("title")),
        _clean(item.get("scientific_lock")),
    ]))
    return bool(_PLACEHOLDER_LOCK_RE.search(text))
'''
    helper_new = helper_anchor + r'''


def _is_self_invalidating_lock(item: Mapping[str, Any]) -> bool:
    """Un candidat qui nie explicitement l'incertitude reste du contexte, pas un verrou."""
    if not isinstance(item, Mapping):
        return False
    blob = _norm(" ".join(
        _clean(item.get(key))
        for key in (
            "title", "scientific_lock", "justification", "why_lock",
            "why_not_simple_engineering", "evidence_summary", "text",
        )
    ))
    return bool(_SELF_INVALIDATING_LOCK_RE.search(blob))
'''
    text = replace_once(text, helper_anchor, helper_new, "axis:self-invalidating-helper")

    # Force context in every validation/fallback gate.
    text = replace_once(
        text,
        '''                or _is_metric_or_method_only_lock(item)
            ):
''',
        '''                or _is_metric_or_method_only_lock(item)
                or _is_self_invalidating_lock(item)
            ):
''',
        "axis:forced-context-self-invalidating",
    )
    # occurrences of eligible fallback guards (2 expected in current V4 code).
    text = text.replace(
        '''            and not _is_metric_or_method_only_lock(item)
        ):
''',
        '''            and not _is_metric_or_method_only_lock(item)
            and not _is_self_invalidating_lock(item)
        ):
''',
    )
    # human-readable context reason.
    text = replace_once(
        text,
        '''                else "KPI, paramètre ou outil expérimental conservé dans la démarche"
                if _is_metric_or_method_only_lock(item)
                else "reclassement prudent après rejet du regroupement proposé"
''',
        '''                else "KPI, paramètre ou outil expérimental conservé dans la démarche"
                if _is_metric_or_method_only_lock(item)
                else "le candidat nie explicitement l'existence d'une incertitude justifiant un verrou"
                if _is_self_invalidating_lock(item)
                else "reclassement prudent après rejet du regroupement proposé"
''',
        "axis:context-reason",
    )
    return text


def patch_presenter(text: str) -> str:
    if PATCH_ID in text:
        return text
    text = add_marker(text, "diagnostic_static_presenter")

    # 1) Anti-contamination : comparer les termes de domaine au PROJET COURANT entier,
    # pas seulement aux quelques preuves sélectionnées pour la section.
    old_sig = 'def _unsupported_domain_terms(body: Any, evidence: Sequence[Dict[str, Any]]) -> List[str]:\n'
    new_sig = 'def _unsupported_domain_terms(body: Any, evidence: Sequence[Dict[str, Any]], project_scope_text: str = "") -> List[str]:\n'
    text = replace_once(text, old_sig, new_sig, "presenter:domain-signature")
    old_source = '''    source = " " + _grounding_norm(" ".join(
        str(item.get("excerpt") or item.get("text") or "")
        for item in evidence
        if isinstance(item, dict)
    )) + " "
'''
    new_source = '''    # Une reformulation consultant peut employer un terme générique déjà présent
    # ailleurs dans le même projet (ex. « logiciel ») sans qu'il figure dans les
    # 2-8 extraits retenus pour cette section. Le contrôle reste strict au niveau
    # du corpus courant complet ; il n'autorise jamais un domaine externe.
    source = " " + _grounding_norm(
        str(project_scope_text or "") + " " + " ".join(
            str(item.get("excerpt") or item.get("text") or "")
            for item in evidence
            if isinstance(item, dict)
        )
    ) + " "
'''
    text = replace_once(text, old_source, new_source, "presenter:domain-use-project-scope")

    # locate call(s) and pass project scope. Current V191 has one guard call.
    text = text.replace(
        "_unsupported_domain_terms(body, evidence)",
        "_unsupported_domain_terms(body, evidence, project_scope_text)",
    )

    # 2) Résultats : forcer une vraie reformulation consultant, jamais une recopie de transcription.
    needle = '''        "Réexplique chaque résultat observé séparément, sous forme d'éléments numérotés compréhensibles par un consultant non spécialiste. "
'''
    repl = '''        "Réexplique chaque résultat observé séparément, sous forme d'éléments numérotés compréhensibles par un consultant non spécialiste. "
        "Ne recopie jamais mot pour mot une transcription, une phrase orale, une ligne de tableau ou un extrait source : transforme-la en 2 à 3 phrases professionnelles, "
        "en supprimant hésitations, répétitions, formulations comme « et il nous donnait », « en fait », « on a fait », tout en conservant strictement le sens factuel. "
'''
    text = replace_once(text, needle, repl, "presenter:result-consultant-rewrite")

    # 3) Le récit Frascati doit présenter d'abord défendabilité R&D puis couverture documentaire.
    # Current branch already supports it; marker/instruction makes the contract explicit.
    frascati_needle = '''        "Rédige un seul paragraphe global, continu et compréhensible par un consultant CIR. Dans ce même paragraphe, "
'''
    frascati_repl = '''        "Rédige un seul paragraphe global, continu et compréhensible par un consultant CIR. Dans ce même paragraphe, "
        "Commence par qualifier la nature des travaux : ingénierie classique, noyau R&D partiel ou noyau R&D défendable, à partir de la chaîne verrou -> hypothèse -> expérimentation -> résultat -> apprentissage. "
        "Présente ensuite DEUX indicateurs distincts : (a) le score de défendabilité R&D, qui peut descendre à 1 % pour une opération d'ingénierie classique bien documentée, "
        "et (b) la couverture documentaire des cinq critères Frascati. Ne présente jamais la couverture documentaire comme un taux d'éligibilité. "
'''
    text = replace_once(text, frascati_needle, frascati_repl, "presenter:frascati-rnd-first")

    # 4) Pydantic failure is already deterministic; keep it. Bump cache so old poor results are not reused.
    text = sub_once(
        text,
        r'_SECTION_CACHE_VERSION\s*=\s*"[^"]+"',
        '_SECTION_CACHE_VERSION = "ennodiagnostic_section_cache_v325_final_quality_speed"',
        "presenter:cache-version",
    )
    return text


def patch_writer(text: str) -> str:
    if PATCH_ID in text:
        return text
    text = add_marker(text, "structured_eligibility_writer")

    # One repair maximum, not a 3-attempt loop.
    text = text.replace("max_retries=2,", "max_retries=1,", 1)
    text = text.replace('retries={"output": 2},', 'retries={"output": 1},', 1)

    # Dynamic technical chain: do not require a claim for a stage with no eligible evidence.
    old = '''    # Chaîne minimale nécessaire à une conclusion CIR exploitable. On ne force
    # plus dix claims exacts : le modèle peut fusionner contexte/méthodes ou
    # apprentissage/conclusion sans être rejeté trois fois.
    core_kinds = {
        "verrou", "hypothese", "etapes_experimentales", "resultats",
        "frascati_acquis", "frascati_a_consolider", "conclusion",
    }
    missing_core = sorted(core_kinds - set(kinds))
    if missing_core:
        errors.append("Claims essentiels manquants : " + ", ".join(missing_core))
'''
    new = '''    # La chaîne technique exigée est dynamique : on demande un claim seulement
    # lorsqu'au moins une preuve autorisée de cette fonction existe. Sinon le LLM
    # ne doit pas être forcé à inventer une hypothèse ou un résultat pour satisfaire
    # le schéma, ce qui évite les boucles ModelRetry impossibles.
    proof_kind_to_claim = {
        "uncertainty": "verrou",
        "hypothesis": "hypothese",
        "hypothesis_component": "hypothese",
        "experiment": "etapes_experimentales",
        "systematicity": "etapes_experimentales",
        "result": "resultats",
        "quantitative_result": "resultats",
        "qualitative_result": "resultats",
    }
    available_technical_kinds: Set[str] = set()
    for evidence in ctx.deps.evidence_by_id.values():
        if str(evidence.get("evidence_id") or "") == ctx.deps.score_evidence_id:
            continue
        proof_kind = _norm_text(evidence.get("proof_kind"))
        claim_kind = proof_kind_to_claim.get(proof_kind)
        if not claim_kind:
            continue
        section_key = {
            "verrou": "verrou",
            "hypothese": "demarche_detectee",
            "etapes_experimentales": "demarche_detectee",
            "resultats": "resultats_metriques",
        }.get(claim_kind, "")
        if section_key and provenance_allows_section(evidence, section_key):
            available_technical_kinds.add(claim_kind)
    core_kinds = {
        "frascati_acquis", "frascati_a_consolider", "conclusion",
        *available_technical_kinds,
    }
    missing_core = sorted(core_kinds - set(kinds))
    if missing_core:
        errors.append("Claims essentiels fondés sur les preuves manquants : " + ", ".join(missing_core))
'''
    text = replace_once(text, old, new, "writer:dynamic-required-claims")

    # Project-current role-aware evidence is accepted just like presenter V3.1.
    old2 = '''        # V2 : « présent dans le dossier » ne signifie pas « réalisé par le projet ».
        project_execution_kinds = TECHNICAL_CLAIM_KINDS - {"verrou"}
        if claim.claim_kind in project_execution_kinds:
            if non_project_ids:
                errors.append(
                    f"{claim.claim_kind}: preuve non project_direct utilisée comme fait du projet : "
                    + ", ".join(non_project_ids)
                )
            if not project_direct_ids:
                errors.append(
                    f"{claim.claim_kind}: au moins une preuve project_direct est obligatoire."
                )
            incompatible_execution = [
                str(item.get("evidence_id"))
                for item, report in documentary_pairs
                if report.get("evidence_origin") == "project_direct"
                and not execution_allows_claim(item, claim.claim_kind)
            ]
            if incompatible_execution:
                errors.append(
                    f"{claim.claim_kind}: statut d'exécution incompatible avec le fait affirmé : "
                    + ", ".join(incompatible_execution)
                )
'''
    new2 = '''        # Un passage ambigu du corpus courant peut être utilisé seulement si le
        # backend l'a conservé avec le rôle NLP compatible et après les gardes
        # anti-littérature. On ne l'élève jamais artificiellement en project_direct.
        project_execution_kinds = TECHNICAL_CLAIM_KINDS - {"verrou"}
        if claim.claim_kind in project_execution_kinds:
            section_for_claim = {
                "contexte": "synthese_strategique",
                "hypothese": "demarche_detectee",
                "methodes_outils": "demarche_detectee",
                "etapes_experimentales": "demarche_detectee",
                "resultats": "resultats_metriques",
                "apprentissage": "resultats_metriques",
            }.get(claim.claim_kind, "")
            allowed_project_items = [
                item for item, _report in documentary_pairs
                if section_for_claim and provenance_allows_section(item, section_for_claim)
            ]
            rejected_ids = [
                str(item.get("evidence_id"))
                for item, _report in documentary_pairs
                if item not in allowed_project_items
            ]
            if rejected_ids:
                errors.append(
                    f"{claim.claim_kind}: preuve non autorisée comme fait du projet : "
                    + ", ".join(rejected_ids)
                )
            if not allowed_project_items:
                errors.append(
                    f"{claim.claim_kind}: au moins une preuve du corpus courant avec rôle compatible est obligatoire."
                )
            incompatible_execution = [
                str(item.get("evidence_id"))
                for item in allowed_project_items
                if not execution_allows_claim(item, claim.claim_kind)
            ]
            if incompatible_execution:
                errors.append(
                    f"{claim.claim_kind}: statut d'exécution incompatible avec le fait affirmé : "
                    + ", ".join(incompatible_execution)
                )
'''
    text = replace_once(text, old2, new2, "writer:role-aware-current-evidence")

    return text


def patch_agent(text: str) -> str:
    if PATCH_ID in text:
        return text
    text = add_marker(text, "ennodiagnostic_agent")

    # Enable cache already implemented by synthesizer but not wired by agent.
    old = '''                previous_cir_context=None,
            )
'''
    new = '''                previous_cir_context=None,
                cache_path=self.diagnostic_dir / "cache" / "verrou_reformulation_v191.json",
            )
'''
    # Restrict to synthesize call area by locating preceding function name.
    pos = text.find("synthesis = synthesize_consultant_verrous(")
    if pos < 0:
        raise RuntimeError("[agent:verrou-cache] appel synthesize_consultant_verrous introuvable")
    tail = text[pos:]
    if old not in tail:
        raise RuntimeError("[agent:verrou-cache] ancre de fin d'appel introuvable")
    tail = tail.replace(old, new, 1)
    text = text[:pos] + tail

    # Fast preflight for no N-1: keep the full historical path when a prior CIR exists.
    import_old = '''            from modules.CIR_MEMORY.cir_memory import (
                load_or_create_cir_memory_comparison,
                memory_v2_fingerprint,
            )
'''
    import_new = '''            from modules.CIR_MEMORY.cir_memory import (
                load_or_create_cir_memory_comparison,
                load_previous_cir_memory_items,
                memory_v2_fingerprint,
            )
'''
    text = replace_once(text, import_old, import_new, "agent:previous-loader-import")

    call_anchor = '''            report = load_or_create_cir_memory_comparison(
                organisme=self.organisme,
'''
    preflight = '''            # Préflight léger : ne pas lancer le matching N/N-1 coûteux lorsque
            # la mémoire officielle confirme qu'aucune année antérieure n'existe.
            # En cas d'erreur du préflight, on conserve l'ancien chemin complet.
            try:
                previous_years_probe, _previous_items_probe = load_previous_cir_memory_items(
                    organisme=self.organisme,
                    project=self.project,
                    current_year=self.year,
                    subproject=self.subproject,
                    max_previous_years=max_previous_years,
                )
                if not previous_years_probe:
                    report = {
                        "ok": True,
                        "has_previous_cir": False,
                        "previous_cir_available": False,
                        "previous_cir_years_used": [],
                        "previous_years": [],
                        "comparisons": [],
                        "verrou_comparisons": [],
                        "previous_cir_source": None,
                        "preflight_no_previous": True,
                        "managed_by_ennodiagnostic": True,
                        "in_prompt": False,
                        "current_verrous_count": len(normalized_current_verrous),
                        "current_verrous_hash": current_verrous_hash,
                    }
                    print(
                        "⏩ Comparaison CIR précédent ignorée : aucune année antérieure disponible (préflight).",
                        flush=True,
                    )
                    return report
            except Exception as preflight_exc:
                print(
                    f"[EnnoDiagnostic][CIR_PREVIOUS_PREFLIGHT][WARN] {preflight_exc}",
                    flush=True,
                )

            report = load_or_create_cir_memory_comparison(
                organisme=self.organisme,
'''
    text = replace_once(text, call_anchor, preflight, "agent:previous-preflight")
    return text


def patch_frascati(text: str) -> str:
    if PATCH_ID in text:
        return text
    # Current v2 already has 1% classical engineering. Only patch if absent.
    marker = 'return 0.01 if coverage > 0 else 0.0'
    if marker not in text:
        # conservative old form: classical => 0.0
        text = sub_once(
            text,
            r'if operation_status == "classical_engineering":\n\s+return 0\.0',
            'if operation_status == "classical_engineering":\n        # 1 % = plancher documentaire, jamais une probabilité CIR.\n        return 0.01 if coverage > 0 else 0.0',
            "frascati:classical-floor",
        )
    return add_marker(text, "frascati_assessment")


def apply(root: Path) -> Path:
    missing = [str(path) for path in TARGETS if not (root / path).is_file()]
    if missing:
        raise RuntimeError("Fichiers introuvables : " + ", ".join(missing))

    originals = {path: read(root / path) for path in TARGETS}
    if all(PATCH_ID in originals[path] for path in TARGETS):
        print("[OK] V4 déjà appliquée sur tous les fichiers.")
        return Path("")

    timestamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = root / ".ennosmart_patch_backups" / f"final_fix_v4_{timestamp}"
    backup.mkdir(parents=True, exist_ok=False)
    for rel, content in originals.items():
        dest = backup / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        write(dest, content)

    try:
        patched = dict(originals)
        patched[CONSULTANT] = patch_consultant(patched[CONSULTANT])
        patched[AXIS] = patch_axis(patched[AXIS])
        patched[PRESENTER] = patch_presenter(patched[PRESENTER])
        patched[AGENT] = patch_agent(patched[AGENT])
        patched[WRITER] = patch_writer(patched[WRITER])
        patched[FRASCATI] = patch_frascati(patched[FRASCATI])

        for rel, content in patched.items():
            write(root / rel, content)

        compile_files = [root / path for path in TARGETS]
        cmd = [sys.executable, "-m", "py_compile", *[str(p) for p in compile_files]]
        proc = subprocess.run(cmd, cwd=root, text=True, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError("py_compile a échoué:\n" + proc.stdout + proc.stderr)

    except Exception:
        for rel, content in originals.items():
            write(root / rel, content)
        raise

    print(f"[OK] Patch V4 appliqué. Backup : {backup}")
    print("[OK] Compilation Python réussie.")
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description="EnnoDiagnostic final fix V4")
    parser.add_argument("--repo", default=".", help="Racine du dépôt EnnoSmart")
    args = parser.parse_args()
    root = Path(args.repo).resolve()
    try:
        apply(root)
    except Exception as exc:
        print(f"\n[ECHEC V4] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
