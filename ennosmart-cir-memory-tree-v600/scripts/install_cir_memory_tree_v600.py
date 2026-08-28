from __future__ import annotations

import shutil
import py_compile
from pathlib import Path

REPO = Path(r"C:\EnnoSmart")
PACK = Path(__file__).resolve().parents[1]

PAGE = REPO / "frontend" / "components" / "ennosmart" / "cir-memory-page.tsx"
ROUTER = REPO / "backend_api" / "routers" / "cir_memory.py"
SERVICE = REPO / "backend_api" / "services" / "cir_memory_source_preview_service.py"

PACK_PAGE = PACK / "frontend" / "components" / "ennosmart" / "cir-memory-page.tsx"
PACK_SERVICE = PACK / "backend_api" / "services" / "cir_memory_source_preview_service.py"


def fail(message: str) -> None:
    print(f"[ERREUR] {message}")
    raise SystemExit(1)


def backup(path: Path, suffix: str) -> None:
    if not path.exists():
        return
    target = path.with_name(path.name + suffix)
    shutil.copy2(path, target)
    print(f"[BACKUP] {target}")


def patch_router(text: str) -> str:
    if "build_memory_source_preview" not in text:
        marker = (
            "from services.sharepoint_audit_service "
            "import mark_matching_items_memory_removed\n"
        )
        addition = (
            "from services.cir_memory_source_preview_service import (\n"
            "    build_memory_source_download,\n"
            "    build_memory_source_preview,\n"
            ")\n"
        )
        if marker not in text:
            fail("Import sharepoint_audit_service introuvable dans cir_memory.py.")
        text = text.replace(marker, marker + addition, 1)

    if "/source-preview" not in text:
        marker = '\n@router.get("/cir-memory/v2/cards")'
        if marker not in text:
            fail("Point d'insertion /cir-memory/v2/cards introuvable.")

        routes = (
            '\n\n@router.get("/cir-memory/v2/projects/{memory_id}/source-preview")\n'
            'def memory_v2_source_preview(\n'
            '    memory_id: str,\n'
            '    current_user: User = Depends(require_superadmin),\n'
            '):\n'
            '    return build_memory_source_preview(memory_id)\n'
            '\n\n@router.get("/cir-memory/v2/projects/{memory_id}/source-download")\n'
            'def memory_v2_source_download(\n'
            '    memory_id: str,\n'
            '    current_user: User = Depends(require_superadmin),\n'
            '):\n'
            '    return build_memory_source_download(memory_id)\n'
        )
        text = text.replace(marker, routes + marker, 1)

    return text


def main() -> None:
    print("=== EnnoSmart CIR Memory - Arborescence V6.00 ===")

    for path in (PAGE, ROUTER, PACK_PAGE, PACK_SERVICE):
        if not path.exists():
            fail(f"Fichier introuvable : {path}")

    backup(PAGE, ".before-cir-memory-tree-v600")
    backup(ROUTER, ".before-cir-memory-tree-v600")
    backup(SERVICE, ".before-cir-memory-tree-v600")

    shutil.copy2(PACK_PAGE, PAGE)
    shutil.copy2(PACK_SERVICE, SERVICE)
    print(f"[OK] Page complète : {PAGE}")
    print(f"[OK] Service aperçu : {SERVICE}")

    router_text = ROUTER.read_text(encoding="utf-8")
    ROUTER.write_text(patch_router(router_text), encoding="utf-8")
    print(f"[OK] Router : {ROUTER}")

    py_compile.compile(str(SERVICE), doraise=True)
    py_compile.compile(str(ROUTER), doraise=True)
    print("[PYTHON OK] backend")

    final_page = PAGE.read_text(encoding="utf-8")
    final_router = ROUTER.read_text(encoding="utf-8")

    checks = {
        "arborescence": "buildTree" in final_page,
        "sous-projet conditionnel": "Sous-projet" in final_page,
        "annees": "groupByYear" in final_page,
        "ouvrir CIR": "Ouvrir le CIR" in final_page,
        "telecharger original": "Télécharger l’original" in final_page,
        "preview backend": "/source-preview" in final_router,
        "download backend": "/source-download" in final_router,
    }

    for label, ok in checks.items():
        print(f"[{'OK' if ok else 'ERREUR'}] {label}")
        if not ok:
            raise SystemExit(2)

    print("")
    print("CIR MEMORY V6.00 INSTALLEE.")
    print("Structure : organisme > projet > sous-projet si present > annee > CIR.")
    print("PDF direct ; Word converti en PDF pour l'aperçu.")
    print("Redemarre FastAPI puis actualise le frontend.")


if __name__ == "__main__":
    main()
