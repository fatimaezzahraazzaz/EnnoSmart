# -*- coding: utf-8 -*-
"""
Audit EnnoScholar avant rédaction de l'état de l'art.

Ce script ne lance aucun LLM et ne modifie aucun fichier.
Il affiche exactement :
- les verrous présents dans selection_payload.json ;
- les articles sélectionnés par verrou ;
- les Article Cards réellement disponibles pour la rédaction ;
- les citations A1, A2... ;
- les écarts sélection / cartes ;
- les articles liés à plusieurs verrous.

Exécution :
    cd C:/EnnoSmart/backend_api
    python test_state_of_art_writer_inputs.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from db.database import SessionLocal
from db.models import Project
from services.scholar_state_of_art_payload_service import (
    build_state_of_art_selection_payload,
)
from services.article_card_builder import get_article_cards_payload


PROJECT_ID = 1


def safe_text(value: Any, max_chars: int = 180) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = " ".join(text.split())
    if len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def article_id_from_item(item: dict[str, Any]) -> int | None:
    for key in (
        "article_id",
        "db_article_id",
        "id",
        "source_article_id",
    ):
        value = item.get(key)
        try:
            if value is not None and str(value).strip():
                return int(value)
        except Exception:
            continue

    source_json = item.get("source_json")
    if isinstance(source_json, dict):
        for key in ("article_id", "db_article_id", "id"):
            try:
                value = source_json.get(key)
                if value is not None and str(value).strip():
                    return int(value)
            except Exception:
                continue

    return None


def article_title_from_item(item: dict[str, Any]) -> str:
    source_json = item.get("source_json")
    source_json = source_json if isinstance(source_json, dict) else {}

    return safe_text(
        item.get("title")
        or item.get("article_title")
        or item.get("paper_title")
        or source_json.get("title")
        or source_json.get("article_title")
        or "Titre absent",
        220,
    )


def article_tag_from_item(item: dict[str, Any]) -> str:
    source_json = item.get("source_json")
    source_json = source_json if isinstance(source_json, dict) else {}

    return safe_text(
        item.get("tag")
        or item.get("tag_article")
        or item.get("classification")
        or source_json.get("tag")
        or source_json.get("tag_article")
        or "Non classé",
        50,
    )


def citation_from_card(card: dict[str, Any], index: int) -> str:
    for key in (
        "citation_id",
        "citation_label",
        "citation",
        "article_ref",
        "reference_id",
    ):
        value = card.get(key)
        if value:
            text = str(value).strip()
            if text.startswith("[") and text.endswith("]"):
                text = text[1:-1]
            return text

    return f"A{index}"


def card_status(card: dict[str, Any]) -> str:
    guard = card.get("quality_guard")
    guard = guard if isinstance(guard, dict) else {}

    return safe_text(
        card.get("status")
        or guard.get("status")
        or "unknown",
        60,
    )


def selected_articles_from_verrou(verrou: dict[str, Any]) -> list[dict[str, Any]]:
    article_keys = (
        "articles_directs",
        "articles_connexes",
        "articles_fondamentaux",
        "direct_articles",
        "related_articles",
        "fundamental_articles",
        "background_articles",
        "selected_articles",
        "articles",
        "scientific_articles",
        "consultant_selected_articles",
    )

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for key in article_keys:
        value = verrou.get(key)
        if not isinstance(value, list):
            continue

        for item in value:
            if not isinstance(item, dict):
                continue

            article_id = article_id_from_item(item)
            dedupe_key = (
                f"id:{article_id}"
                if article_id is not None
                else f"title:{article_title_from_item(item).lower()}"
            )

            if dedupe_key in seen:
                continue

            seen.add(dedupe_key)

            normalized = dict(item)

            if not (
                normalized.get("tag")
                or normalized.get("tag_article")
                or normalized.get("classification")
            ):
                if key in {"articles_directs", "direct_articles"}:
                    normalized["tag"] = "Direct"
                elif key in {"articles_connexes", "related_articles"}:
                    normalized["tag"] = "Connexe"
                elif key in {
                    "articles_fondamentaux",
                    "fundamental_articles",
                    "background_articles",
                }:
                    normalized["tag"] = "Fondamental"

            normalized["_selection_key"] = key
            out.append(normalized)

    return out


def verrou_id_from_item(verrou: dict[str, Any], index: int) -> str:
    return str(
        verrou.get("verrou_id")
        or verrou.get("db_verrou_id")
        or verrou.get("id")
        or index
    )


def verrou_title_from_item(verrou: dict[str, Any], index: int) -> str:
    return safe_text(
        verrou.get("verrou_title")
        or verrou.get("title")
        or verrou.get("scientific_title")
        or f"Verrou {index}",
        260,
    )


def main() -> int:
    db = SessionLocal()

    try:
        project = db.query(Project).filter(Project.id == PROJECT_ID).first()
        if project is None:
            print(f"[ERREUR] Projet introuvable : id={PROJECT_ID}")
            return 1

        print("=" * 110)
        print("AUDIT DES ENTRÉES AVANT RÉDACTION DE L'ÉTAT DE L'ART")
        print("=" * 110)
        print(
            f"Projet : {project.organisme} / {project.project_name} / {project.year} "
            f"(project_id={project.id})"
        )

        # Reconstruit seulement le payload de sélection.
        # Aucun LLM, aucun téléchargement.
        selection_payload = build_state_of_art_selection_payload(
            db=db,
            project=project,
        )

        cards_payload = get_article_cards_payload(project)

        verrous = [
            item
            for item in as_list(selection_payload.get("verrous"))
            if isinstance(item, dict)
        ]
        cards = [
            item
            for item in as_list(cards_payload.get("cards"))
            if isinstance(item, dict)
        ]

        selection_path = (
            selection_payload.get("output_path")
            or selection_payload.get("payload_path")
            or selection_payload.get("path")
        )
        cards_path = (
            cards_payload.get("output_path")
            or cards_payload.get("payload_path")
            or cards_payload.get("path")
        )

        print("\nCHEMINS")
        print("-" * 110)
        print("Selection payload :", selection_path or "chemin non exposé")
        print("Article Cards     :", cards_path or "chemin non exposé")

        selection_summary = (
            selection_payload.get("selection_summary")
            if isinstance(selection_payload.get("selection_summary"), dict)
            else selection_payload.get("summary")
            if isinstance(selection_payload.get("summary"), dict)
            else {}
        )

        print("\nRÉSUMÉ GLOBAL")
        print("-" * 110)
        print("Payload sélection OK             :", selection_payload.get("ok"))
        print("Verrous dans le payload          :", len(verrous))
        print(
            "Articles sélectionnés annoncés   :",
            selection_summary.get("usable_articles_total")
            or selection_summary.get("kept_articles_total")
            or selection_summary.get("selected_articles_count"),
        )
        print("Article Cards payload OK          :", cards_payload.get("ok"))
        print("Article Cards annoncées           :", cards_payload.get("cards_count"))
        print("Article Cards réellement présentes:", len(cards))

        # Index des cartes.
        cards_by_id: dict[int, dict[str, Any]] = {}
        card_citations: dict[int, str] = {}

        for index, card in enumerate(cards, start=1):
            article_id = article_id_from_item(card)
            if article_id is None:
                continue
            cards_by_id[article_id] = card
            card_citations[article_id] = citation_from_card(card, index)

        selected_ids: set[int] = set()
        selected_occurrences: defaultdict[int, list[dict[str, str]]] = defaultdict(list)

        print("\nVERROUS ET ARTICLES ENVOYÉS AUX PHASES SUIVANTES")
        print("=" * 110)

        for verrou_index, verrou in enumerate(verrous, start=1):
            verrou_id = verrou_id_from_item(verrou, verrou_index)
            verrou_title = verrou_title_from_item(verrou, verrou_index)
            articles = selected_articles_from_verrou(verrou)

            print(
                f"\n[V{verrou_index}] verrou_id={verrou_id} | "
                f"articles sélectionnés={len(articles)}"
            )
            print(verrou_title)
            print("-" * 110)

            if not articles:
                print("  [ATTENTION] Aucun article sélectionné pour ce verrou.")
                continue

            direct_count = 0
            connexe_count = 0
            fondamental_count = 0
            missing_card_count = 0

            for article_index, article in enumerate(articles, start=1):
                article_id = article_id_from_item(article)
                title = article_title_from_item(article)
                tag = article_tag_from_item(article)

                if tag.lower() == "direct":
                    direct_count += 1
                elif tag.lower() == "connexe":
                    connexe_count += 1
                elif tag.lower() == "fondamental":
                    fondamental_count += 1

                citation = card_citations.get(article_id or -1, "PAS_DE_CARTE")
                has_card = article_id in cards_by_id if article_id is not None else False

                if article_id is not None:
                    selected_ids.add(article_id)
                    selected_occurrences[article_id].append(
                        {
                            "verrou": f"V{verrou_index}",
                            "verrou_id": verrou_id,
                            "verrou_title": verrou_title,
                            "tag": tag,
                        }
                    )

                if not has_card:
                    missing_card_count += 1

                marker = "OK" if has_card else "EXCLU_SANS_CARTE"

                print(
                    f"  {article_index:02d}. [{marker}] "
                    f"{citation:>12} | id={article_id!s:<6} | {tag:<12} | {title}"
                )

            print(
                f"\n  Totaux V{verrou_index}: "
                f"Direct={direct_count}, Connexe={connexe_count}, "
                f"Fondamental={fondamental_count}, Sans carte={missing_card_count}"
            )

        print("\nARTICLE CARDS RÉELLEMENT DISPONIBLES POUR LA RÉDACTION")
        print("=" * 110)

        for index, card in enumerate(cards, start=1):
            article_id = article_id_from_item(card)
            citation = citation_from_card(card, index)
            title = article_title_from_item(card)
            status = card_status(card)
            source_kind = safe_text(
                card.get("fulltext_source_kind")
                or card.get("source_kind")
                or (
                    card.get("fulltext", {}).get("source_kind")
                    if isinstance(card.get("fulltext"), dict)
                    else ""
                )
                or "—",
                40,
            )

            print(
                f"  {citation:>5} | id={article_id!s:<6} | "
                f"status={status:<22} | source={source_kind:<10} | {title}"
            )

        selected_without_card = sorted(
            article_id
            for article_id in selected_ids
            if article_id not in cards_by_id
        )
        cards_not_in_selection = sorted(
            article_id
            for article_id in cards_by_id
            if article_id not in selected_ids
        )

        print("\nÉCARTS AVANT RÉDACTION")
        print("=" * 110)
        print(
            "Articles sélectionnés uniques :", len(selected_ids)
        )
        print(
            "Cartes utilisables uniques     :", len(cards_by_id)
        )
        print(
            "Sélectionnés mais sans carte   :",
            selected_without_card or "Aucun",
        )
        print(
            "Cartes hors sélection actuelle :",
            cards_not_in_selection or "Aucune",
        )

        print("\nARTICLES MULTI-VERROUS")
        print("=" * 110)

        multi_verrou_count = 0
        for article_id, occurrences in sorted(selected_occurrences.items()):
            unique_verrous = {
                item["verrou"]
                for item in occurrences
            }
            if len(unique_verrous) <= 1:
                continue

            multi_verrou_count += 1
            card = cards_by_id.get(article_id, {})
            citation = card_citations.get(article_id, "PAS_DE_CARTE")
            title = article_title_from_item(card) if card else f"Article {article_id}"
            links = ", ".join(
                f'{item["verrou"]}({item["tag"]})'
                for item in occurrences
            )

            print(
                f"  {citation} | id={article_id} | {links} | {title}"
            )

        if multi_verrou_count == 0:
            print("  Aucun article répété entre plusieurs verrous dans le payload.")

        print("\nDÉCISION D'AUDIT")
        print("=" * 110)

        cards_count = len(cards_by_id)

        if not verrous:
            print("[BLOQUÉ] Aucun verrou n'est envoyé à la rédaction.")
            return 2

        if cards_count == 0:
            print("[BLOQUÉ] Aucune Article Card n'est disponible.")
            return 3

        if selected_without_card:
            print(
                "[ATTENTION] Certains articles sélectionnés n'ont pas de carte et "
                "ne pourront pas alimenter les preuves scientifiques."
            )
            print(
                "Le writer utilisera les Article Cards disponibles :",
                cards_count,
            )
        else:
            print("[OK] Tous les articles sélectionnés disposent d'une Article Card.")

        print(
            f"[OK] Entrées disponibles : {len(verrous)} verrou(s), "
            f"{len(selected_ids)} article(s) sélectionné(s) unique(s), "
            f"{cards_count} Article Card(s)."
        )

        print(
            "\nCe test n'a lancé ni Phase 4, ni Phase 4.5, ni Phase 5, "
            "et n'a appelé aucun LLM."
        )

        return 0

    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
