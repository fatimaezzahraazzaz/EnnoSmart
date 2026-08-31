# -*- coding: utf-8 -*-
from pathlib import Path
import argparse, shutil, datetime, py_compile

CHANGED = [
    Path("backend_api/services/diagnostic_service.py"),
    Path("backend_api/services/diagnostic_display_service.py"),
]

FORBIDDEN = [
    Path("agents/EnnoDiagnostic/ennodiagnostic_agent.py"),
    Path("agents/EnnoDiagnostic/consultant_verrou_synthesizer.py"),
    Path("agents/EnnoDiagnostic/scientific_axis_synthesizer.py"),
    Path("modules/NLP/frascati_assessment.py"),
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    package = Path(__file__).resolve().parent
    files = package / "files"

    # Garantie forte : aucun fichier de détection/groupement des verrous
    # n'est livré dans ce patch.
    for rel in FORBIDDEN:
        if (files / rel).exists():
            raise SystemExit(f"[ERREUR] Payload interdit détecté : {rel}")

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = repo / ".ennosmart_patch_backups" / f"score_only_v57_{stamp}"

    for rel in CHANGED:
        src = files / rel
        dst = repo / rel
        if not src.exists():
            raise SystemExit(f"[ERREUR] Fichier patch manquant : {src}")
        if not dst.exists():
            raise SystemExit(f"[ERREUR] Fichier repo manquant : {dst}")
        b = backup / rel
        b.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dst, b)

    for rel in CHANGED:
        src = files / rel
        dst = repo / rel
        shutil.copy2(src, dst)
        py_compile.compile(str(dst), doraise=True)
        print(f"[OK] installé : {rel}")

    print(f"[OK] backup : {backup}")
    print("[OK] Aucun fichier EnnoDiagnostic/NLP de détection des verrous n'a été modifié.")
    print("[OK] Patch SCORE-ONLY V5.7 installé.")

if __name__ == "__main__":
    main()
