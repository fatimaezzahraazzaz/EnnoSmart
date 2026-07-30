# -*- coding: utf-8 -*-
from db.database import SessionLocal
from db.models import Project
from services.article_card_builder import _load_fulltext, get_selected_articles_for_project

PROJECT_ID = 1


def main():
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.id == PROJECT_ID).first()
        if project is None:
            raise RuntimeError(f"Projet introuvable : {PROJECT_ID}")

        articles = get_selected_articles_for_project(db, project)
        ready, missing = [], []

        for article in articles:
            result = _load_fulltext(project, article)
            row = (article.id, result.get("source_kind"), result.get("text_chars"), article.title)
            (ready if result.get("found") else missing).append(row)

        print(f"SELECTED={len(articles)} READY={len(ready)} MISSING={len(missing)}")
        print("\n--- READY ---")
        for row in ready:
            print(row)
        print("\n--- MISSING ---")
        for row in missing:
            print(row)
    finally:
        db.close()


if __name__ == "__main__":
    main()
