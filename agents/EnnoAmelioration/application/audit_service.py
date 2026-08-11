from __future__ import annotations

import re

from ..domain.models import AuditFinding, RoutingDecision, SectionFunction


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]


def audit_text(text: str, routing: RoutingDecision) -> list[AuditFinding]:
    source = str(text or "").strip()
    if not source:
        return [
            AuditFinding(
                code="empty_target",
                label="Cible vide",
                severity="blocking",
                explanation="Aucun texte exploitable n'a été identifié.",
                recommendation="Sélectionner, coller ou importer le texte à améliorer.",
            )
        ]

    findings: list[AuditFinding] = []
    sentences = _sentences(source)
    words = re.findall(r"\b[\wÀ-ÿ'-]+\b", source)
    long_sentences = [sentence for sentence in sentences if len(sentence.split()) > 36]
    if long_sentences:
        findings.append(
            AuditFinding(
                code="long_sentences",
                label="Lisibilité",
                severity="medium",
                explanation=f"{len(long_sentences)} phrase(s) dépassent 36 mots.",
                recommendation="Scinder les raisonnements longs sans perdre les liens de causalité.",
                metrics={"long_sentence_count": len(long_sentences)},
            )
        )

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", source) if part.strip()]
    repeated_openings: dict[str, int] = {}
    for paragraph in paragraphs:
        opening = " ".join(paragraph.casefold().split()[:4])
        repeated_openings[opening] = repeated_openings.get(opening, 0) + 1
    duplicate_count = sum(count - 1 for count in repeated_openings.values() if count > 1)
    if duplicate_count:
        findings.append(
            AuditFinding(
                code="repetition",
                label="Redondance",
                severity="low",
                explanation="Des paragraphes commencent par des formulations identiques.",
                recommendation="Fusionner les idées répétées et varier les transitions.",
                metrics={"duplicate_openings": duplicate_count},
            )
        )

    numeric_claims = re.findall(r"(?<!\w)\d+(?:[.,]\d+)?\s*(?:%|ms|s|kg|mm|cm|m|°c|k|hz|db)?", source, flags=re.I)
    citations = re.findall(r"\[[A-Za-z]?\d+(?:\s*[-,;]\s*[A-Za-z]?\d+)*\]|\([A-ZÀ-Ý][^)]*\b(?:19|20)\d{2}\)", source)
    if numeric_claims and not citations and routing.needs_scholar:
        findings.append(
            AuditFinding(
                code="untraced_metrics",
                label="Traçabilité des résultats",
                severity="high",
                explanation="Le passage contient des résultats chiffrés sans référence identifiable.",
                recommendation="Relier chaque résultat déterminant à une publication validée.",
                metrics={"numeric_claims": len(numeric_claims), "citations": len(citations)},
            )
        )

    connectors = re.findall(r"\b(?:cependant|toutefois|ainsi|donc|en revanche|par conséquent|néanmoins|dès lors)\b", source, flags=re.I)
    if len(paragraphs) >= 3 and len(connectors) == 0:
        findings.append(
            AuditFinding(
                code="weak_narrative_links",
                label="Enchaînement argumentatif",
                severity="medium",
                explanation="Les paragraphes sont juxtaposés sans lien logique explicite.",
                recommendation="Expliciter les oppositions, conséquences et conditions de validité.",
            )
        )

    if routing.needs_diagnostic:
        function = routing.section_function
        if function == SectionFunction.UNCERTAINTY and not re.search(
            r"\b(incertitude|inconnu|non maîtris|limite|verrou|difficulté|problème|obstacle)\b",
            source,
            re.I,
        ):
            findings.append(
                AuditFinding(
                    code="uncertainty_not_explicit",
                    label="Incertitude insuffisamment explicitée",
                    severity="high",
                    explanation="La section a une fonction d'incertitude, mais le point technique réellement non maîtrisé n'est pas formulé clairement.",
                    recommendation="Préciser uniquement l'incertitude réellement documentée, sans transformer une difficulté ordinaire en verrou R&D.",
                )
            )
        elif function == SectionFunction.METHOD and not re.search(
            r"\b(afin de|pour (?:évaluer|tester|mesurer|vérifier|déterminer|analyser)|car|parce que|objectif|hypothèse)\b",
            source,
            re.I,
        ):
            findings.append(
                AuditFinding(
                    code="method_rationale_weak",
                    label="Justification de la démarche",
                    severity="medium",
                    explanation="La démarche est décrite, mais le rôle des étapes ou choix méthodologiques est peu explicite.",
                    recommendation="Relier chaque étape à son objectif technique seulement lorsque cette justification est présente dans le dossier.",
                )
            )
        elif function == SectionFunction.RESULT and not re.search(
            r"\b(indique|montre|met en évidence|suggère|confirme|limite|écart|compar|interpr|observ)\b",
            source,
            re.I,
        ):
            findings.append(
                AuditFinding(
                    code="result_interpretation_weak",
                    label="Interprétation des résultats",
                    severity="medium",
                    explanation="Les résultats sont présents mais leur signification technique est peu explicitée.",
                    recommendation="Distinguer observation et interprétation, sans ajouter de causalité ou de généralisation non démontrée.",
                )
            )
        elif function == SectionFunction.CONTRIBUTION and not re.search(
            r"\b(apport|a permis|permet de|contribution|connaissance|établi|démontré|mis en évidence|produit)\b",
            source,
            re.I,
        ):
            findings.append(
                AuditFinding(
                    code="contribution_not_anchored",
                    label="Apport insuffisamment relié aux travaux",
                    severity="medium",
                    explanation="La section ne relie pas clairement l'apport annoncé aux travaux ou résultats établis.",
                    recommendation="Formuler l'apport à partir des résultats documentés et limiter sa portée au périmètre démontré.",
                )
            )

    if not findings:
        findings.append(
            AuditFinding(
                code="strengthen_precision",
                label="Précision rédactionnelle",
                severity="low",
                explanation="Le passage est exploitable ; l'amélioration peut se concentrer sur la précision et la continuité.",
                recommendation="Conserver les faits, clarifier les liens logiques et supprimer les formulations vagues.",
                metrics={"word_count": len(words), "sentence_count": len(sentences)},
            )
        )
    return findings
