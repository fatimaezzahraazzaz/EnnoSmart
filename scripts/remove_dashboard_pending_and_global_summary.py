from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
target = ROOT / "frontend" / "components" / "ennosmart" / "dashboard-page.tsx"

if not target.exists():
    raise SystemExit(f"Fichier introuvable : {target}")

text = target.read_text(encoding="utf-8")
original = text

def remove_jsx_card_containing(src: str, marker: str) -> tuple[str, bool]:
    idx = src.find(marker)
    if idx == -1:
        return src, False

    start = src.rfind("<Card", 0, idx)
    if start == -1:
        return src, False

    token_re = re.compile(r"<Card\b|</Card>")
    depth = 0
    end = -1

    for m in token_re.finditer(src, start):
        token = m.group(0)
        if token.startswith("<Card"):
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                end = m.end()
                break

    if end == -1:
        return src, False

    # Supprime les lignes vides autour du bloc.
    while start > 0 and src[start - 1] in " \t":
        start -= 1
    if start > 0 and src[start - 1] == "\n":
        start -= 1

    while end < len(src) and src[end] in " \t\r\n":
        end += 1

    return src[:start] + "\n" + src[end:], True

text, removed_pending = remove_jsx_card_containing(text, "Analyses en attente")
text, removed_global = remove_jsx_card_containing(text, "Synthèse globale")

# La grille des KPI passe de 4 cartes à 3 cartes.
text = text.replace(
    'className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4"',
    'className="grid grid-cols-1 md:grid-cols-3 gap-4"',
)
text = text.replace(
    'className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4"',
    'className="grid grid-cols-1 md:grid-cols-3 gap-4"',
)

# Supprime l'import Clock si le bouton "Analyses en attente" a été retiré.
if "Analyses en attente" not in text:
    text = re.sub(r"\n\s*Clock,\s*", "\n", text)

target.write_text(text, encoding="utf-8")

print("✅ dashboard-page.tsx mis à jour")
print(f"- Carte 'Analyses en attente' supprimée : {removed_pending}")
print(f"- Bloc 'Synthèse globale' supprimé : {removed_global}")

if text == original:
    print("⚠️ Aucun changement détecté. Vérifie que le texte existe encore dans dashboard-page.tsx.")
