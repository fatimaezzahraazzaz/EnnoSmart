from pathlib import Path

target = Path(r'C:\EnnoSmart\frontend\components\ennosmart\new-project-page.tsx')
if not target.exists():
    raise SystemExit(f'Fichier introuvable: {target}')

text = target.read_text(encoding='utf-8')
original = text

anchors = [
    (
        '                          {catalog.organisations.map((item) => (',
        '                          <option value="__other__">+ Ajouter un nouvel organisme</option>\n'
        '                          {catalog.organisations.map((item) => ('
    ),
    (
        '                        {availableProjects.map((item) => (',
        '                        <option value="__other__">+ Ajouter un nouveau projet</option>\n'
        '                        {availableProjects.map((item) => ('
    ),
    (
        '                        {availableSubprojects.map((item) => (',
        '                        <option value="__other__">+ Ajouter un sous-projet</option>\n'
        '                        {availableSubprojects.map((item) => ('
    ),
]

for old, new in anchors:
    if old not in text:
        raise SystemExit(f'Motif introuvable: {old}')
    text = text.replace(old, new, 1)

backup = target.with_name('new-project-page.before-other-option-fix.tsx')
if not backup.exists():
    backup.write_text(original, encoding='utf-8')

target.write_text(text, encoding='utf-8')

print('CORRECTION APPLIQUEE')
print('Fichier :', target)
print('Backup  :', backup)
print('Ajouts: organisme/projet/sous-projet __other__')
