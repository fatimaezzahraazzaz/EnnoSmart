from __future__ import annotations

import re
import shutil
from pathlib import Path

REPO = Path(r"C:\EnnoSmart")
PACK = Path(__file__).resolve().parents[1]

PAGE = REPO / "frontend" / "components" / "ennosmart" / "ennoamelioration-page.tsx"
COMPARATOR = REPO / "frontend" / "components" / "ennosmart" / "improvement-pdf-comparator.tsx"
PACK_COMPARATOR = PACK / "frontend" / "components" / "ennosmart" / "improvement-pdf-comparator.tsx"

def fail(message: str) -> None:
    print(f"[ERREUR] {message}")
    raise SystemExit(1)

def backup(path: Path, suffix: str) -> None:
    if path.exists():
        target = path.with_name(path.name + suffix)
        shutil.copy2(path, target)
        print(f"[BACKUP] {target}")

def main() -> None:
    print("=== EnnoSmart Agent 3 - Espace Comparatif V4.03 ===")
    if not PAGE.exists():
        fail(f"Page Agent 3 introuvable : {PAGE}")
    if not PACK_COMPARATOR.exists():
        fail(f"Comparateur V4.03 introuvable : {PACK_COMPARATOR}")

    backup(PAGE, ".before-agent3-v403")
    backup(COMPARATOR, ".before-agent3-v403")
    shutil.copy2(PACK_COMPARATOR, COMPARATOR)
    print(f"[OK] Comparateur complet : {COMPARATOR}")

    text = PAGE.read_text(encoding="utf-8")

    # 1) Icons Maximize/Minimize
    if "  Maximize2," not in text:
        anchor = "  Loader2,\n"
        if anchor not in text:
            fail("Import Loader2 introuvable.")
        text = text.replace(anchor, anchor + "  Maximize2,\n  Minimize2,\n", 1)

    # 2) State
    if "proposalFullscreen" not in text:
        anchor = "  const [rightOpen, setRightOpen] = useState(false)\n"
        if anchor not in text:
            fail("State rightOpen introuvable.")
        text = text.replace(anchor, anchor + "  const [proposalFullscreen, setProposalFullscreen] = useState(true)\n", 1)

        effect_anchor = "  const [sourcePreviewUrl, setSourcePreviewUrl] = useState(\"\")\n"
        effect = (
            "  useEffect(() => {\n"
            "    if (rightOpen) setProposalFullscreen(true)\n"
            "  }, [rightOpen])\n\n"
        )
        if effect_anchor not in text:
            fail("Point d insertion useEffect introuvable.")
        text = text.replace(effect_anchor, effect + effect_anchor, 1)

    # 3) Backdrop
    old_backdrop = 'className="absolute inset-0 z-20 bg-foreground/5 backdrop-blur-[1px]"'
    new_backdrop = (
        'className={cn(\n'
        '              proposalFullscreen\n'
        '                ? "fixed inset-0 z-40 bg-foreground/10 backdrop-blur-[2px]"\n'
        '                : "absolute inset-0 z-20 bg-foreground/5 backdrop-blur-[1px]",\n'
        '            )}'
    )
    if old_backdrop in text:
        text = text.replace(old_backdrop, new_backdrop, 1)

    # 4) Proposal panel: supports old and V4.00/V4.02 drawer classes.
    aside_pattern = re.compile(
        r'<aside className="absolute inset-y-0 right-0 z-30 flex h-full min-h-0 [^"]+">'
    )
    aside_replacement = (
        '<aside\n'
        '            className={cn(\n'
        '              "flex min-h-0 flex-col overflow-hidden bg-card transition-[inset,width,height,border-radius] duration-200",\n'
        '              proposalFullscreen\n'
        '                ? "fixed inset-2 z-50 h-[calc(100vh-1rem)] w-[calc(100vw-1rem)] rounded-2xl border shadow-2xl sm:inset-3 sm:h-[calc(100vh-1.5rem)] sm:w-[calc(100vw-1.5rem)]"\n'
        '                : "absolute inset-y-0 right-0 z-30 h-full w-[min(96vw,1120px)] max-w-[96vw] resize-x border-l shadow-[-22px_0_55px_rgba(45,20,80,0.14)] sm:min-w-[560px] sm:w-[min(92vw,1120px)] xl:w-[min(76vw,1120px)]",\n'
        '            )}\n'
        '          >'
    )
    if "fixed inset-2 z-50" not in text:
        text, count = aside_pattern.subn(aside_replacement, text, count=1)
        if count != 1:
            fail("Panneau Proposition introuvable.")

    # 5) Header buttons
    if 'aria-label={proposalFullscreen ? "Restaurer la fenêtre" : "Agrandir la fenêtre"}' not in text:
        header_anchor = '            {candidate ? <Badge>Proposition V{candidate.version_number}</Badge> : <Badge variant="outline">Version active</Badge>}\n'
        if header_anchor not in text:
            fail("Header Proposition introuvable.")
        controls = (
            '            {candidate ? <Badge>Proposition V{candidate.version_number}</Badge> : <Badge variant="outline">Version active</Badge>}\n'
            '            <Button\n'
            '              type="button"\n'
            '              variant="ghost"\n'
            '              size="icon"\n'
            '              className="size-8 shrink-0 rounded-lg"\n'
            '              onClick={() => setProposalFullscreen((value) => !value)}\n'
            '              aria-label={proposalFullscreen ? "Restaurer la fenêtre" : "Agrandir la fenêtre"}\n'
            '              title={proposalFullscreen ? "Restaurer la fenêtre" : "Plein écran"}\n'
            '            >\n'
            '              {proposalFullscreen ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}\n'
            '            </Button>\n'
            '            <Button\n'
            '              type="button"\n'
            '              variant="ghost"\n'
            '              size="icon"\n'
            '              className="size-8 shrink-0 rounded-lg"\n'
            '              onClick={() => setRightOpen(false)}\n'
            '              aria-label="Fermer la proposition"\n'
            '              title="Fermer"\n'
            '            >\n'
            '              <X className="size-4" />\n'
            '            </Button>\n'
        )
        text = text.replace(header_anchor, controls, 1)

    PAGE.write_text(text, encoding="utf-8")

    final = PAGE.read_text(encoding="utf-8")
    checks = {
        "plein ecran": "proposalFullscreen" in final and "fixed inset-2 z-50" in final,
        "bouton restaurer": "Minimize2" in final and "Maximize2" in final,
        "bouton fermer": 'aria-label="Fermer la proposition"' in final,
        "comparatif": "ImprovementPdfComparator" in final,
        "sources": '<TabsTrigger value="sources">Sources</TabsTrigger>' in final,
    }
    for label, ok in checks.items():
        print(f"[{'OK' if ok else 'ERREUR'}] {label}")
        if not ok:
            raise SystemExit(2)

    print("")
    print("V4.03 INSTALLEE.")
    print("Proposition plein ecran par defaut.")
    print("Bouton restaurer disponible.")
    print("Liste Changements retractable.")
    print("Actualise le frontend. Aucun rerun Agent 3 necessaire.")

if __name__ == "__main__":
    main()
