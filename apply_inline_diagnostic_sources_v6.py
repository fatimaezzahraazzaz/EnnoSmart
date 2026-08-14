from __future__ import annotations

from pathlib import Path
import shutil
import sys

DEFAULT_PATH = Path(r"C:\EnnoSmart\frontend\components\ennosmart\diagnosis-page.tsx")
VERSION_MARKER = "V194_INLINE_CLICKABLE_SOURCES_NO_CARDS"

HELPERS = r'''

// ===============================
// V194 - Citations inline cliquables pour les sections EnnoDiagnostic
// Les preuves restent dans le payload backend ; l'UI n'affiche que [n].
// Le clic réutilise SourceEvidenceCitations / SourceDocumentDialog et donc
// le surlignage déjà présent dans EnnoSmart.
// ===============================
function getBackendSectionPayloadV194(payload: any, display: any, key: string): any {
  const report = unwrapBackendDiagnosticReportV93(payload)
  const candidates = [
    report?.static_diagnostic?.section_payloads_by_key,
    report?.context_engineering?.section_payloads_by_key,
    report?.section_payloads_by_key,
    payload?.static_diagnostic?.section_payloads_by_key,
    payload?.context_engineering?.section_payloads_by_key,
    payload?.report?.static_diagnostic?.section_payloads_by_key,
    payload?.report?.context_engineering?.section_payloads_by_key,
    payload?.bundle?.report?.static_diagnostic?.section_payloads_by_key,
    payload?.bundle?.report?.context_engineering?.section_payloads_by_key,
    display?.static_diagnostic?.section_payloads_by_key,
    display?.context_engineering?.section_payloads_by_key,
    display?.section_payloads_by_key,
  ]

  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== "object") continue
    const section = candidate?.[key]
    if (section && typeof section === "object") return section
  }

  return null
}

function sectionProofKeyV194(proof: any, fallbackIndex = 0) {
  return String(
    proof?.passage_id ||
      proof?.rag_chunk_id ||
      proof?.evidence_id ||
      `${proof?.document || proof?.document_name || "source"}:${proof?.sentence_start ?? proof?.char_start ?? fallbackIndex}:${String(
        proof?.source_text_original || proof?.excerpt || proof?.text || "",
      ).slice(0, 160)}`,
  )
}

function sectionUnitProofsV194(unit: any, section: any): any[] {
  const explicitProofs = Array.isArray(unit?.proofs)
    ? unit.proofs.filter((proof: any) => proof && String(proof?.evidence_id || "") !== "F0")
    : []

  if (explicitProofs.length > 0) return explicitProofs

  const ids = new Set(
    (Array.isArray(unit?.evidence_ids) ? unit.evidence_ids : [])
      .map((value: any) => String(value || "").trim())
      .filter((value: string) => value && value !== "F0"),
  )

  if (ids.size === 0) return []

  return (Array.isArray(section?.evidence) ? section.evidence : []).filter((proof: any) =>
    ids.has(String(proof?.evidence_id || "").trim()),
  )
}

function InlineSourcedSectionV194({
  text,
  structuredSection,
  projectId,
  sourceDocuments,
  preserveDemarcheAudit = false,
}: {
  text: string
  structuredSection: any
  projectId: number | string
  sourceDocuments: DbSourceDocument[]
  preserveDemarcheAudit?: boolean
}) {
  const rawItems = Array.isArray(structuredSection?.items) ? structuredSection.items : []
  const rawParagraphs = Array.isArray(structuredSection?.paragraphs) ? structuredSection.paragraphs : []
  const units = rawItems.length > 0 ? rawItems : rawParagraphs

  if (units.length === 0) {
    return (
      <BackendSectionRendererV93
        text={text}
        projectId={projectId}
        sourceDocuments={sourceDocuments}
      />
    )
  }

  // Numérotation locale, stable, dans l'ordre de première apparition des preuves.
  // La même preuve garde le même numéro partout dans la section.
  const numberByProof = new Map<string, number>()
  let nextNumber = 1

  const rows = units
    .map((unit: any, index: number) => {
      const proofs = sectionUnitProofsV194(unit, structuredSection)
      const evidence: SourceEvidence[] = []
      const citationNumbers: number[] = []
      const seenUnitProofs = new Set<string>()

      proofs.forEach((proof: any, proofIndex: number) => {
        const key = sectionProofKeyV194(proof, proofIndex)
        if (!key || seenUnitProofs.has(key)) return
        seenUnitProofs.add(key)

        if (!numberByProof.has(key)) {
          numberByProof.set(key, nextNumber)
          nextNumber += 1
        }

        evidence.push(eligibilityProofToEvidenceV191(proof))
        citationNumbers.push(Number(numberByProof.get(key)))
      })

      return {
        label: cleanDisplayText(String(unit?.label || "")),
        text: cleanDisplayText(String(unit?.text || "")),
        evidence,
        citationNumbers,
        index,
      }
    })
    .filter((row: any) => row.text)

  if (rows.length === 0) {
    return (
      <BackendSectionRendererV93
        text={text}
        projectId={projectId}
        sourceDocuments={sourceDocuments}
      />
    )
  }

  let auditPrefix = ""
  if (preserveDemarcheAudit) {
    const marker = "Démarches relevées dans les preuves"
    const markerIndex = String(text || "").indexOf(marker)
    if (markerIndex >= 0) {
      auditPrefix = String(text || "").slice(0, markerIndex).trim()
    }
  }

  return (
    <div className="space-y-3">
      {auditPrefix ? (
        <BackendSectionRendererV93
          text={auditPrefix}
          projectId={projectId}
          sourceDocuments={sourceDocuments}
        />
      ) : null}

      {preserveDemarcheAudit ? (
        <p className="pt-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Démarches relevées dans les preuves
        </p>
      ) : null}

      {rows.map((row: any) => (
        <p
          key={`${row.label || "section"}:${row.index}`}
          className="text-sm leading-7 text-muted-foreground"
        >
          {row.label ? (
            <>
              <span className="font-medium text-foreground">{row.label}</span>
              {" — "}
            </>
          ) : null}
          {row.text}
          {row.evidence.length > 0 ? (
            <SourceEvidenceCitations
              projectId={projectId}
              documents={sourceDocuments}
              evidence={row.evidence}
              citationNumbers={row.citationNumbers}
            />
          ) : null}
        </p>
      ))}
    </div>
  )
}
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: attendu 1 occurrence, trouvé {count}")
    return text.replace(old, new, 1)


def patch_backend_section_card(text: str) -> str:
    start = text.find("function BackendSectionCardV93({")
    if start < 0:
        raise RuntimeError("BackendSectionCardV93 introuvable")
    end = text.find("\n\nfunction ", start + 20)
    if end < 0:
        raise RuntimeError("Fin de BackendSectionCardV93 introuvable")

    block = text[start:end]

    block = replace_once(
        block,
        "  enableSourceDocs = false,\n}: {",
        "  enableSourceDocs = false,\n  structuredSection = null,\n  preserveDemarcheAudit = false,\n}: {",
        "props runtime BackendSectionCardV93",
    )
    block = replace_once(
        block,
        "  enableSourceDocs?: boolean\n}) {",
        "  enableSourceDocs?: boolean\n  structuredSection?: any\n  preserveDemarcheAudit?: boolean\n}) {",
        "types BackendSectionCardV93",
    )

    old_render = """          {text?.trim() ? (\n            <BackendSectionRendererV93 text={text} projectId={projectId} sourceDocuments={sourceDocuments} enableSourceDocs={enableSourceDocs} />\n          ) : ("""
    new_render = """          {text?.trim() ? (\n            structuredSection && projectId ? (\n              <InlineSourcedSectionV194\n                text={text}\n                structuredSection={structuredSection}\n                projectId={projectId}\n                sourceDocuments={sourceDocuments}\n                preserveDemarcheAudit={preserveDemarcheAudit}\n              />\n            ) : (\n              <BackendSectionRendererV93\n                text={text}\n                projectId={projectId}\n                sourceDocuments={sourceDocuments}\n                enableSourceDocs={enableSourceDocs}\n              />\n            )\n          ) : ("""
    block = replace_once(block, old_render, new_render, "rendu BackendSectionCardV93")

    return text[:start] + block + text[end:]


def insert_structured_use_memos(text: str) -> str:
    anchor = '''  const parametresText = useMemo(() => {\n    return pickBackendSectionV93(backendSectionsV93, backendMarkdownV93, [\n      "Paramètres et contraintes techniques",\n      "Paramètres techniques",\n    ])\n  }, [backendSectionsV93, backendMarkdownV93])\n'''
    if anchor not in text:
        raise RuntimeError("Bloc parametresText introuvable")

    addition = anchor + '''\n  const demarcheStructuredSectionV194 = useMemo(() => {\n    return getBackendSectionPayloadV194(diagnosticBundle, display, "demarche_detectee")\n  }, [diagnosticBundle, display])\n\n  const resultatsStructuredSectionV194 = useMemo(() => {\n    return getBackendSectionPayloadV194(diagnosticBundle, display, "resultats_metriques")\n  }, [diagnosticBundle, display])\n\n  const parametresStructuredSectionV194 = useMemo(() => {\n    return getBackendSectionPayloadV194(diagnosticBundle, display, "parametres_contraintes")\n  }, [diagnosticBundle, display])\n'''
    return text.replace(anchor, addition, 1)


def patch_section_cards_usage(text: str) -> str:
    old = '''            <BackendSectionCardV93\n              title="Pertinence des démarches"\n              description="Nécessité des étapes, distinction R&D / ingénierie classique et possibilité d’aller directement à la solution finale."\n              icon={Search}\n              text={demarcheText}\n              emptyText="Aucune démarche détectée."\n            />'''
    new = '''            <BackendSectionCardV93\n              title="Pertinence des démarches"\n              description="Nécessité des étapes, distinction R&D / ingénierie classique et possibilité d’aller directement à la solution finale."\n              icon={Search}\n              text={demarcheText}\n              emptyText="Aucune démarche détectée."\n              projectId={project.id}\n              sourceDocuments={sourceDocuments}\n              structuredSection={demarcheStructuredSectionV194}\n              preserveDemarcheAudit\n            />'''
    text = replace_once(text, old, new, "carte démarches")

    old = '''            <BackendSectionCardV93\n              title="Résultats / métriques"\n              description="Résultats chiffrés, observations qualitatives et éléments insuffisants à confirmer."\n              icon={TrendingUp}\n              text={resultatsText}\n              emptyText="Aucun résultat ou métrique disponible."\n              tone="success"\n            />'''
    new = '''            <BackendSectionCardV93\n              title="Résultats / métriques"\n              description="Résultats chiffrés, observations qualitatives et éléments insuffisants à confirmer."\n              icon={TrendingUp}\n              text={resultatsText}\n              emptyText="Aucun résultat ou métrique disponible."\n              tone="success"\n              projectId={project.id}\n              sourceDocuments={sourceDocuments}\n              structuredSection={resultatsStructuredSectionV194}\n            />'''
    text = replace_once(text, old, new, "carte résultats")

    old = '''            <BackendSectionCardV93\n              title="Paramètres et contraintes techniques"\n              description="Paramètres, jeux de données, conditions expérimentales et contraintes techniques."\n              icon={FileText}\n              text={parametresText}\n              emptyText="Aucun paramètre technique disponible."\n            />'''
    new = '''            <BackendSectionCardV93\n              title="Paramètres et contraintes techniques"\n              description="Paramètres, jeux de données, conditions expérimentales et contraintes techniques."\n              icon={FileText}\n              text={parametresText}\n              emptyText="Aucun paramètre technique disponible."\n              projectId={project.id}\n              sourceDocuments={sourceDocuments}\n              structuredSection={parametresStructuredSectionV194}\n            />'''
    text = replace_once(text, old, new, "carte paramètres")
    return text


def remove_conclusion_source_cards(text: str) -> str:
    function_start = text.find("function UnifiedEligibilityStudyCardV191({")
    if function_start < 0:
        raise RuntimeError("UnifiedEligibilityStudyCardV191 introuvable")

    function_end = text.find("\n\nfunction getComparisonCurrentText", function_start)
    if function_end < 0:
        raise RuntimeError("Fin UnifiedEligibilityStudyCardV191 introuvable")

    block = text[function_start:function_end]
    marker = "Sources et passages"
    marker_pos = block.find(marker)
    if marker_pos < 0:
        # Déjà supprimé : ne pas échouer.
        return text

    card_start = block.rfind('        <div className="rounded-xl border bg-white p-5">', 0, marker_pos)
    if card_start < 0:
        raise RuntimeError("Début de la carte Sources et passages introuvable")

    after_marker = '\n        <p className="text-xs leading-6 text-muted-foreground">'
    card_end = block.find(after_marker, marker_pos)
    if card_end < 0:
        raise RuntimeError("Fin de la carte Sources et passages introuvable")

    block = block[:card_start] + block[card_end:]
    return text[:function_start] + block + text[function_end:]


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    if not target.exists():
        raise SystemExit(f"Fichier introuvable : {target}")

    original = target.read_text(encoding="utf-8")
    if VERSION_MARKER in original:
        print(f"[{VERSION_MARKER}] déjà appliqué : {target}")
        return

    text = original

    # L'import existe déjà sur la V5 actuelle. On le rajoute seulement si nécessaire.
    if "SourceEvidenceCitations," not in text:
        text = replace_once(
            text,
            "  SourceTextWithDocuments,\n",
            "  SourceTextWithDocuments,\n  SourceEvidenceCitations,\n",
            "import SourceEvidenceCitations",
        )

    insert_at = text.find("function BackendSectionCardV93({")
    if insert_at < 0:
        raise RuntimeError("Point d'insertion des helpers introuvable")
    text = text[:insert_at] + f"\n// {VERSION_MARKER}\n" + HELPERS + "\n" + text[insert_at:]

    text = patch_backend_section_card(text)
    text = insert_structured_use_memos(text)
    text = patch_section_cards_usage(text)
    text = remove_conclusion_source_cards(text)

    backup = target.with_suffix(target.suffix + ".before_inline_sources_v6")
    if not backup.exists():
        shutil.copy2(target, backup)

    target.write_text(text, encoding="utf-8")

    # Contrôles simples avant de terminer.
    checks = {
        "helpers": VERSION_MARKER in text,
        "démarches structurées": "structuredSection={demarcheStructuredSectionV194}" in text,
        "résultats structurés": "structuredSection={resultatsStructuredSectionV194}" in text,
        "paramètres structurés": "structuredSection={parametresStructuredSectionV194}" in text,
        "citations inline": "<SourceEvidenceCitations" in text,
        "cartes conclusion supprimées": "Sources et passages" not in text[text.find("function UnifiedEligibilityStudyCardV191({"):text.find("\n\nfunction getComparisonCurrentText", text.find("function UnifiedEligibilityStudyCardV191({"))],
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError("Contrôles finaux échoués : " + ", ".join(failed))

    print(f"[{VERSION_MARKER}] OK")
    print(f"Fichier modifié : {target}")
    print(f"Sauvegarde : {backup}")
    print("Aucun changement backend / NLP / PydanticAI.")
    print("Redémarre uniquement le frontend Next.js si nécessaire.")


if __name__ == "__main__":
    main()
