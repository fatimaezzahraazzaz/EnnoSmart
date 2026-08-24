"use client"

import { useMemo } from "react"
import {
  ArrowRight,
  FileSearch,
  History,
  ShieldCheck,
  Sparkles,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  SourceTextWithDocuments,
  useProjectSourceDocuments,
  type SourceEvidence,
} from "@/components/ennosmart/source-documents-dialog"

type CirPreviousContinuityTabProps = {
  diagnostic: any
  projectId: number | string
  apiBaseUrl?: string
  authToken?: string
  organisme?: string
  projectName?: string
  currentYear?: string | number
}

function asArray<T = any>(value: any): T[] {
  return Array.isArray(value) ? value : []
}

function cleanText(value: any): string {
  return String(value || "")
    .replace(/\r\n/g, "\n")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
}

function shortText(value: any, maxChars = 900): string {
  const text = cleanText(value)
  if (text.length <= maxChars) return text

  const cut = text.slice(0, maxChars)
  const last = Math.max(
    cut.lastIndexOf("."),
    cut.lastIndexOf("!"),
    cut.lastIndexOf("?"),
    cut.lastIndexOf(" "),
  )

  return `${cut.slice(0, last > 220 ? last : maxChars).trim()}…`
}

function normalizeText(value: any): string {
  return cleanText(value)
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
}

function formatPercent(value: any): string {
  const number = Number(value)
  if (!Number.isFinite(number)) return "—"
  const percent = number <= 1 ? number * 100 : number
  return `${Math.round(percent)}%`
}

function unwrapReport(value: any): any {
  if (!value || typeof value !== "object") return {}

  const queue = [
    value?.cir_memory_report,
    value?.cir_memory,
    value?.display?.cir_memory_report,
    value?.display?.cir_memory,
    value?.diagnostic?.cir_memory,
    value?.comparison_report,
    value?.comparison,
    value?.diagnostic?.cir_memory_report,
    value?.data,
    value?.result,
    value?.report,
    value,
  ]

  for (const candidate of queue) {
    if (!candidate || typeof candidate !== "object") continue
    if (
      candidate?.has_previous_cir === true ||
      candidate?.previous_cir_available === true ||
      candidate?.inputs_status?.previous_cir_available === true ||
      candidate?.summary ||
      asArray(candidate?.verrou_comparisons).length > 0 ||
      asArray(candidate?.comparisons).length > 0
    ) {
      return candidate
    }
  }

  return {}
}

function comparisonList(report: any): any[] {
  const candidates = [
    report?.verrou_comparisons,
    report?.comparisons,
    report?.continuity_strong,
    report?.evolution_or_partial_continuity,
    report?.new_or_not_found,
  ]

  for (const candidate of candidates) {
    if (Array.isArray(candidate) && candidate.length > 0) return candidate
  }

  return []
}

function previousCandidate(item: any): any {
  return (
    item?.best_match?.previous_candidate ||
    item?.previous_candidate ||
    item?.previous_item ||
    {}
  )
}

function currentItem(item: any): any {
  return item?.current_item || item?.current || {}
}

function evidenceText(value: any): string {
  if (!value || typeof value !== "object") return ""
  return cleanText(
    value?.excerpt ||
      value?.text ||
      value?.source_text ||
      value?.content ||
      value?.passage ||
      value?.quote ||
      "",
  )
}

function evidenceDocument(value: any): string {
  if (!value || typeof value !== "object") return ""
  const metadata =
    value?.metadata && typeof value.metadata === "object"
      ? value.metadata
      : {}

  return cleanText(
    value?.document ||
      value?.filename ||
      value?.source_document ||
      value?.source_path ||
      metadata?.document ||
      metadata?.filename ||
      metadata?.source_path ||
      "",
  )
}

function toEvidence(value: any, fallbackText = ""): SourceEvidence | null {
  if (!value || typeof value !== "object") return null
  const metadata =
    value?.metadata && typeof value.metadata === "object"
      ? value.metadata
      : {}

  const excerpt = evidenceText(value) || cleanText(fallbackText)
  const document = evidenceDocument(value)
  const sourcePath = cleanText(
    value?.source_path ||
      value?.path ||
      value?.file_path ||
      value?.document_path ||
      metadata?.source_path ||
      metadata?.path ||
      "",
  )

  if (!excerpt && !document && !sourcePath) return null

  return {
    evidence_id: value?.evidence_id || null,
    rag_chunk_id: value?.rag_chunk_id || value?.chunk_id || null,
    passage_id: value?.passage_id || value?.item_id || value?.id || null,
    document_id: value?.document_id ?? metadata?.document_id ?? null,
    document: document || null,
    filename: cleanText(value?.filename || metadata?.filename || document) || null,
    source_path: sourcePath || null,
    year:
      value?.year ??
      value?.previous_year ??
      metadata?.year ??
      metadata?.previous_year ??
      null,
    previous_year:
      value?.previous_year ?? metadata?.previous_year ?? null,
    page_number: value?.page_number ?? value?.page ?? metadata?.page_number ?? metadata?.page ?? null,
    paragraph_index:
      value?.paragraph_index ??
      value?.paragraph ??
      metadata?.paragraph_index ??
      null,
    char_start: value?.char_start ?? metadata?.char_start ?? null,
    char_end: value?.char_end ?? metadata?.char_end ?? null,
    section_title:
      cleanText(
        value?.section_title ||
          value?.section ||
          metadata?.section_title ||
          "",
      ) || null,
    role: cleanText(value?.role || metadata?.role || "") || null,
    excerpt: excerpt || null,
    text: excerpt || null,
    source_text: excerpt || null,
    metadata,
  }
}

const EVIDENCE_KEYS = [
  "source_evidence",
  "primary_evidence",
  "supporting_passages",
  "sources",
  "evidence",
  "evidences",
  "evidence_sources",
  "preuves_sources",
  "proofs",
  "preuves",
  "passages",
]

function collectEvidence(root: any, fallbackText = ""): SourceEvidence[] {
  const output: SourceEvidence[] = []
  const seen = new Set<string>()

  const add = (value: any) => {
    const evidence = toEvidence(value, fallbackText)
    if (!evidence) return

    const key = [
      evidence.passage_id || evidence.rag_chunk_id || "",
      normalizeText(evidence.document || evidence.source_path || ""),
      normalizeText(evidence.excerpt || "").slice(0, 260),
    ].join("|")

    if (!key.replace(/\|/g, "") || seen.has(key)) return
    seen.add(key)
    output.push(evidence)
  }

  const walk = (value: any, depth = 0) => {
    if (!value || depth > 6 || output.length >= 40) return
    if (Array.isArray(value)) {
      value.forEach((item) => walk(item, depth + 1))
      return
    }
    if (typeof value !== "object") return

    const hasLocation = Boolean(
      value?.document ||
        value?.filename ||
        value?.source_path ||
        value?.document_id ||
        value?.passage_id ||
        value?.rag_chunk_id,
    )
    if (hasLocation) add(value)

    EVIDENCE_KEYS.forEach((key) => {
      if (key in value) walk(value[key], depth + 1)
    })

    if (value?.original_verrou) walk(value.original_verrou, depth + 1)
    if (value?.source_json) walk(value.source_json, depth + 1)
  }

  walk(root)
  return output
}

function genericPassage(value: string): boolean {
  const normalized = normalizeText(value)
  return [
    "contexte et objectifs du projet",
    "objectif actuel",
    "passage du dossier courant",
    "extrait cible du cir precedent",
  ].includes(normalized)
}

function bestEvidence(evidence: SourceEvidence[]): SourceEvidence | null {
  if (!evidence.length) return null

  return [...evidence].sort((left, right) => {
    const score = (item: SourceEvidence) => {
      const text = cleanText(item.excerpt || item.text || "")
      let value = 0
      if (item.source_path) value += 40
      if (item.document || item.filename) value += 30
      if (item.document_id) value += 15
      if (item.page_number !== null && item.page_number !== undefined) value += 10
      if (text.length >= 80) value += 20
      if (genericPassage(text)) value -= 50
      return value + Math.min(text.length, 1600) / 1600
    }

    return score(right) - score(left)
  })[0]
}

function currentTitle(item: any): string {
  const current = currentItem(item)
  return cleanText(
    current?.section_title ||
      current?.title ||
      current?.verrou_title ||
      current?.original_verrou?.title ||
      current?.original_verrou?.verrou_title ||
      "Verrou courant",
  )
}

function currentPassage(item: any, evidence: SourceEvidence[]): string {
  const current = currentItem(item)
  const primary = bestEvidence(evidence)

  return shortText(
    primary?.excerpt ||
      current?.display_excerpt ||
      current?.primary_evidence?.excerpt ||
      current?.primary_evidence?.text ||
      current?.text ||
      current?.source_text ||
      currentTitle(item),
    1200,
  )
}

function previousPassage(item: any): string {
  const previous = previousCandidate(item)
  return shortText(
    previous?.display_excerpt ||
      previous?.excerpt ||
      previous?.text ||
      previous?.source_text ||
      "Aucun extrait ciblé disponible.",
    1200,
  )
}

function previousYear(report: any, item?: any): string {
  const previous = item ? previousCandidate(item) : {}
  return cleanText(
    previous?.previous_year ||
      previous?.year ||
      report?.previous_cir_year_used ||
      asArray(report?.previous_cir_years_used)[0] ||
      "N-1",
  )
}

function similarityScore(item: any): number | null {
  const candidates = [
    item?.best_match?.similarity_score,
    item?.decision?.continuity_score,
    item?.similarity_score,
  ]

  for (const candidate of candidates) {
    const number = Number(candidate)
    if (Number.isFinite(number)) return number <= 1 ? number : number / 100
  }

  return null
}

function noveltyScore(item: any): number | null {
  const number = Number(item?.decision?.novelty_score)
  return Number.isFinite(number) ? (number <= 1 ? number : number / 100) : null
}

function decisionLabel(item: any): string {
  const status = cleanText(item?.decision?.status).toLowerCase()

  if (status.includes("strong") || status.includes("continuity_strong")) {
    return "Continuité forte"
  }
  if (status.includes("evolution") || status.includes("partial")) {
    return "Évolution ou continuité partielle"
  }
  if (status.includes("new") || status.includes("not_found")) {
    return "Nouveauté potentielle"
  }

  const score = similarityScore(item)
  if (score !== null && score >= 0.5) return "Continuité forte"
  if (score !== null && score >= 0.3) return "Évolution à examiner"
  return "Nouveauté potentielle"
}

function decisionBadgeClass(label: string): string {
  if (label === "Continuité forte") {
    return "border-amber-300 bg-amber-50 text-amber-800"
  }
  if (label.includes("Évolution")) {
    return "border-blue-300 bg-blue-50 text-blue-800"
  }
  return "border-emerald-300 bg-emerald-50 text-emerald-800"
}

function sharedThemesCount(item: any): number {
  return asArray(item?.best_match?.similarity_details?.shared_themes).length
}

function consultantReading(item: any, year: string): string {
  const previous = previousCandidate(item)
  const previousSection = cleanText(previous?.section_title || previous?.section_key)
  const current = currentTitle(item)
  const label = decisionLabel(item)

  if (label === "Continuité forte") {
    return `Le CIR ${year} contient un passage techniquement proche${
      previousSection ? ` dans « ${previousSection} »` : ""
    }. Le consultant doit expliquer précisément la progression apportée par « ${current} » pendant l’année courante.`
  }

  if (label.includes("Évolution")) {
    return `Le passage du CIR ${year} constitue un antécédent utile, mais le dossier courant semble apporter une évolution sur « ${current} ». Les nouveaux essais, paramètres et résultats doivent être explicités.`
  }

  return `Le verrou « ${current} » n’a pas été retrouvé avec une correspondance forte dans le CIR ${year}. Le consultant doit néanmoins vérifier qu’il ne s’agit pas d’un changement de formulation.`
}

function EvidencePanel({
  title,
  subtitle,
  projectId,
  passage,
  evidence,
  documents,
  tone,
}: {
  title: string
  subtitle: string
  projectId: number | string
  passage: string
  evidence: SourceEvidence[]
  documents: ReturnType<typeof useProjectSourceDocuments>
  tone: "previous" | "current"
}) {
  const toneClass =
    tone === "previous"
      ? "border-amber-200 bg-amber-50/60"
      : "border-brand/20 bg-brand/5"

  return (
    <div className={`rounded-xl border ${toneClass} p-4 space-y-3`}>
      <div className="space-y-1">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {title}
        </p>
        <p className="text-xs text-muted-foreground">{subtitle}</p>
      </div>

      <SourceTextWithDocuments
        projectId={projectId}
        text={passage || "Aucun passage disponible."}
        documents={documents}
        evidence={evidence}
        compact
      />

      <p className="text-[11px] leading-5 text-muted-foreground">
        Ouvre le document proposé pour afficher ce passage directement surligné,
        comme dans les preuves des verrous EnnoDiagnostic.
      </p>
    </div>
  )
}

export default function CirPreviousContinuityTab({
  diagnostic,
  projectId,
  apiBaseUrl,
  authToken,
  organisme,
  projectName,
  currentYear,
}: CirPreviousContinuityTabProps) {
  // Conservés dans le contrat public du composant pour compatibilité avec diagnosis-page.tsx.
  void apiBaseUrl
  void authToken
  void organisme
  void projectName
  void currentYear

  const documents = useProjectSourceDocuments(projectId)
  const report = useMemo(() => unwrapReport(diagnostic), [diagnostic])
  const comparisons = useMemo(() => comparisonList(report), [report])

  const averageContinuity = useMemo(() => {
    const values = comparisons
      .map(similarityScore)
      .filter((value): value is number => value !== null)
    if (!values.length) return null
    return values.reduce((sum, value) => sum + value, 0) / values.length
  }, [comparisons])

  const year = previousYear(report)
  const hiddenCount = Number(report?.summary?.hidden_comparisons_count || 0)
  const hasPreviousCir = Boolean(
    report?.has_previous_cir ||
    report?.previous_cir_available ||
    report?.inputs_status?.previous_cir_available ||
    diagnostic?.has_previous_cir ||
    diagnostic?.previous_cir_available ||
    diagnostic?.inputs_status?.previous_cir_available ||
    comparisons.length > 0
  )

  if (!hasPreviousCir) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <History className="size-4 text-brand" />
            Comparaison CIR N-1
          </CardTitle>
          <CardDescription>
            Aucun rapport de comparaison exploitable n’est disponible.
          </CardDescription>
        </CardHeader>
      </Card>
    )
  }

  if (comparisons.length === 0) {
    return (
      <Card className="border-amber-200 bg-amber-50/60">
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <History className="size-4 text-amber-700" />
            CIR {year} détecté
          </CardTitle>
          <CardDescription className="leading-5">
            Le CIR précédent est disponible, mais le détail de ses rapprochements
            n’est pas encore chargé. Actualise la page ou relance uniquement la comparaison CIR.
          </CardDescription>
        </CardHeader>
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      <Card className="border-brand/20 bg-brand/5">
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <FileSearch className="size-4 text-brand" />
            Comparaison CIR N-1 avec extraits ciblés et surlignage
          </CardTitle>
          <CardDescription className="text-xs leading-5">
            Chaque extrait courant et historique est relié à son document source.
            L’ouverture du document surligne automatiquement le passage comparé.
          </CardDescription>
        </CardHeader>

        <CardContent className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-lg border bg-white p-3">
            <p className="text-xs text-muted-foreground">CIR précédent</p>
            <p className="mt-1 text-xl font-bold text-foreground">{year}</p>
          </div>
          <div className="rounded-lg border bg-white p-3">
            <p className="text-xs text-muted-foreground">Comparaisons utiles</p>
            <p className="mt-1 text-xl font-bold text-foreground">
              {comparisons.length}
            </p>
          </div>
          <div className="rounded-lg border bg-white p-3">
            <p className="text-xs text-muted-foreground">Masquées</p>
            <p className="mt-1 text-xl font-bold text-foreground">{hiddenCount}</p>
          </div>
          <div className="rounded-lg border bg-white p-3">
            <p className="text-xs text-muted-foreground">Continuité moyenne</p>
            <p className="mt-1 text-xl font-bold text-foreground">
              {averageContinuity === null ? "—" : formatPercent(averageContinuity)}
            </p>
          </div>
        </CardContent>
      </Card>

      <div className="space-y-4">
        {comparisons.map((item, index) => {
          const current = currentItem(item)
          const previous = previousCandidate(item)
          const currentEvidence = collectEvidence(current, current?.display_excerpt || current?.text)
          const previousEvidence = collectEvidence(previous, previous?.text)
          const currentExcerpt = currentPassage(item, currentEvidence)
          const previousExcerpt = previousPassage(item)
          const itemYear = previousYear(report, item)
          const label = decisionLabel(item)
          const similarity = similarityScore(item)
          const novelty = noveltyScore(item)

          return (
            <Card key={String(current?.id || current?.item_id || index)}>
              <CardHeader className="space-y-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 space-y-1">
                    <CardTitle className="text-base leading-6">
                      {currentTitle(item)}
                    </CardTitle>
                    <CardDescription className="text-xs">
                      Comparaison CIR {itemYear} / dossier courant
                    </CardDescription>
                  </div>

                  <Badge variant="outline" className={decisionBadgeClass(label)}>
                    {label}
                  </Badge>
                </div>

                <div className="flex flex-wrap gap-2">
                  <Badge variant="secondary">
                    similarité {similarity === null ? "—" : formatPercent(similarity)}
                  </Badge>
                  <Badge variant="secondary">
                    thèmes communs {sharedThemesCount(item)}
                  </Badge>
                  <Badge variant="secondary">
                    apport courant {novelty === null ? "—" : formatPercent(novelty)}
                  </Badge>
                </div>
              </CardHeader>

              <CardContent className="space-y-4">
                <div className="rounded-xl border border-blue-200 bg-blue-50/60 p-4">
                  <div className="flex items-start gap-3">
                    <ShieldCheck className="mt-0.5 size-4 shrink-0 text-blue-700" />
                    <div className="space-y-1">
                      <p className="text-xs font-semibold uppercase tracking-wide text-blue-800">
                        Lecture consultant
                      </p>
                      <p className="text-sm leading-7 text-foreground">
                        {consultantReading(item, itemYear)}
                      </p>
                    </div>
                  </div>
                </div>

                <div className="grid gap-4 xl:grid-cols-[1fr_auto_1fr] xl:items-stretch">
                  <EvidencePanel
                    title="Extrait ciblé du CIR précédent"
                    subtitle={`${itemYear} · ${cleanText(previous?.section_title || previous?.section_key || "CIR précédent")}`}
                    projectId={projectId}
                    passage={previousExcerpt}
                    evidence={previousEvidence.length ? previousEvidence : [toEvidence(previous, previousExcerpt)].filter(Boolean) as SourceEvidence[]}
                    documents={documents}
                    tone="previous"
                  />

                  <div className="hidden items-center justify-center xl:flex">
                    <ArrowRight className="size-5 text-muted-foreground" />
                  </div>

                  <EvidencePanel
                    title="Passage du dossier courant"
                    subtitle={currentTitle(item)}
                    projectId={projectId}
                    passage={currentExcerpt}
                    evidence={currentEvidence.length ? currentEvidence : [toEvidence(current, currentExcerpt)].filter(Boolean) as SourceEvidence[]}
                    documents={documents}
                    tone="current"
                  />
                </div>
              </CardContent>
            </Card>
          )
        })}
      </div>

      <Card className="border-emerald-200 bg-emerald-50/50">
        <CardContent className="flex items-start gap-3 p-4">
          <Sparkles className="mt-0.5 size-4 shrink-0 text-emerald-700" />
          <p className="text-xs leading-6 text-emerald-950">
            Le surlignage utilise la même route et le même dialogue documentaire que
            les preuves des verrous. Les fichiers historiques provenant de Memory V2
            sont ouverts par leur <code>source_path</code> lorsqu’ils ne figurent pas
            dans la liste des documents de l’année courante.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}
