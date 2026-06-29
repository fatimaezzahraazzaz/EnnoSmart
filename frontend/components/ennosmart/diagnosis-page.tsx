"use client"

import {
  useEffect,
  useMemo,
  useRef,
  useState } from "react"
import {
  AlertCircle,
  AlertTriangle,
  BrainCircuit,
  CheckCircle2,
  FileText,
  Loader2,
  Lock,
  Play,
  RefreshCw,
  Search,
  Sparkles,
  Target,
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
import { Progress } from "@/components/ui/progress"
import { Tabs,
  TabsContent,
  TabsList,
  TabsTrigger } from "@/components/ui/tabs"

import {
  getAccessToken,
  getDiagnosticLatest,
  getDocuments,
  getProjects,
  getVerrous,
  importExistingDiagnostic,
  runDiagnostic,
  syncVerrous,
  updateVerrouDecision,
  type DocumentRead,
  type ProjectRead,
  type VerrouRead,
} from "@/lib/api"
import { getCurrentProjectId, setCurrentProjectId } from "@/lib/project-session"
import { CirFinalConsultantPanel } from "@/components/ennosmart/cir-final-consultant-panel"
import CirPreviousContinuityTab from "@/components/ennosmart/cir-previous-continuity-tab"

import { SourceTextWithDocuments, useProjectSourceDocuments, type DbSourceDocument } from "@/components/ennosmart/source-documents-dialog"
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


function firstNonEmptyArray(...values: any[]) {
  for (const value of values) {
    if (Array.isArray(value) && value.length > 0) return value
  }
  return []
}

function unwrapCirPreviousReportForDisplay(value: any): any {
  if (!value || typeof value !== "object") return {}

  const candidates = [
    value?.report,
    value?.comparison_report,
    value?.cir_memory_report,
    value?.cir_previous_report,
    value?.previous_cir_report,
    value?.data,
    value?.result,
    value,
  ]

  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== "object") continue
    if (
      candidate?.has_previous_cir === true ||
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

function getSources(verrou: VerrouRead) {
  const sourceJson = verrou.source_json || {}
  const sources =
    sourceJson.sources ||
    sourceJson.evidence_sources ||
    sourceJson.source_documents ||
    sourceJson.source_ids

  if (Array.isArray(sources)) {
    return sources
      .map((source: any) => {
        if (typeof source === "string") return source
        return (
          source?.filename ||
          source?.source ||
          source?.document ||
          source?.name ||
          source?.id ||
          source?.source_id
        )
      })
      .filter(Boolean)
      .slice(0, 6)
  }

  const document =
    sourceJson.document ||
    sourceJson.source_document ||
    sourceJson.filename ||
    sourceJson.document_name

  return document ? [document] : []
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

function getVerrouExplanationSections(verrou: VerrouRead): VerrouExplanationSections {
  const sourceJson = verrou.source_json || {}
  const sources = getSources(verrou)

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

function shortSourceText(verrou: VerrouRead, limit = 650) {
  const raw = cleanDisplayText(getSourceText(verrou))
    .replace(/^Verrou implicite possible\s*—\s*/i, "")
    .replace(/Question de qualification\s*:\s*[^.?!]+[.?!]?\s*/i, "")
    .replace(/Ce verrou est reconstruit[^.?!]+[.?!]?\s*/i, "")
    .replace(/Documents concernés\s*:\s*/i, "Documents concernés : ")
    .trim()

  if (raw.length <= limit) return raw || "Aucun extrait source disponible."
  return `${raw.slice(0, limit).trim()}…`
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

  return "Piste à examiner : choisir Retenir, À consolider ou Non retenir après lecture des preuves."
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
    <div className="overflow-x-auto rounded-xl border bg-white">
      <table className="min-w-full divide-y divide-border text-sm">
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
      <div className="space-y-4">
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
    <div className="space-y-3">
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
            <ul key={index} className="list-disc space-y-2 pl-5 text-sm leading-7 text-muted-foreground">
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
            <ol key={index} className="list-decimal space-y-2 pl-5 text-sm leading-7 text-muted-foreground">
              {numberedLines.map((line, lineIndex) => (
                <li key={lineIndex}>
                  <InlineMarkdownV93 text={line.replace(/^\d+[.)]\s+/, "")} />
                </li>
              ))}
            </ol>
          )
        }

        return (
          <p key={index} className="text-sm leading-7 text-muted-foreground whitespace-pre-wrap">
            <InlineMarkdownV93 text={paragraph} />
          </p>
        )
      })}
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
    <Card className={toneClass}>
      <CardHeader>
        <CardTitle className="text-sm flex items-center gap-2">
          {Icon ? <Icon className="size-4 text-brand" /> : null}
          {title}
        </CardTitle>
        {description ? (
          <CardDescription className="text-xs">
            {description}
          </CardDescription>
        ) : null}
      </CardHeader>
      <CardContent>
        <div className="rounded-xl border bg-white/80 p-4">
          {text?.trim() ? (
            <BackendSectionRendererV93 text={text} projectId={projectId} sourceDocuments={sourceDocuments} enableSourceDocs={enableSourceDocs} />
          ) : (
            <p className="text-sm text-muted-foreground">{emptyText}</p>
          )}
        </div>
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

  const response = await fetch(`${API_BASE_URL}/projects/${projectId}/scholar/latest`, {
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

  const response = await fetch(`${API_BASE_URL}/projects/${projectId}/articles`, {
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

async function runScholarFromSelectedVerrous(projectId: number) {
  const token = getAccessToken()

  if (!token) {
    throw new Error("Utilisateur non authentifié.")
  }

  const response = await fetch(
    `${API_BASE_URL}/projects/${projectId}/scholar/run-from-selected-verrous?max_verrous=8&limit_per_query=3&offline_dry_run=false`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
      },
    }
  )

  const data = await response.json().catch(() => null)

  if (!response.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : "Erreur lancement EnnoScholar.")
  }

  return data
}

async function syncScholarArticles(projectId: number, runId: number) {
  const token = getAccessToken()

  if (!token) {
    throw new Error("Utilisateur non authentifié.")
  }

  const response = await fetch(`${API_BASE_URL}/projects/${projectId}/scholar/${runId}/sync-articles`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  const data = await response.json().catch(() => null)

  if (!response.ok) {
    throw new Error(typeof data?.detail === "string" ? data.detail : "Erreur synchronisation articles.")
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

export function DiagnosisPage() {
  const [activeTab, setActiveTab] = useState("overview")
  const [loading, setLoading] = useState(true)
  const [actionLoadingId, setActionLoadingId] = useState<number | null>(null)
  const [error, setError] = useState("")
  const [project, setProject] = useState<ProjectRead | null>(null)
  const [projects, setProjects] = useState<ProjectRead[]>([])
  const [documents, setDocuments] = useState<DocumentRead[]>([])
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
  const [scholarLoading, setScholarLoading] = useState(false)

  const [runningMode, setRunningMode] = useState<RunMode>(null)
  const [progress, setProgress] = useState(0)
  const [currentStepIndex, setCurrentStepIndex] = useState(0)

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

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
    display?.frascati_summary?.average_frascati_score ??
    display?.frascati_score ??
    prepareReport?.nlp_stats?.global_frascati_score ??
    null

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

  const cirMemory = previousCirComparisonReport || display?.cir_memory || {}
  const cirMemoryOk = Boolean(display?.cir_memory_ok || cirMemory?.ok)
  const cirMemoryHasPrevious = Boolean(display?.cir_memory_has_previous || cirMemory?.has_previous_cir)
  const cirMemorySummary = display?.cir_memory_summary || cirMemory?.summary || {}
  const cirMemoryPreviousYears = firstNonEmptyArray(display?.cir_memory_previous_years, cirMemory?.previous_cir_years_used, cirMemory?.previous_years)
  const cirMemoryNoveltyScore = display?.cir_memory_project_novelty_score ?? cirMemorySummary?.project_novelty_score
  const cirMemorySignal = display?.cir_memory_signal || cirMemorySummary?.frascati_context_signal
  const cirMemoryExplanation = display?.cir_memory_explanation || cirMemorySummary?.frascati_context_explanation
  const cirMemoryNewVerrous = firstNonEmptyArray(display?.cir_memory_new_verrous, cirMemory?.new_or_not_found)
  const cirMemoryEvolutions = firstNonEmptyArray(display?.cir_memory_evolutions, cirMemory?.evolution_or_partial_continuity)
  const cirMemoryContinuities = firstNonEmptyArray(display?.cir_memory_continuities, cirMemory?.continuity_strong)
  const cirMemoryComparisons = display?.cir_memory_verrou_comparisons || cirMemory?.verrou_comparisons || previousCirComparisonReport?.verrou_comparisons || previousCirComparisonReport?.comparisons || []

  // V102.2 - Le CIR précédent peut déjà exister en mémoire même si la comparaison
  // n'a pas encore été recalculée dans le rapport EnnoDiagnostic.
  // IMPORTANT : ces constantes doivent être déclarées AVANT cirContinuityDiagnostic.
  const previousCirAvailable = previousCirList.length > 0 || Boolean(previousCirUploadReport)

  const previousCirYearsAvailable = useMemo(() => {
    const years = previousCirList
      .map((item: any) => String(item?.year || item?.previous_cir_year || "").trim())
      .filter(Boolean)

    if (years.length > 0) return years

    const uploadYear = String(previousCirUploadReport?.previous_cir_year || previousCirYear || "").trim()
    return uploadYear ? [uploadYear] : []
  }, [previousCirList, previousCirUploadReport, previousCirYear])

  const previousCirItemsCount = useMemo(() => {
    const fromList = previousCirList.reduce((total: number, item: any) => {
      return total + Number(item?.items_count || item?.passages_count || item?.count || 0)
    }, 0)

    return fromList || Number(previousCirUploadReport?.items_count || 0)
  }, [previousCirList, previousCirUploadReport])

  const cirContinuityDiagnostic = useMemo(() => {
    const hasPreviousCir = cirMemoryHasPrevious || previousCirAvailable || Boolean(previousCirComparisonReport?.has_previous_cir)
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
        ...(previousCirComparisonReport || {}),
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

  const decisions = useMemo(() => countByDecision(verrous), [verrous])
  const sourceDocuments = useProjectSourceDocuments(project?.id)

  const backendMarkdownV93 = useMemo(() => {
    return getBackendDiagnosticMarkdownV93(diagnosticBundle, display)
  }, [diagnosticBundle, display])

  const backendSectionsV93 = useMemo(() => {
    return getBackendDiagnosticSectionsV93(diagnosticBundle, display)
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
    return pickBackendSectionV93(backendSectionsV93, backendMarkdownV93, [
      "Lecture Frascati du dossier",
    ])
  }, [backendSectionsV93, backendMarkdownV93])

  const frascatiJustificationText = useMemo(() => {
    return getBackendFrascatiJustificationV94(
      diagnosticBundle,
      backendSectionsV93,
      backendMarkdownV93
    )
  }, [diagnosticBundle, backendSectionsV93, backendMarkdownV93])

  const diagnosticReportJsonV107 = useMemo(() => {
    return unwrapBackendDiagnosticReportV93(diagnosticBundle)
  }, [diagnosticBundle])

  const jsonVerrousV107 = useMemo(() => {
    return collectJsonVerrousV107(
      diagnosticReportJsonV107,
      diagnosticBundle,
      display,
      prepareReport
    )
  }, [diagnosticReportJsonV107, diagnosticBundle, display, prepareReport])

  const verrousForDisplay = useMemo(() => {
    // V123 : quand syncVerrous a enregistré les verrous reformulés en base,
    // on privilégie la base pour garder les décisions consultant actives.
    const syncedLlmVerrous = verrous
      .filter((verrou) => {
        const sourceJson = verrou.source_json || {}
        const source = String(sourceJson.source || sourceJson.source_name || "").toLowerCase()
        const frontendSourceKey = String(sourceJson.frontend_source_key || "").toLowerCase()
        const displayTitleSource = String(sourceJson.display_title_source || "").toLowerCase()
        const synthesisVersion = String(sourceJson.verrou_synthesis_version || sourceJson.version || "").toLowerCase()

        return (
          source.includes("llm_reformulated") ||
          source.includes("consultant_verrous_cir") ||
          source.includes("grouped_rag") ||
          frontendSourceKey.includes("llm_reformulated") ||
          frontendSourceKey.includes("consultant_verrous_cir") ||
          displayTitleSource.includes("llm") ||
          synthesisVersion.includes("v122") ||
          synthesisVersion.includes("v123")
        )
      })
      .filter((verrou) => !isFallbackVerrouReadV124(verrou))

    if (syncedLlmVerrous.length > 0) return syncedLlmVerrous
    if (jsonVerrousV107.length > 0) return jsonVerrousV107

    return verrous.filter((verrou) => !isFallbackVerrouReadV124(verrou))
  }, [jsonVerrousV107, verrous])

  const verrousText = useMemo(() => {
    const fromDisplay = verrousToMarkdownV107(verrousForDisplay)
    if (fromDisplay) return fromDisplay

    return pickBackendSectionV93(backendSectionsV93, backendMarkdownV93, [
      "Verrous R&D / signaux de verrous",
      "Verrous CIR consolidés",
      "Verrous R&D",
      "Verrous",
    ])
  }, [verrousForDisplay, backendSectionsV93, backendMarkdownV93])

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

  const validationText = useMemo(() => {
    return pickBackendSectionV93(backendSectionsV93, backendMarkdownV93, [
      "Points à valider par le consultant",
      "Points à valider",
      "Points de validation",
    ])
  }, [backendSectionsV93, backendMarkdownV93])

  const methods = useMemo(() => {
    const text = demarcheText || pickSection(display, ["demarche", "démarche", "methodes", "méthodes"])
    return text
      ? [text]
      : [
          "Préparation des sources par extraction et NLP",
          "Indexation des passages techniques dans Chroma",
          "Récupération des méthodes et essais depuis les sources projet",
        ]
  }, [demarcheText, display])

  const results = useMemo(() => {
    const text = resultatsText || pickSection(display, ["resultats", "résultats", "results"])
    return text
      ? [text]
      : [
          "Résultats techniques disponibles après diagnostic",
          "Métriques et observations à confirmer par le consultant",
        ]
  }, [resultatsText, display])

  const validationPoints = useMemo(() => {
    const text = validationText || pickSection(display, ["points_validation", "points_a_valider", "validation"])
    return text
      ? [text]
      : [
          "Confirmer les verrous réellement R&D",
          "Valider les sources et les résultats techniques",
          "Séparer contraintes industrielles et incertitudes technologiques",
        ]
  }, [validationText, display])

  const hasDiagnostic = Boolean(backendMarkdownV93 || latestRun || verrousForDisplay.length > 0 || reportMarkdown)

  const loadData = async () => {
    setLoading(true)
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
        setPreviousCirList([])
        return
      }

      const storedProjectId = getCurrentProjectId()
      const selectedProject =
        projectList.find((item) => item.id === storedProjectId) || projectList[0]

      setCurrentProjectId(selectedProject.id)
      setProject(selectedProject)

      const [verrousData, documentsData, diagnosticData, compareIndexData, scholarData, articlesData, previousCirData, previousCirComparisonData] = await Promise.all([
        getVerrous(selectedProject.id).catch(() => []),
        getDocuments(selectedProject.id).catch(() => []),
        getDiagnosticLatest(selectedProject.id).catch(() => null),
        getDocumentComparePairs(selectedProject.id, false).catch(() => null),
        getScholarLatest(selectedProject.id).catch(() => null),
        getArticles(selectedProject.id).catch(() => []),
        getPreviousCirFinals(selectedProject.id).catch(() => []),
        getPreviousCirComparisonLatest(selectedProject.id).catch(() => null),
      ])

      setVerrous(verrousData)
      setDocuments(documentsData)
      setDiagnosticBundle(diagnosticData)
      setDocumentCompareIndex(compareIndexData)
      setScholarBundle(scholarData)
      setArticles(articlesData)
      setPreviousCirList(previousCirData)
      const latestCirComparison = await getPreviousCirComparisonLatest(selectedProject.id).catch(() => null)
      setCirPreviousComparisonReport(latestCirComparison)
      setPreviousCirComparisonReport(previousCirComparisonData)
      const previousComparison = await getPreviousCirComparisonLatest(selectedProject.id).catch(() => null)
      if (previousComparison && previousComparison.ok) setPreviousCirComparisonReport(previousComparison)
      if (!previousCirYear) {
        const previousYear = Number(selectedProject.year)
        setPreviousCirYear(Number.isFinite(previousYear) ? String(previousYear - 1) : "")
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de charger EnnoDiagnostic."
      )
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [])

  const currentSteps =
    runningMode === "prepare"
      ? prepareSteps
      : runningMode === "agent"
        ? agentSteps
        : fullSteps

  const startProgress = (mode: RunMode) => {
    setRunningMode(mode)
    setProgress(4)
    setCurrentStepIndex(0)

    if (intervalRef.current) clearInterval(intervalRef.current)

    intervalRef.current = setInterval(() => {
      setProgress((prev) => {
        const next = Math.min(prev + 4, 92)
        const stepIndex = Math.min(
          Math.floor((next / 100) * currentSteps.length),
          currentSteps.length - 1
        )

        setCurrentStepIndex(stepIndex)
        return next
      })
    }, 900)
  }

  const stopProgress = (success = true) => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }

    if (success) {
      setProgress(100)
      setCurrentStepIndex(currentSteps.length - 1)
    }
  }

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

    try {
      const selectedProject = projects.find((item) => item.id === projectId) || null
      setProject(selectedProject)

      const [verrousData, documentsData, diagnosticData, compareIndexData, scholarData, articlesData, previousCirData, previousCirComparisonData] = await Promise.all([
        getVerrous(projectId).catch(() => []),
        getDocuments(projectId).catch(() => []),
        getDiagnosticLatest(projectId).catch(() => null),
        getDocumentComparePairs(projectId, false).catch(() => null),
        getScholarLatest(projectId).catch(() => null),
        getArticles(projectId).catch(() => []),
        getPreviousCirFinals(projectId).catch(() => []),
        getPreviousCirComparisonLatest(projectId).catch(() => null),
      ])

      setVerrous(verrousData)
      setDocuments(documentsData)
      setDiagnosticBundle(diagnosticData)
      setDocumentCompareIndex(compareIndexData)
      setDocumentCompareReport(null)
      setSelectedPairIndex(null)
      setScholarBundle(scholarData)
      setArticles(articlesData)
      setPreviousCirList(previousCirData)
      setPreviousCirComparisonReport(previousCirComparisonData)
      const previousComparison = await getPreviousCirComparisonLatest(projectId).catch(() => null)
      if (previousComparison && previousComparison.ok) setPreviousCirComparisonReport(previousComparison)
      const previousYear = Number(selectedProject?.year)
      setPreviousCirYear(Number.isFinite(previousYear) ? String(previousYear - 1) : "")
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


  const comparePreviousCirOnly = async () => {
    if (!project) return

    if (!previousCirAvailable) {
      setError("Ajoute d’abord un CIR précédent avant de lancer la comparaison.")
      return
    }

    setPreviousCirCompareLoading(true)
    setError("")
    setActiveTab("cir-precedent")

    try {
      const report = await compareCurrentWithPreviousCir(project.id)
      setPreviousCirComparisonReport(report)
      await loadData()
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


  const runCirPreviousComparison = async () => {
    if (!project) return

    setError("")
    setActiveTab("cir-precedent")
    startProgress("agent")

    try {
      const report = await compareCurrentWithPreviousCir(project.id)
      setPreviousCirComparisonReport(report)
      await loadData()
      setActiveTab("cir-precedent")
      stopProgress(true)
    } catch (err) {
      stopProgress(false)
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de lancer la comparaison avec le CIR précédent."
      )
    } finally {
      setTimeout(() => setRunningMode(null), 700)
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

  const launchEnnoScholar = async () => {
    if (!project) return

    if (selectedVerrousForScholar.length === 0) {
      setError("Sélectionne au moins un verrou avec le statut Gardé avant de lancer EnnoScholar.")
      return
    }

    setScholarLoading(true)
    setError("")

    try {
      const run = await runScholarFromSelectedVerrous(project.id)
      const runId = run?.id

      if (runId) {
        await syncScholarArticles(project.id, runId).catch(() => [])
      }

      const [latestScholar, latestArticles] = await Promise.all([
        getScholarLatest(project.id).catch(() => null),
        getArticles(project.id).catch(() => []),
      ])

      setScholarBundle(latestScholar)
      setArticles(latestArticles)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de lancer EnnoScholar."
      )
    } finally {
      setScholarLoading(false)
    }
  }

  const updateDecision = async (
    verrouId: number,
    decision: ConsultantDecision
  ) => {
    if (!project) return

    setActionLoadingId(verrouId)

    try {
      const updated = await updateVerrouDecision(project.id, verrouId, decision)

      setVerrous((prev) =>
        prev.map((verrou) => (verrou.id === verrouId ? updated : verrou))
      )
    } catch (err) {
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
      <div className="p-6 max-w-7xl mx-auto">
        <Card>
          <CardContent className="p-8 flex items-center justify-center gap-3 text-muted-foreground">
            <Loader2 className="size-5 animate-spin" />
            Chargement du diagnostic depuis FastAPI...
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
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <div className="size-7 rounded-md bg-brand flex items-center justify-center">
              <BrainCircuit className="size-4 text-brand-foreground" />
            </div>
            <h1 className="text-2xl font-bold text-foreground">
              EnnoDiagnostic
            </h1>
          </div>
          <p className="text-sm text-muted-foreground">
            {project.organisme} — {project.project_name} — {project.year}
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            Domaine : {project.domain_label || "Non renseigné"} · Dossier ID #{project.id} · Documents uploadés : {documents.length}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {projects.length > 1 && (
            <select
              value={project.id}
              onChange={(event) => changeProject(Number(event.target.value))}
              className="h-9 rounded-md border border-border bg-background px-3 text-sm"
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
            size="sm"
            onClick={prepareSources}
            disabled={running}
          >
            {runningMode === "prepare" ? (
              <Loader2 className="size-4 mr-2 animate-spin" />
            ) : (
              <Search className="size-4 mr-2" />
            )}
            Préparer les sources
          </Button>

          <Button
            className="bg-brand hover:bg-brand/90"
            size="sm"
            onClick={runAgentOnly}
            disabled={running}
          >
            {runningMode === "agent" ? (
              <Loader2 className="size-4 mr-2 animate-spin" />
            ) : (
              <Play className="size-4 mr-2" />
            )}
            Lancer EnnoDiagnostic
          </Button>

          <Button variant="outline" size="sm" onClick={loadData} disabled={running}>
            <RefreshCw className="size-4 mr-2" />
            Actualiser
          </Button>
        </div>
      </div>

      {running && (
        <Card className="border-brand/30 bg-brand/5">
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <Loader2 className="size-4 animate-spin text-brand" />
              {runningMode === "prepare"
                ? "Préparation des sources en cours"
                : runningMode === "agent"
                  ? "Agent EnnoDiagnostic en cours"
                  : "EnnoDiagnostic complet en cours"}
            </CardTitle>
            <CardDescription className="text-xs">
              Le backend exécute la chaîne demandée. Cette étape peut prendre du temps selon le nombre de fichiers.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Progress value={progress} className="h-2" />
            <div className="flex items-center justify-between gap-3 text-xs">
              <p className="font-medium text-foreground">
                {currentSteps[currentStepIndex]}
              </p>
              <p className="text-muted-foreground">{Math.round(progress)}%</p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-2 pt-2">
              {currentSteps.map((step, index) => (
                <div
                  key={step}
                  className={`p-2 rounded-md border text-xs ${
                    index <= currentStepIndex
                      ? "bg-success/10 border-success/30 text-success"
                      : "bg-background border-border text-muted-foreground"
                  }`}
                >
                  {index <= currentStepIndex ? "✓ " : "• "}
                  {step}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {error && (
        <Card className="border-destructive/30 bg-destructive/10">
          <CardContent className="p-4 flex items-start gap-3 text-destructive">
            <AlertCircle className="size-5 mt-0.5" />
            <div>
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
          <CardContent className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={prepareSources}>
              <Search className="size-4 mr-2" />
              Préparer les sources
            </Button>
            <Button className="bg-brand hover:bg-brand/90" onClick={runAgentOnly}>
              <Play className="size-4 mr-2" />
              Lancer EnnoDiagnostic
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-6 gap-4">
        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Documents extraits</p>
            <p className="text-2xl font-bold text-foreground mt-1">
              {pipelineStats?.documents_loaded_count ?? "—"}
            </p>
            <p className="text-[11px] text-muted-foreground mt-1">
              Raw : {prepareReport?.documents_used_count ?? "—"}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Score Frascati</p>
            <p className="text-2xl font-bold text-warning mt-1">
              {formatScore(frascatiScore)}
            </p>
            <Badge variant="outline" className={`text-xs mt-1 ${riskClass(frascatiRisk)}`}>
              Risque {frascatiRisk || "—"}
            </Badge>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Score IA</p>
            <p className="text-2xl font-bold text-success mt-1">
              {formatScore(aiScore)}
            </p>
            <Badge variant="outline" className={`text-xs mt-1 ${riskClass(aiRisk)}`}>
              {aiRisk || "—"}
            </Badge>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">CIR précédent</p>
            <p className="text-2xl font-bold text-brand mt-1">
              {noveltyPercent(cirMemoryNoveltyScore)}
            </p>
            <Badge
              variant="outline"
              className={`text-xs mt-1 ${comparisonBadgeClass(cirMemorySignal)}`}
            >
              {cirMemoryHasPrevious ? "Comparé" : "Absent"}
            </Badge>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Paires docs</p>
            <p className="text-2xl font-bold text-brand mt-1">
              {docComparePairsCount || "—"}
            </p>
            <Badge variant="outline" className="text-xs mt-1">
              Comparaison A/B
            </Badge>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">EnnoScholar</p>
            <p className="text-2xl font-bold text-brand mt-1">
              {scholarSummary?.verrous_analyzed ?? scholarResults.length ?? "—"}
            </p>
            <Badge variant="outline" className="text-xs mt-1">
              {selectedVerrousForScholar.length} verrou(s) sélectionné(s)
            </Badge>
          </CardContent>
        </Card>
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
        <TabsList className="grid grid-cols-2 lg:grid-cols-9 h-auto">
          <TabsTrigger value="overview">Vue d’ensemble</TabsTrigger>
          <TabsTrigger value="diagnostic">Diagnostic CIR</TabsTrigger>
          <TabsTrigger value="controle-ia">Contrôle IA</TabsTrigger>
          <TabsTrigger value="cir-precedent">CIR précédent</TabsTrigger>
          <TabsTrigger value="cir-final-consultant">CIR final consultant</TabsTrigger>
          <TabsTrigger value="comparaison-docs">Comparaison docs</TabsTrigger>
          <TabsTrigger value="ennoscholar">EnnoScholar</TabsTrigger>
          <TabsTrigger value="rapport">Rapport complet</TabsTrigger>
          <TabsTrigger value="validation">Validation</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm flex items-center gap-2">
                <Sparkles className="size-4 text-brand" />
                Synthèse stratégique
              </CardTitle>
              <CardDescription className="text-xs">
                Synthèse issue du rapport EnnoDiagnostic généré par l’agent.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="rounded-xl border bg-muted/20 p-4">
                <BackendSectionRendererV93 text={summary} />
              </div>
            </CardContent>
          </Card>

        </TabsContent>

        <TabsContent value="diagnostic" className="space-y-4">
          <BackendSectionCardV93
            title="Objectif global"
            icon={Target}
            text={objective}
            emptyText="L’objectif global apparaîtra après l’exécution d’EnnoDiagnostic."
          />

          {lectureFrascatiText && (
            <BackendSectionCardV93
              title="Lecture Frascati du dossier"
              description="Lecture du potentiel CIR à partir des scores Frascati et du contrôle IA documentaire."
              icon={AlertTriangle}
              text={lectureFrascatiText}
              tone="warning"
            />
          )}

          {frascatiJustificationText && (
            <BackendSectionCardV93
              title="Justification Frascati du score"
              description="Justification spécifique au projet courant. Le score reste calculé par le NLP/Frascati ; le LLM explique le score à partir des preuves RAG/Chroma."
              icon={AlertCircle}
              text={frascatiJustificationText}
              tone="brand"
            />
          )}

          <BackendSectionCardV93
            title="Verrous CIR consolidés"
            description="Affichage prioritaire des verrous reformulés par EnnoDiagnostic V122. Les chunks RAG/NLP bruts ne sont utilisés qu’en fallback."
            icon={Lock}
            text={verrousText}
            emptyText="Aucun verrou CIR consolidé généré. Lance EnnoDiagnostic pour générer les verrous reformulés."
            projectId={project?.id || 0}
            sourceDocuments={sourceDocuments}
            enableSourceDocs
            tone="brand"
          />

          <Card>
            <CardHeader>
              <CardTitle className="text-sm flex items-center gap-2">
                <Lock className="size-4 text-brand" />
                Verrous synchronisés pour validation
              </CardTitle>
              <CardDescription className="text-xs">
Les verrous reformulés par EnnoDiagnostic sont affichés en priorité. Les décisions restent actives uniquement pour les verrous synchronisés en base.
              </CardDescription>
            </CardHeader>

            <CardContent>
              {verrousForDisplay.length === 0 ? (
                <div className="p-6 text-center border border-dashed rounded-lg">
                  <p className="text-sm font-medium text-foreground">
                    Aucun verrou synchronisé pour ce projet.
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Lance EnnoDiagnostic puis synchronise les verrous.
                  </p>
                </div>
              ) : (
                <Accordion type="single" className="w-full space-y-3">
                  {verrousForDisplay.map((verrou) => {
                    const sources = getSources(verrou)
                    const isLoading = actionLoadingId === verrou.id
                    const isJsonOnly = isJsonOnlyVerrouV107(verrou)
                    const explanationSections = getVerrouExplanationSections(verrou)
                    const whyVerrou = getShortVerrouRationale(verrou)
                    const action = consultantAction(verrou)
                    const excerpt = shortSourceText(verrou)
                    const sourceTextForDocs = `${verrou.title || ""} ${verrou.justification || ""} ${excerpt || ""} ${JSON.stringify(verrou.source_json || {})}`

                    return (
                      <AccordionItem
                        key={verrou.id}
                        value={String(verrou.id)}
                        className="rounded-xl border bg-white shadow-sm overflow-hidden"
                      >
                        <AccordionTrigger className="hover:bg-muted/30 px-4 py-4">
                          <div className="flex items-start gap-3 text-left flex-1">
                            <div className="flex-1 space-y-2">
                              <div className="flex items-center gap-2 flex-wrap">
                                <Badge variant="outline" className="text-xs bg-brand/10 text-brand border-brand/30">
                                  Signal R&D détecté
                                </Badge>
                                <Badge
                                  variant="outline"
                                  className={`text-xs ${decisionClass(verrou.consultant_status)}`}
                                >
                                  {decisionLabel(verrou.consultant_status)}
                                </Badge>
                                <Badge variant="outline" className="text-xs">
                                  Score {formatVerrouScoreV124(verrou.score)}
                                </Badge>
                              </div>

                              <p className="text-sm font-semibold text-foreground">
                                {verrou.title}
                              </p>

                              <p className="text-xs text-muted-foreground leading-relaxed">
                                {cleanDisplayText(whyVerrou).slice(0, 220) || "Verrou à confirmer à partir des preuves sources."}
                              </p>
                            </div>
                          </div>
                        </AccordionTrigger>

                        <AccordionContent className="px-4 pb-4 bg-muted/20">
                          <div className="space-y-4">
                            <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
                              <div className="rounded-lg border bg-white p-4 space-y-3">
                                <p className="text-xs font-semibold text-brand uppercase tracking-wide">
                                  Pourquoi EnnoDiagnostic le détecte comme verrou
                                </p>
                                <p className="text-sm leading-7 text-foreground">
                                  {explanationSections.detection}
                                </p>

                                {explanationSections.uncertainty && (
                                  <div className="rounded-md bg-muted/40 p-3 space-y-1">
                                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                                      Incertitude technique formulée
                                    </p>
                                    <p className="text-sm leading-7 text-foreground">
                                      {explanationSections.uncertainty}
                                    </p>
                                  </div>
                                )}

                                {explanationSections.notSimpleEngineering && (
                                  <div className="rounded-md bg-muted/40 p-3 space-y-1">
                                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                                      Pourquoi ce n’est pas une simple ingénierie
                                    </p>
                                    <p className="text-sm leading-7 text-foreground">
                                      {explanationSections.notSimpleEngineering}
                                    </p>
                                  </div>
                                )}

                                {explanationSections.evidence && (
                                  <div className="rounded-md bg-muted/40 p-3 space-y-1">
                                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                                      Preuves sources utilisées
                                    </p>
                                    <p className="text-sm leading-7 text-foreground">
                                      {explanationSections.evidence}
                                    </p>
                                  </div>
                                )}
                              </div>

                              <div className="rounded-lg border bg-white p-4 space-y-2">
                                <p className="text-xs font-semibold text-brand uppercase tracking-wide">
                                  Ce que le consultant doit vérifier
                                </p>
                                <p className="text-sm leading-7 text-foreground">
                                  {getConsultantCheckText(verrou)}
                                </p>
                              </div>
                            </div>

                            {sources.length > 0 && (
                              <div className="rounded-lg border bg-white p-4 space-y-2">
                                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                                  Documents sources
                                </p>
                                <div className="flex flex-wrap gap-2">
                                  {sources.map((source, index) => (
                                    <Badge
                                      key={`${source}-${index}`}
                                      variant="secondary"
                                      className="text-xs max-w-full whitespace-normal text-left"
                                    >
                                      {source}
                                    </Badge>
                                  ))}
                                </div>
                              </div>
                            )}

                            <div className="rounded-lg border bg-white p-4 space-y-2">
                              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                                Extrait source utile
                              </p>
                              <SourceTextWithDocuments
                                projectId={project?.id || 0}
                                text={sourceTextForDocs || excerpt}
                                documents={sourceDocuments}
                              />
                            </div>

                            <div className="rounded-lg border bg-brand/5 border-brand/20 p-4 space-y-2">
                              <p className="text-xs font-semibold text-brand uppercase tracking-wide">
                                Action consultant
                              </p>
                              <p className="text-sm leading-7 text-foreground">
                                {action}
                              </p>
                            </div>

                            <details className="rounded-lg border bg-white p-4">
                              <summary className="cursor-pointer text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                                Traçabilité technique
                              </summary>

                              <div className="mt-3 space-y-3">
                                <div>
                                  <p className="text-xs font-medium text-muted-foreground mb-1">
                                    Titre initial détecté
                                  </p>
                                  <p className="text-sm text-foreground">
                                    {verrou.title}
                                  </p>
                                </div>

                                <div>
                                  <p className="text-xs font-medium text-muted-foreground mb-1">
                                    Justification EnnoDiagnostic
                                  </p>
                                  <p className="text-sm text-foreground whitespace-pre-wrap">
                                    {cleanDisplayText(verrou.justification || "Aucune justification disponible.")}
                                  </p>
                                </div>

                                <div className="flex gap-2 flex-wrap">
                                  <Badge variant="outline" className={`text-xs ${tagClass(verrou.tag_cir)}`}>
                                    {verrou.tag_cir || "Verrou à vérifier"}
                                  </Badge>
                                  <Badge variant="outline" className="text-xs">
                                    Score Frascati {formatVerrouScoreV124(verrou.score)}
                                  </Badge>
                                </div>
                              </div>
                            </details>

                            {isJsonOnly && (
                              <div className="rounded-lg border border-warning/20 bg-warning/5 p-3">
                                <p className="text-xs text-warning font-medium">
                                  Ce verrou vient directement du JSON diagnostic. Pour activer les décisions consultant, lance ou vérifie la synchronisation backend des verrous reformulés.
                                </p>
                              </div>
                            )}

                            <div className="flex flex-wrap gap-2 pt-1">
                              <Button
                                size="sm"
                                className="text-xs h-8 bg-brand hover:bg-brand/90"
                                disabled={isLoading || isJsonOnly}
                                onClick={() => {
                                  if (!isJsonOnly) updateDecision(verrou.id, "garde")
                                }}
                              >
                                <CheckCircle2 className="size-3 mr-1" />
                                Retenir
                              </Button>

                              <Button
                                size="sm"
                                variant="outline"
                                className="text-xs h-8"
                                disabled={isLoading || isJsonOnly}
                                onClick={() => {
                                  if (!isJsonOnly) updateDecision(verrou.id, "reformuler")
                                }}
                              >
                                <RefreshCw className="size-3 mr-1" />
                                À consolider
                              </Button>

                              <Button
                                size="sm"
                                variant="outline"
                                className="text-xs h-8 text-muted-foreground border-border hover:bg-muted"
                                disabled={isLoading || isJsonOnly}
                                onClick={() => {
                                  if (!isJsonOnly) updateDecision(verrou.id, "rejete")
                                }}
                              >
                                <XCircle className="size-3 mr-1" />
                                Non retenir
                              </Button>
                            </div>
                          </div>
                        </AccordionContent>
                      </AccordionItem>
                    )
                  })}
                </Accordion>
              )}
            </CardContent>
          </Card>


          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <BackendSectionCardV93
              title="Démarche détectée"
              description="Méthodes, protocoles, essais ou analyses détectés dans les documents sources."
              icon={Search}
              text={demarcheText}
              emptyText="Aucune démarche détectée."
            />

            <BackendSectionCardV93
              title="Résultats / métriques"
              description="Résultats chiffrés, observations qualitatives et éléments insuffisants à confirmer."
              icon={TrendingUp}
              text={resultatsText}
              emptyText="Aucun résultat ou métrique disponible."
              tone="success"
            />

            <BackendSectionCardV93
              title="Paramètres et contraintes techniques"
              description="Contraintes de pression, débit, environnement, mécanique ou acoustique."
              icon={FileText}
              text={parametresText}
              emptyText="Aucun paramètre technique disponible."
            />

            <BackendSectionCardV93
              title="Points à valider"
              description="Contrôles à faire par le consultant avant décision d’éligibilité CIR."
              icon={AlertCircle}
              text={validationText}
              emptyText="Aucun point de validation disponible."
              tone="warning"
            />
          </div>

        </TabsContent>


        <TabsContent value="controle-ia" className="space-y-4">
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
                <div className="space-y-3">
                  {aiPassages.slice(0, 8).map((item: any, index: number) => (
                    <div key={`${item.passage_id || index}`} className="p-3 rounded-md border bg-muted/30">
                      <div className="flex items-center justify-between gap-2 mb-2 flex-wrap">
                        <Badge variant="outline" className={`text-xs ${riskClass(item.risk_level)}`}>
                          {item.risk_level || "niveau inconnu"}
                        </Badge>
                        <Badge variant="outline" className="text-xs">
                          Score {formatScore(item.ai_score)}
                        </Badge>
                      </div>
                      <p className="text-xs text-muted-foreground mb-1">
                        {item.document || "Document non renseigné"}
                      </p>
                      <p className="text-sm whitespace-pre-wrap">
                        {(item.text_excerpt || item.text || "").slice(0, 900)}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="cir-precedent" className="space-y-4">
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
                    ) : (
                      <Badge variant="outline" className="text-xs bg-success/10 text-success border-success/30">
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

        <TabsContent value="cir-final-consultant" className="space-y-4">
          <CirFinalConsultantPanel
            projectId={project.id}
            apiBaseUrl={API_BASE_URL}
            authToken={getAccessToken() || undefined}
            defaultOrganisme={project.organisme || ""}
            defaultProject={project.project_name || ""}
            defaultYear={project.year || ""}
          />
        </TabsContent>

        <TabsContent value="comparaison-docs" className="space-y-4">
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
                <Card className="border-brand/30">
                  <CardHeader>
                    <CardTitle className="text-sm">Rapport de comparaison A/B</CardTitle>
                    <CardDescription className="text-xs">
                      {docCompareSummary?.doc_a || "Document A"} VS {docCompareSummary?.doc_b || "Document B"}
                    </CardDescription>
                  </CardHeader>

                  <CardContent className="space-y-4">
                    <div className="grid grid-cols-1 md:grid-cols-5 gap-3">
                      <div className="p-3 rounded-md border">
                        <p className="text-xs text-muted-foreground">Taux changement</p>
                        <p className="text-xl font-bold mt-1">{formatScore(docCompareSummary?.change_rate)}</p>
                      </div>
                      <div className="p-3 rounded-md border">
                        <p className="text-xs text-muted-foreground">Commun</p>
                        <p className="text-xl font-bold mt-1">{docCompareSummary?.identical_count ?? 0}</p>
                      </div>
                      <div className="p-3 rounded-md border">
                        <p className="text-xs text-muted-foreground">Différent</p>
                        <p className="text-xl font-bold mt-1">{docCompareSummary?.different_count ?? 0}</p>
                      </div>
                      <div className="p-3 rounded-md border">
                        <p className="text-xs text-muted-foreground">Seulement A</p>
                        <p className="text-xl font-bold mt-1">{docCompareSummary?.only_in_a_count ?? 0}</p>
                      </div>
                      <div className="p-3 rounded-md border">
                        <p className="text-xs text-muted-foreground">Seulement B</p>
                        <p className="text-xl font-bold mt-1">{docCompareSummary?.only_in_b_count ?? 0}</p>
                      </div>
                    </div>

                    <Accordion type="single" className="w-full">
                      <AccordionItem value="different">
                        <AccordionTrigger>
                          Différent entre A et B ({docCompareComparison?.different_between_a_b?.length || 0})
                        </AccordionTrigger>
                        <AccordionContent className="space-y-3">
                          {(docCompareComparison?.different_between_a_b || []).slice(0, 12).map((item: any, index: number) => (
                            <div key={`diff-${index}`} className="p-3 rounded-md border bg-warning/5">
                              <div className="flex gap-2 mb-2 flex-wrap">
                                <Badge variant="outline" className="text-xs bg-warning/10 text-warning border-warning/30">
                                  score {formatScore(item.score)}
                                </Badge>
                                {item.numeric_conflict && (
                                  <Badge variant="outline" className="text-xs bg-destructive/10 text-destructive border-destructive/30">
                                    conflit numérique
                                  </Badge>
                                )}
                              </div>
                              <p className="text-xs font-semibold text-muted-foreground mb-1">A</p>
                              <p className="text-sm whitespace-pre-wrap mb-3">{shortDocText(item.a_text, 900)}</p>
                              <p className="text-xs font-semibold text-muted-foreground mb-1">B</p>
                              <p className="text-sm whitespace-pre-wrap">{shortDocText(item.b_text, 900)}</p>
                            </div>
                          ))}
                        </AccordionContent>
                      </AccordionItem>

                      <AccordionItem value="only-a">
                        <AccordionTrigger>
                          Seulement dans A ({docCompareComparison?.only_in_a?.length || 0})
                        </AccordionTrigger>
                        <AccordionContent className="space-y-3">
                          {(docCompareComparison?.only_in_a || []).slice(0, 12).map((item: any, index: number) => (
                            <div key={`only-a-${index}`} className="p-3 rounded-md border bg-muted/30">
                              <Badge variant="outline" className="text-xs mb-2">
                                {item.context_key || "contexte inconnu"}
                              </Badge>
                              <p className="text-sm whitespace-pre-wrap">{shortDocText(item.text, 900)}</p>
                            </div>
                          ))}
                        </AccordionContent>
                      </AccordionItem>

                      <AccordionItem value="only-b">
                        <AccordionTrigger>
                          Seulement dans B ({docCompareComparison?.only_in_b?.length || 0})
                        </AccordionTrigger>
                        <AccordionContent className="space-y-3">
                          {(docCompareComparison?.only_in_b || []).slice(0, 12).map((item: any, index: number) => (
                            <div key={`only-b-${index}`} className="p-3 rounded-md border bg-muted/30">
                              <Badge variant="outline" className="text-xs mb-2">
                                {item.context_key || "contexte inconnu"}
                              </Badge>
                              <p className="text-sm whitespace-pre-wrap">{shortDocText(item.text, 900)}</p>
                            </div>
                          ))}
                        </AccordionContent>
                      </AccordionItem>

                      <AccordionItem value="identical">
                        <AccordionTrigger>
                          Commun aux deux ({docCompareComparison?.identical?.length || 0})
                        </AccordionTrigger>
                        <AccordionContent className="space-y-3">
                          {(docCompareComparison?.identical || []).slice(0, 10).map((item: any, index: number) => (
                            <div key={`same-${index}`} className="p-3 rounded-md border bg-success/5">
                              <Badge variant="outline" className="text-xs bg-success/10 text-success border-success/30 mb-2">
                                score {formatScore(item.score)}
                              </Badge>
                              <p className="text-sm whitespace-pre-wrap">{shortDocText(item.text, 700)}</p>
                            </div>
                          ))}
                        </AccordionContent>
                      </AccordionItem>
                    </Accordion>
                  </CardContent>
                </Card>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="ennoscholar" className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-start justify-between gap-3 flex-wrap">
                <div>
                  <CardTitle className="text-sm">EnnoScholar — validation scientifique des verrous</CardTitle>
                  <CardDescription className="text-xs">
                    Seuls les verrous gardés par le consultant sont envoyés vers les bases scientifiques.
                  </CardDescription>
                </div>

                <Button
                  className="bg-brand hover:bg-brand/90"
                  onClick={launchEnnoScholar}
                  disabled={scholarLoading || selectedVerrousForScholar.length === 0}
                >
                  {scholarLoading ? (
                    <Loader2 className="size-4 mr-2 animate-spin" />
                  ) : (
                    <Search className="size-4 mr-2" />
                  )}
                  Lancer EnnoScholar
                </Button>
              </div>
            </CardHeader>

            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
                <div className="p-3 rounded-md border">
                  <p className="text-xs text-muted-foreground">Verrous sélectionnés</p>
                  <p className="text-2xl font-bold mt-1">{selectedVerrousForScholar.length}</p>
                </div>

                <div className="p-3 rounded-md border">
                  <p className="text-xs text-muted-foreground">Verrous analysés</p>
                  <p className="text-2xl font-bold mt-1">{scholarSummary?.verrous_analyzed ?? scholarResults.length ?? 0}</p>
                </div>

                <div className="p-3 rounded-md border">
                  <p className="text-xs text-muted-foreground">Défendables</p>
                  <p className="text-2xl font-bold mt-1">
                    {scholarDecisionCounts?.verrou_scientifiquement_defendable ?? 0}
                  </p>
                </div>

                <div className="p-3 rounded-md border">
                  <p className="text-xs text-muted-foreground">Articles</p>
                  <p className="text-2xl font-bold mt-1">{articles.length || scholarSummary?.articles_total || 0}</p>
                </div>
              </div>

              {selectedVerrousForScholar.length === 0 && (
                <div className="p-4 rounded-md border border-warning/30 bg-warning/5">
                  <p className="text-sm font-medium text-warning">
                    Aucun verrou sélectionné pour EnnoScholar.
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    Va dans l’onglet Validation, choisis les verrous réellement utiles, puis mets leur statut sur “Gardé”.
                  </p>
                </div>
              )}

              {scholarGroupingActive && (
                <Card className="border-brand/30 bg-brand/5">
                  <CardHeader>
                    <CardTitle className="text-sm">Regroupement automatique avant EnnoScholar</CardTitle>
                    <CardDescription className="text-xs">
                      Les signaux gardés par le consultant ont été regroupés en verrous scientifiques uniques avant la recherche, pour éviter les doublons.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                      <div className="p-3 rounded-md border bg-background/70">
                        <p className="text-xs text-muted-foreground">Signaux retenus</p>
                        <p className="text-xl font-bold mt-1">{scholarGroupingSummary?.input_signals_count ?? selectedVerrousForScholar.length}</p>
                      </div>
                      <div className="p-3 rounded-md border bg-background/70">
                        <p className="text-xs text-muted-foreground">Verrous scientifiques envoyés</p>
                        <p className="text-xl font-bold mt-1">{scholarGroupingSummary?.grouped_verrous_count ?? scholarResults.length}</p>
                      </div>
                      <div className="p-3 rounded-md border bg-background/70">
                        <p className="text-xs text-muted-foreground">Doublons regroupés</p>
                        <p className="text-xl font-bold mt-1">{scholarGroupingSummary?.duplicates_removed ?? 0}</p>
                      </div>
                    </div>

                    {scholarGroupingGroups.length > 0 && (
                      <div className="space-y-2">
                        {scholarGroupingGroups.map((group: any, index: number) => (
                          <div key={`${group.group_key || group.consolidated_title || index}`} className="p-3 rounded-md border bg-background/80">
                            <div className="flex items-center gap-2 flex-wrap mb-2">
                              <Badge variant="outline" className="text-xs">
                                {group.grouped_count || 1} signal{Number(group.grouped_count || 1) > 1 ? "s" : ""} regroupé{Number(group.grouped_count || 1) > 1 ? "s" : ""}
                              </Badge>
                              <Badge variant="outline" className="text-xs">
                                {group.profile || "profil scientifique"}
                              </Badge>
                            </div>
                            <p className="text-sm font-medium text-foreground">
                              {group.consolidated_title || "Verrou scientifique consolidé"}
                            </p>
                            <p className="text-xs text-muted-foreground mt-1">
                              Regroupe : {(group.grouped_original_titles || []).join(" ; ") || "signal unique"}
                            </p>
                            <p className="text-xs text-muted-foreground mt-1">
                              Pourquoi : {group.reason || "même objet technique ou même phénomène scientifique détecté dans les sources"}
                            </p>
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              )}

              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Signaux retenus par le consultant</CardTitle>
                  <CardDescription className="text-xs">
                    Cette liste montre la décision du consultant. La recherche EnnoScholar utilise ensuite les verrous scientifiques regroupés ci-dessus.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {selectedVerrousForScholar.length === 0 ? (
                    <p className="text-sm text-muted-foreground">Aucun verrou gardé.</p>
                  ) : (
                    selectedVerrousForScholar.map((verrou) => (
                      <div key={verrou.id} className="p-3 rounded-md border bg-success/5">
                        <div className="flex items-center gap-2 flex-wrap mb-2">
                          <Badge variant="outline" className={decisionClass(verrou.consultant_status)}>
                            {decisionLabel(verrou.consultant_status)}
                          </Badge>
                          <Badge variant="outline" className="text-xs">
                            Score {formatVerrouScoreV124(verrou.score)}
                          </Badge>
                        </div>
                        <p className="text-sm font-medium text-foreground">
                          {verrou.title}
                        </p>
                        <p className="text-xs text-muted-foreground mt-1 whitespace-pre-wrap">
                          {getSourceText(verrou).slice(0, 600)}
                        </p>
                      </div>
                    ))
                  )}
                </CardContent>
              </Card>

              {scholarResults.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">Résultats de validation scientifique</CardTitle>
                    <CardDescription className="text-xs">
                      Décision automatique à valider par le consultant : défendable, à confirmer, support faible ou aucun article.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    {scholarResults.map((result: any, index: number) => (
                      <div key={`${result.verrou_id}-${index}`} className="p-3 rounded-md border">
                        <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
                          <Badge variant="outline" className={scholarDecisionClass(result.decision)}>
                            {scholarDecisionLabel(result.decision)}
                          </Badge>

                          <div className="flex gap-2 flex-wrap">
                            <Badge variant="outline" className="text-xs">
                              Support {formatScore(result.scientific_support_score)}
                            </Badge>
                            <Badge variant="outline" className="text-xs">
                              Articles {result.articles_found ?? result.articles?.length ?? 0}
                            </Badge>
                          </div>
                        </div>

                        <p className="text-sm font-semibold text-foreground">
                          {result.verrou_title || result.title || `Verrou ${index + 1}`}
                        </p>

                        {result.scientific_intent?.scientific_problem && (
                          <p className="text-xs text-muted-foreground mt-2 whitespace-pre-wrap">
                            Problème scientifique : {result.scientific_intent.scientific_problem}
                          </p>
                        )}

                        {result.gap_analysis && (
                          <p className="text-sm text-foreground mt-2 whitespace-pre-wrap">
                            {result.gap_analysis}
                          </p>
                        )}

                        {result.consultant_action && (
                          <p className="text-xs text-muted-foreground mt-2">
                            Action consultant : {result.consultant_action}
                          </p>
                        )}

                        {Array.isArray(result.queries) && result.queries.length > 0 && (
                          <Accordion type="single" className="mt-3">
                            <AccordionItem value="queries">
                              <AccordionTrigger>Requêtes scientifiques</AccordionTrigger>
                              <AccordionContent className="space-y-2">
                                {result.queries.slice(0, 6).map((query: any, qIndex: number) => (
                                  <div key={qIndex} className="p-2 rounded-md bg-muted/30 border">
                                    <p className="text-xs font-medium">{query.kind || "query"}</p>
                                    <p className="text-sm">{query.query || String(query)}</p>
                                  </div>
                                ))}
                              </AccordionContent>
                            </AccordionItem>
                          </Accordion>
                        )}

                        {Array.isArray(result.articles) && result.articles.length > 0 && (
                          <Accordion type="single" className="mt-3">
                            <AccordionItem value="articles">
                              <AccordionTrigger>Articles trouvés</AccordionTrigger>
                              <AccordionContent className="space-y-2">
                                {result.articles.slice(0, 5).map((article: any, aIndex: number) => (
                                  <div key={aIndex} className="p-3 rounded-md border bg-background">
                                    <div className="flex gap-2 flex-wrap mb-2">
                                      <Badge variant="outline" className={tagClass(article.tag || article.tag_article)}>
                                        {article.tag || article.tag_article || "Article"}
                                      </Badge>
                                      <Badge variant="outline" className="text-xs">
                                        Score {formatScore(article.relevance_score || article.score)}
                                      </Badge>
                                      {article.year && (
                                        <Badge variant="outline" className="text-xs">
                                          {article.year}
                                        </Badge>
                                      )}
                                    </div>
                                    <p className="text-sm font-medium">{article.title}</p>
                                    {article.abstract && (
                                      <p className="text-xs text-muted-foreground mt-1 whitespace-pre-wrap">
                                        {article.abstract.slice(0, 500)}
                                      </p>
                                    )}
                                  </div>
                                ))}
                              </AccordionContent>
                            </AccordionItem>
                          </Accordion>
                        )}
                      </div>
                    ))}
                  </CardContent>
                </Card>
              )}

              <Card>
                <CardHeader>

          <div className="mt-6">
          </div>


                  <CardTitle className="text-sm">Articles synchronisés</CardTitle>
                  <CardDescription className="text-xs">
                    Articles sauvegardés côté backend et reliés aux verrous quand l’information est disponible.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {articles.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      Aucun article synchronisé pour le moment.
                    </p>
                  ) : (
                    articles.slice(0, 20).map((article) => {
                      const validation = articleValidationFromSource(article)
                      return (
                        <div key={article.id} className="p-3 rounded-md border">
                          <div className="flex gap-2 flex-wrap mb-2">
                            <Badge variant="outline" className={tagClass(article.tag_article)}>
                              {article.tag_article || "Article"}
                            </Badge>
                            <Badge variant="outline" className="text-xs">
                              Score {formatScore(article.score)}
                            </Badge>
                            {article.year && (
                              <Badge variant="outline" className="text-xs">
                                {article.year}
                              </Badge>
                            )}
                            {validation?.scientific_decision && (
                              <Badge variant="outline" className={scholarDecisionClass(validation.scientific_decision)}>
                                {scholarDecisionLabel(validation.scientific_decision)}
                              </Badge>
                            )}
                          </div>

                          <p className="text-sm font-medium text-foreground">
                            {article.title}
                          </p>

                          <p className="text-xs text-muted-foreground mt-1">
                            Source : {article.source || "—"} | Verrou lié : {article.verrou_id || "—"}
                          </p>

                          {article.url && (
                            <a
                              href={article.url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-xs text-brand underline mt-2 inline-block"
                            >
                              Ouvrir l’article
                            </a>
                          )}
                        </div>
                      )
                    })
                  )}
                </CardContent>
              </Card>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="rapport" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Rapport EnnoDiagnostic complet</CardTitle>
              <CardDescription className="text-xs">
                Contenu brut retourné par display.report_markdown.
              </CardDescription>
            </CardHeader>
            <CardContent>
              {(backendMarkdownV93 || reportMarkdown) ? (
                <div className="rounded-xl border bg-muted/20 p-4">
                  <BackendSectionRendererV93 text={backendMarkdownV93 || reportMarkdown} />
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">
                  Aucun rapport disponible. Lance EnnoDiagnostic.
                </p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="validation" className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            {["garde", "reformuler", "rejete", "en_attente"].map((status) => {
              const count = decisions[status as keyof typeof decisions]

              return (
                <Card key={status}>
                  <CardContent className="p-4">
                    <p className="text-xs text-muted-foreground">
                      {decisionLabel(status)}
                    </p>
                    <p className="text-2xl font-bold text-foreground mt-1">{count}</p>
                  </CardContent>
                </Card>
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
    </div>
  )
}
