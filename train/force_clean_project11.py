from pathlib import Path
import json
import re

INPUT = Path(r"C:\EnnoSmart\projects\projet_11_\annotations\ner_candidates.json")
OUTPUT = Path(r"C:\EnnoSmart\projects\projet_11_\annotations\ner_candidates_clean.json")

KEEP_SECTION_TITLES = True

REMOVE_EXACT = {
    # Bruit tableau / générique
    "thésaurus",
    "nom",
    "prénom",
    "fonction",
    "heures",
    "chef de projet",
    "objectif",
    "objectifs",
    "résultat",
    "résultats",
    "les résultats",
    "résultats obtenus",
    "auteurs",
    "les auteurs",
    "développeur",
    "développeurs",
    "développeur humain",
    "utilisateur",
    "utilisateurs",
    "personnes",
    "personnes sans expertise technique",
    "industriels",
    "entreprises",

    # Auteurs état de l'art
    "fu et al",
    "siddiq et al",
    "tufano et al",
    "alagarsamy al",
    "wang et al",
    "chen et al",
    "lewis et al",
    "li et al",
    "ni et al",
    "wei et al",
    "gao et al",
    "chen",
    "song",
    "jiawei liu",
    "abhinav jain",

    # Trop générique / mal labellisé
    "méthode cible",
    "méthode à tester",
    "generator",
    "générateur",
    "sample pruning",
    "reranking",
    "résultats d’exécution",
    "résultats d'exécution",
    "taux de succès à l’exécution",
    "taux de succès à l'exécution",
    "domaine language-to-code",
    "langage-to-code",
    "table qa",
    "math qa",

    # Acronymes trop ambigus / non utiles seuls
    "mat",
    "tu",
    "df",
    "résultats de l’exécution",
    "résultats de l'exécution",
    "résultats prometteurs",
}

AUTO_FIX = {
    # ORGANISMES
    "scalian": "ORGANISME",
    "github": "ORGANISME",
    "openai": "ORGANISME",
    "hugging face": "ORGANISME",
    "confident-ai": "ORGANISME",
    "deepeval": "TECHNOLOGIE_RD",
    "cen simu": "ORGANISME",

    # DOMAINES
    "intelligence artificielle": "DOMAINE_RD",
    "ia générative": "DOMAINE_RD",
    "génie logiciel": "DOMAINE_RD",
    "développement logiciel": "DOMAINE_RD",
    "génération automatique de code": "DOMAINE_RD",
    "génération automatique de tests unitaires": "DOMAINE_RD",
    "génération de tests unitaires": "DOMAINE_RD",
    "génération de tests unitaires java": "DOMAINE_RD",
    "génération automatique de cas de tests unitaires": "DOMAINE_RD",
    "tests logiciels": "DOMAINE_RD",
    "tests unitaires": "DOMAINE_RD",
    "language-to-code": "DOMAINE_RD",
    "llm": "TECHNOLOGIE_RD",
    "llms": "TECHNOLOGIE_RD",
    "llm’s": "TECHNOLOGIE_RD",
    "large language models": "TECHNOLOGIE_RD",

    # TECHNOLOGIES / MODELES / OUTILS
    "ai code": "TECHNOLOGIE_RD",
    "github copilot": "TECHNOLOGIE_RD",
    "copilot": "TECHNOLOGIE_RD",
    "codex": "TECHNOLOGIE_RD",
    "gpt-3": "TECHNOLOGIE_RD",
    "gpt-3.5": "TECHNOLOGIE_RD",
    "gpt-3.5-turbo": "TECHNOLOGIE_RD",
    "gpt-4": "TECHNOLOGIE_RD",
    "chatgpt": "TECHNOLOGIE_RD",
    "starcoder": "TECHNOLOGIE_RD",
    "starcoder2-7b": "TECHNOLOGIE_RD",
    "starcoder2-7b-instruct": "TECHNOLOGIE_RD",
    "code-mistral-7b": "TECHNOLOGIE_RD",
    "code-mistral": "TECHNOLOGIE_RD",
    "codellama": "TECHNOLOGIE_RD",
    "codellama-7b-hf": "TECHNOLOGIE_RD",
    "codellama-7b-hf-instruct": "TECHNOLOGIE_RD",
    "codegemma": "TECHNOLOGIE_RD",
    "codegemma-7b": "TECHNOLOGIE_RD",
    "codegemma-7b-instruct": "TECHNOLOGIE_RD",
    "qwen2,5-coder-7b": "TECHNOLOGIE_RD",
    "qwen2.5-coder-7b": "TECHNOLOGIE_RD",
    "qwen2,5-coder-7b-instruct": "TECHNOLOGIE_RD",
    "qwen2.5-coder-7b-instruct": "TECHNOLOGIE_RD",
    "qwen2.5-coder": "TECHNOLOGIE_RD",
    "mistral": "TECHNOLOGIE_RD",
    "instructgpt": "TECHNOLOGIE_RD",
    "codegen": "TECHNOLOGIE_RD",

    "java": "TECHNOLOGIE_RD",
    "junit": "TECHNOLOGIE_RD",
    "junit 5": "TECHNOLOGIE_RD",
    "mockito": "TECHNOLOGIE_RD",
    "maven": "TECHNOLOGIE_RD",
    "jacoco": "TECHNOLOGIE_RD",
    "spring": "TECHNOLOGIE_RD",
    "angular": "TECHNOLOGIE_RD",
    "sonarqube": "TECHNOLOGIE_RD",
    "ollama": "TECHNOLOGIE_RD",
    "hugging face": "TECHNOLOGIE_RD",
    "api": "TECHNOLOGIE_RD",
    "apis": "TECHNOLOGIE_RD",
    "cwe": "TECHNOLOGIE_RD",
    "common weakness enumeration": "TECHNOLOGIE_RD",
    "defects4j": "TECHNOLOGIE_RD",
    "sf110": "TECHNOLOGIE_RD",
    "evosuite": "TECHNOLOGIE_RD",
    "evosuite sf110": "TECHNOLOGIE_RD",
    "humaneval": "TECHNOLOGIE_RD",
    "method2test": "TECHNOLOGIE_RD",
    "methods2test": "TECHNOLOGIE_RD",
    "méthodotest": "TECHNOLOGIE_RD",
    "athenatest": "TECHNOLOGIE_RD",
    "a3test": "TECHNOLOGIE_RD",
    "at3test": "TECHNOLOGIE_RD",
    "lever": "TECHNOLOGIE_RD",
    "evalplus": "TECHNOLOGIE_RD",
    "rlcf": "TECHNOLOGIE_RD",
    "chattester": "TECHNOLOGIE_RD",
    "utbotjava": "TECHNOLOGIE_RD",
    "utbotcpp": "TECHNOLOGIE_RD",
    "unittestbot": "TECHNOLOGIE_RD",

    # METHODES / APPROCHES
    "test-driven development": "METHODE_RD",
    "test-drivent development": "METHODE_RD",
    "tdd": "METHODE_RD",
    "validation formelle": "METHODE_RD",
    "prompt engineering": "METHODE_RD",
    "zero-shot": "METHODE_RD",
    "zero-shot learning": "METHODE_RD",
    "few-shot": "METHODE_RD",
    "few-shots": "METHODE_RD",
    "few-shot learning": "METHODE_RD",
    "few-shots learning": "METHODE_RD",
    "chain-of-thought": "METHODE_RD",
    "chain of thought": "METHODE_RD",
    "chain of thoughts prompting": "METHODE_RD",
    "cot": "METHODE_RD",
    "structured chain-of-thought": "METHODE_RD",
    "structured chain of thought": "METHODE_RD",
    "scot": "METHODE_RD",
    "scot4ut": "METHODE_RD",
    "scot aaa": "METHODE_RD",
    "arrange-act-assert": "METHODE_RD",
    "aaa": "METHODE_RD",
    "auto-consistance universelle": "METHODE_RD",
    "universal self-consistency": "METHODE_RD",
    "usc": "METHODE_RD",
    "usc4ut": "METHODE_RD",
    "retrieval-augmented generation": "METHODE_RD",
    "retrieval-augmented generation (rag": "METHODE_RD",
    "rag": "METHODE_RD",
    "rag4ut": "METHODE_RD",
    "mixture-of-agents": "METHODE_RD",
    "mélange d’agents": "METHODE_RD",
    "mélange d'agents": "METHODE_RD",
    "moa": "METHODE_RD",
    "pal": "METHODE_RD",
    "pot": "METHODE_RD",
    "ast": "TECHNOLOGIE_RD",
    "bart": "TECHNOLOGIE_RD",
    "rest assured": "TECHNOLOGIE_RD",
    "apache commons lang": "TECHNOLOGIE_RD",
    "jfreechart": "TECHNOLOGIE_RD",
    "apache common cli": "TECHNOLOGIE_RD",
    "apache common csv": "TECHNOLOGIE_RD",
    "google gson": "TECHNOLOGIE_RD",
    "program-aided language models": "METHODE_RD",
    "program-of-thought": "METHODE_RD",
    "program-of-thought prompting": "METHODE_RD",
    "self-consistency": "METHODE_RD",
    "traditional testing + llms": "METHODE_RD",
    "search-based": "METHODE_RD",
    "constraint-based": "METHODE_RD",
    "random-based": "METHODE_RD",
    "fine-tuning": "METHODE_RD",
    "pré-entraînement": "METHODE_RD",
    "différentes méthodes de pré-entraînement": "METHODE_RD",
    "protocole expérimental": "METHODE_RD",
    "nouvelles méthodes de prompting": "METHODE_RD",
    "structuration aaa": "METHODE_RD",
    "cohérence interne": "METHODE_RD",
    "enrichissement contextuel ciblé": "METHODE_RD",
    "méthodes de génération de tests automatisés": "METHODE_RD",
    "compilation automatique via maven": "METHODE_RD",
    "re-prompting": "METHODE_RD",

    # VERROUS
    "verrous scientifiques ou techniques": "VERROU_TECH",
    "verrous technologiques": "VERROU_TECH",
    "verrous scientifiques et techniques": "VERROU_TECH",
    "limitations des performances des llms en contexte réel": "VERROU_TECH",
    "difficultés liées à l’évaluation de la qualité des tests générés": "VERROU_TECH",
    "difficultés liées à l'évaluation de la qualité des tests générés": "VERROU_TECH",
    "contraintes opérationnelles et souveraineté des données": "VERROU_TECH",
    "gestion des exceptions": "VERROU_TECH",
    "oracle de test": "VERROU_TECH",
    "problème de test oracle": "VERROU_TECH",
    "tests non compilables": "VERROU_TECH",
    "faible spécialisation des modèles": "VERROU_TECH",
    "souveraineté des données": "VERROU_TECH",
    "confidentialité des données": "VERROU_TECH",

    # RESULTATS / METRIQUES
    "résultats expérimentaux": "RESULTAT_RD",
    "résultats obtenus avec zero-shot learning": "RESULTAT_RD",
    "taux de compilabilité": "RESULTAT_RD",
    "taux de compilation": "RESULTAT_RD",
    "compilabilité": "RESULTAT_RD",
    "couverture de code": "RESULTAT_RD",
    "line coverage": "RESULTAT_RD",
    "branch coverage": "RESULTAT_RD",
    "code coverage": "RESULTAT_RD",
    "coverage": "RESULTAT_RD",
    "test coverage": "RESULTAT_RD",
    "assert ratio": "RESULTAT_RD",
    "coverage estimation": "RESULTAT_RD",
    "input variety": "RESULTAT_RD",
    "exception handling score": "RESULTAT_RD",
    "test smells": "RESULTAT_RD",
    "syntactic correctness": "RESULTAT_RD",
}

SECTION_TITLES = {
    "intitulé de l’opération": "OBJECTIF_RD",
    "objectifs visés": "OBJECTIF_RD",
    "performances à atteindre": "OBJECTIF_RD",
    "analyse de l’état de l’art": "METHODE_RD",
    "verrous et incertitudes scientifiques, techniques, technologiques": "VERROU_TECH",
    "limitations des performances des llms en contexte réel": "VERROU_TECH",
    "difficultés liées à l’évaluation de la qualité des tests générés": "VERROU_TECH",
    "contraintes opérationnelles et souveraineté des données": "VERROU_TECH",
    "raisonnement scientifique et démarche expérimentale appliquée": "METHODE_RD",
    "evaluation des performances en “zero-shot learning” et “few-shot learning”": "METHODE_RD",
    "mise en oeuvre d’une approche basée sur le “structured chain of thought (scot)” adapté à la génération de tests unitaires : scot4ut": "METHODE_RD",
    "mise en œuvre de usc4ut": "METHODE_RD",
    "mise en œuvre d’une approche basée sur le « retrieval-augmented generation (rag) » : rag4ut": "METHODE_RD",
    "résultats et analyse des données": "RESULTAT_RD",
    "conclusion et contribution scientifique, technique ou technologique": "RESULTAT_RD",
    "génération automatique de cas de tests unitaires : athenatest et a3test": "METHODE_RD",
    "méthodes de raisonnement : du chain-of-thought (cot) au structured chain-of-thought (scot)": "METHODE_RD",
    "protocole expérimental": "METHODE_RD",
    "critères d’évaluation": "METHODE_RD",
}

DATE_KEEP_PATTERNS = [
    r"^\d{4}$",
    r"^année\s+\d{4}$",
    r"^annÉe\s+\d{4}$",
    r"^année\s+\d{4}$",
    r"^\d{1,2}/\d{1,2}/\d{2,4}$",
    r"^(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\s+\d{4}$",
]

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

def lower_norm(text: str) -> str:
    return norm(text).lower()

def is_valid_date(text: str) -> bool:
    t = lower_norm(text)
    return any(re.match(p, t, flags=re.IGNORECASE) for p in DATE_KEEP_PATTERNS)

def clean_entity(ent):
    text = norm(ent.get("text", ""))
    label = ent.get("label", "")
    low = lower_norm(text)

    if not text:
        return None

    # Section titles
    if low in SECTION_TITLES:
        if KEEP_SECTION_TITLES:
            ent["text"] = text
            ent["label"] = SECTION_TITLES[low]
            ent["status"] = "force_cleaned"
            return ent
        return None

    # Direct remove
    if low in REMOVE_EXACT:
        return None

    # URLs / emails
    if "@" in text or low.startswith("http") or low.startswith("www.") or ".com/" in low or ".org/" in low:
        return None
    if low.startswith("https://") or low.startswith("http://"):
        return None

    # Dates
    if label == "DATE_PERIODE":
        if not is_valid_date(text):
            return None
        ent["text"] = text
        return ent

    # Remove bibliography/person noise
    if label == "PERSONNE":
        if low.endswith("et al") or low.endswith("et al.") or " et al" in low:
            return None
        if low in {
            "auteurs", "les auteurs", "développeur", "développeurs",
            "développeur humain", "utilisateurs", "personnes",
            "personnes sans expertise technique"
        }:
            return None

    # LLM is never ORGANISME/PERSONNE here
    if low in {"llm", "llms", "llm’s", "llm's"}:
        ent["text"] = text
        ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"
        return ent

    # Correction directe
    if low in AUTO_FIX:
        ent["text"] = text
        ent["label"] = AUTO_FIX[low]
        ent["status"] = "force_cleaned"
        return ent

    # Patterns technologies / methods
    if re.search(r"\b(llm|llms|gpt|codex|copilot|starcoder|codellama|codegemma|qwen|mistral)\b", low):
        ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"

    if re.search(r"\b(junit|mockito|maven|jacoco|java|spring|angular|evosuite|defects4j|sf110|humaneval)\b", low):
        ent["label"] = "TECHNOLOGIE_RD"
        ent["status"] = "force_cleaned"

    if re.search(r"\b(scot4ut|usc4ut|rag4ut|scot aaa|scot|cot|usc|rag|aaa|self-consistency|chain-of-thought|retrieval-augmented)\b", low):
        ent["label"] = "METHODE_RD"
        ent["status"] = "force_cleaned"

    if "prompt" in low or "few-shot" in low or "zero-shot" in low or "fine-tuning" in low:
        ent["label"] = "METHODE_RD"
        ent["status"] = "force_cleaned"

    if "verrou" in low or "difficulté" in low or "limitations" in low or "contraintes" in low:
        ent["label"] = "VERROU_TECH"
        ent["status"] = "force_cleaned"

    if "coverage" in low or "compilabilité" in low or "taux de compilation" in low:
        ent["label"] = "RESULTAT_RD"
        ent["status"] = "force_cleaned"

    # Wrong label cleanup
    if label == "JALON":
        if low in {"scot", "cot", "scot15", "cot14"}:
            ent["label"] = "METHODE_RD"
            ent["status"] = "force_cleaned"
        else:
            return None

    if label == "ORGANISME" and low in {"llm", "llms", "llm’s", "industriels", "entreprises"}:
        if low.startswith("llm"):
            ent["label"] = "TECHNOLOGIE_RD"
            ent["status"] = "force_cleaned"
        else:
            return None

    if label == "RESULTAT_RD" and low in {"sample pruning", "reranking", "résultats d’exécution", "résultats d'exécution"}:
        return None

    if len(text) <= 2 and low not in {"ia", "qa"}:
        return None

    ent["text"] = text
    return ent

def deduplicate(entities):
    seen = set()
    out = []
    for ent in entities:
        key = (
            lower_norm(ent.get("text", "")),
            ent.get("label"),
            ent.get("start"),
            ent.get("end"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(ent)
    return out

with open(INPUT, "r", encoding="utf-8") as f:
    data = json.load(f)

before = 0
after = 0
removed = 0
fixed = 0

for item in data:
    new_entities = []

    for ent in item.get("entities", []):
        before += 1
        old_label = ent.get("label")
        old_text = ent.get("text")

        cleaned = clean_entity(dict(ent))

        if cleaned is None:
            removed += 1
            continue

        if (
            cleaned.get("label") != old_label
            or cleaned.get("text") != old_text
            or cleaned.get("status") == "force_cleaned"
        ):
            fixed += 1

        new_entities.append(cleaned)

    item["entities"] = deduplicate(new_entities)
    item["annotation_status"] = "force_cleaned"
    after += len(item["entities"])

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("✅ Force clean projet 11 terminé")
print(f"Avant   : {before}")
print(f"Après   : {after}")
print(f"Removed : {removed}")
print(f"Fixed   : {fixed}")
print(f"Output  : {OUTPUT}")