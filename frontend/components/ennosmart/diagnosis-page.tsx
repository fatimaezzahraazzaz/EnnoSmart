"use client"

import {
  type FormEvent,
  useEffect,
  useMemo,
  useState } from "react"
import {
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  BrainCircuit,
  CheckCircle2,
  FileText,
  Loader2,
  Lock,
  Plus,
  Play,
  RefreshCw,
  Search,
  Sparkles,
  Target,
  Tags,
  TrendingUp,
  Upload,
  XCircle,
  } from "lucide-react"

import { Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger } from "@/components/ui/accordion"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle } from "@/components/ui/card"
import { Tabs,
  TabsContent,
  TabsList,
  TabsTrigger } from "@/components/ui/tabs"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

import {
  createManualVerrou,
  getAccessToken,
  getDiagnosticLatest,
  getDiagnosticCorpusReview,
  getDocuments,
  getProjects,
  getVerrous,
  importExistingDiagnostic,
  runDiagnostic,
  syncVerrous,
  updateDiagnosticCorpusReview,
  updateVerrouDecision,
  type DiagnosticCorpusReview,
  type DocumentRead,
  type ProjectRead,
  type VerrouRead,
} from "@/lib/api"
import { getCurrentProjectId, setCurrentProjectId } from "@/lib/project-session"
import { CirFinalConsultantPanel } from "@/components/ennosmart/cir-final-consultant-panel"
import CirPreviousContinuityTab from "@/components/ennosmart/cir-previous-continuity-tab"
import { DiagnosticRagChat } from "@/components/ennosmart/diagnostic-rag-chat"
import {
  ContextBadge,
  PageHeader,
  StatusNotice,
  WorkflowSteps,
} from "@/components/ennosmart/workspace-ui"

import {
  SourceTextWithDocuments,
  SourceEvidenceCitations,
  useProjectSourceDocuments,
  type DbSourceDocument,
  type SourceEvidence,
} from "@/components/ennosmart/source-documents-dialog"
type ConsultantDecision = "garde" | "rejete" | "reformuler" | "en_attente"
type RunMode = "prepare" | "agent" | "full" | "import" | null

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

const prepareSteps = [
  "Lecture des fichiers raw",
  "Extraction texte",
  "Analyse NLP",
  "Contrôle Frascati",
  "Création chunks",
  "Indexation RAG / Chroma",
]

const agentSteps = [
  "Lecture Chroma",
  "Score IA documentaire",
  "Récupération Frascati",
  "Génération EnnoDiagnostic",
  "Synchronisation des verrous",
]

const fullSteps = [
  "Extraction documents",
  "NLP + Frascati",
  "RAG / Chroma",
  "Score IA",
  "Agent EnnoDiagnostic",
  "Synchronisation",
]

const diagnosticTabs = [
  { value: "overview", label: "Vue d’ensemble" },
  { value: "diagnostic", label: "Diagnostic CIR" },
  { value: "controle-ia", label: "Contrôle IA" },
  { value: "cir-precedent", label: "CIR précédent" },
  { value: "comparaison-docs", label: "Comparaison docs" },
  { value: "validation", label: "Validation" },
  { value: "cir-final-consultant", label: "Déposer le CIR final" },
] as const

const diagnosticSubsections = [
  { value: "objectif", label: "Objectif global", shortLabel: "Objectif" },
  { value: "eligibilite", label: "Étude d’éligibilité", shortLabel: "Éligibilité" },
  { value: "verrous", label: "Verrous & preuves", shortLabel: "Verrous" },
  { value: "demarche", label: "Pertinence des démarches", shortLabel: "Démarche" },
  { value: "resultats", label: "Résultats & métriques", shortLabel: "Résultats" },
  { value: "parametres", label: "Paramètres & contraintes", shortLabel: "Paramètres" },
] as const

type DiagnosticSubsection = (typeof diagnosticSubsections)[number]["value"]


function firstNonEmptyArray(...values: any[]) {
  for (const value of values) {
    if (Array.isArray(value) && value.length > 0) return value
  }
  return []
}

function unwrapCirPreviousReportForDisplay(value: any): any {
  if (!value || typeof value !== "object") return {}

  const candidates = [
    // Toujours privilégier le rapport CIR structuré. Une enveloppe générale
    // peut aussi avoir un champ `summary` et masquer les comparaisons imbriquées.
    value?.cir_memory_report,
    value?.cir_memory,
    value?.display?.cir_memory_report,
    value?.display?.cir_memory,
    value?.diagnostic?.cir_memory_report,
    value?.diagnostic?.cir_memory,
    value?.comparison_report,
    value?.comparison,
    value?.cir_previous_report,
    value?.previous_cir_report,
    value?.payload,
    value?.data,
    value?.result,
    value?.report,
    value,
  ]

  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== "object") continue
    if (
      candidate?.has_previous_cir === true ||
      candidate?.previous_cir_available === true ||
      candidate?.inputs_status?.previous_cir_available === true ||
      candidate?.summary ||
      firstNonEmptyArray(candidate?.verrou_comparisons, candidate?.comparisons, candidate?.new_or_not_found, candidate?.evolution_or_partial_continuity, candidate?.continuity_strong).length > 0 ||
      firstNonEmptyArray(candidate?.previous_cir_years_used, candidate?.previous_years, candidate?.registered_previous_cirs).length > 0
    ) {
      return candidate
    }
  }

  return {}
}

function cirReportHasComparisons(value: any) {
  const report = unwrapCirPreviousReportForDisplay(value)
  return firstNonEmptyArray(
    report?.verrou_comparisons,
    report?.comparisons,
    report?.new_or_not_found,
    report?.evolution_or_partial_continuity,
    report?.continuity_strong
  ).length > 0
}

function chooseCirMemoryReport(...values: any[]) {
  for (const value of values) {
    const report = unwrapCirPreviousReportForDisplay(value)
    if (cirReportHasComparisons(report)) return report
  }

  for (const value of values) {
    const report = unwrapCirPreviousReportForDisplay(value)
    if (
      report?.has_previous_cir === true ||
      report?.previous_cir_available === true ||
      report?.inputs_status?.previous_cir_available === true ||
      report?.summary ||
      firstNonEmptyArray(report?.previous_cir_years_used, report?.previous_years, report?.registered_previous_cirs).length > 0
    ) {
      return report
    }
  }

  return {}
}

function formatScore(score: number | string | null | undefined) {
  if (score === null || score === undefined || score === "") return "—"

  const value = Number(score)
  if (!Number.isFinite(value)) return "—"

  const normalized = value <= 1 ? value * 100 : value
  return `${Math.round(normalized)}%`
}

function scorePercent(score: number | string | null | undefined) {
  const value = Number(score)
  if (!Number.isFinite(value)) return null
  return Math.max(0, Math.min(100, value <= 1 ? value * 100 : value))
}

function formatVerrouScoreV124(score: number | string | null | undefined) {
  const normalized = normalizeVerrouScoreV124(score)
  if (normalized === null) return "—"
  return formatScore(normalized)
}

function riskClass(risk: string | null | undefined) {
  const value = (risk || "").toLowerCase()

  if (value.includes("élev") || value.includes("haut") || value.includes("high")) {
    return "bg-destructive/10 text-destructive border-destructive/30"
  }

  if (value.includes("moy") || value.includes("medium")) {
    return "bg-warning/10 text-warning border-warning/30"
  }

  if (value.includes("faible") || value.includes("low")) {
    return "bg-success/10 text-success border-success/30"
  }

  return "bg-muted text-muted-foreground border-border"
}

function tagClass(tag: string | null) {
  const value = (tag || "").toUpperCase()

  if (value.includes("PERTINENT")) {
    return "bg-success/10 text-success border-success/30"
  }

  if (value.includes("MOYEN") || value.includes("VERIFIER") || value.includes("VÉRIFIER")) {
    return "bg-warning/10 text-warning border-warning/30"
  }

  if (value.includes("FAIBLE")) {
    return "bg-muted text-muted-foreground border-border"
  }

  return "bg-brand/10 text-brand border-brand/30"
}

function decisionClass(status: string) {
  switch (status) {
    case "garde":
      return "bg-success/10 text-success border-success/30"
    case "rejete":
      return "bg-destructive/10 text-destructive border-destructive/30"
    case "reformuler":
      return "bg-brand/10 text-brand border-brand/30"
    default:
      return "bg-muted text-muted-foreground border-border"
  }
}

function decisionLabel(status: string) {
  switch (status) {
    case "garde":
      return "Retenu"
    case "rejete":
      return "Non retenu"
    case "reformuler":
      return "À consolider"
    default:
      return "À examiner"
  }
}

function isManualConsultantVerrou(verrou: VerrouRead) {
  const sourceJson = verrou.source_json || {}
  return Boolean(
    sourceJson.manual_verrou ||
      sourceJson.supplementary_verrou ||
      (sourceJson.human_validated === true &&
        sourceJson.automatic_verrou_creation === false)
  )
}

function getSourceText(verrou: VerrouRead) {
  const sourceJson = verrou.source_json || {}

  return (
    sourceJson.manual_scholar_text ||
    sourceJson.evidence_summary ||
    sourceJson.scientific_lock ||
    sourceJson.why_not_simple_engineering ||
    sourceJson.text ||
    sourceJson.source_text ||
    sourceJson.description ||
    verrou.justification ||
    "Aucun extrait source disponible."
  )
}

type VerrouSourceDocument = {
  key: string
  document: string
  displayName: string
  sourcePath: string
  passagesCount: number
  excerpts: string[]
  evidence: SourceEvidence[]
}

function cleanSourceDocumentName(value: string) {
  const raw = cleanDisplayText(String(value || ""))
  const filename = raw.split(/[\\/]/).pop() || raw

  return filename
    .replace(/_[a-f0-9]{10,}(?=\.[^.]+$)/i, "")
    .replace(/\.(docx?|docm|pdf|msg|pptx?|xlsx?|txt)$/i, "")
    .replace(/_/g, " ")
    .replace(/\s+/g, " ")
    .trim()
}

function isAggregatedSourceDocumentLabel(value: string) {
  const text = cleanDisplayText(String(value || ""))

  if (!text || !text.includes(";")) {
    return false
  }

  const parts = text
    .split(/\s*;\s*/)
    .map((part) => part.trim())
    .filter(Boolean)

  return parts.length > 1
}

function getVerrouSourceDocuments(verrou: VerrouRead): VerrouSourceDocument[] {
  const sourceJson = verrou.source_json || {}
  const rawSources: any[] = []

  // Les vraies sources / preuves du verrou.
  for (const value of [
    sourceJson.sources,
    sourceJson.evidence_sources,
    sourceJson.source_documents,
    sourceJson.evidence,
    (verrou as any).sources,
    (verrou as any).evidence_sources,
  ]) {
    if (Array.isArray(value)) rawSources.push(...value)
  }

  // Compatibilité avec les diagnostics où un document unique est stocké
  // directement dans sourceJson.document / filename.
  //
  // IMPORTANT :
  // certains diagnostics contiennent ici plusieurs noms concaténés :
  // "doc1 ; doc2 ; doc3 ; doc4".
  // Ce champ agrégé ne doit pas devenir une 5e carte de document.
  const directDocument =
    sourceJson.document ||
    sourceJson.source_document ||
    sourceJson.filename ||
    sourceJson.document_name

  const directDocumentText = cleanDisplayText(String(directDocument || ""))

  if (
    directDocumentText &&
    !isAggregatedSourceDocumentLabel(directDocumentText)
  ) {
    rawSources.push({ document: directDocumentText })
  }

  const grouped = new Map<string, VerrouSourceDocument>()

  rawSources.forEach((source: any) => {
    const item = typeof source === "string" ? { document: source } : (source || {})
    const metadata = item?.metadata && typeof item.metadata === "object" ? item.metadata : {}

    const document = cleanDisplayText(
      item?.document ||
        item?.filename ||
        item?.source_name ||
        item?.name ||
        metadata?.document ||
        metadata?.filename ||
        metadata?.source_name ||
        ""
    )

    const sourcePath = cleanDisplayText(
      item?.source_path ||
        item?.path ||
        metadata?.source_path ||
        metadata?.path ||
        ""
    )

    if (!document && !sourcePath) return

    const canonicalDocument =
      document ||
      sourcePath.split(/[\\/]/).pop() ||
      sourcePath

    // Sécurité supplémentaire : même si une concaténation arrive depuis
    // une autre propriété de source_json, elle n'est jamais affichée
    // comme un document réel.
    if (isAggregatedSourceDocumentLabel(canonicalDocument)) return

    const key = normalizeKeyV93(sourcePath || canonicalDocument)
    if (!key) return

    const excerpt = cleanDisplayText(
      item?.excerpt ||
        item?.text ||
        item?.source_text ||
        item?.content ||
        ""
    )

    const current = grouped.get(key) || {
      key,
      document: canonicalDocument,
      displayName: cleanSourceDocumentName(canonicalDocument),
      sourcePath,
      passagesCount: 0,
      excerpts: [],
      evidence: [],
    }

    const evidenceItem: SourceEvidence = {
      evidence_id: item?.evidence_id,
      rag_chunk_id: item?.rag_chunk_id || metadata?.rag_chunk_id,
      passage_id:
        item?.passage_id ||
        metadata?.passage_id ||
        metadata?.original_passage_id,
      document_id: item?.document_id || metadata?.document_id,
      document: canonicalDocument,
      filename: item?.filename || metadata?.filename,
      source_path: sourcePath,
      page_number: item?.page_number ?? metadata?.page_number ?? metadata?.page,
      paragraph_index:
        item?.paragraph_index ?? metadata?.paragraph_index,
      char_start:
        item?.char_start ??
        metadata?.char_start ??
        metadata?.start_char ??
        metadata?.start,
      char_end:
        item?.char_end ??
        metadata?.char_end ??
        metadata?.end_char ??
        metadata?.end,
      section_title: item?.section_title || metadata?.section_title,
      role: item?.role || metadata?.role,
      excerpt,
      metadata,
    }

    const evidenceKey = cleanDisplayText(
      String(
        evidenceItem.passage_id ||
          evidenceItem.rag_chunk_id ||
          evidenceItem.evidence_id ||
          `${canonicalDocument}:${excerpt.slice(0, 160)}`
      )
    )

    const alreadyAdded = current.evidence.some((existing, existingIndex) => {
      const existingKey = cleanDisplayText(
        String(
          existing.passage_id ||
            existing.rag_chunk_id ||
            existing.evidence_id ||
            `${existing.document || canonicalDocument}:${String(existing.excerpt || "").slice(0, 160)}:${existingIndex}`
        )
      )
      return existingKey === evidenceKey
    })

    if (!alreadyAdded) {
      current.evidence.push(evidenceItem)
    }

    current.passagesCount = current.evidence.length

    if (excerpt && !current.excerpts.includes(excerpt)) {
      current.excerpts.push(excerpt)
    }

    grouped.set(key, current)
  })

  return Array.from(grouped.values())
    .filter((item) => !isAggregatedSourceDocumentLabel(item.document))
    .sort(
      (a, b) =>
        b.passagesCount - a.passagesCount ||
        a.displayName.localeCompare(b.displayName)
    )
}

function getSources(verrou: VerrouRead) {
  return getVerrouSourceDocuments(verrou).map((item) => item.document)
}

function getConsultantContextExplanation(verrou: VerrouRead) {
  const sourceJson = verrou.source_json || {}

  const direct = cleanDisplayText(
    sourceJson.consultant_explanation ||
      sourceJson.agent_reasoning ||
      sourceJson.why_agent_found_verrou ||
      (verrou as any).consultant_explanation ||
      (verrou as any).agent_reasoning ||
      (verrou as any).why_agent_found_verrou ||
      ""
  )

  if (direct) return sanitizeVerrouExplanation(direct)

  const scientificLock = cleanDisplayText(sourceJson.scientific_lock || "")
  const whyNotSimple = cleanDisplayText(sourceJson.why_not_simple_engineering || "")
  const evidenceSummary = cleanDisplayText(sourceJson.evidence_summary || "")
  const sources = getSources(verrou)

  const parts: string[] = []

  parts.push(
    `EnnoDiagnostic identifie ce point comme un verrou car les sources du projet font apparaître une incertitude technique autour de : ${verrou.title}.`
  )

  if (sources.length > 0) {
    parts.push(`Les indices proviennent notamment de : ${sources.slice(0, 3).join(" ; ")}.`)
  }

  if (scientificLock) {
    parts.push(`Incertitude détectée : ${scientificLock}`)
  }

  if (whyNotSimple) {
    parts.push(`Ce n’est pas seulement de l’ingénierie standard car : ${whyNotSimple}`)
  }

  if (evidenceSummary) {
    parts.push(`Preuves utilisées : ${evidenceSummary}`)
  }

  return sanitizeVerrouExplanation(parts.join(" "))
}

function sanitizeVerrouExplanation(value: string) {
  return cleanDisplayText(value || "")
    .replace(/\bAucun procédé existant ne garantit\b/gi, "Les documents fournis ne montrent pas de solution directement applicable garantissant")
    .replace(/\bAucune solution existante ne garantit\b/gi, "Les documents fournis ne montrent pas de solution directement applicable garantissant")
    .replace(/\baucun procédé existant ne garantit\b/gi, "les documents fournis ne montrent pas de solution directement applicable garantissant")
    .replace(/\baucune solution existante ne garantit\b/gi, "les documents fournis ne montrent pas de solution directement applicable garantissant")
    .replace(/\s+/g, " ")
    .trim()
}

function firstUsefulSentence(value: string, limit = 260) {
  const clean = sanitizeVerrouExplanation(value)
  if (!clean) return ""

  const cutMarkers = [
    " Comment ",
    " Pourquoi ",
    " La simple ",
    " Les sources ",
    " Les documents ",
    " Les preuves ",
    " Ce point ",
    " Aucun procédé ",
    " Aucune solution ",
  ]

  let candidate = clean
  for (const marker of cutMarkers) {
    const idx = candidate.indexOf(marker)
    if (idx > 80) {
      candidate = candidate.slice(0, idx).trim()
      break
    }
  }

  const firstSentence = candidate.match(/^(.{80,}?[.!?])\s+/)
  if (firstSentence?.[1]) candidate = firstSentence[1].trim()

  if (candidate.length > limit) return `${candidate.slice(0, limit).trim()}…`
  return candidate
}

type VerrouExplanationSections = {
  detection: string
  uncertainty: string
  notSimpleEngineering: string
  evidence: string
}

type HistoricalLockContinuity = {
  status: string
  previousYears: string[]
  familyTitles: string[]
  locks: string[]
  methods: string[]
  parameters: string[]
  results: string[]
}

function getHistoricalLockContinuity(verrou: VerrouRead): HistoricalLockContinuity | null {
  const sourceJson = verrou.source_json || {}
  const full = isObjectV107(sourceJson?.full_persisted_verrou)
    ? sourceJson.full_persisted_verrou
    : {}
  const history =
    (isObjectV107((verrou as any)?.historical_continuity) && (verrou as any).historical_continuity) ||
    (isObjectV107(sourceJson?.historical_continuity) && sourceJson.historical_continuity) ||
    (isObjectV107(full?.historical_continuity) && full.historical_continuity) ||
    null

  if (!history || history?.history_is_current_proof === true) return null

  const rows = (value: any): string[] => {
    if (!Array.isArray(value)) return []
    return value
      .map((item: any) => cleanDisplayText(
        typeof item === "string"
          ? item
          : item?.text || item?.title || item?.excerpt || ""
      ))
      .filter(Boolean)
      .filter((value: string, index: number, all: string[]) => all.indexOf(value) === index)
      .slice(0, 8)
  }

  const story = isObjectV107(history?.historical_story) ? history.historical_story : {}
  const familyTitles = [
    ...rows(history?.historical_family_titles),
    cleanDisplayText(history?.historical_family_title || ""),
  ].filter(Boolean).filter((value, index, all) => all.indexOf(value) === index)
  const locks = [
    ...rows(history?.historical_lock_context),
    cleanDisplayText(history?.historical_excerpt || ""),
  ].filter(Boolean).filter((value, index, all) => all.indexOf(value) === index)
  const methods = [...rows(history?.historical_method_context), ...rows(story?.methode)]
    .filter((value, index, all) => all.indexOf(value) === index)
  const parameters = [...rows(history?.historical_parameter_context), ...rows(story?.parametre)]
    .filter((value, index, all) => all.indexOf(value) === index)
  const results = [...rows(history?.historical_result_context), ...rows(story?.resultat)]
    .filter((value, index, all) => all.indexOf(value) === index)
  const previousYears = [
    ...rows(history?.previous_years),
    cleanDisplayText(history?.previous_year || ""),
  ].filter(Boolean).filter((value, index, all) => all.indexOf(value) === index)
  const status = cleanDisplayText(history?.status || "")

  if (!status && !familyTitles.length && !locks.length && !methods.length && !parameters.length && !results.length) {
    return null
  }

  return { status, previousYears, familyTitles, locks, methods, parameters, results }
}

function historicalContinuityLabel(status: string): string {
  const labels: Record<string, string> = {
    continued: "Continuité confirmée",
    refined: "Verrou affiné",
    sub_lock: "Sous-verrou poursuivi",
    partially_lifted: "Partiellement levé",
    extended_scope: "Périmètre étendu",
    mixed_continuity: "Continuité multiple",
    mixed_continuity_and_new_subproblems: "Continuité avec nouveaux sous-problèmes",
    continued_to_confirm: "Continuité récupérée à confirmer",
  }
  return labels[status] || cleanDisplayText(status) || "Continuité N-1"
}

type HistoricalMemoryCardMetric = {
  isMemoryCard: boolean
  percentage: number | null
}

function getHistoricalMemoryCardMetric(verrou: VerrouRead): HistoricalMemoryCardMetric {
  const sourceJson = isObjectV107(verrou?.source_json) ? verrou.source_json : {}
  const full = isObjectV107(sourceJson?.full_persisted_verrou)
    ? sourceJson.full_persisted_verrou
    : {}
  const agentItem = isObjectV107(sourceJson?.full_agent_item)
    ? sourceJson.full_agent_item
    : {}
  const holders = [(verrou as any) || {}, sourceJson, full, agentItem]
  const isMemoryCard = holders.some((holder: any) => {
    const origin = String(holder?.candidate_origin || "")
    return Boolean(
      holder?.historical_memory_card === true ||
      holder?.historical_gap_recovered === true ||
      origin.startsWith("historical_memory_")
    )
  })

  if (!isMemoryCard) return { isMemoryCard: false, percentage: null }

  let rawValue: any = null
  for (const holder of holders) {
    const history = isObjectV107(holder?.historical_continuity)
      ? holder.historical_continuity
      : {}
    rawValue =
      holder?.continuity_percentage ??
      history?.continuity_percentage ??
      history?.confidence ??
      rawValue
    if (rawValue !== null && rawValue !== undefined && rawValue !== "") break
  }

  const numeric = Number(rawValue)
  if (!Number.isFinite(numeric) || numeric < 0) {
    return { isMemoryCard: true, percentage: null }
  }
  const percentage = numeric <= 1 ? numeric * 100 : numeric
  return {
    isMemoryCard: true,
    percentage: Math.max(0, Math.min(100, Math.round(percentage))),
  }
}

function getVerrouExplanationSections(verrou: VerrouRead): VerrouExplanationSections {
  const sourceJson = verrou.source_json || {}
  const sources = getSources(verrou)

  if (isManualConsultantVerrou(verrou)) {
    return {
      detection:
        cleanDisplayText(
          sourceJson.manual_description ||
            verrou.justification ||
            sourceJson.manual_scholar_text ||
            ""
        ) ||
        "Ce verrou a été ajouté explicitement par le consultant et sera traité comme un verrou retenu.",
      uncertainty: "",
      notSimpleEngineering: "",
      evidence: "",
    }
  }

  const direct = sanitizeVerrouExplanation(
    sourceJson.consultant_explanation ||
      sourceJson.agent_reasoning ||
      sourceJson.why_agent_found_verrou ||
      (verrou as any).consultant_explanation ||
      (verrou as any).agent_reasoning ||
      (verrou as any).why_agent_found_verrou ||
      ""
  )

  const scientificLock = sanitizeVerrouExplanation(
    sourceJson.scientific_lock ||
      (verrou as any).scientific_lock ||
      ""
  )

  const whyNotSimple = sanitizeVerrouExplanation(
    sourceJson.why_not_simple_engineering ||
      (verrou as any).why_not_simple_engineering ||
      ""
  )

  const evidenceSummary = sanitizeVerrouExplanation(
    sourceJson.evidence_summary ||
      (verrou as any).evidence_summary ||
      ""
  )

  const detection = firstUsefulSentence(direct, 420) ||
    `EnnoDiagnostic a détecté ce verrou parce que les sources du projet font apparaître une incertitude technique autour de « ${verrou.title} ».${
      sources.length ? ` Les indices proviennent notamment de ${sources.slice(0, 2).join(" ; ")}.` : ""
    }`

  return {
    detection,
    uncertainty: scientificLock,
    notSimpleEngineering: whyNotSimple,
    evidence: evidenceSummary,
  }
}

function getShortVerrouRationale(verrou: VerrouRead) {
  const sections = getVerrouExplanationSections(verrou)
  return sections.detection || "Verrou à confirmer à partir des preuves sources."
}

function normalizeForConsultant(value: string) {
  return String(value || "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
}


function consultantSignalTitle(verrou: VerrouRead) {
  const title = cleanDisplayText(verrou.title || "")
  const source = cleanDisplayText(getSourceText(verrou))
  const justification = cleanDisplayText(verrou.justification || "")
  const candidate = title || source.split(/[.!?\n]/).find((line) => line.trim().length > 12) || justification

  if (candidate) {
    return candidate
      .replace(/^Verrou\s*[:\-]\s*/i, "")
      .replace(/^Signal\s*R&D\s*[:\-]\s*/i, "")
      .slice(0, 140)
      .trim()
  }

  return "Signal R&D détecté"
}

function proposedCirVerrou(verrou: VerrouRead) {
  const title = consultantSignalTitle(verrou)
  const source = cleanDisplayText(getSourceText(verrou))
  const justification = cleanDisplayText(verrou.justification || "")
  const evidence = source || justification

  if (title && title !== "Signal R&D détecté") {
    return `${title}. À confirmer et reformuler en verrou CIR par le consultant à partir des preuves sources.`
  }

  if (evidence) {
    return "Piste R&D à rattacher au verrou CIR le plus proche après validation consultant."
  }

  return "Verrou R&D à reformuler et valider par le consultant à partir des preuves sources."
}

function consultantInterpretation(verrou: VerrouRead) {
  const sections = getVerrouExplanationSections(verrou)
  const source = cleanDisplayText(getSourceText(verrou))
  const justification = cleanDisplayText(verrou.justification || "")

  if (sections.detection) {
    return sections.detection
  }

  if (justification) {
    return sanitizeVerrouExplanation(justification)
  }

  if (source && source !== "Aucun extrait source disponible.") {
    return "EnnoDiagnostic a identifié cette piste à partir des preuves présentes dans les documents bruts. Le consultant doit vérifier si elle correspond à une incertitude technique réelle, si elle n’est pas déjà résolue par les solutions connues, et si elle peut être reliée à une démarche expérimentale."
  }

  return "EnnoDiagnostic a identifié une piste R&D. Le consultant peut la retenir, la consolider ou la rattacher à un verrou plus structurant du dossier."
}

function getConsultantCheckText(verrou: VerrouRead) {
  const sourceJson = verrou.source_json || {}
  if (isManualConsultantVerrou(verrou)) {
    const keywords = Array.isArray(sourceJson.keywords)
      ? sourceJson.keywords.filter(Boolean).join(", ")
      : ""
    return (
      "Vérifier que la formulation décrit bien l’incertitude scientifique ou technique à documenter. " +
      (keywords
        ? `Les mots-clés transmis à EnnoScholar sont : ${keywords}.`
        : "Ajoutez des mots-clés précis pour guider la recherche scientifique.")
    )
  }
  const scientificLock = cleanDisplayText(sourceJson.scientific_lock || "")
  const evidenceSummary = cleanDisplayText(sourceJson.evidence_summary || "")

  if (scientificLock || evidenceSummary) {
    return [
      scientificLock ? `Confirmer l’incertitude technique suivante : ${scientificLock}` : "",
      evidenceSummary ? `Vérifier les preuves sources associées : ${evidenceSummary}` : "",
      "Contrôler si les essais, calculs, simulations ou résultats disponibles suffisent pour justifier ce verrou CIR avant EnnoScholar.",
    ]
      .filter(Boolean)
      .join(" ")
  }

  return "Vérifier que les preuves montrent une difficulté technique réelle, non résolue directement par les solutions connues, et que les essais ou analyses apportent une réponse expérimentale."
}


function consultantAction(verrou: VerrouRead) {
  const status = verrou.consultant_status

  if (status === "garde") {
    return "Piste retenue : elle sera transmise à EnnoScholar pour recherche scientifique et consolidation de l’état de l’art."
  }

  if (status === "reformuler") {
    return "Piste à consolider : préciser le titre du verrou et vérifier les preuves documentaires avant recherche scientifique."
  }

  if (status === "rejete") {
    return "Piste non retenue pour l’instant : elle reste traçable mais n’est pas transmise à EnnoScholar."
  }

  return "Piste à examiner : choisir Retenir ou Non retenir après lecture des preuves."
}

function splitConsultantItem(value: string) {
  const cleaned = cleanDisplayText(value)
    .replace(/^[-*•]\s*/, "")
    .replace(/^\d+[.)]\s*/, "")
    .trim()

  const match = cleaned.match(/^(.{3,90}?)(?:\s+—\s+|\s+-\s+)([\s\S]+)$/)
  if (match) {
    return {
      title: match[1].trim(),
      body: match[2].trim(),
    }
  }

  const colon = cleaned.match(/^(.{3,60}?):\s+([\s\S]+)$/)
  if (colon) {
    return {
      title: colon[1].trim(),
      body: colon[2].trim(),
    }
  }

  return {
    title: "",
    body: cleaned,
  }
}

function ConsultantTextCard({
  item,
  tone = "default",
}: {
  item: string
  tone?: "default" | "success" | "warning"
}) {
  const parsed = splitConsultantItem(item)
  const toneClass =
    tone === "success"
      ? "bg-success/5 border-success/20"
      : tone === "warning"
        ? "bg-warning/5 border-warning/20"
        : "bg-white border-border"

  return (
    <div className={`rounded-lg border ${toneClass} p-4 space-y-2`}>
      {parsed.title && (
        <p className="text-sm font-semibold text-foreground">
          {parsed.title}
        </p>
      )}
      <BackendSectionRendererV93 text={parsed.body || parsed.title || "—"} />
    </div>
  )
}


// ===============================
// V93 - Rendu propre des sections backend EnnoDiagnostic
// ===============================
function parseJsonMaybeV93(value: any): any {
  if (!value) return null
  if (typeof value === "object") return value
  if (typeof value === "string") {
    const trimmed = value.trim()
    if (!trimmed) return null
    try {
      return JSON.parse(trimmed)
    } catch {
      return null
    }
  }
  return null
}

function fixFrenchMojibakeV93(value: string): string {
  return String(value || "")
    .replace(/Ã©/g, "é")
    .replace(/Ã¨/g, "è")
    .replace(/Ãª/g, "ê")
    .replace(/Ã«/g, "ë")
    .replace(/Ã /g, "à")
    .replace(/Ã¢/g, "â")
    .replace(/Ã§/g, "ç")
    .replace(/Ã´/g, "ô")
    .replace(/Ã¹/g, "ù")
    .replace(/Ã»/g, "û")
    .replace(/Ã®/g, "î")
    .replace(/Ã¯/g, "ï")
    .replace(/Ã‰/g, "É")
    .replace(/â€™/g, "’")
    .replace(/â€œ/g, "“")
    .replace(/â€/g, "”")
    .replace(/â€“/g, "–")
    .replace(/â€”/g, "—")
}

function normalizeKeyV93(value: string): string {
  return fixFrenchMojibakeV93(String(value || ""))
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
}

function unwrapBackendDiagnosticReportV93(payload: any): any {
  const walk = (value: any, depth = 0): any => {
    if (!value || depth > 8) return null

    const obj = typeof value === "string" ? parseJsonMaybeV93(value) : value
    if (!obj || typeof obj !== "object") return null

    if (
      obj.diagnostic_sections ||
      obj.report_sections ||
      obj.display?.report_sections ||
      obj.diagnostic?.sections ||
      obj.diagnostic?.content ||
      obj.display?.report_markdown ||
      obj.llm_reformulated_verrous ||
      obj.chroma_sections ||
      obj.frascati_summary ||
      obj.frascati_justification ||
      obj.ai_detection_report ||
      obj.raw_result?.evidence_pack_before_frascati ||
      obj.raw_result?.evidence_pack_for_ennodiagnostic ||
      obj.merged_evidence_pack_for_ennodiagnostic ||
      obj.multi_document_evidence_pack_for_ennodiagnostic
    ) {
      return obj
    }

    const candidates = [
      obj.report,
      obj.diagnostic,
      obj.display,
      obj.data,
      obj.result,
      obj.latest,
      obj.latest_run,
      obj.latestRun,
      obj.diagnostic_run,
      obj.run,
      obj.item,
      obj.bundle,
      obj.payload,
    ]

    for (const candidate of candidates) {
      const found = walk(candidate, depth + 1)
      if (found) return found
    }

    for (const key of [
      "result_json",
      "report_json",
      "raw_json",
      "output_json",
      "content_json",
      "data_json",
    ]) {
      const found = walk(obj[key], depth + 1)
      if (found) return found
    }

    return null
  }

  return walk(payload) || {}
}

function getBackendDiagnosticSectionsV93(payload: any, display: any): Record<string, string> {
  const report = unwrapBackendDiagnosticReportV93(payload)

  const candidates = [
    report?.diagnostic_sections,
    report?.report_sections,
    report?.diagnostic?.sections,
    report?.display?.report_sections,
    display?.report_sections,
  ]

  for (const candidate of candidates) {
    if (candidate && typeof candidate === "object") {
      return candidate as Record<string, string>
    }
  }

  return {}
}

function getBackendDiagnosticMarkdownV93(payload: any, display: any): string {
  const report = unwrapBackendDiagnosticReportV93(payload)

  return fixFrenchMojibakeV93(String(
    report?.diagnostic?.content ||
    report?.diagnostic_content ||
    report?.content ||
    report?.markdown ||
    report?.display?.report_markdown ||
    display?.report_markdown ||
    ""
  )).trim()
}

function extractSectionFromMarkdownV93(markdown: string, titles: string[]): string {
  const content = fixFrenchMojibakeV93(markdown || "").replace(/\r\n/g, "\n").trim()
  if (!content) return ""

  const lines = content.split("\n")
  const wanted = titles.map(normalizeKeyV93)
  let start = -1

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i].trim()
    const match = line.match(/^#{1,6}\s+(.+?)\s*$/)
    if (!match) continue

    const heading = normalizeKeyV93(match[1])
    if (wanted.some((item) => heading === item || heading.includes(item) || item.includes(heading))) {
      start = i + 1
      break
    }
  }

  if (start < 0) return ""

  const out: string[] = []
  for (let i = start; i < lines.length; i += 1) {
    if (/^#{1,6}\s+/.test(lines[i].trim())) break
    out.push(lines[i])
  }

  return fixFrenchMojibakeV93(out.join("\n").trim())
}

function pickBackendSectionV93(
  sections: Record<string, string>,
  markdown: string,
  titles: string[],
  fallback = ""
): string {
  for (const title of titles) {
    const direct = sections?.[title]
    if (typeof direct === "string" && direct.trim()) {
      return fixFrenchMojibakeV93(direct).trim()
    }
  }

  const entries = Object.entries(sections || {})
  for (const title of titles) {
    const normalizedTitle = normalizeKeyV93(title)
    const found = entries.find(([key, value]) => {
      const normalizedKey = normalizeKeyV93(key)
      return (
        typeof value === "string" &&
        value.trim() &&
        (normalizedKey === normalizedTitle ||
          normalizedKey.includes(normalizedTitle) ||
          normalizedTitle.includes(normalizedKey))
      )
    })

    if (found) return fixFrenchMojibakeV93(String(found[1])).trim()
  }

  const fromMarkdown = extractSectionFromMarkdownV93(markdown, titles)
  if (fromMarkdown) return fromMarkdown

  return fixFrenchMojibakeV93(fallback || "").trim()
}



// ===============================
// V107 - Verrous JSON directs, sans codage dur projet
// Objectif : afficher les verrous réellement présents dans les JSON NLP/RAG/agent
// au lieu de se limiter aux catégories Frascati génériques synchronisées en base.
// ===============================
type JsonVerrouCandidateV107 = {
  item: any
  sourceKey: string
  path: string
}

function isObjectV107(value: any) {
  return Boolean(value && typeof value === "object" && !Array.isArray(value))
}

function normalizePathKeyV107(value: string) {
  return normalizeKeyV93(value).replace(/\s+/g, "_")
}

function collectNamedArraysV107(
  root: any,
  wantedKeys: string[],
  path = "root",
  depth = 0,
  visited = new WeakSet<object>()
): JsonVerrouCandidateV107[] {
  if (!root || depth > 9) return []

  const parsed = typeof root === "string" ? parseJsonMaybeV93(root) : root
  if (!parsed || typeof parsed !== "object") return []

  if (visited.has(parsed)) return []
  visited.add(parsed)

  const wanted = new Set(wantedKeys.map(normalizePathKeyV107))
  const found: JsonVerrouCandidateV107[] = []

  if (Array.isArray(parsed)) {
    parsed.forEach((entry, index) => {
      found.push(
        ...collectNamedArraysV107(entry, wantedKeys, `${path}[${index}]`, depth + 1, visited)
      )
    })
    return found
  }

  Object.entries(parsed).forEach(([key, value]) => {
    const normalizedKey = normalizePathKeyV107(key)

    if (wanted.has(normalizedKey) && Array.isArray(value)) {
      value.forEach((item, index) => {
        if (item !== null && item !== undefined) {
          found.push({
            item,
            sourceKey: key,
            path: `${path}.${key}[${index}]`,
          })
        }
      })
    }

    if (value && typeof value === "object") {
      found.push(
        ...collectNamedArraysV107(value, wantedKeys, `${path}.${key}`, depth + 1, visited)
      )
    }
  })

  return found
}

function itemTextV107(item: any) {
  const sourceJson = isObjectV107(item?.source_json) ? item.source_json : {}

  return cleanDisplayText(
    item?.text ||
      item?.source_text ||
      item?.description ||
      item?.justification ||
      item?.scientific_lock ||
      item?.why_not_simple_engineering ||
      item?.evidence_summary ||
      item?.llm_block ||
      sourceJson?.text ||
      sourceJson?.source_text ||
      sourceJson?.description ||
      sourceJson?.justification ||
      sourceJson?.scientific_lock ||
      sourceJson?.why_not_simple_engineering ||
      sourceJson?.evidence_summary ||
      sourceJson?.llm_block ||
      ""
  )
}

function itemTitleV107(item: any) {
  const sourceJson = isObjectV107(item?.source_json) ? item.source_json : {}

  return cleanDisplayText(
    item?.title ||
      item?.titre ||
      item?.llm_title ||
      item?.verrou_title ||
      item?.scientific_title ||
      item?.consolidated_title ||
      item?.theme_label ||
      item?.label ||
      sourceJson?.title ||
      sourceJson?.titre ||
      sourceJson?.llm_title ||
      sourceJson?.verrou_title ||
      sourceJson?.scientific_title ||
      sourceJson?.consolidated_title ||
      sourceJson?.theme_label ||
      sourceJson?.label ||
      ""
  )
}

function isTableHeaderLikeV107(value: string) {
  const key = normalizeKeyV93(value)
  if (!key) return true

  const weakSchemaWords = new Set([
    "id verrou",
    "id",
    "verrou",
    "description",
    "impact r d",
    "impact rd",
    "question de qualification",
    "documents concernes",
  ])

  return weakSchemaWords.has(key) || key.length < 6
}

function extractSpecificTitleFromVerrouTextV107(text: string) {
  const clean = cleanDisplayText(text || "")
  if (!clean) return ""

  const tableMatch = clean.match(/\bV\d+\s*\|\s*([^|:\n.]{4,180})(?:\s*:\s*([^|\n.]{0,220}))?/i)
  if (tableMatch) {
    const part1 = cleanDisplayText(tableMatch[1] || "")
    const part2 = cleanDisplayText(tableMatch[2] || "")
    const title = part2 && part2.length > 4 ? `${part1} : ${part2}` : part1
    if (!isTableHeaderLikeV107(title)) return title.slice(0, 180).trim()
  }

  const explicitMatch = clean.match(/\bVerrou\s*(?:R&D|scientifique|technique)?\s*\d*\s*[:\-–—]\s*([^\n.]{8,220})/i)
  if (explicitMatch) {
    const title = cleanDisplayText(explicitMatch[1] || "")
    if (!isTableHeaderLikeV107(title)) return title.slice(0, 180).trim()
  }

  const firstUsefulLine = clean
    .split(/[\n.!?]+/)
    .map((line) => cleanDisplayText(line))
    .find((line) => line.length >= 12 && !isTableHeaderLikeV107(line))

  return firstUsefulLine ? firstUsefulLine.slice(0, 180).trim() : ""
}

function isUniversalReconstructionV107(candidate: JsonVerrouCandidateV107) {
  const item = candidate.item || {}
  const sourceJson = isObjectV107(item?.source_json) ? item.source_json : item
  const metadata = isObjectV107(sourceJson?.metadata) ? sourceJson.metadata : {}

  const joined = [
    candidate.sourceKey,
    candidate.path,
    item?.passage_id,
    item?.parent_passage_id,
    item?.verrou_source,
    sourceJson?.verrou_source,
    metadata?.verrou_source,
    item?.source,
    sourceJson?.source,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase()

  const text = itemTextV107(item).toLowerCase()

  return (
    joined.includes("universal") ||
    joined.includes("verrou_implicit") ||
    text.startsWith("verrou implicite possible")
  )
}


function isFallbackSynthesizedVerrouV124(candidate: JsonVerrouCandidateV107, item: any, title = "", text = "") {
  const sourceJson = isObjectV107(item?.source_json) ? item.source_json : item

  const joined = cleanDisplayText([
    candidate.sourceKey,
    candidate.path,
    title,
    text,
    item?.justification,
    item?.source,
    sourceJson?.source,
    sourceJson?.llm_block,
  ].filter(Boolean).join(" ")).toLowerCase()

  return (
    joined.includes("fallback_grouped_rag_verrou_synthesis") ||
    joined.includes("signal technique candidat extrait des preuves rag/nlp") ||
    joined.includes("la reformulation llm dédiée n'a pas produit de json exploitable") ||
    joined.includes("la reformulation llm dediee n'a pas produit de json exploitable")
  )
}

function normalizeVerrouScoreV124(value: any): number | null {
  if (value === null || value === undefined || value === "") return null

  const n = Number(value)
  if (!Number.isFinite(n) || n < 0) return null

  // Cas normal : score déjà entre 0 et 1.
  if (n <= 1) return n

  // Cas V122 : score agrégé des preuves sur une échelle proche de 0..2.
  // Ex. 1.86 doit être affiché comme 93%, pas 2%.
  if (n <= 2.5) return n / 2

  // Cas score déjà stocké en pourcentage.
  if (n <= 100) return n

  return null
}

function getNormalizedVerrouScoreV124(item: any, sourceJson: any): number | null {
  const candidates = [
    item?.verrou_score,
    item?.score,
    item?.frascati_score,
    item?.confidence,
    sourceJson?.verrou_score,
    sourceJson?.score,
    sourceJson?.frascati_score,
    sourceJson?.confidence,
  ]

  for (const candidate of candidates) {
    const normalized = normalizeVerrouScoreV124(candidate)
    if (normalized !== null) return normalized
  }

  return null
}

function hasVerrouShapeV107(candidate: JsonVerrouCandidateV107) {
  const item = candidate.item
  if (typeof item === "string") return cleanDisplayText(item).length > 8
  if (!isObjectV107(item)) return false

  const sourceKey = normalizeKeyV93(candidate.sourceKey)
  const role = normalizeKeyV93(String(item?.role || item?.source_json?.role || ""))
  const title = itemTitleV107(item)
  const text = itemTextV107(item)

  return (
    sourceKey.includes("verrou") ||
    role === "verrou" ||
    Boolean(title) ||
    /\bV\d+\s*\|/.test(text) ||
    /\bverrou\b/i.test(text)
  )
}

function candidatePriorityV107(candidate: JsonVerrouCandidateV107, title: string) {
  const item = candidate.item || {}
  const sourceJson = isObjectV107(item?.source_json) ? item.source_json : {}
  const sourceKey = normalizeKeyV93(candidate.sourceKey)
  const path = normalizeKeyV93(candidate.path)
  const text = itemTextV107(item)
  const universal = isUniversalReconstructionV107(candidate)
  let priority = 0

  // V123 : priorité absolue aux verrous reformulés par EnnoDiagnostic V122.
  if (sourceKey.includes("llm reformulated")) priority += 200
  if (sourceKey.includes("consultant verrous cir")) priority += 195
  if (path.includes("llm reformulated verrous")) priority += 190
  if (path.includes("consultant verrous cir")) priority += 185

  // Fallbacks seulement si aucune reformulation LLM n'existe.
  if (sourceKey.includes("verrous rnd locaux")) priority += 80
  if (path.includes("evidence pack before frascati")) priority += 70
  if (path.includes("evidence pack for ennodiagnostic")) priority += 70
  if (path.includes("chroma sections")) priority += 65
  if (String(item?.verrou_source || sourceJson?.verrou_source || "").includes("direct")) priority += 50
  if (/\bV\d+\s*\|/.test(text)) priority += 35
  if (item?.document || sourceJson?.document || sourceJson?.source_document) priority += 15
  if (title && !isTableHeaderLikeV107(title)) priority += 15
  if (universal) priority -= 120

  return priority
}

function stableNegativeIdV107(value: string, index: number) {
  const key = `${value || "verrou"}-${index}`
  let hash = 0
  for (let i = 0; i < key.length; i += 1) {
    hash = ((hash << 5) - hash + key.charCodeAt(i)) | 0
  }
  return -Math.abs(hash || index + 1)
}

function toJsonVerrouReadV107(candidate: JsonVerrouCandidateV107, index: number): (VerrouRead & { _json_priority?: number }) | null {
  const rawItem = candidate.item
  const item = typeof rawItem === "string" ? { text: rawItem } : rawItem
  if (!isObjectV107(item)) return null

  const text = itemTextV107(item)
  const structuredTitle = itemTitleV107(item)
  const extractedTitle = extractSpecificTitleFromVerrouTextV107(text)
  const title = cleanDisplayText(structuredTitle || extractedTitle)

  if (!title || isTableHeaderLikeV107(title)) return null

  const sourceJson = isObjectV107(item?.source_json) ? item.source_json : item

  // V124 : ne pas afficher les pseudo-verrous fallback produits quand le LLM n'a pas réussi
  // à générer un JSON exploitable. Ces éléments restent des preuves/chunks, pas des verrous CIR.
  if (isFallbackSynthesizedVerrouV124(candidate, item, title, text)) return null

  const scoreValue = getNormalizedVerrouScoreV124(item, sourceJson)

  const sourceDocument =
    item?.document ||
    item?.source_document ||
    item?.filename ||
    sourceJson?.document ||
    sourceJson?.source_document ||
    sourceJson?.filename ||
    ""

  const rawSources =
    item?.sources ||
    item?.evidence_sources ||
    item?.source_documents ||
    item?.source_ids ||
    sourceJson?.sources ||
    sourceJson?.evidence_sources ||
    sourceJson?.source_documents ||
    sourceJson?.source_ids

  const normalizedSources = Array.isArray(rawSources)
    ? rawSources
    : sourceDocument
      ? [{ document: sourceDocument }]
      : []

  const justification = cleanDisplayText(
    item?.justification ||
      item?.scientific_lock ||
      item?.why_not_simple_engineering ||
      item?.evidence_summary ||
      sourceJson?.justification ||
      sourceJson?.scientific_lock ||
      sourceJson?.why_not_simple_engineering ||
      sourceJson?.evidence_summary ||
      text ||
      "Verrou reformulé par EnnoDiagnostic à partir des preuves RAG/NLP."
  )

  const priority = candidatePriorityV107(candidate, title)

  return {
    id: Number(item?.id || item?.verrou_id || 0) || stableNegativeIdV107(title, index),
    diagnostic_run_id: Number(item?.diagnostic_run_id || 0),
    title,
    tag_cir: item?.tag_cir || item?.decision || item?.status || "À valider",
    score: scoreValue,
    consultant_status: item?.consultant_status || "en_attente",
    justification,
    source_json: {
      ...sourceJson,
      sources: normalizedSources.length > 0 ? normalizedSources : sourceJson?.sources,
      text: text || sourceJson?.text || justification,
      evidence_summary: item?.evidence_summary || sourceJson?.evidence_summary,
      scientific_lock: item?.scientific_lock || sourceJson?.scientific_lock,
      why_not_simple_engineering: item?.why_not_simple_engineering || sourceJson?.why_not_simple_engineering,
      source_ids: item?.source_ids || sourceJson?.source_ids,
      frontend_json_only: Number(item?.id || item?.verrou_id || 0) ? false : true,
      frontend_source_key: candidate.sourceKey,
      frontend_source_path: candidate.path,
      frontend_universal_reconstruction: isUniversalReconstructionV107(candidate),
    },
    created_at: item?.created_at || "",
    _json_priority: priority,
  }
}

function uniqueVerrousForDisplayV107(items: Array<VerrouRead & { _json_priority?: number }>, limit = 8) {
  const seen = new Set<string>()

  return items
    .sort((a, b) => Number(b._json_priority || 0) - Number(a._json_priority || 0))
    .filter((item) => {
      const key = normalizeKeyV93(item.title)
      if (!key || seen.has(key)) return false
      seen.add(key)
      return true
    })
    .slice(0, limit)
}

function collectJsonVerrousV107(...roots: any[]) {
  // V123 : priorité stricte aux verrous reformulés par EnnoDiagnostic V122.
  // Les chunks RAG/NLP bruts ne sont lus qu'en fallback si aucune reformulation n'existe.
  const primaryWantedKeys = [
    "llm_reformulated_verrous",
    "consultant_verrous_cir",
  ]

  const primaryCandidates = roots.flatMap((root, rootIndex) =>
    collectNamedArraysV107(root, primaryWantedKeys, `primary${rootIndex}`)
  )

  const primaryNormalized = primaryCandidates
    .filter(hasVerrouShapeV107)
    .map(toJsonVerrouReadV107)
    .filter(Boolean) as Array<VerrouRead & { _json_priority?: number }>

  if (primaryNormalized.length > 0) {
    return uniqueVerrousForDisplayV107(primaryNormalized, 8)
  }

  const fallbackWantedKeys = [
    "verrous_rnd_locaux",
    "verrous",
    "chroma_verrous",
  ]

  const fallbackCandidates = roots.flatMap((root, rootIndex) =>
    collectNamedArraysV107(root, fallbackWantedKeys, `fallback${rootIndex}`)
  )

  const fallbackNormalized = fallbackCandidates
    .filter(hasVerrouShapeV107)
    .map(toJsonVerrouReadV107)
    .filter(Boolean) as Array<VerrouRead & { _json_priority?: number }>

  if (fallbackNormalized.length === 0) return []

  return uniqueVerrousForDisplayV107(fallbackNormalized, 8)
}

function verrousToMarkdownV107(verrous: VerrouRead[]) {
  if (!verrous.length) return ""

  return verrous
    .map((verrou, index) => {
      const sources = getSources(verrou)
      const sourceLabel = sources.length ? `\nSource : ${sources.slice(0, 2).join(" ; ")}` : ""
      const explanation = cleanDisplayText(getConsultantContextExplanation(verrou))
      const justification = cleanDisplayText(verrou.justification || getSourceText(verrou))
      const preferredText = explanation || justification
      const shortJustification = preferredText.length > 500 ? `${preferredText.slice(0, 500).trim()}…` : preferredText
      const reasonLabel = explanation ? "Pourquoi EnnoDiagnostic le détecte comme verrou :" : ""

      return `${index + 1}. ${verrou.title}${sourceLabel}${shortJustification ? `\n${reasonLabel ? `${reasonLabel} ` : ""}${shortJustification}` : ""}`
    })
    .join("\n\n")
}

function isJsonOnlyVerrouV107(verrou: VerrouRead) {
  return Number(verrou.id) < 0 || Boolean(verrou.source_json?.frontend_json_only)
}

function isFallbackVerrouReadV124(verrou: VerrouRead) {
  const sourceJson = verrou.source_json || {}
  const joined = cleanDisplayText([
    verrou.title,
    verrou.justification,
    sourceJson?.text,
    sourceJson?.source,
    sourceJson?.llm_block,
    sourceJson?.frontend_source_key,
    sourceJson?.frontend_source_path,
  ].filter(Boolean).join(" ")).toLowerCase()

  return (
    joined.includes("fallback_grouped_rag_verrou_synthesis") ||
    joined.includes("signal technique candidat extrait des preuves rag/nlp") ||
    joined.includes("la reformulation llm dédiée n'a pas produit de json exploitable") ||
    joined.includes("la reformulation llm dediee n'a pas produit de json exploitable")
  )
}


// ===============================
// V139 - Frontend simple : le backend est la source unique.
// Le frontend ne parcourt plus tout le JSON diagnostic pour reconstruire les verrous.
// Il lit seulement display.validation_verrous / validation_verrous renvoyés par
// /diagnostic/latest.
// ===============================
function normalizeBackendDisplayVerrouV139(item: any, index: number): VerrouRead | null {
  if (!item || typeof item !== "object") return null

  const sourceJson = isObjectV107(item?.source_json) ? item.source_json : {}
  const title = cleanDisplayText(
    item?.title ||
      item?.titre ||
      item?.verrou ||
      item?.name ||
      sourceJson?.title ||
      sourceJson?.titre ||
      ""
  )

  if (!title || isTableHeaderLikeV107(title)) return null

  const idValue = item?.id ?? item?.verrou_id ?? item?.db_id
  const idNumber = Number(idValue)
  const hasDbId = Number.isFinite(idNumber) && idNumber > 0

  const justification = cleanDisplayText(
    item?.justification ||
      item?.description ||
      item?.text ||
      item?.consultant_explanation ||
      item?.why_agent_found_verrou ||
      item?.why_not_simple_engineering ||
      item?.scientific_lock ||
      sourceJson?.justification ||
      sourceJson?.description ||
      sourceJson?.text ||
      sourceJson?.consultant_explanation ||
      sourceJson?.why_not_simple_engineering ||
      sourceJson?.evidence_summary ||
      ""
  )

  return {
    ...(item || {}),
    id: hasDbId ? idNumber : stableNegativeIdV107(title, index),
    diagnostic_run_id: Number(item?.diagnostic_run_id || 0),
    title,
    tag_cir: item?.tag_cir || item?.decision || item?.status || "À valider",
    score: item?.score ?? item?.frascati_score ?? item?.confidence ?? sourceJson?.score ?? null,
    consultant_status: item?.consultant_status || "en_attente",
    justification,
    source_json: {
      ...sourceJson,
      sources:
        item?.sources ||
        item?.evidence_sources ||
        item?.source_documents ||
        sourceJson?.sources ||
        sourceJson?.evidence_sources ||
        sourceJson?.source_documents,
      text: item?.text || sourceJson?.text || justification,
      evidence_summary: item?.evidence_summary || sourceJson?.evidence_summary,
      scientific_lock: item?.scientific_lock || sourceJson?.scientific_lock,
      why_not_simple_engineering: item?.why_not_simple_engineering || sourceJson?.why_not_simple_engineering,
      consultant_explanation: item?.consultant_explanation || sourceJson?.consultant_explanation || justification,
      frontend_source_key: "backend_display_v139",
      frontend_source_path: "display.validation_verrous",
      frontend_json_only: !hasDbId,
    },
    created_at: item?.created_at || "",
  } as VerrouRead
}

function verrouIdentityKeyV144(item: any): string {
  const id = Number(item?.id ?? item?.verrou_id ?? item?.db_id)
  if (Number.isFinite(id) && id > 0) return `id:${id}`

  const title = normalizeKeyV93(String(item?.title || item?.titre || item?.verrou || ""))
  return title ? `title:${title}` : ""
}

function mergeVerrouForLiveStatusV144(displayItem: VerrouRead, dbItem?: VerrouRead): VerrouRead {
  if (!dbItem) return displayItem

  return {
    ...displayItem,
    ...dbItem,
    id: Number(dbItem.id || displayItem.id),
    title: dbItem.title || displayItem.title,
    tag_cir: dbItem.tag_cir || displayItem.tag_cir,
    score: dbItem.score ?? displayItem.score,
    consultant_status:
      dbItem.consultant_status ||
      displayItem.consultant_status ||
      "en_attente",
    justification:
      displayItem.justification ||
      dbItem.justification ||
      "",
    source_json: {
      ...(displayItem.source_json || {}),
      ...(dbItem.source_json || {}),
      frontend_json_only: false,
    },
  }
}

function patchVerrouArrayV144(
  value: any,
  verrouId: number,
  decision: ConsultantDecision,
  updated?: VerrouRead
): any {
  if (!Array.isArray(value)) return value

  return value.map((item: any) => {
    const itemId = Number(item?.id ?? item?.verrou_id ?? item?.db_id)
    if (!Number.isFinite(itemId) || itemId !== Number(verrouId)) return item

    return {
      ...item,
      ...(updated || {}),
      id: Number(updated?.id ?? itemId),
      consultant_status:
        updated?.consultant_status ||
        decision,
      source_json: {
        ...(item?.source_json || {}),
        ...(updated?.source_json || {}),
        frontend_json_only: false,
      },
      is_db_synced: true,
      can_decide: true,
    }
  })
}

function collectBackendDisplayVerrousV139(
  diagnosticBundle: any,
  display: any,
  dbVerrous: VerrouRead[]
): VerrouRead[] {
  const backendItems = firstNonEmptyArray(
    display?.validation_verrous,
    display?.validation_verrous_preview,
    diagnosticBundle?.validation_verrous,
    diagnosticBundle?.display?.validation_verrous,
    display?.consultant_verrous_cir,
    display?.llm_reformulated_verrous
  )

  const normalizedBackend = backendItems
    .map((item: any, index: number) => normalizeBackendDisplayVerrouV139(item, index))
    .filter((verrou): verrou is VerrouRead => Boolean(verrou))
    .filter((verrou) => !isFallbackVerrouReadV124(verrou))

  const cleanDbVerrous = (dbVerrous || [])
    .filter((verrou) => !isFallbackVerrouReadV124(verrou))

  if (normalizedBackend.length === 0) return cleanDbVerrous
  if (cleanDbVerrous.length === 0) return normalizedBackend

  const dbByIdentity = new Map<string, VerrouRead>()
  const dbByTitle = new Map<string, VerrouRead>()

  cleanDbVerrous.forEach((verrou) => {
    const identity = verrouIdentityKeyV144(verrou)
    const titleKey = normalizeKeyV93(verrou.title || "")

    if (identity) dbByIdentity.set(identity, verrou)
    if (titleKey) dbByTitle.set(titleKey, verrou)
  })

  const merged = normalizedBackend.map((verrou) => {
    const identity = verrouIdentityKeyV144(verrou)
    const titleKey = normalizeKeyV93(verrou.title || "")
    const dbMatch =
      (identity ? dbByIdentity.get(identity) : undefined) ||
      (titleKey ? dbByTitle.get(titleKey) : undefined)

    return mergeVerrouForLiveStatusV144(verrou, dbMatch)
  })

  const mergedKeys = new Set(
    merged
      .map(verrouIdentityKeyV144)
      .filter(Boolean)
  )

  cleanDbVerrous.forEach((verrou) => {
    const identity = verrouIdentityKeyV144(verrou)
    if (identity && !mergedKeys.has(identity)) {
      merged.push(verrou)
      mergedKeys.add(identity)
    }
  })

  return merged
}

function getBackendFrascatiJustificationV94(
  payload: any,
  sections: Record<string, string>,
  markdown: string
): string {
  const report = unwrapBackendDiagnosticReportV93(payload)

  const fromSection = pickBackendSectionV93(sections, markdown, [
    "Justification Frascati du score",
    "Justification du score Frascati",
    "Justification Frascati",
  ])

  if (fromSection) return fromSection

  const structured =
    report?.frascati_justification?.text ||
    report?.diagnostic?.frascati_justification?.text ||
    report?.display?.frascati_justification?.text ||
    report?.frascati_justification?.pourquoi_ce_score ||
    ""

  if (typeof structured === "string" && structured.trim()) {
    return fixFrenchMojibakeV93(structured).trim()
  }

  return ""
}

function getEligibilityEvidenceReportV153(payload: any, display: any): any {
  const report = unwrapBackendDiagnosticReportV93(payload)
  return (
    display?.frascati_summary?.eligibility_evidence_report ||
    report?.display?.frascati_summary?.eligibility_evidence_report ||
    report?.frascati_summary?.eligibility_evidence_report ||
    report?.static_diagnostic?.section_payloads_by_key?.lecture_frascati?.eligibility_evidence_report ||
    {}
  )
}

function getEligibilityProofClaimsV153(payload: any, display: any): any[] {
  const report = unwrapBackendDiagnosticReportV93(payload)
  const cards =
    display?.diagnostic_cards ||
    report?.display?.diagnostic_cards ||
    report?.diagnostic_cards ||
    report?.static_diagnostic?.cards ||
    []
  if (!Array.isArray(cards)) return []
  const card = cards.find((item: any) =>
    String(item?.key || "") === "lecture_frascati" ||
    normalizeKeyV93(String(item?.title || "")) === "etude d eligibilite"
  )
  if (!card || !Array.isArray(card?.paragraphs)) return []
  const evidenceById = new Map<string, any>()
  for (const evidence of Array.isArray(card?.evidence) ? card.evidence : []) {
    if (evidence?.evidence_id) evidenceById.set(String(evidence.evidence_id), evidence)
  }
  const resolveProofs = (claim: any) => Array.isArray(claim?.proofs) && claim.proofs.length > 0
    ? claim.proofs
    : (Array.isArray(claim?.evidence_ids) ? claim.evidence_ids : [])
        .map((id: any) => evidenceById.get(String(id)))
        .filter(Boolean)
  return card.paragraphs
    .filter((claim: any) => cleanDisplayText(String(claim?.text || "")) && Array.isArray(claim?.evidence_ids) && claim.evidence_ids.length > 0)
    .map((claim: any) => ({
      text: cleanDisplayText(String(claim.text || "")),
      proofs: resolveProofs(claim),
      claims: (Array.isArray(claim?.claims) ? claim.claims : [])
        .filter((part: any) => cleanDisplayText(String(part?.text || "")))
        .map((part: any) => ({
          claim_kind: cleanDisplayText(String(part?.claim_kind || "")),
          text: cleanDisplayText(String(part?.text || "")),
          proofs: resolveProofs(part),
        })),
    }))
}

function stripMarkdownSymbolsV93(value: string) {
  return fixFrenchMojibakeV93(String(value || ""))
    .replace(/^#{1,6}\s+/, "")
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/\*(.*?)\*/g, "$1")
    .replace(/\u00a0/g, " ")
    .trim()
}

function InlineMarkdownV93({ text }: { text: string }) {
  const value = fixFrenchMojibakeV93(String(text || ""))
  const parts = value.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g).filter(Boolean)

  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return (
            <strong key={index} className="font-semibold text-foreground">
              {part.slice(2, -2)}
            </strong>
          )
        }

        if (part.startsWith("*") && part.endsWith("*")) {
          return (
            <span key={index} className="font-semibold text-foreground">
              {part.slice(1, -1)}
            </span>
          )
        }

        return <span key={index}>{part}</span>
      })}
    </>
  )
}

function isMarkdownTableSeparatorV93(line: string) {
  return /^\|\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(String(line || "").trim())
}

function splitMarkdownTableRowV93(line: string) {
  return String(line || "")
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => stripMarkdownSymbolsV93(cell))
}

function MarkdownTableV93({
  lines,
  projectId,
  sourceDocuments = [],
  enableSourceDocs = false,
}: {
  lines: string[]
  projectId?: number | string
  sourceDocuments?: DbSourceDocument[]
  enableSourceDocs?: boolean
}) {
  const tableLines = lines.filter((line) => line.trim().startsWith("|"))
  const header = splitMarkdownTableRowV93(tableLines[0] || "")
  const rows = tableLines
    .slice(2)
    .map(splitMarkdownTableRowV93)
    .filter((row) => row.some((cell) => cell.trim()))

  return (
    <div className="max-w-full overflow-x-auto rounded-xl border bg-white">
      <table className="w-full min-w-[720px] divide-y divide-border text-sm">
        <thead className="bg-muted/60">
          <tr>
            {header.map((cell, index) => (
              <th
                key={index}
                className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground"
              >
                {cell || "—"}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="hover:bg-muted/30">
              {header.map((headerCell, cellIndex) => {
                const cellText = row[cellIndex] || "—"
                const isSourceCell =
                  enableSourceDocs &&
                  /source/i.test(String(headerCell || "")) &&
                  projectId

                return (
                  <td
                    key={cellIndex}
                    className="px-4 py-3 align-top text-sm leading-7 text-foreground"
                  >
                    {isSourceCell ? (
                      <SourceTextWithDocuments
                        projectId={projectId}
                        text={cellText}
                        documents={sourceDocuments}
                        compact
                        hideTextWhenMatched
                      />
                    ) : (
                      <InlineMarkdownV93 text={cellText} />
                    )}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function BackendSectionRendererV93({
  text,
  projectId,
  sourceDocuments = [],
  enableSourceDocs = false,
}: {
  text: string
  projectId?: number | string
  sourceDocuments?: DbSourceDocument[]
  enableSourceDocs?: boolean
}) {
  const raw = fixFrenchMojibakeV93(String(text || "").replace(/\r\n/g, "\n")).trim()

  if (!raw) {
    return <p className="text-sm text-muted-foreground">Aucun contenu disponible.</p>
  }

  const lines = raw.split("\n")
  const hasTable = lines.some((line) => line.trim().startsWith("|")) && lines.some(isMarkdownTableSeparatorV93)

  if (hasTable) {
    const blocks: Array<{ type: "table" | "text"; lines: string[] }> = []
    let textBuffer: string[] = []
    let tableBuffer: string[] = []
    let inTable = false

    const flushText = () => {
      if (textBuffer.some((line) => line.trim())) {
        blocks.push({ type: "text", lines: textBuffer })
      }
      textBuffer = []
    }

    const flushTable = () => {
      if (tableBuffer.length) {
        blocks.push({ type: "table", lines: tableBuffer })
      }
      tableBuffer = []
    }

    for (const line of lines) {
      if (line.trim().startsWith("|")) {
        if (!inTable) {
          flushText()
          inTable = true
        }
        tableBuffer.push(line)
      } else {
        if (inTable) {
          flushTable()
          inTable = false
        }
        textBuffer.push(line)
      }
    }

    if (inTable) flushTable()
    flushText()

    return (
      <div className="min-w-0 space-y-4">
        {blocks.map((block, index) =>
          block.type === "table" ? (
            <MarkdownTableV93 key={index} lines={block.lines} projectId={projectId} sourceDocuments={sourceDocuments} enableSourceDocs={enableSourceDocs} />
          ) : (
            <BackendSectionRendererV93 key={index} text={block.lines.join("\n")} projectId={projectId} sourceDocuments={sourceDocuments} enableSourceDocs={enableSourceDocs} />
          )
        )}
      </div>
    )
  }

  const paragraphs = raw
    .split(/\n\s*\n+/)
    .map((item) => item.trim())
    .filter(Boolean)

  return (
    <div className="min-w-0 space-y-3 [overflow-wrap:anywhere]">
      {paragraphs.map((paragraph, index) => {
        const pLines = paragraph
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean)

        if (pLines.length === 1 && /^#{1,6}\s+/.test(pLines[0])) {
          return (
            <h3 key={index} className="text-sm font-semibold text-foreground">
              <InlineMarkdownV93 text={pLines[0].replace(/^#{1,6}\s+/, "")} />
            </h3>
          )
        }

        const bulletLines = pLines.filter((line) => /^[-*•]\s+/.test(line))
        if (bulletLines.length === pLines.length && bulletLines.length > 0) {
          return (
            <ul key={index} className="list-disc space-y-2 pl-5 text-sm leading-7 text-muted-foreground [overflow-wrap:anywhere]">
              {bulletLines.map((line, lineIndex) => (
                <li key={lineIndex}>
                  <InlineMarkdownV93 text={line.replace(/^[-*•]\s+/, "")} />
                </li>
              ))}
            </ul>
          )
        }

        const numberedLines = pLines.filter((line) => /^\d+[.)]\s+/.test(line))
        if (numberedLines.length === pLines.length && numberedLines.length > 0) {
          return (
            <ol key={index} className="list-decimal space-y-2 pl-5 text-sm leading-7 text-muted-foreground [overflow-wrap:anywhere]">
              {numberedLines.map((line, lineIndex) => (
                <li key={lineIndex}>
                  <InlineMarkdownV93 text={line.replace(/^\d+[.)]\s+/, "")} />
                </li>
              ))}
            </ol>
          )
        }

        return (
          <p key={index} className="whitespace-pre-wrap text-sm leading-7 text-muted-foreground [overflow-wrap:anywhere]">
            <InlineMarkdownV93 text={paragraph} />
          </p>
        )
      })}
    </div>
  )
}


// V194_INLINE_CLICKABLE_SOURCES_NO_CARDS


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

function BackendSectionCardV93({
  title,
  description,
  icon,
  text,
  emptyText = "Aucun contenu disponible.",
  tone = "default",
  projectId,
  sourceDocuments = [],
  enableSourceDocs = false,
  structuredSection = null,
  preserveDemarcheAudit = false,
}: {
  title: string
  description?: string
  icon?: any
  text: string
  emptyText?: string
  tone?: "default" | "brand" | "success" | "warning"
  projectId?: number | string
  sourceDocuments?: DbSourceDocument[]
  enableSourceDocs?: boolean
  structuredSection?: any
  preserveDemarcheAudit?: boolean
}) {
  const Icon = icon
  const toneClass =
    tone === "brand"
      ? "border-brand/20 bg-brand/5"
      : tone === "success"
        ? "border-success/20 bg-success/5"
        : tone === "warning"
          ? "border-warning/20 bg-warning/5"
          : ""

  return (
    <Card className={`min-w-0 ${toneClass}`}>
      <CardHeader>
        <CardTitle className="flex min-w-0 items-start gap-2 text-sm">
          {Icon ? <Icon className="mt-0.5 size-4 shrink-0 text-brand" /> : null}
          <span className="min-w-0 [overflow-wrap:anywhere]">{title}</span>
        </CardTitle>
        {description ? (
          <CardDescription className="text-xs">
            {description}
          </CardDescription>
        ) : null}
      </CardHeader>
      <CardContent className="min-w-0">
        <div className="min-w-0 rounded-xl border bg-white/80 p-3 sm:p-4">
          {text?.trim() ? (
            structuredSection && projectId ? (
              <InlineSourcedSectionV194
                text={text}
                structuredSection={structuredSection}
                projectId={projectId}
                sourceDocuments={sourceDocuments}
                preserveDemarcheAudit={preserveDemarcheAudit}
              />
            ) : (
              <BackendSectionRendererV93
                text={text}
                projectId={projectId}
                sourceDocuments={sourceDocuments}
                enableSourceDocs={enableSourceDocs}
              />
            )
          ) : (
            <p className="text-sm text-muted-foreground">{emptyText}</p>
          )}
        </div>
      </CardContent>
    </Card>
  )
}


function EligibilityProofExcerptV153({
  proof,
  projectId,
  sourceDocuments = [],
}: {
  proof: any
  projectId?: number | string
  sourceDocuments?: DbSourceDocument[]
}) {
  const originalSource = cleanDisplayText(String(
    proof?.source_text_original || proof?.excerpt || proof?.text || ""
  ))
  if (!originalSource) return null
  const locator = [
    cleanSourceDocumentName(String(proof?.document || "")),
    proof?.section_title ? `section « ${cleanDisplayText(String(proof.section_title))} »` : "",
    proof?.role ? `rôle ${cleanDisplayText(String(proof.role))}` : "",
    proof?.page_number !== null && proof?.page_number !== undefined ? `page ${proof.page_number}` : "",
    proof?.sentence_start !== null && proof?.sentence_start !== undefined ? `position ${proof.sentence_start}` : "",
  ].filter(Boolean).join(" · ")
  const isCalculation = String(proof?.proof_type || "") === "calculation_rule" || String(proof?.evidence_id || "") === "F0"
  const summaryFr = cleanDisplayText(String(
    proof?.summary_fr || "Ce passage apporte une preuve directement rattachée à l’opération évaluée."
  ))
  const evidence: SourceEvidence = {
    evidence_id: proof?.evidence_id,
    rag_chunk_id: proof?.rag_chunk_id,
    passage_id: proof?.passage_id || proof?.evidence_id,
    document_id: proof?.document_id,
    document: proof?.document,
    document_name: proof?.document_name || proof?.document,
    source_path: proof?.source_path,
    page_number: proof?.page_number,
    paragraph_index: proof?.paragraph_index,
    char_start: proof?.char_start ?? proof?.sentence_start,
    char_end: proof?.char_end,
    sentence_start: proof?.sentence_start,
    section_title: proof?.section_title,
    section_path: proof?.section_path,
    role: proof?.role,
    summary_fr: summaryFr,
    source_text_original: originalSource,
    source_field: proof?.source_field,
    source_is_original: proof?.source_is_original,
    highlight_coordinates: proof?.highlight_coordinates,
    excerpt: originalSource,
  }

  return (
    <div className={`rounded-lg border-l-4 p-3 ${isCalculation ? "border-brand bg-brand/5" : "border-warning bg-warning/5"}`}>
      <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {isCalculation ? "Règle de calcul NLP/Frascati" : "Preuve source"}
        {locator ? ` · ${locator}` : ""}
      </p>
      {isCalculation ? (
        <p className="mt-2 text-sm leading-6 text-foreground">{originalSource}</p>
      ) : (
        <>
          <p className="mt-2 text-sm leading-6 text-foreground">{summaryFr}</p>
          {projectId ? (
            <div className="mt-3">
              <SourceTextWithDocuments
                projectId={projectId}
                text={locator ? `Source : ${locator}` : "Source documentaire"}
                documents={sourceDocuments}
                evidence={[evidence]}
                compact
                hideTextWhenMatched
                actionLabel="Voir la preuve dans le document"
              />
            </div>
          ) : locator ? (
            <p className="mt-2 text-xs text-muted-foreground">Source : {locator}</p>
          ) : null}
        </>
      )}
    </div>
  )
}


function isFrenchEligibilityTextV190(value: unknown) {
  const text = cleanDisplayText(String(value || "")).toLocaleLowerCase("fr")
  if (!text) return false
  const frenchSignals = text.match(/\b(?:l’opération|l'operation|incertitude|verrou|hypothèse|expérimentation|résultats?|l’équipe|l'equipe|preuves?|démarche|caractère|consultant|documenté)\b/g)
  return (frenchSignals?.length || 0) >= 2
}

function isStrongProjectEligibilityTextV192(value: unknown) {
  const text = cleanDisplayText(String(value || "")).toLocaleLowerCase("fr")
  if (!isFrenchEligibilityTextV190(text)) return false
  if (/calcul officiel nlp\/frascati|règle\s*:\s*cinq critères|documenté apporte 0[.,]2/i.test(text)) return false
  const projectStages = [
    /incertitude|verrou/,
    /hypothèse|raison d’investigation|raison d'investigation/,
    /expérimentation|protocole|démarche|essais?|comparaison/,
    /résultats?|apprentissage|observation|mesure/,
  ]
  return projectStages.filter((pattern) => pattern.test(text)).length >= 3
}


function FrascatiAnalysisCard({
  score,
  documentaryCoverage,
  signalsCount,
  candidateCount,
  reading,
  justification,
  demarche,
  evidenceReport,
  proofClaims,
  projectId,
  sourceDocuments,
}: {
  score: number | string | null | undefined
  documentaryCoverage: number | string | null | undefined
  signalsCount: number
  candidateCount: number
  reading: string
  justification: string
  demarche: any
  evidenceReport: any
  proofClaims: any[]
  projectId?: number | string
  sourceDocuments: DbSourceDocument[]
}) {
  const demarcheLabels: Record<string, string> = {
    rnd_core_defendable: "Au moins un noyau R&D défendable",
    rnd_core_partial: "Noyau R&D partiel à compléter",
    classical_engineering: "Ingénierie classique sans noyau R&D défendable",
    insufficient_evidence: "Preuves insuffisantes pour qualifier le noyau R&D",
  }
  const demarcheStatus = String(demarche?.project_status || demarche?.operation_status || "insufficient_evidence")
  const demarcheLabel = demarcheLabels[demarcheStatus] || "Démarche à qualifier"
  const isRoutineEngineering = demarcheStatus === "classical_engineering"
  const scoreBasisOperation = evidenceReport?.score_basis_operation || {}
  const referenceOperation = evidenceReport?.reference_operation || scoreBasisOperation
  const criteria = Array.isArray(referenceOperation?.criteria) ? referenceOperation.criteria : []
  const operations = Array.isArray(evidenceReport?.operations) ? evidenceReport.operations : []
  const generalProofClaims = proofClaims.filter((claim: any) =>
    isFrenchEligibilityTextV190(claim?.text) && (
      !Array.isArray(claim?.proofs) || !claim.proofs.some((proof: any) =>
        /^opération\s+\d+\s+—/i.test(String(proof?.role || "").trim())
      )
    )
  )
  const rawDocumentedShare = Number(evidenceReport?.documented_share ?? score ?? 0)
  const documentedShare = Math.max(0, Math.min(1, rawDocumentedShare > 1 ? rawDocumentedShare / 100 : rawDocumentedShare))
  const rawRemainingShare = Number(evidenceReport?.remaining_documentary_gap ?? (1 - documentedShare))
  const remainingShare = Math.max(0, Math.min(1, rawRemainingShare > 1 ? rawRemainingShare / 100 : rawRemainingShare))
  const criterionStatusLabels: Record<string, string> = {
    documented: "Documenté",
    partial: "Partiel",
    missing: "Manquant",
    contradictory: "Contradictoire",
  }
  const chainLabels: Record<string, string> = {
    uncertainty_evidence_ids: "Incertitude ou verrou",
    hypothesis_or_rationale_evidence_ids: "Hypothèse",
    experiment_evidence_ids: "Expérimentation",
    result_or_learning_evidence_ids: "Résultat et apprentissage",
  }

  return (
    <Card className="min-w-0 overflow-hidden border-brand/20 bg-gradient-to-br from-brand/5 via-white to-white">
      <CardHeader className="border-b bg-white/70">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="space-y-1">
            <CardTitle className="flex items-center gap-2 text-base">
              <BrainCircuit className="size-5 text-brand" />
              Étude d'éligibilité
            </CardTitle>
            <CardDescription>
              Frascati mesure la couverture des cinq critères ; l'étude de démarche vérifie séparément si les travaux lèvent une incertitude R&D ou relèvent de l'ingénierie classique.
            </CardDescription>
          </div>
          <Badge variant="outline" className="border-warning/30 bg-warning/10 text-warning">
            Validation humaine requise
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-5 pt-5">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <div className="rounded-xl border bg-white p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Indice de défendabilité R&D
            </p>
            <p className="mt-1 text-2xl font-semibold text-foreground">
              {formatScore(score)}
            </p>
          </div>

          <div className="rounded-xl border bg-white p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Couverture Frascati
            </p>
            <p className="mt-1 text-2xl font-semibold text-foreground">
              {formatScore(documentaryCoverage)}
            </p>
          </div>

          <div className="rounded-xl border bg-white p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Opérations évaluées
            </p>
            <p className="mt-1 text-2xl font-semibold text-foreground">
              {Number(demarche?.operations_count || signalsCount || 0)}
            </p>
          </div>

          <div className="rounded-xl border bg-white p-4">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Verrous candidats
            </p>
            <p className="mt-1 text-2xl font-semibold text-foreground">
              {candidateCount}
            </p>
          </div>
        </div>

        {demarche && Object.keys(demarche).length > 0 ? (
          <div className={`rounded-xl border p-5 ${isRoutineEngineering ? "border-destructive/30 bg-destructive/5" : "border-brand/20 bg-white"}`}>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-brand">
                  Analyse de la pertinence des démarches
                </p>
                <p className="mt-2 text-sm font-semibold text-foreground">{demarcheLabel}</p>
              </div>
              <Badge variant="outline" className={isRoutineEngineering ? "border-destructive/30 text-destructive" : "border-brand/30 text-brand"}>
                {isRoutineEngineering ? "Non éligible potentiel" : "Validation consultant requise"}
              </Badge>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
              <div className="rounded-lg border bg-white p-3">
                <p className="text-[11px] text-muted-foreground">Noyaux R&D défendables</p>
                <p className="mt-1 text-lg font-semibold text-success">{Number(demarche?.rnd_core_defendable_operations_count || 0)}</p>
              </div>
              <div className="rounded-lg border bg-white p-3">
                <p className="text-[11px] text-muted-foreground">Noyaux R&D partiels</p>
                <p className="mt-1 text-lg font-semibold text-warning">{Number(demarche?.rnd_core_partial_operations_count || 0)}</p>
              </div>
              <div className="rounded-lg border bg-white p-3">
                <p className="text-[11px] text-muted-foreground">Opérations classiques</p>
                <p className="mt-1 text-lg font-semibold text-warning">{Number(demarche?.classical_engineering_operations_count || 0)}</p>
              </div>
              <div className="rounded-lg border bg-white p-3">
                <p className="text-[11px] text-muted-foreground">Opérations à documenter</p>
                <p className="mt-1 text-lg font-semibold">{Number(demarche?.insufficient_evidence_operations_count || 0)}</p>
              </div>
            </div>

            <div className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
              <div className="rounded-lg border bg-white p-3">
                <p className="text-[11px] text-muted-foreground">Activités R&D directes</p>
                <p className="mt-1 text-lg font-semibold text-success">{Number(demarche?.direct_rnd_activities_count || 0)}</p>
              </div>
              <div className="rounded-lg border bg-white p-3">
                <p className="text-[11px] text-muted-foreground">Supports R&D nécessaires</p>
                <p className="mt-1 text-lg font-semibold text-brand">{Number(demarche?.necessary_rnd_support_activities_count || 0)}</p>
              </div>
              <div className="rounded-lg border bg-white p-3">
                <p className="text-[11px] text-muted-foreground">Activités classiques</p>
                <p className="mt-1 text-lg font-semibold text-warning">{Number(demarche?.classical_engineering_activities_count || 0)}</p>
              </div>
              <div className="rounded-lg border bg-white p-3">
                <p className="text-[11px] text-muted-foreground">Activités à rattacher</p>
                <p className="mt-1 text-lg font-semibold">{Number(demarche?.insufficient_evidence_activities_count || 0)}</p>
              </div>
            </div>

            <p className="mt-4 text-sm leading-6 text-muted-foreground">
              Une opération reconstruit la chaîne verrou → hypothèse → expérience → résultat. Plusieurs passages ou activités peuvent donc appartenir au même noyau R&D. Les preuves insuffisantes augmentent le risque, mais ne réduisent pas mécaniquement la couverture Frascati.
            </p>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              Une validation ou une métrique classique n'est pas une R&D directe : elle est classée support seulement si son lien avec le protocole qui traite l'incertitude est prouvé ; sinon elle relève de l'ingénierie classique ou reste à documenter.
            </p>
            {demarche?.direct_final_solution_risk ? (
              <p className="mt-2 text-sm font-medium text-warning">
                Raccourci possible : le dossier doit expliquer pourquoi la solution finale ne pouvait pas être choisie dès le départ.
              </p>
            ) : null}
          </div>
        ) : null}

        <div className="rounded-xl border border-brand/20 bg-white p-5 space-y-5">
          <div className="flex items-start gap-3">
            <Sparkles className="mt-0.5 size-5 text-brand" />
            <div>
              <p className="text-sm font-semibold text-foreground">
                Pourquoi cet indice, quels verrous et quelles preuves ?
              </p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                Une seule analyse réunit le calcul, l'opération de référence, la démarche, les cinq critères Frascati et les extraits sources qui les justifient.
              </p>
            </div>
          </div>

          {evidenceReport && Object.keys(evidenceReport).length > 0 ? (
            <>
              <div className="rounded-lg border bg-muted/15 p-4">
                <div className="flex items-center justify-between gap-3 text-xs font-medium">
                  <span>Part documentée : {formatScore(documentedShare)}</span>
                  <span>Part à consolider : {formatScore(remainingShare)}</span>
                </div>
                <div className="mt-3 flex h-3 overflow-hidden rounded-full bg-muted">
                  <div className="bg-success" style={{ width: `${documentedShare * 100}%` }} />
                  <div className="bg-warning/70" style={{ width: `${remainingShare * 100}%` }} />
                </div>
                <p className="mt-3 text-xs leading-5 text-muted-foreground">
                  Chaque critère pèse 20 % : documenté = 20 %, partiel = 10 %, manquant ou contradictoire = 0 %. Le garde « ingénierie classique » est appliqué séparément.
                </p>
              </div>

              {operations.length > 0 ? (
                <div>
                  <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-brand">
                    Opérations et verrous évalués
                  </p>
                  <div className="grid gap-3 lg:grid-cols-2">
                    {operations.map((operation: any, index: number) => {
                      const isReference = String(operation?.group_id || "") === String(evidenceReport?.reference_operation_group_id || "")
                      const operationNumber = index + 1
                      const operationClaim = proofClaims.find((claim: any) =>
                        isFrenchEligibilityTextV190(claim?.text) && Array.isArray(claim?.proofs) && claim.proofs.some((proof: any) =>
                          String(proof?.role || "").toLocaleLowerCase("fr").includes(`opération ${operationNumber}`)
                        )
                      )
                      const operationProofs = Array.isArray(operationClaim?.proofs)
                        ? operationClaim.proofs.slice(0, 3)
                        : operation?.anchor_evidence?.excerpt
                          ? [operation.anchor_evidence]
                          : []
                      return (
                        <div key={operation?.group_id || index} className={`rounded-lg border p-4 ${isReference ? "border-success/40 bg-success/5" : "bg-muted/10"}`}>
                          <div className="flex items-start justify-between gap-2">
                            <p className="text-sm font-medium text-foreground">
                              Opération {operationNumber}
                            </p>
                            {isReference ? <Badge className="shrink-0 bg-success/10 text-success hover:bg-success/10">Opération de référence</Badge> : null}
                          </div>
                          <p className="mt-2 text-xs text-muted-foreground">
                            {demarcheLabels[String(operation?.operation_status || "")] || "Démarche à qualifier"} · couverture {formatScore(operation?.documentary_coverage)}
                          </p>
                          <p className="mt-3 text-sm leading-6 text-foreground">
                            {cleanDisplayText(String(
                              operationClaim?.text || operation?.justification_fr || "La justification de cette opération doit être complétée par le consultant CIR."
                            ))}
                          </p>
                          {operation?.consultant_validation_required ? (
                            <p className="mt-2 text-xs font-medium text-warning">Validation du consultant requise.</p>
                          ) : null}
                          {operationProofs.length > 0 ? (
                            <div className="mt-3 space-y-2">
                              {operationProofs.map((proof: any, proofIndex: number) => (
                                <EligibilityProofExcerptV153
                                  key={proof?.evidence_id || proofIndex}
                                  proof={proof}
                                  projectId={projectId}
                                  sourceDocuments={sourceDocuments}
                                />
                              ))}
                            </div>
                          ) : null}
                        </div>
                      )
                    })}
                  </div>
                </div>
              ) : null}

              {referenceOperation?.group_id ? (
                <div className="rounded-lg border border-brand/20 bg-brand/5 p-4">
                  <p className="text-xs font-semibold uppercase tracking-wide text-brand">Opération de référence pour l’étude des preuves</p>
                  <p className="mt-2 text-sm font-semibold text-foreground">
                    Opération {Math.max(1, operations.findIndex((operation: any) => String(operation?.group_id) === String(referenceOperation?.group_id)) + 1)}
                  </p>
                  <p className="mt-2 text-xs text-muted-foreground">
                    {demarcheLabels[String(referenceOperation?.operation_status || "")] || "Démarche à qualifier"}
                  </p>
                  {referenceOperation?.anchor_evidence?.excerpt ? (
                    <div className="mt-3">
                      <EligibilityProofExcerptV153
                        proof={referenceOperation.anchor_evidence}
                        projectId={projectId}
                        sourceDocuments={sourceDocuments}
                      />
                    </div>
                  ) : null}
                </div>
              ) : null}

              {referenceOperation?.causal_chain_evidence ? (
                <div>
                  <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-brand">Chaîne de démarche prouvée</p>
                  <div className="grid gap-3 lg:grid-cols-2">
                    {Object.entries(chainLabels).map(([key, label]) => {
                      const proofs = Array.isArray(referenceOperation?.causal_chain_evidence?.[key])
                        ? referenceOperation.causal_chain_evidence[key]
                        : []
                      return (
                        <div key={key} className="rounded-lg border bg-muted/10 p-3 space-y-2">
                          <p className="text-xs font-semibold text-foreground">{label}</p>
                          {proofs.length > 0 ? proofs.slice(0, 3).map((proof: any, index: number) => (
                            <EligibilityProofExcerptV153
                              key={proof?.evidence_id || index}
                              proof={proof}
                              projectId={projectId}
                              sourceDocuments={sourceDocuments}
                            />
                          )) : (
                            <p className="text-xs text-warning">Preuve explicite à compléter.</p>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
              ) : null}

              {criteria.length > 0 ? (
                <div>
                  <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-brand">
                    Pourquoi la part est acquise et pourquoi le reste manque
                  </p>
                  <div className="space-y-3">
                    {criteria.map((criterion: any, index: number) => {
                      const proofs = Array.isArray(criterion?.evidence) ? criterion.evidence : []
                      const criterionLabels: Record<string, string> = {
                        novelty: "Nouveauté",
                        creativity: "Créativité",
                        uncertainty: "Incertitude ou verrou",
                        systematicity: "Démarche systématique",
                        transferability: "Transférabilité",
                      }
                      return (
                        <div key={criterion?.criterion || index} className="rounded-lg border bg-muted/10 p-4 space-y-3">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <p className="text-sm font-semibold text-foreground">
                              {criterionLabels[String(criterion?.criterion || "")] || criterion?.label || "Critère Frascati"}
                            </p>
                            <div className="flex items-center gap-2">
                              <Badge variant="outline">{criterionStatusLabels[String(criterion?.status || "")] || criterion?.status}</Badge>
                              <span className="text-xs font-medium text-success">+{formatScore(criterion?.contribution_to_index)}</span>
                              {Number(criterion?.remaining_gap_to_full_coverage || 0) > 0 ? (
                                <span className="text-xs font-medium text-warning">reste {formatScore(criterion?.remaining_gap_to_full_coverage)}</span>
                              ) : null}
                            </div>
                          </div>
                          <p className="text-sm leading-6 text-muted-foreground">{cleanDisplayText(String(criterion?.reason_fr || "Justification documentaire à compléter."))}</p>
                          {proofs.length > 0 ? (
                            <div className="space-y-2">
                              {proofs.slice(0, 3).map((proof: any, proofIndex: number) => (
                                <EligibilityProofExcerptV153
                                  key={proof?.evidence_id || proofIndex}
                                  proof={proof}
                                  projectId={projectId}
                                  sourceDocuments={sourceDocuments}
                                />
                              ))}
                            </div>
                          ) : (
                            <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs text-warning">
                              Aucun extrait source suffisamment explicite : ce point explique la part documentaire restante.
                            </div>
                          )}
                          {criterion?.question ? (
                            <p className="text-xs font-medium text-warning">
                              À documenter : compléter ce critère avec une preuve projet explicite et la faire valider par le consultant.
                            </p>
                          ) : null}
                        </div>
                      )
                    })}
                  </div>
                </div>
              ) : null}
            </>
          ) : (
            <BackendSectionRendererV93 text={reading || "L'analyse détaillée sera disponible après l'exécution du NLP et d'EnnoDiagnostic."} />
          )}

          {generalProofClaims.length > 0 || operations.length === 0 ? (
          <div className="border-t pt-5">
            <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-brand">
              Analyse approfondie projet-spécifique
            </p>
            {generalProofClaims.length > 0 ? (
              <div className="space-y-4">
                {generalProofClaims.map((claim: any, index: number) => (
                  <div key={index} className="rounded-lg border bg-white p-4 space-y-3">
                    <p className="text-sm leading-7 text-foreground">{claim.text}</p>
                    {(claim.proofs || []).slice(0, 3).map((proof: any, proofIndex: number) => (
                      <EligibilityProofExcerptV153
                        key={proof?.evidence_id || proofIndex}
                        proof={proof}
                        projectId={projectId}
                        sourceDocuments={sourceDocuments}
                      />
                    ))}
                  </div>
                ))}
              </div>
            ) : (
              <BackendSectionRendererV93 text="L’analyse approfondie en français sera disponible après l’exécution d’EnnoDiagnostic." />
            )}
          </div>
          ) : null}
        </div>

        <p className="text-xs leading-6 text-muted-foreground">
          L'indice est une mesure interne de défendabilité documentaire, pas une probabilité d'acceptation CIR. Une opération classique est bloquée séparément ; un projet reste potentiellement éligible s'il contient au moins une opération R&D défendable ou partielle selon les preuves.
        </p>
      </CardContent>
    </Card>
  )
}

function eligibilityProofToEvidenceV191(proof: any): SourceEvidence {
  return {
    evidence_id: proof?.evidence_id,
    rag_chunk_id: proof?.rag_chunk_id,
    passage_id: proof?.passage_id || proof?.evidence_id,
    document_id: proof?.document_id,
    document: proof?.document,
    document_name: proof?.document_name || proof?.document,
    source_path: proof?.source_path,
    page_number: proof?.page_number,
    paragraph_index: proof?.paragraph_index,
    char_start: proof?.char_start ?? proof?.sentence_start,
    char_end: proof?.char_end,
    sentence_start: proof?.sentence_start,
    section_title: proof?.section_title,
    section_path: proof?.section_path,
    role: proof?.role,
    summary_fr: proof?.summary_fr,
    source_text_original: proof?.source_text_original || proof?.excerpt,
    source_field: proof?.source_field,
    source_is_original: proof?.source_is_original,
    highlight_coordinates: proof?.highlight_coordinates,
    semantic_link: proof?.semantic_link,
    justification_bridge_fr: proof?.justification_bridge_fr,
    excerpt: proof?.source_text_original || proof?.excerpt,
  }
}

function eligibilityProofKeyV193(proof: any, fallbackIndex = 0) {
  return String(
    proof?.passage_id || proof?.evidence_id ||
    `${proof?.document || "source"}:${proof?.sentence_start ?? proof?.char_start ?? fallbackIndex}:${String(proof?.source_text_original || proof?.excerpt || "").slice(0, 180)}`
  )
}

function dedupeEligibilityProofsV191(proofs: any[]) {
  const seen = new Set<string>()
  return proofs.filter((proof: any, index: number) => {
    if (!proof || typeof proof !== "object") return false
    if (String(proof?.evidence_id || "") === "F0") return false
    const key = eligibilityProofKeyV193(proof, index)
    if (!key || seen.has(key)) return false
    seen.add(key)
    return Boolean(proof?.source_text_original || proof?.excerpt)
  })
}

function UnifiedEligibilityStudyCardV191({
  score,
  signalsCount,
  candidateCount,
  reading,
  justification,
  demarche,
  evidenceReport,
  proofClaims,
  projectId,
  sourceDocuments,
}: {
  score: number | string | null | undefined
  signalsCount: number
  candidateCount: number
  reading: string
  justification: string
  demarche: any
  evidenceReport: any
  proofClaims: any[]
  projectId?: number | string
  sourceDocuments: DbSourceDocument[]
}) {
  const operations = Array.isArray(evidenceReport?.operations) ? evidenceReport.operations : []
  const scoreBasisOperation = evidenceReport?.score_basis_operation || {}
  const referenceOperation = evidenceReport?.reference_operation || scoreBasisOperation
  const criteria = Array.isArray(scoreBasisOperation?.criteria) && scoreBasisOperation.criteria.length > 0
    ? scoreBasisOperation.criteria
    : Array.isArray(referenceOperation?.criteria) ? referenceOperation.criteria : []
  const frenchClaims = (proofClaims || []).filter((claim: any) => isStrongProjectEligibilityTextV192(claim?.text))
  const atomicNarrativeClaims = frenchClaims.flatMap((claim: any) =>
    Array.isArray(claim?.claims) ? claim.claims : []
  ).filter((claim: any) => cleanDisplayText(String(claim?.text || "")))
  const narrativeParts = atomicNarrativeClaims.length > 0 ? atomicNarrativeClaims : frenchClaims
  const generatedNarrative = cleanDisplayText(
    narrativeParts.map((claim: any) => cleanDisplayText(String(claim?.text || ""))).filter(Boolean).join(" ")
  )
  const demarcheLabels: Record<string, string> = {
    rnd_core_defendable: "les preuves soutiennent un noyau R&D défendable",
    rnd_core_partial: "un noyau R&D est identifiable mais doit être consolidé",
    classical_engineering: "les travaux relèvent de l’ingénierie classique selon les preuves disponibles",
    insufficient_evidence: "les preuves sont insuffisantes et nécessitent une validation du consultant",
  }
  const demarcheStatus = String(demarche?.project_status || demarche?.operation_status || "insufficient_evidence")
  const operationNarrative = operations
    .map((operation: any) => cleanDisplayText(String(operation?.justification_fr || "")))
    .filter(Boolean)
    .join(" ")
  const criteriaNarrative = criteria
    .map((criterion: any) => cleanDisplayText(String(criterion?.reason_fr || "")))
    .filter(Boolean)
    .join(" ")
  const documentedCriteriaNarrative = criteria
    .filter((criterion: any) => String(criterion?.status || "") === "documented")
    .map((criterion: any) => {
      const label = cleanDisplayText(String(criterion?.label || criterion?.criterion || "critère"))
      const reason = cleanDisplayText(String(criterion?.reason_fr || criterion?.reason || ""))
      return `${label} est documenté${reason ? ` : ${reason}` : ""}`
    })
    .join(" ; ")
  const weakCriteriaNarrative = criteria
    .filter((criterion: any) => String(criterion?.status || "") !== "documented")
    .map((criterion: any) => {
      const label = cleanDisplayText(String(criterion?.label || criterion?.criterion || "critère"))
      const reason = cleanDisplayText(String(criterion?.reason_fr || criterion?.reason || ""))
      const gap = formatScore(criterion?.remaining_gap_to_full_coverage)
      return `${gap !== "—" ? `${gap} restent à consolider pour ` : "Le critère "}${label}${reason ? `, car ${reason}` : ""}`
    })
    .join(" ; ")
  const remainingGap = formatScore(evidenceReport?.remaining_documentary_gap)
  const fallbackConclusion = demarcheStatus === "classical_engineering"
    ? "EnnoDiagnostic considère donc l’opération comme non éligible au titre d’une activité de R&D, sous réserve de validation par le consultant CIR."
    : demarcheStatus === "insufficient_evidence"
      ? "Les preuves disponibles ne permettent pas encore de conclure à une éligibilité potentielle ; le consultant CIR doit confirmer la qualification."
      : "EnnoDiagnostic considère ainsi l’opération comme potentiellement éligible au CIR, sous réserve de validation par le consultant."
  const fallbackNarrative = cleanDisplayText(
    `${operationNarrative || cleanDisplayText(reading || justification)} ` +
    `${demarcheLabels[demarcheStatus] || "La nature de la démarche reste à qualifier"}. ` +
    `${documentedCriteriaNarrative ? `Les critères Frascati acquis s’expliquent ainsi : ${documentedCriteriaNarrative}. ` : ""}` +
    `${weakCriteriaNarrative ? `La part restant à consolider se répartit ainsi : ${weakCriteriaNarrative}. ` : criteriaNarrative} ` +
    `Ces éléments conduisent à un niveau de défendabilité R&D de ${formatScore(score)}${remainingGap !== "—" ? `, avec ${remainingGap} restant à consolider` : ""}. ` +
    fallbackConclusion
  )
  const narrative = generatedNarrative || fallbackNarrative

  const referenceFunctional = referenceOperation?.functional_evidence && typeof referenceOperation.functional_evidence === "object"
    ? referenceOperation.functional_evidence
    : {}
  const referenceProofs = ["uncertainty", "hypothesis", "experiment", "result", "learning"].flatMap((stage) =>
    Array.isArray(referenceFunctional?.[stage]) ? referenceFunctional[stage].slice(0, 1) : []
  )
  const otherOperationProofs = operations
    .filter((operation: any) => String(operation?.group_id || "") !== String(referenceOperation?.group_id || ""))
    .flatMap((operation: any) => {
      const functional = operation?.functional_evidence && typeof operation.functional_evidence === "object"
        ? operation.functional_evidence
        : {}
      const firstStage = ["uncertainty", "hypothesis", "experiment", "result", "learning"].find((stage) =>
        Array.isArray(functional?.[stage]) && functional[stage].length > 0
      )
      return firstStage ? functional[firstStage].slice(0, 1) : []
    })
  const criterionProofs = criteria.flatMap((criterion: any) =>
    Array.isArray(criterion?.evidence) ? criterion.evidence.slice(0, 1) : []
  )
  const citedProofs = narrativeParts.flatMap((claim: any) =>
    Array.isArray(claim?.proofs) ? claim.proofs : []
  )
  const documentaryCitedProofs = citedProofs.filter((proof: any) => String(proof?.evidence_id || "") !== "F0")
  const fallbackProofs = [...referenceProofs, ...otherOperationProofs, ...criterionProofs]
  const sourceProofs = dedupeEligibilityProofsV191(
    documentaryCitedProofs.length > 0 ? documentaryCitedProofs : fallbackProofs
  ).slice(0, 8)
  const sourceEvidence = sourceProofs.map(eligibilityProofToEvidenceV191)
  const sourceProofNumbers = new Map(
    sourceProofs.map((proof: any, index: number) => [eligibilityProofKeyV193(proof, index), index + 1])
  )
  const sourcedNarrativeParts = generatedNarrative
    ? narrativeParts.map((claim: any) => {
        const proofs = dedupeEligibilityProofsV191(Array.isArray(claim?.proofs) ? claim.proofs : [])
          .map((proof: any) => ({ proof, citationNumber: sourceProofNumbers.get(eligibilityProofKeyV193(proof)) }))
          .filter((item: any) => Number.isFinite(item.citationNumber))
        return {
          text: cleanDisplayText(String(claim?.text || "")),
          evidence: proofs.map((item: any) => eligibilityProofToEvidenceV191(item.proof)),
          citationNumbers: proofs.map((item: any) => Number(item.citationNumber)),
        }
      }).filter((claim: any) => claim.text)
    : []
  const operationsCount = Number(demarche?.operations_count || operations.length || signalsCount || 0)

  return (
    <Card className="overflow-hidden border-brand/20 bg-gradient-to-br from-brand/5 via-white to-white">
      <CardHeader className="border-b bg-white/70">
        <div className="flex min-w-0 flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <CardTitle className="flex items-center gap-2 text-base">
              <BrainCircuit className="size-5 text-brand" />
              Étude d’éligibilité
            </CardTitle>
            <CardDescription className="mt-1">
              Une lecture unique reliant les verrous, la démarche de recherche, les critères Frascati et les preuves du dossier.
            </CardDescription>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">{operationsCount} opération(s) · {candidateCount} verrou(s)</Badge>
          </div>
        </div>
      </CardHeader>

      <CardContent className="min-w-0 space-y-5 pt-5">
        <div className="min-w-0 rounded-xl border border-brand/20 bg-white p-4 sm:p-5">
          <p className="text-sm font-semibold text-foreground">Conclusion globale d’éligibilité</p>
          <p className="mt-3 text-sm leading-7 text-foreground">
            {sourcedNarrativeParts.length > 0 ? sourcedNarrativeParts.map((claim: any, index: number) => (
              <span key={`${claim.text.slice(0, 80)}:${index}`}>
                {claim.text}
                {projectId && claim.evidence.length > 0 ? (
                  <SourceEvidenceCitations
                    projectId={projectId}
                    documents={sourceDocuments}
                    evidence={claim.evidence}
                    citationNumbers={claim.citationNumbers}
                  />
                ) : null}
                {index < sourcedNarrativeParts.length - 1 ? " " : null}
              </span>
            )) : narrative}
            {sourcedNarrativeParts.length === 0 && projectId && sourceEvidence.length > 0 ? (
              <SourceEvidenceCitations
                projectId={projectId}
                documents={sourceDocuments}
                evidence={sourceEvidence}
              />
            ) : null}
          </p>
        </div>


        <p className="text-xs leading-6 text-muted-foreground">
          L’indice mesure la défendabilité documentaire interne et non une probabilité d’acceptation administrative. La décision finale reste celle du consultant CIR.
        </p>
      </CardContent>
    </Card>
  )
}


function getComparisonCurrentText(item: any) {
  return (
    item?.current_item?.text ||
    item?.current_item?.source_text ||
    item?.current_item?.title ||
    item?.current_item?.section_title ||
    "Verrou courant non renseigné."
  )
}

function getComparisonPreviousText(item: any) {
  return (
    item?.best_match?.previous_candidate?.text ||
    item?.best_match?.previous_candidate?.source_text ||
    item?.best_match?.previous_candidate?.title ||
    ""
  )
}

function getComparisonPreviousYear(item: any) {
  return item?.best_match?.previous_candidate?.year || "—"
}

function getComparisonDecision(item: any) {
  return item?.decision?.label || item?.decision?.status || "À vérifier"
}

function noveltyPercent(value: any) {
  const n = Number(value)
  if (!Number.isFinite(n)) return "—"
  return `${Math.round((n <= 1 ? n * 100 : n))}%`
}

function normalizeComparisonStatus(status: any) {
  const value = String(status || "").toLowerCase()

  if (value.includes("new") || value.includes("nouveau")) {
    return "Nouveauté à examiner"
  }

  if (value.includes("evolution") || value.includes("partial") || value.includes("partielle")) {
    return "Évolution à valoriser"
  }

  if (value.includes("continuity") || value.includes("continuité")) {
    return "Continuité forte"
  }

  return "À analyser"
}

function cleanComparisonCurrentTitle(item: any) {
  const raw = cleanDisplayText(getComparisonCurrentText(item))
  const match = raw.match(/Verrou implicite possible\s*[—-]\s*([^.?;\n]+)/i)

  if (match?.[1]) return match[1].trim()

  const firstLine = raw.split("\n").find(Boolean) || ""
  return firstLine
    .replace(/^Verrou courant\s*/i, "")
    .replace(/^Verrou\s*[:\-]\s*/i, "")
    .slice(0, 120)
    .trim() || "Verrou courant"
}

function cleanComparisonEvidence(item: any, limit = 360) {
  const raw = cleanDisplayText(getComparisonCurrentText(item))
  const indices = raw.split(/Indices sources\s*:/i)[1] || raw
  const cleaned = indices
    .replace(/Documents concernés\s*:[\s\S]*?(?=Indices sources\s*:|$)/i, "")
    .replace(/Question de qualification\s*:[^.?!]+[.?!]?\s*/i, "")
    .replace(/Ce verrou est reconstruit[^.?!]+[.?!]?\s*/i, "")
    .replace(/^[-•]\s*/gm, "")
    .trim()

  if (cleaned.length <= limit) return cleaned || "Preuve documentaire à vérifier."
  return `${cleaned.slice(0, limit).trim()}…`
}

function cleanPreviousCirMatch(item: any, limit = 260) {
  const raw = cleanDisplayText(getComparisonPreviousText(item))
  if (!raw) return ""

  const lines = raw
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line) => !/^PROJET\s+/i.test(line))
    .filter((line) => !/^Fiche descriptive/i.test(line))
    .filter((line) => !/^Intitulé du projet$/i.test(line))
    .filter((line) => !/^Objectifs du projet$/i.test(line))
    .filter((line) => !/^Contexte du projet$/i.test(line))

  const text = lines.join(" ")
  if (text.length <= limit) return text
  return `${text.slice(0, limit).trim()}…`
}

function cirConsultantReading(summary: any, explanation: any, noveltyScore: any) {
  const novelty = Number(noveltyScore)
  const newCount = Number(summary?.new_verrou_count ?? 0)
  const evolutionCount = Number(summary?.evolution_verrou_count ?? 0)
  const continuityCount = Number(summary?.continuity_verrou_count ?? 0)

  if (evolutionCount > 0 && newCount === 0 && continuityCount === 0) {
    return "Le projet courant s’inscrit dans la continuité du CIR précédent, mais avec des évolutions techniques à documenter. Le consultant doit expliciter les nouveaux essais, les nouvelles configurations, les nouvelles mesures ou les nouvelles solutions testées."
  }

  if (newCount > 0) {
    return "Le projet contient des éléments potentiellement nouveaux par rapport au CIR précédent. Le consultant doit vérifier que ces nouveautés sont bien soutenues par des preuves techniques de l’année courante."
  }

  if (Number.isFinite(novelty) && novelty >= 0.5) {
    return "La comparaison indique un niveau de nouveauté significatif. Le consultant doit transformer cette lecture en arguments CIR : évolution des verrous, travaux réalisés cette année et justification de la non-répétition."
  }

  return cleanDisplayText(String(explanation || "La comparaison N vs N-1 est disponible. Le consultant peut l’utiliser pour distinguer continuités, évolutions et nouveautés."))
}

function cirConsultantRisk(summary: any) {
  const evolutionCount = Number(summary?.evolution_verrou_count ?? 0)
  const continuityCount = Number(summary?.continuity_verrou_count ?? 0)

  if (evolutionCount > 0) {
    return "Risque principal : présenter des travaux en continuité comme s’ils étaient totalement nouveaux. À sécuriser en expliquant précisément les nouvelles expérimentations, les nouvelles configurations et les résultats obtenus sur l’année courante."
  }

  if (continuityCount > 0) {
    return "Risque principal : redondance avec le CIR précédent. Les passages en continuité forte doivent être justifiés par une progression technique réelle."
  }

  return "Point de vigilance : vérifier que chaque verrou retenu est appuyé par des preuves de l’année courante et par une différence claire avec le CIR précédent."
}

function previousReferenceLabel(item: any) {
  const previousYear = getComparisonPreviousYear(item)
  const previous = cleanDisplayText(getComparisonPreviousText(item))

  if (!previous) {
    return previousYear ? `Référence CIR ${previousYear} détectée` : "Référence CIR précédent détectée"
  }

  const firstLine = previous.split("\n").find(Boolean) || previous
  const cleaned = firstLine
    .replace(/^PROJET\s+.*$/i, "")
    .replace(/^Fiche descriptive.*$/i, "")
    .trim()

  if (cleaned) {
    return `${previousYear ? `CIR ${previousYear}` : "CIR précédent"} — ${cleaned.slice(0, 140)}`
  }

  return previousYear ? `Référence CIR ${previousYear} détectée` : "Référence CIR précédent détectée"
}

function previousReferenceMeaning(item: any) {
  const previousYear = getComparisonPreviousYear(item)
  const previous = cleanPreviousCirMatch(item, 420)

  if (previous) {
    return `Le CIR ${previousYear || "précédent"} contient un passage proche. Le consultant doit l’utiliser comme repère historique, puis expliquer précisément ce que le dossier courant apporte de nouveau ou de plus avancé.`
  }

  return `Le CIR ${previousYear || "précédent"} contient un axe proche. Le consultant doit comparer les preuves courantes avec ce repère pour distinguer continuité, évolution et nouveauté.`
}

function currentEvolutionMeaning(item: any) {
  const evidence = cleanComparisonEvidence(item, 420)
  const decision = normalizeComparisonStatus(item?.decision?.status)

  if (decision === "Continuité forte") {
    return "Le dossier courant semble proche du CIR précédent. L’intérêt CIR dépend donc de la capacité à démontrer une progression technique réelle à partir des preuves de l’année courante."
  }

  if (decision === "Évolution à valoriser") {
    return "Les documents courants apportent des éléments techniques nouveaux ou plus précis. Le consultant doit les rattacher à la progression du verrou par rapport au CIR précédent."
  }

  if (decision === "Nouveauté à examiner") {
    return "Les documents courants contiennent un signal potentiellement nouveau. Le consultant doit confirmer qu’il s’agit bien d’un verrou ou d’un apport technique utile, et non d’un passage isolé ou mal classé."
  }

  if (evidence && evidence !== "Preuve documentaire à vérifier.") {
    return "Les documents courants apportent des éléments techniques exploitables. Le consultant doit vérifier leur rôle exact dans la justification CIR."
  }

  return "La progression technique doit être confirmée à partir des preuves documentaires de l’année courante."
}

function consultantJustificationNeeded(item: any) {
  const decision = normalizeComparisonStatus(item?.decision?.status)

  if (decision === "Continuité forte") {
    return "Justifier clairement ce qui a changé cette année afin d’éviter une simple répétition du CIR précédent."
  }

  if (decision === "Nouveauté à examiner") {
    return "Vérifier si ce point est réellement nouveau ou s’il correspond à un passage mal classé. Le conserver seulement s’il apporte une preuve technique utile."
  }

  if (decision === "Évolution à valoriser") {
    return "Mettre en avant les nouveaux essais, les nouvelles configurations, les valeurs mesurées et l’impact observé par rapport au CIR précédent."
  }

  return "Expliquer la différence avec le CIR précédent et citer les preuves de l’année courante."
}

function CirComparisonCard({ item, index }: { item: any; index: number }) {
  const status = item?.decision?.status
  const title = cleanComparisonCurrentTitle(item)
  const evidence = cleanComparisonEvidence(item)
  const decision = normalizeComparisonStatus(status)

  return (
    <div className="rounded-xl border bg-white p-4 space-y-4 shadow-sm">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="space-y-1">
          <Badge variant="outline" className={`text-xs ${comparisonBadgeClass(status)}`}>
            {decision}
          </Badge>
          <p className="text-sm font-semibold text-foreground">
            {title}
          </p>
        </div>

        <div className="flex gap-2 flex-wrap">
          <Badge variant="outline" className="text-xs">
            continuité {noveltyPercent(item?.decision?.continuity_score)}
          </Badge>
          <Badge variant="outline" className="text-xs">
            nouveauté {noveltyPercent(item?.decision?.novelty_score)}
          </Badge>
        </div>
      </div>

      <div className="rounded-lg border border-brand/20 bg-brand/5 p-4">
        <p className="text-xs font-semibold text-brand uppercase tracking-wide mb-1">
          Lecture consultant
        </p>
        <p className="text-sm leading-7 text-foreground">
          {currentEvolutionMeaning(item)}
        </p>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        <div className="rounded-lg border bg-muted/20 p-4">
          <p className="text-xs font-semibold text-brand uppercase tracking-wide mb-1">
            Apport du dossier courant
          </p>
          <p className="text-sm leading-7 text-foreground whitespace-pre-wrap">
            {evidence}
          </p>
        </div>

        <div className="rounded-lg border bg-muted/20 p-4">
          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">
            Repère dans le CIR précédent
          </p>
          <p className="text-sm font-medium text-foreground mb-2">
            {previousReferenceLabel(item)}
          </p>
          <p className="text-sm leading-7 text-muted-foreground">
            {previousReferenceMeaning(item)}
          </p>
        </div>
      </div>

      <div className="rounded-lg border border-warning/20 bg-warning/5 p-4">
        <p className="text-xs font-semibold text-warning uppercase tracking-wide mb-1">
          À justifier dans le CIR courant
        </p>
        <p className="text-sm leading-7 text-foreground">
          {consultantJustificationNeeded(item)}
        </p>
      </div>

      <details className="rounded-lg border bg-white p-3">
        <summary className="cursor-pointer text-xs font-semibold text-muted-foreground uppercase tracking-wide">
          Traçabilité technique
        </summary>
        <div className="mt-3 space-y-3">
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1">Verrou courant brut</p>
            <p className="text-xs text-foreground whitespace-pre-wrap">
              {cleanDisplayText(getComparisonCurrentText(item))}
            </p>
          </div>
          {getComparisonPreviousText(item) && (
            <div>
              <p className="text-xs font-medium text-muted-foreground mb-1">Correspondance CIR précédent brute</p>
              <p className="text-xs text-muted-foreground whitespace-pre-wrap">
                {cleanDisplayText(getComparisonPreviousText(item)).slice(0, 1200)}
              </p>
            </div>
          )}
        </div>
      </details>
    </div>
  )
}


function comparisonBadgeClass(status: string | null | undefined) {
  const value = String(status || "").toLowerCase()

  if (value.includes("new") || value.includes("nouveau")) {
    return "bg-brand/10 text-brand border-brand/30"
  }

  if (value.includes("evolution") || value.includes("partial") || value.includes("partielle")) {
    return "bg-warning/10 text-warning border-warning/30"
  }

  if (value.includes("continuity") || value.includes("continuité") || value.includes("continuite")) {
    return "bg-success/10 text-success border-success/30"
  }

  if (value.includes("mixed") || value.includes("mixte")) {
    return "bg-brand/10 text-brand border-brand/30"
  }

  return "bg-muted text-muted-foreground border-border"
}

function cirSignalLabel(signal: any) {
  const value = String(signal || "").toLowerCase()

  if (value.includes("mixed")) return "Profil mixte"
  if (value.includes("new") || value.includes("nouveau")) return "Nouveautés dominantes"
  if (value.includes("evolution") || value.includes("partial")) return "Évolutions dominantes"
  if (value.includes("continuity") || value.includes("continuité") || value.includes("continuite")) return "Continuité forte"
  if (value.includes("risk")) return "Risque de répétition"

  return signal || "Analyse disponible"
}

function docCompareDecisionClass(decision: string | null | undefined) {
  const value = String(decision || "").toLowerCase()

  if (value === "strong") {
    return "bg-success/10 text-success border-success/30"
  }

  if (value === "medium") {
    return "bg-warning/10 text-warning border-warning/30"
  }

  return "bg-muted text-muted-foreground border-border"
}

function shortDocText(value: any, limit = 420) {
  const text = String(value || "").trim()
  if (text.length <= limit) return text || "—"
  return `${text.slice(0, limit).trim()}…`
}


type DocComparisonViewKind = "different" | "only_a" | "only_b" | "identical"

type DocComparisonViewItem = {
  id: string
  kind: DocComparisonViewKind
  label: string
  aText: string
  bText: string
  score: any
  numericConflict: boolean
}

type DocComparisonToken = {
  value: string
  changed: boolean
}


function buildDocComparisonLabelV202({
  rawLabel,
  aText,
  bText,
  fallbackPrefix,
  index,
}: {
  rawLabel: any
  aText: string
  bText: string
  fallbackPrefix: string
  index: number
}) {
  const cleanedLabel = cleanDisplayText(String(rawLabel || ""))
  const invalidLabels = new Set(["", "unknown", "n/a", "null", "undefined"])

  if (!invalidLabels.has(cleanedLabel.toLowerCase())) {
    return cleanedLabel
  }

  const sourceText = cleanDisplayText(String(aText || bText || ""))
  if (!sourceText) {
    return `${fallbackPrefix} ${index + 1}`
  }

  const words = sourceText.split(/\s+/).filter(Boolean).slice(0, 8)
  return words.join(" ")
}

function buildDocComparisonEvidenceV202({
  item,
  side,
  summary,
}: {
  item: DocComparisonViewItem | null
  side: "a" | "b"
  summary: any
}): SourceEvidence | null {
  if (!item) return null

  const documentName =
    side === "a"
      ? cleanDisplayText(String(summary?.doc_a || ""))
      : cleanDisplayText(String(summary?.doc_b || ""))

  const excerpt =
    side === "a"
      ? cleanDisplayText(String(item?.aText || ""))
      : cleanDisplayText(String(item?.bText || ""))

  if (!documentName && !excerpt) return null

  return {
    evidence_id: `doc-compare-${item.id}-${side}`,
    passage_id: `doc-compare-${item.id}-${side}`,
    document: documentName || undefined,
    document_name: documentName || undefined,
    role:
      side === "a"
        ? "Comparaison documentaire — passage A"
        : "Comparaison documentaire — passage B",
    excerpt: excerpt || undefined,
    source_text_original: excerpt || undefined,
    text: excerpt || undefined,
    metadata: {
      comparison_kind: item.kind,
      comparison_label: item.label,
      comparison_side: side,
    },
  } as SourceEvidence
}

function normalizeDocComparisonItemsV201(comparison: any): Record<DocComparisonViewKind, DocComparisonViewItem[]> {
  const different = Array.isArray(comparison?.different_between_a_b)
    ? comparison.different_between_a_b
    : []
  const onlyA = Array.isArray(comparison?.only_in_a)
    ? comparison.only_in_a
    : []
  const onlyB = Array.isArray(comparison?.only_in_b)
    ? comparison.only_in_b
    : []
  const identical = Array.isArray(comparison?.identical)
    ? comparison.identical
    : []

  return {
    different: different.map((item: any, index: number) => {
      const aText = cleanDisplayText(String(item?.a_text || item?.a || ""))
      const bText = cleanDisplayText(String(item?.b_text || item?.b || ""))

      return {
        id: `different-${index}`,
        kind: "different" as const,
        label: buildDocComparisonLabelV202({
          rawLabel: item?.context_key,
          aText,
          bText,
          fallbackPrefix: "Différence",
          index,
        }),
        aText,
        bText,
        score: item?.score,
        numericConflict: Boolean(item?.numeric_conflict),
      }
    }),
    only_a: onlyA.map((item: any, index: number) => {
      const aText = cleanDisplayText(String(item?.text || item?.a_text || ""))

      return {
        id: `only-a-${index}`,
        kind: "only_a" as const,
        label: buildDocComparisonLabelV202({
          rawLabel: item?.context_key,
          aText,
          bText: "",
          fallbackPrefix: "Seulement A",
          index,
        }),
        aText,
        bText: "",
        score: item?.score,
        numericConflict: false,
      }
    }),
    only_b: onlyB.map((item: any, index: number) => {
      const bText = cleanDisplayText(String(item?.text || item?.b_text || ""))

      return {
        id: `only-b-${index}`,
        kind: "only_b" as const,
        label: buildDocComparisonLabelV202({
          rawLabel: item?.context_key,
          aText: "",
          bText,
          fallbackPrefix: "Seulement B",
          index,
        }),
        aText: "",
        bText,
        score: item?.score,
        numericConflict: false,
      }
    }),
    identical: identical.map((item: any, index: number) => {
      const textValue = cleanDisplayText(
        String(item?.text || item?.a_text || item?.b_text || "")
      )

      return {
        id: `identical-${index}`,
        kind: "identical" as const,
        label: buildDocComparisonLabelV202({
          rawLabel: item?.context_key,
          aText: textValue,
          bText: textValue,
          fallbackPrefix: "Commun",
          index,
        }),
        aText: textValue,
        bText: textValue,
        score: item?.score,
        numericConflict: false,
      }
    }),
  }
}

function tokenizeDocComparisonV201(value: string) {
  return String(value || "")
    .replace(/\r\n/g, "\n")
    .split(/(\s+|[.,;:!?()[\]{}"“”'’/\\\-–—])/)
    .filter((token) => token !== "")
}

function docComparisonTokenComparableV201(value: string) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("fr")
}

function computeDocComparisonDiffV201(aText: string, bText: string): {
  a: DocComparisonToken[]
  b: DocComparisonToken[]
} {
  const aTokens = tokenizeDocComparisonV201(aText)
  const bTokens = tokenizeDocComparisonV201(bText)

  // Garde-fou pour des extraits anormalement longs.
  // Les comparaisons backend sont normalement des passages courts.
  if (aTokens.length > 700 || bTokens.length > 700) {
    return {
      a: aTokens.map((value) => ({ value, changed: true })),
      b: bTokens.map((value) => ({ value, changed: true })),
    }
  }

  const aComparable = aTokens.map(docComparisonTokenComparableV201)
  const bComparable = bTokens.map(docComparisonTokenComparableV201)

  const rows = aTokens.length + 1
  const cols = bTokens.length + 1
  const dp: number[][] = Array.from({ length: rows }, () =>
    Array.from({ length: cols }, () => 0)
  )

  for (let i = aTokens.length - 1; i >= 0; i -= 1) {
    for (let j = bTokens.length - 1; j >= 0; j -= 1) {
      if (aComparable[i] === bComparable[j]) {
        dp[i][j] = dp[i + 1][j + 1] + 1
      } else {
        dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1])
      }
    }
  }

  const aResult: DocComparisonToken[] = []
  const bResult: DocComparisonToken[] = []

  let i = 0
  let j = 0

  while (i < aTokens.length && j < bTokens.length) {
    if (aComparable[i] === bComparable[j]) {
      aResult.push({ value: aTokens[i], changed: false })
      bResult.push({ value: bTokens[j], changed: false })
      i += 1
      j += 1
      continue
    }

    if (dp[i + 1][j] >= dp[i][j + 1]) {
      aResult.push({ value: aTokens[i], changed: true })
      i += 1
    } else {
      bResult.push({ value: bTokens[j], changed: true })
      j += 1
    }
  }

  while (i < aTokens.length) {
    aResult.push({ value: aTokens[i], changed: true })
    i += 1
  }

  while (j < bTokens.length) {
    bResult.push({ value: bTokens[j], changed: true })
    j += 1
  }

  return { a: aResult, b: bResult }
}

function DocComparisonHighlightedTextV201({
  tokens,
  side,
}: {
  tokens: DocComparisonToken[]
  side: "a" | "b"
}) {
  const changedClass =
    side === "a"
      ? "border-warning/35 bg-warning/15 text-foreground"
      : "border-brand/30 bg-brand/10 text-foreground"

  return (
    <p className="whitespace-pre-wrap text-sm leading-7 text-foreground [overflow-wrap:anywhere]">
      {tokens.map((token, index) => {
        if (/^\s+$/.test(token.value) || !token.changed) {
          return <span key={index}>{token.value}</span>
        }

        return (
          <span
            key={index}
            className={`rounded border px-0.5 ${changedClass}`}
          >
            {token.value}
          </span>
        )
      })}
    </p>
  )
}


type InlineDocumentPreviewStateV203 = {
  loading: boolean
  error: string
  objectUrl: string
  mediaType: string
  highlightPage: number | null
  highlightExact: boolean | null
}

const EMPTY_INLINE_DOCUMENT_PREVIEW_V203: InlineDocumentPreviewStateV203 = {
  loading: false,
  error: "",
  objectUrl: "",
  mediaType: "",
  highlightPage: null,
  highlightExact: null,
}

function normalizeDocumentNameV203(value: any) {
  return String(value || "")
    .replace(/\\/g, "/")
    .split("/")
    .pop()!
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("fr")
    .replace(/\.[a-z0-9]{2,6}$/i, "")
    .replace(/_[a-f0-9]{10,64}$/i, "")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
}

function findComparisonSourceDocumentV203(
  rawName: string,
  sourceDocuments: DbSourceDocument[],
) {
  const wanted = normalizeDocumentNameV203(rawName)
  if (!wanted) return null

  let best: DbSourceDocument | null = null
  let bestScore = 0

  for (const document of sourceDocuments || []) {
    const candidates = [
      normalizeDocumentNameV203(document?.filename),
      normalizeDocumentNameV203(document?.stored_filename),
    ].filter(Boolean)

    for (const candidate of candidates) {
      let score = 0

      if (candidate === wanted) {
        score = 100
      } else if (candidate.includes(wanted) || wanted.includes(candidate)) {
        score = 92
      } else {
        const wantedWords = new Set(wanted.split(" ").filter(Boolean))
        const candidateWords = new Set(candidate.split(" ").filter(Boolean))
        let common = 0

        wantedWords.forEach((word) => {
          if (candidateWords.has(word)) common += 1
        })

        const ratio =
          common /
          Math.max(1, Math.min(wantedWords.size, candidateWords.size))

        if (common >= 2 && ratio >= 0.7) {
          score = Math.round(70 + ratio * 20)
        }
      }

      if (score > bestScore) {
        bestScore = score
        best = document
      }
    }
  }

  return bestScore >= 70 ? best : null
}

function InlineComparisonDocumentViewerV203({
  projectId,
  documentName,
  sourceDocuments,
  selectedItem,
  side,
}: {
  projectId: number | string
  documentName: string
  sourceDocuments: DbSourceDocument[]
  selectedItem: DocComparisonViewItem | null
  side: "a" | "b"
}) {
  const [preview, setPreview] = useState<InlineDocumentPreviewStateV203>(
    EMPTY_INLINE_DOCUMENT_PREVIEW_V203,
  )

  const excerpt =
    side === "a"
      ? cleanDisplayText(String(selectedItem?.aText || ""))
      : cleanDisplayText(String(selectedItem?.bText || ""))

  const resolvedDocument = useMemo(
    () => findComparisonSourceDocumentV203(documentName, sourceDocuments),
    [documentName, sourceDocuments],
  )

  useEffect(() => {
    let cancelled = false
    let createdObjectUrl = ""

    const load = async () => {
      if (!projectId || !documentName) {
        setPreview({
          loading: false,
          error: "Document non résolu.",
          objectUrl: "",
          mediaType: "",
          highlightPage: null,
          highlightExact: null,
        })
        return
      }

      setPreview({
        loading: true,
        error: "",
        objectUrl: "",
        mediaType: "",
        highlightPage: null,
        highlightExact: null,
      })

      const token = getAccessToken()
      const authHeaders = token
        ? { Authorization: `Bearer ${token}` }
        : {}

      try {
        let response: Response | null = null

        // Toujours essayer la prévisualisation surlignée en premier :
        // elle renvoie le document complet (HTML/PDF/image) avec le passage
        // sélectionné mis en évidence.
        if (excerpt) {
          response = await fetch(
            `${API_BASE_URL}/projects/${projectId}/source-highlight/preview`,
            {
              method: "POST",
              headers: {
                ...authHeaders,
                "Content-Type": "application/json",
              },
              body: JSON.stringify({
                document_id:
                  resolvedDocument && Number(resolvedDocument.id) > 0
                    ? resolvedDocument.id
                    : null,
                source_name: documentName,
                document_name: documentName,
                passage_id: selectedItem?.id || null,
                excerpt,
              }),
            },
          )

          if (!response.ok) {
            response = null
          }
        }

        // Fallback : document brut complet si le surlignage n'est pas disponible.
        if (
          !response &&
          resolvedDocument &&
          Number(resolvedDocument.id) > 0
        ) {
          response = await fetch(
            `${API_BASE_URL}/projects/${projectId}/source-documents/${resolvedDocument.id}/open`,
            {
              headers: authHeaders,
            },
          )

          if (!response.ok) {
            response = null
          }
        }

        if (!response) {
          throw new Error(
            "Le document complet n'a pas pu être prévisualisé.",
          )
        }

        // Le backend indique la page où le passage a réellement été retrouvé.
        // On la lit AVANT response.blob(), puis on l'utilise dans le fragment
        // du PDF (#page=N). Ainsi, quand le consultant sélectionne un passage,
        // les deux documents A/B se positionnent automatiquement sur la bonne page.
        const highlightPageRaw = response.headers.get(
          "X-EnnoSmart-Highlight-Page",
        )
        const highlightExactRaw = response.headers.get(
          "X-EnnoSmart-Highlight-Exact",
        )

        const parsedHighlightPage = Number(highlightPageRaw)
        const highlightPage =
          Number.isFinite(parsedHighlightPage) && parsedHighlightPage > 0
            ? parsedHighlightPage
            : null

        const highlightExact =
          highlightExactRaw === "true"
            ? true
            : highlightExactRaw === "false"
              ? false
              : null

        const blob = await response.blob()
        createdObjectUrl = URL.createObjectURL(blob)

        const mediaType = String(
          response.headers.get("content-type") || blob.type || "",
        ).toLocaleLowerCase("fr")

        if (!cancelled) {
          setPreview({
            loading: false,
            error: "",
            objectUrl: createdObjectUrl,
            mediaType,
            highlightPage,
            highlightExact,
          })
        }
      } catch (error) {
        if (!cancelled) {
          setPreview({
            loading: false,
            error:
              error instanceof Error
                ? error.message
                : "Prévisualisation documentaire indisponible.",
            objectUrl: "",
            mediaType: "",
            highlightPage: null,
            highlightExact: null,
          })
        }
      }
    }

    void load()

    return () => {
      cancelled = true
      if (createdObjectUrl) {
        URL.revokeObjectURL(createdObjectUrl)
      }
    }
  }, [
    projectId,
    documentName,
    excerpt,
    resolvedDocument?.id,
    selectedItem?.id,
    side,
  ])

  const isImage = preview.mediaType.startsWith("image/")
  const isPdf =
    preview.mediaType.includes("application/pdf") ||
    preview.mediaType.includes("pdf")

  // Chrome/Edge PDF viewer comprend #page=N.
  // Le key de l'iframe inclut selectedItem?.id : à chaque sélection,
  // l'iframe est recréée et s'ouvre directement à la page du passage.
  const documentViewerUrl =
    preview.objectUrl && isPdf && preview.highlightPage
      ? `${preview.objectUrl}#page=${preview.highlightPage}&zoom=page-width`
      : preview.objectUrl

  return (
    <div className="relative h-[760px] min-w-0 overflow-hidden bg-white">
      {preview.loading ? (
        <div className="flex h-full items-center justify-center">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            Chargement du document complet…
          </div>
        </div>
      ) : preview.error ? (
        <div className="flex h-full items-center justify-center p-6">
          <div className="max-w-sm rounded-xl border border-warning/30 bg-warning/5 p-4 text-center">
            <AlertTriangle className="mx-auto size-5 text-warning" />
            <p className="mt-2 text-sm font-medium text-foreground">
              Prévisualisation indisponible
            </p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {preview.error}
            </p>
          </div>
        </div>
      ) : isImage && preview.objectUrl ? (
        <div className="h-full overflow-auto p-4">
          <img
            src={preview.objectUrl}
            alt={documentName}
            className="mx-auto max-w-full rounded-lg border"
          />
        </div>
      ) : documentViewerUrl ? (
        <iframe
          key={`${documentViewerUrl}:${selectedItem?.id || "document"}:${side}`}
          src={documentViewerUrl}
          title={documentName}
          className="h-full w-full border-0 bg-white"
        />
      ) : null}
    </div>
  )
}

function DocumentComparisonSideBySideV201({
  summary,
  comparison,
  projectId,
  sourceDocuments,
}: {
  summary: any
  comparison: any
  projectId: number | string
  sourceDocuments: DbSourceDocument[]
}) {
  const itemsByKind = useMemo(
    () => normalizeDocComparisonItemsV201(comparison),
    [comparison]
  )

  const [filter, setFilter] = useState<DocComparisonViewKind>("different")
  const [selectedIndex, setSelectedIndex] = useState(0)

  useEffect(() => {
    setSelectedIndex(0)
  }, [filter, comparison])

  const items = itemsByKind[filter] || []
  const selected = items[selectedIndex] || items[0] || null

  const documentA = cleanDisplayText(String(summary?.doc_a || "Document A"))
  const documentB = cleanDisplayText(String(summary?.doc_b || "Document B"))

  const tabs: Array<{
    key: DocComparisonViewKind
    label: string
    count: number
  }> = [
    {
      key: "different",
      label: "Différences",
      count: itemsByKind.different.length,
    },
    {
      key: "only_a",
      label: "Seulement A",
      count: itemsByKind.only_a.length,
    },
    {
      key: "only_b",
      label: "Seulement B",
      count: itemsByKind.only_b.length,
    },
    {
      key: "identical",
      label: "Commun",
      count: itemsByKind.identical.length,
    },
  ]

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          {tabs.map((tab) => (
            <Button
              key={tab.key}
              type="button"
              size="sm"
              variant={filter === tab.key ? "default" : "outline"}
              className={filter === tab.key ? "bg-brand hover:bg-brand/90" : ""}
              onClick={() => setFilter(tab.key)}
            >
              {tab.label}
              <Badge
                variant="secondary"
                className="ml-1.5 min-w-5 justify-center px-1.5 text-[10px]"
              >
                {tab.count}
              </Badge>
            </Button>
          ))}
        </div>

        {selected ? (
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline">
              Passage {selectedIndex + 1}/{Math.max(items.length, 1)}
            </Badge>
            {selected.score !== null && selected.score !== undefined ? (
              <Badge variant="outline">
                Similarité {formatScore(selected.score)}
              </Badge>
            ) : null}
          </div>
        ) : null}
      </div>

      {items.length === 0 ? (
        <div className="rounded-xl border border-dashed bg-muted/10 p-8 text-center">
          <p className="text-sm font-medium text-foreground">
            Aucun passage dans cette catégorie.
          </p>
        </div>
      ) : (
        <div className="grid min-w-0 gap-4 xl:grid-cols-[250px_minmax(0,1fr)]">
          <aside className="min-w-0 rounded-xl border bg-muted/10 p-3">
            <div className="mb-3 flex items-center justify-between gap-2">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Sélectionner un passage
              </p>
              <Badge variant="outline" className="text-[10px]">
                {items.length}
              </Badge>
            </div>

            <div className="max-h-[820px] space-y-2 overflow-y-auto pr-1">
              {items.map((item, index) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setSelectedIndex(index)}
                  className={`w-full rounded-xl border p-3 text-left transition-colors ${
                    selectedIndex === index
                      ? "border-brand/45 bg-brand/5 shadow-sm"
                      : "bg-white hover:bg-muted/40"
                  }`}
                >
                  <p className="text-xs font-semibold leading-5 text-foreground">
                    {index + 1}. {item.label}
                  </p>

                  <p className="mt-2 line-clamp-3 text-xs leading-5 text-muted-foreground">
                    {item.aText || item.bText || "Passage non disponible"}
                  </p>

                  {item.numericConflict ? (
                    <Badge
                      variant="outline"
                      className="mt-2 border-destructive/30 bg-destructive/10 text-[10px] text-destructive"
                    >
                      Conflit numérique
                    </Badge>
                  ) : null}
                </button>
              ))}
            </div>
          </aside>

          <section className="min-w-0 space-y-3">
            <div className="rounded-xl border bg-muted/10 px-4 py-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Documents complets
              </p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                Les deux vrais documents restent ouverts côte à côte. Lorsque vous
                sélectionnez un passage dans la liste, EnnoSmart recharge les deux
                documents avec le passage correspondant surligné automatiquement.
              </p>
            </div>

            <div className="grid min-w-0 gap-4 lg:grid-cols-2">
              <div className="min-w-0 overflow-hidden rounded-xl border bg-white shadow-sm">
                <div className="border-b bg-warning/[0.055] px-4 py-3">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-warning">
                    Document A
                  </p>
                  <p
                    className="mt-1 truncate text-xs font-medium text-foreground"
                    title={documentA}
                  >
                    {documentA}
                  </p>
                </div>

                <InlineComparisonDocumentViewerV203
                  projectId={projectId}
                  documentName={documentA}
                  sourceDocuments={sourceDocuments}
                  selectedItem={selected}
                  side="a"
                />
              </div>

              <div className="min-w-0 overflow-hidden rounded-xl border bg-white shadow-sm">
                <div className="border-b bg-brand/[0.055] px-4 py-3">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-brand">
                    Document B
                  </p>
                  <p
                    className="mt-1 truncate text-xs font-medium text-foreground"
                    title={documentB}
                  >
                    {documentB}
                  </p>
                </div>

                <InlineComparisonDocumentViewerV203
                  projectId={projectId}
                  documentName={documentB}
                  sourceDocuments={sourceDocuments}
                  selectedItem={selected}
                  side="b"
                />
              </div>
            </div>

            <div className="rounded-xl border bg-white px-4 py-3">
              <p className="text-xs leading-5 text-muted-foreground">
                Légende : surlignage beige = passage correspondant dans A ·
                surlignage violet = passage correspondant dans B. Le reste du
                document reste visible pour conserver tout le contexte.
              </p>
            </div>
          </section>
        </div>
      )}
    </div>
  )
}


async function uploadPreviousCirFinal(projectId: number, year: string, file: File) {
  const token = getAccessToken()

  if (!token) {
    throw new Error("Utilisateur non authentifié.")
  }

  const formData = new FormData()
  formData.append("year", year)
  formData.append("file", file)

  const response = await fetch(`${API_BASE_URL}/projects/${projectId}/cir-previous/upload-final`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
    },
    body: formData,
  })

  const data = await response.json().catch(() => null)

  if (!response.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : "Erreur ajout CIR précédent.")
  }

  return data
}

async function getPreviousCirFinals(projectId: number) {
  const token = getAccessToken()

  if (!token) {
    throw new Error("Utilisateur non authentifié.")
  }

  const response = await fetch(`${API_BASE_URL}/projects/${projectId}/cir-previous`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  const data = await response.json().catch(() => null)

  if (!response.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : "Erreur liste CIR précédents.")
  }

  return Array.isArray(data?.items) ? data.items : []
}

async function getCirFinalConsultantStatus(projectId: number) {
  const token = getAccessToken()

  if (!token) return false

  try {
    const response = await fetch(
      `${API_BASE_URL}/projects/${projectId}/cir-final-consultant/latest`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      }
    )

    if (!response.ok) return false

    const data = await response.json().catch(() => null)
    return Boolean(data && data?.status !== "empty")
  } catch {
    return false
  }
}

async function getDocumentComparePairs(projectId: number, force = false) {
  const token = getAccessToken()

  if (!token) {
    throw new Error("Utilisateur non authentifié.")
  }

  const response = await fetch(
    `${API_BASE_URL}/projects/${projectId}/diagnostic/document-compare/auto-pairs?force=${force ? "true" : "false"}`,
    {
      method: "GET",
      headers: {
        Authorization: `Bearer ${token}`,
      },
    }
  )

  const data = await response.json().catch(() => null)

  if (!response.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : "Erreur comparaison documentaire.")
  }

  return data
}

async function runDocumentCompareAutoPairs(projectId: number) {
  const token = getAccessToken()

  if (!token) {
    throw new Error("Utilisateur non authentifié.")
  }

  const response = await fetch(`${API_BASE_URL}/projects/${projectId}/diagnostic/document-compare/auto-pairs`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      min_similarity: 0.70,
      include_medium: true,
      force: true,
    }),
  })

  const data = await response.json().catch(() => null)

  if (!response.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : "Erreur détection des paires.")
  }

  return data
}

async function compareDocumentPair(projectId: number, pairIndex: number) {
  const token = getAccessToken()

  if (!token) {
    throw new Error("Utilisateur non authentifié.")
  }

  const response = await fetch(`${API_BASE_URL}/projects/${projectId}/diagnostic/document-compare/compare-pair`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      pair_index: pairIndex,
      force: true,
    }),
  })

  const data = await response.json().catch(() => null)

  if (!response.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : "Erreur comparaison de paire.")
  }

  return data
}

async function uploadAndCompareDocumentPair(projectId: number, fileA: File, fileB: File) {
  const token = getAccessToken()

  if (!token) {
    throw new Error("Utilisateur non authentifié.")
  }

  const formData = new FormData()
  formData.append("file_a", fileA)
  formData.append("file_b", fileB)

  const response = await fetch(`${API_BASE_URL}/projects/${projectId}/diagnostic/document-compare/upload-pair`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
    },
    body: formData,
  })

  const data = await response.json().catch(() => null)

  if (!response.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : "Erreur comparaison manuelle.")
  }

  return data
}

async function getScholarLatest(projectId: number) {
  const token = getAccessToken()

  if (!token) {
    throw new Error("Utilisateur non authentifié.")
  }

  const response = await fetch(`${API_BASE_URL}/projects/${projectId}/scholar/latest?compact=true`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  const data = await response.json().catch(() => null)

  if (!response.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : "Erreur EnnoScholar latest.")
  }

  return data
}

async function getArticles(projectId: number) {
  const token = getAccessToken()

  if (!token) {
    throw new Error("Utilisateur non authentifié.")
  }

  const response = await fetch(`${API_BASE_URL}/projects/${projectId}/articles?compact=true`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  const data = await response.json().catch(() => null)

  if (!response.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : "Erreur chargement articles.")
  }

  return Array.isArray(data) ? data : []
}

function scholarDecisionClass(decision: string | null | undefined) {
  const value = String(decision || "").toLowerCase()

  if (value.includes("defendable") || value.includes("défendable")) {
    return "bg-success/10 text-success border-success/30"
  }

  if (value.includes("confirmer")) {
    return "bg-warning/10 text-warning border-warning/30"
  }

  if (value.includes("faible") || value.includes("aucun")) {
    return "bg-destructive/10 text-destructive border-destructive/30"
  }

  return "bg-muted text-muted-foreground border-border"
}

function scholarDecisionLabel(decision: string | null | undefined) {
  const value = String(decision || "")

  if (value === "verrou_scientifiquement_defendable") return "Défendable scientifiquement"
  if (value === "verrou_a_confirmer_par_etat_art") return "À confirmer par état de l’art"
  if (value === "support_scientifique_faible") return "Support scientifique faible"
  if (value === "aucun_article_trouve") return "Aucun article trouvé"

  return value || "Non évalué"
}

function articleValidationFromSource(article: any) {
  return article?.source_json?.verrou_scientific_validation || {}
}


function countByDecision(verrous: VerrouRead[]) {
  return {
    garde: verrous.filter((v) => v.consultant_status === "garde").length,
    rejete: verrous.filter((v) => v.consultant_status === "rejete").length,
    reformuler: verrous.filter((v) => v.consultant_status === "reformuler").length,
    en_attente: verrous.filter((v) => v.consultant_status === "en_attente").length,
  }
}

function extractRunId(data: any) {
  const value =
    data?.id ||
    data?.run_id ||
    data?.diagnostic_run_id ||
    data?.diagnostic_run?.id ||
    data?.run?.id

  const id = Number(value)
  return Number.isFinite(id) ? id : null
}

function pickSection(display: any, keys: string[]) {
  const sections = display?.report_sections || {}

  for (const key of keys) {
    const value = sections?.[key] || display?.[key]
    if (typeof value === "string" && value.trim()) return value.trim()
  }

  return ""
}

function cleanDisplayText(value: string) {
  return String(value || "")
    .replace(/\r\n/g, "\n")
    .replace(/\*\*(.*?)\*\*/g, "$1")
    .replace(/\*\*/g, "")
    .replace(/#{1,6}\s*/g, "")
    .replace(/\u00a0/g, " ")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
}

function cleanListLine(value: string) {
  return cleanDisplayText(value)
    .replace(/^[-*•]+\s*/, "")
    .replace(/^\d+[.)]\s*/, "")
    .replace(/^\|+|\|+$/g, "")
    .replace(/\s+–\s+/g, " — ")
    .trim()
}

function listFromText(text: string, fallback: string[]) {
  const cleaned = cleanDisplayText(text)

  if (!cleaned) return fallback

  const lines = cleaned
    .split(/\n+/)
    .flatMap((line) => {
      const normalized = cleanListLine(line)
      if (!normalized) return []

      if (normalized.includes(" | ")) {
        return normalized
          .split("|")
          .map((part) => cleanListLine(part))
          .filter(Boolean)
      }

      return [normalized]
    })
    .filter((line) => line.length > 4)
    .filter((line) => !line.startsWith("---"))
    .filter((line) => !/^chiffrés?$/i.test(line))
    .filter((line) => !/^qualitatifs?$/i.test(line))
    .slice(0, 9)

  return lines.length > 0 ? lines : [cleaned]
}

async function postDiagnosticAction(projectId: number, action: "prepare-sources" | "run-agent") {
  const token = getAccessToken()

  if (!token) {
    throw new Error("Utilisateur non authentifié.")
  }

  const response = await fetch(`${API_BASE_URL}/projects/${projectId}/diagnostic/${action}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  })

  let data: any = null

  try {
    data = await response.json()
  } catch {
    data = null
  }

  if (!response.ok) {
    const detail =
      typeof data?.detail === "string"
        ? data.detail
        : Array.isArray(data?.detail)
          ? data.detail.map((item: any) => item.msg || item.type || item).join(" | ")
          : "Erreur API."

    throw new Error(detail)
  }

  return data
}

function formatDocumentSize(value: number) {
  const bytes = Math.max(0, Number(value || 0))
  if (bytes < 1024) return `${bytes} o`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} Ko`
  return `${(bytes / (1024 * 1024)).toFixed(1)} Mo`
}

async function compareCurrentWithPreviousCir(projectId: number) {
  const token = getAccessToken()

  if (!token) {
    throw new Error("Utilisateur non authentifié.")
  }

  const response = await fetch(`${API_BASE_URL}/projects/${projectId}/cir-previous/compare-current`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  })

  const data = await response.json().catch(() => null)

  if (!response.ok) {
    throw new Error(
      typeof data?.detail === "string"
        ? data.detail
        : "Erreur comparaison CIR précédent."
    )
  }

  return data
}

async function getPreviousCirComparisonLatest(projectId: number) {
  const token = getAccessToken()

  if (!token) {
    throw new Error("Utilisateur non authentifié.")
  }

  const response = await fetch(`${API_BASE_URL}/projects/${projectId}/cir-previous/comparison-latest`, {
    method: "GET",
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  const data = await response.json().catch(() => null)

  if (!response.ok) {
    throw new Error(
      typeof data?.detail === "string"
        ? data.detail
        : "Erreur lecture comparaison CIR précédent."
    )
  }

  return data
}

export function DiagnosisPage(
  { onOpenScholar }: { onOpenScholar?: () => void } = {}
) {
  const [activeTab, setActiveTab] = useState("overview")
  const [diagnosticSection, setDiagnosticSection] = useState<DiagnosticSubsection>("objectif")
  const [loading, setLoading] = useState(true)
  const [actionLoadingId, setActionLoadingId] = useState<number | null>(null)
  const [error, setError] = useState("")
  const [project, setProject] = useState<ProjectRead | null>(null)
  const [projects, setProjects] = useState<ProjectRead[]>([])
  const [documents, setDocuments] = useState<DocumentRead[]>([])
  const [diagnosticCorpusReview, setDiagnosticCorpusReview] = useState<DiagnosticCorpusReview | null>(null)
  const [corpusReviewOpen, setCorpusReviewOpen] = useState(false)
  const [corpusReviewSaving, setCorpusReviewSaving] = useState(false)
  const [corpusKeepIds, setCorpusKeepIds] = useState<Set<number>>(new Set())
  const [verrous, setVerrous] = useState<VerrouRead[]>([])
  const [diagnosticBundle, setDiagnosticBundle] = useState<any>(null)
  const [prepareReport, setPrepareReport] = useState<any>(null)
  const [documentCompareIndex, setDocumentCompareIndex] = useState<any>(null)
  const [documentCompareReport, setDocumentCompareReport] = useState<any>(null)
  const [documentCompareLoading, setDocumentCompareLoading] = useState(false)
  const [selectedPairIndex, setSelectedPairIndex] = useState<number | null>(null)
  const [manualFileA, setManualFileA] = useState<File | null>(null)
  const [manualFileB, setManualFileB] = useState<File | null>(null)
  const [previousCirFile, setPreviousCirFile] = useState<File | null>(null)
  const [previousCirYear, setPreviousCirYear] = useState("")
  const [previousCirUploading, setPreviousCirUploading] = useState(false)
  const [previousCirList, setPreviousCirList] = useState<any[]>([])
  const [previousCirUploadReport, setPreviousCirUploadReport] = useState<any>(null)
  const [cirPreviousComparisonReport, setCirPreviousComparisonReport] = useState<any>(null)
  const [cirPreviousCompareLoading, setCirPreviousCompareLoading] = useState(false)
  const [previousCirComparisonReport, setPreviousCirComparisonReport] = useState<any>(null)
  const [previousCirCompareLoading, setPreviousCirCompareLoading] = useState(false)
  const [scholarBundle, setScholarBundle] = useState<any>(null)
  const [articles, setArticles] = useState<any[]>([])
  const [cirFinalRegistered, setCirFinalRegistered] = useState(false)
  const [manualFormOpen, setManualFormOpen] = useState(false)
  const [manualTitle, setManualTitle] = useState("")
  const [manualDescription, setManualDescription] = useState("")
  const [manualKeywords, setManualKeywords] = useState("")
  const [manualSubmitting, setManualSubmitting] = useState(false)
  const [manualFeedback, setManualFeedback] = useState("")
  const [manualFormError, setManualFormError] = useState("")

  const [runningMode, setRunningMode] = useState<RunMode>(null)

  const running = runningMode !== null

  const display = diagnosticBundle?.display || {}
  const latestRun = diagnosticBundle?.latest_run || null
  const reportMarkdown = display?.report_markdown || ""
  const reportSections = display?.report_sections || {}

  const pipelineStats = useMemo(() => {
    const fromLatest = display?.pipeline_stats || {}
    const fromPrepare = {
      documents_loaded_count: prepareReport?.documents_loaded_count,
      raw_candidates: prepareReport?.nlp_stats?.raw_candidates,
      raw_kept: prepareReport?.nlp_stats?.raw_kept,
      merged_verrous: prepareReport?.nlp_stats?.merged_verrous,
      chunks_prepared: prepareReport?.index_report?.chunks_prepared,
      chunks_indexed: prepareReport?.index_report?.chunks_indexed,
    }

    return {
      ...fromPrepare,
      ...Object.fromEntries(
        Object.entries(fromLatest).filter(([, value]) => value !== null && value !== undefined)
      ),
    }
  }, [display, prepareReport])

  const frascatiScore =
    display?.frascati_summary?.rnd_defensibility_index ??
    diagnosticBundle?.frascati_summary?.rnd_defensibility_index ??
    prepareReport?.nlp_stats?.rnd_defensibility_index ??
    display?.frascati_summary?.eligibility_assessment_score ??
    diagnosticBundle?.frascati_summary?.eligibility_assessment_score ??
    prepareReport?.nlp_stats?.eligibility_assessment_score ??
    display?.frascati_summary?.average_frascati_score ??
    display?.frascati_score ??
    prepareReport?.nlp_stats?.global_frascati_score ??
    null

  const frascatiDocumentaryCoverage =
    display?.frascati_summary?.documentary_coverage ??
    diagnosticBundle?.frascati_summary?.documentary_coverage ??
    display?.frascati_summary?.average_frascati_score ??
    diagnosticBundle?.frascati_summary?.average_frascati_score ??
    prepareReport?.nlp_stats?.global_frascati_score ??
    null

  const demarcheAudit =
    display?.frascati_summary?.demarche_legibility ||
    diagnosticBundle?.frascati_summary?.demarche_legibility ||
    diagnosticBundle?.demarche_legibility ||
    prepareReport?.demarche_legibility ||
    {}

  const frascatiRisk =
    display?.frascati_summary?.risk_level ||
    display?.frascati_risk_level ||
    prepareReport?.nlp_stats?.frascati_risk_level ||
    "moyen"

  const aiScore = display?.ai_score ?? display?.ai_detection?.summary?.average_ai_percentage ?? null
  const aiRisk = display?.ai_risk_level ?? display?.ai_detection?.summary?.risk_level ?? "—"
  const aiPassages = display?.ai_suspected_passages || []

  const styleMemory = display?.style_memory || {}
  const styleMemoryOk = Boolean(display?.style_memory_ok || styleMemory?.ok)
  const styleExamplesCount = Number(display?.style_memory_examples_count ?? styleMemory?.examples_count ?? 0)
  const styleRoles = display?.style_memory_roles || styleMemory?.examples_by_role_count || {}
  const styleStats = display?.style_memory_stats || styleMemory?.stats || {}

  const cirMemory = useMemo(
    () => chooseCirMemoryReport(
      cirPreviousComparisonReport,
      previousCirComparisonReport,
      display?.cir_memory_report,
      display?.cir_memory,
      display,
      diagnosticBundle?.cir_memory_report,
      diagnosticBundle
    ),
    [
      cirPreviousComparisonReport,
      previousCirComparisonReport,
      display,
      diagnosticBundle,
    ]
  )
  const cirMemoryOk = Boolean(
    display?.cir_memory_ok ||
    cirMemory?.ok ||
    cirMemory?.has_previous_cir ||
    cirMemory?.previous_cir_available ||
    display?.inputs_status?.previous_cir_available
  )
  const cirMemoryHasPrevious = Boolean(
    display?.cir_memory_has_previous ||
    cirMemory?.has_previous_cir ||
    cirMemory?.previous_cir_available ||
    display?.inputs_status?.previous_cir_available ||
    diagnosticBundle?.inputs_status?.previous_cir_available
  )
  const cirMemorySummary = display?.cir_memory_summary || cirMemory?.summary || {}
  const cirMemoryPreviousYears = firstNonEmptyArray(display?.cir_memory_previous_years, cirMemory?.previous_cir_years_used, cirMemory?.previous_years)
  const cirMemoryNoveltyScore = display?.cir_memory_project_novelty_score ?? cirMemorySummary?.project_novelty_score
  const cirMemorySignal = display?.cir_memory_signal || cirMemorySummary?.frascati_context_signal
  const cirMemoryExplanation = display?.cir_memory_explanation || cirMemorySummary?.frascati_context_explanation
  const cirMemoryNewVerrous = firstNonEmptyArray(display?.cir_memory_new_verrous, cirMemory?.new_or_not_found)
  const cirMemoryEvolutions = firstNonEmptyArray(display?.cir_memory_evolutions, cirMemory?.evolution_or_partial_continuity)
  const cirMemoryContinuities = firstNonEmptyArray(display?.cir_memory_continuities, cirMemory?.continuity_strong)
  // Un tableau vide est truthy en JavaScript : utiliser || masquait donc les
  // comparaisons réellement remplies par comparison-latest.
  const cirMemoryComparisons = firstNonEmptyArray(
    display?.cir_memory_verrou_comparisons,
    cirMemory?.verrou_comparisons,
    cirMemory?.comparisons,
    unwrapCirPreviousReportForDisplay(previousCirComparisonReport)?.verrou_comparisons,
    unwrapCirPreviousReportForDisplay(previousCirComparisonReport)?.comparisons,
    unwrapCirPreviousReportForDisplay(cirPreviousComparisonReport)?.verrou_comparisons,
    unwrapCirPreviousReportForDisplay(cirPreviousComparisonReport)?.comparisons
  )

  // V156 - Le CIR précédent peut être détecté par plusieurs sources :
  // liste des CIR enregistrés, rapport de comparaison sauvegardé, mémoire CIR
  // exposée par EnnoDiagnostic ou résultat d'un upload courant.
  // La valeur du champ de formulaire `previousCirYear` ne suffit jamais, à elle
  // seule, pour considérer qu'un CIR précédent existe réellement.
  const previousCirDetectedYears = useMemo(() => {
    const latestReport = unwrapCirPreviousReportForDisplay(
      previousCirComparisonReport
    )
    const runtimeReport = unwrapCirPreviousReportForDisplay(
      cirPreviousComparisonReport
    )

    const candidates = [
      ...previousCirList.map((item: any) =>
        String(item?.year || item?.previous_cir_year || "").trim()
      ),
      ...(Array.isArray(cirMemoryPreviousYears)
        ? cirMemoryPreviousYears.map((year: any) =>
            String(year || "").trim()
          )
        : []),
      ...(Array.isArray(latestReport?.previous_cir_years_used)
        ? latestReport.previous_cir_years_used.map((year: any) =>
            String(year || "").trim()
          )
        : []),
      ...(Array.isArray(latestReport?.previous_years)
        ? latestReport.previous_years.map((year: any) =>
            String(year || "").trim()
          )
        : []),
      ...(Array.isArray(runtimeReport?.previous_cir_years_used)
        ? runtimeReport.previous_cir_years_used.map((year: any) =>
            String(year || "").trim()
          )
        : []),
      ...(Array.isArray(runtimeReport?.previous_years)
        ? runtimeReport.previous_years.map((year: any) =>
            String(year || "").trim()
          )
        : []),
      String(previousCirUploadReport?.previous_cir_year || "").trim(),
    ].filter(Boolean)

    return Array.from(new Set(candidates))
  }, [
    previousCirList,
    previousCirUploadReport,
    cirMemoryPreviousYears,
    previousCirComparisonReport,
    cirPreviousComparisonReport,
  ])

  const previousCirAvailable = useMemo(() => {
    const latestReport = unwrapCirPreviousReportForDisplay(
      previousCirComparisonReport
    )
    const runtimeReport = unwrapCirPreviousReportForDisplay(
      cirPreviousComparisonReport
    )

    return Boolean(
      previousCirList.length > 0 ||
      previousCirUploadReport ||
      cirMemoryHasPrevious ||
      cirMemory?.has_previous_cir === true ||
      display?.cir_memory_has_previous === true ||
      display?.inputs_status?.previous_cir_available === true ||
      diagnosticBundle?.inputs_status?.previous_cir_available === true ||
      previousCirDetectedYears.length > 0 ||
      latestReport?.has_previous_cir === true ||
      latestReport?.previous_cir_available === true ||
      runtimeReport?.has_previous_cir === true ||
      runtimeReport?.previous_cir_available === true ||
      firstNonEmptyArray(
        latestReport?.registered_previous_cirs,
        runtimeReport?.registered_previous_cirs,
        cirMemory?.registered_previous_cirs
      ).length > 0
    )
  }, [
    previousCirList,
    previousCirUploadReport,
    cirMemoryHasPrevious,
    cirMemory,
    display,
    previousCirDetectedYears,
    previousCirComparisonReport,
    cirPreviousComparisonReport,
  ])

  const previousCirYearsAvailable = useMemo(() => {
    if (previousCirDetectedYears.length > 0) {
      return previousCirDetectedYears
    }

    const uploadYear = String(
      previousCirUploadReport?.previous_cir_year ||
      previousCirYear ||
      ""
    ).trim()

    return uploadYear ? [uploadYear] : []
  }, [
    previousCirDetectedYears,
    previousCirUploadReport,
    previousCirYear,
  ])

  const previousCirItemsCount = useMemo(() => {
    const fromList = previousCirList.reduce((total: number, item: any) => {
      return total + Number(item?.items_count || item?.passages_count || item?.count || 0)
    }, 0)

    return fromList || Number(previousCirUploadReport?.items_count || 0)
  }, [previousCirList, previousCirUploadReport])

  const cirContinuityDiagnostic = useMemo(() => {
    const hasPreviousCir = cirMemoryHasPrevious || previousCirAvailable || Boolean(cirMemory?.has_previous_cir)
    const previousYears =
      Array.isArray(cirMemoryPreviousYears) && cirMemoryPreviousYears.length > 0
        ? cirMemoryPreviousYears
        : (previousCirComparisonReport?.previous_cir_years_used || previousCirComparisonReport?.previous_years || previousCirYearsAvailable)

    const normalizedSummary = {
      ...(cirMemorySummary || {}),
      has_previous_cir: hasPreviousCir,
      previous_cir_available: hasPreviousCir,
      previous_years: previousYears,
      previous_cir_years_used: previousYears,
      registered_previous_cirs_count: previousCirList.length,
      registered_memory_items_count: previousCirItemsCount,
    }

    return {
      ...(diagnosticBundle || {}),
      ...(display || {}),

      // Champs top-level lus par certains composants anciens
      has_previous_cir: hasPreviousCir,
      previous_cir_available: hasPreviousCir,
      previous_cir_years_used: previousYears,
      previous_years: previousYears,
      registered_previous_cirs: previousCirList,

      cir_memory_ok: cirMemoryOk || hasPreviousCir,
      cir_memory_has_previous: hasPreviousCir,
      cir_memory_previous_years: previousYears,
      cir_memory_summary: normalizedSummary,
      cir_memory_project_novelty_score: cirMemoryNoveltyScore,
      cir_memory_signal: cirMemorySignal,
      cir_memory_explanation: cirMemoryExplanation,
      cir_memory_new_verrous: cirMemoryNewVerrous,
      cir_memory_evolutions: cirMemoryEvolutions,
      cir_memory_continuities: cirMemoryContinuities,
      cir_memory_verrou_comparisons: cirMemoryComparisons,

      cir_memory_report: {
        ...(cirMemory || {}),
        ok: cirMemoryOk || hasPreviousCir,
        has_previous_cir: hasPreviousCir,
        previous_cir_available: hasPreviousCir,
        previous_cir_years_used: previousYears,
        previous_years: previousYears,
        registered_previous_cirs: previousCirList,
        registered_previous_cirs_count: previousCirList.length,
        registered_memory_items_count: previousCirItemsCount,
        summary: normalizedSummary,
        project_novelty_score: cirMemoryNoveltyScore,
        frascati_context_signal: cirMemorySignal,
        frascati_context_explanation: cirMemoryExplanation,
        new_or_not_found: cirMemoryNewVerrous,
        evolution_or_partial_continuity: cirMemoryEvolutions,
        continuity_strong: cirMemoryContinuities,
        verrou_comparisons: cirMemoryComparisons,
        comparisons: cirMemoryComparisons,
      },
    }
  }, [
    diagnosticBundle,
    display,
    cirMemory,
    cirMemoryOk,
    cirMemoryHasPrevious,
    cirMemorySummary,
    cirMemoryPreviousYears,
    cirMemoryNoveltyScore,
    cirMemorySignal,
    cirMemoryExplanation,
    cirMemoryNewVerrous,
    cirMemoryEvolutions,
    cirMemoryContinuities,
    cirMemoryComparisons,
    previousCirAvailable,
    previousCirYearsAvailable,
    previousCirItemsCount,
    previousCirList,
  ])


  const documentCompareDisplay = display?.document_compare || {}
  const docCompareIndex = documentCompareIndex || documentCompareDisplay || {}
  const docComparePairs = docCompareIndex?.pairs || display?.document_compare_pairs || []
  const docComparePairsCount = Number(docCompareIndex?.pairs_count ?? display?.document_compare_pairs_count ?? docComparePairs.length ?? 0)
  const docCompareSummary = documentCompareReport?.summary || {}
  const docCompareComparison = documentCompareReport?.comparison || {}

  const selectedVerrousForScholar = verrous.filter((verrou) => verrou.consultant_status === "garde")
  const scholarReport = scholarBundle?.bundle?.report || {}
  const scholarSummary = scholarBundle?.bundle?.summary || {}
  const scholarResults = scholarReport?.results || []
  const scholarDecisionCounts = scholarSummary?.decision_counts || scholarReport?.decision_counts || {}
  const scholarRunId = scholarBundle?.latest_run?.id
  const scholarPayload = scholarBundle?.bundle?.payload || {}
  const scholarGroupingSummary = scholarPayload?.grouping_summary || scholarReport?.grouping_summary || scholarSummary?.grouping_summary || {}
  const scholarGroupingGroups = scholarPayload?.grouping_report?.groups || scholarReport?.grouping_report?.groups || scholarSummary?.grouping_report?.groups || []
  const scholarGroupingActive = Boolean(
    scholarGroupingSummary?.active ||
    scholarGroupingSummary?.grouping_applied ||
    scholarGroupingGroups.length > 0
  )

  const scholarArticlesCount = useMemo(() => {
    const results = Array.isArray(scholarResults) ? scholarResults : []

    const fromResults = results.reduce((sum: number, result: any) => {
      const candidates = [
        result?.articles_found,
        result?.articles_count,
        result?.raw_articles_retrieved,
        Array.isArray(result?.articles) ? result.articles.length : null,
      ]

      const value = candidates
        .map((item) => Number(item))
        .find((item) => Number.isFinite(item) && item > 0)

      return sum + (value || 0)
    }, 0)

    const summaryCandidates = [
      scholarSummary?.articles_total,
      scholarSummary?.articles_found_total,
      scholarSummary?.articles_found,
      scholarSummary?.raw_articles_retrieved,
      scholarReport?.articles_total,
      scholarReport?.articles_found_total,
      scholarPayload?.articles_total,
      scholarPayload?.articles_found_total,
    ]

    const fromSummary = summaryCandidates
      .map((item) => Number(item))
      .find((item) => Number.isFinite(item) && item > 0)

    // articles.length est volontairement en dernier : il peut contenir
    // l'ancien historique DB si le backend n'est pas encore corrigé.
    return Number(fromResults || fromSummary || articles.length || 0)
  }, [scholarResults, scholarSummary, scholarReport, scholarPayload, articles.length])


  const decisions = useMemo(() => countByDecision(verrous), [verrous])
  const decisionSegments = useMemo(() => [
    {
      key: "garde",
      label: "Retenus",
      count: decisions.garde,
      color: "bg-success",
      dot: "bg-success",
    },
    {
      key: "reformuler",
      label: "À consolider",
      count: decisions.reformuler,
      color: "bg-brand",
      dot: "bg-brand",
    },
    {
      key: "rejete",
      label: "Non retenus",
      count: decisions.rejete,
      color: "bg-destructive/75",
      dot: "bg-destructive/75",
    },
    {
      key: "en_attente",
      label: "En attente",
      count: decisions.en_attente,
      color: "bg-warning",
      dot: "bg-warning",
    },
  ], [decisions])
  const frascatiPercent = scorePercent(frascatiScore)
  const sourceDocuments = useProjectSourceDocuments(project?.id)

  const backendMarkdownV93 = useMemo(() => {
    return getBackendDiagnosticMarkdownV93(diagnosticBundle, display)
  }, [diagnosticBundle, display])

  const backendSectionsV93 = useMemo(() => {
    return getBackendDiagnosticSectionsV93(diagnosticBundle, display)
  }, [diagnosticBundle, display])

  const eligibilityEvidenceReport = useMemo(() => {
    return getEligibilityEvidenceReportV153(diagnosticBundle, display)
  }, [diagnosticBundle, display])

  const eligibilityProofClaims = useMemo(() => {
    return getEligibilityProofClaimsV153(diagnosticBundle, display)
  }, [diagnosticBundle, display])

  const summary = useMemo(() => {
    return (
      pickBackendSectionV93(backendSectionsV93, backendMarkdownV93, [
        "Synthèse stratégique du projet",
        "Synthèse stratégique",
      ]) ||
      cleanDisplayText(pickSection(display, ["synthese", "synthèse", "synthese_strategique", "summary"]) || "") ||
      "La synthèse stratégique apparaîtra après l’exécution d’EnnoDiagnostic."
    )
  }, [backendSectionsV93, backendMarkdownV93, display])

  const objective = useMemo(() => {
    return (
      pickBackendSectionV93(backendSectionsV93, backendMarkdownV93, [
        "Objectif global reformulé",
        "Objectif global",
      ]) ||
      cleanDisplayText(pickSection(display, ["objectif", "objectif_global", "objective"]) || "") ||
      "L’objectif global apparaîtra après l’exécution d’EnnoDiagnostic."
    )
  }, [backendSectionsV93, backendMarkdownV93, display])

  const lectureFrascatiText = useMemo(() => {
    const merged = pickBackendSectionV93(backendSectionsV93, backendMarkdownV93, [
      "Étude d'éligibilité",
      "Analyse Frascati",
    ])

    if (merged) {
      const beforeJustification = merged.split(/Justification projet-spécifique|Analyse approfondie reliée aux preuves/i)[0]
      return beforeJustification
        .replace(/^Lecture du score\s*/i, "")
        .trim()
    }

    return pickBackendSectionV93(backendSectionsV93, backendMarkdownV93, [
      "Lecture Frascati du dossier",
    ])
  }, [backendSectionsV93, backendMarkdownV93])

  const frascatiJustificationText = useMemo(() => {
    const explicit = getBackendFrascatiJustificationV94(
      diagnosticBundle,
      backendSectionsV93,
      backendMarkdownV93
    )
    if (explicit) return explicit

    const merged = pickBackendSectionV93(backendSectionsV93, backendMarkdownV93, [
      "Étude d'éligibilité",
      "Analyse Frascati",
    ])
    const parts = merged.split(/Justification projet-spécifique|Analyse approfondie reliée aux preuves/i)
    return parts.length > 1 ? parts.slice(1).join(" ").trim() : ""
  }, [diagnosticBundle, backendSectionsV93, backendMarkdownV93])

  const verrousForDisplay = useMemo(() => {
    return collectBackendDisplayVerrousV139(
      diagnosticBundle,
      display,
      verrous
    )
  }, [diagnosticBundle, display, verrous])

  const demarcheText = useMemo(() => {
    return pickBackendSectionV93(backendSectionsV93, backendMarkdownV93, [
      "Démarche expérimentale détectée",
      "Démarche détectée",
      "Démarche expérimentale",
    ])
  }, [backendSectionsV93, backendMarkdownV93])

  const resultatsText = useMemo(() => {
    return pickBackendSectionV93(backendSectionsV93, backendMarkdownV93, [
      "Résultats et métriques disponibles",
      "Résultats / métriques",
      "Résultats",
    ])
  }, [backendSectionsV93, backendMarkdownV93])

  const parametresText = useMemo(() => {
    return pickBackendSectionV93(backendSectionsV93, backendMarkdownV93, [
      "Paramètres et contraintes techniques",
      "Paramètres techniques",
    ])
  }, [backendSectionsV93, backendMarkdownV93])

  const demarcheStructuredSectionV194 = useMemo(() => {
    return getBackendSectionPayloadV194(diagnosticBundle, display, "demarche_detectee")
  }, [diagnosticBundle, display])

  const resultatsStructuredSectionV194 = useMemo(() => {
    return getBackendSectionPayloadV194(diagnosticBundle, display, "resultats_metriques")
  }, [diagnosticBundle, display])

  const parametresStructuredSectionV194 = useMemo(() => {
    return getBackendSectionPayloadV194(diagnosticBundle, display, "parametres_contraintes")
  }, [diagnosticBundle, display])


  const hasDiagnostic = Boolean(backendMarkdownV93 || latestRun || verrousForDisplay.length > 0 || reportMarkdown)

  const pendingImprovementDocuments =
    diagnosticCorpusReview?.pending_improvement_documents || []
  const diagnosticDocumentsCount =
    diagnosticCorpusReview?.diagnostic_documents.length ?? documents.length

  const applyDiagnosticCorpusReview = (review: DiagnosticCorpusReview | null) => {
    setDiagnosticCorpusReview(review)
    const pending = review?.pending_improvement_documents || []
    setCorpusKeepIds(new Set())
    setCorpusReviewOpen(pending.length > 0)
  }

  const reviewedVerrousCount =
    decisions.garde + decisions.reformuler + decisions.rejete
  const pendingReviewCount = decisions.en_attente
  const reviewProgress =
    verrousForDisplay.length > 0
      ? Math.round((reviewedVerrousCount / verrousForDisplay.length) * 100)
      : 0

  const nextDiagnosticAction = !hasDiagnostic
    ? "Préparer les sources puis lancer EnnoDiagnostic"
    : pendingReviewCount > 0
      ? `Examiner ${pendingReviewCount} verrou(s) encore en attente`
      : selectedVerrousForScholar.length > 0
        ? `Passer ${selectedVerrousForScholar.length} verrou(s) retenu(s) à EnnoScholar`
        : "Relire la synthèse et finaliser les décisions"

  const loadData = async () => {
    // V12 : ouverture rapide SANS vider les anciens résultats.
    // Le correctif V11 libérait l’interface trop tôt et faisait un reset des states,
    // donc la page s’ouvrait mais les onglets semblaient vides jusqu’au retour API.
    // Ici : au premier chargement on attend seulement les données principales,
    // puis les blocs lourds secondaires se chargent en arrière-plan.
    const isInitialLoad = project === null

    if (isInitialLoad) {
      setLoading(true)
    }

    setError("")

    try {
      const projectList = await getProjects()
      setProjects(projectList)

      if (projectList.length === 0) {
        setProject(null)
        setVerrous([])
        setDocuments([])
        setDiagnosticBundle(null)
        setScholarBundle(null)
        setArticles([])
        setCirFinalRegistered(false)
        setPreviousCirList([])
        setDocumentCompareIndex(null)
        setPreviousCirComparisonReport(null)
        setCirPreviousComparisonReport(null)
        setLoading(false)
        return
      }

      const storedProjectId = getCurrentProjectId()
      const selectedProject =
        projectList.find((item) => item.id === storedProjectId) || projectList[0]

      setCurrentProjectId(selectedProject.id)
      setProject(selectedProject)

      const previousYear = Number(selectedProject.year)
      if (!previousCirYear) {
        setPreviousCirYear(Number.isFinite(previousYear) ? String(previousYear - 1) : "")
      }

      // Le diagnostic officiel contient déjà les verrous décisionnels. L'ancien
      // chargement appelait /verrous en parallèle et relisait deux fois le même
      // gros rapport PostgreSQL/fichier.
      const [documentsData, diagnosticData, corpusReviewData] = await Promise.all([
        getDocuments(selectedProject.id).catch(() => []),
        getDiagnosticLatest(selectedProject.id).catch(() => null),
        getDiagnosticCorpusReview(selectedProject.id).catch(() => null),
      ])

      const diagnosticVerrous =
        diagnosticData?.validation_verrous ||
        diagnosticData?.display?.validation_verrous ||
        []
      setVerrous(Array.isArray(diagnosticVerrous) ? diagnosticVerrous : [])
      setDocuments(Array.isArray(documentsData) ? documentsData : [])
      setDiagnosticBundle(diagnosticData)
      applyDiagnosticCorpusReview(corpusReviewData)
      setLoading(false)

      void getCirFinalConsultantStatus(selectedProject.id)
        .then(setCirFinalRegistered)
        .catch(() => setCirFinalRegistered(false))

      // Données secondaires : elles ne bloquent pas l’affichage du diagnostic.
      Promise.all([
        getScholarLatest(selectedProject.id).catch(() => null),
        getArticles(selectedProject.id).catch(() => []),
        getDocumentComparePairs(selectedProject.id, false).catch(() => null),
        getPreviousCirFinals(selectedProject.id).catch(() => []),
        getPreviousCirComparisonLatest(selectedProject.id).catch(() => null),
      ])
        .then(([scholarData, articlesData, compareIndexData, previousCirData, previousCirComparisonData]) => {
          setScholarBundle(scholarData)
          setArticles(Array.isArray(articlesData) ? articlesData : [])
          setDocumentCompareIndex(compareIndexData)
          setPreviousCirList(Array.isArray(previousCirData) ? previousCirData : [])
          setPreviousCirComparisonReport(previousCirComparisonData)
          setCirPreviousComparisonReport(previousCirComparisonData)
        })
        .catch(() => undefined)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de charger EnnoDiagnostic."
      )
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  const submitManualVerrou = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!project || manualSubmitting) return

    const title = manualTitle.trim()
    const description = manualDescription.trim()
    const keywords = Array.from(
      new Set(
        manualKeywords
          .split(/[,;\n]+/)
          .map((keyword) => keyword.trim())
          .filter(Boolean)
      )
    ).slice(0, 20)

    if (title.length < 5) {
      setManualFormError("Le titre doit contenir au moins 5 caractères.")
      return
    }

    setManualSubmitting(true)
    setManualFormError("")
    setManualFeedback("")
    try {
      const created = await createManualVerrou(project.id, {
        title,
        description,
        keywords,
      })
      setVerrous((current) => {
        const withoutDuplicate = current.filter((item) => item.id !== created.id)
        return [created, ...withoutDuplicate]
      })
      setManualTitle("")
      setManualDescription("")
      setManualKeywords("")
      setManualFormOpen(false)
      setManualFeedback(
        `« ${created.title} » est retenu et prêt pour EnnoScholar.`
      )
    } catch (err) {
      setManualFormError(
        err instanceof Error
          ? err.message
          : "Impossible d’ajouter le verrou manuel."
      )
    } finally {
      setManualSubmitting(false)
    }
  }

  const currentSteps =
    runningMode === "prepare"
      ? prepareSteps
      : runningMode === "agent"
        ? agentSteps
        : fullSteps

  const startProgress = (mode: RunMode) => {
    setRunningMode(mode)
  }

  const stopProgress = (_success = true) => undefined

  const prepareSources = async () => {
    if (!project) return

    setError("")
    setActiveTab("overview")
    startProgress("prepare")

    try {
      const result = await postDiagnosticAction(project.id, "prepare-sources")
      setPrepareReport(result)
      await loadData()
      stopProgress(true)
    } catch (err) {
      stopProgress(false)
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de préparer les sources EnnoDiagnostic."
      )
    } finally {
      setTimeout(() => setRunningMode(null), 700)
    }
  }

  const runAgentOnly = async () => {
    if (!project) return

    setError("")
    setActiveTab("overview")
    startProgress("agent")

    try {
      const runResult = await postDiagnosticAction(project.id, "run-agent")
      const runId = extractRunId(runResult)

      if (runId) {
        await syncVerrous(project.id, runId).catch(() => null)
      }

      await loadData()
      stopProgress(true)
    } catch (err) {
      stopProgress(false)
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de lancer l’agent EnnoDiagnostic."
      )
    } finally {
      setTimeout(() => setRunningMode(null), 700)
    }
  }

  const runFullDiagnostic = async () => {
    if (!project) return

    setError("")
    setActiveTab("overview")
    startProgress("full")

    try {
      const runResult = await runDiagnostic(project.id)
      const runId = extractRunId(runResult)

      if (runId) {
        await syncVerrous(project.id, runId).catch(() => null)
      }

      await loadData()
      stopProgress(true)
    } catch (err) {
      stopProgress(false)
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de lancer EnnoDiagnostic complet."
      )
    } finally {
      setTimeout(() => setRunningMode(null), 700)
    }
  }

  const prepareSourcesAndRunDiagnostic = async () => {
    if (!project) return

    setError("")
    setActiveTab("overview")
    startProgress("full")

    try {
      const prepared = await postDiagnosticAction(project.id, "prepare-sources")
      setPrepareReport(prepared)
      const runResult = await postDiagnosticAction(project.id, "run-agent")
      const runId = extractRunId(runResult)
      if (runId) {
        await syncVerrous(project.id, runId).catch(() => null)
      }
      await loadData()
      stopProgress(true)
    } catch (err) {
      stopProgress(false)
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de préparer puis relancer EnnoDiagnostic."
      )
    } finally {
      setTimeout(() => setRunningMode(null), 700)
    }
  }

  const saveCorpusReview = async (runAfterSave: boolean) => {
    if (!project || corpusReviewSaving) return
    setCorpusReviewSaving(true)
    setError("")
    try {
      const updated = await updateDiagnosticCorpusReview(
        project.id,
        pendingImprovementDocuments.map((document) => ({
          document_id: document.id,
          keep: corpusKeepIds.has(document.id),
        })),
      )
      setDiagnosticCorpusReview(updated)
      setCorpusReviewOpen(false)
      setCorpusKeepIds(new Set())
      if (runAfterSave) {
        await prepareSourcesAndRunDiagnostic()
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible d’enregistrer la sélection du corpus Diagnostic."
      )
    } finally {
      setCorpusReviewSaving(false)
    }
  }

  const importExisting = async () => {
    if (!project) return

    setError("")
    startProgress("import")

    try {
      const imported = await importExistingDiagnostic(project.id)
      const runId = extractRunId(imported)

      if (runId) {
        await syncVerrous(project.id, runId).catch(() => null)
      }

      await loadData()
      stopProgress(true)
    } catch (err) {
      stopProgress(false)
      setError(
        err instanceof Error
          ? err.message
          : "Impossible d’importer le diagnostic existant."
      )
    } finally {
      setTimeout(() => setRunningMode(null), 700)
    }
  }

  const changeProject = async (projectId: number) => {
    setCurrentProjectId(projectId)
    setLoading(true)
    setError("")
    setPrepareReport(null)
    setCorpusReviewOpen(false)
    setDiagnosticCorpusReview(null)

    try {
      const selectedProject = projects.find((item) => item.id === projectId) || null
      setProject(selectedProject)

      setVerrous([])
      setDocuments([])
      setDiagnosticBundle(null)
      setDocumentCompareIndex(null)
      setDocumentCompareReport(null)
      setSelectedPairIndex(null)
      setScholarBundle(null)
      setArticles([])
      setCirFinalRegistered(false)
      setPreviousCirList([])
      setPreviousCirComparisonReport(null)
      setCirPreviousComparisonReport(null)

      const previousYear = Number(selectedProject?.year)
      setPreviousCirYear(Number.isFinite(previousYear) ? String(previousYear - 1) : "")

      const [documentsData, diagnosticData, corpusReviewData] = await Promise.all([
        getDocuments(projectId).catch(() => []),
        getDiagnosticLatest(projectId).catch(() => null),
        getDiagnosticCorpusReview(projectId).catch(() => null),
      ])

      const diagnosticVerrous =
        diagnosticData?.validation_verrous ||
        diagnosticData?.display?.validation_verrous ||
        []
      setVerrous(Array.isArray(diagnosticVerrous) ? diagnosticVerrous : [])
      setDocuments(documentsData)
      setDiagnosticBundle(diagnosticData)
      applyDiagnosticCorpusReview(corpusReviewData)
      setLoading(false)

      void getCirFinalConsultantStatus(projectId)
        .then(setCirFinalRegistered)
        .catch(() => setCirFinalRegistered(false))

      Promise.all([
        getScholarLatest(projectId).catch(() => null),
        getArticles(projectId).catch(() => []),
        getDocumentComparePairs(projectId, false).catch(() => null),
        getPreviousCirFinals(projectId).catch(() => []),
        getPreviousCirComparisonLatest(projectId).catch(() => null),
      ])
        .then(([scholarData, articlesData, compareIndexData, previousCirData, previousCirComparisonData]) => {
          setScholarBundle(scholarData)
          setArticles(Array.isArray(articlesData) ? articlesData : [])
          setDocumentCompareIndex(compareIndexData)
          setPreviousCirList(previousCirData)
          setPreviousCirComparisonReport(previousCirComparisonData)
          setCirPreviousComparisonReport(previousCirComparisonData)
        })
        .catch(() => undefined)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de charger le diagnostic du projet."
      )
    } finally {
      setLoading(false)
    }
  }


  const applyPreviousCirComparisonReport = async (
    rawReport: any
  ) => {
    if (!project) return

    // La réponse du POST est la source la plus fraîche. On la conserve
    // immédiatement au lieu de relancer loadData(), qui pouvait réinjecter
    // un ancien rapport vide depuis comparison-latest.
    const normalizedRuntimeReport =
      unwrapCirPreviousReportForDisplay(rawReport)

    const [savedReportRaw, previousItems] = await Promise.all([
      getPreviousCirComparisonLatest(project.id).catch(() => null),
      getPreviousCirFinals(project.id).catch(() => []),
    ])

    const normalizedSavedReport =
      unwrapCirPreviousReportForDisplay(savedReportRaw)

    const bestReport = chooseCirMemoryReport(
      normalizedRuntimeReport,
      normalizedSavedReport,
      rawReport,
      savedReportRaw
    )

    const reportToDisplay =
      Object.keys(bestReport || {}).length > 0
        ? bestReport
        : normalizedRuntimeReport

    setPreviousCirComparisonReport(reportToDisplay)
    setCirPreviousComparisonReport(reportToDisplay)
    setPreviousCirList(
      Array.isArray(previousItems) ? previousItems : []
    )
  }

  const comparePreviousCirOnly = async () => {
    if (!project) return

    if (!previousCirAvailable) {
      setError(
        "Aucun CIR précédent exploitable n’a été détecté pour ce projet."
      )
      return
    }

    setPreviousCirCompareLoading(true)
    setError("")
    setActiveTab("cir-precedent")

    try {
      const rawReport = await compareCurrentWithPreviousCir(project.id)
      await applyPreviousCirComparisonReport(rawReport)
      setActiveTab("cir-precedent")
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de comparer le dossier courant avec le CIR précédent."
      )
    } finally {
      setPreviousCirCompareLoading(false)
    }
  }


  const uploadPreviousCir = async () => {
    if (!project) return

    if (!previousCirFile) {
      setError("Sélectionne le CIR final précédent à enregistrer.")
      return
    }

    const cleanYear = String(previousCirYear || "").trim()
    if (!/^\d{4}$/.test(cleanYear)) {
      setError("Renseigne l’année du CIR précédent, par exemple 2022.")
      return
    }

    setPreviousCirUploading(true)
    setError("")

    try {
      const report = await uploadPreviousCirFinal(project.id, cleanYear, previousCirFile)
      const previousItems = await getPreviousCirFinals(project.id).catch(() => [])
      setPreviousCirUploadReport(report)
      setPreviousCirList(previousItems)
      setPreviousCirFile(null)
      await loadData()
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible d’enregistrer le CIR final précédent."
      )
    } finally {
      setPreviousCirUploading(false)
    }
  }

  const detectDocumentPairs = async () => {
    if (!project) return

    setDocumentCompareLoading(true)
    setError("")

    try {
      const index = await runDocumentCompareAutoPairs(project.id)
      setDocumentCompareIndex(index)
      setDocumentCompareReport(null)
      setSelectedPairIndex(null)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de détecter les paires documentaires."
      )
    } finally {
      setDocumentCompareLoading(false)
    }
  }

  const compareSelectedPair = async (pairIndex: number) => {
    if (!project) return

    setDocumentCompareLoading(true)
    setSelectedPairIndex(pairIndex)
    setError("")

    try {
      const report = await compareDocumentPair(project.id, pairIndex)
      setDocumentCompareReport(report)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de comparer cette paire documentaire."
      )
    } finally {
      setDocumentCompareLoading(false)
    }
  }

  const compareManualFiles = async () => {
    if (!project) return

    if (!manualFileA || !manualFileB) {
      setError("Sélectionne le document A et le document B avant de lancer la comparaison.")
      return
    }

    setDocumentCompareLoading(true)
    setSelectedPairIndex(null)
    setError("")

    try {
      const report = await uploadAndCompareDocumentPair(project.id, manualFileA, manualFileB)
      setDocumentCompareReport(report)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de comparer les deux documents chargés manuellement."
      )
    } finally {
      setDocumentCompareLoading(false)
    }
  }

  const updateDecision = async (
    verrouId: number,
    decision: ConsultantDecision
  ) => {
    if (!project) return

    const numericId = Number(verrouId)
    if (!Number.isFinite(numericId) || numericId <= 0) {
      setError(
        "Ce verrou n’est pas encore synchronisé en base. Actualise le diagnostic avant de prendre une décision."
      )
      return
    }

    setActionLoadingId(numericId)
    setError("")

    const previousVerrous = verrous
    const previousDiagnosticBundle = diagnosticBundle

    // Mise à jour optimiste : le badge change immédiatement.
    setVerrous((prev) =>
      prev.map((verrou) =>
        Number(verrou.id) === numericId
          ? { ...verrou, consultant_status: decision }
          : verrou
      )
    )

    // Le diagnostic affiché vient de display.validation_verrous.
    // Il faut donc mettre à jour toutes les copies frontend du verrou,
    // pas seulement le state `verrous`.
    setDiagnosticBundle((prev: any) => {
      if (!prev || typeof prev !== "object") return prev

      const nextDisplay = {
        ...(prev.display || {}),
      }

      for (const key of [
        "validation_verrous",
        "validation_verrous_preview",
        "consultant_verrous_cir",
        "llm_reformulated_verrous",
      ]) {
        nextDisplay[key] = patchVerrouArrayV144(
          nextDisplay[key],
          numericId,
          decision
        )
      }

      return {
        ...prev,
        validation_verrous: patchVerrouArrayV144(
          prev.validation_verrous,
          numericId,
          decision
        ),
        display: nextDisplay,
      }
    })

    try {
      const updated = await updateVerrouDecision(
        project.id,
        numericId,
        decision
      )

      // Réconciliation avec la réponse réelle du backend.
      setVerrous((prev) =>
        prev.map((verrou) =>
          Number(verrou.id) === numericId
            ? {
                ...verrou,
                ...updated,
                consultant_status:
                  updated.consultant_status ||
                  decision,
                source_json: {
                  ...(verrou.source_json || {}),
                  ...(updated.source_json || {}),
                },
              }
            : verrou
        )
      )

      setDiagnosticBundle((prev: any) => {
        if (!prev || typeof prev !== "object") return prev

        const nextDisplay = {
          ...(prev.display || {}),
        }

        for (const key of [
          "validation_verrous",
          "validation_verrous_preview",
          "consultant_verrous_cir",
          "llm_reformulated_verrous",
        ]) {
          nextDisplay[key] = patchVerrouArrayV144(
            nextDisplay[key],
            numericId,
            decision,
            updated
          )
        }

        return {
          ...prev,
          validation_verrous: patchVerrouArrayV144(
            prev.validation_verrous,
            numericId,
            decision,
            updated
          ),
          display: nextDisplay,
        }
      })

      // Confirme le statut depuis PostgreSQL sans bloquer l’interface.
      const freshVerrous = await getVerrous(project.id).catch(() => null)
      if (Array.isArray(freshVerrous)) {
        setVerrous(freshVerrous)
      }
    } catch (err) {
      // Annulation de la mise à jour optimiste si l’API échoue.
      setVerrous(previousVerrous)
      setDiagnosticBundle(previousDiagnosticBundle)
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de mettre à jour la décision consultant."
      )
    } finally {
      setActionLoadingId(null)
    }
  }

  if (loading) {
    return (
      <div
        className="workspace-page-wide min-w-0 space-y-5 pb-8"
        aria-busy="true"
        aria-label="Chargement d’EnnoDiagnostic"
      >
        <Card className="overflow-hidden">
          <CardContent className="space-y-5 p-5 sm:p-6">
            <div className="flex items-center gap-3">
              <div className="grid size-11 shrink-0 place-items-center rounded-xl bg-brand/10 text-brand">
                <BrainCircuit className="size-5" aria-hidden="true" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-foreground">Ouverture d’EnnoDiagnostic</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  Le projet s’affiche dès que la synthèse principale est prête.
                </p>
              </div>
              <Loader2 className="size-4 animate-spin text-brand" aria-hidden="true" />
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              {[0, 1, 2].map((item) => (
                <div key={item} className="space-y-2 rounded-xl border p-4">
                  <div className="h-3 w-24 rounded bg-muted motion-safe:animate-pulse" />
                  <div className="h-7 w-16 rounded bg-muted motion-safe:animate-pulse" />
                  <div className="h-3 w-full rounded bg-muted/70 motion-safe:animate-pulse" />
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (!project) {
    return (
      <div className="p-6 max-w-7xl mx-auto">
        <Card>
          <CardContent className="p-8 text-center">
            <p className="text-sm font-medium text-foreground">
              Aucun projet disponible.
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              Crée un projet avant d’ouvrir EnnoDiagnostic.
            </p>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="workspace-page-wide min-w-0 space-y-4 pb-24 sm:space-y-5 sm:pb-7 lg:space-y-6 lg:pb-8">
      <Dialog
        open={corpusReviewOpen}
        onOpenChange={(open) => {
          // Cette vérification protège le corpus. La fermeture passe par une
          // des deux actions explicites du pied de dialogue.
          if (open) setCorpusReviewOpen(true)
        }}
      >
        <DialogContent className="sm:max-w-2xl" showCloseButton={false}>
          <DialogHeader>
            <div className="flex items-start gap-3 pr-2">
              <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-warning/12 text-warning">
                <AlertTriangle className="size-5" aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <DialogTitle>Vérifier les documents avant le diagnostic</DialogTitle>
                <DialogDescription className="mt-2 leading-6">
                  Vous avez ajouté {pendingImprovementDocuments.length} document(s) depuis
                  EnnoAmélioration. Ils sont exclus du Diagnostic par défaut afin d’éviter
                  d’analyser un CIR ou un texte non souhaité.
                </DialogDescription>
              </div>
            </div>
          </DialogHeader>

          <div className="max-h-[45dvh] space-y-2 overflow-y-auto pr-1" role="list">
            {pendingImprovementDocuments.map((document) => {
              const kept = corpusKeepIds.has(document.id)
              return (
                <div
                  key={document.id}
                  className="flex flex-col gap-3 rounded-xl border border-border bg-card p-3 sm:flex-row sm:items-center"
                  role="listitem"
                >
                  <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
                    <FileText className="size-4" aria-hidden="true" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-foreground" title={document.filename}>
                      {document.filename}
                    </p>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <span>{formatDocumentSize(document.file_size)}</span>
                      <Badge variant={kept ? "default" : "outline"} className="h-5 px-2 text-[10px]">
                        {kept ? "Gardé pour Diagnostic" : "En attente"}
                      </Badge>
                    </div>
                  </div>
                  <Button
                    type="button"
                    variant={kept ? "default" : "outline"}
                    className="min-h-11 shrink-0 sm:min-h-9"
                    aria-pressed={kept}
                    disabled={corpusReviewSaving}
                    onClick={() => {
                      setCorpusKeepIds((current) => {
                        const next = new Set(current)
                        if (next.has(document.id)) next.delete(document.id)
                        else next.add(document.id)
                        return next
                      })
                    }}
                  >
                    {kept ? <CheckCircle2 className="size-4" /> : <Plus className="size-4" />}
                    {kept ? "Gardé" : "Garder"}
                  </Button>
                </div>
              )
            })}
          </div>

          <p className="text-xs leading-5 text-muted-foreground" role="status" aria-live="polite">
            {diagnosticDocumentsCount + corpusKeepIds.size === 0
              ? "Aucune source Diagnostic ne resterait disponible. Gardez au moins un document avant de relancer."
              : corpusKeepIds.size > 0
                ? `${corpusKeepIds.size} document(s) seront ajoutés au corpus Diagnostic.`
                : "Aucun de ces documents ne sera utilisé par le Diagnostic."}
          </p>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              className="min-h-11"
              disabled={corpusReviewSaving}
              onClick={() => void saveCorpusReview(false)}
            >
              Continuer sans relancer
            </Button>
            <Button
              type="button"
              className="min-h-11 bg-brand hover:bg-brand/90"
              disabled={corpusReviewSaving || diagnosticDocumentsCount + corpusKeepIds.size === 0}
              onClick={() => void saveCorpusReview(true)}
            >
              {corpusReviewSaving ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Play className="size-4" />
              )}
              Préparer les sources puis relancer
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <PageHeader
        className="module-header module-diagnostic"
        eyebrow="Agent de qualification"
        title="EnnoDiagnostic"
        description="Qualifiez les verrous scientifiques et techniques, contrôlez les preuves, puis validez le diagnostic avec une décision humaine."
        icon={BrainCircuit}
        context={
          <>
            <ContextBadge
              className="max-w-full"
              label="Projet"
              value={`${project.organisme} · ${project.project_name} · ${project.year}`}
            />
            <ContextBadge
              className="max-w-full"
              label="Domaine"
              value={project.domain_label || "Non renseigné"}
            />
            <ContextBadge>{diagnosticDocumentsCount} source(s) Diagnostic</ContextBadge>
          </>
        }
        actions={
          <div className="grid w-full min-w-0 gap-2 sm:w-[32rem] sm:grid-cols-2 xl:w-[34rem] 2xl:flex 2xl:w-auto">
            {projects.length > 1 && (
              <select
                aria-label="Changer de projet"
                value={project.id}
                onChange={(event) => changeProject(Number(event.target.value))}
                className="min-h-11 min-w-0 rounded-md border border-border bg-background px-3 text-sm sm:col-span-2 sm:min-h-9 2xl:col-span-1 2xl:w-72"
                disabled={running}
              >
                {projects.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.organisme} — {item.project_name} — {item.year}
                  </option>
                ))}
              </select>
            )}

            <Button
              variant="outline"
              className="min-h-11 w-full sm:min-h-9 2xl:w-auto"
              onClick={prepareSources}
              disabled={running}
            >
              {runningMode === "prepare" ? (
                <Loader2 className="animate-spin" data-icon="inline-start" />
              ) : (
                <Search data-icon="inline-start" />
              )}
              Préparer les sources
            </Button>

            <Button
              className="min-h-11 w-full sm:min-h-9 2xl:w-auto"
              onClick={runAgentOnly}
              disabled={running}
            >
              {runningMode === "agent" ? (
                <Loader2 className="animate-spin" data-icon="inline-start" />
              ) : (
                <Play data-icon="inline-start" />
              )}
              Lancer EnnoDiagnostic
            </Button>

            <Button
              variant="ghost"
              className="min-h-11 w-full sm:col-span-2 sm:min-h-9 2xl:col-span-1 2xl:w-auto"
              onClick={loadData}
              disabled={running}
            >
              <RefreshCw data-icon="inline-start" />
              Actualiser
            </Button>
          </div>
        }
      />

      <WorkflowSteps
        className="snap-x snap-mandatory"
        steps={[
          {
            label: "Sources",
            detail: "Préparation",
            status: hasDiagnostic
              ? "complete"
              : runningMode === "prepare"
                ? "current"
                : "upcoming",
          },
          {
            label: "Diagnostic",
            detail: "Analyse IA",
            status: hasDiagnostic
              ? "complete"
              : running && runningMode !== "prepare"
                ? "current"
                : "upcoming",
          },
          {
            label: "Contrôle",
            detail:
              hasDiagnostic && pendingReviewCount === 0
                ? "Revue terminée"
                : "Preuves & décisions",
            status:
              hasDiagnostic && pendingReviewCount === 0
                ? "complete"
                : hasDiagnostic
                  ? "current"
                  : "upcoming",
          },
          {
            label: "Validation",
            detail: cirFinalRegistered ? "CIR final archivé" : "CIR final à déposer",
            status: cirFinalRegistered
              ? "complete"
              : hasDiagnostic && pendingReviewCount === 0
                ? "current"
                : "upcoming",
          },
        ]}
      />

      {running && (
        <StatusNotice
          state="processing"
          live
          title={runningMode === "prepare"
                ? "Préparation des sources en cours"
                : runningMode === "agent"
                  ? "Agent EnnoDiagnostic en cours"
                  : "EnnoDiagnostic complet en cours"}
          description="Le traitement est exécuté par le backend. Aucune estimation fiable n'est exposée ; vous pouvez laisser cette page ouverte."
        >
          <p className="mb-2 font-medium text-foreground">Séquence prévue</p>
          <p>{currentSteps.join(" · ")}</p>
        </StatusNotice>
      )}

      {error && (
        <Card className="border-destructive/30 bg-destructive/10">
          <CardContent className="flex items-start gap-3 p-4 text-destructive">
            <AlertCircle className="mt-0.5 size-5 shrink-0" />
            <div className="min-w-0 [overflow-wrap:anywhere]">
              <p className="text-sm font-semibold">Erreur EnnoDiagnostic</p>
              <p className="text-xs mt-1">{error}</p>
            </div>
          </CardContent>
        </Card>
      )}

      {!hasDiagnostic && !running && (
        <Card className="border-warning/30 bg-warning/5">
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <AlertTriangle className="size-4 text-warning" />
              Diagnostic non lancé
            </CardTitle>
            <CardDescription className="text-xs">
              Prépare d’abord les sources, puis lance EnnoDiagnostic.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-2 sm:flex sm:flex-wrap">
            <Button className="min-h-11 w-full sm:min-h-9 sm:w-auto" variant="outline" onClick={prepareSources}>
              <Search className="size-4 mr-2" />
              Préparer les sources
            </Button>
            <Button className="min-h-11 w-full bg-brand hover:bg-brand/90 sm:min-h-9 sm:w-auto" onClick={runAgentOnly}>
              <Play className="size-4 mr-2" />
              Lancer EnnoDiagnostic
            </Button>
          </CardContent>
        </Card>
      )}

      <section className="grid min-w-0 gap-4 xl:grid-cols-[0.92fr_1.08fr_1.35fr]">
        <Card className="overflow-hidden rounded-2xl border-brand/15 bg-gradient-to-br from-white via-white to-brand/[0.035] shadow-sm">
          <CardContent className="p-5">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                  Défendabilité R&D
                </p>
                <div className="mt-3 flex flex-wrap items-end gap-3">
                  <span className="text-4xl font-semibold tracking-[-0.05em] text-foreground">
                    {formatScore(frascatiScore)}
                  </span>
                  <Badge
                    variant="outline"
                    className={`mb-1 ${riskClass(frascatiRisk)}`}
                  >
                    Risque {frascatiRisk || "—"}
                  </Badge>
                </div>
              </div>

              <span className="grid size-11 shrink-0 place-items-center rounded-2xl border border-brand/15 bg-brand/[0.06] text-brand">
                <TrendingUp className="size-5" aria-hidden="true" />
              </span>
            </div>

            <p className="mt-4 max-w-sm text-xs leading-5 text-muted-foreground">
              Indice interne de défendabilité documentaire. La décision finale reste celle du consultant CIR.
            </p>

            <div className="mt-5" role="img" aria-label={`Défendabilité documentaire : ${formatScore(frascatiScore)}`}>
              <div className="relative h-2.5 overflow-hidden rounded-full bg-muted">
                <div className="absolute inset-y-0 left-1/2 w-px bg-foreground/25" aria-hidden="true" />
                <div className="absolute inset-y-0 left-3/4 w-px bg-foreground/35" aria-hidden="true" />
                {frascatiPercent !== null && (
                  <div
                    className="h-full rounded-full bg-brand motion-safe:transition-[width] motion-safe:duration-500"
                    style={{ width: `${frascatiPercent}%` }}
                  />
                )}
              </div>
              <div className="mt-1.5 flex justify-between text-[9px] text-muted-foreground" aria-hidden="true">
                <span>À documenter</span>
                <span>50</span>
                <span>75</span>
                <span>Solide</span>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="overflow-hidden rounded-2xl border-border bg-card shadow-sm">
          <CardContent className="p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                  Revue consultant
                </p>
                <div className="mt-3 flex items-baseline gap-2">
                  <span className="text-3xl font-semibold tracking-[-0.04em] text-foreground">
                    {reviewedVerrousCount}/{verrousForDisplay.length || 0}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    verrou(s) examiné(s)
                  </span>
                </div>
              </div>

              <Badge
                variant="outline"
                className={
                  pendingReviewCount === 0
                    ? "border-success/30 bg-success/10 text-success"
                    : "border-warning/30 bg-warning/10 text-warning"
                }
              >
                {reviewProgress}%
              </Badge>
            </div>

            <div
              className="mt-4 flex h-2.5 overflow-hidden rounded-full bg-muted"
              role="img"
              aria-label={decisionSegments.map((segment) => `${segment.label} : ${segment.count}`).join(" ; ")}
            >
              {decisionSegments.map((segment) =>
                segment.count > 0 ? (
                  <span
                    key={segment.key}
                    className={`${segment.color} h-full motion-safe:transition-[width] motion-safe:duration-500`}
                    style={{ width: `${(segment.count / Math.max(verrousForDisplay.length, 1)) * 100}%` }}
                  />
                ) : null
              )}
            </div>

            <div className="mt-4 grid grid-cols-2 gap-2">
              {decisionSegments.map((segment) => (
                <button
                  key={segment.key}
                  type="button"
                  className="flex min-h-10 items-center justify-between gap-2 rounded-lg border bg-background px-3 text-left transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => {
                    setActiveTab("diagnostic")
                    setDiagnosticSection("verrous")
                  }}
                  aria-label={`${segment.label} : ${segment.count}. Ouvrir les verrous.`}
                >
                  <span className="flex min-w-0 items-center gap-2 text-[10px] text-muted-foreground">
                    <span className={`size-2 shrink-0 rounded-full ${segment.dot}`} aria-hidden="true" />
                    <span className="truncate">{segment.label}</span>
                  </span>
                  <span className="text-sm font-semibold tabular-nums text-foreground">{segment.count}</span>
                </button>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card className="overflow-hidden rounded-2xl border-border bg-card shadow-sm">
          <CardContent className="p-5">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                  Dossier & contrôles
                </p>
                <div className="mt-3 flex items-baseline gap-2">
                  <span className="text-3xl font-semibold tracking-[-0.04em] text-foreground">
                    {pipelineStats?.documents_loaded_count ?? documents.length ?? 0}
                  </span>
                  <span className="text-xs text-muted-foreground">document(s) extrait(s)</span>
                </div>
              </div>

              <span className="grid size-11 shrink-0 place-items-center rounded-2xl border bg-muted/35 text-muted-foreground">
                <FileText className="size-5" aria-hidden="true" />
              </span>
            </div>

            <div className="mt-5 grid grid-cols-3 divide-x divide-border">
              <div className="pr-3">
                <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                  Contrôle IA
                </p>
                <p className="mt-1 text-sm font-semibold text-foreground">{formatScore(aiScore)}</p>
                <p className="text-[10px] text-muted-foreground">{aiRisk || "—"}</p>
              </div>

              <div className="px-3">
                <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                  CIR précédent
                </p>
                <p className="mt-1 text-sm font-semibold text-foreground">
                  {noveltyPercent(cirMemoryNoveltyScore)}
                </p>
                <p className="text-[10px] text-muted-foreground">
                  {cirMemoryComparisons.length > 0
                    ? "Comparé"
                    : previousCirAvailable
                      ? "Disponible"
                      : "Absent"}
                </p>
              </div>

              <div className="pl-3">
                <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                  Comparaison docs
                </p>
                <p className="mt-1 text-sm font-semibold text-foreground">{docComparePairsCount || 0}</p>
                <p className="text-[10px] text-muted-foreground">paire(s)</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </section>

      {hasDiagnostic && selectedVerrousForScholar.length > 0 ? (
        <Card className="overflow-hidden rounded-2xl border-brand/20 bg-gradient-to-r from-brand/[0.055] via-white to-white shadow-sm">
          <CardContent className="p-0">
            <div className="grid min-w-0 lg:grid-cols-[minmax(0,1fr)_240px]">
              <div className="min-w-0 p-5">
                <div className="grid gap-5 xl:grid-cols-[280px_minmax(0,1fr)]">
                  <div className="flex items-start gap-3">
                    <span className="grid size-11 shrink-0 place-items-center rounded-2xl border border-brand/20 bg-white text-brand shadow-sm">
                      <Search className="size-5" aria-hidden="true" />
                    </span>

                    <div className="min-w-0">
                      <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-brand">
                        Relais Agent 1 → Agent 2
                      </p>
                      <h2 className="mt-1 text-base font-semibold text-foreground">
                        Passage vers EnnoScholar
                      </h2>
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">
                        Seuls les verrous retenus par le consultant sont transmis à l’agent scientifique.
                      </p>
                    </div>
                  </div>

                  <div className="grid min-w-0 gap-2 md:grid-cols-3">
                    {selectedVerrousForScholar.slice(0, 3).map((verrou, index) => (
                      <div
                        key={verrou.id}
                        className="flex min-w-0 items-start gap-2 rounded-xl border border-brand/10 bg-white/90 p-3"
                      >
                        <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-success/10 text-[10px] font-semibold text-success">
                          ✓
                        </span>
                        <div className="min-w-0">
                          <p className="line-clamp-2 text-xs font-medium leading-5 text-foreground">
                            {verrou.title}
                          </p>
                          <p className="mt-1 text-[10px] text-muted-foreground">
                            {getHistoricalMemoryCardMetric(verrou).isMemoryCard
                              ? `${getHistoricalMemoryCardMetric(verrou).percentage ?? "—"} % continuité`
                              : `V${index + 1} · Score ${formatVerrouScoreV124(verrou.score)}`}
                          </p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="flex flex-col justify-center gap-2 border-t border-brand/15 bg-white/65 p-5 lg:border-l lg:border-t-0">
                <Button
                  className="w-full bg-brand hover:bg-brand/90"
                  disabled={pendingReviewCount > 0 || !onOpenScholar}
                  onClick={() => onOpenScholar?.()}
                >
                  <Search className="size-4" data-icon="inline-start" aria-hidden="true" />
                  Passer à EnnoScholar
                  <ArrowRight className="size-4" data-icon="inline-end" aria-hidden="true" />
                </Button>

                <Button
                  variant="ghost"
                  className="w-full"
                  onClick={() => {
                    setActiveTab("diagnostic")
                    setDiagnosticSection("verrous")
                  }}
                >
                  Revoir les verrous
                </Button>

                {!onOpenScholar && scholarRunId ? (
                  <p className="text-center text-[10px] leading-4 text-muted-foreground">
                    Le passage est prêt. Ouvrez EnnoScholar depuis le menu latéral.
                  </p>
                ) : null}
              </div>
            </div>
          </CardContent>
        </Card>
      ) : null}

      <Tabs value={activeTab} onValueChange={setActiveTab} className="min-w-0 space-y-4">
        <div className="sticky top-2 z-20 rounded-xl border border-border bg-card/95 p-3 shadow-sm backdrop-blur md:hidden">
          <label htmlFor="diagnostic-section" className="mb-2 block text-xs font-semibold text-foreground">
            Section EnnoDiagnostic
          </label>
          <select
            id="diagnostic-section"
            value={activeTab}
            onChange={(event) => setActiveTab(event.target.value)}
            className="min-h-11 w-full min-w-0 rounded-md border border-input bg-background px-3 text-sm text-foreground"
          >
            {diagnosticTabs.map((tab) => (
              <option key={tab.value} value={tab.value}>
                {tab.label}
              </option>
            ))}
          </select>
        </div>

        <div className="sticky top-2 z-20 hidden min-w-0 overflow-hidden rounded-2xl border border-border bg-card/95 shadow-sm backdrop-blur md:grid xl:grid-cols-[1.2fr_0.9fr_0.8fr]">
          <div className="min-w-0 border-b border-border p-2 xl:border-b-0 xl:border-r">
            <p className="px-2 pb-1.5 text-[9px] font-semibold uppercase tracking-[0.14em] text-brand">
              Analyse
            </p>
            <TabsList className="grid h-auto w-full grid-cols-3 gap-1 bg-muted/35 p-1">
              <TabsTrigger value="overview" className="min-h-10 min-w-0 whitespace-normal px-2 py-2 text-xs leading-4">
                Vue d’ensemble
              </TabsTrigger>
              <TabsTrigger value="diagnostic" className="min-h-10 min-w-0 whitespace-normal px-2 py-2 text-xs leading-4">
                Diagnostic CIR
              </TabsTrigger>
              <TabsTrigger value="controle-ia" className="min-h-10 min-w-0 whitespace-normal px-2 py-2 text-xs leading-4">
                Contrôle IA
              </TabsTrigger>
            </TabsList>
          </div>

          <div className="min-w-0 border-b border-border p-2 xl:border-b-0 xl:border-r">
            <p className="px-2 pb-1.5 text-[9px] font-semibold uppercase tracking-[0.14em] text-brand">
              Consolidation
            </p>
            <TabsList className="grid h-auto w-full grid-cols-2 gap-1 bg-muted/35 p-1">
              <TabsTrigger value="cir-precedent" className="min-h-10 min-w-0 whitespace-normal px-2 py-2 text-xs leading-4">
                CIR précédent
              </TabsTrigger>
              <TabsTrigger value="comparaison-docs" className="min-h-10 min-w-0 whitespace-normal px-2 py-2 text-xs leading-4">
                Comparaison docs
              </TabsTrigger>
            </TabsList>
          </div>

          <div className="min-w-0 p-2">
            <p className="px-2 pb-1.5 text-[9px] font-semibold uppercase tracking-[0.14em] text-success">
              Finalisation
            </p>
            <TabsList className="grid h-auto w-full grid-cols-2 gap-1 bg-muted/35 p-1">
              <TabsTrigger value="validation" className="min-h-10 min-w-0 whitespace-normal px-2 py-2 text-xs leading-4">
                Validation
              </TabsTrigger>
              <TabsTrigger value="cir-final-consultant" className="min-h-10 min-w-0 whitespace-normal px-2 py-2 text-xs leading-4">
                Déposer le CIR final
              </TabsTrigger>
            </TabsList>
          </div>
        </div>

        <TabsContent value="overview" className="space-y-4 motion-safe:animate-in motion-safe:fade-in motion-safe:duration-200">
          <Card className="overflow-hidden rounded-2xl border-border bg-card shadow-sm">
            <CardHeader className="border-b bg-gradient-to-r from-brand/[0.04] via-white to-white">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Sparkles className="size-4 text-brand" />
                    Synthèse stratégique
                  </CardTitle>
                  <CardDescription className="mt-1 text-xs">
                    Une lecture rapide du dossier avant d’ouvrir les détails techniques.
                  </CardDescription>
                </div>

                <Badge variant="outline" className={riskClass(frascatiRisk)}>
                  Défendabilité {formatScore(frascatiScore)}
                </Badge>
              </div>
            </CardHeader>

            <CardContent className="p-0">
              <div className="border-b border-border px-5 py-5">
                <BackendSectionRendererV93 text={summary} />
              </div>

              <div className="grid min-w-0 md:grid-cols-3">
                <div className="min-w-0 p-5 md:border-r md:border-border">
                  <div className="flex items-center gap-2 text-brand">
                    <Target className="size-4" aria-hidden="true" />
                    <p className="text-[10px] font-semibold uppercase tracking-[0.1em]">
                      Objectif global
                    </p>
                  </div>
                  <p className="mt-3 text-sm leading-6 text-foreground">
                    {cleanDisplayText(objective).slice(0, 420)}
                    {cleanDisplayText(objective).length > 420 ? "…" : ""}
                  </p>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="mt-3 px-0 text-brand"
                    onClick={() => {
                      setActiveTab("diagnostic")
                      setDiagnosticSection("objectif")
                    }}
                  >
                    Voir le détail
                    <ArrowRight className="size-4" data-icon="inline-end" />
                  </Button>
                </div>

                <div className="min-w-0 border-t border-border p-5 md:border-r md:border-t-0">
                  <div className="flex items-center gap-2 text-brand">
                    <CheckCircle2 className="size-4" aria-hidden="true" />
                    <p className="text-[10px] font-semibold uppercase tracking-[0.1em]">
                      Conclusion d’éligibilité
                    </p>
                  </div>
                  <div className="mt-3 flex items-center gap-2">
                    <span className="text-2xl font-semibold text-foreground">
                      {formatScore(frascatiScore)}
                    </span>
                    <Badge variant="outline" className={riskClass(frascatiRisk)}>
                      Risque {frascatiRisk || "—"}
                    </Badge>
                  </div>
                  <p className="mt-2 text-sm leading-6 text-muted-foreground">
                    {cleanDisplayText(lectureFrascatiText || frascatiJustificationText).slice(0, 320) ||
                      "L’étude détaillée relie les critères Frascati aux preuves du dossier."}
                    {cleanDisplayText(lectureFrascatiText || frascatiJustificationText).length > 320 ? "…" : ""}
                  </p>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="mt-3 px-0 text-brand"
                    onClick={() => {
                      setActiveTab("diagnostic")
                      setDiagnosticSection("eligibilite")
                    }}
                  >
                    Voir l’étude
                    <ArrowRight className="size-4" data-icon="inline-end" />
                  </Button>
                </div>

                <div className="min-w-0 border-t border-border p-5 md:border-t-0">
                  <div className="flex items-center gap-2 text-brand">
                    <ArrowRight className="size-4" aria-hidden="true" />
                    <p className="text-[10px] font-semibold uppercase tracking-[0.1em]">
                      Prochaine étape
                    </p>
                  </div>
                  <p className="mt-3 text-sm font-semibold leading-6 text-foreground">
                    {nextDiagnosticAction}
                  </p>
                  <p className="mt-2 text-xs leading-5 text-muted-foreground">
                    {pendingReviewCount > 0
                      ? "Terminez les décisions consultant avant le passage vers l’agent scientifique."
                      : selectedVerrousForScholar.length > 0
                        ? `${selectedVerrousForScholar.length} verrou(s) retenu(s) sont prêts à être transmis à EnnoScholar.`
                        : "Aucun verrou retenu n’est encore prêt pour EnnoScholar."}
                  </p>
                  <Button
                    size="sm"
                    className="mt-4"
                    onClick={() => {
                      setActiveTab("diagnostic")
                      setDiagnosticSection(pendingReviewCount > 0 ? "verrous" : "eligibilite")
                    }}
                  >
                    Continuer
                    <ArrowRight className="size-4" data-icon="inline-end" />
                  </Button>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="diagnostic" className="space-y-4 motion-safe:animate-in motion-safe:fade-in motion-safe:duration-200">
          <Card className="sticky top-2 z-20 min-w-0 border-brand/15 bg-card/95 shadow-sm backdrop-blur">
            <CardContent className="p-2">
              <div className="flex min-w-0 items-center gap-2">
                <div className="hidden shrink-0 px-2 lg:block">
                  <p className="text-[9px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                    Diagnostic CIR
                  </p>
                  <p className="mt-0.5 text-xs font-medium text-foreground">
                    6 sections métier
                  </p>
                </div>

                <div className="flex min-w-0 flex-1 gap-1 overflow-x-auto pb-0.5">
                  {diagnosticSubsections.map((section, index) => (
                    <button
                      key={section.value}
                      type="button"
                      onClick={() => setDiagnosticSection(section.value)}
                      aria-current={diagnosticSection === section.value ? "step" : undefined}
                      className={`group flex min-h-10 shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-left text-xs font-medium transition ${
                        diagnosticSection === section.value
                          ? "bg-brand text-brand-foreground shadow-sm"
                          : "text-muted-foreground hover:bg-muted hover:text-foreground"
                      }`}
                    >
                      <span
                        className={`flex size-5 items-center justify-center rounded-full text-[10px] font-semibold ${
                          diagnosticSection === section.value
                            ? "bg-white/20 text-white"
                            : "bg-muted text-muted-foreground group-hover:bg-background"
                        }`}
                      >
                        {index + 1}
                      </span>
                      <span>{section.shortLabel}</span>
                    </button>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>

          <div className="flex min-w-0 items-center justify-between gap-3 px-1">
            <div className="min-w-0">
              <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-brand">
                Vous êtes ici
              </p>
              <p className="truncate text-sm font-semibold text-foreground">
                {diagnosticSubsections.find((section) => section.value === diagnosticSection)?.label}
              </p>
            </div>
            <p className="shrink-0 text-xs text-muted-foreground">
              {diagnosticSubsections.findIndex((section) => section.value === diagnosticSection) + 1}/6
            </p>
          </div>

          {diagnosticSection === "objectif" && (
            <BackendSectionCardV93
                        title="Objectif global"
                        icon={Target}
                        text={objective}
                        emptyText="L’objectif global apparaîtra après l’exécution d’EnnoDiagnostic."
                      />
          )}

          {diagnosticSection === "eligibilite" && (
            <UnifiedEligibilityStudyCardV191
                        score={frascatiScore}
                        signalsCount={Number(
                          display?.frascati_summary?.scores_count ??
                            diagnosticBundle?.frascati_summary?.scores_count ??
                            0
                        )}
                        candidateCount={verrousForDisplay.length}
                        reading={lectureFrascatiText}
                        justification={frascatiJustificationText}
                        demarche={demarcheAudit}
                        evidenceReport={eligibilityEvidenceReport}
                        proofClaims={eligibilityProofClaims}
                        projectId={project?.id}
                        sourceDocuments={sourceDocuments}
                      />
          )}

          {diagnosticSection === "verrous" && (
            <Card className="overflow-hidden rounded-2xl border border-border bg-white shadow-sm">
              <CardHeader className="border-b bg-white px-5 py-4 sm:px-6">
                <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
                  <div className="min-w-0">
                    <CardTitle className="flex items-center gap-2 text-sm">
                      <Lock className="size-4 text-brand" />
                      Verrous synchronisés pour validation
                    </CardTitle>
                    <CardDescription className="mt-1 max-w-3xl text-xs leading-5">
                      Chaque verrou conserve l’ensemble des éléments du diagnostic : raisonnement,
                      incertitude, preuves, contrôle consultant, documents sources et décision humaine.
                    </CardDescription>
                  </div>

                  <Button
                    type="button"
                    variant={manualFormOpen ? "secondary" : "outline"}
                    className="min-h-10 w-full shrink-0 rounded-xl sm:w-auto"
                    aria-expanded={manualFormOpen}
                    aria-controls="manual-verrou-form"
                    onClick={() => {
                      setManualFormOpen((current) => !current)
                      setManualFormError("")
                    }}
                  >
                    <Plus className="size-4" data-icon="inline-start" />
                    Ajouter un verrou
                  </Button>
                </div>
              </CardHeader>

              <CardContent className="space-y-4 p-4 sm:p-5">
                {manualFormOpen && (
                  <form
                    id="manual-verrou-form"
                    onSubmit={submitManualVerrou}
                    className="space-y-4 rounded-xl border border-brand/20 bg-brand/[0.035] p-4 sm:p-5"
                  >
                    <div>
                      <p className="text-sm font-semibold text-foreground">
                        Nouveau verrou consultant
                      </p>
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">
                        Il sera marqué « Retenu » dès sa création. La description et les mots-clés guideront la recherche et la rédaction.
                      </p>
                    </div>

                    <div className="grid gap-4 lg:grid-cols-2">
                      <div className="space-y-2 lg:col-span-2">
                        <Label htmlFor="manual-verrou-title">Titre du verrou</Label>
                        <Input
                          id="manual-verrou-title"
                          value={manualTitle}
                          onChange={(event) => setManualTitle(event.target.value)}
                          placeholder="Ex. Incertitude sur la stabilité du procédé en conditions variables"
                          minLength={5}
                          maxLength={500}
                          required
                          disabled={manualSubmitting}
                        />
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="manual-verrou-description">Description</Label>
                        <Textarea
                          id="manual-verrou-description"
                          value={manualDescription}
                          onChange={(event) => setManualDescription(event.target.value)}
                          placeholder="Décrivez l’incertitude, les limites connues et ce qui reste à démontrer."
                          className="min-h-28 resize-y"
                          maxLength={4000}
                          disabled={manualSubmitting}
                        />
                      </div>

                      <div className="space-y-2">
                        <Label htmlFor="manual-verrou-keywords">Mots-clés</Label>
                        <Input
                          id="manual-verrou-keywords"
                          value={manualKeywords}
                          onChange={(event) => setManualKeywords(event.target.value)}
                          placeholder="stabilité, robustesse, variabilité, validation"
                          disabled={manualSubmitting}
                        />
                        <p className="flex items-start gap-1.5 text-xs leading-5 text-muted-foreground">
                          <Tags className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                          Séparez les mots-clés par une virgule. Ils sont transmis au moteur de recherche scientifique.
                        </p>
                      </div>
                    </div>

                    {manualFormError && (
                      <div
                        role="alert"
                        className="rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive"
                      >
                        {manualFormError}
                      </div>
                    )}

                    <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                      <Button
                        type="button"
                        variant="ghost"
                        className="min-h-10"
                        disabled={manualSubmitting}
                        onClick={() => setManualFormOpen(false)}
                      >
                        Annuler
                      </Button>
                      <Button type="submit" className="min-h-10" disabled={manualSubmitting}>
                        {manualSubmitting ? (
                          <Loader2 className="size-4 animate-spin" data-icon="inline-start" />
                        ) : (
                          <CheckCircle2 className="size-4" data-icon="inline-start" />
                        )}
                        Ajouter et retenir
                      </Button>
                    </div>
                  </form>
                )}

                {manualFeedback && (
                  <div
                    role="status"
                    className="rounded-lg border border-success/20 bg-success/5 px-3 py-2 text-xs text-success"
                  >
                    {manualFeedback}
                  </div>
                )}

                {verrousForDisplay.length === 0 ? (
                  <div className="rounded-xl border border-dashed p-8 text-center">
                    <p className="text-sm font-medium text-foreground">
                      Aucun verrou à qualifier pour ce projet.
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Lance EnnoDiagnostic ou ajoute directement un verrou consultant.
                    </p>
                  </div>
                ) : (
                  <Accordion className="w-full space-y-3">
                    {verrousForDisplay.map((verrou) => {
                      const sourceDocumentsForVerrou = getVerrouSourceDocuments(verrou)
                      const isLoading = actionLoadingId === verrou.id
                      const isJsonOnly = isJsonOnlyVerrouV107(verrou)
                      const canDecide = Boolean(
                        (verrou as any)?.id ||
                          (verrou as any)?.verrou_id ||
                          (verrou as any)?.db_id ||
                          (verrou as any)?.can_decide === true ||
                          (verrou as any)?.is_db_synced === true
                      )
                      const isManual = isManualConsultantVerrou(verrou)
                      const verrouKeywords = Array.isArray(verrou.source_json?.keywords)
                        ? verrou.source_json.keywords.filter(Boolean).slice(0, 6)
                        : []
                      const explanationSections = getVerrouExplanationSections(verrou)
                      const whyVerrou = getShortVerrouRationale(verrou)
                      const action = consultantAction(verrou)
                      const consultantCheck = getConsultantCheckText(verrou)
                      const historicalContinuity = getHistoricalLockContinuity(verrou)
                      const historicalMemoryMetric = getHistoricalMemoryCardMetric(verrou)
                      const groupedSubproblems = Array.from(
                        new Set(
                          [
                            ...(Array.isArray((verrou as any)?.subproblems_current)
                              ? (verrou as any).subproblems_current
                              : []),
                            ...(Array.isArray((verrou as any)?.absorbed_scientific_axis_titles)
                              ? (verrou as any).absorbed_scientific_axis_titles
                              : []),
                          ]
                            .map((value) => cleanDisplayText(value))
                            .filter(
                              (value) =>
                                Boolean(value) &&
                                value.toLocaleLowerCase("fr") !==
                                  cleanDisplayText(verrou.title).toLocaleLowerCase("fr")
                            )
                        )
                      )
                      const sourcePassagesCount = sourceDocumentsForVerrou.reduce(
                        (total, item) => total + item.passagesCount,
                        0
                      )

                      return (
                        <AccordionItem
                          key={verrou.id}
                          value={String(verrou.id)}
                          className="overflow-hidden rounded-xl border border-brand/15 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.04)]"
                        >
                          <AccordionTrigger className="px-4 py-4 hover:bg-brand/[0.025] sm:px-5">
                            <div className="min-w-0 flex-1 text-left">
                              <div className="flex flex-wrap items-center gap-2">
                                <Badge
                                  variant="outline"
                                  className="border-brand/25 bg-brand/[0.06] text-[10px] font-medium text-brand"
                                >
                                  {isManual
                                    ? "Ajout consultant"
                                    : historicalMemoryMetric.isMemoryCard
                                      ? "Mémoire CIR confirmée"
                                      : "Signal R&D détecté"}
                                </Badge>
                                <Badge
                                  variant="outline"
                                  className={`text-[10px] ${decisionClass(verrou.consultant_status)}`}
                                >
                                  {decisionLabel(verrou.consultant_status)}
                                </Badge>
                                {!isManual && (
                                  <Badge variant="outline" className="whitespace-nowrap text-[10px] tabular-nums">
                                    {historicalMemoryMetric.isMemoryCard
                                      ? `${historicalMemoryMetric.percentage ?? "—"} % continuité`
                                      : `Score ${formatVerrouScoreV124(verrou.score)}`}
                                  </Badge>
                                )}
                              </div>

                              <p className="mt-2 text-sm font-semibold leading-6 text-foreground">
                                {verrou.title}
                              </p>

                              <p className="mt-1 max-w-5xl text-xs leading-5 text-muted-foreground">
                                {cleanDisplayText(whyVerrou).slice(0, 260) ||
                                  "Verrou à confirmer à partir des preuves sources."}
                              </p>

                              {verrouKeywords.length > 0 && (
                                <div className="mt-2 flex flex-wrap gap-1.5" aria-label="Mots-clés du verrou">
                                  {verrouKeywords.map((keyword: string) => (
                                    <Badge key={keyword} variant="secondary" className="text-[10px] font-normal">
                                      {keyword}
                                    </Badge>
                                  ))}
                                </div>
                              )}
                            </div>
                          </AccordionTrigger>

                          <AccordionContent className="border-t border-brand/10 bg-[#fcfcff] px-3 pb-3 sm:px-4 sm:pb-4">
                            <div className="space-y-3 pt-4">
                              {/* Zone principale : exactement la hiérarchie de la maquette */}
                              <div className="grid gap-3 xl:grid-cols-[1.02fr_0.98fr]">
                                <section
                                  className="rounded-xl border border-brand/15 bg-white p-4 sm:p-5"
                                  aria-labelledby={`verrou-why-${verrou.id}`}
                                >
                                  <div className="mb-4 flex items-center gap-3">
                                    <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-brand/[0.07] text-brand">
                                      <BrainCircuit className="size-4" aria-hidden="true" />
                                    </span>
                                    <div>
                                      <h4
                                        id={`verrou-why-${verrou.id}`}
                                        className="text-sm font-semibold text-foreground"
                                      >
                                        {isManual
                                          ? "Pourquoi ce verrou est retenu"
                                          : "Pourquoi EnnoDiagnostic le détecte comme verrou"}
                                      </h4>
                                      <p className="mt-0.5 text-[11px] text-muted-foreground">
                                        Raisonnement technique relié aux preuves du dossier.
                                      </p>
                                    </div>
                                  </div>

                                  <div className="space-y-2.5">
                                    <div className="rounded-lg border bg-muted/[0.18] p-3.5">
                                      <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                                        {isManual
                                          ? "Formulation du consultant"
                                          : "Incertitude technique formulée"}
                                      </p>
                                      <p className="mt-2 whitespace-pre-wrap text-xs leading-6 text-foreground sm:text-sm">
                                        {explanationSections.uncertainty ||
                                          explanationSections.detection}
                                      </p>
                                    </div>

                                    {explanationSections.notSimpleEngineering && (
                                      <div className="rounded-lg border bg-muted/[0.18] p-3.5">
                                        <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                                          Pourquoi ce n’est pas une simple ingénierie
                                        </p>
                                        <p className="mt-2 whitespace-pre-wrap text-xs leading-6 text-foreground sm:text-sm">
                                          {explanationSections.notSimpleEngineering}
                                        </p>
                                      </div>
                                    )}

                                    {explanationSections.evidence && (
                                      <div className="rounded-lg border border-success/20 bg-success/[0.035] p-3.5">
                                        <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-success">
                                          Preuves sources utilisées
                                        </p>
                                        <p className="mt-2 whitespace-pre-wrap text-xs leading-6 text-foreground sm:text-sm">
                                          {explanationSections.evidence}
                                        </p>
                                      </div>
                                    )}

                                    {!explanationSections.uncertainty &&
                                      !explanationSections.notSimpleEngineering &&
                                      !explanationSections.evidence && (
                                        <div className="rounded-lg border bg-muted/[0.18] p-3.5">
                                          <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                                            Signal et raisonnement de détection
                                          </p>
                                          <p className="mt-2 whitespace-pre-wrap text-xs leading-6 text-foreground sm:text-sm">
                                            {explanationSections.detection}
                                          </p>
                                        </div>
                                      )}
                                  </div>
                                </section>

                                {groupedSubproblems.length > 0 && (
                                  <section className="rounded-xl border border-brand/15 bg-white p-4 sm:p-5">
                                    <div className="mb-3">
                                      <h4 className="text-sm font-semibold text-foreground">
                                        Sous-verrous et difficultés regroupés
                                      </h4>
                                      <p className="mt-0.5 text-[11px] leading-5 text-muted-foreground">
                                        Ces points restent visibles et traçables même lorsqu’ils relèvent du même verrou scientifique principal.
                                      </p>
                                    </div>
                                    <ol className="space-y-2 pl-5 text-xs leading-6 text-foreground sm:text-sm">
                                      {groupedSubproblems.map((subproblem) => (
                                        <li key={subproblem} className="list-decimal pl-1">
                                          {subproblem}
                                        </li>
                                      ))}
                                    </ol>
                                  </section>
                                )}

                                <section
                                  className="rounded-xl border border-brand/15 bg-white p-4 sm:p-5"
                                  aria-labelledby={`verrou-check-${verrou.id}`}
                                >
                                  <div className="mb-4 flex items-center gap-3">
                                    <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-brand/[0.07] text-brand">
                                      <Search className="size-4" aria-hidden="true" />
                                    </span>
                                    <div>
                                      <h4
                                        id={`verrou-check-${verrou.id}`}
                                        className="text-sm font-semibold text-foreground"
                                      >
                                        Ce que le consultant doit vérifier
                                      </h4>
                                      <p className="mt-0.5 text-[11px] text-muted-foreground">
                                        Points de contrôle avant validation du verrou.
                                      </p>
                                    </div>
                                  </div>

                                  <p className="whitespace-pre-wrap text-xs leading-6 text-foreground sm:text-sm sm:leading-7">
                                    {consultantCheck}
                                  </p>
                                </section>
                              </div>

                              {historicalContinuity && (
                                <section className="overflow-hidden rounded-xl border border-blue-200 bg-blue-50/45">
                                  <div className="flex flex-col gap-2 border-b border-blue-200 bg-white/75 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                                    <div>
                                      <p className="text-xs font-semibold text-blue-900">
                                        Continuité intégrée du CIR précédent
                                      </p>
                                      <p className="mt-0.5 text-[11px] leading-5 text-blue-800/75">
                                        Ces éléments N-1 complètent ce verrou ; ils restent distingués des preuves et résultats de l’année courante.
                                      </p>
                                    </div>
                                    <div className="flex flex-wrap gap-2">
                                      <Badge variant="outline" className="border-blue-300 bg-white text-[10px] text-blue-800">
                                        {historicalContinuityLabel(historicalContinuity.status)}
                                      </Badge>
                                      {historicalContinuity.previousYears.map((year) => (
                                        <Badge key={year} variant="secondary" className="text-[10px]">
                                          CIR {year}
                                        </Badge>
                                      ))}
                                    </div>
                                  </div>

                                  <div className="space-y-3 p-4">
                                    {(historicalContinuity.familyTitles.length > 0 || historicalContinuity.locks.length > 0) && (
                                      <div className="rounded-lg border border-blue-100 bg-white p-3.5">
                                        <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-blue-800">
                                          Verrou et incertitude N-1
                                        </p>
                                        {historicalContinuity.familyTitles.map((text) => (
                                          <p key={`family-${text}`} className="mt-2 text-xs font-semibold leading-5 text-foreground sm:text-sm">
                                            {text}
                                          </p>
                                        ))}
                                        {historicalContinuity.locks.map((text) => (
                                          <p key={`lock-${text}`} className="mt-2 whitespace-pre-wrap text-xs leading-5 text-muted-foreground">
                                            {text}
                                          </p>
                                        ))}
                                      </div>
                                    )}

                                    <div className="grid gap-3 lg:grid-cols-3">
                                      {[
                                        ["Démarche N-1", historicalContinuity.methods],
                                        ["Paramètres N-1", historicalContinuity.parameters],
                                        ["Résultats N-1", historicalContinuity.results],
                                      ].map(([label, values]) => (
                                        Array.isArray(values) && values.length > 0 ? (
                                          <div key={String(label)} className="rounded-lg border border-blue-100 bg-white p-3.5">
                                            <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-blue-800">
                                              {String(label)}
                                            </p>
                                            <div className="mt-2 space-y-2">
                                              {values.map((text: string) => (
                                                <p key={`${String(label)}-${text}`} className="whitespace-pre-wrap text-xs leading-5 text-foreground">
                                                  {text}
                                                </p>
                                              ))}
                                            </div>
                                          </div>
                                        ) : null
                                      ))}
                                    </div>

                                    <p className="text-[10px] leading-4 text-blue-800/75">
                                      Règle de preuve : le CIR N-1 confirme la trajectoire historique. Toute affirmation sur l’année courante doit rester soutenue par un document de l’année courante.
                                    </p>
                                  </div>
                                </section>
                              )}

                              {/* Action consultant : bandeau léger et non une grosse carte */}
                              <section
                                className="flex flex-col gap-3 rounded-xl border border-success/20 bg-success/[0.035] px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
                                aria-labelledby={`verrou-action-${verrou.id}`}
                              >
                                <div className="flex min-w-0 items-start gap-3">
                                  <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-white text-success ring-1 ring-success/15">
                                    <CheckCircle2 className="size-4" aria-hidden="true" />
                                  </span>
                                  <div className="min-w-0">
                                    <h4
                                      id={`verrou-action-${verrou.id}`}
                                      className="text-xs font-semibold uppercase tracking-[0.08em] text-foreground"
                                    >
                                      Action consultant
                                    </h4>
                                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                                      {action}
                                    </p>
                                  </div>
                                </div>
                                <Badge
                                  variant="outline"
                                  className={`w-fit shrink-0 bg-white text-[10px] ${decisionClass(verrou.consultant_status)}`}
                                >
                                  Statut : {decisionLabel(verrou.consultant_status)}
                                </Badge>
                              </section>

                              {!canDecide && (
                                <div className="rounded-lg border border-warning/20 bg-warning/5 p-3">
                                  <p className="text-xs font-medium text-warning">
                                    Ce verrou vient directement du JSON diagnostic. Pour activer les décisions consultant, lance ou vérifie la synchronisation backend des verrous reformulés.
                                  </p>
                                </div>
                              )}

                              {/* Sources : visibles comme dans la version originale, mais plus respirantes */}
                              {sourceDocumentsForVerrou.length > 0 && (
                                <section
                                  className="overflow-hidden rounded-xl border border-border bg-white"
                                  aria-labelledby={`verrou-sources-${verrou.id}`}
                                >
                                  <div className="flex flex-col gap-3 border-b bg-muted/[0.12] px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between">
                                    <div className="flex items-start gap-3">
                                      <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-brand/[0.06] text-brand">
                                        <FileText className="size-4" aria-hidden="true" />
                                      </span>
                                      <div>
                                        <h4
                                          id={`verrou-sources-${verrou.id}`}
                                          className="text-sm font-semibold text-foreground"
                                        >
                                          Documents et preuves associés au verrou
                                        </h4>
                                        <p className="mt-0.5 text-[11px] leading-5 text-muted-foreground">
                                          Cliquez sur une source pour contrôler les passages utilisés.
                                        </p>
                                      </div>
                                    </div>

                                    <div className="flex flex-wrap gap-2">
                                      <Badge variant="outline" className="bg-white text-[10px]">
                                        {sourceDocumentsForVerrou.length} document(s)
                                      </Badge>
                                      <Badge variant="secondary" className="text-[10px]">
                                        {sourcePassagesCount} passage(s)
                                      </Badge>
                                    </div>
                                  </div>

                                  <div className="grid grid-cols-1 gap-2.5 p-3.5 xl:grid-cols-2">
                                    {sourceDocumentsForVerrou.map((source) => (
                                      <article
                                        key={source.key}
                                        className="rounded-lg border bg-white p-3 transition-colors hover:border-brand/25 hover:bg-brand/[0.02]"
                                      >
                                        <div className="flex items-start gap-3">
                                          <span className="grid size-8 shrink-0 place-items-center rounded-lg border bg-muted/[0.16] text-brand">
                                            <FileText className="size-3.5" aria-hidden="true" />
                                          </span>
                                          <div className="min-w-0 flex-1">
                                            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                                              <p className="[overflow-wrap:anywhere] text-xs font-semibold leading-5 text-foreground sm:text-sm">
                                                {source.displayName || source.document}
                                              </p>
                                              <Badge variant="secondary" className="w-fit shrink-0 text-[9px]">
                                                {source.passagesCount} passage(s)
                                              </Badge>
                                            </div>

                                            <div className="mt-2">
                                              <SourceTextWithDocuments
                                                projectId={project?.id || 0}
                                                text={source.document}
                                                documents={sourceDocuments}
                                                evidence={source.evidence}
                                                compact
                                                hideTextWhenMatched
                                              />
                                            </div>
                                          </div>
                                        </div>
                                      </article>
                                    ))}
                                  </div>
                                </section>
                              )}

                              {/* Traçabilité secondaire : gardée mais repliée */}
                              <details className="group overflow-hidden rounded-xl border bg-white">
                                <summary className="flex min-h-11 cursor-pointer list-none items-center justify-between gap-3 px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-muted-foreground transition-colors hover:bg-muted/20 [&::-webkit-details-marker]:hidden">
                                  <span>Traçabilité technique</span>
                                  <ArrowRight
                                    className="size-4 transition-transform group-open:rotate-90"
                                    aria-hidden="true"
                                  />
                                </summary>

                                <div className="grid gap-4 border-t p-4 lg:grid-cols-2">
                                  <div>
                                    <p className="mb-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                                      {isManual ? "Titre saisi" : "Titre initial détecté"}
                                    </p>
                                    <p className="text-sm leading-6 text-foreground">
                                      {verrou.title}
                                    </p>
                                  </div>

                                  <div>
                                    <p className="mb-1 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                                      {isManual
                                        ? "Description du consultant"
                                        : "Justification EnnoDiagnostic"}
                                    </p>
                                    <p className="whitespace-pre-wrap text-sm leading-6 text-foreground">
                                      {cleanDisplayText(
                                        verrou.justification ||
                                          "Aucune justification disponible."
                                      )}
                                    </p>
                                  </div>
                                </div>
                              </details>

                              {/* Décision : barre finale, comme sur la maquette */}
                              <section className="rounded-xl border border-brand/15 bg-white p-3">
                                <div className="mb-2 flex flex-col gap-2 px-1 sm:flex-row sm:items-center sm:justify-between">
                                  <div>
                                    <p className="text-xs font-semibold text-foreground">
                                      Décision du consultant
                                    </p>
                                    <p className="mt-0.5 text-[10px] text-muted-foreground">
                                      Le choix est enregistré immédiatement.
                                    </p>
                                  </div>
                                  <Badge
                                    variant="outline"
                                    className={`w-fit text-[10px] ${decisionClass(verrou.consultant_status)}`}
                                  >
                                    {decisionLabel(verrou.consultant_status)}
                                  </Badge>
                                </div>

                                <div
                                  className="grid gap-1 rounded-lg bg-muted/[0.42] p-1 sm:grid-cols-2"
                                  role="group"
                                  aria-label={`Décision pour ${verrou.title}`}
                                >
                                  <Button
                                    size="sm"
                                    variant={
                                      verrou.consultant_status === "garde"
                                        ? "default"
                                        : "ghost"
                                    }
                                    className="min-h-10 justify-center rounded-md text-xs"
                                    aria-pressed={verrou.consultant_status === "garde"}
                                    disabled={isLoading || !canDecide}
                                    onClick={() => {
                                      const verrouId =
                                        (verrou as any)?.id ??
                                        (verrou as any)?.verrou_id ??
                                        (verrou as any)?.db_id

                                      if (verrouId) updateDecision(verrouId, "garde")
                                    }}
                                  >
                                    {isLoading &&
                                    verrou.consultant_status === "garde" ? (
                                      <Loader2
                                        className="size-3.5 animate-spin"
                                        data-icon="inline-start"
                                      />
                                    ) : (
                                      <CheckCircle2
                                        className="size-3.5"
                                        data-icon="inline-start"
                                      />
                                    )}
                                    Retenir
                                  </Button>

                                  <Button
                                    size="sm"
                                    variant={
                                      verrou.consultant_status === "rejete"
                                        ? "destructive"
                                        : "ghost"
                                    }
                                    className="min-h-10 justify-center rounded-md text-xs"
                                    aria-pressed={verrou.consultant_status === "rejete"}
                                    disabled={isLoading || isJsonOnly}
                                    onClick={() => {
                                      if (!isJsonOnly) updateDecision(verrou.id, "rejete")
                                    }}
                                  >
                                    {isLoading &&
                                    verrou.consultant_status === "rejete" ? (
                                      <Loader2
                                        className="size-3.5 animate-spin"
                                        data-icon="inline-start"
                                      />
                                    ) : (
                                      <XCircle
                                        className="size-3.5"
                                        data-icon="inline-start"
                                      />
                                    )}
                                    Non retenir
                                  </Button>
                                </div>
                              </section>
                            </div>
                          </AccordionContent>
                        </AccordionItem>
                      )
                    })}
                  </Accordion>
                )}
              </CardContent>
            </Card>
          )}

          {diagnosticSection === "demarche" && (
            <BackendSectionCardV93
                          title="Pertinence des démarches"
                          description="Nécessité des étapes, distinction R&D / ingénierie classique et possibilité d’aller directement à la solution finale."
                          icon={Search}
                          text={demarcheText}
                          emptyText="Aucune démarche détectée."
                          projectId={project.id}
                          sourceDocuments={sourceDocuments}
                          structuredSection={demarcheStructuredSectionV194}
                          preserveDemarcheAudit
                        />
          )}

          {diagnosticSection === "resultats" && (
            <BackendSectionCardV93
                          title="Résultats / métriques"
                          description="Résultats chiffrés, observations qualitatives et éléments insuffisants à confirmer."
                          icon={TrendingUp}
                          text={resultatsText}
                          emptyText="Aucun résultat ou métrique disponible."
                          tone="success"
                          projectId={project.id}
                          sourceDocuments={sourceDocuments}
                          structuredSection={resultatsStructuredSectionV194}
                        />
          )}

          {diagnosticSection === "parametres" && (
            <BackendSectionCardV93
                          title="Paramètres et contraintes techniques"
                          description="Paramètres, jeux de données, conditions expérimentales et contraintes techniques."
                          icon={FileText}
                          text={parametresText}
                          emptyText="Aucun paramètre technique disponible."
                          projectId={project.id}
                          sourceDocuments={sourceDocuments}
                          structuredSection={parametresStructuredSectionV194}
                        />
          )}
        </TabsContent>

        <TabsContent value="controle-ia" className="space-y-4 motion-safe:animate-in motion-safe:fade-in motion-safe:duration-200">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Contrôle IA documentaire</CardTitle>
              <CardDescription className="text-xs">
                Score indicatif calculé sur les textes sources extraits avant reformulation LLM.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div className="p-3 rounded-md border">
                  <p className="text-xs text-muted-foreground">Score IA moyen</p>
                  <p className="text-2xl font-bold mt-1">{formatScore(aiScore)}</p>
                </div>
                <div className="p-3 rounded-md border">
                  <p className="text-xs text-muted-foreground">Niveau</p>
                  <Badge variant="outline" className={`mt-2 ${riskClass(aiRisk)}`}>
                    {aiRisk || "—"}
                  </Badge>
                </div>
                <div className="p-3 rounded-md border">
                  <p className="text-xs text-muted-foreground">Passages suspects</p>
                  <p className="text-2xl font-bold mt-1">{aiPassages.length}</p>
                </div>
              </div>

              {aiPassages.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  Aucun passage suspect exposé par le backend.
                </p>
              ) : (
                <div className="space-y-4">
                  {aiPassages.slice(0, 8).map((item: any, index: number) => {
                    const metadata =
                      item?.metadata && typeof item.metadata === "object"
                        ? item.metadata
                        : {}

                    const source =
                      item?.source && typeof item.source === "object"
                        ? item.source
                        : {}

                    const sourceJson =
                      item?.source_json && typeof item.source_json === "object"
                        ? item.source_json
                        : {}

                    const excerpt = cleanDisplayText(
                      String(
                        item?.text_excerpt ||
                          item?.source_text_original ||
                          item?.excerpt ||
                          item?.text ||
                          item?.source_text ||
                          metadata?.source_text_original ||
                          metadata?.excerpt ||
                          metadata?.text ||
                          ""
                      )
                    ).slice(0, 900)

                    const documentName = cleanDisplayText(
                      String(
                        item?.document ||
                          item?.document_name ||
                          item?.source_document ||
                          item?.filename ||
                          item?.file_name ||
                          item?.source_name ||
                          source?.document ||
                          source?.document_name ||
                          source?.filename ||
                          sourceJson?.document ||
                          sourceJson?.document_name ||
                          sourceJson?.source_document ||
                          sourceJson?.filename ||
                          metadata?.document ||
                          metadata?.document_name ||
                          metadata?.source_document ||
                          metadata?.filename ||
                          metadata?.file_name ||
                          metadata?.source_name ||
                          ""
                      )
                    )

                    const sourcePath = cleanDisplayText(
                      String(
                        item?.source_path ||
                          item?.path ||
                          source?.source_path ||
                          source?.path ||
                          sourceJson?.source_path ||
                          sourceJson?.path ||
                          metadata?.source_path ||
                          metadata?.path ||
                          ""
                      )
                    )

                    const evidence: SourceEvidence = {
                      ...item,
                      evidence_id:
                        item?.evidence_id ||
                        metadata?.evidence_id ||
                        `ai-passage-${item?.passage_id || index + 1}`,
                      rag_chunk_id:
                        item?.rag_chunk_id ||
                        metadata?.rag_chunk_id,
                      passage_id:
                        item?.passage_id ||
                        metadata?.passage_id ||
                        metadata?.original_passage_id,
                      document_id:
                        item?.document_id ||
                        source?.document_id ||
                        sourceJson?.document_id ||
                        metadata?.document_id,
                      document: documentName || undefined,
                      document_name: documentName || undefined,
                      filename:
                        item?.filename ||
                        source?.filename ||
                        sourceJson?.filename ||
                        metadata?.filename,
                      source_path: sourcePath || undefined,
                      page_number:
                        item?.page_number ??
                        source?.page_number ??
                        metadata?.page_number ??
                        metadata?.page,
                      paragraph_index:
                        item?.paragraph_index ??
                        source?.paragraph_index ??
                        metadata?.paragraph_index,
                      char_start:
                        item?.char_start ??
                        source?.char_start ??
                        metadata?.char_start ??
                        metadata?.start_char ??
                        metadata?.start,
                      char_end:
                        item?.char_end ??
                        source?.char_end ??
                        metadata?.char_end ??
                        metadata?.end_char ??
                        metadata?.end,
                      sentence_start:
                        item?.sentence_start ??
                        source?.sentence_start ??
                        metadata?.sentence_start,
                      section_title:
                        item?.section_title ||
                        source?.section_title ||
                        metadata?.section_title,
                      section_path:
                        item?.section_path ||
                        source?.section_path ||
                        metadata?.section_path,
                      role: "Passage suspect IA",
                      source_text_original: excerpt,
                      excerpt,
                      text: excerpt,
                      metadata,
                    }

                    return (
                      <div
                        key={`${item?.passage_id || item?.evidence_id || index}`}
                        className="min-w-0"
                      >
                        <div className="mb-1 flex flex-wrap items-center gap-2">
                          <Badge
                            variant="outline"
                            className={`text-xs ${riskClass(item?.risk_level)}`}
                          >
                            {item?.risk_level || "niveau inconnu"}
                          </Badge>

                          <span className="text-xs font-medium text-muted-foreground">
                            Score {formatScore(item?.ai_score)}
                          </span>
                        </div>

                        <p className="text-sm leading-7 text-foreground whitespace-pre-wrap">
                          {excerpt || "Passage non disponible."}
                          {excerpt ? (
                            <SourceEvidenceCitations
                              projectId={project.id}
                              documents={sourceDocuments}
                              evidence={[evidence]}
                              citationNumbers={[index + 1]}
                            />
                          ) : null}
                        </p>
                      </div>
                    )
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="cir-precedent" className="space-y-4 motion-safe:animate-in motion-safe:fade-in motion-safe:duration-200">
          <Card className="border-brand/20 bg-brand/5">
            <CardHeader>
              <CardTitle className="text-sm flex items-center gap-2">
                <Upload className="size-4 text-brand" />
                Ajouter un CIR final précédent
              </CardTitle>
              <CardDescription className="text-xs">
                Ce fichier sert de mémoire CIR N-1. Il n’est pas traité comme document brut du projet courant.
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-[160px_1fr_auto] gap-3 items-end">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-muted-foreground">
                    Année CIR
                  </label>
                  <input
                    type="number"
                    min="2000"
                    max="2100"
                    value={previousCirYear}
                    onChange={(event) => setPreviousCirYear(event.target.value)}
                    className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm"
                    placeholder="2022"
                  />
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-medium text-muted-foreground">
                    CIR final PDF / DOCX
                  </label>
                  <input
                    type="file"
                    accept=".pdf,.docx,.txt"
                    onChange={(event) => setPreviousCirFile(event.target.files?.[0] || null)}
                    className="block w-full text-sm text-muted-foreground file:mr-3 file:h-9 file:rounded-md file:border file:border-border file:bg-background file:px-3 file:text-sm file:font-medium file:text-foreground"
                  />
                </div>

                <Button
                  size="sm"
                  className="bg-brand hover:bg-brand/90"
                  onClick={uploadPreviousCir}
                  disabled={previousCirUploading || !previousCirFile}
                >
                  {previousCirUploading ? (
                    <Loader2 className="size-4 mr-2 animate-spin" />
                  ) : (
                    <FileText className="size-4 mr-2" />
                  )}
                  Enregistrer
                </Button>
              </div>

              {previousCirList.length > 0 && (
                <div className="rounded-lg border bg-white p-4 space-y-2">
                  <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                    CIR précédents enregistrés
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {previousCirList.map((item: any) => (
                      <Badge
                        key={item.year}
                        variant="outline"
                        className="text-xs bg-success/10 text-success border-success/30"
                      >
                        {item.year} · {item.items_count || 0} passages mémoire
                      </Badge>
                    ))}
                  </div>
                </div>
              )}

              {previousCirUploadReport && (
                <div className="rounded-lg border bg-success/5 border-success/20 p-4 space-y-2">
                  <p className="text-sm font-medium text-success">
                    CIR final {previousCirUploadReport.previous_cir_year} enregistré.
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {previousCirUploadReport.items_count || 0} passages ont été ajoutés à la mémoire CIR. Tu peux lancer la comparaison avec le CIR précédent sans réimporter le fichier et sans relancer EnnoDiagnostic.
                  </p>
                </div>
              )}
            </CardContent>
          </Card>

          <Card className="border-blue-200 bg-blue-50/70">
            <CardHeader>
              <CardTitle className="text-sm flex items-center gap-2">
                <RefreshCw className="size-4 text-blue-700" />
                Comparaison avec le CIR précédent
              </CardTitle>
              <CardDescription className="text-xs">
                {previousCirAvailable
                  ? "Un CIR précédent est déjà enregistré. Tu peux lancer directement la comparaison sans réimporter le fichier."
                  : "Aucun CIR précédent n’est encore enregistré. Ajoute d’abord un CIR final précédent pour activer la comparaison."}
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  className="bg-brand hover:bg-brand/90"
                  onClick={comparePreviousCirOnly}
                  disabled={running || previousCirCompareLoading || !previousCirAvailable}
                >
                  {previousCirCompareLoading ? (
                    <Loader2 className="size-4 mr-2 animate-spin" />
                  ) : (
                    <Play className="size-4 mr-2" />
                  )}
                  Lancer la comparaison CIR précédent
                </Button>

                <Button
                  variant="outline"
                  size="sm"
                  onClick={loadData}
                  disabled={running}
                >
                  <RefreshCw className="size-4 mr-2" />
                  Actualiser
                </Button>
              </div>

              {previousCirAvailable ? (
                <div className="rounded-lg border bg-white/80 p-3">
                  <p className="text-xs font-semibold text-blue-800 uppercase tracking-wide mb-2">
                    CIR précédent disponible
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {previousCirList.length > 0 ? (
                      previousCirList.map((item: any) => (
                        <Badge
                          key={String(item.year || item.previous_cir_year || Math.random())}
                          variant="outline"
                          className="text-xs bg-success/10 text-success border-success/30"
                        >
                          {item.year || item.previous_cir_year || "Année inconnue"} · {item.items_count || item.passages_count || 0} passages mémoire
                        </Badge>
                      ))
                    ) : previousCirDetectedYears.length > 0 ? (
                      previousCirDetectedYears.map((year) => (
                        <Badge
                          key={year}
                          variant="outline"
                          className="text-xs bg-success/10 text-success border-success/30"
                        >
                          CIR {year} détecté en mémoire
                        </Badge>
                      ))
                    ) : (
                      <Badge
                        variant="outline"
                        className="text-xs bg-success/10 text-success border-success/30"
                      >
                        CIR précédent enregistré
                      </Badge>
                    )}
                  </div>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Ajoute un CIR précédent pour lancer la comparaison.
                </p>
              )}
            </CardContent>
          </Card>

          <CirPreviousContinuityTab
            diagnostic={cirContinuityDiagnostic}
            projectId={project.id}
            apiBaseUrl={API_BASE_URL}
            authToken={getAccessToken() || undefined}
            organisme={project.organisme || ""}
            projectName={project.project_name || ""}
            currentYear={project.year || ""}
          />

          {reportSections?.comparaison_cir && (
            <details className="rounded-xl border bg-white p-4">
              <summary className="cursor-pointer text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                Voir la section brute générée dans le rapport
              </summary>
              <pre className="mt-3 whitespace-pre-wrap text-sm leading-relaxed font-sans text-muted-foreground">
                {cleanDisplayText(reportSections.comparaison_cir)}
              </pre>
            </details>
          )}
        </TabsContent>

        <TabsContent value="cir-final-consultant" className="space-y-4 motion-safe:animate-in motion-safe:fade-in motion-safe:duration-200">
          <Card className="overflow-hidden border-success/25 bg-gradient-to-br from-success/5 via-white to-white">
            <CardHeader className="border-b bg-white/75">
              <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div className="flex min-w-0 items-start gap-3">
                  <span className="grid size-10 shrink-0 place-items-center rounded-xl border border-success/20 bg-success/10 text-success">
                    <Upload className="size-5" aria-hidden="true" />
                  </span>
                  <div className="min-w-0">
                    <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-success">
                      Clôture du dossier
                    </p>
                    <CardTitle className="mt-1 text-base">
                      Déposer le CIR final réellement livré
                    </CardTitle>
                    <CardDescription className="mt-1 max-w-3xl text-xs leading-5">
                      EnnoSmart ne génère pas ici l’intégralité du CIR. Lorsque le consultant a terminé et validé la version définitive sur son poste, cette version doit être déposée pour conserver la référence réellement livrée et maintenir la mémoire CIR du projet à jour.
                    </CardDescription>
                  </div>
                </div>

                <Badge variant="outline" className="shrink-0 border-warning/30 bg-warning/10 text-warning">
                  À faire à la clôture
                </Badge>
              </div>
            </CardHeader>

            <CardContent className="grid gap-3 pt-5 md:grid-cols-2 xl:grid-cols-4">
              <div className="rounded-xl border bg-white p-3">
                <p className="text-xs font-semibold text-foreground">Version officielle</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  Conserver le document réellement remis au client ou utilisé pour la déclaration.
                </p>
              </div>
              <div className="rounded-xl border bg-white p-3">
                <p className="text-xs font-semibold text-foreground">Mémoire CIR à jour</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  La version finale devient une référence fiable pour les futurs travaux.
                </p>
              </div>
              <div className="rounded-xl border bg-white p-3">
                <p className="text-xs font-semibold text-foreground">Comparaison N / N-1</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  Le CIR de cette année pourra servir de repère historique lors du prochain exercice.
                </p>
              </div>
              <div className="rounded-xl border bg-white p-3">
                <p className="text-xs font-semibold text-foreground">Traçabilité</p>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  La plateforme conserve le lien entre analyse, décisions et livrable final.
                </p>
              </div>
            </CardContent>
          </Card>

          <CirFinalConsultantPanel
            projectId={project.id}
            apiBaseUrl={API_BASE_URL}
            authToken={getAccessToken() || undefined}
            defaultOrganisme={project.organisme || ""}
            defaultProject={project.project_name || ""}
            defaultYear={project.year || ""}
            onStatusChange={setCirFinalRegistered}
          />
        </TabsContent>

        <TabsContent value="comparaison-docs" className="space-y-4 motion-safe:animate-in motion-safe:fade-in motion-safe:duration-200">
          <Card>
            <CardHeader>
              <div className="flex items-start justify-between gap-3 flex-wrap">
                <div>
                  <CardTitle className="text-sm">Comparaison des documents bruts</CardTitle>
                  <CardDescription className="text-xs">
                    Détection automatique des paires proches puis comparaison A/B : commun, différent, seulement dans A, seulement dans B.
                  </CardDescription>
                </div>

                <Button
                  variant="outline"
                  size="sm"
                  onClick={detectDocumentPairs}
                  disabled={documentCompareLoading}
                >
                  {documentCompareLoading ? (
                    <Loader2 className="size-4 mr-2 animate-spin" />
                  ) : (
                    <RefreshCw className="size-4 mr-2" />
                  )}
                  Détecter les paires
                </Button>
              </div>
            </CardHeader>

            <CardContent className="space-y-4">
              <Card className="border-brand/20 bg-brand/5">
                <CardHeader>
                  <CardTitle className="text-sm">Comparaison manuelle A/B</CardTitle>
                  <CardDescription className="text-xs">
                    Comme dans Streamlit : charge directement deux documents, puis lance la comparaison sans passer par les paires automatiques.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div className="space-y-2">
                      <label className="text-xs font-medium text-muted-foreground">
                        Document A
                      </label>
                      <input
                        type="file"
                        className="block w-full text-sm border rounded-md p-2 bg-background"
                        accept=".pdf,.docx,.doc,.pptx,.ppt,.xlsx,.xls,.txt,.msg,.png,.jpg,.jpeg"
                        onChange={(event) => setManualFileA(event.target.files?.[0] || null)}
                      />
                      {manualFileA && (
                        <p className="text-xs text-muted-foreground break-all">
                          {manualFileA.name}
                        </p>
                      )}
                    </div>

                    <div className="space-y-2">
                      <label className="text-xs font-medium text-muted-foreground">
                        Document B
                      </label>
                      <input
                        type="file"
                        className="block w-full text-sm border rounded-md p-2 bg-background"
                        accept=".pdf,.docx,.doc,.pptx,.ppt,.xlsx,.xls,.txt,.msg,.png,.jpg,.jpeg"
                        onChange={(event) => setManualFileB(event.target.files?.[0] || null)}
                      />
                      {manualFileB && (
                        <p className="text-xs text-muted-foreground break-all">
                          {manualFileB.name}
                        </p>
                      )}
                    </div>
                  </div>

                  <div className="flex items-center gap-2 flex-wrap">
                    <Button
                      className="bg-brand hover:bg-brand/90"
                      onClick={compareManualFiles}
                      disabled={documentCompareLoading || !manualFileA || !manualFileB}
                    >
                      {documentCompareLoading && selectedPairIndex === null ? (
                        <Loader2 className="size-4 mr-2 animate-spin" />
                      ) : (
                        <FileText className="size-4 mr-2" />
                      )}
                      Comparer manuellement
                    </Button>

                    <Button
                      variant="outline"
                      onClick={() => {
                        setManualFileA(null)
                        setManualFileB(null)
                        setDocumentCompareReport(null)
                        setSelectedPairIndex(null)
                      }}
                      disabled={documentCompareLoading}
                    >
                      Réinitialiser
                    </Button>
                  </div>
                </CardContent>
              </Card>

              <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                <div className="p-3 rounded-md border">
                  <p className="text-xs text-muted-foreground">Paires détectées</p>
                  <p className="text-2xl font-bold mt-1">{docComparePairsCount || 0}</p>
                </div>

                <div className="p-3 rounded-md border">
                  <p className="text-xs text-muted-foreground">Différences</p>
                  <p className="text-2xl font-bold mt-1">{docCompareSummary?.different_count ?? "—"}</p>
                </div>

                <div className="p-3 rounded-md border">
                  <p className="text-xs text-muted-foreground">Seulement A</p>
                  <p className="text-2xl font-bold mt-1">{docCompareSummary?.only_in_a_count ?? "—"}</p>
                </div>

                <div className="p-3 rounded-md border">
                  <p className="text-xs text-muted-foreground">Seulement B</p>
                  <p className="text-2xl font-bold mt-1">{docCompareSummary?.only_in_b_count ?? "—"}</p>
                </div>
              </div>

              {docComparePairs.length === 0 ? (
                <div className="p-6 text-center border border-dashed rounded-lg">
                  <p className="text-sm font-medium text-foreground">
                    Aucune paire détectée pour le moment.
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Clique sur “Détecter les paires” pour analyser les fichiers du dossier raw.
                  </p>
                </div>
              ) : (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground">
                    Paires proposées
                  </p>

                  {docComparePairs.map((pair: any, index: number) => (
                    <div
                      key={`${pair.name_a}-${pair.name_b}-${index}`}
                      className={`p-3 rounded-md border ${
                        selectedPairIndex === index ? "border-brand bg-brand/5" : "border-border bg-background"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3 flex-wrap">
                        <div className="flex-1 min-w-0">
                          <div className="flex gap-2 flex-wrap mb-2">
                            <Badge variant="outline" className={`text-xs ${docCompareDecisionClass(pair.decision)}`}>
                              {pair.decision || "paire"}
                            </Badge>
                            <Badge variant="outline" className="text-xs">
                              Similarité {formatScore(pair.similarity)}
                            </Badge>
                            <Badge variant="outline" className="text-xs">
                              {pair.reason || "raison non renseignée"}
                            </Badge>
                          </div>

                          <p className="text-sm font-medium text-foreground break-all">
                            A — {pair.name_a}
                          </p>
                          <p className="text-sm font-medium text-foreground break-all mt-1">
                            B — {pair.name_b}
                          </p>

                          {Array.isArray(pair.common_tokens) && pair.common_tokens.length > 0 && (
                            <p className="text-xs text-muted-foreground mt-2">
                              Tokens communs : {pair.common_tokens.slice(0, 8).join(", ")}
                            </p>
                          )}
                        </div>

                        <Button
                          size="sm"
                          className="bg-brand hover:bg-brand/90"
                          disabled={documentCompareLoading}
                          onClick={() => compareSelectedPair(index)}
                        >
                          {documentCompareLoading && selectedPairIndex === index ? (
                            <Loader2 className="size-4 mr-2 animate-spin" />
                          ) : (
                            <Search className="size-4 mr-2" />
                          )}
                          Comparer
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {documentCompareReport && (
                <Card className="overflow-hidden border-brand/30">
                  <CardHeader className="border-b bg-gradient-to-r from-brand/[0.045] via-white to-white">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <CardTitle className="text-sm">
                          Comparaison visuelle A / B
                        </CardTitle>
                        <CardDescription className="mt-1 text-xs">
                          Les deux documents sources complets sont affichés côte à côte. Sélectionnez un passage : il est recherché et surligné automatiquement dans chaque document.
                        </CardDescription>
                      </div>

                      <Badge
                        variant="outline"
                        className="shrink-0 border-brand/30 bg-white text-brand"
                      >
                        {formatScore(docCompareSummary?.change_rate)} de changement
                      </Badge>
                    </div>
                  </CardHeader>

                  <CardContent className="space-y-5 pt-5">
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
                      <div className="rounded-xl border bg-white p-3">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                          Changement
                        </p>
                        <p className="mt-1 text-xl font-semibold text-foreground">
                          {formatScore(docCompareSummary?.change_rate)}
                        </p>
                      </div>

                      <div className="rounded-xl border bg-success/[0.045] p-3">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-success">
                          Commun
                        </p>
                        <p className="mt-1 text-xl font-semibold text-foreground">
                          {docCompareSummary?.identical_count ?? 0}
                        </p>
                      </div>

                      <div className="rounded-xl border bg-warning/[0.045] p-3">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-warning">
                          Différent
                        </p>
                        <p className="mt-1 text-xl font-semibold text-foreground">
                          {docCompareSummary?.different_count ?? 0}
                        </p>
                      </div>

                      <div className="rounded-xl border bg-blue-50/40 p-3">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-blue-700">
                          Seulement A
                        </p>
                        <p className="mt-1 text-xl font-semibold text-foreground">
                          {docCompareSummary?.only_in_a_count ?? 0}
                        </p>
                      </div>

                      <div className="rounded-xl border bg-violet-50/40 p-3">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-violet-700">
                          Seulement B
                        </p>
                        <p className="mt-1 text-xl font-semibold text-foreground">
                          {docCompareSummary?.only_in_b_count ?? 0}
                        </p>
                      </div>
                    </div>

                    <DocumentComparisonSideBySideV201
                      summary={docCompareSummary}
                      comparison={docCompareComparison}
                      projectId={project.id}
                      sourceDocuments={sourceDocuments}
                    />
                  </CardContent>
                </Card>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="validation" className="space-y-4 motion-safe:animate-in motion-safe:fade-in motion-safe:duration-200">
          <Card
            className={
              cirFinalRegistered
                ? "overflow-hidden rounded-2xl border-success/30 bg-success/[0.045] shadow-sm"
                : "overflow-hidden rounded-2xl border-warning/30 bg-warning/[0.045] shadow-sm"
            }
          >
            <CardContent className="flex flex-col gap-4 p-5 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex min-w-0 items-start gap-3">
                <span
                  className={`grid size-10 shrink-0 place-items-center rounded-xl border ${
                    cirFinalRegistered
                      ? "border-success/30 bg-white text-success"
                      : "border-warning/30 bg-white text-warning"
                  }`}
                >
                  {cirFinalRegistered ? (
                    <CheckCircle2 className="size-5" aria-hidden="true" />
                  ) : (
                    <Upload className="size-5" aria-hidden="true" />
                  )}
                </span>

                <div className="min-w-0">
                  <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                    Clôture du dossier
                  </p>
                  <p className="mt-1 text-sm font-semibold text-foreground">
                    {cirFinalRegistered
                      ? "Validation finale complétée"
                      : "La revue est terminée, le CIR final reste à déposer"}
                  </p>
                  <p className="mt-1 max-w-3xl text-xs leading-5 text-muted-foreground">
                    {cirFinalRegistered
                      ? "La version réellement livrée est archivée dans EnnoSmart et pourra servir de référence historique pour les exercices suivants."
                      : "La validation ne passe au vert qu’après dépôt de la version CIR finale réellement livrée par le consultant."}
                  </p>
                </div>
              </div>

              {cirFinalRegistered ? (
                <Badge
                  variant="outline"
                  className="shrink-0 border-success/30 bg-white text-success"
                >
                  CIR final archivé
                </Badge>
              ) : (
                <Button
                  className="shrink-0"
                  onClick={() => setActiveTab("cir-final-consultant")}
                >
                  Déposer le CIR final
                  <ArrowRight className="size-4" data-icon="inline-end" />
                </Button>
              )}
            </CardContent>
          </Card>

          <div className="grid gap-3 sm:grid-cols-4">
            {["garde", "reformuler", "rejete", "en_attente"].map((status) => {
              const count = decisions[status as keyof typeof decisions]

              return (
                <div key={status} className="rounded-xl border bg-card px-4 py-3">
                  <p className="text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                    {decisionLabel(status)}
                  </p>
                  <p className="mt-1 text-xl font-semibold text-foreground">{count}</p>
                </div>
              )
            })}
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Décisions consultant</CardTitle>
              <CardDescription className="text-xs">
                Résumé des décisions prises sur les verrous.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {["garde", "reformuler", "rejete", "en_attente"].map((status) => {
                const items = verrous.filter((v) => v.consultant_status === status)

                return (
                  <div key={status} className="p-3 rounded-md border border-border">
                    <div className="flex items-center justify-between gap-2 mb-2">
                      <p className="text-sm font-medium text-foreground">
                        {decisionLabel(status)}
                      </p>
                      <Badge variant="outline" className={`text-xs ${decisionClass(status)}`}>
                        {items.length}
                      </Badge>
                    </div>

                    {items.length === 0 ? (
                      <p className="text-xs text-muted-foreground">
                        Aucun verrou dans cette catégorie.
                      </p>
                    ) : (
                      <div className="space-y-1">
                        {items.map((item) => (
                          <p key={item.id} className="text-xs text-muted-foreground">
                            • {item.title}
                          </p>
                        ))}
                      </div>
                    )}
                  </div>
                )
              })}
            </CardContent>
          </Card>


        </TabsContent>
      </Tabs>

      <DiagnosticRagChat
        projectId={project.id}
        refreshToken={`${project.id}:${hasDiagnostic ? "diagnostic" : "pending"}:${pipelineStats?.chunks_indexed ?? 0}:${latestRun?.id ?? ""}`}
      />
    </div>
  )
}
