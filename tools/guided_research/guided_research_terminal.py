# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Callable

ROOT = Path(os.getenv("ENNOSMART_ROOT", r"C:\EnnoSmart"))


def _prepare_path() -> None:
    for path in (ROOT, ROOT / "backend_api"):
        value = str(path)
        if path.exists() and value not in sys.path:
            sys.path.insert(0, value)


def _import_first(names: tuple[str, ...]) -> Any:
    errors: list[str] = []
    for name in names:
        try:
            return importlib.import_module(name)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    raise RuntimeError("Import impossible. " + " | ".join(errors))


def _session_factory() -> Callable[[], Any]:
    module = _import_first(("db.database", "backend_api.db.database", "database"))
    for attr in ("SessionLocal", "SessionFactory", "session_factory"):
        factory = getattr(module, attr, None)
        if callable(factory):
            return factory
    engine = getattr(module, "engine", None)
    if engine is None:
        raise RuntimeError("Ni SessionLocal ni engine trouvés dans le module de base de données.")
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _load_project_model() -> Any:
    module = _import_first(("db.models", "backend_api.db.models"))
    model = getattr(module, "Project", None)
    if model is None:
        raise RuntimeError("Modèle Project introuvable.")
    return model


def _load_agent() -> Any:
    module = _import_first((
        "agents.EnnoScholar.guided_research.application.guided_research_agent",
        "modules.EnnoScholar.guided_research.application.guided_research_agent",
    ))
    return module.EnnoScholarGuidedResearchAgent()


def _fold(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s*", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _find_project(db: Any, Project: Any, args: argparse.Namespace) -> Any:
    if args.project_id is not None:
        project = db.query(Project).filter(Project.id == args.project_id).first()
        if project is None:
            raise RuntimeError(f"Projet id={args.project_id} introuvable.")
        return project

    rows = db.query(Project).all()
    matches = [
        p for p in rows
        if _fold(getattr(p, "organisme", "")) == _fold(args.organisme)
        and _fold(getattr(p, "project_name", "")) == _fold(args.project)
        and str(getattr(p, "year", "")) == str(args.year)
    ]
    if not matches:
        available = [
            f"id={getattr(p, 'id', '?')} | {getattr(p, 'organisme', '')} | "
            f"{getattr(p, 'project_name', '')} | {getattr(p, 'year', '')}"
            for p in rows[-20:]
        ]
        raise RuntimeError(
            "Projet introuvable. Projets récents :\n" + "\n".join(available)
        )
    if len(matches) > 1:
        print("Plusieurs projets correspondent ; utilisation du plus récent par id.")
        matches.sort(key=lambda p: int(getattr(p, "id", 0)), reverse=True)
    return matches[0]


def _print_response(response: Any) -> None:
    print("\n" + "=" * 96)
    print("ENNOAMEL / ENNOSCHOLAR")
    print("=" * 96)
    print(response.assistant_message)
    print("-" * 96)
    print(f"État       : {response.state.value}")
    print(f"Action     : {response.next_action.value}")
    print(f"Prêt Phase5: {response.ready_to_write}")

    candidates = response.metadata.get("candidates") or response.metadata.get("new_candidates") or []
    if candidates:
        print("\nSOURCES PROPOSÉES")
        for i, source in enumerate(candidates[:12], start=1):
            print(
                f"{i:02d}. {source.get('candidate_id')} | score={source.get('relevance_score')} | "
                f"recommended={source.get('recommended')}\n    {source.get('title')}"
            )


def _print_plan(snapshot: dict[str, Any]) -> None:
    brief = (snapshot.get("session") or {}).get("brief") or (snapshot.get("session") or {}).get("brief_json") or {}
    sections = brief.get("requested_sections") or []
    print("\nPLAN CONSULTANT ENREGISTRÉ")
    for section in sorted(sections, key=lambda x: int(x.get("order") or 999)):
        print(f"{section.get('order')}. {section.get('title')}")
        if section.get("objective"):
            print(f"   Objectif : {section.get('objective')}")
    print(f"Nombre de sections : {len(sections)}")


def _print_sources(snapshot: dict[str, Any]) -> None:
    sources = (snapshot.get("artifacts") or {}).get("selected_sources") or []
    print("\nSOURCES DE LA SESSION")
    if not sources:
        print("Aucune source proposée pour le moment.")
        return
    for i, source in enumerate(sources, start=1):
        print(
            f"{i:02d}. {source.get('candidate_id')} | {source.get('consultant_decision')} | "
            f"score={source.get('relevance_score')} | {source.get('title')}"
        )


def _print_inputs(agent: Any, project: Any, session_id: str) -> None:
    info = agent.preparation.inspect_existing_inputs(project, session_id)
    print("\nENTRÉES SCIENTIFIQUES EXISTANTES — LECTURE SEULE")
    print(f"Phase 1 : {info.get('selection_path')}")
    print(f"SHA-256 : {info.get('selection_sha256')}")
    print(f"Phase 2 : {info.get('article_cards_path')}")
    print(f"SHA-256 : {info.get('article_cards_sha256')}")
    print(f"Articles sélectionnés : {info.get('selected_articles_count')}")
    print(f"Article Cards : {info.get('article_cards_count')}")
    print("Recherche externe : désactivée")
    print("Reconstruction Phase 1/2 : interdite")


def _print_mapping(agent: Any, project: Any, session_id: str) -> None:
    paths = agent.preparation.paths(project, session_id)
    path = paths.get("section_claim_mapping")
    if path is None or not Path(path).exists():
        print(
            "\nAucun rapport de mapping disponible. "
            "Décris d'abord le plan pour exécuter les Phases 3→4.7."
        )
        return
    payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    print("\nMAPPING SÉMANTIQUE STRICT — SECTIONS ↔ ARTICLE CARDS")
    print(f"Rapport : {path}")
    print(f"Validation globale : {payload.get('ok')}")
    print(
        f"Sections couvertes : {payload.get('supported_sections_count')}/"
        f"{payload.get('sections_count')}"
    )
    print(f"Sections rédigeables : {payload.get('writable_sections_count')}")
    print(f"Notes de couverture : {payload.get('coverage_note_sections_count')}")
    print(f"Claims autorisés : {payload.get('mapped_claims_count')}")
    for row in payload.get("sections") or []:
        if row.get("section_mode") == "coverage_note":
            symbol = "NOTE"
        else:
            symbol = "OK" if row.get("writing_allowed") else "BLOQUÉE"
        print(
            f"{int(row.get('order') or 0):02d}. [{symbol}] {row.get('title')}\n"
            f"    profil={row.get('semantic_profile')} "
            f"mode={row.get('section_mode')} "
            f"couverture={row.get('coverage')} "
            f"claims={len(row.get('selected_claim_ids') or [])} "
            f"citations={', '.join(row.get('selected_citations') or []) or '—'}"
        )
        if row.get("reason"):
            print(f"    raison={row.get('reason')}")
        rejected = row.get("rejected_claims_top") or []
        if rejected and not row.get("writing_allowed"):
            for claim in rejected[:3]:
                print(
                    f"    rejet {claim.get('claim_id')} "
                    f"score={claim.get('score')} : "
                    f"{', '.join(claim.get('reasons') or [])}"
                )


def _extract_markdown(snapshot: dict[str, Any]) -> str:
    draft = (snapshot.get("artifacts") or {}).get("draft") or {}
    if isinstance(draft, dict):
        markdown = draft.get("markdown")
        if isinstance(markdown, str):
            return markdown
        phase5 = draft.get("phase5") or {}
        if isinstance(phase5, dict) and isinstance(phase5.get("markdown"), str):
            return phase5["markdown"]
    return ""


def _verify_plan(snapshot: dict[str, Any]) -> None:
    session = snapshot.get("session") or {}
    brief = session.get("brief") or session.get("brief_json") or {}
    expected = [
        str(x.get("title") or "").strip()
        for x in sorted(brief.get("requested_sections") or [], key=lambda x: int(x.get("order") or 999))
        if str(x.get("title") or "").strip()
    ]
    markdown = _extract_markdown(snapshot)
    if not markdown:
        print("Aucun brouillon Phase 5 disponible. Utilise /verify après « tu peux rédiger ».")
        return

    headings: list[str] = []
    for line in markdown.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if not m:
            continue
        title = m.group(1).strip()
        if _fold(title) in {"references utilisees", "references", "bibliographie"}:
            continue
        headings.append(re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", title).strip())

    expected_folded = [_fold(x) for x in expected]
    actual_folded = [_fold(x) for x in headings]
    missing = [expected[i] for i, key in enumerate(expected_folded) if key not in actual_folded]
    unexpected = [headings[i] for i, key in enumerate(actual_folded) if key not in expected_folded]
    order_ok = [x for x in actual_folded if x in expected_folded] == [x for x in expected_folded if x in actual_folded]
    exact = not missing and not unexpected and order_ok and len(headings) == len(expected)

    print("\nVÉRIFICATION DU PLAN")
    print(f"Respect exact : {exact}")
    print(f"Ordre correct : {order_ok}")
    print(f"Sections attendues : {len(expected)}")
    print(f"Sections produites : {len(headings)}")
    if missing:
        print("Manquantes : " + " | ".join(missing))
    if unexpected:
        print("Non demandées : " + " | ".join(unexpected))
    if not missing and not unexpected:
        for index, (want, got) in enumerate(zip(expected, headings), start=1):
            print(f"{index:02d}. attendu={want}\n    obtenu ={got}")


def _read_multiline_prompt() -> str:
    print("\nColle maintenant le prompt complet.")
    print("Termine par une ligne contenant uniquement : <<<FIN_PROMPT>>>")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip() == "<<<FIN_PROMPT>>>":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _extract_numbered_sections(prompt: str) -> list[dict[str, Any]]:
    """Extract Markdown or plain numbered section headings from one prompt."""
    lines = prompt.replace("\r", "\n").splitlines()
    start_index = 0
    for index, line in enumerate(lines):
        folded = _fold(line)
        if any(marker in folded for marker in (
            "respecte exactement le plan suivant",
            "plan suivant",
            "structure suivante",
            "sections suivantes",
        )):
            start_index = index + 1
            break

    heading_re = re.compile(r"^\s*(?:#{1,6}\s*)?(\d{1,2})\s*[.)-]\s+(.+?)\s*$")
    candidates: list[tuple[int, int, str]] = []
    for index in range(start_index, len(lines)):
        match = heading_re.match(lines[index])
        if not match:
            continue
        number = int(match.group(1))
        title = re.sub(r"[*_`]+", "", match.group(2)).strip(" :-")
        if not title or number < 1 or number > 50:
            continue
        # Ignore process lists before the actual scientific plan. Scientific
        # headings are normally Markdown headings or form a consecutive block.
        is_markdown = lines[index].lstrip().startswith("#")
        if is_markdown or start_index > 0:
            candidates.append((index, number, title))

    if not candidates:
        # Fallback: find the longest consecutive numbered block.
        all_candidates: list[tuple[int, int, str]] = []
        for index, line in enumerate(lines):
            match = heading_re.match(line)
            if match:
                all_candidates.append((index, int(match.group(1)), match.group(2).strip(" :-")))
        blocks: list[list[tuple[int, int, str]]] = []
        current: list[tuple[int, int, str]] = []
        expected = 1
        for row in all_candidates:
            if row[1] == 1:
                if current:
                    blocks.append(current)
                current = [row]
                expected = 2
            elif current and row[1] == expected:
                current.append(row)
                expected += 1
            elif current:
                blocks.append(current)
                current = []
                expected = 1
        if current:
            blocks.append(current)
        candidates = max(blocks, key=len, default=[])

    sections: list[dict[str, Any]] = []
    for pos, (line_index, number, title) in enumerate(candidates):
        next_index = candidates[pos + 1][0] if pos + 1 < len(candidates) else len(lines)
        body_lines = lines[line_index + 1: next_index]
        body: list[str] = []
        for line in body_lines:
            stripped = line.strip()
            if stripped == "<<<FIN_PROMPT>>>":
                break
            if _fold(stripped) in {
                "tu peux rediger",
                "ne lance pas la redaction finale tant que je n ai pas ecrit exactement",
            }:
                continue
            if stripped:
                body.append(re.sub(r"^\s*[-*+]\s+", "", stripped))
        objective = re.sub(r"\s+", " ", " ".join(body)).strip()
        sections.append({
            "order": number,
            "title": title,
            "objective": objective[:5000],
        })
    sections.sort(key=lambda row: int(row["order"]))
    return sections


def _normalized_single_prompt_message(prompt: str, sections: list[dict[str, Any]]) -> str:
    lines = [
        "Construis maintenant un seul plan consultant strict à partir de cette demande.",
        f"Le plan contient exactement {len(sections)} sections obligatoires, dans cet ordre, sans section supplémentaire.",
    ]
    for section in sections:
        lines.append(
            f"Section {section['order']} intitulée « {section['title']} ». "
            f"Objectif détaillé : {section.get('objective') or 'Traiter uniquement ce titre avec les preuves validées.'}"
        )
    global_constraints: list[str] = []
    for line in prompt.splitlines():
        folded = _fold(line)
        if any(marker in folded for marker in (
            "n invente", "ne remplace", "preuves", "source", "mocem",
            "publication externe", "validation humaine", "citations",
        )) and not re.match(r"^\s*(?:#{1,6}\s*)?\d+[.)-]", line):
            global_constraints.append(line.strip())
    if global_constraints:
        lines.append("Contraintes générales obligatoires :")
        lines.extend(f"- {value}" for value in global_constraints[:30])
    lines.append("Ne lance pas encore la rédaction ; prépare d'abord les Phases 3 à 4.7 et le mapping.")
    return "\n".join(lines)


def _extract_global_constraints(prompt: str) -> list[str]:
    constraints: list[str] = []
    for line in prompt.splitlines():
        stripped = line.strip()
        if not stripped or re.match(r"^\s*(?:#{1,6}\s*)?\d+[.)-]", stripped):
            continue
        folded = _fold(stripped)
        if any(marker in folded for marker in (
            "n invente", "ne remplace", "preuves", "source", "publication externe",
            "validation humaine", "citations", "etat de l art global", "tous les verrous",
            "phase 4 7", "sous section", "sous titre",
        )):
            constraints.append(re.sub(r"^\s*[-*+]\s+", "", stripped))
    seen: set[str] = set()
    out: list[str] = []
    for value in constraints:
        key = _fold(value)
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out[:40]


def _snapshot_sections(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    brief = (snapshot.get("session") or {}).get("brief") or (snapshot.get("session") or {}).get("brief_json") or {}
    values = brief.get("requested_sections") or []
    return [x for x in values if isinstance(x, dict)]


def _print_final_paths(agent: Any, project: Any, session_id: str) -> None:
    paths = agent.preparation.paths(project, session_id)
    markdown = paths.get("phase5_markdown")
    payload = paths.get("phase5_payload")
    print("\nSORTIES FINALES")
    print(f"Markdown : {markdown}")
    print(f"Payload  : {payload}")
    if markdown and Path(markdown).exists():
        print(f"Taille Markdown : {Path(markdown).stat().st_size} octets")


def _run_one_prompt(
    *,
    agent: Any,
    db: Any,
    project: Any,
    session_id: str,
    prompt: str,
) -> bool:
    prompt = prompt.strip()
    if not prompt:
        print("Prompt vide : exécution annulée.")
        return False
    sections = _extract_numbered_sections(prompt)
    if not sections:
        print("Aucune section numérotée détectée. Utilise des titres comme : ## 1. Titre")
        return False

    print(f"\nPrompt unique reçu : {len(prompt)} caractères, {len(sections)} grands titres détectés.")
    print("Enregistrement atomique du plan : aucun appel au parseur LLM et une seule exécution des Phases 3→4.7.")
    submit = getattr(agent, "submit_structured_prompt", None)
    if not callable(submit):
        raise RuntimeError(
            "Le backend installé ne contient pas submit_structured_prompt. "
            "Réinstalle le package V3 global hiérarchique."
        )
    response = submit(
        db,
        project,
        session_id=session_id,
        raw_request=prompt,
        sections=sections,
        general_constraints=_extract_global_constraints(prompt),
    )
    _print_response(response)
    snapshot = agent.get_session(db, session_id)
    _print_plan(snapshot)
    _print_mapping(agent, project, session_id)

    ready = bool(getattr(response, "ready_to_write", False))
    if not ready:
        state_value = ((snapshot.get("session") or {}).get("state") or "")
        ready = str(state_value).lower() in {"ready_to_write", "ready"}
    if not ready:
        print("\nLa préparation globale hiérarchique n'autorise pas encore la Phase 5.")
        return False

    print("\nFusion validée. Lancement automatique de la Phase 5 globale uniquement...")
    write_response = agent.handle_message(
        db,
        project,
        session_id=session_id,
        consultant_message="tu peux rédiger",
    )
    _print_response(write_response)
    final_snapshot = agent.get_session(db, session_id)
    _verify_plan(final_snapshot)
    _print_final_paths(agent, project, session_id)
    draft = (final_snapshot.get("artifacts") or {}).get("draft") or {}
    return bool(isinstance(draft, dict) and draft.get("ok"))


def _print_help() -> None:
    print(
        """
Commandes terminal :
  /run        Colle un seul prompt complet, puis exécute mapping + rédaction + vérification.
  /plan       Affiche le plan enregistré dans la session.
  /inputs     Vérifie les artefacts Phase 1/2 lus en lecture seule.
  /state      Affiche l'état complet de la session en JSON.
  /mapping    Affiche le mapping strict entre sections et claims.
  /verify     Vérifie que le brouillon Phase 5 respecte exactement le plan.
  /quit       Ferme le chat.

Format conseillé pour le prompt unique :
  ## 1. Titre de section
  Objectif détaillé...
  ## 2. Titre de section
  ...

Après /run, termine le collage par : <<<FIN_PROMPT>>>
Le terminal enregistre tout le plan en une fois, exécute une seule fois les Phases 3→4.7, fusionne les verrous en sous-sections, lance uniquement la Phase 5 globale et vérifie le plan.
Les Phases 1/2 existantes restent en lecture seule.
""".strip()
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat terminal Guided Research EnnoScholar")
    parser.add_argument("--project-id", type=int)
    parser.add_argument("--organisme", default="Scalian")
    parser.add_argument("--project", default="AI_RADAR")
    parser.add_argument("--year", default="2025")
    parser.add_argument("--user-id", type=int, default=None)
    parser.add_argument("--session-id", default=None, help="Reprendre une session existante")
    parser.add_argument("--initial-message-file", default=None)
    parser.add_argument("--prompt-file", default=None, help="Prompt complet à exécuter en une seule fois")
    parser.add_argument("--auto-run", action="store_true", help="Exécute automatiquement le prompt-file")
    parser.add_argument("--exit-after-run", action="store_true")
    args = parser.parse_args()

    _prepare_path()
    SessionFactory = _session_factory()
    Project = _load_project_model()
    agent = _load_agent()
    db = SessionFactory()
    try:
        project = _find_project(db, Project, args)
        if args.session_id:
            session_id = args.session_id
            snapshot = agent.get_session(db, session_id)
            print(f"Session reprise : {session_id}")
        else:
            session = agent.create_session(
                db,
                project,
                created_by_user_id=args.user_id,
            )
            session_id = session.session_id
            print(f"Session créée : {session_id}")
        print(
            f"Projet : id={project.id} | {project.organisme} | "
            f"{project.project_name} | {project.year}"
        )
        _print_inputs(agent, project, session_id)
        _print_help()

        if args.initial_message_file:
            path = Path(args.initial_message_file)
            message = path.read_text(encoding="utf-8-sig")
            print("\nCONSULTANT > message initial chargé depuis :", path)
            response = agent.handle_message(
                db, project, session_id=session_id, consultant_message=message
            )
            _print_response(response)
            _print_plan(agent.get_session(db, session_id))

        if args.prompt_file and args.auto_run:
            prompt_path = Path(args.prompt_file)
            prompt = prompt_path.read_text(encoding="utf-8-sig")
            print("\nCONSULTANT > prompt unique chargé depuis :", prompt_path)
            _run_one_prompt(
                agent=agent, db=db, project=project, session_id=session_id, prompt=prompt
            )
            if args.exit_after_run:
                return

        while True:
            try:
                message = input("\nCONSULTANT > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nFin du chat.")
                break
            if not message:
                continue
            command = message.lower()
            if command in {"/quit", "/exit", "quit", "exit"}:
                print("Fin du chat.")
                break
            snapshot = agent.get_session(db, session_id)
            if command == "/run":
                prompt = _read_multiline_prompt()
                _run_one_prompt(
                    agent=agent, db=db, project=project, session_id=session_id, prompt=prompt
                )
                continue
            if command == "/plan":
                _print_plan(snapshot)
                continue
            if command == "/inputs":
                _print_inputs(agent, project, session_id)
                continue
            if command == "/sources":
                print("La gestion des sources est désactivée ici. Utilise l'onglet Sélection articles.")
                continue
            if command == "/state":
                print(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
                continue
            if command == "/mapping":
                _print_mapping(agent, project, session_id)
                continue
            if command == "/verify":
                _verify_plan(snapshot)
                continue
            if command == "/help":
                _print_help()
                continue

            response = agent.handle_message(
                db, project, session_id=session_id, consultant_message=message
            )
            _print_response(response)
    finally:
        db.close()


if __name__ == "__main__":
    main()
