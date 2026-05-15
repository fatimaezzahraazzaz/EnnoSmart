"""
modules/NLP/ner.py
──────────────────────────────────────────────────────────────────────────────
NER universel R&D/CIR pour EnnoSmart.

Rôle :
  - Extraire largement des candidats R&D.
  - Ne pas décider si une entité est centrale ou secondaire.
  - Ne pas importer modules.NLP.ner depuis lui-même.
  - Découper temporairement les chunks longs uniquement pour GLiNER afin
    d'éviter la troncature interne à 384 tokens.

Pipeline :
  cleaner → normalizer → ner.py → llm_extractor_smart.py → router.py

Version : 1.1.0
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_GLINER_MODEL = "urchade/gliner_multi-v2.1"

# Labels universels, non liés à un domaine précis.
GLINER_LABELS = [
    "objet de recherche",
    "verrou scientifique ou technique",
    "objectif de recherche",
    "hypothèse de recherche",
    "résultat expérimental ou mesuré",
    "méthode scientifique ou technique",
    "protocole expérimental",
    "outil logiciel ou technologique",
    "framework logiciel",
    "outil de simulation",
    "modèle de langage ou IA",
    "modèle ou algorithme",
    "architecture système",
    "jeu de données ou benchmark",
    "corpus expérimental",
    "métrique d'évaluation",
    "résultat quantifié",
    "paramètre ou variable",
    "paramètre électrique",
    "matériau ou composant",
    "composant électronique",
    "norme technique",
    "livrable technique",
    "limitation ou perspective",
    "organisation",
    "personne",
    "lieu",
    "date ou période",
    "brevet",
    "partenaire",
]

LABEL_MAPPING = {
    "objet de recherche": "OBJET_RECHERCHE",
    "verrou scientifique ou technique": "VERROU_TECH",
    "objectif de recherche": "OBJECTIF_RD",
    "hypothèse de recherche": "HYPOTHESE_RD",
    "hypothese de recherche": "HYPOTHESE_RD",
    "résultat expérimental ou mesuré": "RESULTAT_RD",
    "resultat experimental ou mesure": "RESULTAT_RD",
    "méthode scientifique ou technique": "METHODE_RD",
    "methode scientifique ou technique": "METHODE_RD",
    "protocole expérimental": "PROTOCOLE_EXPERIMENTAL",
    "protocole experimental": "PROTOCOLE_EXPERIMENTAL",
    "outil logiciel ou technologique": "OUTIL_TECHNOLOGIE",
    "framework logiciel": "OUTIL_TECHNOLOGIE",
    "outil de simulation": "OUTIL_TECHNOLOGIE",
    "modèle de langage ou IA": "MODELE_ALGORITHME",
    "modele de langage ou ia": "MODELE_ALGORITHME",
    "modèle ou algorithme": "MODELE_ALGORITHME",
    "modele ou algorithme": "MODELE_ALGORITHME",
    "architecture système": "ARCHITECTURE_SYSTEME",
    "architecture systeme": "ARCHITECTURE_SYSTEME",
    "jeu de données ou benchmark": "BENCHMARK_DATASET",
    "jeu de donnees ou benchmark": "BENCHMARK_DATASET",
    "corpus expérimental": "BENCHMARK_DATASET",
    "corpus experimental": "BENCHMARK_DATASET",
    "métrique d'évaluation": "METRIQUE_EVALUATION",
    "résultat quantifié": "METRIQUE_EVALUATION",
    "resultat quantifie": "METRIQUE_EVALUATION",
    "metrique d'evaluation": "METRIQUE_EVALUATION",
    "paramètre ou variable": "PARAMETRE_VARIABLE",
    "parametre ou variable": "PARAMETRE_VARIABLE",
    "paramètre électrique": "PARAMETRE_VARIABLE",
    "parametre electrique": "PARAMETRE_VARIABLE",
    "matériau ou composant": "MATERIAU_COMPOSANT",
    "materiau ou composant": "MATERIAU_COMPOSANT",
    "composant électronique": "MATERIAU_COMPOSANT",
    "composant electronique": "MATERIAU_COMPOSANT",
    "norme technique": "NORME_TECHNIQUE",
    "livrable technique": "LIVRABLE",
    "limitation ou perspective": "LIMITATION_PERSPECTIVE",
    "organisation": "ORGANISME",
    "personne": "PERSONNE",
    "lieu": "LIEU",
    "date ou période": "DATE_PERIODE",
    "date ou periode": "DATE_PERIODE",
    "brevet": "BREVET",
    "partenaire": "PARTENAIRE_RD",
}

MIN_CONFIDENCE_BY_TYPE = {
    "OBJET_RECHERCHE": 0.30,
    "VERROU_TECH": 0.30,
    "OBJECTIF_RD": 0.30,
    "HYPOTHESE_RD": 0.30,
    "RESULTAT_RD": 0.30,
    "METHODE_RD": 0.28,
    "PROTOCOLE_EXPERIMENTAL": 0.28,
    "OUTIL_TECHNOLOGIE": 0.28,
    "MODELE_ALGORITHME": 0.28,
    "ARCHITECTURE_SYSTEME": 0.28,
    "BENCHMARK_DATASET": 0.28,
    "METRIQUE_EVALUATION": 0.28,
    "PARAMETRE_VARIABLE": 0.28,
    "MATERIAU_COMPOSANT": 0.28,
    "NORME_TECHNIQUE": 0.30,
    "LIMITATION_PERSPECTIVE": 0.30,
    "PERSONNE": 0.45,
    "ORGANISME": 0.40,
    "LIEU": 0.40,
    "DATE_PERIODE": 0.35,
    "BREVET": 0.30,
    "PARTENAIRE_RD": 0.30,
}

GENERIC_FALSE_POSITIVES = {
    "projet", "méthode", "methode", "modèle", "modele", "analyse",
    "simulation", "technologie", "technologies", "matériau", "materiau",
    "résultat", "resultat", "objectif", "objectifs", "données", "donnees",
    "approche", "système", "systeme", "test", "tests", "code", "logiciel",
    "composant", "composants", "paramètre", "parametre",
}

STRUCTURAL_NOISE = {
    "SLIDE", "SLIDES", "NOTE", "NOTES", "IMAGE", "IMAGES", "PAGE", "SECTION",
    "TABLEAU", "TABLEAUX", "FORMULE", "FORMULES", "LATEX", "DOMAINE",
    "CONFIANCE", "EXPLICATION", "QUALITÉ", "QUALITE", "SOMMAIRE", "CONCLUSION",
    "OBJECTIF", "OBJECTIFS", "RESULTAT", "RÉSULTAT", "RESULTATS", "RÉSULTATS",
    "TAUX", "UNITÉ DE MESURE", "UNITE DE MESURE",
}


@dataclass
class Entity:
    text: str
    type: str
    start: int
    end: int
    confidence: float
    source: str = "unknown"
    chunk_index: Optional[int] = None
    chunk_source: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "type": self.type,
            "start": self.start,
            "end": self.end,
            "confidence": round(float(self.confidence), 3),
            "source": self.source,
            "chunk_index": self.chunk_index,
            "chunk_source": self.chunk_source,
        }


@dataclass
class ChunkNERResult:
    chunk_index: int
    entities: list[Entity] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "chunk_index": self.chunk_index,
            "entities": [e.to_dict() for e in self.entities],
        }


@dataclass
class BatchNERResult:
    results: list[ChunkNERResult] = field(default_factory=list)
    backend_stats: dict[str, int] = field(default_factory=dict)
    total_entities: int = 0

    def to_dict(self) -> dict:
        return {
            "results": [r.to_dict() for r in self.results],
            "backend_stats": self.backend_stats,
            "total_entities": self.total_entities,
        }


_GLINER_MODEL = None


def load_gliner_model(model_name: str = DEFAULT_GLINER_MODEL):
    global _GLINER_MODEL
    if _GLINER_MODEL is not None:
        return _GLINER_MODEL
    try:
        from gliner import GLiNER
        logger.info("Chargement GLiNER : %s", model_name)
        _GLINER_MODEL = GLiNER.from_pretrained(model_name)
        return _GLINER_MODEL
    except Exception as exc:
        logger.warning("GLiNER indisponible : %s", exc)
        return None


def _clean_entity_text(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text.strip(" ,.;:()[]{}'\"")


def _normalize_type(label: str) -> str:
    label_clean = re.sub(r"\s+", " ", str(label or "").strip().replace("’", "'"))
    label_lower = label_clean.lower()
    return LABEL_MAPPING.get(label_clean) or LABEL_MAPPING.get(label_lower) or label_clean.upper().replace(" ", "_").replace("'", "_")


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text.strip()) if w])


def _is_visual_chunk(text: str) -> bool:
    upper = str(text or "").strip()[:200].upper()
    return upper.startswith("[IMAGE") or "[QUALITÉ:" in upper or "[QUALITE:" in upper


def _is_structural_noise(text: str, entity_type: str) -> bool:
    cleaned = _clean_entity_text(text)
    if not cleaned:
        return True
    upper = cleaned.upper()
    lower = cleaned.lower()
    if upper in STRUCTURAL_NOISE:
        return True
    if upper.startswith(("SLIDE ", "PAGE ", "IMAGE ", "FORMULE ", "LATEX:", "DOMAINE:", "CONFIANCE:")):
        return True
    if lower.startswith(("avec des ", "avec une ", "dans des ", "cadre de ", "partir des ")):
        return True
    return False


def _looks_like_valid_entity(text: str, entity_type: str) -> bool:
    text_clean = _clean_entity_text(text)
    if not text_clean or len(text_clean) < 2:
        return False

    words = _word_count(text_clean)
    lower = text_clean.lower()

    if words == 1 and lower in GENERIC_FALSE_POSITIVES:
        return False

    max_words = {
        "VERROU_TECH": 22,
        "OBJECTIF_RD": 22,
        "RESULTAT_RD": 18,
        "LIMITATION_PERSPECTIVE": 22,
        "OBJET_RECHERCHE": 16,
        "METHODE_RD": 14,
    }.get(entity_type, 12)

    if words > max_words:
        return False

    bad_starts = {"avec", "dans", "pour", "afin", "lors", "comme", "ce", "cette", "ces"}
    first = lower.split()[0] if lower.split() else ""
    if first in bad_starts:
        return False

    return True


def _passes_confidence(entity_type: str, confidence: float, chunk_source: str) -> bool:
    min_conf = MIN_CONFIDENCE_BY_TYPE.get(entity_type, 0.35)
    if chunk_source == "visual":
        min_conf += 0.12
    return confidence >= min_conf


def _entity_key(text: str, entity_type: str) -> tuple[str, str]:
    # Normalisation légère pour dédupliquer les variantes OCR/espaces.
    value = re.sub(r"\s+", " ", str(text or "").strip().lower())
    value = value.replace("’", "'")
    value = re.sub(r"\s*[-–/]\s*", "-", value)
    value = re.sub(r"[^a-z0-9àâäéèêëîïôöùûüçœµ%°._+' -]", "", value)
    return (value, entity_type)


def _deduplicate_entities(entities: list[Entity]) -> list[Entity]:
    grouped: dict[tuple[str, str], Entity] = {}
    for entity in entities:
        key = _entity_key(entity.text, entity.type)
        if key not in grouped or entity.confidence > grouped[key].confidence:
            grouped[key] = entity
    return sorted(
        grouped.values(),
        key=lambda e: (
            e.chunk_index or 0,
            e.start if e.start is not None else 10**9,
            -e.confidence,
        ),
    )


def _normalize_long_lines_for_gliner(text: str) -> str:
    """
    Ajoute des séparateurs légers pour éviter que GLiNER reçoive une
    phrase/listing de plus de 384 tokens.

    Important : cette normalisation ne modifie pas le chunk RAG final.
    Elle sert seulement au passage temporaire dans GLiNER.
    """
    t = str(text or "")

    # Listes collées issues de PDF/DOCX : mot)Mot, ; Mot, puces, etc.
    t = re.sub(r"\s*[•·▪◦]\s*", "\n- ", t)
    t = re.sub(r";\s+(?=[A-ZÉÈÀÂÎÔÛÇa-zà-ÿ0-9])", ";\n", t)

    # Cas fréquent : "... (RAG)Universal ..." ou "SCoT)Auto..."
    t = re.sub(r"(?<=[a-zà-ÿ0-9\)])(?=[A-ZÉÈÀÂÎÔÛÇ][a-zà-ÿ])", ". ", t)

    # Couper les lignes extrêmement longues sur des séparateurs sûrs.
    out: list[str] = []
    for line in t.splitlines():
        if len(line) <= 900:
            out.append(line)
            continue

        current = line.strip()
        while len(current) > 900:
            cut = max(
                current.rfind(". ", 0, 900),
                current.rfind("; ", 0, 900),
                current.rfind(", ", 0, 900),
                current.rfind(" ", 0, 900),
            )
            if cut < 350:
                cut = 900
            out.append(current[:cut].strip())
            current = current[cut:].strip()
        if current:
            out.append(current)

    return "\n".join(out)


def _split_for_gliner(
    text: str,
    max_chars: int = 900,
    overlap: int = 120,
) -> list[tuple[str, int]]:
    """
    Découpe temporairement un chunk long uniquement pour GLiNER.

    Version renforcée :
    - réduit max_chars pour rester sous la limite interne GLiNER ;
    - coupe mieux les listes/paragraphes collés ;
    - garde des offsets approximatifs suffisants pour la traçabilité.

    Le chunk original n'est jamais modifié pour le RAG.
    Retourne : [(segment_text, offset_start_in_original_text), ...]
    """
    original = str(text or "")
    text = _normalize_long_lines_for_gliner(original)

    if len(text) <= max_chars:
        return [(text, 0)]

    segments: list[tuple[str, int]] = []
    start = 0
    n = len(text)

    while start < n:
        end = min(start + max_chars, n)

        cut_candidates = [
            text.rfind("\n\n", start, end),
            text.rfind("\n", start, end),
            text.rfind(". ", start, end),
            text.rfind("; ", start, end),
            text.rfind(", ", start, end),
            text.rfind(" ", start, end),
        ]

        cut = max(cut_candidates)

        if cut <= start + 250:
            cut = end

        raw_segment = text[start:cut]
        segment = raw_segment.strip()

        if segment:
            left_trim = len(raw_segment) - len(raw_segment.lstrip())
            segments.append((segment, start + left_trim))

        if cut >= n:
            break

        next_start = max(cut - overlap, start + 1)
        if next_start <= start:
            next_start = min(start + max_chars - overlap, n)

        start = next_start

    return segments


DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|20\d{2}|19\d{2}|ANNÉE\s+20\d{2}|ANNEE\s+20\d{2}|T[1-4]\s*20\d{2}|S[1-2]\s*20\d{2}|janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|septembre|octobre|novembre|décembre|decembre)\b",
    re.IGNORECASE,
)

MONTANT_RE = re.compile(
    r"\b(?:\d+(?:[.,]\d+)?\s*(?:k€|K€|M€|€|euros?|EUR|MAD|DH|dirhams?)|\d{1,3}(?:\s\d{3})+(?:[.,]\d+)?\s*(?:€|euros?|EUR|MAD|DH|dirhams?))\b",
    re.IGNORECASE,
)

ETP_RE = re.compile(
    r"\b(?:\d+(?:[.,]\d+)?\s*ETP|ETP\s*[:=]?\s*\d+(?:[.,]\d+)?|équivalent\s+temps\s+plein|equivalent\s+temps\s+plein)\b",
    re.IGNORECASE,
)

JALON_RE = re.compile(
    r"\b(?:jalon\s*\d+|milestone\s*\d+|phase\s*\d+|lot\s*\d+|tâche\s*\d+|tache\s*\d+|WP\s*\d+)\b",
    re.IGNORECASE,
)

BREVET_RE = re.compile(
    r"\b(?:brevet|dépôt\s+de\s+brevet|depot\s+de\s+brevet|propriété\s+intellectuelle|EP\d{7}|WO\d{4}/\d{6}|FR\d{7}|US\d{7}[A-Z]?)\b",
    re.IGNORECASE,
)

# Paramètres techniques universels : valeurs électriques, mécaniques, temporelles,
# ratios, capacités, résistances, fréquences, rendements, MTBF, etc.
TECH_PARAM_RE = re.compile(
    r"\b(?:"
    r"(?:Ron|Roff|R_on|R_off)\s*[=:]?\s*\d+(?:[.,]\d+)?\s*(?:Ω|Ω|ohm|mΩ|kΩ|MΩ)"
    r"|MTBF\s*[><=]?\s*\d+(?:\s*\d{3})*\s*h?"
    r"|\d+(?:\s*\d{3})*(?:[.,]\d+)?\s*(?:kV|V|mV|A|mA|µA|uA|Ω|Ω|ohm|mΩ|kΩ|MΩ|µF|uF|mF|nF|pF|Hz|kHz|MHz|GHz|s|ms|µs|us|ns|%|W|kW|MW|Wh|kWh|N|kN|Pa|kPa|MPa|GPa|°C)"
    r"|\d+(?:[.,]\d+)?\s*/\s*\d+(?:[.,]\d+)?\s*(?:Hz|kHz|MHz|GHz)"
    r")\b",
    re.IGNORECASE,
)

# Normes/standards techniques universels.
# Important :
# - EN 61140 = norme
# - en 2021 = pas une norme
STANDARD_RE = re.compile(
    r"\b(?:"
    r"IEC\s*\d{3,}(?:[-–]\d+)*"
    r"|NF\s+EN\s+\d{3,}(?:[-–]\d+)*"
    r"|EN\s+(?!20\d{2}\b)\d{4,}(?:[-–]\d+)*"
    r"|ISO\s*\d{3,}(?:[-–]\d+)*"
    r"|IEEE\s*\d{3,}(?:[-–]\d+)*"
    r"|UL\s*\d{3,}(?:[-–]\d+)*"
    r"|RoHS|REACH"
    r")\b",
    re.IGNORECASE,
)



# Lexiques regex volontairement conservateurs : ils améliorent le rappel sur
# les termes courts que GLiNER rate souvent ou classe mal.
METHOD_TERM_RE = re.compile(
    r"\b(?:"
    r"zero[\s-]?shot(?:\s+learning)?|few[\s-]?shot(?:\s+learning)?|"
    r"prompt\s+engineering|Chain[\s-]?of[\s-]?Thought\s*/?\s*CoT|CoT|"
    r"Structured\s+Chain[\s-]?of[\s-]?Thought\s*/?\s*SCoT|SCoT|SCoT4UT|"
    r"Universal\s+Self[\s-]?Consistency\s*/?\s*USC|USC|USC4UT|"
    r"Retrieval[\s-]?Augmented\s+Generation\s*/?\s*RAG|RAG|RAG4UT|"
    r"Self[\s-]?Consistency|re[\s-]?prompting|correction\s+automatique|"
    r"simulation\s+num[ée]rique|mod[ée]lisation|dimensionnement|maillage|"
    r"analyse\s+statique|analyse\s+syntaxique|parsing\s+AST|AST"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

PROTOCOL_TERM_RE = re.compile(
    r"\b(?:Arrange\s*[-–]\s*Act\s*[-–]\s*Assert\s*/?\s*AAA|Arrange\s+Act\s+Assert|AAA|SCoT\s+AAA)\b",
    re.IGNORECASE | re.UNICODE,
)

TOOL_TERM_RE = re.compile(
    r"\b(?:"
    r"JUnit|Mockito|Maven|Gradle|JaCoCo|DeepEval|EvoSuite|Ollama|Hugging\s*Face|"
    r"Spring|PyTest|Selenium|Docker|Kubernetes|GitHub\s+Copilot|Copilot|"
    r"MATLAB|Simulink|Ansys|Abaqus|SolidWorks|LTSpice|SPICE|PSCAD|Catia|Comsol"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

MODEL_TERM_RE = re.compile(
    r"\b(?:"
    r"GPT\s*[- ]?3\s*[,.]\s*5\s*[- ]?Turbo|GPT\s*[- ]?4|Codex|"
    r"CodeGemma(?:\s*[- ]?\s*instruct)?|CodeLlama(?:\s*[- ]?\s*instruct)?|"
    r"Code\s*[- ]\s*Mistral|Qwen\s*2\s*[,.]\s*5\s*[- ]?Coder(?:\s*[- ]?\s*instruct)?|"
    r"Qwen2\.5\s*[- ]?Coder|StarCoder2?|starcoder2?|LLMs?|mod[èe]les?\s+de\s+langage"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

BENCHMARK_TERM_RE = re.compile(
    r"\b(?:EvoSuite\s+SF\s*110|SF\s*110|Methods\s*2\s*Test|Methods2Test|Defects\s*4\s*J|Defects4J|HumanEval|M[ée]thodoTest)\b",
    re.IGNORECASE | re.UNICODE,
)

METRIC_TERM_RE = re.compile(
    r"\b(?:"
    r"taux\s+de\s+compilabilit[ée](?:\s+des\s+fichiers\s+g[ée]n[ée]r[ée]s)?|"
    r"taux\s+de\s+compilation\s+sans\s+erreur|"
    r"%\s*Compilable|Compilation\s+Status|"
    r"couverture\s+de\s+code|couverture\s+de\s+lignes?|couverture\s+de\s+branches?|"
    r"%\s*Line\s+Coverage|%\s*Branch\s+Coverage|"
    r"taux\s+de\s+succ[èe]s\s+[àa]\s+l['’]?ex[ée]cution|"
    r"taux\s+de\s+r[ée]ussite\s+[àa]\s+l['’]?ex[ée]cution|"
    r"assert\s+ratio|coverage\s+estimation|input\s+variety|exception\s+handling\s+score|"
    r"test\s+smells?|odeurs?\s+de\s+test|qualit[ée]\s+des\s+tests\s+g[ée]n[ée]r[ée]s"
    r")\b",
    re.IGNORECASE | re.UNICODE,
)

def extract_regex_entities(text: str, chunk_index: int, chunk_source: str) -> list[Entity]:
    entities: list[Entity] = []
    specs = [
        (METHOD_TERM_RE, "METHODE_RD", 0.93),
        (PROTOCOL_TERM_RE, "PROTOCOLE_EXPERIMENTAL", 0.93),
        (TOOL_TERM_RE, "OUTIL_TECHNOLOGIE", 0.93),
        (MODEL_TERM_RE, "MODELE_ALGORITHME", 0.93),
        (BENCHMARK_TERM_RE, "BENCHMARK_DATASET", 0.94),
        (METRIC_TERM_RE, "METRIQUE_EVALUATION", 0.92),
        (DATE_RE, "DATE_PERIODE", 0.88),
        (MONTANT_RE, "MONTANT_CIR", 0.92),
        (ETP_RE, "ETP", 0.92),
        (JALON_RE, "JALON", 0.90),
        (BREVET_RE, "BREVET", 0.90),
        (STANDARD_RE, "NORME_TECHNIQUE", 0.92),
        (TECH_PARAM_RE, "PARAMETRE_VARIABLE", 0.88),
    ]

    for pattern, entity_type, confidence in specs:
        for match in pattern.finditer(text or ""):
            ent_text = _clean_entity_text(match.group(0))
            if not ent_text or _is_structural_noise(ent_text, entity_type):
                continue
            entities.append(
                Entity(
                    text=ent_text,
                    type=entity_type,
                    start=match.start(),
                    end=match.end(),
                    confidence=confidence,
                    source="regex",
                    chunk_index=chunk_index,
                    chunk_source=chunk_source,
                )
            )

    return entities


def extract_gliner_entities(text: str, chunk_index: int, chunk_source: str, model: Any) -> list[Entity]:
    if model is None:
        return []

    entities: list[Entity] = []

    try:
        # Découpage temporaire uniquement pour GLiNER.
        # Le chunk RAG original reste complet.
        segments = _split_for_gliner(text, max_chars=1400, overlap=180)

        if len(segments) > 1:
            logger.debug(
                "GLiNER split chunk %s : %d caractères → %d segments",
                chunk_index,
                len(str(text or "")),
                len(segments),
            )

        for segment_text, offset in segments:
            predictions = model.predict_entities(segment_text, GLINER_LABELS)

            for pred in predictions:
                raw_text = _clean_entity_text(pred.get("text", ""))
                raw_label = pred.get("label", "")
                entity_type = _normalize_type(raw_label)
                confidence = float(pred.get("score", 0.0) or 0.0)

                if not raw_text:
                    continue

                if chunk_source == "visual":
                    confidence *= 0.75

                if _is_structural_noise(raw_text, entity_type):
                    continue

                if not _looks_like_valid_entity(raw_text, entity_type):
                    continue

                if not _passes_confidence(entity_type, confidence, chunk_source):
                    continue

                start = int(pred.get("start", -1))
                end = int(pred.get("end", -1))

                # Recalage des positions sur le chunk original.
                if start >= 0:
                    start += offset

                if end >= 0:
                    end += offset

                entities.append(
                    Entity(
                        text=raw_text,
                        type=entity_type,
                        start=start,
                        end=end,
                        confidence=confidence,
                        source="gliner",
                        chunk_index=chunk_index,
                        chunk_source=chunk_source,
                    )
                )

    except Exception as exc:
        logger.warning("Erreur GLiNER chunk %s : %s", chunk_index, exc)

    return _deduplicate_entities(entities)


def extract_entities(
    text: str,
    chunk_index: int = 0,
    use_gliner: bool = True,
    use_spacy: bool = False,
    use_regex: bool = True,
    chunk_source: Optional[str] = None,
) -> ChunkNERResult:
    if chunk_source is None:
        chunk_source = "visual" if _is_visual_chunk(text) else "text"

    all_entities: list[Entity] = []

    if use_gliner:
        model = load_gliner_model()
        all_entities.extend(extract_gliner_entities(text, chunk_index, chunk_source, model))

    # spaCy laissé désactivé : GLiNER + regex sont suffisants ici.
    if use_regex:
        all_entities.extend(extract_regex_entities(text, chunk_index, chunk_source))

    return ChunkNERResult(chunk_index=chunk_index, entities=_deduplicate_entities(all_entities))


def extract_entities_batch(
    chunks: list[str],
    use_gliner: bool = True,
    use_spacy: bool = False,
    use_regex: bool = True,
    chunk_sources: Optional[list[str]] = None,
) -> BatchNERResult:
    results: list[ChunkNERResult] = []
    backend_stats: dict[str, int] = {"gliner": 0, "regex": 0}

    for i, chunk in enumerate(chunks or []):
        source = chunk_sources[i] if chunk_sources and i < len(chunk_sources) else None
        result = extract_entities(
            text=chunk or "",
            chunk_index=i,
            use_gliner=use_gliner,
            use_spacy=use_spacy,
            use_regex=use_regex,
            chunk_source=source,
        )
        for entity in result.entities:
            backend_stats[entity.source] = backend_stats.get(entity.source, 0) + 1
        results.append(result)

    return BatchNERResult(
        results=results,
        backend_stats=backend_stats,
        total_entities=sum(len(r.entities) for r in results),
    )
