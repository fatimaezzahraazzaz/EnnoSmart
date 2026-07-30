# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any


ROOT = Path(r"C:\EnnoSmart")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROJECT_ID = int(os.environ.get("ENNOSCHOLAR_TEST_PROJECT_ID", "1"))


PROMPT = """
Je veux organiser l’état de l’art global en commençant par une analyse générale, puis présenter les bases de données SAR disponibles publiquement pour la détection et la reconnaissance automatique de cibles.

Ensuite, je veux expliquer les principales approches classiques et modernes de détection et de reconnaissance, notamment les techniques de Deep Learning appliquées aux images SAR. Pour chaque approche, il faut expliquer le principe, les données utilisées, les protocoles d’apprentissage et d’évaluation, les résultats rapportés, les conditions d’utilisation et les limites.

Il faut ensuite présenter précisément la base MSTAR, ses caractéristiques, les types de cibles, les conditions d’acquisition, les angles de dépression et la constitution des ensembles d’apprentissage et de test.

Je veux également une partie détaillée sur l’entraînement des modèles ATR et ATD avec des données simulées, en expliquant les méthodes, les protocoles, les bénéfices attendus et les risques liés à l’écart entre données simulées et données réelles.

Une partie distincte doit présenter l’état de l’art interne à partir des documents actuels autorisés du projet. Elle ne doit pas utiliser les anciens dossiers CIR et ne doit pas déclencher de recherche web.

Enfin, il faut synthétiser les insuffisances, les verrous et les incertitudes scientifiques et technologiques, notamment la représentativité des données synthétiques, la validation des modèles, la discordance entre données radar mesurées et simulées et la génération d’images SAR réalistes et variées.

Les titres servent uniquement à organiser le livrable. Ils ne créent pas de nouveaux verrous. Il faut conserver uniquement les verrous scientifiques déjà validés dans le dossier.

La rédaction doit être globale, cohérente, détaillée et bien argumentée, avec des comparaisons, une analyse critique et des transitions naturelles. Avant la rédaction, il faut analyser la couverture des articles existants pour chaque section et rechercher uniquement les informations scientifiques réellement manquantes. Toute nouvelle source doit être validée avant son intégration.
""".strip()


def import_attribute(
    module_names: list[str],
    attribute_names: list[str],
) -> Any:
    errors: list[str] = []

    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            errors.append(f"{module_name}: {exc}")
            continue

        for attribute_name in attribute_names:
            if hasattr(module, attribute_name):
                return getattr(module, attribute_name)

    raise ImportError(
        "Import impossible.\n"
        + "\n".join(errors)
        + "\nAttributs recherchés : "
        + ", ".join(attribute_names)
    )


def load_session_factory() -> Any:
    return import_attribute(
        [
            "backend_api.database",
            "backend_api.db.database",
            "backend.database",
            "backend.db.database",
            "database",
            "db.database",
            "app.database",
            "app.db.database",
        ],
        [
            "SessionLocal",
            "session_local",
        ],
    )


def load_project_model() -> Any:
    return import_attribute(
        [
            "backend_api.models.project",
            "backend_api.models.projects",
            "backend_api.models",
            "backend.models.project",
            "backend.models.projects",
            "backend.models",
            "models.project",
            "models.projects",
            "models",
            "app.models.project",
            "app.models",
        ],
        [
            "Project",
            "ScholarProject",
        ],
    )


def compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): compact(v) for k, v in value.items()}

    if isinstance(value, list):
        return [compact(item) for item in value]

    if hasattr(value, "model_dump"):
        return compact(value.model_dump(mode="json"))

    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass

    return value


def print_json(title: str, value: Any) -> None:
    print("\n" + "=" * 90)
    print(title)
    print("=" * 90)
    print(
        json.dumps(
            compact(value),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def main() -> None:
    from backend_api.services.guided_research_service import (
        accept_guided_research_plan,
        create_guided_research_session,
        read_guided_research_session,
        send_guided_research_message,
    )

    SessionLocal = load_session_factory()
    Project = load_project_model()

    db = SessionLocal()

    try:
        project = db.query(Project).filter(Project.id == PROJECT_ID).first()

        if project is None:
            raise RuntimeError(
                f"Projet introuvable avec id={PROJECT_ID}. "
                "Modifie $ProjectId au début du bloc PowerShell."
            )

        print("\nProjet chargé :")
        print(f"  id   = {getattr(project, 'id', None)}")
        print(
            "  nom  = "
            + str(
                getattr(
                    project,
                    "name",
                    getattr(
                        project,
                        "project_name",
                        getattr(project, "title", "non disponible"),
                    ),
                )
            )
        )

        # ----------------------------------------------------
        # 1. Création de la session
        # ----------------------------------------------------
        created = create_guided_research_session(
            db,
            project,
            user_id=None,
            target_mode="global",
            entry_module="ennoscholar",
        )

        print_json("1. SESSION CRÉÉE", created)

        session_id = (
            created.get("session_id")
            or created.get("id")
            or created.get("guided_research_session_id")
        )

        if not session_id:
            raise RuntimeError(
                "Aucun session_id trouvé dans la réponse de création."
            )

        print(f"\nSESSION_ID={session_id}")

        # ----------------------------------------------------
        # 2. Envoi du prompt consultant
        # ----------------------------------------------------
        first_response = send_guided_research_message(
            db,
            project,
            session_id=str(session_id),
            message=PROMPT,
        )

        print_json(
            "2. RÉPONSE APRÈS INTERPRÉTATION DU PROMPT",
            first_response,
        )

        # ----------------------------------------------------
        # 3. Validation automatique du plan
        # ----------------------------------------------------
        validation_response = accept_guided_research_plan(
            db,
            project,
            session_id=str(session_id),
        )

        print_json(
            "3. RÉPONSE APRÈS VALIDATION DU PLAN",
            validation_response,
        )

        # ----------------------------------------------------
        # 4. Lecture de l'état final de la session
        # ----------------------------------------------------
        session_state = read_guided_research_session(
            db,
            str(session_id),
        )

        print_json(
            "4. ÉTAT COMPLET DE LA SESSION",
            session_state,
        )

        # Sauvegarde du résultat complet.
        output_path = (
            ROOT
            / f"guided_research_test_result_{session_id}.json"
        )

        output_path.write_text(
            json.dumps(
                compact(session_state),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

        print("\n" + "=" * 90)
        print("TEST TERMINÉ")
        print("=" * 90)
        print(f"Session : {session_id}")
        print(f"Résultat JSON : {output_path}")
        print()
        print("La Phase 5 ne doit pas encore être lancée.")
        print(
            "Les nouvelles sources doivent maintenant être "
            "acceptées ou rejetées par le consultant."
        )

    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("\nERREUR DU TEST GUIDED RESEARCH")
        print("=" * 90)
        traceback.print_exc()
        sys.exit(1)
