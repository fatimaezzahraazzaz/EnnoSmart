from __future__ import annotations

import re
import shutil
import py_compile
from pathlib import Path

REPO = Path(r"C:\EnnoSmart")
PACK = Path(__file__).resolve().parents[1]

PAGE = REPO / "frontend" / "components" / "ennosmart" / "ennoamelioration-page.tsx"
ROUTER = REPO / "backend_api" / "routers" / "improvement.py"
SERVICE = REPO / "backend_api" / "services" / "improvement_comparison_service.py"
COMPARATOR = REPO / "frontend" / "components" / "ennosmart" / "improvement-pdf-comparator.tsx"
PACK_SERVICE = PACK / "backend_api" / "services" / "improvement_comparison_service.py"
PACK_COMPARATOR = PACK / "frontend" / "components" / "ennosmart" / "improvement-pdf-comparator.tsx"


def fail(message: str) -> None:
    print(f"[ERREUR] {message}")
    raise SystemExit(1)


def backup(path: Path) -> None:
    if path.exists():
        target = path.with_name(path.name + ".before-agent3-v406")
        shutil.copy2(path, target)
        print(f"[BACKUP] {target}")



def ensure_compact_actions(text: str) -> str:
    """Garde le correctif V4.05 même si l'utilisateur ne l'a pas encore appliqué."""
    bottom_patterns = [
        re.compile(
            r'\n\s*\{candidate && \(\s*'
            r'<div className="grid grid-cols-2 gap-2 border-t p-3">\s*'
            r'.*?decide\("rejected"\).*?'
            r'.*?decide\("accepted"\).*?'
            r'</div>\s*'
            r'\)\}',
            flags=re.S,
        ),
        re.compile(
            r'\n\s*\{candidate && \(\s*'
            r'<div[^>]*className="[^"]*grid-cols-2[^"]*border-t[^"]*"[^>]*>\s*'
            r'.*?decide\("rejected"\).*?'
            r'.*?decide\("accepted"\).*?'
            r'</div>\s*'
            r'\)\}',
            flags=re.S,
        ),
    ]
    for pattern in bottom_patterns:
        text, count = pattern.subn("", text, count=1)
        if count == 1:
            break

    if 'aria-label="Rejeter la proposition"' in text:
        return text

    marker = 'aria-label={proposalFullscreen ? "Restaurer la fenêtre" : "Agrandir la fenêtre"}'
    marker_index = text.find(marker)
    if marker_index < 0:
        marker_index = text.find('onClick={() => setProposalFullscreen((value) => !value)}')
    if marker_index < 0:
        return text

    button_start = text.rfind('<Button', 0, marker_index)
    line_start = text.rfind('\n', 0, button_start) + 1
    indent = text[line_start:button_start]

    compact = (
        f'{indent}{{candidate && (\n'
        f'{indent}  <div className="flex shrink-0 items-center gap-1">\n'
        f'{indent}    <Button type="button" variant="outline" size="icon" className="size-8 rounded-lg border-rose-200 text-rose-600 hover:bg-rose-50 hover:text-rose-700" disabled={{busy}} onClick={{() => decide("rejected")}} aria-label="Rejeter la proposition" title="Rejeter la proposition">\n'
        f'{indent}      <X className="size-4" />\n'
        f'{indent}    </Button>\n'
        f'{indent}    <Button type="button" size="icon" className="size-8 rounded-lg" disabled={{busy}} onClick={{() => decide("accepted")}} aria-label="Accepter la proposition" title="Accepter la proposition">\n'
        f'{indent}      <Check className="size-4" />\n'
        f'{indent}    </Button>\n'
        f'{indent}  </div>\n'
        f'{indent})}}\n'
    )
    return text[:line_start] + compact + text[line_start:]

def patch_page(text: str) -> str:
    text = ensure_compact_actions(text)
    old = (
        '                versionId={(candidate || activeVersion)?.version_id || ""}\n'
        '                changes={comparisonChanges}\n'
    )
    new = (
        '                versionId={candidate?.version_id || ""}\n'
        '                activeVersionId={activeVersion?.version_id || ""}\n'
        '                changes={comparisonChanges}\n'
    )
    if old in text:
        return text.replace(old, new, 1)
    if 'activeVersionId={activeVersion?.version_id || ""}' in text:
        return text
    pattern = re.compile(
        r'(\s*)versionId=\{\(candidate \|\| activeVersion\)\?\.version_id \|\| ""\}\s*\n'
        r'\1changes=\{comparisonChanges\}'
    )
    replacement = (
        r'\1versionId={candidate?.version_id || ""}\n'
        r'\1activeVersionId={activeVersion?.version_id || ""}\n'
        r'\1changes={comparisonChanges}'
    )
    text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        fail("Props du comparateur introuvables dans ennoamelioration-page.tsx.")
    return text


def patch_router(text: str) -> str:
    if "build_version_document_preview" not in text:
        import_pattern = re.compile(
            r'from services\.improvement_comparison_service import \(\s*'
            r'build_comparison_preview,\s*'
            r'comparison_file_response,\s*'
            r'\)',
            flags=re.S,
        )
        replacement = (
            'from services.improvement_comparison_service import (\n'
            '    build_comparison_preview,\n'
            '    build_version_document_preview,\n'
            '    comparison_file_response,\n'
            ')'
        )
        text, count = import_pattern.subn(replacement, text, count=1)
        if count != 1:
            fail("Import improvement_comparison_service introuvable.")

    if "/document-preview" not in text:
        marker = '\n@router.post("/sessions/{session_id}/versions/{version_id}/decision")'
        if marker not in text:
            fail("Route decision introuvable.")
        route = r'''

@router.get("/sessions/{session_id}/versions/{version_id}/document-preview")
def get_improvement_version_document_preview(
    project_id: int,
    session_id: str,
    version_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = get_project_for_user(db, project_id, current_user)
    preview = build_version_document_preview(
        db,
        project_id=project.id,
        session_id=session_id,
        version_id=version_id,
    )
    return comparison_file_response(preview)
'''
        text = text.replace(marker, route + marker, 1)
    return text


def main() -> None:
    print("=== EnnoSmart Agent 3 - Etat apres decision V4.06 ===")
    for path in (PAGE, ROUTER, PACK_SERVICE, PACK_COMPARATOR):
        if not path.exists():
            fail(f"Fichier introuvable : {path}")
    for path in (PAGE, ROUTER, SERVICE, COMPARATOR):
        backup(path)

    shutil.copy2(PACK_SERVICE, SERVICE)
    shutil.copy2(PACK_COMPARATOR, COMPARATOR)
    print(f"[OK] Service : {SERVICE}")
    print(f"[OK] Comparateur : {COMPARATOR}")

    PAGE.write_text(patch_page(PAGE.read_text(encoding="utf-8")), encoding="utf-8")
    print("[OK] ennoamelioration-page.tsx")
    ROUTER.write_text(patch_router(ROUTER.read_text(encoding="utf-8")), encoding="utf-8")
    print("[OK] improvement.py")

    py_compile.compile(str(SERVICE), doraise=True)
    py_compile.compile(str(ROUTER), doraise=True)
    print("[PYTHON OK] backend")

    final_page = PAGE.read_text(encoding="utf-8")
    final_router = ROUTER.read_text(encoding="utf-8")
    checks = {
        "candidate separe": 'versionId={candidate?.version_id || ""}' in final_page,
        "version active transmise": 'activeVersionId={activeVersion?.version_id || ""}' in final_page,
        "route document preview": "/document-preview" in final_router,
        "plein ecran conserve": "proposalFullscreen" in final_page,
        "sources conserve": '<TabsTrigger value="sources">Sources</TabsTrigger>' in final_page,
    }
    for label, ok in checks.items():
        print(f"[{'OK' if ok else 'ERREUR'}] {label}")
        if not ok:
            raise SystemExit(2)

    print("")
    print("V4.06 INSTALLEE.")
    print("Apres Acceptation/Rejet : version active a gauche, modifications et nouvelle version vides.")


if __name__ == "__main__":
    main()
