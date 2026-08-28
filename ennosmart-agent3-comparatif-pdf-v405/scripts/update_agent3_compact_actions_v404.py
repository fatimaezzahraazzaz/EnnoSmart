from __future__ import annotations

import re
import shutil
from pathlib import Path

REPO = Path(r"C:\EnnoSmart")
PAGE = REPO / "frontend" / "components" / "ennosmart" / "ennoamelioration-page.tsx"


def fail(message: str) -> None:
    print(f"[ERREUR] {message}")
    raise SystemExit(1)


def main() -> None:
    print("=== EnnoSmart Agent 3 - Actions compactes V4.04 ===")

    if not PAGE.exists():
        fail(f"Page Agent 3 introuvable : {PAGE}")

    backup = PAGE.with_name(PAGE.name + ".before-agent3-v404")
    shutil.copy2(PAGE, backup)
    print(f"[BACKUP] {backup}")

    text = PAGE.read_text(encoding="utf-8")

    bottom_pattern = re.compile(
        r'\n\s*\{candidate && \(\s*'
        r'<div className="grid grid-cols-2 gap-2 border-t p-3">\s*'
        r'<Button variant="outline" className="gap-2" disabled=\{busy\} onClick=\{\(\) => decide\("rejected"\)\}>'
        r'<X className="size-4" /> Rejeter</Button>\s*'
        r'<Button className="gap-2" disabled=\{busy\} onClick=\{\(\) => decide\("accepted"\)\}>'
        r'<Check className="size-4" /> Accepter</Button>\s*'
        r'</div>\s*'
        r'\)\}',
        flags=re.S,
    )

    text, removed = bottom_pattern.subn("", text, count=1)

    if removed != 1 and 'aria-label="Rejeter la proposition"' not in text:
        fail("Les grands boutons Rejeter / Accepter n'ont pas été trouvés.")

    if 'aria-label="Rejeter la proposition"' not in text:
        header_anchor = (
            '            {candidate ? <Badge>Proposition V{candidate.version_number}</Badge> '
            ': <Badge variant="outline">Version active</Badge>}\\n'
        )

        if header_anchor not in text:
            fail("Badge Proposition introuvable dans le header.")

        compact_actions = (
            header_anchor
            + '            {candidate && (\\n'
            + '              <div className="flex shrink-0 items-center gap-1">\\n'
            + '                <Button\\n'
            + '                  type="button"\\n'
            + '                  variant="outline"\\n'
            + '                  size="icon"\\n'
            + '                  className="size-8 rounded-lg border-rose-200 text-rose-600 hover:bg-rose-50 hover:text-rose-700"\\n'
            + '                  disabled={busy}\\n'
            + '                  onClick={() => decide("rejected")}\\n'
            + '                  aria-label="Rejeter la proposition"\\n'
            + '                  title="Rejeter la proposition"\\n'
            + '                >\\n'
            + '                  <X className="size-4" />\\n'
            + '                </Button>\\n'
            + '                <Button\\n'
            + '                  type="button"\\n'
            + '                  size="icon"\\n'
            + '                  className="size-8 rounded-lg"\\n'
            + '                  disabled={busy}\\n'
            + '                  onClick={() => decide("accepted")}\\n'
            + '                  aria-label="Accepter la proposition"\\n'
            + '                  title="Accepter la proposition"\\n'
            + '                >\\n'
            + '                  <Check className="size-4" />\\n'
            + '                </Button>\\n'
            + '              </div>\\n'
            + '            )}\\n'
        )

        text = text.replace(header_anchor, compact_actions, 1)

    PAGE.write_text(text, encoding="utf-8")

    final = PAGE.read_text(encoding="utf-8")

    checks = {
        "Rejeter compact": 'aria-label="Rejeter la proposition"' in final,
        "Accepter compact": 'aria-label="Accepter la proposition"' in final,
        "grande barre supprimee": 'grid grid-cols-2 gap-2 border-t p-3' not in final,
        "plein ecran conserve": "proposalFullscreen" in final,
        "comparatif conserve": "ImprovementPdfComparator" in final,
        "sources conserve": '<TabsTrigger value="sources">Sources</TabsTrigger>' in final,
    }

    for label, ok in checks.items():
        print(f"[{'OK' if ok else 'ERREUR'}] {label}")
        if not ok:
            raise SystemExit(2)

    print("")
    print("V4.04 INSTALLEE.")
    print("Rejeter / Accepter sont maintenant dans le header.")
    print("La grande barre sous les PDF a ete supprimee.")
    print("Aucun rerun Agent 3 n'est necessaire.")


if __name__ == "__main__":
    main()
