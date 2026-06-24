# -*- coding: utf-8 -*-
from __future__ import annotations

"""
EnnoDiagnostic Agent - V96 SIGNALS + justification Frascati dédiée

Architecture respectée :
- Le score Frascati est calculé en amont par le NLP / Frascati et stocké dans les métadonnées Chroma.
- Cet agent ne recalcule jamais le score Frascati.
- Le LLM sert uniquement à reformuler le diagnostic et à justifier le score à partir des preuves du projet.
- La mémoire CIR stylistique est utilisée uniquement comme style rédactionnel, jamais comme preuve factuelle.
- La comparaison CIR précédent est séparée d'EnnoDiagnostic : elle est lancée depuis l'onglet CIR précédent et ne bloque pas le diagnostic principal.
"""

import hashlib
import json
import os
import re
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# =========================================================
# Constantes vocabulaire métier
# =========================================================

# Dans EnnoDiagnostic, on ne valide pas encore des verrous CIR.
# On détecte des signaux / candidats, qui seront ensuite filtrés par le consultant
# puis confrontés à l’état de l’art dans EnnoScholar.
SIGNAL_SECTION_TITLE = "Signaux de verrous R&D candidats"
LEGACY_SIGNAL_SECTION_TITLE = "Verrous R&D / signaux de verrous"


# =========================================================
# Utils
# =========================================================

def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_file(path: Path) -> str:
    """Calcule une empreinte SHA-256 stable pour un fichier."""
    try:
        if not path.exists() or not path.is_file():
            return ""
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def sha256_text(value: str) -> str:
    """Calcule une empreinte SHA-256 stable pour une chaîne."""
    return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()


def safe_read_json(path: Path) -> Dict[str, Any]:
    """Lit un JSON sans bloquer le diagnostic si le fichier est absent ou invalide."""
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def clean_text(text: Any) -> str:
    return str(text or "").strip()


def repair_mojibake(text: Any) -> str:
    s = clean_text(text)
    if not s:
        return ""

    markers = ("Ã", "Â", "â€™", "â€“", "â€œ", "â€")
    if not any(m in s for m in markers):
        return s

    replacements = {
        "Ã©": "é",
        "Ã¨": "è",
        "Ãª": "ê",
        "Ã«": "ë",
        "Ã ": "à",
        "Ã¢": "â",
        "Ã§": "ç",
        "Ã´": "ô",
        "Ã¹": "ù",
        "Ã»": "û",
        "Ã®": "î",
        "Ã¯": "ï",
        "Ã‰": "É",
        "â€™": "’",
        "â€œ": "“",
        "â€": "”",
        "â€“": "–",
        "â€”": "—",
    }
    fixed = s
    for bad, good in replacements.items():
        fixed = fixed.replace(bad, good)

    try:
        latin_fixed = s.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
        if sum(latin_fixed.count(m) for m in markers) < sum(fixed.count(m) for m in markers):
            fixed = latin_fixed
    except Exception:
        pass

    return fixed


def truncate(text: Any, max_chars: int = 700) -> str:
    text = clean_text(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def extract_markdown_section(content: str, title: str) -> str:
    content = repair_mojibake(content)
    if not content:
        return ""

    pattern = re.compile(
        r"^##\s+" + re.escape(title).replace(r"\ ", r"\s+") + r"\s*$([\s\S]*?)(?=^##\s+|\Z)",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(content)
    return match.group(1).strip() if match else ""


def insert_markdown_section_after(content: str, after_title: str, new_section: str) -> str:
    content = repair_mojibake(content)
    new_section = repair_mojibake(new_section).strip()
    if not new_section:
        return content

    pattern = re.compile(
        r"(^##\s+" + re.escape(after_title).replace(r"\ ", r"\s+") + r"\s*$[\s\S]*?)(?=^##\s+|\Z)",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(content)

    if not match:
        return f"{new_section}\n\n{content}".strip()

    insert_at = match.end(1)
    return (content[:insert_at].rstrip() + "\n\n" + new_section + "\n\n" + content[insert_at:].lstrip()).strip()


def replace_or_insert_markdown_section(
    content: str,
    title: str,
    new_section: str,
    after_title: str = "Lecture Frascati du dossier",
) -> str:
    content = repair_mojibake(content)
    new_section = repair_mojibake(new_section).strip()

    if not new_section:
        return content

    if not re.match(r"^##\s+", new_section):
        new_section = f"## {title}\n{new_section}"

    pattern = re.compile(
        r"(^##\s+" + re.escape(title).replace(r"\ ", r"\s+") + r"\s*$[\s\S]*?)(?=^##\s+|\Z)",
        re.IGNORECASE | re.MULTILINE,
    )

    if pattern.search(content):
        return pattern.sub(new_section.rstrip() + "\n\n", content, count=1).strip()

    return insert_markdown_section_after(content, after_title=after_title, new_section=new_section)


def build_diagnostic_sections(content: str) -> Dict[str, str]:
    titles = [
        "Lecture Frascati du dossier",
        "Justification Frascati du score",
        "Synthèse stratégique du projet",
        "Objectif global reformulé",
        SIGNAL_SECTION_TITLE,
        LEGACY_SIGNAL_SECTION_TITLE,
        "Démarche expérimentale détectée",
        "Résultats et métriques disponibles",
        "Paramètres et contraintes techniques",
        "Points à valider par le consultant",
    ]

    sections = {title: extract_markdown_section(content, title) for title in titles}

    # Compatibilité frontend : l’ancien frontend peut encore chercher
    # "Verrous R&D / signaux de verrous". On lui donne le même contenu,
    # mais le titre métier canonique reste "Signaux de verrous R&D candidats".
    signal_text = sections.get(SIGNAL_SECTION_TITLE) or sections.get(LEGACY_SIGNAL_SECTION_TITLE) or ""
    if signal_text:
        sections[SIGNAL_SECTION_TITLE] = signal_text
        sections[LEGACY_SIGNAL_SECTION_TITLE] = signal_text

    return sections


def normalize_report_vocabulary(content: str) -> str:
    """
    Corrige le vocabulaire du rapport pour éviter de présenter comme validés
    des éléments qui ne sont encore que des signaux candidats avant EnnoScholar.
    """
    content = repair_mojibake(content)
    if not content:
        return ""

    replacements = {
        "## Verrous CIR consolidés": f"## {SIGNAL_SECTION_TITLE}",
        "Verrous CIR consolidés": "Signaux de verrous R&D candidats",
        "Verrou identifié": "Signal candidat détecté",
        "Verrous identifiés": "Signaux candidats détectés",
        "Nature du verrou": "Hypothèse de verrou",
        "verrou CIR validé": "signal candidat à confirmer",
        "verrous CIR validés": "signaux candidats à confirmer",
        "verrou scientifiquement défendable": "signal à confirmer par EnnoScholar",
    }

    for old, new in replacements.items():
        content = content.replace(old, new)

    return content


def meta_of(src: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(src, dict):
        return {}
    meta = src.get("metadata")
    return meta if isinstance(meta, dict) else {}


def source_text(src: Dict[str, Any]) -> str:
    if not isinstance(src, dict):
        return ""
    return clean_text(
        src.get("text")
        or src.get("source_text")
        or src.get("content")
        or src.get("excerpt")
        or ""
    )


def source_doc(src: Dict[str, Any]) -> str:
    if not isinstance(src, dict):
        return ""
    meta = meta_of(src)
    return clean_text(
        meta.get("document")
        or meta.get("filename")
        or meta.get("source_name")
        or src.get("document")
        or src.get("filename")
        or src.get("source_name")
        or ""
    )


def source_path(src: Dict[str, Any]) -> str:
    if not isinstance(src, dict):
        return ""
    meta = meta_of(src)
    return clean_text(
        meta.get("source_path")
        or meta.get("path")
        or src.get("source_path")
        or src.get("path")
        or ""
    )


def dedupe_sources(sources: List[Dict[str, Any]], max_items: int = 30) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []

    for src in sources or []:
        if not isinstance(src, dict):
            continue

        txt = source_text(src)
        if not txt:
            continue

        meta = meta_of(src)
        key = (source_doc(src), clean_text(meta.get("role")), txt[:250])
        if key in seen:
            continue

        seen.add(key)
        out.append(src)

        if len(out) >= max_items:
            break

    return out


def build_sources_block(title: str, sources: List[Dict[str, Any]], max_items: int = 10, max_text_chars: int = 520) -> str:
    lines = [f"## {title}"]

    if not sources:
        lines.append("- Aucun élément récupéré depuis Chroma.")
        return "\n".join(lines)

    for i, src in enumerate(sources[:max_items], start=1):
        meta = meta_of(src)
        role = clean_text(meta.get("role"))
        doc = source_doc(src)
        decision = clean_text(meta.get("frascati_decision") or meta.get("decision"))
        fr_score = meta.get("frascati_score", "")
        page = meta.get("page") or meta.get("page_number") or src.get("page")
        txt = truncate(source_text(src), max_text_chars)

        lines.append(
            f"- Source {i} | rôle={role or '-'} | document={doc or '-'} | "
            f"page={page if page not in (None, '') else '-'} | "
            f"frascati={decision or '-'} | score={fr_score if fr_score != '' else '-'}\n"
            f"  Texte : {txt}"
        )

    return "\n".join(lines)


def build_sources_block_compact(
    title: str,
    sources: List[Dict[str, Any]],
    max_items: int = 5,
    max_text_chars: int = 260,
) -> str:
    lines = [f"## {title}"]

    if not sources:
        lines.append("- Aucun élément récupéré depuis Chroma.")
        return "\n".join(lines)

    for i, src in enumerate(sources[:max_items], start=1):
        meta = meta_of(src)
        role = clean_text(meta.get("role"))
        doc = source_doc(src)
        decision = clean_text(meta.get("frascati_decision") or meta.get("decision"))
        fr_score = meta.get("frascati_score", "")
        txt = truncate(source_text(src), max_text_chars)

        lines.append(
            f"- Source {i} | rôle={role or '-'} | document={doc or '-'} | "
            f"frascati={decision or '-'} | score={fr_score if fr_score != '' else '-'} | "
            f"texte={txt}"
        )

    return "\n".join(lines)


def build_llm(model=None):
    try:
        from modules.LLM.llm_client import LLMClient
        return LLMClient(model=model)
    except TypeError:
        from modules.LLM.llm_client import LLMClient
        return LLMClient()
    except Exception as e:
        raise RuntimeError(f"Impossible de charger modules.LLM.llm_client.LLMClient : {e}")


# =========================================================
# Agent EnnoDiagnostic
# =========================================================

class EnnoDiagnosticAgent:
    def __init__(
        self,
        organisme: Optional[str] = None,
        project: Optional[str] = None,
        year: Optional[str | int] = None,
        organisme_id: Optional[str] = None,
        project_id: Optional[str] = None,
        year_id: Optional[str | int] = None,
        out_dir: Optional[str] = None,
        model: Optional[str] = None,
        gemini_model: Optional[str] = None,
        use_llm: bool = True,
        use_style_memory: bool = True,
        **kwargs,
    ):
        self.organisme = organisme_id or organisme or "unknown_organisme"
        self.project = project_id or project or "unknown_project"
        self.year = str(year_id or year or "2023")
        self.model = model or gemini_model
        self.use_llm = use_llm
        self.use_style_memory = use_style_memory

        self.out_dir = Path(out_dir) if out_dir else (
            Path("outputs") / "safe_rag_upload" / self.organisme / self.project / self.year
        )
        self.diagnostic_dir = self.out_dir / "ennodiagnostic"
        self.report_path = self.diagnostic_dir / "ennodiagnostic_report.json"

        from modules.RAG.retriever import EnnoRetriever

        self.retriever = EnnoRetriever(
            organisme=self.organisme,
            project=self.project,
            year=self.year,
        )

        self.llm = build_llm(self.model) if self.use_llm else None

    # =====================================================
    # Chroma retrieval
    # =====================================================

    def search_chroma(self, role: Optional[str], query: str, top_k: int = 12) -> List[Dict[str, Any]]:
        if role:
            sources = self.retriever.search(question=query, role_filter=role, top_k=top_k)
        else:
            sources = self.retriever.search(question=query, role_filter=None, top_k=top_k)
        return dedupe_sources(sources, max_items=top_k)

    def retrieve_all_sections(self) -> Dict[str, List[Dict[str, Any]]]:
        sections: Dict[str, List[Dict[str, Any]]] = {}

        sections["global"] = self.search_chroma(
            role=None,
            query="résumé global contexte technique objectif difficultés travaux résultats limites innovation",
            top_k=14,
        )

        sections["objectifs"] = self.search_chroma(
            role="objectif",
            query="objectifs locaux objectif global finalité technique besoin performances attendues",
            top_k=12,
        )

        sections["verrous"] = self.search_chroma(
            role="verrou",
            query="verrous R&D incertitudes techniques difficultés scientifiques blocages limites hypothèses à valider phénomènes non maîtrisés",
            top_k=18,
        )

        sections["methodes"] = self.search_chroma(
            role="methode",
            query="démarche expérimentale méthodes essais prototypes simulations protocole travaux réalisés validation technique",
            top_k=14,
        )

        sections["resultats"] = self.search_chroma(
            role="resultat",
            query="résultats métriques mesures performances essais valeurs chiffrées observations conclusions résultats qualitatifs",
            top_k=14,
        )

        sections["parametres"] = self.search_chroma(
            role="parametre",
            query="paramètres techniques contraintes valeurs seuils dimensions pression débit température configuration conditions",
            top_k=10,
        )

        sections["limites"] = self.search_chroma(
            role="limite",
            query="limites contraintes problèmes points bloquants données manquantes risques à vérifier incertitudes",
            top_k=10,
        )

        # Axes complémentaires génériques : ils servent à récupérer des preuves
        # sans coder un projet précis ni un domaine précis.
        sections["axe_problemes_transverses"] = self.search_chroma(
            role=None,
            query="problème difficulté limite incertitude non maîtrisé instabilité anomalie défaut non conforme robustesse fiabilité performance qualité",
            top_k=10,
        )

        sections["axe_contraintes_transverses"] = self.search_chroma(
            role=None,
            query="contraintes exigences conditions paramètres seuils configuration contexte environnement ressources compatibilité objectif attendu",
            top_k=10,
        )

        sections["axe_preuves_resultats"] = self.search_chroma(
            role=None,
            query="preuves mesures tests essais résultats observations métriques comparaison validation limites conclusions valeurs courbes tableaux",
            top_k=10,
        )

        return sections

    # =====================================================
    # Frascati
    # =====================================================

    def frascati_summary_from_chroma(self, verrou_sources: List[Dict[str, Any]]) -> Dict[str, Any]:
        scores: List[float] = []
        decisions: Dict[str, int] = {}
        levels: Dict[str, int] = {}

        for src in verrou_sources or []:
            meta = meta_of(src)

            try:
                score_float = float(meta.get("frascati_score"))
                if score_float > 0:
                    scores.append(score_float)
            except Exception:
                pass

            decision = clean_text(meta.get("frascati_decision") or meta.get("decision") or "unknown")
            decisions[decision] = decisions.get(decision, 0) + 1

            level = clean_text(meta.get("verrou_candidate_level") or "unknown")
            levels[level] = levels.get(level, 0) + 1

        avg_score = round(statistics.mean(scores), 3) if scores else None

        return {
            "average_frascati_score": avg_score,
            "scores_count": len(scores),
            "decisions_count": decisions,
            "candidate_levels_count": levels,
            "explanation": (
                "Le score Frascati est calculé à partir des métadonnées des chunks de rôle verrou "
                "récupérés depuis Chroma. Il reprend le contrôle NLP/Frascati déjà exécuté."
            ),
        }

    def _compact_frascati_block(self, frascati_summary: Dict[str, Any]) -> str:
        return json.dumps(
            {
                "average_frascati_score": frascati_summary.get("average_frascati_score"),
                "scores_count": frascati_summary.get("scores_count"),
                "decisions_count": frascati_summary.get("decisions_count"),
                "candidate_levels_count": frascati_summary.get("candidate_levels_count"),
            },
            ensure_ascii=False,
            indent=2,
        )

    # =====================================================
    # Style memory
    # =====================================================

    def _sources_query_text(self, sources: List[Dict[str, Any]], max_sources: int = 8) -> str:
        parts: List[str] = []
        for src in sources[:max_sources]:
            doc = source_doc(src)
            txt = source_text(src)
            if txt:
                parts.append(f"document={doc}\n{txt}")
        return "\n\n".join(parts)

    def load_style_memory_context(self, sections: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        if not self.use_style_memory:
            return {
                "ok": False,
                "disabled": True,
                "message": "Mémoire rédactionnelle CIR désactivée.",
                "style_block": "Mémoire rédactionnelle CIR désactivée.",
                "stats": {},
                "examples_by_role": {},
            }

        try:
            from modules.CIR_STYLE_MEMORY.style_memory import (
                load_style_memory,
                retrieve_style_examples,
                build_style_block,
                style_memory_path,
            )

            memory = load_style_memory(self.organisme)
            stats = memory.get("stats") or {}
            memory_path = str(style_memory_path(self.organisme))

            role_sources = {
                "objectif": sections.get("objectifs", []),
                "verrou": sections.get("verrous", []) + sections.get("limites", []),
                "methode": sections.get("methodes", []),
                "resultat": sections.get("resultats", []),
                "parametre": sections.get("parametres", []),
            }

            examples_by_role: Dict[str, List[Dict[str, Any]]] = {}
            all_examples: List[Dict[str, Any]] = []

            for role, sources in role_sources.items():
                query = self._sources_query_text(sources, max_sources=8)
                examples = retrieve_style_examples(
                    organisme=self.organisme,
                    target_role=role,
                    query_text=query,
                    project=self.project,
                    top_k=3,
                    strict_domain=True,
                )
                examples_by_role[role] = examples
                all_examples.extend(examples)

            seen = set()
            unique_examples = []
            for ex in all_examples:
                ex_id = ex.get("example_id") or f"{ex.get('role')}|{ex.get('text','')[:80]}"
                if ex_id in seen:
                    continue
                seen.add(ex_id)
                unique_examples.append(ex)

            style_block = build_style_block(unique_examples[:8], max_chars_per_example=450)

            return {
                "ok": True,
                "memory_path": memory_path,
                "stats": stats,
                "examples_count": len(unique_examples),
                "examples_by_role_count": {k: len(v) for k, v in examples_by_role.items()},
                "examples_by_role": examples_by_role,
                "style_block": style_block,
                "principle": (
                    "Les exemples CIR sont utilisés uniquement pour la rédaction. "
                    "Les faits doivent provenir uniquement des sources Chroma du dossier courant."
                ),
            }

        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "message": "Mémoire rédactionnelle CIR indisponible.",
                "stats": {},
                "examples_by_role": {},
                "style_block": "Aucun exemple de style CIR disponible.",
            }

    def _style_memory_for_role(
        self,
        style_memory_report: Optional[Dict[str, Any]],
        role: str,
        max_chars: int = 1600,
    ) -> str:
        if not isinstance(style_memory_report, dict) or not style_memory_report.get("ok"):
            return "Aucune mémoire de style exploitable. Appliquer un style CIR clair, technique, prudent et vérifiable."

        examples_by_role = style_memory_report.get("examples_by_role") or {}
        role_examples = examples_by_role.get(role) or []

        if role_examples:
            lines = [
                "Exemples de style CIR pour ce rôle uniquement.",
                "Règle : ne jamais copier un fait ni une phrase entière ; utiliser seulement le style.",
                "",
            ]
            for i, ex in enumerate(role_examples[:3], start=1):
                ex_text = clean_text(ex.get("text") or ex.get("content") or "")
                ex_role = clean_text(ex.get("role") or role)
                if ex_text:
                    lines.append(f"- Exemple {i} | rôle={ex_role} : {truncate(ex_text, 420)}")
            return truncate("\n".join(lines), max_chars)

        return truncate(clean_text(style_memory_report.get("style_block")), max_chars)


    # =====================================================
    # Verrous reformulés par le LLM pour validation consultant
    # =====================================================

    def _token_set_for_matching(self, text: Any) -> set[str]:
        text = repair_mojibake(text)
        text = text.lower()
        text = re.sub(r"[^a-z0-9àâäéèêëîïôöùûüç]+", " ", text)
        stop = {
            "les", "des", "une", "dans", "pour", "avec", "sans", "sur", "par", "aux", "est", "sont", "que", "qui",
            "verrou", "verrous", "signal", "signaux", "technique", "techniques", "candidat", "candidats", "source", "sources",
            "document", "documents", "frascati", "validation", "consultant", "projet", "dossier", "preuve", "preuves",
        }
        return {w for w in re.findall(r"[a-z0-9àâäéèêëîïôöùûüç]{3,}", text) if w not in stop}

    def _similarity_for_llm_candidate(self, candidate_text: str, src: Dict[str, Any]) -> float:
        a = self._token_set_for_matching(candidate_text)
        meta = meta_of(src)
        source_blob = "\n".join([
            source_text(src),
            str(meta.get("theme_label") or ""),
            str(meta.get("theme_id") or ""),
            str(meta.get("final_role") or ""),
            str(meta.get("technical_signature") or ""),
        ])
        b = self._token_set_for_matching(source_blob)
        if not a or not b:
            return 0.0
        inter = len(a & b)
        return inter / max(1, min(len(a), len(b)))

    def _extract_signal_blocks_from_markdown(self, content: str) -> List[Dict[str, str]]:
        section = extract_markdown_section(content, SIGNAL_SECTION_TITLE) or extract_markdown_section(content, LEGACY_SIGNAL_SECTION_TITLE)
        section = repair_mojibake(section)
        if not section:
            return []

        lines = [ln.rstrip() for ln in section.splitlines()]
        blocks: List[List[str]] = []
        current: List[str] = []

        def starts_candidate(line: str) -> bool:
            s = line.strip()
            if not s:
                return False
            if re.match(r"^#{3,}\s+", s):
                return True
            if re.match(r"^(?:[-*]|\d+[\.)])\s+\*\*[^*]{15,}\*\*", s):
                return True
            if re.match(r"^(?:[-*]|\d+[\.)])\s+(?:Signal|Hypothèse|Verrou|Axe)\b", s, flags=re.I):
                return True
            return False

        for line in lines:
            if starts_candidate(line):
                if current:
                    blocks.append(current)
                current = [line]
            else:
                if current:
                    current.append(line)
        if current:
            blocks.append(current)

        out: List[Dict[str, str]] = []
        for block in blocks[:8]:
            raw = "\n".join(block).strip()
            if len(raw) < 30:
                continue
            first = block[0].strip()
            first = re.sub(r"^#{3,}\s+", "", first)
            first = re.sub(r"^(?:[-*]|\d+[\.)])\s+", "", first)
            m = re.search(r"\*\*(.*?)\*\*", first)
            title = m.group(1).strip() if m else first
            title = re.split(r"\s+[—–-]\s+|\s*:\s*", title, maxsplit=1)[0]
            title = re.sub(r"^(Signal|Hypothèse|Verrou|Axe)\s*(candidat|technique)?\s*\d*\s*[:\-–—]?\s*", "", title, flags=re.I).strip()
            title = truncate(title, 220)
            if len(title) < 12:
                continue
            out.append({"title": title, "block": raw})
        return out

    def build_llm_reformulated_verrous(
        self,
        content: str,
        sections: Dict[str, List[Dict[str, Any]]],
        frascati_summary: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Produit une liste structurée pour l'onglet de validation consultant.

        Important :
        - le titre affiché vient de la reformulation LLM ;
        - les preuves et scores restent ceux de Chroma/NLP/Frascati ;
        - aucune preuve n'est ajoutée depuis la mémoire CIR précédente.
        """
        raw_sources = dedupe_sources(sections.get("verrous", []) or [], max_items=24)
        parsed = self._extract_signal_blocks_from_markdown(content)

        if not parsed:
            # Fallback : si le LLM n'a pas structuré la section, on garde les sources Chroma,
            # mais on marque explicitement que ce n'est pas une reformulation LLM complète.
            parsed = []
            for src in raw_sources[:8]:
                meta = meta_of(src)
                title = clean_text(meta.get("theme_label") or meta.get("final_role") or "Signal technique à reformuler")
                parsed.append({
                    "title": title,
                    "block": source_text(src),
                })

        structured: List[Dict[str, Any]] = []
        used_titles: set[str] = set()

        for idx, cand in enumerate(parsed, start=1):
            title = clean_text(cand.get("title"))
            block = repair_mojibake(cand.get("block") or "")
            if not title or title.lower() in used_titles:
                continue
            used_titles.add(title.lower())

            scored_sources = sorted(
                raw_sources,
                key=lambda s: self._similarity_for_llm_candidate(title + "\n" + block, s),
                reverse=True,
            )
            selected_sources = [s for s in scored_sources[:4] if self._similarity_for_llm_candidate(title + "\n" + block, s) > 0.02]
            if not selected_sources and idx <= len(raw_sources):
                selected_sources = [raw_sources[idx - 1]]

            docs: List[str] = []
            scores: List[float] = []
            decisions: Dict[str, int] = {}
            source_payload: List[Dict[str, Any]] = []

            for src in selected_sources:
                meta = meta_of(src)
                doc = source_doc(src)
                if doc and doc not in docs:
                    docs.append(doc)
                try:
                    score = float(meta.get("frascati_score"))
                    if score > 0:
                        scores.append(score)
                except Exception:
                    pass
                decision = clean_text(meta.get("frascati_decision") or meta.get("decision") or "verrou_a_verifier")
                decisions[decision] = decisions.get(decision, 0) + 1
                source_payload.append({
                    "document": doc,
                    "source_path": source_path(src),
                    "text": truncate(source_text(src), 900),
                    "metadata": meta,
                })

            avg_score = round(sum(scores) / len(scores), 4) if scores else frascati_summary.get("average_frascati_score")
            main_decision = max(decisions.items(), key=lambda x: x[1])[0] if decisions else "verrou_a_verifier"
            tag = "MOYEN POUR CIR"
            try:
                if avg_score is not None and float(avg_score) >= 0.68:
                    tag = "PERTINENT POUR CIR"
            except Exception:
                pass

            structured.append({
                "title": title,
                "tag_cir": tag,
                "score": avg_score,
                "frascati_decision": main_decision,
                "consultant_status": "a_valider",
                "document": "; ".join(docs[:6]) or "Sources Chroma à vérifier",
                "justification": truncate(block, 1200),
                "text": block,
                "source": "llm_reformulated_from_chroma_frascati",
                "needs_human_validation": True,
                "source_json": {
                    "source": "llm_reformulated_from_chroma_frascati",
                    "llm_title": title,
                    "llm_block": block,
                    "frascati_decision": main_decision,
                    "frascati_score": avg_score,
                    "sources": source_payload,
                    "principle": "Titre reformulé par LLM ; preuves et score issus de Chroma/NLP/Frascati.",
                },
            })

            if len(structured) >= 6:
                break

        return structured

    # =====================================================
    # External reports
    # =====================================================

    def load_ai_detection_report(self) -> Dict[str, Any]:
        path = self.diagnostic_dir / "ai_detection_report.json"
        if not path.exists():
            return {"ok": False, "missing": True, "message": "Aucun rapport IA documentaire disponible."}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def ai_detection_prompt_block(self, ai_report: Dict[str, Any]) -> str:
        if not isinstance(ai_report, dict) or not ai_report.get("ok"):
            return "Aucun score IA documentaire disponible."

        summary = ai_report.get("summary") or {}
        ai_detection = ai_report.get("ai_detection") or {}
        score = summary.get("average_ai_percentage") or ai_detection.get("global_ai_percentage")
        if score is None:
            score = summary.get("average_ai_score")

        return (
            "Contrôle IA documentaire :\n"
            f"- Score IA moyen : {score}%\n"
            f"- Niveau : {summary.get('risk_level') or ai_detection.get('risk_level')}\n"
            f"- Passages analysés : {summary.get('passages_count') or ai_detection.get('total_passages_analyzed')}\n"
            f"- Passages risque élevé : {summary.get('high_count') or ai_detection.get('high_risk_passages_count')}\n"
            f"- Passages risque moyen : {summary.get('medium_count') or ai_detection.get('medium_risk_passages_count')}\n"
            "Note : ce score concerne les passages extraits des documents bruts, pas la synthèse LLM."
        )

    def _find_current_nlp_result_path(self) -> Optional[Path]:
        candidates = [
            self.out_dir / "nlp" / "nlp_result.json",
            self.out_dir / "nlp_result.json",
            Path(r"C:\EnnoSmart") / "storage" / "organismes" / str(self.organisme).lower() / "projects" / str(self.project).lower() / "years" / self.year / "nlp" / "nlp_result.json",
            Path(r"C:\EnnoSmart") / "outputs" / "safe_rag_upload" / self.organisme / self.project / self.year / "nlp_result.json",
        ]

        for path in candidates:
            if path.exists():
                return path

        return None

    def load_cir_memory_report(self) -> Dict[str, Any]:
        """
        Comparaison CIR précédent toujours active, mais avec cache.

        Objectif consultant :
        - ne pas recalculer la comparaison N vs N-1 à chaque clic ;
        - réutiliser le résultat si le NLP courant et la mémoire CIR précédente n'ont pas changé ;
        - recalculer seulement après modification des documents/NLP ou après nouvel import CIR N-1.
        """
        try:
            from modules.CIR_MEMORY.cir_memory import load_or_create_cir_memory_comparison

            nlp_path = self._find_current_nlp_result_path()
            if not nlp_path:
                return {
                    "ok": False,
                    "missing": True,
                    "message": "nlp_result.json courant introuvable pour comparer avec le CIR précédent.",
                }

            cache_dir = self.diagnostic_dir / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_dir / "cir_previous_comparison_cache.json"

            # Empreinte du NLP courant.
            nlp_hash = sha256_file(nlp_path)

            # Empreinte de la mémoire CIR précédente.
            # On couvre les chemins utilisés par les versions précédentes du projet.
            root = Path(r"C:\EnnoSmart")
            org_slug = str(self.organisme).lower()
            project_slug = str(self.project).lower()

            possible_memory_paths = [
                self.out_dir / "cir_previous" / "cir_final_memory.json",
                self.out_dir / "cir_final_consultant" / "current" / "cir_final_memory.json",
                self.out_dir / "cir_memory" / "cir_final_memory.json",
                root / "storage" / "organismes" / org_slug / "projects" / project_slug / "years" / self.year / "cir_previous" / "cir_final_memory.json",
                root / "storage" / "organismes" / org_slug / "projects" / project_slug / "years" / self.year / "cir_final_consultant" / "current" / "cir_final_memory.json",
                root / "storage" / "organismes" / org_slug / "projects" / project_slug / "years" / self.year / "cir_memory" / "cir_final_memory.json",
                root / "outputs" / "safe_rag_upload" / self.organisme / self.project / self.year / "cir_previous" / "cir_final_memory.json",
                root / "outputs" / "safe_rag_upload" / self.organisme / self.project / self.year / "cir_final_consultant" / "current" / "cir_final_memory.json",
            ]

            previous_hash_parts: List[str] = []
            existing_memory_paths: List[str] = []

            for memory_path in possible_memory_paths:
                try:
                    if memory_path.exists() and memory_path.is_file():
                        previous_hash_parts.append(sha256_file(memory_path))
                        existing_memory_paths.append(str(memory_path))
                except Exception:
                    continue

            # Si aucun fichier mémoire n'est détecté, on laisse quand même le module CIR_MEMORY décider.
            previous_hash = sha256_text("|".join(previous_hash_parts))
            cache_key = sha256_text(
                f"{self.organisme}|{self.project}|{self.year}|{nlp_hash}|{previous_hash}"
            )

            cached = safe_read_json(cache_path)
            if cached.get("cache_key") == cache_key and isinstance(cached.get("report"), dict):
                report = cached["report"]
                report["cached"] = True
                report["cache_key"] = cache_key
                report["cache_path"] = str(cache_path)
                report["message_cache"] = "Comparaison CIR précédent réutilisée depuis le cache."
                print("♻️ Comparaison CIR précédent réutilisée depuis le cache")
                return report

            t0 = time.time()
            report = load_or_create_cir_memory_comparison(
                organisme=self.organisme,
                project=self.project,
                year=self.year,
                nlp_result_path=nlp_path,
            )

            if not isinstance(report, dict):
                report = {
                    "ok": False,
                    "message": "Rapport CIR mémoire invalide.",
                }

            elapsed = round(time.time() - t0, 2)

            report["cached"] = False
            report["cache_key"] = cache_key
            report["cache_path"] = str(cache_path)
            report["comparison_elapsed_seconds"] = elapsed
            report["nlp_result_path"] = str(nlp_path)
            report["previous_memory_paths_detected"] = existing_memory_paths

            save_json(
                cache_path,
                {
                    "cache_key": cache_key,
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                    "organisme": self.organisme,
                    "project": self.project,
                    "year": self.year,
                    "nlp_hash": nlp_hash,
                    "previous_hash": previous_hash,
                    "previous_memory_paths_detected": existing_memory_paths,
                    "report": report,
                },
            )

            print(f"✅ Comparaison CIR précédent recalculée en {elapsed}s")
            return report

        except Exception as e:
            return {
                "ok": False,
                "error": str(e),
                "message": "Impossible de comparer avec le CIR précédent.",
            }

    # =====================================================
    # LLM prompt
    # =====================================================

    def _build_fast_single_prompt(
        self,
        sections: Dict[str, List[Dict[str, Any]]],
        frascati_summary: Dict[str, Any],
        style_memory_report: Optional[Dict[str, Any]],
        ai_detection_report: Optional[Dict[str, Any]],
        cir_memory_report: Optional[Dict[str, Any]],
    ) -> str:
        budgets = {
            "global": int(os.getenv("ENNOSMART_DIAG_FAST_GLOBAL", "5")),
            "objectifs": int(os.getenv("ENNOSMART_DIAG_FAST_OBJECTIFS", "5")),
            "verrous": int(os.getenv("ENNOSMART_DIAG_FAST_VERROUS", "10")),
            "methodes": int(os.getenv("ENNOSMART_DIAG_FAST_METHODES", "7")),
            "resultats": int(os.getenv("ENNOSMART_DIAG_FAST_RESULTATS", "7")),
            "parametres": int(os.getenv("ENNOSMART_DIAG_FAST_PARAMETRES", "5")),
            "limites": int(os.getenv("ENNOSMART_DIAG_FAST_LIMITES", "5")),
        }

        parts: List[str] = []
        parts.append("Tu es EnnoDiagnostic, agent de synthèse CIR.")
        parts.append("")
        parts.append("Règles strictes :")
        parts.append("- Les faits doivent venir uniquement des sources RAG/Chroma du dossier courant.")
        parts.append("- La mémoire CIR est uniquement un style de rédaction, jamais une source factuelle.")
        parts.append("- Ne refais pas Frascati : reprends seulement le résumé déjà calculé.")
        parts.append("- Ne recalcule jamais le score Frascati.")
        parts.append("- Le score Frascati vient du NLP / module Frascati ; tu dois seulement le justifier.")
        parts.append("- Ne dis jamais que le CIR ou qu’un verrou est validé ; écris à valider par le consultant et à confirmer par EnnoScholar si nécessaire.")
        parts.append("- Cite les noms de documents quand ils sont disponibles.")
        parts.append("- Ne cite jamais 'JSON NLP'. Dis sources indexées ou sources Chroma.")
        parts.append("- Si une comparaison CIR précédent est disponible, ne l'utilise pas comme preuve factuelle du dossier courant ; elle sert seulement à distinguer continuité, évolution et nouveauté.")
        parts.append("")
        parts.append("Règles de consolidation CIR :")
        parts.append("- Le NLP/Frascati fournit des signaux bruts, des scores et des preuves. Ton rôle est de les reformuler en hypothèses de verrous R&D candidates, lisibles pour un consultant CIR.")
        parts.append("- La mémoire CIR précédente sert seulement au style de rédaction : niveau de précision, structure, ton prudent. Elle ne doit jamais ajouter un fait absent des sources du dossier courant.")
        parts.append("- Interdiction de garder des titres génériques comme : Non-transférabilité, Cause racine, Performance insuffisante, Comportement instable, Compromis entre contraintes.")
        parts.append("- Construis des titres techniques spécifiques à partir des preuves : objet technique + phénomène non maîtrisé + contrainte ou condition d'usage.")
        parts.append("- Ne force aucun axe métier prédéfini : mécanique, chimie, architecture, IA ou LLM doivent suivre la même logique objet/phénomène/contrainte/incertitude/preuves.")
        parts.append("")
        parts.append("Résumé Frascati déjà calculé par le NLP / Frascati :")
        parts.append(json.dumps(frascati_summary, ensure_ascii=False, indent=2))
        parts.append("")
        parts.append("Consigne spéciale pour la section 'Justification Frascati du score' :")
        parts.append("- La justification doit être spécifique au projet courant, jamais générique.")
        parts.append("- Explique pourquoi le score est cohérent avec les preuves du projet.")
        parts.append("- Explique les éléments qui augmentent le score et ceux qui le limitent.")
        parts.append("- Explique ce qui a été vérifié par NLP/Frascati : rôles, scores, décisions, signaux candidats, méthodes, résultats, paramètres, limites.")
        parts.append("")

        if isinstance(ai_detection_report, dict) and ai_detection_report.get("ok"):
            parts.append("Contrôle IA documentaire déjà disponible :")
            parts.append(self.ai_detection_prompt_block(ai_detection_report))
            parts.append("")
        else:
            parts.append("Contrôle IA documentaire déjà disponible : aucun rapport exploitable ou rapport absent.")
            parts.append("")

        parts.append("Mémoire de style CIR courte :")
        parts.append(truncate(self._style_memory_for_role(style_memory_report, "verrou", max_chars=1200), 1200))
        parts.append("")
        parts.append("Sources RAG compactes par rôle :")
        parts.append(build_sources_block("Contexte global", sections.get("global", []), budgets["global"]))
        parts.append(build_sources_block("Objectifs", sections.get("objectifs", []), budgets["objectifs"]))
        parts.append(build_sources_block("Verrous", sections.get("verrous", []), budgets["verrous"]))
        parts.append(build_sources_block("Démarches / méthodes", sections.get("methodes", []), budgets["methodes"]))
        parts.append(build_sources_block("Résultats", sections.get("resultats", []), budgets["resultats"]))
        parts.append(build_sources_block("Paramètres", sections.get("parametres", []), budgets["parametres"]))
        parts.append(build_sources_block("Limites / points à vérifier", sections.get("limites", []), budgets["limites"]))
        parts.append(build_sources_block("Axe problèmes transverses", sections.get("axe_problemes_transverses", []), 5))
        parts.append(build_sources_block("Axe contraintes transverses", sections.get("axe_contraintes_transverses", []), 5))
        parts.append(build_sources_block("Axe preuves et résultats", sections.get("axe_preuves_resultats", []), 5))
        parts.append("")
        parts.append("Réponse attendue exactement avec ces titres Markdown :")
        parts.append("## Lecture Frascati du dossier")
        parts.append("## Justification Frascati du score")
        parts.append("## Synthèse stratégique du projet")
        parts.append("## Objectif global reformulé")
        parts.append(f"## {SIGNAL_SECTION_TITLE}")
        parts.append("## Démarche expérimentale détectée")
        parts.append("## Résultats et métriques disponibles")
        parts.append("## Paramètres et contraintes techniques")
        parts.append("## Points à valider par le consultant")
        parts.append("")
        parts.append("Contraintes de rédaction :")
        parts.append("- Maximum 5 signaux candidats consolidés, techniques, prudents et sourcés.")
        parts.append("- Pour chaque signal candidat : titre technique provisoire reformulé par le LLM, difficulté observée, phénomène possiblement non maîtrisé, preuves/documents, statut de validation.")
        parts.append("- Le titre doit être exploitable pour EnnoScholar après validation consultant : pas un simple mot-clé, pas un thème générique.")
        parts.append("- Dans la démarche : organiser par axe technique, sans inventer de protocole.")
        parts.append("- Dans les résultats : séparer résultats chiffrés, observations qualitatives, résultats insuffisants/à valider.")
        parts.append("- Ne fabrique jamais de valeur, de résultat ou de document source.")

        prompt = "\n".join(parts)

        try:
            llm_max_chars = int(os.getenv("ENNOSMART_LLM_MAX_PROMPT_CHARS", "18000"))
        except Exception:
            llm_max_chars = 18000
        try:
            requested_hard_max = int(os.getenv("ENNOSMART_DIAG_FAST_PROMPT_HARD_MAX", str(llm_max_chars)))
        except Exception:
            requested_hard_max = llm_max_chars

        hard_max = max(12000, min(requested_hard_max, llm_max_chars))

        if len(prompt) > hard_max:
            print(f"[EnnoDiagnostic][FAST_PROMPT][V96] prompt_chars={len(prompt)} > {hard_max}, compression contractuelle")

            parts2: List[str] = []
            parts2.append("Tu es EnnoDiagnostic, agent de synthèse CIR.")
            parts2.append("")
            parts2.append("Règles strictes :")
            parts2.append("- Les faits doivent venir uniquement des sources RAG/Chroma du dossier courant.")
            parts2.append("- Ne refais pas Frascati et ne recalcule jamais le score.")
            parts2.append("- Le score Frascati vient du NLP / module Frascati ; tu dois seulement le justifier.")
            parts2.append("- La justification Frascati doit être spécifique au projet courant, jamais générique.")
            parts2.append("- Ne dis jamais que le CIR ou qu’un verrou est validé ; écris à valider par le consultant et à confirmer par EnnoScholar si nécessaire.")
            parts2.append("- Si une comparaison CIR précédent est disponible, ne l'utilise pas comme preuve factuelle du dossier courant ; elle sert seulement à distinguer continuité, évolution et nouveauté.")
            parts2.append("")
            parts2.append("Résumé Frascati déjà calculé par le NLP / Frascati :")
            parts2.append(json.dumps(frascati_summary, ensure_ascii=False, indent=2))
            parts2.append("")

            if isinstance(ai_detection_report, dict) and ai_detection_report.get("ok"):
                parts2.append("Contrôle IA documentaire déjà disponible :")
                parts2.append(self.ai_detection_prompt_block(ai_detection_report))
                parts2.append("")
            else:
                parts2.append("Contrôle IA documentaire déjà disponible : aucun rapport exploitable ou rapport absent.")
                parts2.append("")

            parts2.append("Mémoire de style CIR courte :")
            parts2.append(truncate(self._style_memory_for_role(style_memory_report, "verrou", max_chars=900), 900))
            parts2.append("")
            parts2.append("Sources RAG compactes par rôle :")
            parts2.append(build_sources_block_compact("Contexte global", sections.get("global", []), 2, 220))
            parts2.append(build_sources_block_compact("Objectifs", sections.get("objectifs", []), 2, 220))
            parts2.append(build_sources_block_compact("Verrous", sections.get("verrous", []), 5, 250))
            parts2.append(build_sources_block_compact("Démarches / méthodes", sections.get("methodes", []), 4, 240))
            parts2.append(build_sources_block_compact("Résultats", sections.get("resultats", []), 4, 240))
            parts2.append(build_sources_block_compact("Paramètres", sections.get("parametres", []), 3, 220))
            parts2.append(build_sources_block_compact("Limites / points à vérifier", sections.get("limites", []), 3, 220))
            parts2.append(build_sources_block_compact("Axe problèmes transverses", sections.get("axe_problemes_transverses", []), 2, 220))
            parts2.append(build_sources_block_compact("Axe contraintes transverses", sections.get("axe_contraintes_transverses", []), 2, 220))
            parts2.append(build_sources_block_compact("Axe preuves et résultats", sections.get("axe_preuves_resultats", []), 2, 220))
            parts2.append("")
            parts2.append("Réponse attendue exactement avec ces titres Markdown :")
            parts2.append("## Lecture Frascati du dossier")
            parts2.append("## Justification Frascati du score")
            parts2.append("## Synthèse stratégique du projet")
            parts2.append("## Objectif global reformulé")
            parts2.append(f"## {SIGNAL_SECTION_TITLE}")
            parts2.append("## Démarche expérimentale détectée")
            parts2.append("## Résultats et métriques disponibles")
            parts2.append("## Paramètres et contraintes techniques")
            parts2.append("## Points à valider par le consultant")
            parts2.append("")
            parts2.append("Contraintes : verrous techniques sourcés, justification Frascati spécifique, pas de valeurs inventées.")

            prompt = "\n".join(parts2)

            if len(prompt) > hard_max:
                overflow_note = "\n\n[Contexte réduit : Frascati, score IA, mémoire style et titres obligatoires conservés. Sources complètes disponibles dans chroma_sections.]"
                prompt = prompt[: max(1000, hard_max - len(overflow_note))].rstrip() + overflow_note

        return prompt

    # =====================================================
    # Fallbacks and dedicated Frascati justification
    # =====================================================

    def build_frascati_section(self, frascati_summary: Dict[str, Any], ai_detection_report: Optional[Dict[str, Any]] = None) -> str:
        lines = []
        lines.append("## Lecture Frascati du dossier")
        lines.append(f"- Score Frascati moyen récupéré depuis Chroma : {frascati_summary.get('average_frascati_score')}")
        lines.append(f"- Nombre de scores utilisés : {frascati_summary.get('scores_count')}")
        lines.append(f"- Décisions détectées : {frascati_summary.get('decisions_count')}")
        lines.append(f"- Niveaux de signaux candidats : {frascati_summary.get('candidate_levels_count')}")
        lines.append("")
        lines.append(
            "Ce score reprend les métadonnées produites pendant le NLP et le contrôle Frascati. "
            "Il ne constitue pas une validation finale du CIR ; les verrous doivent rester à valider par le consultant."
        )

        if isinstance(ai_detection_report, dict) and ai_detection_report.get("ok"):
            lines.append("")
            lines.append("### Contrôle IA documentaire")
            lines.append(self.ai_detection_prompt_block(ai_detection_report))

        return "\n".join(lines)

    def fallback_section_without_llm(self, title: str, sources: List[Dict[str, Any]], max_items: int = 8) -> str:
        lines = [f"## {title}"]

        if not sources:
            lines.append("- Aucun élément récupéré depuis Chroma.")
            return "\n".join(lines)

        for src in sources[:max_items]:
            meta = meta_of(src)
            doc = source_doc(src)
            role = clean_text(meta.get("role"))
            decision = clean_text(meta.get("frascati_decision") or meta.get("decision"))
            score = meta.get("frascati_score", "")
            page = meta.get("page") or meta.get("page_number") or src.get("page")
            txt = truncate(source_text(src), 330)
            lines.append(
                f"- Document : {doc or '-'} | page={page if page not in (None, '') else '-'} | "
                f"rôle={role or '-'} | Frascati={decision or '-'} | score={score if score != '' else '-'}\n"
                f"  {txt}"
            )

        return "\n".join(lines)

    def build_frascati_justification_section(
        self,
        frascati_summary: Dict[str, Any],
        sections: Dict[str, List[Dict[str, Any]]],
        ai_detection_report: Optional[Dict[str, Any]] = None,
    ) -> str:
        score = frascati_summary.get("average_frascati_score")
        scores_count = frascati_summary.get("scores_count")
        decisions = frascati_summary.get("decisions_count") or {}
        levels = frascati_summary.get("candidate_levels_count") or {}

        evidence_sources = dedupe_sources(
            (sections.get("verrous") or [])[:8]
            + (sections.get("methodes") or [])[:5]
            + (sections.get("resultats") or [])[:5]
            + (sections.get("parametres") or [])[:4]
            + (sections.get("limites") or [])[:4]
            + (sections.get("axe_problemes_transverses") or [])[:4]
            + (sections.get("axe_contraintes_transverses") or [])[:4]
            + (sections.get("axe_preuves_resultats") or [])[:4],
            max_items=18,
        )

        docs: List[str] = []
        evidence_lines: List[str] = []
        joined_text = " ".join(source_text(src) for src in evidence_sources).lower()

        # Extraction générique de thèmes dominants depuis les sources, sans lexique projet.
        theme_terms = []
        stop_terms = {
            "verrou", "verrous", "signal", "signaux", "technique", "techniques", "candidat", "candidats",
            "source", "sources", "document", "documents", "frascati", "validation", "consultant", "projet",
            "preuve", "preuves", "dossier", "methode", "méthode", "resultat", "résultat", "analyse",
        }
        for tok in re.findall(r"[a-zA-ZÀ-ÿ0-9][a-zA-ZÀ-ÿ0-9%°µ/\-.']{3,}", joined_text):
            t = tok.strip("-_. '").lower()
            if t and t not in stop_terms:
                theme_terms.append(t)
        counts = {}
        for t in theme_terms:
            counts[t] = counts.get(t, 0) + 1
        themes = [t for t, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:8]]

        for src in evidence_sources:
            doc = source_doc(src)
            if doc and doc not in docs:
                docs.append(doc)

            txt = truncate(source_text(src), 240)
            meta = meta_of(src)
            role = clean_text(meta.get("role"))
            decision = clean_text(meta.get("frascati_decision") or meta.get("decision"))
            if txt:
                evidence_lines.append(
                    f"- {doc or 'Source Chroma'}"
                    f"{f' ({role})' if role else ''}"
                    f"{f' — décision {decision}' if decision else ''} : {txt}"
                )

        if not evidence_lines:
            evidence_lines.append("- Aucune preuve Chroma suffisamment explicite n’a été retrouvée : à valider par le consultant.")

        ai_summary = ""
        if isinstance(ai_detection_report, dict) and ai_detection_report.get("ok"):
            ai_summary = self.ai_detection_prompt_block(ai_detection_report)

        lines: List[str] = []
        lines.append("## Justification Frascati du score")
        lines.append("")
        lines.append("### Pourquoi ce score ?")
        lines.append(
            f"Le score Frascati de {score if score is not None else 'non disponible'} provient du module NLP/Frascati exécuté pendant la préparation des sources. "
            f"Il s’appuie sur {scores_count} chunk(s) porteurs d’un score et sur les décisions Frascati récupérées depuis Chroma. Ces éléments sont des signaux candidats, pas des verrous validés. "
            "Il n’est pas recalculé par le LLM."
        )
        if themes:
            lines.append("Dans ce dossier, le score est porté par les signaux techniques candidats suivants : " + "; ".join(themes) + ".")
        if docs:
            lines.append("Les documents principalement mobilisés sont : " + "; ".join(docs[:8]) + ".")
        lines.append("")
        lines.append("### Éléments qui augmentent le score")
        lines.extend(evidence_lines[:8])
        lines.append("")
        lines.append("### Éléments qui limitent le score")
        lines.append(f"- Les décisions Frascati restent au stade de qualification avant EnnoScholar : {json.dumps(decisions, ensure_ascii=False)}.")
        lines.append(f"- Les niveaux candidats détectés montrent que les signaux ne sont pas confirmés comme verrous CIR : {json.dumps(levels, ensure_ascii=False)}.")
        lines.append("- La justification CIR finale nécessite EnnoScholar et validation consultant pour confirmer le caractère non directement résoluble des difficultés techniques.")
        lines.append("- Les résultats chiffrés et la cause technique exacte doivent être reliés clairement aux signaux retenus pour EnnoScholar.")
        lines.append("- Les contraintes industrielles doivent être séparées des incertitudes technologiques réellement investiguées.")
        lines.append("")
        lines.append("### Ce qui a été vérifié")
        lines.append(f"- Nombre de scores Frascati exploités pour prioriser les signaux candidats : {scores_count}.")
        lines.append(f"- Décisions Frascati récupérées depuis les chunks Chroma : {json.dumps(decisions, ensure_ascii=False)}.")
        lines.append(f"- Niveaux de signaux candidats : {json.dumps(levels, ensure_ascii=False)}.")
        lines.append("- Présence de passages de rôle verrou, méthode, résultat, paramètre et limite dans les sources indexées ; le rôle verrou signifie candidat à vérifier, pas validation finale.")
        if ai_summary:
            lines.append("- Cohérence avec le contrôle IA documentaire disponible, distinct de la décision CIR.")
        lines.append("")
        lines.append("### Points à valider par le consultant")
        lines.append("- Confirmer quels signaux correspondent réellement à une incertitude technologique au sens CIR.")
        lines.append("- Vérifier que les preuves documentaires rattachent bien les essais, mesures et observations aux signaux retenus.")
        lines.append("- Confirmer les résultats manquants ou partiels avant EnnoScholar et avant toute décision d’éligibilité.")
        if ai_summary:
            lines.append("")
            lines.append("### Rappel contrôle IA documentaire")
            lines.append(ai_summary)

        return "\n".join(lines)

    def generate_frascati_justification_section(
        self,
        sections: Dict[str, List[Dict[str, Any]]],
        frascati_summary: Dict[str, Any],
        ai_detection_report: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        title = "Justification Frascati du score"

        selected_sources = dedupe_sources(
            (sections.get("verrous") or [])[:10]
            + (sections.get("methodes") or [])[:5]
            + (sections.get("resultats") or [])[:5]
            + (sections.get("parametres") or [])[:4]
            + (sections.get("limites") or [])[:4]
            + (sections.get("axe_problemes_transverses") or [])[:4]
            + (sections.get("axe_contraintes_transverses") or [])[:4]
            + (sections.get("axe_preuves_resultats") or [])[:4],
            max_items=22,
        )

        fallback_content = self.build_frascati_justification_section(
            frascati_summary=frascati_summary,
            sections=sections,
            ai_detection_report=ai_detection_report,
        )

        if self.llm is None:
            return {
                "ok": True,
                "title": title,
                "content": fallback_content,
                "mode": "fallback_without_llm",
                "prompt_chars": 0,
                "sources_count": len(selected_sources),
            }

        prompt = f"""
Tu es EnnoDiagnostic, agent d'aide à la qualification CIR.

RÈGLE ABSOLUE :
Le score Frascati est déjà calculé par le NLP / module Frascati.
Tu ne dois jamais recalculer le score.
Tu ne dois jamais inventer un autre score.
Tu dois uniquement JUSTIFIER le score fourni à partir des preuves du projet courant. Ce score priorise des signaux candidats avant EnnoScholar ; il ne valide aucun verrou CIR.

Score et décisions Frascati calculés par le backend :
{json.dumps(frascati_summary, ensure_ascii=False, indent=2)}

Contrôle IA documentaire :
{self.ai_detection_prompt_block(ai_detection_report or {})}

Sources du projet courant :
{build_sources_block("Sources utilisées pour justifier le score Frascati", selected_sources, max_items=18)}

Ta réponse doit être spécifique au projet courant.
Interdiction d'écrire une justification générique.
Ne parle pas de manière abstraite : cite les phénomènes, essais, paramètres, documents et limites présents dans les sources.
Si une preuve manque, écris clairement : à valider par le consultant.

Tu dois produire exactement cette section Markdown :

## Justification Frascati du score

### Pourquoi ce score ?
Explique pourquoi le score obtenu est cohérent avec les preuves du projet courant.
Explique aussi pourquoi ce score ne correspond pas à une validation maximale ni à un verrou scientifiquement défendable.

### Éléments qui augmentent le score
- Liste les éléments techniques concrets détectés dans les sources.
- Chaque point doit être spécifique au projet et relié à une preuve ou un document.

### Éléments qui limitent le score
- Explique pourquoi le score n'est pas maximal.
- Mentionne les preuves manquantes, incomplètes ou à confirmer.

### Ce qui a été vérifié
- Explique ce que le NLP/Frascati a vérifié : rôles des passages, signaux candidats, méthodes, résultats, paramètres, limites, scores et décisions.

### Points à valider par le consultant
- Liste les validations humaines et scientifiques nécessaires avant décision CIR, notamment le passage par EnnoScholar.

Contraintes :
- Ne pas inventer de preuve.
- Ne pas utiliser de justification générique.
- Citer les documents quand ils sont disponibles.
- Ne jamais écrire que le CIR ou qu’un verrou est validé avant EnnoScholar et validation consultant.
- Ne jamais recalculer ni modifier le score.
""".strip()

        try:
            content = self.llm.generate(
                prompt,
                temperature=float(os.getenv("ENNOSMART_FRASCATI_JUSTIFICATION_TEMPERATURE", "0.03")),
                max_output_tokens=int(os.getenv("ENNOSMART_FRASCATI_JUSTIFICATION_MAX_TOKENS", "1100")),
                retries=int(os.getenv("ENNOSMART_FRASCATI_JUSTIFICATION_RETRIES", "1")),
            )
            content = repair_mojibake(content)
            if not content.startswith("##"):
                content = f"## {title}\n{content}"

            section_text = extract_markdown_section(content, title)
            if len(section_text) < 450:
                return {
                    "ok": False,
                    "title": title,
                    "content": fallback_content,
                    "mode": "fallback_after_too_short_llm_justification",
                    "prompt_chars": len(prompt),
                    "sources_count": len(selected_sources),
                    "llm_section_chars": len(section_text),
                }

            return {
                "ok": True,
                "title": title,
                "content": content,
                "mode": "llm_dedicated_frascati_justification",
                "prompt_chars": len(prompt),
                "sources_count": len(selected_sources),
            }

        except Exception as e:
            print(f"[EnnoDiagnostic][FRASCATI_JUSTIFICATION][ERROR] {e}")
            return {
                "ok": False,
                "title": title,
                "content": fallback_content,
                "mode": "fallback_after_llm_error",
                "error": str(e),
                "prompt_chars": len(prompt),
                "sources_count": len(selected_sources),
            }

    def fallback_without_llm(self, sections: Dict[str, List[Dict[str, Any]]], frascati_summary: Dict[str, Any]) -> str:
        return "\n\n".join(
            [
                self.build_frascati_section(frascati_summary),
                self.build_frascati_justification_section(frascati_summary, sections),
                self.fallback_section_without_llm("Synthèse stratégique du projet", sections.get("global", []), 6),
                self.fallback_section_without_llm("Objectif global reformulé", sections.get("objectifs", []), 6),
                self.fallback_section_without_llm(SIGNAL_SECTION_TITLE, sections.get("verrous", []), 12),
                self.fallback_section_without_llm("Démarche expérimentale détectée", sections.get("methodes", []), 8),
                self.fallback_section_without_llm("Résultats et métriques disponibles", sections.get("resultats", []), 8),
                self.fallback_section_without_llm("Paramètres et contraintes techniques", sections.get("parametres", []), 8),
                self.fallback_section_without_llm("Points à valider par le consultant", sections.get("limites", []) + sections.get("verrous", []), 10),
            ]
        ).strip()

    # =====================================================
    # Compatibility
    # =====================================================

    def build_section_prompt(
        self,
        title: str,
        instruction: str,
        role: str,
        sources: List[Dict[str, Any]],
        frascati_summary: Dict[str, Any],
        style_memory_report: Optional[Dict[str, Any]] = None,
        extra_sources: Optional[List[Dict[str, Any]]] = None,
        max_sources: int = 10,
        max_extra_sources: int = 4,
    ) -> str:
        extra_sources = extra_sources or []
        parts = [
            "Tu es EnnoDiagnostic, agent de synthèse CIR.",
            "Les faits doivent venir uniquement des sources Chroma du dossier courant.",
            "Tu ne dois pas refaire Frascati ni recalculer le score.",
            f"Objectif : rédiger uniquement la section suivante : {title}",
            instruction,
            build_sources_block(f"Sources RAG utiles pour {title}", sources, max_sources),
        ]
        if extra_sources:
            parts.append(build_sources_block("Contexte complémentaire limité", extra_sources, max_extra_sources))
        parts.append(f"## {title}")
        return "\n\n".join(parts)

    def generate_llm_section(
        self,
        title: str,
        instruction: str,
        role: str,
        sources: List[Dict[str, Any]],
        frascati_summary: Dict[str, Any],
        style_memory_report: Optional[Dict[str, Any]],
        extra_sources: Optional[List[Dict[str, Any]]] = None,
        max_sources: int = 10,
        max_extra_sources: int = 4,
        max_output_tokens: int = 900,
    ) -> Dict[str, Any]:
        if self.llm is None:
            return {
                "ok": True,
                "title": title,
                "content": self.fallback_section_without_llm(title, sources, max_items=max_sources),
                "mode": "fallback_without_llm",
                "prompt_chars": 0,
                "sources_count": len(sources or []),
            }

        prompt = self.build_section_prompt(
            title=title,
            instruction=instruction,
            role=role,
            sources=sources,
            frascati_summary=frascati_summary,
            style_memory_report=style_memory_report,
            extra_sources=extra_sources,
            max_sources=max_sources,
            max_extra_sources=max_extra_sources,
        )
        try:
            content = self.llm.generate(prompt, temperature=0.08, max_output_tokens=max_output_tokens, retries=2)
            content = repair_mojibake(content)
            if not content.startswith("##"):
                content = f"## {title}\n{content}"
            return {"ok": True, "title": title, "content": content, "mode": "llm_section_by_section", "prompt_chars": len(prompt), "sources_count": len(sources or [])}
        except Exception as e:
            return {
                "ok": False,
                "title": title,
                "content": self.fallback_section_without_llm(title, sources, max_items=max_sources),
                "mode": "fallback_after_llm_error",
                "error": str(e),
                "prompt_chars": len(prompt),
                "sources_count": len(sources or []),
            }

    def build_prompt(
        self,
        sections: Dict[str, List[Dict[str, Any]]],
        frascati_summary: Dict[str, Any],
        style_memory_report: Optional[Dict[str, Any]] = None,
        cir_memory_report: Optional[Dict[str, Any]] = None,
    ) -> str:
        return self._build_fast_single_prompt(
            sections=sections,
            frascati_summary=frascati_summary,
            style_memory_report=style_memory_report,
            ai_detection_report=None,
            cir_memory_report=cir_memory_report,
        )

    # =====================================================
    # Generate
    # =====================================================

    def generate_diagnostic(self, save: bool = True) -> Dict[str, Any]:
        t0 = time.time()

        sections = self.retrieve_all_sections()
        frascati_summary = self.frascati_summary_from_chroma(sections.get("verrous", []))
        ai_detection_report = self.load_ai_detection_report()

        # V105 — Comparaison CIR précédent toujours active avec cache.
        # Il n'y a plus de variable d'environnement pour activer/désactiver.
        # Le consultant garde un seul bouton : Lancer EnnoDiagnostic.
        # V106 — Comparaison CIR précédent séparée du diagnostic principal.
        # Le bouton "Lancer EnnoDiagnostic" doit rester rapide :
        # RAG/Chroma + Frascati + contrôle IA + LLM diagnostic.
        # La comparaison N vs N-1 est lancée indépendamment depuis l'onglet
        # "CIR précédent" via POST /projects/{project_id}/cir-previous/compare-current.
        run_cir_memory = False
        print("⏭️ Comparaison CIR précédent ignorée dans /diagnostic/run-agent. Utilise l'onglet CIR précédent pour la lancer.")
        cir_memory_report = {
            "ok": False,
            "skipped": True,
            "has_previous_cir": False,
            "message": "Comparaison CIR précédent non relancée pendant EnnoDiagnostic. Lance-la depuis l'onglet CIR précédent.",
            "route": "/projects/{project_id}/cir-previous/compare-current",
        }
        style_memory_report = self.load_style_memory_context(sections)

        if self.llm is not None:
            prompt = self._build_fast_single_prompt(
                sections=sections,
                frascati_summary=frascati_summary,
                style_memory_report=style_memory_report,
                ai_detection_report=ai_detection_report,
                cir_memory_report=cir_memory_report,
            )
            print(
                f"[EnnoDiagnostic][V95_FAST_CIR_AXES] "
                f"prompt_chars={len(prompt)} "
                f"sources_total={sum(len(v) for v in sections.values())}"
            )
            try:
                content = self.llm.generate(
                    prompt,
                    temperature=float(os.getenv("ENNOSMART_DIAG_TEMPERATURE", "0.06")),
                    max_output_tokens=int(os.getenv("ENNOSMART_DIAG_MAX_OUTPUT_TOKENS", "2600")),
                    retries=int(os.getenv("ENNOSMART_DIAG_LLM_RETRIES", "1")),
                )
                content = repair_mojibake(content)
                if not content.startswith("##"):
                    content = "## Diagnostic CIR\n" + content
                llm_status = "ok"
                llm_error = None
            except Exception as e:
                print(f"[EnnoDiagnostic][V95_FAST_CIR_AXES][ERROR] {e}")
                content = self.fallback_without_llm(sections, frascati_summary)
                llm_status = "fallback_after_llm_error"
                llm_error = str(e)
                prompt = prompt if "prompt" in locals() else ""
        else:
            prompt = ""
            content = self.fallback_without_llm(sections, frascati_summary)
            llm_status = "fallback_without_llm"
            llm_error = None

        # V95 — appel dédié pour la justification Frascati.
        content = normalize_report_vocabulary(content)

        frascati_justification_result = self.generate_frascati_justification_section(
            sections=sections,
            frascati_summary=frascati_summary,
            ai_detection_report=ai_detection_report,
        )
        frascati_justification_content = clean_text((frascati_justification_result or {}).get("content"))

        if frascati_justification_content:
            content = replace_or_insert_markdown_section(
                content=content,
                title="Justification Frascati du score",
                new_section=frascati_justification_content,
                after_title="Lecture Frascati du dossier",
            )

        content = normalize_report_vocabulary(content)
        frascati_justification_text = extract_markdown_section(content, "Justification Frascati du score")
        diagnostic_sections = build_diagnostic_sections(content)
        llm_reformulated_verrous = self.build_llm_reformulated_verrous(
            content=content,
            sections=sections,
            frascati_summary=frascati_summary,
        )

        elapsed = round(time.time() - t0, 2)

        report = {
            "ok": True,
            "mode": "ennodiagnostic_v106_no_cir_previous_in_agent",
            "principle": (
                "Chroma remains the source of truth. The Frascati score is calculated by NLP/Frascati; "
                "the LLM only explains this score from project sources."
            ),
            "organisme": self.organisme,
            "project": self.project,
            "year": self.year,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "elapsed_seconds": elapsed,
            "frascati_summary": frascati_summary,
            "frascati_justification": {
                "source": (frascati_justification_result or {}).get("mode"),
                "score_source": "nlp_frascati_chroma_metadata",
                "generation": frascati_justification_result,
                "score": frascati_summary.get("average_frascati_score"),
                "scores_count": frascati_summary.get("scores_count"),
                "decisions_count": frascati_summary.get("decisions_count"),
                "candidate_levels_count": frascati_summary.get("candidate_levels_count"),
                "llm_generated": (frascati_justification_result or {}).get("mode") == "llm_dedicated_frascati_justification",
                "text": frascati_justification_text,
                "principle": (
                    "Le score Frascati est calculé par le NLP/Frascati. "
                    "Le LLM produit uniquement une justification projet-spécifique à partir des sources RAG/Chroma."
                ),
            },
            "ai_detection_report": ai_detection_report,
            "cir_memory_report": cir_memory_report,
            "style_memory_report": {
                "ok": style_memory_report.get("ok"),
                "memory_path": style_memory_report.get("memory_path"),
                "stats": style_memory_report.get("stats"),
                "examples_count": style_memory_report.get("examples_count", 0),
                "examples_by_role_count": style_memory_report.get("examples_by_role_count", {}),
                "principle": style_memory_report.get("principle"),
                "error": style_memory_report.get("error"),
                "message": style_memory_report.get("message"),
            },
            "diagnostic": {
                "content": content,
                "sections": diagnostic_sections,
                "status": llm_status,
                "error": llm_error,
            },
            "diagnostic_sections": diagnostic_sections,
            "llm_reformulated_verrous": llm_reformulated_verrous,
            "consultant_validation_source": "llm_reformulated_verrous",
            "llm_strategy": {
                "version": "v120_llm_reformulation_for_consultant",
                "why": "NLP/Frascati détecte et score les signaux. Le LLM reformule les hypothèses de verrous en style CIR à partir des sources Chroma et de la mémoire de style, sans utiliser le CIR précédent comme preuve.",
                "prompt_chars": len(prompt),
                "elapsed_seconds": elapsed,
                "cir_memory_in_agent": run_cir_memory,
                "cir_memory_available": bool(isinstance(cir_memory_report, dict) and (cir_memory_report.get("ok") or cir_memory_report.get("has_previous_cir") or cir_memory_report.get("summary"))),
                "sources_count_by_section": {k: len(v or []) for k, v in sections.items()},
            },
            "inputs_status": {
                "global_sources_count": len(sections.get("global", [])),
                "objectifs_count": len(sections.get("objectifs", [])),
                "verrous_count": len(sections.get("verrous", [])),
                "methodes_count": len(sections.get("methodes", [])),
                "resultats_count": len(sections.get("resultats", [])),
                "parametres_count": len(sections.get("parametres", [])),
                "limites_count": len(sections.get("limites", [])),
                "style_examples_count": style_memory_report.get("examples_count", 0),
            },
            "chroma_sections": sections,
        }

        if save:
            save_json(self.report_path, report)

        print(f"[EnnoDiagnostic][V105_DONE] elapsed_seconds={elapsed} status={llm_status}")
        return report

    def generate_diagnostic_complet(self, save: bool = True) -> Dict[str, Any]:
        return self.generate_diagnostic(save=save)

    def generate_all_sections(self, save: bool = True) -> Dict[str, Any]:
        return self.generate_diagnostic(save=save)
