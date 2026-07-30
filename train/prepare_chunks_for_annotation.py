"""
====================================================================
PRÉPARATION DES CHUNKS POUR ANNOTATION CLAUDE.AI
====================================================================

Adapté à TA structure réelle :
    C:\EnnoSmart\projects\projet_X_\chunks.json

Ce script fait :
1. Charge les chunks de TOUS les projets (32 projets)
2. Filtre le bruit (TOC, trop courts, répétitifs)
3. Split les chunks longs (> 350 tokens) en sous-chunks avec overlap
4. Échantillonne les chunks les plus riches en contenu R&D (optionnel)
5. Génère des prompts prêts à coller dans Claude.ai

Usage:
    python prepare_chunks_for_annotation.py
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from collections import Counter

# ============================================================================
# CONFIGURATION - À ADAPTER SI BESOIN
# ============================================================================

BASE_DIR = Path(r"C:\EnnoSmart")
PROJECTS_DIR = BASE_DIR / "projects"

# Où on génère tout
WORK_DIR = BASE_DIR / "llm_annotation"
PROMPTS_DIR = WORK_DIR / "prompts"
RESPONSES_DIR = WORK_DIR / "responses"
STATS_DIR = WORK_DIR / "stats"

# Projets à traiter
PROJECT_IDS = [f"projet_{i}_" for i in range(1, 33)]

# Paramètres batches
CHUNKS_PER_BATCH = 8

# Paramètres filtrage
MIN_TOKENS = 50
MAX_TOKENS = 350
SPLIT_OVERLAP = 30

# Échantillonnage (None = tout garder, ou un nombre par projet)
MAX_CHUNKS_PER_PROJECT = 40  # ex: 60

# Labels
LABELS_CORE = [
    "VERROU_TECH",
    "METHODE_RD",
    "TECHNOLOGIE_RD",
    "EQUIPEMENT_RD",
    "COMPOSANT_TECHNIQUE",
    "MATERIAU_SPECIFIQUE",
    "DOMAINE_RD",
    "RESULTAT_RD",
    "OBJECTIF_RD",
]


# ============================================================================
# DEFINITIONS DES LABELS POUR LE PROMPT
# ============================================================================

LABEL_DEFINITIONS = {
    "VERROU_TECH": {
        "definition": "Obstacle, difficulté, problème technique/scientifique CONCRET à résoudre",
        "examples": [
            "manque de précision des capteurs actuels",
            "absence de modèle prédictif fiable pour la respiration cellulaire",
            "instabilité de la nano-émulsion à température ambiante",
            "difficulté de vectorisation de la perfluorodécaline",
        ],
        "not_examples": [
            "le projet (trop générique)",
            "problème (vague sans précision)",
            "défi (sans contexte)",
        ],
    },
    "METHODE_RD": {
        "definition": "Approche, méthodologie, démarche scientifique précise",
        "examples": [
            "évaluation ex vivo sur explants de peau humaine",
            "analyse par cytométrie en flux",
            "méthode Monte Carlo",
            "test in vitro sur kératinocytes",
        ],
        "not_examples": [
            "méthode (sans précision)",
            "étude (vague)",
        ],
    },
    "TECHNOLOGIE_RD": {
        "definition": "Technologie, framework, plateforme ou paradigme technique",
        "examples": [
            "nano-émulsion",
            "deep learning",
            "spectroscopie Raman",
            "encapsulation liposomale",
        ],
        "not_examples": [
            "technologie (générique)",
            "informatique (trop large)",
        ],
    },
    "EQUIPEMENT_RD": {
        "definition": "Équipement physique, machine, instrument utilisé",
        "examples": [
            "microscope à fluorescence",
            "cytomètre en flux",
            "spectromètre de masse",
            "banc d'essai thermique",
        ],
        "not_examples": [
            "machine (générique)",
            "ordinateur (sans spec)",
        ],
    },
    "COMPOSANT_TECHNIQUE": {
        "definition": "Composant, élément technique d'un système ou produit",
        "examples": [
            "vectorisateur lipidique",
            "capteur infrarouge",
            "algorithme de tri",
            "complexe oxygénant",
        ],
        "not_examples": [
            "composant (générique)",
            "système (trop vague)",
        ],
    },
    "MATERIAU_SPECIFIQUE": {
        "definition": "Matériau, substance ou molécule spécifique nommée",
        "examples": [
            "perfluorodécaline",
            "mélatonine",
            "acide hyaluronique",
            "polymère biosourcé PLA",
        ],
        "not_examples": [
            "matériau (générique)",
            "produit (vague)",
        ],
    },
    "DOMAINE_RD": {
        "definition": "Domaine scientifique, secteur d'application, champ disciplinaire",
        "examples": [
            "cosmétique",
            "respiration cellulaire",
            "intelligence artificielle",
            "dermatologie",
        ],
        "not_examples": [
            "recherche (trop générique)",
            "science (trop large)",
        ],
    },
    "RESULTAT_RD": {
        "definition": "Résultat concret obtenu, performance mesurée, livrable",
        "examples": [
            "augmentation de 30% de l'oxygénation cellulaire",
            "prototype fonctionnel validé",
            "brevet déposé EP123456",
        ],
        "not_examples": [
            "résultat (générique)",
            "succès (vague)",
        ],
    },
    "OBJECTIF_RD": {
        "definition": "Objectif, but ou cible visée par le projet",
        "examples": [
            "stimuler la respiration cellulaire",
            "développer un système autonome",
            "identifier de nouveaux actifs",
        ],
        "not_examples": [
            "but (générique)",
            "amélioration (sans précision)",
        ],
    },
}


# ============================================================================
# DÉTECTION DE BRUIT
# ============================================================================

def is_table_of_contents(text: str) -> bool:
    """Détecte si le chunk est une table des matières"""
    if "table des matières" in text.lower()[:200]:
        return True
    
    lines = [l for l in text.split('\n') if l.strip()]
    if len(lines) < 5:
        return False
    
    # Pattern TOC : "I.1. Titre 4" ou "II.3.2. ... 15"
    toc_pattern = re.compile(r'^\s*[IVX]+\.?\d*\.?\d*\.?\s+.+\s+\d+\s*$')
    toc_lines = sum(1 for line in lines if toc_pattern.match(line))
    
    return toc_lines / len(lines) > 0.3


def is_too_repetitive(text: str) -> bool:
    """Détecte les chunks répétitifs (headers, footers)"""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if len(lines) < 3:
        return False
    
    return len(set(lines)) / len(lines) < 0.4


def has_useful_rd_content(text: str) -> bool:
    """Vérifie qu'il y a du contenu R&D"""
    rd_keywords = [
        'verrou', 'méthode', 'méthodologie', 'technologie', 'développ',
        'recherche', 'objectif', 'résultat', 'expérim', 'mesure',
        'test', 'analyse', 'protocole', 'étude', 'innovation',
        'prototype', 'modèle', 'matériau', 'composant', 'système',
        'validation', 'évaluation', 'optimisation', 'caractérisation',
    ]
    
    text_lower = text.lower()
    keyword_count = sum(1 for kw in rd_keywords if kw in text_lower)
    
    return keyword_count >= 2


def filter_chunk(chunk: Dict) -> Optional[str]:
    """
    Retourne None si le chunk est OK,
    sinon une string décrivant la raison du rejet
    """
    text = chunk.get("text", "").strip()
    
    if not text:
        return "vide"
    
    n_tokens = len(text.split())
    
    if n_tokens < MIN_TOKENS:
        return f"trop court (<{MIN_TOKENS} tokens)"
    
    if is_table_of_contents(text):
        return "table des matières"
    
    if is_too_repetitive(text):
        return "répétitif"
    
    if not has_useful_rd_content(text):
        return "pas de contenu R&D"
    
    return None


# ============================================================================
# SPLIT DES CHUNKS LONGS
# ============================================================================

def create_subchunk(parent: Dict, text: str, sub_idx: int) -> Dict:
    """Crée un sous-chunk depuis un parent"""
    return {
        "chunk_id": f"{parent['chunk_id']}_sub{sub_idx:02d}",
        "project_id": parent["project_id"],
        "source_file": parent.get("source_file", ""),
        "file_name": parent.get("file_name", ""),
        "file_category": parent.get("file_category", ""),
        "source_type": parent.get("source_type", "text"),
        "text": text.strip(),
        "_parent_chunk_id": parent["chunk_id"],
        "_is_subchunk": True,
    }


def split_long_chunk(chunk: Dict) -> List[Dict]:
    """Split un chunk trop long en sous-chunks (par paragraphes/phrases)"""
    text = chunk["text"]
    
    if len(text.split()) <= MAX_TOKENS:
        return [chunk]
    
    # Découper par paragraphes
    paragraphs = text.split('\n\n')
    
    sub_chunks = []
    current_text = ""
    current_tokens = 0
    sub_idx = 0
    
    for para in paragraphs:
        para_tokens = len(para.split())
        
        # Paragraphe seul trop long → split par phrases
        if para_tokens > MAX_TOKENS:
            sentences = re.split(r'(?<=[.!?])\s+', para)
            for sent in sentences:
                sent_tokens = len(sent.split())
                
                if current_tokens + sent_tokens > MAX_TOKENS and current_text:
                    sub_chunks.append(create_subchunk(chunk, current_text, sub_idx))
                    sub_idx += 1
                    # Overlap
                    words = current_text.split()
                    overlap_text = " ".join(words[-SPLIT_OVERLAP:]) if len(words) >= SPLIT_OVERLAP else ""
                    current_text = (overlap_text + " " + sent).strip()
                    current_tokens = len(current_text.split())
                else:
                    current_text = (current_text + "\n" + sent).strip() if current_text else sent
                    current_tokens += sent_tokens
        else:
            if current_tokens + para_tokens > MAX_TOKENS and current_text:
                sub_chunks.append(create_subchunk(chunk, current_text, sub_idx))
                sub_idx += 1
                words = current_text.split()
                overlap_text = " ".join(words[-SPLIT_OVERLAP:]) if len(words) >= SPLIT_OVERLAP else ""
                current_text = (overlap_text + "\n\n" + para).strip()
                current_tokens = len(current_text.split())
            else:
                current_text = (current_text + "\n\n" + para).strip() if current_text else para
                current_tokens += para_tokens
    
    if current_text:
        sub_chunks.append(create_subchunk(chunk, current_text, sub_idx))
    
    return sub_chunks


# ============================================================================
# ÉCHANTILLONNAGE INTELLIGENT
# ============================================================================

def richness_score(text: str) -> int:
    """Score de richesse R&D d'un texte"""
    rd_keywords = [
        'verrou', 'méthode', 'méthodologie', 'technologie', 'développ',
        'objectif', 'résultat', 'expérim', 'protocole', 'innovation',
        'prototype', 'modèle', 'matériau', 'composant', 'analyse',
        'mesure', 'évaluation', 'test', 'validation', 'caractérisation',
        'optimisation', 'simulation', 'algorithme', 'capteur',
    ]
    
    text_lower = text.lower()
    return sum(text_lower.count(kw) for kw in rd_keywords)


def sample_richest_chunks(chunks: List[Dict], max_n: int) -> List[Dict]:
    """Garde les N chunks les plus riches en contenu R&D"""
    scored = [(richness_score(c["text"]), c) for c in chunks]
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:max_n]]


# ============================================================================
# CHARGEMENT
# ============================================================================

def load_chunks_from_project(project_id: str) -> List[Dict]:
    """Charge extracted/chunks.json d'un projet"""
    chunks_file = PROJECTS_DIR / project_id / "extracted" / "chunks.json"
    
    if not chunks_file.exists():
        return []
    
    with open(chunks_file, "r", encoding="utf-8") as f:
        chunks = json.load(f)
    
    fixed_chunks = []
    
    for idx, chunk in enumerate(chunks):
        text = chunk.get("text", "")
        
        if not text or not text.strip():
            continue
        
        chunk_id = (
            chunk.get("chunk_id")
            or chunk.get("source_chunk_id")
            or f"{project_id}_chunk_{idx:05d}"
        )
        
        fixed_chunks.append({
            "chunk_id": str(chunk_id),
            "project_id": project_id,
            "source_file": chunk.get("source_file", ""),
            "file_name": chunk.get("file_name", chunk.get("source_file", "")),
            "file_category": chunk.get("file_category", ""),
            "source_type": chunk.get("source_type", "text"),
            "text": text,
            "_source_json": str(chunks_file),
        })
    
    return fixed_chunks


def process_all_projects() -> Tuple[List[Dict], Dict, Dict]:
    """Pipeline complet : chargement + filtrage + split + sampling"""
    
    all_chunks = []
    rejection_stats = Counter()
    project_stats = {}
    
    print("\n📂 Traitement des projets")
    print("-" * 70)
    print(f"{'Projet':<15} {'Bruts':>8} {'Filtrés':>8} {'Gardés':>8} {'Sub':>6}")
    print("-" * 70)
    
    for project_id in PROJECT_IDS:
        raw_chunks = load_chunks_from_project(project_id)
        
        if not raw_chunks:
            project_stats[project_id] = {"raw": 0, "filtered": 0, "kept": 0, "sub": 0}
            print(f"{project_id:<15} {'MISSING':>8}")
            continue
        
        # Filtrage
        filtered_chunks = []
        for chunk in raw_chunks:
            rejection = filter_chunk(chunk)
            if rejection:
                rejection_stats[rejection] += 1
            else:
                filtered_chunks.append(chunk)
        
        # Split des chunks longs
        split_chunks = []
        for chunk in filtered_chunks:
            split_chunks.extend(split_long_chunk(chunk))
        
        # Sampling optionnel
        if MAX_CHUNKS_PER_PROJECT and len(split_chunks) > MAX_CHUNKS_PER_PROJECT:
            split_chunks = sample_richest_chunks(split_chunks, MAX_CHUNKS_PER_PROJECT)
        
        n_sub = sum(1 for c in split_chunks if c.get("_is_subchunk"))
        
        project_stats[project_id] = {
            "raw": len(raw_chunks),
            "filtered": len(filtered_chunks),
            "kept": len(split_chunks),
            "sub": n_sub,
        }
        
        all_chunks.extend(split_chunks)
        
        print(f"{project_id:<15} {len(raw_chunks):>8} {len(filtered_chunks):>8} "
              f"{len(split_chunks):>8} {n_sub:>6}")
    
    print("-" * 70)
    print(f"{'TOTAL':<15} {sum(s['raw'] for s in project_stats.values()):>8} "
          f"{sum(s['filtered'] for s in project_stats.values()):>8} "
          f"{len(all_chunks):>8}")
    
    return all_chunks, rejection_stats, project_stats


# ============================================================================
# CONSTRUCTION DES PROMPTS
# ============================================================================

def build_definitions_section() -> str:
    sections = []
    for label in LABELS_CORE:
        info = LABEL_DEFINITIONS[label]
        examples = "\n".join([f'     - "{ex}"' for ex in info["examples"]])
        not_examples = "\n".join([f'     - "{ex}"' for ex in info["not_examples"]])
        
        sections.append(f"""**{label}**
- Définition : {info["definition"]}
- ✅ Exemples valides :
{examples}
- ❌ NE PAS annoter :
{not_examples}""")
    
    return "\n\n".join(sections)


def build_prompt_for_batch(batch: List[Dict], batch_id: int, total_batches: int) -> str:
    definitions = build_definitions_section()
    
    chunks_text = []
    for chunk in batch:
        chunks_text.append(
            f"### CHUNK_ID: {chunk['chunk_id']}\n"
            f"```\n{chunk['text']}\n```"
        )
    chunks_str = "\n\n".join(chunks_text)
    
    return f"""# MISSION : ANNOTATION NER POUR PROJETS R&D (CIR)

Tu es un expert annotateur pour des projets de R&D éligibles au Crédit Impôt Recherche français.
Ta tâche : extraire des entités nommées avec une **précision MAXIMALE**.

---

# DÉFINITIONS DES 9 LABELS

{definitions}

---

# RÈGLES STRICTES

1. **N'annote QUE ce qui correspond CLAIREMENT à une catégorie**
2. **NE JAMAIS inventer** d'entités absentes du texte
3. Les positions `start` et `end` doivent être **EXACTES** (positions de caractère dans le chunk)
4. **Préfère ne pas annoter** plutôt que d'annoter incorrectement
5. Les entités doivent être **SPÉCIFIQUES** (pas trop génériques)
6. **Évite les chevauchements** entre entités
7. Vérification : `chunk_text[start:end] == entity_text` doit être VRAI

---

# CHUNKS À ANNOTER (batch {batch_id}/{total_batches}, {len(batch)} chunks)

{chunks_str}

---

# FORMAT DE RÉPONSE ATTENDU

Réponds UNIQUEMENT avec ce JSON dans un bloc ```json ... ``` :

```json
{{
  "batch_id": {batch_id},
  "annotations": [
    {{
      "chunk_id": "ID_DU_CHUNK",
      "entities": [
        {{"text": "texte exact", "label": "VERROU_TECH", "start": 0, "end": 10}}
      ]
    }}
  ]
}}
```"""


# ============================================================================
# GÉNÉRATION FICHIERS
# ============================================================================

def generate_prompt_files(chunks: List[Dict]) -> int:
    """Génère un fichier par batch + l'index"""
    
    n_batches = (len(chunks) + CHUNKS_PER_BATCH - 1) // CHUNKS_PER_BATCH
    
    print(f"\n📦 Génération de {n_batches} batches...")
    
    index = []
    
    for batch_idx in range(n_batches):
        start = batch_idx * CHUNKS_PER_BATCH
        end = min(start + CHUNKS_PER_BATCH, len(chunks))
        batch = chunks[start:end]
        batch_id = batch_idx + 1
        
        # Prompt à coller dans Claude.ai
        prompt = build_prompt_for_batch(batch, batch_id, n_batches)
        prompt_file = PROMPTS_DIR / f"batch_{batch_id:04d}.txt"
        with open(prompt_file, "w", encoding="utf-8") as f:
            f.write(prompt)
        
        # Mapping pour le merge
        mapping_file = PROMPTS_DIR / f"batch_{batch_id:04d}_mapping.json"
        with open(mapping_file, "w", encoding="utf-8") as f:
            json.dump({
                "batch_id": batch_id,
                "chunks": batch,
            }, f, ensure_ascii=False, indent=2)
        
        index.append({
            "batch_id": batch_id,
            "prompt_file": prompt_file.name,
            "mapping_file": mapping_file.name,
            "expected_response_file": f"batch_{batch_id:04d}_response.json",
            "n_chunks": len(batch),
            "chunk_ids": [c["chunk_id"] for c in batch],
            "status": "pending",
        })
    
    # Index global
    with open(WORK_DIR / "index.json", "w", encoding="utf-8") as f:
        json.dump({
            "total_batches": n_batches,
            "total_chunks": len(chunks),
            "chunks_per_batch": CHUNKS_PER_BATCH,
            "batches": index,
        }, f, ensure_ascii=False, indent=2)
    
    return n_batches


def write_readme(total_chunks: int, n_batches: int, rejection_stats: Counter) -> None:
    """Génère le README avec instructions"""
    
    rejection_table = "\n".join([
        f"| {reason} | {count} |"
        for reason, count in rejection_stats.most_common()
    ])
    
    estimated_hours = round(n_batches * 2 / 60, 1)
    estimated_days = max(1, n_batches // 30)
    
    readme = f"""# 📋 GUIDE D'ANNOTATION CLAUDE.AI

## 📊 Statistiques

- **{total_chunks} chunks** à annoter
- **{n_batches} batches** de {CHUNKS_PER_BATCH} chunks chacun
- Estimation : **~{estimated_hours}h** de travail (~{estimated_days} jours)

### Chunks rejetés (filtrage automatique)

| Raison | Nombre |
|--------|--------|
{rejection_table}

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
**30 batches/jour** tranquille → {n_batches} batches en {estimated_days} jours

## 📁 Structure

```
{WORK_DIR}/
├── prompts/         ← À copier-coller
├── responses/       ← Mets les réponses ici
├── stats/           ← Statistiques détaillées
├── index.json
└── README.md
```
"""
    
    with open(WORK_DIR / "README.md", "w", encoding="utf-8") as f:
        f.write(readme)


def save_stats(project_stats: Dict, rejection_stats: Counter, total_chunks: int, n_batches: int) -> None:
    """Sauvegarde toutes les stats dans le dossier stats/"""
    
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Stats par projet
    with open(STATS_DIR / "project_stats.json", "w", encoding="utf-8") as f:
        json.dump(project_stats, f, ensure_ascii=False, indent=2)
    
    # Stats de rejet
    with open(STATS_DIR / "rejection_stats.json", "w", encoding="utf-8") as f:
        json.dump(dict(rejection_stats), f, ensure_ascii=False, indent=2)
    
    # Summary
    with open(STATS_DIR / "summary.json", "w", encoding="utf-8") as f:
        json.dump({
            "total_projects": len(PROJECT_IDS),
            "total_chunks_kept": total_chunks,
            "total_batches": n_batches,
            "chunks_per_batch": CHUNKS_PER_BATCH,
            "filtering": {
                "min_tokens": MIN_TOKENS,
                "max_tokens": MAX_TOKENS,
                "split_overlap": SPLIT_OVERLAP,
            },
            "rejection_summary": dict(rejection_stats),
        }, f, ensure_ascii=False, indent=2)


# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 70)
    print("🤖 PRÉPARATION CHUNKS POUR ANNOTATION CLAUDE.AI")
    print("=" * 70)
    
    # Créer les dossiers
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    RESPONSES_DIR.mkdir(parents=True, exist_ok=True)
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    
    # Traitement
    all_chunks, rejection_stats, project_stats = process_all_projects()
    
    if not all_chunks:
        print("\n❌ Aucun chunk à annoter. Vérifie tes fichiers chunks.json")
        return
    
    print(f"\n📊 Récap rejets :")
    for reason, count in rejection_stats.most_common():
        print(f"   {reason:30s} : {count}")
    
    # Générer prompts
    n_batches = generate_prompt_files(all_chunks)
    
    # Sauvegarder stats
    save_stats(project_stats, rejection_stats, len(all_chunks), n_batches)
    
    # README
    write_readme(len(all_chunks), n_batches, rejection_stats)
    
    print("\n" + "=" * 70)
    print("✅ PRÉPARATION TERMINÉE")
    print("=" * 70)
    print(f"\n📁 Dossier : {WORK_DIR}")
    print(f"📝 {n_batches} prompts dans : {PROMPTS_DIR}")
    print(f"📂 Mets les réponses dans : {RESPONSES_DIR}")
    print(f"📖 Lis : {WORK_DIR / 'README.md'}")
    print(f"\n⏱️ Estimation : ~{round(n_batches * 2 / 60, 1)}h de travail")
    print(f"\n👉 Étape suivante : ouvre {PROMPTS_DIR / 'batch_0001.txt'}")


if __name__ == "__main__":
    main()