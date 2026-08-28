from __future__ import annotations

import re
import shutil
from pathlib import Path

REPO = Path(r"C:\EnnoSmart")
PACK = Path(__file__).resolve().parents[1]

FRONT = REPO / "frontend" / "components" / "ennosmart" / "ennoamelioration-page.tsx"
ROUTER = REPO / "backend_api" / "routers" / "improvement.py"
SERVICE_TARGET = REPO / "backend_api" / "services" / "improvement_comparison_service.py"
COMPONENT_TARGET = REPO / "frontend" / "components" / "ennosmart" / "improvement-pdf-comparator.tsx"
SOURCE_HIGHLIGHT = REPO / "backend_api" / "routers" / "source_highlight.py"

SERVICE_SOURCE = PACK / "backend_api" / "services" / "improvement_comparison_service.py"
COMPONENT_SOURCE = PACK / "frontend" / "components" / "ennosmart" / "improvement-pdf-comparator.tsx"
SOURCE_HIGHLIGHT_SOURCE = PACK / "backend_api" / "routers" / "source_highlight.py"


def die(message: str) -> None:
    print(f"[ERREUR] {message}")
    raise SystemExit(1)


def backup(path: Path, suffix: str) -> Path:
    target = path.with_name(path.name + suffix)
    shutil.copy2(path, target)
    print(f"[BACKUP] {target}")
    return target


def ensure_import(text: str) -> str:
    if "ImprovementPdfComparator" in text:
        return text

    marker = (
        'import { LoadingState } '
        'from "@/components/ennosmart/workspace-ui"\n'
    )
    import_line = (
        'import { ImprovementPdfComparator } '
        'from "@/components/ennosmart/improvement-pdf-comparator"\n'
    )

    if marker not in text:
        die("Import LoadingState introuvable dans ennoamelioration-page.tsx.")

    return text.replace(marker, marker + import_line, 1)


def patch_frontend(text: str) -> str:
    text = ensure_import(text)

    # Comparatif devient l'onglet d'ouverture.
    text = text.replace(
        '<Tabs defaultValue="improved" className="min-h-0 flex-1 gap-0">',
        '<Tabs defaultValue="diff" className="min-h-0 flex-1 gap-0">',
        1,
    )

    # Afficher seulement Comparatif + Sources.
    # Le TabsContent "sources" reste physiquement et textuellement intact.
    if '<TabsTrigger value="improved">Améliorée</TabsTrigger>' in text:
        tabs_pattern = re.compile(
            r'<TabsList className=\{cn\("grid w-full", current\.source_document_id \? "grid-cols-5" : "grid-cols-4"\)\}>'
            r'.*?'
            r'</TabsList>',
            flags=re.S,
        )
        tabs_replacement = (
            '<TabsList className="grid w-full grid-cols-2">\n'
            '                <TabsTrigger value="diff">Comparatif</TabsTrigger>\n'
            '                <TabsTrigger value="sources">Sources</TabsTrigger>\n'
            '              </TabsList>'
        )
        text, count = tabs_pattern.subn(tabs_replacement, text, count=1)
        if count != 1:
            die("La barre des onglets Agent 3 n'a pas été trouvée.")

    # Remplacer uniquement le comparatif textuel.
    if "<ImprovementPdfComparator" not in text:
        diff_pattern = re.compile(
            r'<TabsContent value="diff" className="min-h-0 overflow-hidden p-0">'
            r'.*?'
            r'</TabsContent>\s*'
            r'(<TabsContent value="audit")',
            flags=re.S,
        )

        diff_replacement = (
            '<TabsContent value="diff" className="min-h-0 overflow-hidden p-0">\n'
            '              <ImprovementPdfComparator\n'
            '                projectId={projectId}\n'
            '                sessionId={current.session_id}\n'
            '                versionId={(candidate || activeVersion)?.version_id || ""}\n'
            '                changes={comparisonChanges}\n'
            '                sourceFilename={sourceDocument?.filename || "Document source"}\n'
            '              />\n'
            '            </TabsContent>\n'
            '            \\1'
        )

        text, count = diff_pattern.subn(diff_replacement, text, count=1)
        if count != 1:
            die("Le bloc Comparatif actuel n'a pas été trouvé.")

    # Fenêtre Proposition plus large et redimensionnable.
    old_aside = (
        '<aside className="absolute inset-y-0 right-0 z-30 flex h-full min-h-0 '
        'w-[min(94vw,640px)] flex-col overflow-hidden border-l bg-card '
        'shadow-[-22px_0_55px_rgba(45,20,80,0.14)] '
        'sm:w-[min(88vw,640px)] xl:w-[min(48vw,640px)]">'
    )

    new_aside = (
        '<aside className="absolute inset-y-0 right-0 z-30 flex h-full min-h-0 '
        'w-[min(96vw,1120px)] max-w-[96vw] resize-x flex-col overflow-hidden '
        'border-l bg-card shadow-[-22px_0_55px_rgba(45,20,80,0.14)] '
        'sm:min-w-[560px] sm:w-[min(92vw,1120px)] '
        'xl:w-[min(76vw,1120px)]">'
    )

    if old_aside in text:
        text = text.replace(old_aside, new_aside, 1)
    elif "resize-x" not in text:
        die("Le panneau Proposition n'a pas été trouvé pour le rendre redimensionnable.")

    return text


def patch_router(text: str) -> str:
    if "build_comparison_preview" not in text:
        marker = (
            "from services.improvement_context_service "
            "import get_improvement_project_context\n"
        )
        service_import = (
            "from services.improvement_comparison_service import (\n"
            "    build_comparison_preview,\n"
            "    comparison_file_response,\n"
            ")\n"
        )

        if marker not in text:
            die("Import improvement_context_service introuvable dans improvement.py.")

        text = text.replace(marker, marker + service_import, 1)

    if "/comparison-preview" in text:
        return text

    route = r'''

@router.get("/sessions/{session_id}/versions/{version_id}/comparison-preview")
def get_improvement_comparison_preview(
    project_id: int,
    session_id: str,
    version_id: str,
    side: str = Query(..., pattern="^(original|proposed)$"),
    change_index: int = Query(..., ge=0, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Comparatif PDF visuel Agent 3.
    project = get_project_for_user(
        db,
        project_id,
        current_user,
    )
    preview = build_comparison_preview(
        db,
        project_id=project.id,
        session_id=session_id,
        version_id=version_id,
        side=side,
        change_index=change_index,
    )
    return comparison_file_response(preview)
'''

    marker = (
        '\n@router.post("/sessions/{session_id}/versions/{version_id}/decision")'
    )
    if marker not in text:
        die("Point d'insertion de la route comparison-preview introuvable.")

    return text.replace(marker, route + marker, 1)


def main() -> None:
    print("=== EnnoSmart Agent 3 - Comparatif PDF V4.00 ===")

    for path in (FRONT, ROUTER, SERVICE_SOURCE, COMPONENT_SOURCE):
        if not path.exists():
            die(f"Fichier introuvable : {path}")

    backup(FRONT, ".before-agent3-pdf-v400")
    backup(ROUTER, ".before-agent3-pdf-v400")

    shutil.copy2(SERVICE_SOURCE, SERVICE_TARGET)
    shutil.copy2(COMPONENT_SOURCE, COMPONENT_TARGET)
    print(f"[OK] Service : {SERVICE_TARGET}")
    print(f"[OK] Composant : {COMPONENT_TARGET}")

    # Le comparatif nécessite la conversion Office -> PDF.
    source_highlight_text = (
        SOURCE_HIGHLIGHT.read_text(encoding="utf-8", errors="ignore")
        if SOURCE_HIGHLIGHT.exists()
        else ""
    )

    if "def convert_office_to_pdf(" not in source_highlight_text:
        if not SOURCE_HIGHLIGHT_SOURCE.exists():
            die(
                "source_highlight actuel ne contient pas convert_office_to_pdf "
                "et la version compatible n'est pas présente dans le pack."
            )

        if SOURCE_HIGHLIGHT.exists():
            backup(SOURCE_HIGHLIGHT, ".before-agent3-pdf-v400")

        shutil.copy2(SOURCE_HIGHLIGHT_SOURCE, SOURCE_HIGHLIGHT)
        print("[OK] source_highlight compatible installé.")
    else:
        print("[OK] source_highlight compatible déjà présent ; non remplacé.")

    front_text = FRONT.read_text(encoding="utf-8")
    FRONT.write_text(patch_frontend(front_text), encoding="utf-8")
    print("[OK] ennoamelioration-page.tsx modifié.")

    router_text = ROUTER.read_text(encoding="utf-8")
    ROUTER.write_text(patch_router(router_text), encoding="utf-8")
    print("[OK] improvement.py modifié.")

    import py_compile

    for path in (ROUTER, SERVICE_TARGET, SOURCE_HIGHLIGHT):
        if not path.exists():
            continue
        py_compile.compile(str(path), doraise=True)
        print(f"[PYTHON OK] {path.name}")

    final_front = FRONT.read_text(encoding="utf-8")
    checks = {
        "Comparatif visible": '<TabsTrigger value="diff">Comparatif</TabsTrigger>' in final_front,
        "Sources visible": '<TabsTrigger value="sources">Sources</TabsTrigger>' in final_front,
        "Comparateur PDF": "<ImprovementPdfComparator" in final_front,
        "Fenetre redimensionnable": "resize-x" in final_front,
    }

    for label, ok in checks.items():
        print(f"[{'OK' if ok else 'ERREUR'}] {label}")
        if not ok:
            raise SystemExit(2)

    print("")
    print("INSTALLATION TERMINEE.")
    print("Redemarre FastAPI puis actualise le frontend.")
    print("Pas besoin de relancer l'amelioration existante.")


if __name__ == "__main__":
    main()
