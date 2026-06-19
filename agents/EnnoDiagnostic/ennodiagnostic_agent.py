# -*- coding: utf-8 -*-
from __future__ import annotations

"""
EnnoDiagnostic - CHROMA ONLY + CIR_STYLE_MEMORY

Principe :
- Le NLP extrait et applique Frascati.
- Le RAG indexe le JSON NLP dans Chroma.
- EnnoDiagnostic interroge Chroma par rôle :
    objectif, verrou, methode, resultat, parametre, limite
- Le score Frascati affiché vient des métadonnées Chroma des chunks verrous.
- Le score IA est lu depuis ai_detection_report.json s'il existe.
- La mémoire rédactionnelle CIR est utilisée comme STYLE uniquement :
    elle aide le LLM à mieux rédiger les objectifs/verrous/méthodes/résultats,
    mais elle ne fournit jamais de faits pour le dossier courant.

Le JSON de sortie ennodiagnostic_report.json sert à sauvegarder le rapport généré.
"""

import json
import statistics
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional


# =========================================================
# Utils
# =========================================================

def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def clean_text(text: Any) -> str:
    return str(text or "").strip()


def truncate(text: str, max_chars: int = 700) -> str:
    text = clean_text(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def meta_of(src: Dict[str, Any]) -> Dict[str, Any]:
    return src.get("metadata") if isinstance(src.get("metadata"), dict) else {}


def source_text(src: Dict[str, Any]) -> str:
    return clean_text(src.get("text") or src.get("source_text") or src.get("content") or "")


def source_doc(src: Dict[str, Any]) -> str:
    meta = meta_of(src)
    return clean_text(meta.get("document") or src.get("document") or "")


def dedupe_sources(sources: List[Dict[str, Any]], max_items: int = 30) -> List[Dict[str, Any]]:
    seen = set()
    out = []

    for src in sources or []:
        txt = source_text(src)
        if not txt:
            continue

        meta = meta_of(src)
        key = (
            source_doc(src),
            clean_text(meta.get("role")),
            txt[:250],
        )

        if key in seen:
            continue

        seen.add(key)
        out.append(src)

        if len(out) >= max_items:
            break

    return out


def build_sources_block(title: str, sources: List[Dict[str, Any]], max_items: int = 20) -> str:
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

        txt = truncate(source_text(src), 650)

        lines.append(
            f"- Source {i} | rôle={role or '-'} | document={doc or '-'} | "
            f"frascati={decision or '-'} | score={fr_score if fr_score != '' else '-'}\n"
            f"  Texte : {txt}"
        )

    return "\n".join(lines)


def build_llm(model=None):
    # LLM centralisé dans modules/LLM.
    # Utilise OpenRouter principal + Gemini fallback depuis .env.
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
        self.organisme = organisme_id or organisme or "Girodin"
        self.project = project_id or project or "TGM100"
        self.year = str(year_id or year or "2023")
        self.model = model or gemini_model
        self.use_llm = use_llm
        self.use_style_memory = use_style_memory

        self.out_dir = Path(out_dir) if out_dir else (
            Path("outputs") / "safe_rag_upload" / self.organisme / self.project / self.year
        )
        self.diagnostic_dir = self.out_dir / "ennodiagnostic"
        self.report_path = self.diagnostic_dir / "ennodiagnostic_report.json"

        # IMPORTANT :
        # On charge EnnoRetriever ici seulement quand l'utilisateur clique sur Générer.
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
            sources = self.retriever.search(
                question=query,
                role_filter=role,
                top_k=top_k,
            )
        else:
            sources = self.retriever.search(
                question=query,
                role_filter=None,
                top_k=top_k,
            )

        return dedupe_sources(sources, max_items=top_k)

    def retrieve_all_sections(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Ici on interroge Chroma, pas le JSON NLP.
        """
        sections: Dict[str, List[Dict[str, Any]]] = {}

        sections["global"] = self.search_chroma(
            role=None,
            query=(
                "résumé global du projet contexte technique objectif difficultés "
                "travaux résultats limites innovation"
            ),
            top_k=18,
        )

        sections["objectifs"] = self.search_chroma(
            role="objectif",
            query=(
                "objectifs locaux objectif global finalité technique besoin du projet "
                "performances attendues"
            ),
            top_k=14,
        )

        sections["verrous"] = self.search_chroma(
            role="verrou",
            query=(
                "verrous R&D incertitudes techniques difficultés scientifiques "
                "blocages limites hypothèses à valider"
            ),
            top_k=20,
        )

        sections["methodes"] = self.search_chroma(
            role="methode",
            query=(
                "démarche expérimentale méthodes essais prototypes simulations "
                "protocole travaux réalisés"
            ),
            top_k=16,
        )

        sections["resultats"] = self.search_chroma(
            role="resultat",
            query=(
                "résultats métriques mesures performances essais valeurs chiffrées "
                "observations conclusions"
            ),
            top_k=16,
        )

        sections["parametres"] = self.search_chroma(
            role="parametre",
            query=(
                "paramètres techniques contraintes valeurs seuils dimensions "
                "pression débit température configuration"
            ),
            top_k=12,
        )

        sections["limites"] = self.search_chroma(
            role="limite",
            query=(
                "limites contraintes problèmes points bloquants données manquantes "
                "risques à vérifier"
            ),
            top_k=12,
        )

        return sections

    # =====================================================
    # Frascati from Chroma metadata
    # =====================================================

    def frascati_summary_from_chroma(self, verrou_sources: List[Dict[str, Any]]) -> Dict[str, Any]:
        scores = []
        decisions: Dict[str, int] = {}
        levels: Dict[str, int] = {}

        for src in verrou_sources or []:
            meta = meta_of(src)

            score = meta.get("frascati_score")
            try:
                score = float(score)
                if score > 0:
                    scores.append(score)
            except Exception:
                pass

            decision = clean_text(meta.get("frascati_decision") or meta.get("decision") or "unknown")
            decisions[decision] = decisions.get(decision, 0) + 1

            level = clean_text(meta.get("verrou_candidate_level") or "unknown")
            levels[level] = levels.get(level, 0) + 1

        avg_score = round(statistics.mean(scores), 3) if scores else None

        explanation = (
            "Le score Frascati affiché est calculé à partir des métadonnées des chunks "
            "de rôle verrou récupérés depuis Chroma. Il ne s'agit pas d'une nouvelle décision "
            "EnnoDiagnostic : c'est la reprise du contrôle Frascati déjà appliqué dans le NLP."
        )

        return {
            "average_frascati_score": avg_score,
            "scores_count": len(scores),
            "decisions_count": decisions,
            "candidate_levels_count": levels,
            "explanation": explanation,
        }

    # =====================================================
    # CIR Style Memory
    # =====================================================

    def _sources_query_text(self, sources: List[Dict[str, Any]], max_sources: int = 10) -> str:
        parts = []
        for src in sources[:max_sources]:
            doc = source_doc(src)
            txt = source_text(src)
            if txt:
                parts.append(f"document={doc}\n{txt}")
        return "\n\n".join(parts)

    def load_style_memory_context(self, sections: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        """
        Charge la mémoire rédactionnelle CIR.

        IMPORTANT :
        - Elle sert au style uniquement.
        - Elle ne doit jamais devenir source factuelle.
        - Si la mémoire est absente, l'agent continue avec des règles rédactionnelles strictes.
        """
        if not self.use_style_memory:
            return {
                "ok": False,
                "disabled": True,
                "message": "Mémoire rédactionnelle CIR désactivée.",
                "stats": {},
                "examples_by_role": {},
                "style_block": "Mémoire rédactionnelle CIR désactivée.",
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
            }

            examples_by_role: Dict[str, List[Dict[str, Any]]] = {}
            all_examples: List[Dict[str, Any]] = []

            for role, sources in role_sources.items():
                query = self._sources_query_text(sources, max_sources=10)
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

            # Déduplication exemples.
            seen = set()
            unique_examples = []
            for ex in all_examples:
                ex_id = ex.get("example_id") or f"{ex.get('role')}|{ex.get('text','')[:80]}"
                if ex_id in seen:
                    continue
                seen.add(ex_id)
                unique_examples.append(ex)

            style_block = build_style_block(unique_examples[:10], max_chars_per_example=700)

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
                    "Les faits du diagnostic doivent provenir uniquement des sources Chroma du dossier courant."
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

    def build_style_memory_prompt_block(self, style_memory_report: Dict[str, Any]) -> str:
        lines = []
        lines.append("## Mémoire rédactionnelle CIR")
        lines.append("")
        lines.append("Règle d'utilisation :")
        lines.append("- Les exemples de style CIR ci-dessous servent uniquement à imiter le style, la structure et le vocabulaire.")
        lines.append("- Tu ne dois jamais copier une phrase entière d'un ancien CIR.")
        lines.append("- Tu ne dois jamais reprendre un fait historique d'un ancien CIR s'il n'est pas dans les sources Chroma du dossier courant.")
        lines.append("- Les faits, chiffres, documents et conclusions doivent provenir uniquement des sources Chroma du projet courant.")
        lines.append("")

        if not isinstance(style_memory_report, dict) or not style_memory_report.get("ok"):
            lines.append("Aucune mémoire de style exploitable. Applique alors les règles rédactionnelles strictes ci-dessous.")
        else:
            stats = style_memory_report.get("stats") or {}
            lines.append(f"Exemples de style récupérés : {style_memory_report.get('examples_count', 0)}")
            lines.append(f"Statistiques mémoire : {json.dumps(stats, ensure_ascii=False)}")
            lines.append("")
            lines.append(style_memory_report.get("style_block") or "Aucun exemple disponible.")

        lines.append("")
        lines.append("Règles spécifiques pour les verrous R&D :")
        lines.append("- Les titres de verrous doivent être techniques, contextualisés et reliés au projet.")
        lines.append("- N'écris pas de titres génériques comme : Performance insuffisante, Cause racine inconnue, Compromis de contraintes, Qualité non conforme.")
        lines.append("- Reformule plutôt sous la forme : Maîtrise de [phénomène technique] dans [contexte/prototype/condition].")
        lines.append("- Chaque verrou doit contenir : phénomène technique, difficulté réelle, pourquoi l'état de l'art ou les solutions connues ne suffisent pas, preuves ou documents sources.")
        lines.append("- Cite les documents sources par leur nom quand ils sont disponibles, pas seulement Source 1 ou Source 2.")
        lines.append("- Si les sources sont seulement des signaux faibles, écris clairement : verrou à vérifier par le consultant.")
        lines.append("")
        lines.append("Exemples de titres attendus pour le domaine compresseur TGM100 :")
        lines.append("- Maîtrise du comportement vibro-acoustique du compresseur TGM100 en conditions de fonctionnement.")
        lines.append("- Maîtrise thermique du réfrigérant du premier étage sous forte pression et débit variable.")
        lines.append("- Maîtrise du soufflage carter lié à l'usure des segments et à l'étanchéité du piston.")
        lines.append("- Conception d'un contrepoids sans plomb compatible avec les contraintes d'équilibrage dynamique.")
        lines.append("")
        return "\n".join(lines)

    # =====================================================
    # LLM prompt
    # =====================================================

    def build_prompt(
        self,
        sections: Dict[str, List[Dict[str, Any]]],
        frascati_summary: Dict[str, Any],
        style_memory_report: Optional[Dict[str, Any]] = None,
        cir_memory_report: Optional[Dict[str, Any]] = None,
    ) -> str:
        parts: List[str] = []

        parts.append("Tu es EnnoDiagnostic, agent de synthèse CIR.")
        parts.append("")
        parts.append("Règle centrale :")
        parts.append("- Les sources ci-dessous viennent de Chroma, qui contient les chunks indexés à partir du JSON NLP.")
        parts.append("- Le NLP a déjà fait l'extraction et le contrôle Frascati.")
        parts.append("- Tu ne dois pas refaire Frascati.")
        parts.append("- Tu ne dois pas inventer de verrou absent des sources.")
        parts.append("- Tu dois reformuler en style R&D/CIR clair, professionnel et exploitable par un consultant.")
        parts.append("- Si une information manque, écris clairement : à valider.")
        parts.append("")
        parts.append("Résumé Frascati récupéré depuis les métadonnées Chroma :")
        parts.append(json.dumps(frascati_summary, ensure_ascii=False, indent=2))
        parts.append("")
        parts.append("- N’utilise pas de gras autour des titres Markdown : écris ## Titre, pas **## Titre**.")
        parts.append("")

        parts.append(self.build_style_memory_prompt_block(style_memory_report or {}))
        parts.append("")
        parts.append(self.build_cir_memory_prompt_block(cir_memory_report or {}))
        parts.append("")

        parts.append(build_sources_block("Sources globales récupérées depuis Chroma", sections.get("global", []), 18))
        parts.append(build_sources_block("Objectifs récupérés depuis Chroma", sections.get("objectifs", []), 14))
        parts.append(build_sources_block("Verrous récupérés depuis Chroma", sections.get("verrous", []), 20))
        parts.append(build_sources_block("Méthodes récupérées depuis Chroma", sections.get("methodes", []), 16))
        parts.append(build_sources_block("Résultats récupérés depuis Chroma", sections.get("resultats", []), 16))
        parts.append(build_sources_block("Paramètres récupérés depuis Chroma", sections.get("parametres", []), 12))
        parts.append(build_sources_block("Limites récupérées depuis Chroma", sections.get("limites", []), 12))

        parts.append("")
        parts.append("Réponse attendue exactement avec cette structure :")
        parts.append("")
        parts.append("## Lecture Frascati du dossier")
        parts.append("Explique en 4-6 lignes ce que signifie le score/statut Frascati récupéré depuis Chroma. Précise que c'est à valider humainement.")
        parts.append("")
        parts.append("## Synthèse stratégique du projet")
        parts.append("Synthèse courte et claire du sujet, du problème technique et de l'intérêt R&D potentiel.")
        parts.append("")
        parts.append("## Objectif global reformulé")
        parts.append("Construis un objectif global à partir des objectifs locaux récupérés depuis Chroma.")
        parts.append("")
        parts.append("## Verrous R&D / signaux de verrous")
        parts.append("Pour chaque verrou récupéré :")
        parts.append("- titre technique contextualisé, pas générique ;")
        parts.append("- difficulté technique ;")
        parts.append("- preuves et documents sources nommés ;")
        parts.append("- statut : probable / à vérifier / à valider selon les métadonnées disponibles.")
        parts.append("")
        parts.append("Important pour les verrous :")
        parts.append("- Ne conserve pas tels quels les thèmes génériques issus du NLP.")
        parts.append("- Transforme les signaux génériques en verrous techniques ancrés dans les sources.")
        parts.append("- Ne cite pas uniquement Source 1/Source 2 : ajoute le nom du document quand disponible.")
        parts.append("")
        parts.append("## Démarche expérimentale détectée")
        parts.append("Reformule les méthodes, essais, prototypes, simulations, paramètres.")
        parts.append("")
        parts.append("## Résultats et métriques disponibles")
        parts.append("Liste les résultats et mesures. Sépare les résultats chiffrés des résultats qualitatifs.")
        parts.append("")
        parts.append("## Paramètres et contraintes techniques")
        parts.append("Liste les paramètres importants.")
        parts.append("")
        parts.append("## Comparaison avec le CIR précédent")
        parts.append("Présente les verrous nouveaux, les verrous en évolution, les continuités fortes et les changements techniques par rapport au CIR précédent.")
        parts.append("")
        parts.append("## Points à valider par le consultant")
        parts.append("Questions concrètes à poser pour sécuriser le dossier CIR.")
        parts.append("")
        parts.append("Important : ne cite pas 'JSON NLP'. Dis seulement 'sources indexées' ou 'sources Chroma'.")

        return "\n".join(parts)

    def fallback_without_llm(self, sections: Dict[str, List[Dict[str, Any]]], frascati_summary: Dict[str, Any]) -> str:
        def bullets(sources, n=8):
            if not sources:
                return "- Aucun élément récupéré depuis Chroma."
            return "\n".join(f"- {truncate(source_text(s), 350)}" for s in sources[:n])

        return f"""
## Lecture Frascati du dossier
{frascati_summary.get("explanation")}
Score moyen récupéré : {frascati_summary.get("average_frascati_score")}
Décisions détectées : {frascati_summary.get("decisions_count")}

## Synthèse stratégique du projet
{bullets(sections.get("global", []), 6)}

## Objectif global reformulé
{bullets(sections.get("objectifs", []), 6)}

## Verrous R&D / signaux de verrous
{bullets(sections.get("verrous", []), 12)}

## Démarche expérimentale détectée
{bullets(sections.get("methodes", []), 8)}

## Résultats et métriques disponibles
{bullets(sections.get("resultats", []), 8)}

## Paramètres et contraintes techniques
{bullets(sections.get("parametres", []), 8)}

## Comparaison avec le CIR précédent
Comparaison non disponible en mode fallback sans LLM. Vérifier le rapport cir_memory/cir_memory_comparison_report.json si présent.

## Points à valider par le consultant
- Confirmer les verrous réellement R&D.
- Relier chaque verrou à une preuve technique.
- Vérifier les résultats chiffrés disponibles.
- Séparer contraintes industrielles et incertitudes scientifiques/techniques.
""".strip()

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
        """
        Trouve le nlp_result courant annuel.

        Le backend passe out_dir = storage/.../years/{year}.
        Donc le bon chemin est généralement :
        out_dir/nlp/nlp_result.json
        """
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
        Compare le dossier courant avec le ou les CIR finaux précédents.

        Cette comparaison sert à dire :
        - nouveau verrou potentiel ;
        - évolution / continuité partielle ;
        - continuité forte avec le CIR précédent ;
        - risque de répétition du CIR N-1.

        Elle ne remplace pas la validation consultant.
        """
        try:
            from modules.CIR_MEMORY.cir_memory import load_or_create_cir_memory_comparison

            nlp_path = self._find_current_nlp_result_path()
            if not nlp_path:
                return {
                    "ok": False,
                    "missing": True,
                    "message": "nlp_result.json courant introuvable pour comparer avec le CIR précédent.",
                    "searched_from_out_dir": str(self.out_dir),
                }

            # Signature actuelle de modules.CIR_MEMORY.cir_memory :
            # compare_current_raw_with_cir_memory(organisme, project, year, nlp_result_path)
            report = load_or_create_cir_memory_comparison(
                organisme=self.organisme,
                project=self.project,
                year=self.year,
                nlp_result_path=nlp_path,
            )
            return report if isinstance(report, dict) else {"ok": False, "message": "Rapport CIR mémoire invalide."}

        except TypeError:
            # Compatibilité avec anciennes variantes éventuelles.
            try:
                from modules.CIR_MEMORY.cir_memory import compare_current_raw_with_cir_memory

                nlp_path = self._find_current_nlp_result_path()
                if not nlp_path:
                    return {
                        "ok": False,
                        "missing": True,
                        "message": "nlp_result.json courant introuvable pour comparer avec le CIR précédent.",
                        "searched_from_out_dir": str(self.out_dir),
                    }

                report = compare_current_raw_with_cir_memory(
                    organisme=self.organisme,
                    project=self.project,
                    year=self.year,
                    nlp_result_path=nlp_path,
                )
                return report if isinstance(report, dict) else {"ok": False, "message": "Rapport CIR mémoire invalide."}
            except Exception as e:
                return {"ok": False, "error": str(e), "message": "Impossible de comparer avec le CIR précédent."}

        except Exception as e:
            return {"ok": False, "error": str(e), "message": "Impossible de comparer avec le CIR précédent."}

    def build_cir_memory_prompt_block(self, cir_memory_report: Dict[str, Any]) -> str:
        """
        Bloc injecté dans le prompt LLM pour faire apparaître la comparaison N vs N-1.
        """
        if not isinstance(cir_memory_report, dict) or not cir_memory_report.get("ok"):
            return (
                "## Comparaison avec le CIR précédent\n"
                "Aucune comparaison CIR précédente exploitable. "
                "Ne conclus pas sur la nouveauté annuelle sans validation consultant."
            )

        if not cir_memory_report.get("has_previous_cir"):
            return (
                "## Comparaison avec le CIR précédent\n"
                "Aucun CIR précédent enregistré pour ce projet. "
                "Les verrous doivent être présentés comme potentiels et à valider."
            )

        summary = cir_memory_report.get("summary") or {}
        years = cir_memory_report.get("previous_cir_years_used") or []

        lines = []
        lines.append("## Comparaison avec le CIR précédent")
        lines.append("")
        lines.append("Règle : cette comparaison sert à qualifier la continuité ou la nouveauté des verrous.")
        lines.append("Elle ne doit pas remplacer la validation humaine.")
        lines.append("")
        lines.append(f"Années CIR précédentes utilisées : {years}")
        lines.append(f"Score de nouveauté projet : {summary.get('project_novelty_score')}")
        lines.append(f"Signal contexte Frascati : {summary.get('frascati_context_signal')}")
        lines.append(f"Explication : {summary.get('frascati_context_explanation')}")
        lines.append("")
        lines.append("Synthèse quantitative :")
        lines.append(f"- Verrous comparés : {summary.get('verrou_count')}")
        lines.append(f"- Nouveaux ou non retrouvés : {summary.get('new_verrou_count')}")
        lines.append(f"- Évolutions ou continuités partielles : {summary.get('evolution_verrou_count')}")
        lines.append(f"- Continuités fortes : {summary.get('continuity_verrou_count')}")
        lines.append("")
        lines.append("Comparaisons principales de verrous :")

        for idx, item in enumerate((cir_memory_report.get("verrou_comparisons") or [])[:8], start=1):
            cur = item.get("current_item") or {}
            best = item.get("best_match") or {}
            prev = best.get("previous_candidate") or {}
            dec = item.get("decision") or {}
            lines.append("")
            lines.append(f"[COMPARAISON VERROU {idx}]")
            lines.append(f"- Statut : {dec.get('label') or dec.get('status')}")
            lines.append(f"- Continuité : {dec.get('continuity_score')} | Nouveauté : {dec.get('novelty_score')}")
            lines.append(f"- Verrou courant : {truncate(clean_text(cur.get('text')), 320)}")
            if prev:
                lines.append(f"- Proche CIR précédent ({prev.get('year')}): {truncate(clean_text(prev.get('text')), 300)}")
            else:
                lines.append("- Proche CIR précédent : aucun rapprochement fort.")

        lines.append("")
        lines.append("Dans le rapport final, ajoute une section claire :")
        lines.append("## Comparaison avec le CIR précédent")
        lines.append("- Verrous nouveaux ou non retrouvés.")
        lines.append("- Verrous en évolution ou continuité partielle.")
        lines.append("- Verrous en continuité forte.")
        lines.append("- Changements techniques constatés.")
        lines.append("- Risque de répétition / points à justifier pour l'année courante.")
        lines.append("")
        lines.append("Important : ne dis jamais que le CIR est validé. Écris 'à vérifier par le consultant' si nécessaire.")

        return "\n".join(lines)

    # =====================================================
    # Generate
    # =====================================================

    def generate_diagnostic(self, save: bool = True) -> Dict[str, Any]:
        sections = self.retrieve_all_sections()
        frascati_summary = self.frascati_summary_from_chroma(sections.get("verrous", []))
        ai_detection_report = self.load_ai_detection_report()
        cir_memory_report = self.load_cir_memory_report()
        style_memory_report = self.load_style_memory_context(sections)

        if self.llm is not None:
            prompt = (
                self.build_prompt(sections, frascati_summary, style_memory_report=style_memory_report, cir_memory_report=cir_memory_report)
                + "\n\n## Contrôle IA documentaire\n"
                + self.ai_detection_prompt_block(ai_detection_report)
            )
            content = self.llm.generate(
                prompt,
                temperature=0.08,
                max_output_tokens=2800,
                retries=3,
            )
        else:
            content = self.fallback_without_llm(sections, frascati_summary)

        report = {
            "ok": True,
            "mode": "ennodiagnostic_chroma_only_style_memory",
            "principle": (
                "Chroma is the source of truth; style memory is used only for CIR writing style, "
                "not as factual evidence."
            ),
            "organisme": self.organisme,
            "project": self.project,
            "year": self.year,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "frascati_summary": frascati_summary,
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
                "status": "ok",
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

        return report

    # Compatibilité avec anciens appels
    def generate_diagnostic_complet(self, save: bool = True) -> Dict[str, Any]:
        return self.generate_diagnostic(save=save)

    def generate_all_sections(self, save: bool = True) -> Dict[str, Any]:
        return self.generate_diagnostic(save=save)
