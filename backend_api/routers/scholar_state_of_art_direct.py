# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import importlib
import json
import re
import time

from fastapi import APIRouter, Body, HTTPException, Query

router = APIRouter(prefix="/projects", tags=["EnnoScholar - State of Art"])


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _clean(value: Any, max_chars: int = 4000) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    if max_chars and len(text) > max_chars:
        return text[:max_chars].rsplit(" ", 1)[0] + "..."
    return text


def _tag(article: Dict[str, Any]) -> str:
    return _clean(article.get("tag") or article.get("tag_article") or article.get("classification"), 100)


def _is_direct_connexe(article: Dict[str, Any]) -> bool:
    return _tag(article).lower() in {"direct", "connexe"}


def _authors(article: Dict[str, Any]) -> str:
    authors = article.get("authors") or []
    if isinstance(authors, list):
        return ", ".join([_clean(a, 120) for a in authors if _clean(a, 120)])
    return _clean(authors, 500)


def _best_verrou_title_from_articles(verrou: Dict[str, Any], articles: List[Dict[str, Any]]) -> str:
    current = _clean(verrou.get("verrou_title") or verrou.get("title"), 500)

    if current and not current.lower().startswith("verrou lié") and not current.lower().startswith("verrou lie"):
        return current

    for article in articles:
        validation = article.get("verrou_scientific_validation")
        if isinstance(validation, dict):
            title = _clean(validation.get("verrou_title"), 500)
            if title:
                return title

        intent = article.get("scientific_intent")
        if isinstance(intent, dict):
            title = _clean(intent.get("verrou_title"), 500)
            if title:
                return title

    return current or "Verrou scientifique"


def _load_llm_client():
    errors = []
    for module_name in [
        "modules.LLM.llm_client",
        "modules.LLM",
        "modules.llm.llm_client",
        "modules.llm",
        "agents.llm.llm_client",
        "agents.LLM.llm_client",
        "llm.llm_client",
        "llm_client",
    ]:
        try:
            mod = importlib.import_module(module_name)
            client_cls = getattr(mod, "LLMClient")
            return client_cls
        except Exception as exc:
            errors.append(f"{module_name}: {exc}")

    raise RuntimeError("LLMClient introuvable. Modules testés : " + " | ".join(errors))


def _call_llm(prompt: str, max_output_tokens: int = 3500, temperature: float = 0.08) -> str:
    Client = _load_llm_client()
    client = Client()
    return _clean(
        client.generate(
            prompt=prompt,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            retries=1,
        ),
        60000,
    )


def _references_from_articles(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    refs = []
    for i, article in enumerate(articles, start=1):
        refs.append({
            "citation_id": f"A{i}",
            "label": f"[A{i}]",
            "title": _clean(article.get("title"), 600),
            "authors": _authors(article),
            "year": article.get("year"),
            "tag": _tag(article),
            "doi": _clean(article.get("doi"), 300),
            "url": _clean(article.get("url"), 1000),
            "source": _clean(article.get("source"), 200),
            "relevance_score": article.get("relevance_score"),
        })
    return refs


def _articles_block(articles: List[Dict[str, Any]]) -> str:
    blocks = []
    for i, article in enumerate(articles, start=1):
        block = "\n".join([
            f"[A{i}] {_clean(article.get('title'), 700)}",
            f"Tag consultant : {_tag(article)}",
            f"Année : {article.get('year') or ''}",
            f"Auteurs : {_authors(article)}",
            f"Source : {_clean(article.get('source'), 200)}",
            f"DOI/URL : {_clean(article.get('doi') or article.get('url'), 1000)}",
            f"Raison de pertinence : {_clean(article.get('reason'), 1000)}",
            "Résumé :",
            _clean(article.get("abstract"), 2500),
        ])
        blocks.append(block)
    return "\n\n".join(blocks)


def _style_memory_module():
    for module_name in [
        "modules.CIR_STYLE_MEMORY.style_memory",
        "modules.CIR_STYLE_MEMORY",
        "modules.cir_style_memory.style_memory",
        "modules.cir_style_memory",
        "style_memory",
    ]:
        try:
            return importlib.import_module(module_name)
        except Exception:
            continue
    return None


def _keyword_set(text: str) -> set[str]:
    text = _clean(text, 20000).lower()
    text = re.sub(r"[^\wÀ-ÿ'-]+", " ", text)
    stop = {
        "avec", "dans", "pour", "plus", "moins", "entre", "comme", "cette",
        "cela", "ainsi", "afin", "sont", "nous", "notre", "leur", "leurs",
        "des", "les", "une", "aux", "sur", "par", "que", "qui", "quoi",
        "dont", "projet", "travaux", "article", "articles", "verrou"
    }
    return {w for w in text.split() if len(w) >= 4 and w not in stop}


def _manual_style_examples_from_all_memories(query_text: str, top_k: int = 3) -> list[dict]:
    root = Path(r"C:\EnnoSmart") / "storage" / "organismes"
    if not root.exists():
        return []

    query_words = _keyword_set(query_text)
    scored = []

    for path in root.glob("*/cir_style_memory/style_memory.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        for ex in data.get("examples") or []:
            if not isinstance(ex, dict):
                continue
            if str(ex.get("role") or "") != "etat_art":
                continue

            txt = _clean(ex.get("text"), 3000)
            if len(txt) < 120:
                continue

            ex_words = _keyword_set(txt)
            overlap = len(query_words & ex_words) / max(1, len(query_words | ex_words))
            if overlap <= 0:
                overlap = 0.01

            scored.append((overlap, ex))

    scored.sort(key=lambda x: x[0], reverse=True)

    out = []
    seen = set()
    for score, ex in scored:
        eid = ex.get("example_id") or _clean(ex.get("text"), 100)
        if eid in seen:
            continue
        seen.add(eid)
        y = dict(ex)
        y["style_match_score"] = round(float(score), 4)
        out.append(y)
        if len(out) >= top_k:
            break

    return out


def _build_manual_style_block(examples: list[dict], max_chars_per_example: int = 900) -> str:
    if not examples:
        return "Aucun exemple de style CIR disponible."

    lines = [
        "EXEMPLES DE STYLE CIR VALIDÉS",
        "Ces exemples servent uniquement à imiter le style, la structure argumentative et le vocabulaire.",
        "Ils ne doivent pas être copiés et leurs faits ne doivent pas être réutilisés comme faits du nouveau dossier.",
    ]

    for i, ex in enumerate(examples, 1):
        lines.append("")
        lines.append(
            f"[STYLE {i}] rôle={ex.get('role')} | projet={ex.get('project')} | année={ex.get('year')} | score={ex.get('style_match_score')}"
        )
        if ex.get("section_title"):
            lines.append(f"Titre section : {ex.get('section_title')}")
        lines.append("Extrait de style :")
        lines.append(_clean(ex.get("text"), max_chars_per_example))

    return "\n".join(lines).strip()


def _get_state_art_style_context(payload: Dict[str, Any], verrou: Dict[str, Any], articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    title = _best_verrou_title_from_articles(verrou, articles)
    query_text = "\n".join(
        [title]
        + [_clean(a.get("title"), 500) for a in articles]
        + [_clean(a.get("abstract"), 1000) for a in articles]
    )

    organisme = _clean(payload.get("organisme") or payload.get("organization") or payload.get("client") or payload.get("organisme_name"), 200)
    project = _clean(payload.get("project") or payload.get("project_name") or payload.get("projet"), 200)

    domain = payload.get("domain_detection") or {}
    domain_key = "unknown"
    if isinstance(domain, dict):
        domain_key = _clean(domain.get("domain_key") or domain.get("main_domain_code") or domain.get("domain_code_niv2") or domain.get("domain_code_niv1") or "unknown", 200)

    examples = []
    source = "none"

    mod = _style_memory_module()
    if mod is not None and organisme:
        try:
            examples = mod.retrieve_style_examples(
                organisme=organisme,
                target_role="etat_art",
                query_text=query_text,
                project=project,
                top_k=3,
                target_domain_key=domain_key or "unknown",
                strict_domain=False,
            )
            source = "style_memory.retrieve_style_examples"
        except Exception as exc:
            examples = []
            source = f"style_memory_error: {exc}"

    if not examples:
        examples = _manual_style_examples_from_all_memories(query_text=query_text, top_k=3)
        if examples:
            source = "manual_scan_all_style_memories"

    if mod is not None and hasattr(mod, "build_style_block"):
        try:
            block = mod.build_style_block(examples, max_chars_per_example=900)
        except Exception:
            block = _build_manual_style_block(examples)
    else:
        block = _build_manual_style_block(examples)

    return {
        "style_examples": examples,
        "style_examples_count": len(examples),
        "style_block": block,
        "style_memory_source": source,
        "style_memory_used": bool(examples),
        "organisme_used": organisme,
        "project_used": project,
        "domain_key_used": domain_key,
        "warning": "Mémoire de style uniquement : ne jamais utiliser ces exemples comme preuves factuelles." if examples else "Aucun exemple de style CIR trouvé.",
    }


def _project_context_block(payload: Dict[str, Any], verrou: Dict[str, Any]) -> str:
    parts = []
    for k in ["organisme", "project", "year"]:
        if payload.get(k):
            parts.append(f"{k}: {payload.get(k)}")

    diag = payload.get("diagnostic_context") or {}
    if isinstance(diag, dict) and diag:
        parts.append("Contexte diagnostic: " + _clean(json.dumps(diag, ensure_ascii=False), 2500))

    intent = verrou.get("scientific_intent") or {}
    if isinstance(intent, dict) and intent:
        parts.append("Intention scientifique: " + _clean(json.dumps(intent, ensure_ascii=False), 2500))

    signals = verrou.get("source_signals") or []
    if signals:
        parts.append("Signaux sources: " + _clean(json.dumps(signals, ensure_ascii=False), 3000))

    return "\n".join(parts) if parts else (
        "Contexte projet limité : aucune caractéristique détaillée du projet n’est fournie ici. "
        "Ne pas affirmer les matériaux, la pression, le gaz, la géométrie ou le mode oil-free ; "
        "les formuler comme hypothèses à valider consultant."
    )


def _json_schema_text() -> str:
    return """
{
  "verrou_title": "...",
  "positionnement": "paragraphe court",
  "travaux_directs": [
    {
      "article_ref": "A1",
      "article_title": "...",
      "synthesis": "apport scientifique/technique",
      "limits_for_project": "limite ou transposition à valider"
    }
  ],
  "travaux_connexes": [
    {
      "article_ref": "A5",
      "article_title": "...",
      "synthesis": "apport indirect",
      "limits_for_project": "limite ou précaution"
    }
  ],
  "limites_etat_art": [
    "limite 1",
    "limite 2"
  ],
  "gap_scientifique": "paragraphe CIR prudent",
  "hypotheses_a_valider": [
    "hypothèse 1",
    "hypothèse 2"
  ],
  "references": [
    {
      "article_ref": "A1",
      "reference": "Titre — auteurs — année"
    }
  ]
}
""".strip()


def _build_structured_prompt(payload: Dict[str, Any], verrou: Dict[str, Any], articles: List[Dict[str, Any]], style_ctx: Dict[str, Any]) -> str:
    title = _best_verrou_title_from_articles(verrou, articles)

    return f"""
Tu es EnnoScholar, agent scientifique du système EnnoSmart.

OBJECTIF :
Rédiger un état de l’art structuré pour un dossier CIR, après sélection des articles par le consultant.

IMPORTANT :
Tu dois répondre uniquement en JSON valide.
Tu ne dois pas écrire de Markdown.
Tu ne dois pas ajouter de texte avant ou après le JSON.

VERROU À TRAITER :
{title}

CONTEXTE PROJET :
{_project_context_block(payload, verrou)}

ARTICLES AUTORISÉS :
{_articles_block(articles)}

MÉMOIRE DE STYLE CIR — STYLE UNIQUEMENT :
{style_ctx.get("style_block") or "Aucun exemple de style CIR disponible."}

RÈGLES STRICTES :
- Les articles Direct/Connexe sont les seules sources scientifiques factuelles.
- La mémoire de style CIR sert uniquement au ton, à la structure et au niveau de détail.
- Ne copie jamais les anciens CIR.
- Ne réutilise jamais un fait d’un ancien CIR comme fait du dossier actuel.
- Ne présente jamais comme fait projet une information présente seulement dans un article.
- Si une caractéristique projet manque, formule-la dans "hypotheses_a_valider".
- Ne conclus pas définitivement à l’éligibilité CIR.
- Distingue les travaux directs et les travaux connexes.
- Les limites doivent expliquer pourquoi les articles ne suffisent pas entièrement pour le cas projet.
- Chaque article utilisé doit garder sa référence A1, A2, etc.

FORMAT JSON OBLIGATOIRE :
{_json_schema_text()}
""".strip()


def _extract_json_object(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?", "", raw.strip(), flags=re.IGNORECASE).strip()
    raw = re.sub(r"```$", "", raw.strip()).strip()

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidate = raw[start:end + 1]
        data = json.loads(candidate)
        if isinstance(data, dict):
            return data

    raise ValueError("Réponse LLM non JSON valide.")


def _repair_json_with_llm(raw: str, title: str) -> Dict[str, Any]:
    repair_prompt = f"""
Répare la réponse suivante pour obtenir uniquement un JSON valide conforme au schéma.
Ne change pas le fond si possible.
Ne mets aucun Markdown.

VERROU :
{title}

SCHÉMA :
{_json_schema_text()}

RÉPONSE À RÉPARER :
{_clean(raw, 12000)}
""".strip()
    repaired = _call_llm(repair_prompt, max_output_tokens=3500, temperature=0.0)
    return _extract_json_object(repaired)


def _normalize_structured(data: Dict[str, Any], title: str, refs: List[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(data or {})
    out["verrou_title"] = _clean(out.get("verrou_title") or title, 800)

    for key in ["positionnement", "gap_scientifique"]:
        out[key] = _clean(out.get(key), 6000)

    for key in ["travaux_directs", "travaux_connexes", "references"]:
        if not isinstance(out.get(key), list):
            out[key] = []

    for key in ["limites_etat_art", "hypotheses_a_valider"]:
        arr = out.get(key)
        if not isinstance(arr, list):
            arr = []
        out[key] = [_clean(x, 1500) for x in arr if _clean(x, 1500)]

    if not out["references"]:
        out["references"] = [
            {"article_ref": r["citation_id"], "reference": f"{r.get('title')} — {r.get('authors')} — {r.get('year')}"}
            for r in refs
        ]

    return out


def _plain_from_structured(s: Dict[str, Any]) -> str:
    lines = []
    lines.append(f"État de l’art — {s.get('verrou_title')}")
    lines.append("")
    lines.append("1. Positionnement du verrou")
    lines.append(_clean(s.get("positionnement"), 8000))
    lines.append("")

    lines.append("2. Travaux directement liés")
    for item in s.get("travaux_directs") or []:
        if not isinstance(item, dict):
            continue
        lines.append(f"{_clean(item.get('article_ref'), 20)} — {_clean(item.get('article_title'), 500)}")
        if item.get("synthesis"):
            lines.append("Synthèse : " + _clean(item.get("synthesis"), 3000))
        if item.get("limits_for_project"):
            lines.append("Limite / transposition : " + _clean(item.get("limits_for_project"), 2500))
        lines.append("")

    lines.append("3. Travaux connexes utiles")
    for item in s.get("travaux_connexes") or []:
        if not isinstance(item, dict):
            continue
        lines.append(f"{_clean(item.get('article_ref'), 20)} — {_clean(item.get('article_title'), 500)}")
        if item.get("synthesis"):
            lines.append("Synthèse : " + _clean(item.get("synthesis"), 3000))
        if item.get("limits_for_project"):
            lines.append("Limite / transposition : " + _clean(item.get("limits_for_project"), 2500))
        lines.append("")

    lines.append("4. Limites de l’état de l’art")
    for x in s.get("limites_etat_art") or []:
        lines.append("- " + _clean(x, 1500))
    lines.append("")

    lines.append("5. Gap scientifique pour le dossier CIR")
    lines.append(_clean(s.get("gap_scientifique"), 8000))
    lines.append("")

    lines.append("6. Hypothèses à valider consultant")
    for x in s.get("hypotheses_a_valider") or []:
        lines.append("- " + _clean(x, 1500))
    lines.append("")

    lines.append("7. Références mobilisées")
    for r in s.get("references") or []:
        if isinstance(r, dict):
            lines.append(f"- [{_clean(r.get('article_ref'), 20)}] {_clean(r.get('reference'), 1000)}")

    return "\n".join([x for x in lines if x is not None]).strip()


def _citation_guard_plain(text: str, article_count: int) -> Dict[str, Any]:
    found = sorted(set(re.findall(r"\[?A(\d+)\]?", text or "")), key=lambda x: int(x))
    unknown = [f"A{x}" for x in found if int(x) < 1 or int(x) > article_count]
    return {"ok": len(unknown) == 0 and bool(found), "used_citations": [f"A{x}" for x in found], "unknown_citations": unknown}


@router.post("/{project_id}/scholar/state-of-art/write-from-selection")
def write_state_of_art_from_frontend_selection(
    project_id: int,
    payload: Dict[str, Any] = Body(...),
    writer_mode: str = Query("llm"),
):
    writer_mode = "llm"

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload invalide.")

    verrous = payload.get("verrous") or []
    if not isinstance(verrous, list) or not verrous:
        raise HTTPException(status_code=400, detail="Aucun verrou reçu.")

    root = _repo_root()
    out_dir = root / "outputs" / "frontend_state_of_art" / f"project_{project_id}" / str(int(time.time()))
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_json(out_dir / "selected_articles_from_frontend.json", payload)

    results = []

    for verrou in verrous:
        if not isinstance(verrou, dict):
            continue

        raw_articles = verrou.get("selected_articles") or []
        if not isinstance(raw_articles, list):
            raw_articles = []

        articles = [a for a in raw_articles if isinstance(a, dict) and _is_direct_connexe(a)]
        if not articles:
            continue

        title = _best_verrou_title_from_articles(verrou, articles)
        refs = _references_from_articles(articles)
        style_ctx = _get_state_art_style_context(payload, verrou, articles)
        prompt = _build_structured_prompt(payload, verrou, articles, style_ctx=style_ctx)

        try:
            raw = _call_llm(prompt, max_output_tokens=4200, temperature=0.08)
            try:
                structured = _extract_json_object(raw)
            except Exception:
                structured = _repair_json_with_llm(raw, title)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    "Erreur LLM pendant la rédaction structurée de l'état de l'art. "
                    "Aucun fallback template n'a été utilisé. "
                    f"Détail : {repr(exc)}"
                ),
            )

        structured = _normalize_structured(structured, title, refs)
        draft = _plain_from_structured(structured)
        guard = _citation_guard_plain(draft, len(articles))

        result = {
            "verrou_id": verrou.get("verrou_id"),
            "verrou_title": title,
            "scientific_intent": {**(verrou.get("scientific_intent") if isinstance(verrou.get("scientific_intent"), dict) else {}), "verrou_title": title},
            "selected_articles_count": len(articles),
            "citation_articles": refs,
            "format": "structured_json",
            "structured_state_of_art": structured,
            "draft": draft,
            "llm_used": True,
            "fallback_used": False,
            "style_memory": {
                "used": style_ctx.get("style_memory_used"),
                "source": style_ctx.get("style_memory_source"),
                "examples_count": style_ctx.get("style_examples_count"),
                "organisme_used": style_ctx.get("organisme_used"),
                "project_used": style_ctx.get("project_used"),
                "domain_key_used": style_ctx.get("domain_key_used"),
                "warning": style_ctx.get("warning"),
            },
            "state_of_art": {
                "mode": "llm",
                "format": "structured_json",
                "llm_used": True,
                "fallback_used": False,
                "structured": structured,
                "draft": draft,
                "references": refs,
                "citation_guard": guard,
                "warnings": [] if guard.get("ok") else ["Vérifier les citations générées par le LLM."],
            },
        }
        results.append(result)

    if not results:
        raise HTTPException(status_code=400, detail="Aucun verrou avec articles Direct/Connexe n'a été reçu pour la rédaction.")

    report = {
        "agent": "EnnoScholar",
        "version": "v56_structured_state_of_art_chat",
        "mode": "write-selection",
        "writer_mode": "llm",
        "format": "structured_json",
        "llm_used": True,
        "fallback_used": False,
        "project_id": project_id,
        "organisme": payload.get("organisme"),
        "project": payload.get("project"),
        "year": payload.get("year"),
        "verrous_written": len(results),
        "results": results,
        "outputs": {
            "selection_payload": str(out_dir / "selected_articles_from_frontend.json"),
            "state_of_art_report": str(out_dir / "ennoscholar_state_of_art_report.json"),
        },
    }

    _write_json(out_dir / "ennoscholar_state_of_art_report.json", report)
    return report


@router.post("/{project_id}/scholar/state-of-art/chat")
def chat_update_state_of_art_for_verrou(project_id: int, payload: Dict[str, Any] = Body(...)):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload invalide.")

    instruction = _clean(payload.get("consultant_instruction"), 4000)
    if not instruction:
        raise HTTPException(status_code=400, detail="Instruction consultant vide.")

    current = payload.get("current_state_of_art") or payload.get("structured_state_of_art")
    if not isinstance(current, dict):
        raise HTTPException(status_code=400, detail="current_state_of_art doit être un objet JSON.")

    articles = payload.get("selected_articles") or payload.get("articles") or []
    if not isinstance(articles, list):
        articles = []
    articles = [a for a in articles if isinstance(a, dict)]

    title = _clean(payload.get("verrou_title") or current.get("verrou_title"), 800) or "Verrou scientifique"
    refs = _references_from_articles(articles) if articles else []

    prompt = f"""
Tu es EnnoScholar. Tu modifies uniquement l'état de l'art structuré du verrou demandé.

RÈGLES :
- Réponds uniquement en JSON valide.
- Ne mets aucun Markdown.
- Conserve la structure JSON.
- Applique la consigne consultant sans inventer.
- Si le consultant demande de moins utiliser un article, réduis son importance mais ne supprime pas les références utiles.
- N'ajoute pas de sources absentes.
- Les articles restent les seules sources factuelles.
- Ne conclus pas à l'éligibilité CIR.

VERROU :
{title}

CONSIGNE CONSULTANT :
{instruction}

ARTICLES DISPONIBLES :
{_articles_block(articles)}

ÉTAT DE L'ART ACTUEL :
{json.dumps(current, ensure_ascii=False, indent=2)}

FORMAT DE SORTIE :
{{
  "updated_state_of_art": {_json_schema_text()},
  "changes_summary": [
    "changement 1",
    "changement 2"
  ]
}}
""".strip()

    try:
        raw = _call_llm(prompt, max_output_tokens=4500, temperature=0.08)
        try:
            data = _extract_json_object(raw)
        except Exception:
            data = _repair_json_with_llm(raw, title)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur LLM pendant le chat d'amélioration du verrou : {repr(exc)}")

    updated = data.get("updated_state_of_art") if isinstance(data.get("updated_state_of_art"), dict) else data
    updated = _normalize_structured(updated, title, refs)
    draft = _plain_from_structured(updated)

    return {
        "agent": "EnnoScholar",
        "version": "v56_chat_by_verrou",
        "project_id": project_id,
        "verrou_title": title,
        "instruction": instruction,
        "updated_state_of_art": updated,
        "draft": draft,
        "changes_summary": data.get("changes_summary") if isinstance(data.get("changes_summary"), list) else [],
        "llm_used": True,
        "fallback_used": False,
    }


@router.post("/{project_id}/scholar/state-of-art/chat-verrou")
def chat_update_state_of_art_for_verrou_alias(project_id: int, payload: Dict[str, Any] = Body(...)):
    return chat_update_state_of_art_for_verrou(project_id=project_id, payload=payload)
