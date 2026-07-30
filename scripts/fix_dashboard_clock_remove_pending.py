from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
target = ROOT / "frontend" / "components" / "ennosmart" / "dashboard-page.tsx"

if not target.exists():
    raise SystemExit(f"Fichier introuvable : {target}")

text = target.read_text(encoding="utf-8")
original = text

def remove_card_around_marker(src: str, marker: str) -> tuple[str, bool]:
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

    while start > 0 and src[start - 1] in " \t":
        start -= 1
    if start > 0 and src[start - 1] == "\n":
        start -= 1

    while end < len(src) and src[end] in " \t\r\n":
        end += 1

    return src[:start] + "\n" + src[end:], True

removed = False

# Supprime la carte "Analyses en attente", même si le texte a été modifié.
for marker in [
    "Analyses en attente",
    "analyses en attente",
    "validations consultant",
    "<Clock",
]:
    text, ok = remove_card_around_marker(text, marker)
    removed = removed or ok

# Supprime le bloc Synthèse globale si encore présent.
for marker in [
    "Synthèse globale",
    "Synthese globale",
    "Articles totaux",
]:
    text, ok = remove_card_around_marker(text, marker)
    removed = removed or ok

# Ajuste la grille KPI à 3 cartes.
text = text.replace(
    'className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4"',
    'className="grid grid-cols-1 md:grid-cols-3 gap-4"',
)
text = text.replace(
    'className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4"',
    'className="grid grid-cols-1 md:grid-cols-3 gap-4"',
)

# Nettoie l'import Clock uniquement s'il n'est plus utilisé.
body_without_import = text.split("from \"lucide-react\"", 1)[-1] if "from \"lucide-react\"" in text else text
if "<Clock" not in body_without_import and "Clock className" not in body_without_import:
    text = re.sub(r"\n\s*Clock,\s*", "\n", text)

target.write_text(text, encoding="utf-8")

print("✅ Correction terminée : dashboard-page.tsx")
print(f"- Carte contenant Clock / Analyses en attente supprimée : {removed}")
print(f"- Clock encore présent dans le fichier : {'Clock' in text}")
print(f"- Synthèse globale encore présente : {'Synthèse globale' in text or 'Articles totaux' in text}")

if text == original:
    print("⚠️ Aucun changement détecté. Le fichier était peut-être déjà modifié autrement.")
