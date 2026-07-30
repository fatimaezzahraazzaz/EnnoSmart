# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(r"C:\EnnoSmart")
PACK_DIR = Path(__file__).resolve().parent


def backup(path: Path) -> None:
    bak = path.with_suffix(path.suffix + ".bak_memory_v2_global")
    if path.exists() and not bak.exists():
        shutil.copy2(path, bak)


def copy_with_backup(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    backup(dst)
    shutil.copy2(src, dst)


def patch_ai_content_detector() -> None:
    path = ROOT / "agents" / "EnnoDiagnostic" / "ai_content_detector.py"
    if not path.exists():
        print("[WARN] introuvable", path)
        return
    text = path.read_text(encoding="utf-8")

    if "from modules.common.project_path_resolver import resolve_project_root" not in text:
        text = text.replace(
            "from dotenv import load_dotenv\n",
            "from dotenv import load_dotenv\n\n"
            "try:\n"
            "    from modules.common.project_path_resolver import resolve_project_root\n"
            "except Exception:\n"
            "    resolve_project_root = None\n",
        )

    old_loader = '''        self.organisme = safe_name(organisme)
        self.project = safe_name(project)

        self.base_dir = base_dir or Path(_str_env("ENNOSMART_BASE_DIR", r"C:\\EnnoSmart"))
        self.project_root = (
            self.base_dir
            / "storage"
            / "organismes"
            / self.organisme
            / "projects"
            / self.project
        )

        self.nlp_path = self.project_root / "nlp" / "nlp_result.json"
        self.rag_chunks_path = self.project_root / "rag" / "chunks.json"
        self.processed_dir = self.project_root / "documents" / "processed"'''
    new_loader = '''        self.organisme = safe_name(organisme)
        self.project = safe_name(project)

        self.base_dir = base_dir or Path(_str_env("ENNOSMART_BASE_DIR", r"C:\\EnnoSmart"))
        if resolve_project_root is not None:
            self.project_root = resolve_project_root(self.base_dir, self.organisme, project, create_if_missing=False)
            self.project = self.project_root.name
        else:
            self.project_root = (
                self.base_dir
                / "storage"
                / "organismes"
                / self.organisme
                / "projects"
                / self.project
            )

        self.nlp_path = self.project_root / "nlp" / "nlp_result.json"
        self.rag_chunks_path = self.project_root / "rag" / "chunks.json"
        self.processed_dir = self.project_root / "documents" / "processed"'''
    if old_loader in text:
        text = text.replace(old_loader, new_loader)
    else:
        print("[WARN] bloc loader non trouvé exactement")

    old_service = '''        self.organisme = safe_name(organisme)
        self.project = safe_name(project)

        self.base_dir = Path(_str_env("ENNOSMART_BASE_DIR", r"C:\\EnnoSmart"))
        self.project_root = (
            self.base_dir
            / "storage"
            / "organismes"
            / self.organisme
            / "projects"
            / self.project
        )

        self.diagnostics_dir = self.project_root / "diagnostics"'''
    new_service = '''        self.organisme = safe_name(organisme)
        self.project = safe_name(project)

        self.base_dir = Path(_str_env("ENNOSMART_BASE_DIR", r"C:\\EnnoSmart"))
        if resolve_project_root is not None:
            self.project_root = resolve_project_root(self.base_dir, self.organisme, project, create_if_missing=False)
            self.project = self.project_root.name
        else:
            self.project_root = (
                self.base_dir
                / "storage"
                / "organismes"
                / self.organisme
                / "projects"
                / self.project
            )

        self.diagnostics_dir = self.project_root / "diagnostics"'''
    if old_service in text:
        text = text.replace(old_service, new_service)
    else:
        print("[WARN] bloc service non trouvé exactement")

    backup(path)
    path.write_text(text, encoding="utf-8")
    print("[OK] ai_content_detector.py")


def patch_consultant_verrou_synthesizer() -> None:
    path = ROOT / "agents" / "EnnoDiagnostic" / "consultant_verrou_synthesizer.py"
    if not path.exists():
        print("[WARN] introuvable", path)
        return
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        '"frascati_score": score,',
        '"signal_priority_score": score,\n                "score_label": "score de priorité du signal, pas score Frascati final",',
    )
    text = text.replace(
        'src_json["frascati_score"] = score',
        'src_json["signal_priority_score"] = score\n        src_json["score_label"] = "score de priorité du signal, pas score Frascati final"',
    )
    old = '        lines.append(f"   - Frascati : {decision} ; score {score if score is not None else \'non disponible\'}.")'
    new = '        lines.append(f"   - Signal NLP : {decision} ; score de priorité {score if score is not None else \'non disponible\'} (ce n’est pas un score Frascati final).")'
    if old in text:
        text = text.replace(old, new)
    else:
        text = re.sub(
            r'lines\.append\(f"   - Frascati\s*:\s*\{decision\}\s*;\s*score\s*\{score if score is not None else [^}]+?\}\."\)',
            new,
            text,
        )
    backup(path)
    path.write_text(text, encoding="utf-8")
    print("[OK] consultant_verrou_synthesizer.py")


def patch_ennodiagnostic_agent() -> None:
    path = ROOT / "agents" / "EnnoDiagnostic" / "ennodiagnostic_agent.py"
    if not path.exists():
        print("[WARN] introuvable", path)
        return
    text = path.read_text(encoding="utf-8")

    if "def build_memory_v2_retriever(" not in text:
        marker = "# =========================================================\n# Agent EnnoDiagnostic"
        helper = '''def build_memory_v2_retriever(organisme: str, project: str, year: str):
    try:
        from modules.EXPERIENCE_MEMORY.memory_v2_retriever import ExperienceMemoryV2Retriever
        return ExperienceMemoryV2Retriever(organisme=organisme, project=project, year=year)
    except Exception as exc:
        print(f"[EnnoDiagnostic][MEMORY_V2][WARN] impossible de charger Memory V2 : {exc}")
        return None


def normalize_signal_score_vocabulary(content: str) -> str:
    content = repair_mojibake(content)
    if not content:
        return ""
    content = re.sub(
        r"Frascati\s*:\s*([^;\n]+)\s*;\s*score\s*([0-9.,]+|non disponible)\s*\.",
        r"Signal NLP : \1 ; score de priorité \2 (ce n’est pas un score Frascati final).",
        content,
        flags=re.I,
    )
    return content


'''
        text = text.replace(marker, helper + marker)

    if "self.memory_v2 = build_memory_v2_retriever" not in text:
        text = text.replace(
            "        self.llm = build_llm(self.model) if self.use_llm else None\n",
            "        self.llm = build_llm(self.model) if self.use_llm else None\n"
            "        self.memory_v2 = build_memory_v2_retriever(\n"
            "            organisme=self.organisme,\n"
            "            project=self.project,\n"
            "            year=self.year,\n"
            "        )\n",
        )

    if "def load_experience_memory_v2_context(" not in text:
        marker = "    # =====================================================\n    # Style memory\n    # ====================================================="
        method = '''    # =====================================================
    # Experience Memory V2
    # =====================================================

    def load_experience_memory_v2_context(self, sections: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        if not getattr(self, "memory_v2", None):
            return {
                "ok": False,
                "message": "Memory V2 indisponible.",
                "prompt_block": "Mémoire d'expérience V2 indisponible.",
            }
        try:
            return self.memory_v2.retrieve_for_diagnostic(sections)
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "message": "Erreur récupération Memory V2.",
                "prompt_block": "Mémoire d'expérience V2 indisponible.",
            }

'''
        text = text.replace(marker, method + marker)

    text = text.replace(
        "        cir_memory_report: Optional[Dict[str, Any]],\n    ) -> str:",
        "        cir_memory_report: Optional[Dict[str, Any]],\n"
        "        experience_memory_report: Optional[Dict[str, Any]] = None,\n"
        "    ) -> str:",
    )

    if "Mémoire d'expérience V2 :" not in text:
        target = '''        parts.append("Mémoire de style CIR courte :")
        parts.append(truncate(self._style_memory_for_role(style_memory_report, "verrou", max_chars=1200), 1200))
        parts.append("")'''
        text = text.replace(target, target + '''
        if isinstance(experience_memory_report, dict) and experience_memory_report.get("ok"):
            parts.append("Mémoire d'expérience V2 :")
            parts.append(truncate(experience_memory_report.get("prompt_block"), 4200))
            parts.append("")
        else:
            parts.append("Mémoire d'expérience V2 : aucune mémoire exploitable.")
            parts.append("")''')
        target2 = '''            parts2.append("Mémoire de style CIR courte :")
            parts2.append(truncate(self._style_memory_for_role(style_memory_report, "verrou", max_chars=900), 900))
            parts2.append("")'''
        text = text.replace(target2, target2 + '''
            if isinstance(experience_memory_report, dict) and experience_memory_report.get("ok"):
                parts2.append("Mémoire d'expérience V2 courte :")
                parts2.append(truncate(experience_memory_report.get("prompt_block"), 2200))
                parts2.append("")
            else:
                parts2.append("Mémoire d'expérience V2 courte : aucune mémoire exploitable.")
                parts2.append("")''')

    text = text.replace(
        "            ai_detection_report=None,\n            cir_memory_report=cir_memory_report,\n        )",
        "            ai_detection_report=None,\n"
        "            cir_memory_report=cir_memory_report,\n"
        "            experience_memory_report=None,\n"
        "        )",
    )

    if "experience_memory_report = self.load_experience_memory_v2_context(sections)" not in text:
        text = text.replace(
            "        style_memory_report = self.load_style_memory_context(sections)\n        self._last_style_memory_report = style_memory_report\n",
            "        style_memory_report = self.load_style_memory_context(sections)\n"
            "        self._last_style_memory_report = style_memory_report\n"
            "        experience_memory_report = self.load_experience_memory_v2_context(sections)\n",
        )

    text = text.replace(
        "                cir_memory_report=cir_memory_report,\n            )",
        "                cir_memory_report=cir_memory_report,\n"
        "                experience_memory_report=experience_memory_report,\n"
        "            )",
    )

    text = text.replace(
        "        content = normalize_report_vocabulary(content)\n",
        "        content = normalize_signal_score_vocabulary(normalize_report_vocabulary(content))\n",
    )

    if "for _v in llm_reformulated_verrous:" not in text:
        target = '''        frascati_justification_text = extract_markdown_section(content, "Justification Frascati du score")
        diagnostic_sections = build_diagnostic_sections(content)'''
        replacement = '''        frascati_justification_text = extract_markdown_section(content, "Justification Frascati du score")
        for _v in llm_reformulated_verrous:
            if isinstance(_v, dict):
                if _v.get("score") is not None and _v.get("signal_priority_score") is None:
                    _v["signal_priority_score"] = _v.get("score")
                    _v["score_label"] = "score de priorité du signal, pas score Frascati final"
                sj = _v.get("source_json") if isinstance(_v.get("source_json"), dict) else {}
                if sj and sj.get("frascati_score") is not None and sj.get("signal_priority_score") is None:
                    sj["signal_priority_score"] = sj.get("frascati_score")
                    sj["score_label"] = "score de priorité du signal, pas score Frascati final"
                    sj["frascati_score"] = None
                    _v["source_json"] = sj
        diagnostic_sections = build_diagnostic_sections(content)'''
        text = text.replace(target, replacement)

    if '"experience_memory_v2_report"' not in text:
        target = '''            "style_memory_report": {
                "ok": style_memory_report.get("ok"),
                "memory_path": style_memory_report.get("memory_path"),
                "stats": style_memory_report.get("stats"),
                "examples_count": style_memory_report.get("examples_count", 0),
                "examples_by_role_count": style_memory_report.get("examples_by_role_count", {}),
                "principle": style_memory_report.get("principle"),
                "error": style_memory_report.get("error"),
                "message": style_memory_report.get("message"),
            },'''
        text = text.replace(target, target + '''
            "experience_memory_v2_report": {
                "ok": experience_memory_report.get("ok") if isinstance(experience_memory_report, dict) else False,
                "source": experience_memory_report.get("source") if isinstance(experience_memory_report, dict) else None,
                "similar_count": experience_memory_report.get("similar_count", 0) if isinstance(experience_memory_report, dict) else 0,
                "style_examples_count": experience_memory_report.get("style_examples_count", 0) if isinstance(experience_memory_report, dict) else 0,
                "principle": experience_memory_report.get("principle") if isinstance(experience_memory_report, dict) else None,
                "error": experience_memory_report.get("error") if isinstance(experience_memory_report, dict) else None,
                "message": experience_memory_report.get("message") if isinstance(experience_memory_report, dict) else None,
            },''')

    if '"experience_memory_v2_used"' not in text:
        text = text.replace(
            '                "cir_memory_available": bool(isinstance(cir_memory_report, dict) and (cir_memory_report.get("ok") or cir_memory_report.get("has_previous_cir") or cir_memory_report.get("summary"))),\n',
            '                "cir_memory_available": bool(isinstance(cir_memory_report, dict) and (cir_memory_report.get("ok") or cir_memory_report.get("has_previous_cir") or cir_memory_report.get("summary"))),\n'
            '                "experience_memory_v2_used": bool(isinstance(experience_memory_report, dict) and experience_memory_report.get("ok")),\n'
            '                "experience_memory_v2_similar_count": experience_memory_report.get("similar_count", 0) if isinstance(experience_memory_report, dict) else 0,\n',
        )

    backup(path)
    path.write_text(text, encoding="utf-8")
    print("[OK] ennodiagnostic_agent.py")


def main():
    copy_with_backup(PACK_DIR / "project_path_resolver.py", ROOT / "modules" / "common" / "project_path_resolver.py")
    copy_with_backup(PACK_DIR / "style_memory.py", ROOT / "modules" / "CIR_STYLE_MEMORY" / "style_memory.py")
    patch_ai_content_detector()
    patch_consultant_verrou_synthesizer()
    patch_ennodiagnostic_agent()
    print("\nTerminé. Redémarre backend puis relance /diagnostic/run.")


if __name__ == "__main__":
    main()
