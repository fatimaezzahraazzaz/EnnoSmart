from __future__ import annotations

import re
import shutil
from pathlib import Path

REPO = Path(r"C:\EnnoSmart")
PAGE = REPO / "frontend" / "components" / "ennosmart" / "ennoamelioration-page.tsx"


def fail(message: str) -> None:
    print(f"[ERREUR] {message}")
    raise SystemExit(1)


def backup(path: Path, suffix: str) -> Path:
    target = path.with_name(path.name + suffix)
    shutil.copy2(path, target)
    print(f"[BACKUP] {target}")
    return target


def remove_bottom_actions(text: str) -> tuple[str, bool]:
    """
    Supprime la grande barre Rejeter / Accepter en bas.
    Plusieurs regex sont prévues pour tolérer les changements de formatage TSX.
    """
    if 'aria-label="Rejeter la proposition"' in text:
        # Une version compacte est peut-être déjà présente ; on retire quand même
        # une éventuelle ancienne grande barre si elle existe.
        pass

    patterns = [
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

    for pattern in patterns:
        new_text, count = pattern.subn("", text, count=1)
        if count == 1:
            return new_text, True

    # Peut déjà avoir été supprimée.
    if (
        'decide("rejected")' not in text
        or 'grid grid-cols-2 gap-2 border-t p-3' not in text
    ):
        return text, False

    return text, False


def inject_compact_actions_before_fullscreen(text: str) -> tuple[str, bool]:
    """
    V4.05 ne dépend plus du texte exact du Badge Proposition.
    On s'ancre sur le bouton Plein écran ajouté par V4.03, qui est stable.
    """
    if 'aria-label="Rejeter la proposition"' in text:
        return text, False

    fullscreen_marker = (
        'aria-label={proposalFullscreen ? "Restaurer la fenêtre" : "Agrandir la fenêtre"}'
    )
    marker_index = text.find(fullscreen_marker)

    if marker_index < 0:
        # Fallback : repérer le handler plein écran.
        fallback_marker = "onClick={() => setProposalFullscreen((value) => !value)}"
        marker_index = text.find(fallback_marker)

    if marker_index < 0:
        fail(
            "Le bouton Plein ecran V4.03 est introuvable. "
            "La page locale n'est probablement pas en V4.03."
        )

    # Retrouver le <Button ...> qui contient ce marker.
    button_start = text.rfind("<Button", 0, marker_index)
    if button_start < 0:
        fail("Impossible de localiser le debut du bouton Plein ecran.")

    # Garder l'indentation du bouton existant.
    line_start = text.rfind("\n", 0, button_start) + 1
    indent = text[line_start:button_start]

    compact = (
        f'{indent}{{candidate && (\n'
        f'{indent}  <div className="flex shrink-0 items-center gap-1">\n'
        f'{indent}    <Button\n'
        f'{indent}      type="button"\n'
        f'{indent}      variant="outline"\n'
        f'{indent}      size="icon"\n'
        f'{indent}      className="size-8 rounded-lg border-rose-200 text-rose-600 hover:bg-rose-50 hover:text-rose-700"\n'
        f'{indent}      disabled={{busy}}\n'
        f'{indent}      onClick={{() => decide("rejected")}}\n'
        f'{indent}      aria-label="Rejeter la proposition"\n'
        f'{indent}      title="Rejeter la proposition"\n'
        f'{indent}    >\n'
        f'{indent}      <X className="size-4" />\n'
        f'{indent}    </Button>\n'
        f'{indent}    <Button\n'
        f'{indent}      type="button"\n'
        f'{indent}      size="icon"\n'
        f'{indent}      className="size-8 rounded-lg"\n'
        f'{indent}      disabled={{busy}}\n'
        f'{indent}      onClick={{() => decide("accepted")}}\n'
        f'{indent}      aria-label="Accepter la proposition"\n'
        f'{indent}      title="Accepter la proposition"\n'
        f'{indent}    >\n'
        f'{indent}      <Check className="size-4" />\n'
        f'{indent}    </Button>\n'
        f'{indent}  </div>\n'
        f'{indent})}}\n'
    )

    return text[:line_start] + compact + text[line_start:], True


def main() -> None:
    print("=== EnnoSmart Agent 3 - Actions compactes V4.05 ===")

    if not PAGE.exists():
        fail(f"Page Agent 3 introuvable : {PAGE}")

    backup(PAGE, ".before-agent3-v405")

    text = PAGE.read_text(encoding="utf-8")

    text, bottom_removed = remove_bottom_actions(text)
    text, compact_added = inject_compact_actions_before_fullscreen(text)

    PAGE.write_text(text, encoding="utf-8")

    final = PAGE.read_text(encoding="utf-8")

    checks = {
        "Rejeter compact": 'aria-label="Rejeter la proposition"' in final,
        "Accepter compact": 'aria-label="Accepter la proposition"' in final,
        "grande barre supprimee": (
            'grid grid-cols-2 gap-2 border-t p-3' not in final
        ),
        "plein ecran conserve": (
            "proposalFullscreen" in final
            and (
                'aria-label={proposalFullscreen ? "Restaurer la fenêtre" : "Agrandir la fenêtre"}'
                in final
                or "setProposalFullscreen" in final
            )
        ),
        "comparatif conserve": "ImprovementPdfComparator" in final,
        "sources conserve": '<TabsTrigger value="sources">Sources</TabsTrigger>' in final,
    }

    print(f"[INFO] Grande barre retiree maintenant : {bottom_removed}")
    print(f"[INFO] Actions compactes ajoutees maintenant : {compact_added}")

    for label, ok in checks.items():
        print(f"[{'OK' if ok else 'ERREUR'}] {label}")
        if not ok:
            raise SystemExit(2)

    print("")
    print("V4.05 INSTALLEE.")
    print("La V4.05 ne cherche plus le Badge Proposition.")
    print("Elle s'ancre directement sur le bouton Plein ecran V4.03.")
    print("Actualise le frontend. Aucun rerun Agent 3 n'est necessaire.")


if __name__ == "__main__":
    main()
