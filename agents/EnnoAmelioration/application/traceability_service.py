from __future__ import annotations

import difflib
import math
import re
from typing import Any

from ..domain.models import ImprovementIntent, RoutingDecision


_TOKEN_RE = re.compile(r"\b[\wÀ-ÿ-]{4,}\b", flags=re.U)
_NUMBER_RE = re.compile(
    r"(?<![\w.])\d+(?:[.,]\d+)?(?:e[+-]?\d+)?(?!\w|\.\d)(?:\s*(?:%|ms|s|kg|g|mm|cm|m|°c|k|hz|db|go|mo))?(?!\w)",
    flags=re.I,
)
_STRUCTURAL_NUMBER_PREFIX_RE = re.compile(
    r"(?m)^(?P<indent>[ \t]*)(?:#{1,6}[ \t]+)?(?:\d+(?:\.\d+){1,6}\.?|\d+[.)])(?=[ \t]+\S)"
)
_STANDALONE_STRUCTURAL_NUMBER_RE = re.compile(
    r"^\s*(?:\d+(?:\.\d+){1,6}\.?|\d+[.)])\s*$"
)
_CITATION_RE = re.compile(r"\bA\d+\b", flags=re.I)
_URL_RE = re.compile(r"https?://[^\s)>]+", flags=re.I)
_ACRONYM_RE = re.compile(r"\b[A-ZÀ-Ý][A-ZÀ-Ý0-9_-]{2,}\b")
_GENERIC_WORDS = {
    "ainsi",
    "alors",
    "amélioration",
    "améliorer",
    "après",
    "aucune",
    "avec",
    "cette",
    "comme",
    "dans",
    "depuis",
    "devant",
    "donc",
    "elles",
    "entre",
    "leurs",
    "mais",
    "notamment",
    "notre",
    "obtenus",
    "permet",
    "peuvent",
    "plusieurs",
    "pour",
    "projet",
    "résultats",
    "sans",
    "selon",
    "sont",
    "système",
    "travaux",
    "toutefois",
    "uniquement",
    "utiliser",
}


def _tokens(value: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(str(value or ""))}


def _meaningful_tokens(value: str) -> set[str]:
    return _tokens(value) - _GENERIC_WORDS


def _segments(value: str) -> list[str]:
    """Découpe pour l'explication, jamais au milieu d'un mot."""

    text = str(value or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    chunks = re.split(
        r"\n{2,}|(?<=[.!?])\s+(?=(?:[A-ZÀ-Ý0-9\[]|#{1,6}\s))",
        text,
    )
    return [re.sub(r"\s+", " ", chunk).strip() for chunk in chunks if chunk.strip()]


def _normalised_segment(value: str) -> str:
    return " ".join(re.findall(r"[\wÀ-ÿ]+", value.casefold()))


def _fact_evidence(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    project_context = evidence.get("project_context")
    if isinstance(project_context, dict):
        # Ces valeurs viennent des champs officiels du projet en base, jamais de
        # la consigne libre du consultant. Elles constituent donc une provenance
        # factuelle autorisée, contrairement aux termes seulement cités dans le
        # prompt. L'identifiant technique interne du projet est volontairement
        # exclu du corpus rédactionnel.
        trusted_project_fields = {
            "project_name": project_context.get("project_name"),
            "organisme": project_context.get("organisme"),
            "year": project_context.get("year"),
            "domain": project_context.get("domain"),
        }
        project_text = "\n".join(
            f"{key}: {value}"
            for key, value in trusted_project_fields.items()
            if str(value or "").strip()
        )
        if project_text:
            rows.append(
                {
                    "evidence_id": "P:project:identity",
                    "type": "trusted_project_metadata",
                    "namespace": "project_context",
                    "title": "Identité officielle du projet",
                    "text": project_text,
                    "fact_eligible": True,
                    "_tokens": _meaningful_tokens(project_text),
                }
            )
    for namespace in ("diagnostic", "scholar"):
        payload = evidence.get(namespace)
        if not isinstance(payload, dict):
            continue
        for item in payload.get("evidence_items") or []:
            if not isinstance(item, dict) or item.get("fact_eligible") is False:
                continue
            text = str(item.get("text") or "")
            if text.strip():
                rows.append(
                    {
                        **item,
                        "namespace": namespace,
                        "_tokens": _meaningful_tokens(text),
                    }
                )
        if namespace == "scholar" and not payload.get("evidence_items"):
            for item in payload.get("evidence") or []:
                if not isinstance(item, dict):
                    continue
                text = "\n".join(
                    str(item.get(key) or "")
                    for key in ("title", "method", "results", "limits", "impact")
                )
                citation_id = str(item.get("citation_id") or "").strip()
                rows.append(
                    {
                        **item,
                        "evidence_id": citation_id or f"S:article:{item.get('article_id')}",
                        "type": "scholar_article_card",
                        "namespace": namespace,
                        "text": text,
                        "_tokens": _meaningful_tokens(text),
                    }
                )
    return rows


def _markers(value: str) -> set[str]:
    text = str(value or "")
    # Les numéros de titres/listes (ex. « 1.2.1 Contexte » ou « 1. Étape »)
    # sont des marqueurs éditoriaux, pas des faits scientifiques. Ils ne doivent
    # donc jamais déclencher seuls le garde-fou anti-hallucination.
    numeric_text = (
        ""
        if _STANDALONE_STRUCTURAL_NUMBER_RE.fullmatch(text)
        else _STRUCTURAL_NUMBER_PREFIX_RE.sub(lambda m: m.group("indent"), text)
    )
    return {
        *{item.casefold().replace(" ", "") for item in _NUMBER_RE.findall(numeric_text)},
        *{item.upper() for item in _CITATION_RE.findall(text)},
        *{item.casefold() for item in _URL_RE.findall(text)},
        *{item.upper() for item in _ACRONYM_RE.findall(text)},
    }


def _supporting_items(
    before: str,
    after: str,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Relie seulement le contenu réellement ajouté, pas les mots déjà présents."""

    novel = _meaningful_tokens(after) - _meaningful_tokens(before)
    added_markers = _markers(after) - _markers(before)
    citations = {item.upper() for item in _CITATION_RE.findall(after)}
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        row_text = str(row.get("text") or "")
        row_id = str(row.get("citation_id") or row.get("evidence_id") or "").upper()
        if row_id and row_id in citations:
            scored.append((1.0, row))
            continue
        if added_markers and added_markers & _markers(row_text):
            scored.append((0.95, row))
            continue
        if not novel:
            continue
        overlap = len(novel & set(row.get("_tokens") or set()))
        required = max(3, math.ceil(len(novel) * 0.45))
        score = overlap / max(1, len(novel))
        if overlap >= required and score >= 0.45:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in scored[:3]]


def _segment_similarity(before: str, after: str) -> float:
    """Score lexical robuste utilisé uniquement pour aligner le comparatif.

    Le score ne décide jamais qu'un fait est vrai. Il sert à éviter les faux
    appariements du type « phrase sur le radar » -> « phrase sur le Dataset
    Shift » lorsque le writer a réorganisé le texte.
    """

    left = _normalised_segment(before)
    right = _normalised_segment(after)
    sequence = difflib.SequenceMatcher(a=left, b=right).ratio()
    left_tokens = _meaningful_tokens(before)
    right_tokens = _meaningful_tokens(after)
    if not left_tokens or not right_tokens:
        containment = 0.0
        jaccard = 0.0
    else:
        overlap = len(left_tokens & right_tokens)
        containment = overlap / max(1, min(len(left_tokens), len(right_tokens)))
        jaccard = overlap / max(1, len(left_tokens | right_tokens))
    # V2.7 : une vraie reformulation peut changer fortement la syntaxe tout en
    # conservant les mêmes concepts. L'ancien poids (72 % SequenceMatcher)
    # transformait alors deux paraphrases en INSERT + DELETE. On donne plus de
    # poids au recouvrement des mots informatifs, sans utiliser ce score comme
    # preuve factuelle : il sert uniquement à l'alignement du diff.
    return (0.35 * sequence) + (0.40 * containment) + (0.25 * jaccard)


def _replacement_pairs(
    before_rows: list[str],
    after_rows: list[str],
    *,
    allow_local_grouping: bool = True,
) -> list[tuple[str, str, str]]:
    """Aligne le comparatif en autorisant les fusions/scissions locales.

    En mode éditorial, une phrase source peut légitimement être scindée en deux
    phrases plus lisibles, et deux phrases adjacentes peuvent être fusionnées.
    Un diff strictement 1→1 transformait ces cas en faux INSERT/DELETE.

    L'alignement reste monotone et local : au maximum deux segments consécutifs
    de chaque côté peuvent être regroupés. Il ne sert qu'à présenter le diff,
    jamais à décider de la véracité d'un fait.
    """

    n, m = len(before_rows), len(after_rows)
    if not n:
        return [("insert", "", item) for item in after_rows]
    if not m:
        return [("delete", item, "") for item in before_rows]

    neg_inf = float("-inf")
    dp = [[neg_inf] * (m + 1) for _ in range(n + 1)]
    prev: list[list[tuple[int, int, str, int, int] | None]] = [
        [None] * (m + 1) for _ in range(n + 1)
    ]
    dp[0][0] = 0.0
    gap_penalty = 0.11
    group_bonus = 0.08
    match_threshold = 0.24

    def joined(rows: list[str], start: int, count: int) -> str:
        return " ".join(rows[start : start + count]).strip()

    for i in range(n + 1):
        for j in range(m + 1):
            base = dp[i][j]
            if base == neg_inf:
                continue

            if i < n:
                score = base - gap_penalty
                if score > dp[i + 1][j]:
                    dp[i + 1][j] = score
                    prev[i + 1][j] = (i, j, "delete", 1, 0)
            if j < m:
                score = base - gap_penalty
                if score > dp[i][j + 1]:
                    dp[i][j + 1] = score
                    prev[i][j + 1] = (i, j, "insert", 0, 1)

            span_sizes = (1, 2) if allow_local_grouping else (1,)
            for a_count in span_sizes:
                if i + a_count > n:
                    continue
                before = joined(before_rows, i, a_count)
                for b_count in span_sizes:
                    if j + b_count > m:
                        continue
                    after = joined(after_rows, j, b_count)
                    similarity = _segment_similarity(before, after)
                    if similarity < match_threshold:
                        continue
                    score = (
                        base
                        + similarity
                        + group_bonus * ((a_count - 1) + (b_count - 1))
                    )
                    ni, nj = i + a_count, j + b_count
                    if score > dp[ni][nj]:
                        dp[ni][nj] = score
                        prev[ni][nj] = (i, j, "replace", a_count, b_count)

    operations: list[tuple[str, str, str]] = []
    i, j = n, m
    while i > 0 or j > 0:
        step = prev[i][j]
        if step is None:
            # Garde-fou défensif : ne devrait pas arriver grâce aux transitions gap.
            if i > 0:
                operations.append(("delete", before_rows[i - 1], ""))
                i -= 1
            elif j > 0:
                operations.append(("insert", "", after_rows[j - 1]))
                j -= 1
            continue
        pi, pj, action, a_count, b_count = step
        if action == "replace":
            before = joined(before_rows, pi, a_count)
            after = joined(after_rows, pj, b_count)
            if _normalised_segment(before) != _normalised_segment(after):
                operations.append(("replace", before, after))
        elif action == "delete":
            operations.append(("delete", before_rows[pi], ""))
        else:
            operations.append(("insert", "", after_rows[pj]))
        i, j = pi, pj

    return _coalesce_adjacent_gap_pairs(list(reversed(operations)))

def _gap_pair_is_rewrite(before: str, after: str) -> bool:
    """Reconnaît un INSERT+DELETE adjacent comme une reformulation déplacée.

    Ce garde-fou reste volontairement conservateur : il exige soit une
    similarité textuelle minimale, soit des marqueurs techniques communs et un
    recouvrement lexical. Il ne sert qu'à présenter correctement le diff.
    """

    if not before.strip() or not after.strip():
        return False
    score = _segment_similarity(before, after)
    if score >= 0.24:
        return True
    shared_markers = _markers(before) & _markers(after)
    shared_tokens = _meaningful_tokens(before) & _meaningful_tokens(after)
    return bool(len(shared_markers) >= 1 and len(shared_tokens) >= 2)


def _coalesce_adjacent_gap_pairs(
    operations: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    output: list[tuple[str, str, str]] = []
    index = 0
    while index < len(operations):
        current = operations[index]
        if index + 1 < len(operations):
            following = operations[index + 1]
            if current[0] == "insert" and following[0] == "delete":
                before, after = following[1], current[2]
                if _gap_pair_is_rewrite(before, after):
                    output.append(("replace", before, after))
                    index += 2
                    continue
            if current[0] == "delete" and following[0] == "insert":
                before, after = current[1], following[2]
                if _gap_pair_is_rewrite(before, after):
                    output.append(("replace", before, after))
                    index += 2
                    continue
        output.append(current)
        index += 1
    return output


def _aligned_changes(
    original: str,
    improved: str,
    *,
    allow_local_grouping: bool = False,
) -> list[tuple[str, str, str]]:
    before_rows = _segments(original)
    after_rows = _segments(improved)
    if allow_local_grouping:
        # V2.9 : pour une révision explicitement à faits constants, une fusion
        # ou une scission locale est une opération éditoriale normale. On aligne
        # donc globalement les segments en autorisant 1↔2.
        return _replacement_pairs(
            before_rows, after_rows, allow_local_grouping=True
        )

    # Pour les modes d'enrichissement, on conserve un diff plus strict afin
    # qu'un véritable ajout scientifique reste visible comme INSERT et ne soit
    # pas absorbé dans un remplacement voisin.
    matcher = difflib.SequenceMatcher(
        a=[_normalised_segment(item) for item in before_rows],
        b=[_normalised_segment(item) for item in after_rows],
    )
    output: list[tuple[str, str, str]] = []
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            output.extend(
                _replacement_pairs(
                    before_rows[a0:a1],
                    after_rows[b0:b1],
                    allow_local_grouping=False,
                )
            )
        elif tag == "delete":
            output.extend(("delete", item, "") for item in before_rows[a0:a1])
        else:
            output.extend(("insert", "", item) for item in after_rows[b0:b1])
    return output

def build_revision_trace(
    original: str,
    improved: str,
    routing: RoutingDecision,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Produit des modifications lisibles et une provenance conservatrice."""

    source = str(original or "")
    proposal = str(improved or "")
    rows = _fact_evidence(evidence)
    factual_corpus = "\n".join(
        " ".join(
            str(value or "")
            for value in (row.get("evidence_id"), row.get("citation_id"), row.get("text"))
        )
        for row in rows
    )
    allowed_markers = _markers(source + "\n" + factual_corpus)
    style_context = evidence.get("cir_style") or {}
    selected_style_pattern_ids = list(style_context.get("selected_pattern_ids") or [])
    style_available = bool(
        style_context.get("available")
        and style_context.get("guidance_injected")
        and selected_style_pattern_ids
    )
    changes: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    used: dict[str, dict[str, Any]] = {}
    concision_requested = ImprovementIntent.CONCISION in routing.intents

    for index, (operation, before, after) in enumerate(
        _aligned_changes(
            source,
            proposal,
            allow_local_grouping=bool(
                routing.editorial_only or routing.strict_fact_preservation
            ),
        ),
        start=1,
    ):
        support = _supporting_items(before, after, rows)
        evidence_refs: list[str] = []
        namespaces: set[str] = set()
        for row in support:
            evidence_id = str(row.get("evidence_id") or row.get("citation_id") or "")
            if not evidence_id:
                continue
            evidence_refs.append(evidence_id)
            namespaces.add(str(row.get("namespace") or ""))
            used[evidence_id] = {
                "evidence_id": evidence_id,
                "type": row.get("type"),
                "namespace": row.get("namespace"),
                "title": row.get("title"),
                "authors": row.get("authors"),
                "year": row.get("year"),
                "doi": row.get("doi"),
                "url": row.get("url") or row.get("source_url"),
            }

        added_markers = sorted(_markers(after) - _markers(before) - allowed_markers)
        if added_markers:
            unsupported.append(
                {
                    "change_id": f"change-{index}",
                    "claim": after[:1600],
                    "reason": "Un chiffre, identifiant, acronyme ou lien nouveau n'est présent ni dans l'original ni dans les preuves autorisées.",
                    "markers": added_markers,
                    "severity": "warning",
                }
            )

        similarity = difflib.SequenceMatcher(
            a=_normalised_segment(before), b=_normalised_segment(after)
        ).ratio()
        novel = _meaningful_tokens(after) - _meaningful_tokens(before)
        evidence_required = bool(
            routing.needs_diagnostic
            or routing.needs_scholar
            or routing.strict_fact_preservation
        )
        substantial_addition = bool(
            operation == "insert"
            or (len(after.split()) > max(10, int(len(before.split()) * 1.25)))
        )
        needs_review = bool(
            evidence_required
            and operation != "delete"
            and substantial_addition
            and len(novel) >= 5
            and similarity < 0.72
            and not evidence_refs
            and not added_markers
        )
        if operation == "delete" and not concision_requested and len(_meaningful_tokens(before)) >= 6:
            needs_review = True
        if needs_review:
            unsupported.append(
                {
                    "change_id": f"change-{index}",
                    "claim": (after or before)[:1600],
                    "reason": (
                        "La suppression n'était pas demandée et doit être contrôlée."
                        if operation == "delete"
                        else (
                            "La demande est à faits constants et cet ajout ne peut pas être relié automatiquement au texte source."
                            if routing.strict_fact_preservation
                            else "Le renforcement ne peut pas être relié automatiquement à une preuve précise du projet."
                        )
                    ),
                    "markers": [],
                    "severity": "review",
                }
            )

        if "scholar" in namespaces:
            reason = "Ajout relié à une référence scientifique validée par le consultant."
        elif "diagnostic" in namespaces:
            reason = "Renforcement relié à une preuve précise du projet."
        elif "project_context" in namespaces:
            reason = "Ajout relié à une métadonnée officielle du projet."
        elif operation == "delete":
            reason = (
                "Allègement conforme à la demande de concision."
                if concision_requested
                else "Suppression détectée : validation consultant nécessaire avant acceptation."
            )
        elif needs_review:
            reason = (
                "Modification à confirmer : la demande est à faits constants et le contenu n'est pas relié automatiquement au texte source."
                if routing.strict_fact_preservation
                else "Renforcement proposé, mais preuve documentaire précise à confirmer."
            )
        else:
            reason = "Reformulation rédactionnelle du contenu existant, sans nouveau fait identifié."
        changes.append(
            {
                "change_id": f"change-{index}",
                "operation": operation,
                "before": before[:5000],
                "after": after[:5000],
                "reason": reason,
                "evidence_refs": evidence_refs,
                "style_refs": selected_style_pattern_ids
                if style_available and operation != "delete"
                else [],
            }
        )

    agents = ["EnnoAmelioration"]
    if routing.needs_diagnostic and (evidence.get("diagnostic") or {}).get("available"):
        agents.append("EnnoDiagnostic")
    if (
        routing.needs_scholar
        and not routing.forbids_scholar
        and (evidence.get("scholar") or {}).get("available")
    ):
        agents.append("EnnoScholar")
    if style_available:
        agents.append("CIRStyleMemory")

    questions: list[str] = []
    if routing.needs_diagnostic and not (evidence.get("diagnostic") or {}).get("available"):
        questions.append(
            "Les preuves R&D structurées du projet sont indisponibles : quels éléments documentaires le consultant peut-il confirmer ?"
        )
    if routing.needs_scholar and not (evidence.get("scholar") or {}).get("available"):
        explicit_scientific_enrichment = any(
            intent in routing.intents
            for intent in (
                ImprovementIntent.SCIENTIFIC_ENRICHMENT,
                ImprovementIntent.RESEARCH,
            )
        )
        if explicit_scientific_enrichment and not (
            routing.forbids_new_research or routing.forbids_scholar
        ):
            questions.append(
                "Aucune source scientifique validée n'est disponible : faut-il lancer puis valider une recherche ciblée ?"
            )
    if any(item.get("severity") == "review" for item in unsupported):
        questions.append(
            "Les ajouts ou suppressions non reliés automatiquement à une preuve doivent être confirmés par le consultant."
        )

    return {
        "changes": changes,
        "sources_used": list(used.values()),
        "agents_used": agents,
        "unsupported_claims": unsupported,
        "questions_for_consultant": questions,
        "diagnostic_used": bool(
            routing.needs_diagnostic and (evidence.get("diagnostic") or {}).get("available")
        ),
        "scholar_used": bool(
            routing.needs_scholar
            and not routing.forbids_scholar
            and (evidence.get("scholar") or {}).get("available")
        ),
        "cir_memory_used": style_available,
        "cir_style_pattern_ids": selected_style_pattern_ids if style_available else [],
        # Human-in-the-loop: les contrôles de traçabilité n'écartent jamais une
        # proposition. Ils signalent les points à vérifier au consultant, qui
        # reste seul décisionnaire de la validation ou d'une nouvelle correction.
        "blocking": False,
        "has_warnings": bool(unsupported),
    }
