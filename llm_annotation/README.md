# 📋 GUIDE D'ANNOTATION CLAUDE.AI

## 📊 Statistiques

- **1077 chunks** à annoter
- **135 batches** de 8 chunks chacun
- Estimation : **~4.5h** de travail (~4 jours)

### Chunks rejetés (filtrage automatique)

| Raison | Nombre |
|--------|--------|
| pas de contenu R&D | 1239 |
| trop court (<50 tokens) | 391 |
| table des matières | 50 |

## 🎯 Workflow

### 1. Ouvrir Claude.ai

- https://claude.ai
- Connecte-toi (compte Pro)
- Nouveau chat
- Sélectionne **Claude Opus 4.7** (qualité max) ou **Sonnet 4.7**

### 2. Pour chaque batch

```
1. Ouvre prompts/batch_0001.txt
2. Copie tout (Ctrl+A, Ctrl+C)
3. Colle dans Claude.ai → Envoie
4. Copie le JSON retourné
5. Crée responses/batch_0001_response.json
6. Colle le JSON → Sauvegarde
7. Batch suivant
```

### 3. Conseils

- 🔄 **Nouveau chat tous les 5-10 batches**
- ⚡ **Paralléliser** : 3-4 onglets Claude.ai
- 🎯 Si Claude bavarde : "JUSTE le JSON, sans texte"
- 📝 Vérifie 1 batch sur 10 (qualité)

### 4. Fusion finale

```bash
python merge_responses.py
```

## ⚠️ Limites Claude Pro

~45 messages / 5h. Si limite atteinte → pause ou autre session.

## 🚀 Astuce vitesse

**4 onglets en parallèle** = 4 batches en ~3 min
**30 batches/jour** tranquille → 135 batches en 4 jours

## 📁 Structure

```
C:\EnnoSmart\llm_annotation/
├── prompts/         ← À copier-coller
├── responses/       ← Mets les réponses ici
├── stats/           ← Statistiques détaillées
├── index.json
└── README.md
```
