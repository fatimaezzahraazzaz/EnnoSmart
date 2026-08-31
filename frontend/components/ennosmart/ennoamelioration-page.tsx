"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import {
  Bot,
  Check,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  FileText,
  FileUp,
  History,
  Library,
  Loader2,
  Maximize2,
  Minimize2,
  MessageSquareText,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  RotateCcw,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  createImprovementSession,
  decideImprovementSources,
  decideImprovementVersion,
  deleteImprovementSession,
  getDocuments,
  getImprovementBackgroundJob,
  getImprovementProjectContext,
  getImprovementSession,
  getImprovementSourceDocument,
  getProjects,
  listImprovementSessions,
  restoreImprovementVersion,
  sendImprovementMessage,
  uploadDocument,
  type DocumentRead,
  type ImprovementBackgroundJob,
  type ImprovementSession,
  type ImprovementProjectContext,
  type ImprovementSection,
  type ImprovementVersion,
  type ProjectRead,
} from "@/lib/api"
import { getCurrentProjectId, setCurrentProjectId } from "@/lib/project-session"
import { cn } from "@/lib/utils"
import { LoadingState } from "@/components/ennosmart/workspace-ui"
import { ImprovementPdfComparator } from "@/components/ennosmart/improvement-pdf-comparator"

function normalizeSourceDecision(value: unknown) {
  const decision = String(value || "").trim().toLowerCase()
  if (["accept", "accepted", "garde", "kept"].includes(decision)) return "accepted"
  if (["reject", "rejected", "rejete", "rejeté", "ecarte", "écarté"].includes(decision)) return "rejected"
  return "pending"
}

function isDirectPdfUrl(value: unknown) {
  const url = String(value || "").trim().toLowerCase()
  return url.endsWith(".pdf") || url.includes("/pdf/") || url.endsWith("/document")
}

function publicationSiteUrl(source: Record<string, any>) {
  const explicit = String(source.site_url || "").trim()
  if (explicit) return explicit
  const doi = String(source.doi || "").trim().replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")
  if (doi) return `https://doi.org/${doi}`
  const rawUrl = String(source.source_url || source.url || "").trim()
  if (/arxiv\.org\/pdf\//i.test(rawUrl)) return rawUrl.replace(/\/pdf\//i, "/abs/").replace(/\.pdf$/i, "")
  if (/(?:hal\.science|hal\.archives-ouvertes\.fr)\/.+\/document$/i.test(rawUrl)) return rawUrl.replace(/\/document$/i, "")
  return rawUrl && !isDirectPdfUrl(rawUrl) ? rawUrl : ""
}


function articleConsultUrl(source: Record<string, any>) {
  return publicationSiteUrl(source)
    || String(source.pdf_url || source.source_url || source.url || "").trim()
}


function sourceEvidenceExcerpt(source: Record<string, any>) {
  return String(
    source.evidence_excerpt
    || source.evidence_text
    || source.quote
    || source.abstract
    || source.abstract_or_snippet
    || "",
  ).trim()
}


function sourceIdentity(source: Record<string, any>) {
  return String(
    source.article_id
    || source.candidate_id
    || source.evidence_id
    || source.citation_id
    || source.title
    || "",
  ).trim()
}


function uniqueComparisonSources(rows: Array<Record<string, any>>) {
  const values = new Map<string, Record<string, any>>()
  rows.forEach((source, index) => {
    const key = sourceIdentity(source) || `source-${index}`
    values.set(key, { ...(values.get(key) || {}), ...source })
  })
  return [...values.values()]
}


function progressiveEvidenceSources(version: ImprovementVersion | null) {
  const sections = Array.isArray(version?.evidence?.sections) ? version?.evidence?.sections : []
  return sections.flatMap((section: Record<string, any>) => {
    const research = section?.research || {}
    const finalEvidence = research?.final_evidence || {}
    const selection = research?.auto_selection || {}
    const rows = finalEvidence.auto_accepted?.length
      ? finalEvidence.auto_accepted
      : finalEvidence.advisory_sources?.length
        ? finalEvidence.advisory_sources
        : research.accepted_sources?.length
          ? research.accepted_sources
          : selection.selected || []
    return (rows as Array<Record<string, any>>).map((source) => ({
      ...source,
      section_id: source.section_id || section.section_id,
      section_ref: source.section_ref || section.section_ref,
      section_title: source.section_title || section.section_title,
    }))
  })
}


function sourcesForComparisonChange(
  change: Record<string, any>,
  allSources: Array<Record<string, any>>,
) {
  if (Array.isArray(change.sources) && change.sources.length > 0) {
    return uniqueComparisonSources(change.sources)
  }
  const refs = new Set((change.evidence_refs || []).map((value: unknown) => String(value || "")))
  const matches = allSources.filter((source) => (
    (change.section_id && source.section_id === change.section_id)
    || (change.section_ref && source.section_ref === change.section_ref)
    || refs.has(String(source.evidence_id || ""))
    || refs.has(String(source.citation_id || ""))
  ))
  return uniqueComparisonSources(matches)
}


type TextComparisonChange = {
  originalIndex: number
  id: string
  operation: "Ajout" | "Suppression" | "Modification"
  label: string
  before: string
  after: string
  reason: string
}

function cleanComparisonText(value: unknown) {
  return String(value || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
}

function compactComparisonText(value: unknown, max = 120) {
  const normalized = cleanComparisonText(value).replace(/\s+/g, " ")
  return normalized.length <= max ? normalized : `${normalized.slice(0, max - 1)}…`
}

function comparisonOperationLabel(operation: unknown, before: string, after: string) {
  const normalized = String(operation || "").trim().toLowerCase()
  if (!before && after) return "Ajout" as const
  if (before && !after) return "Suppression" as const
  if (["insert", "add", "added", "addition"].includes(normalized)) return "Ajout" as const
  if (["delete", "remove", "removed", "deletion"].includes(normalized)) return "Suppression" as const
  return "Modification" as const
}

function normalizeTextComparisonChanges(
  changes: Array<Record<string, any>>,
): TextComparisonChange[] {
  return (changes || [])
    .map((change, index) => {
      const before = cleanComparisonText(change?.before)
      const after = cleanComparisonText(change?.after)
      const section = cleanComparisonText(
        [change?.section_ref, change?.section_title]
          .filter(Boolean)
          .join(" · "),
      )
      const operation = comparisonOperationLabel(change?.operation, before, after)
      return {
        originalIndex: index,
        id: String(change?.change_id || `${index}-${operation}`),
        operation,
        label: section || cleanComparisonText(change?.label) || `${operation} ${index + 1}`,
        before,
        after,
        reason: cleanComparisonText(change?.reason),
      }
    })
    .filter((change) => {
      if (!change.before && !change.after) return false
      return change.before !== change.after
    })
}

function textComparisonTone(operation: TextComparisonChange["operation"]) {
  if (operation === "Ajout") return "border-emerald-200 bg-emerald-50 text-emerald-700"
  if (operation === "Suppression") return "border-rose-200 bg-rose-50 text-rose-700"
  return "border-violet-200 bg-violet-50 text-violet-700"
}

function HighlightedComparisonText({
  text,
  highlight,
  accent,
}: {
  text: string
  highlight: string
  accent: "red" | "green" | "neutral"
}) {
  const content = String(text || "")
  const needle = String(highlight || "")
  const position = needle ? content.indexOf(needle) : -1

  if (!content.trim()) {
    return (
      <p className="text-sm italic text-muted-foreground">
        Aucun contenu disponible pour cette version.
      </p>
    )
  }

  if (!needle || position < 0) {
    return <>{content}</>
  }

  return (
    <>
      {content.slice(0, position)}
      <mark
        className={cn(
          "rounded px-0.5 py-0.5 text-inherit",
          accent === "red"
            ? "bg-rose-100 ring-1 ring-rose-200"
            : accent === "green"
              ? "bg-emerald-100 ring-1 ring-emerald-200"
              : "bg-muted",
        )}
      >
        {content.slice(position, position + needle.length)}
      </mark>
      {content.slice(position + needle.length)}
    </>
  )
}

function ImprovementTextComparator({
  originalVersion,
  proposedVersion,
  changes,
  sourceLabel,
}: {
  originalVersion: ImprovementVersion | null
  proposedVersion: ImprovementVersion | null
  changes: Array<Record<string, any>>
  sourceLabel: string
}) {
  const rows = useMemo(() => normalizeTextComparisonChanges(changes), [changes])
  const [selectedPosition, setSelectedPosition] = useState(0)
  const selected = rows[selectedPosition] || rows[0] || null
  const hasPendingProposal = Boolean(proposedVersion)

  useEffect(() => {
    if (selectedPosition >= rows.length) setSelectedPosition(0)
  }, [rows.length, selectedPosition])

  const originalText = String(originalVersion?.content || "")
  const proposedText = String(proposedVersion?.content || "")

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="shrink-0 border-b bg-card px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          {hasPendingProposal ? (
            <>
              <Badge variant="outline" className="border-rose-200 bg-rose-50 text-[10px] text-rose-700">
                Rouge = texte original remplacé ou supprimé
              </Badge>
              <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-[10px] text-emerald-700">
                Vert = texte proposé
              </Badge>
            </>
          ) : (
            <Badge variant="outline" className="border-slate-200 bg-slate-50 text-[10px] text-slate-700">
              Version active conservée · aucune proposition en attente
            </Badge>
          )}
          <span className="ml-auto text-[10px] text-muted-foreground">
            Comparaison texte de la section
          </span>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <aside className="hidden w-[230px] shrink-0 overflow-y-auto border-r bg-muted/15 p-2.5 md:block 2xl:w-[250px]">
          <div className="mb-2 flex items-center justify-between gap-2 px-1">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Modifications
            </p>
            <Badge variant="outline" className="h-5 px-1.5 text-[10px]">
              {rows.length}
            </Badge>
          </div>

          <div className="space-y-1.5 pb-3">
            {!hasPendingProposal ? (
              <div className="rounded-xl border border-dashed bg-background/60 px-3 py-4 text-center">
                <p className="text-[11px] font-semibold text-foreground">
                  Aucune modification en attente
                </p>
                <p className="mt-1 text-[10px] leading-4 text-muted-foreground">
                  La prochaine amélioration apparaîtra ici.
                </p>
              </div>
            ) : rows.length === 0 ? (
              <div className="rounded-xl border border-dashed bg-background/60 px-3 py-4 text-center">
                <p className="text-[11px] font-semibold text-foreground">
                  Comparaison du texte complet
                </p>
                <p className="mt-1 text-[10px] leading-4 text-muted-foreground">
                  La proposition existe, mais aucun changement structuré n&apos;a été fourni.
                </p>
              </div>
            ) : rows.map((change, position) => (
              <button
                key={change.id}
                type="button"
                onClick={() => setSelectedPosition(position)}
                className={cn(
                  "w-full rounded-xl border px-3 py-2.5 text-left transition",
                  position === selectedPosition
                    ? "border-brand/40 bg-brand/5 shadow-sm"
                    : "border-border bg-card hover:bg-muted/40",
                )}
              >
                <div className="flex items-start gap-2">
                  <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-md border bg-background text-[9px] font-semibold">
                    {position + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <Badge
                      variant="outline"
                      className={cn("h-5 px-1.5 text-[9px]", textComparisonTone(change.operation))}
                    >
                      {change.operation}
                    </Badge>
                    <p className="mt-1.5 line-clamp-2 text-[11px] font-semibold leading-4 text-foreground">
                      {change.label}
                    </p>
                    <p className="mt-1 line-clamp-2 text-[10px] leading-4 text-muted-foreground">
                      {compactComparisonText(change.after || change.before, 100)}
                    </p>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </aside>

        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
          {hasPendingProposal && selected && (
            <div className="shrink-0 border-b bg-background px-3 py-2">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <Badge
                  variant="outline"
                  className={cn("shrink-0 text-[10px]", textComparisonTone(selected.operation))}
                >
                  {selected.operation}
                </Badge>
                <p className="min-w-0 flex-1 truncate text-xs font-semibold">
                  {selected.label}
                </p>
                {selected.reason && (
                  <p className="hidden max-w-[45%] truncate text-[10px] text-muted-foreground xl:block" title={selected.reason}>
                    {selected.reason}
                  </p>
                )}
              </div>
            </div>
          )}

          <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-2">
            <section className="flex min-h-0 min-w-0 flex-col overflow-hidden border-b bg-white lg:border-b-0 lg:border-r">
              <div className="flex min-h-12 shrink-0 items-center gap-3 border-b bg-rose-50/70 px-3 py-2">
                <span className="grid size-8 shrink-0 place-items-center rounded-lg border border-rose-200 bg-white text-rose-600">
                  <FileText className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-rose-700">
                    {hasPendingProposal ? "Version précédente" : "Version active"}
                  </p>
                  <p className="truncate text-[11px] text-muted-foreground">
                    {sourceLabel}
                  </p>
                </div>
                {originalVersion && (
                  <Badge variant="outline" className="shrink-0 bg-white text-[10px]">
                    V{originalVersion.version_number}
                  </Badge>
                )}
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto bg-background p-5">
                <div className="whitespace-pre-wrap break-words text-sm leading-7 text-foreground">
                  <HighlightedComparisonText
                    text={originalText}
                    highlight={hasPendingProposal ? selected?.before || "" : ""}
                    accent={hasPendingProposal ? "red" : "neutral"}
                  />
                </div>
              </div>
            </section>

            <section className="flex min-h-0 min-w-0 flex-col overflow-hidden bg-white">
              <div className="flex min-h-12 shrink-0 items-center gap-3 border-b bg-emerald-50/70 px-3 py-2">
                <span className="grid size-8 shrink-0 place-items-center rounded-lg border border-emerald-200 bg-white text-emerald-600">
                  <Sparkles className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-[11px] font-semibold uppercase tracking-wide text-emerald-700">
                    Nouvelle version
                  </p>
                  <p className="truncate text-[11px] text-muted-foreground">
                    {hasPendingProposal ? sourceLabel : "Aucune amélioration en attente"}
                  </p>
                </div>
                {proposedVersion && (
                  <Badge variant="outline" className="shrink-0 bg-white text-[10px]">
                    V{proposedVersion.version_number}
                  </Badge>
                )}
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto bg-background p-5">
                {hasPendingProposal ? (
                  <div className="whitespace-pre-wrap break-words text-sm leading-7 text-foreground">
                    <HighlightedComparisonText
                      text={proposedText}
                      highlight={selected?.after || ""}
                      accent="green"
                    />
                  </div>
                ) : (
                  <div className="grid h-full min-h-[240px] place-items-center p-6 text-center">
                    <div className="max-w-sm">
                      <Sparkles className="mx-auto size-8 text-muted-foreground/35" />
                      <p className="mt-3 text-sm font-semibold text-foreground">
                        En attente d&apos;une nouvelle amélioration
                      </p>
                      <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
                        La version active reste conservée à gauche. La prochaine proposition apparaîtra ici.
                      </p>
                    </div>
                  </div>
                )}
              </div>
            </section>
          </div>
        </main>
      </div>
    </div>
  )
}


type Props = {
  onImmersiveModeChange?: (immersive: boolean) => void
  onCreateProject?: () => void
}

type ImprovementMode = "section" | "full_document"

const quickPrompts = [
  "Améliore la clarté et la fluidité sans changer les faits.",
  "Renforce l'argumentation avec les preuves déjà validées.",
  "Renforce les verrous sans inventer de difficulté.",
  "Enrichis l'état de l'art avec des références traçables.",
  "Applique un style CIR professionnel.",
  "Identifie ce qui reste insuffisamment démontré.",
  "Vérifie la chaîne incertitude–méthode–résultat–connaissance pour le CIR.",
]

const MAX_INTERACTIVE_SELECTION_CHARS = 80_000


function sectionHasChildren(sections: ImprovementSection[], index: number) {
  return Boolean(sections[index + 1] && sections[index + 1].level > sections[index].level)
}


function visibleSectionRows(
  sections: ImprovementSection[],
  expandedIds: Set<string>,
  query: string,
) {
  const wanted = query.trim().toLocaleLowerCase("fr")
  if (wanted) {
    return sections
      .map((section, index) => ({ section, index, hasChildren: sectionHasChildren(sections, index) }))
      .filter(({ section }) => section.title.toLocaleLowerCase("fr").includes(wanted))
  }

  const rows: Array<{ section: ImprovementSection; index: number; hasChildren: boolean }> = []
  const collapsedParentLevels: number[] = []
  sections.forEach((section, index) => {
    while (
      collapsedParentLevels.length > 0
      && section.level <= collapsedParentLevels[collapsedParentLevels.length - 1]
    ) {
      collapsedParentLevels.pop()
    }
    if (collapsedParentLevels.length > 0) return
    const hasChildren = sectionHasChildren(sections, index)
    rows.push({ section, index, hasChildren })
    if (hasChildren && !expandedIds.has(section.section_id)) {
      collapsedParentLevels.push(section.level)
    }
  })
  return rows
}


function expandedPathForSection(sections: ImprovementSection[], sectionId?: string | null) {
  if (!sectionId) return new Set<string>()
  const stack: ImprovementSection[] = []
  for (const section of sections) {
    while (stack.length > 0 && stack[stack.length - 1].level >= section.level) stack.pop()
    if (section.section_id === sectionId) {
      return new Set([...stack.map((row) => row.section_id), section.section_id])
    }
    stack.push(section)
  }
  return new Set<string>()
}


function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Une erreur inattendue est survenue."
}

function isBackgroundJobActive(job?: ImprovementBackgroundJob | null) {
  return ["queued", "running", "retrying"].includes(String(job?.status || "").toLowerCase())
}

function sessionBackgroundJob(session?: ImprovementSession | null) {
  const job = session?.context?.cir_background_job
  return job && typeof job === "object" ? job as ImprovementBackgroundJob : null
}

function backgroundProgressLabel(job?: ImprovementBackgroundJob | null) {
  const status = String(job?.status || "queued").toLowerCase()
  const cursor = Number(job?.progress?.cursor || 0)
  const total = Number(job?.progress?.total || 0)
  const currentSection = job?.progress?.current_section
  const section = typeof currentSection === "string"
    ? currentSection.trim()
    : String(currentSection?.section_title || currentSection?.section_ref || "").trim()
  if (status === "retrying") return "Une étape a échoué — reprise automatique en cours…"
  if (status === "queued") return "Demande mise en file — démarrage du traitement…"
  if (cursor > 0 && total > 0) {
    return `Traitement du CIR — section ${Math.min(cursor + 1, total)}/${total}${section ? ` · ${section}` : ""}`
  }
  return "Demande reçue — analyse du CIR en cours…"
}


function evidenceReferenceLabel(reference: string) {
  const parts = String(reference || "").split(":")
  const detail = parts.slice(2).join(" ").replace(/[_-]+/g, " ").trim()
  if (parts[0] === "D") return detail ? `Preuve projet · ${detail}` : "Preuve projet"
  if (parts[0] === "S") return detail ? `Référence validée · ${detail}` : "Référence validée"
  if (parts[0] === "P") return "Identité officielle du projet"
  return detail || reference
}


function versionLabel(version: ImprovementVersion) {
  if (version.status === "original") return `V${version.version_number} · Original`
  if (version.status === "candidate") return `V${version.version_number} · Proposition`
  if (version.status === "accepted") return `V${version.version_number} · Active`
  if (version.status === "rejected") return `V${version.version_number} · Rejetée`
  return `V${version.version_number} · ${version.status}`
}



function messageResearchSources(message: any) {
  const metadata = message?.metadata && typeof message.metadata === "object"
    ? message.metadata
    : {}

  const candidates = [
    message?.research_sources,
    message?.sources,
    metadata?.research_sources,
    metadata?.sources,
    metadata?.research?.sources,
    metadata?.research?.candidates,
    metadata?.scholar_handoff?.sources,
    metadata?.scholar?.sources,
  ]

  const rows = candidates.find((value) => Array.isArray(value))
  return uniqueComparisonSources(Array.isArray(rows) ? rows : [])
}

function researchRoleLabel(source: Record<string, any>) {
  const raw = String(
    source.relevance_role
    || source.tag_article
    || source.category
    || source.role
    || "",
  ).toLowerCase()

  if (raw.includes("direct")) return "Directe"
  if (raw.includes("connexe") || raw.includes("connected")) return "Connexe"
  if (raw.includes("fondamental") || raw.includes("fundamental")) return "Fondamentale"
  if (raw.includes("hors")) return "Hors sujet"
  return "Source"
}

function ResearchAttachment({
  sources,
  busy,
  onDecision,
}: {
  sources: Array<Record<string, any>>
  busy: boolean
  onDecision: (candidateId: string, decision: "accepted" | "rejected") => void
}) {
  const [expanded, setExpanded] = useState(false)

  if (sources.length === 0) return null

  const direct = sources.filter((source) => researchRoleLabel(source) === "Directe").length
  const connexe = sources.filter((source) => researchRoleLabel(source) === "Connexe").length
  const fondamental = sources.filter((source) => researchRoleLabel(source) === "Fondamentale").length

  return (
    <div className="mt-3 overflow-hidden rounded-2xl border border-brand/15 bg-card shadow-[0_10px_30px_rgba(48,28,83,0.055)]">
      <div className="flex flex-col gap-3 border-b border-brand/10 bg-brand/[0.025] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="grid size-8 shrink-0 place-items-center rounded-xl border border-brand/15 bg-background text-brand">
              <Library className="size-4" />
            </span>
            <div>
              <p className="text-sm font-semibold text-foreground">Recherche scientifique</p>
              <p className="text-[11px] text-muted-foreground">
                {sources.length} source(s) trouvée(s) pour cette demande
              </p>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-1.5 text-[10px]">
          {direct > 0 && <Badge variant="outline" className="border-emerald-200 bg-emerald-50 text-emerald-700">Directes {direct}</Badge>}
          {connexe > 0 && <Badge variant="outline" className="border-violet-200 bg-violet-50 text-violet-700">Connexes {connexe}</Badge>}
          {fondamental > 0 && <Badge variant="outline" className="border-blue-200 bg-blue-50 text-blue-700">Fondamentales {fondamental}</Badge>}
        </div>
      </div>

      {!expanded ? (
        <div className="px-4 py-3">
          <div className="space-y-2">
            {sources.slice(0, 3).map((source, index) => (
              <div key={sourceIdentity(source) || index} className="flex min-w-0 items-start gap-3 rounded-xl bg-muted/25 px-3 py-2.5">
                <span className="grid size-6 shrink-0 place-items-center rounded-lg bg-background text-[10px] font-semibold text-brand ring-1 ring-border">
                  {index + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="line-clamp-1 text-xs font-semibold text-foreground">
                    {source.title || "Source scientifique"}
                  </p>
                  <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                    {[researchRoleLabel(source), source.year, source.provider || source.source].filter(Boolean).join(" · ")}
                  </p>
                </div>
              </div>
            ))}
          </div>
          <button
            type="button"
            className="mt-3 inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2 text-xs font-semibold text-brand hover:bg-brand/5"
            onClick={() => setExpanded(true)}
          >
            Voir les {sources.length} sources
            <ChevronDown className="size-3.5" />
          </button>
        </div>
      ) : (
        <div className="space-y-2.5 p-3 sm:p-4">
          {sources.map((source, index) => {
            const decision = normalizeSourceDecision(source.consultant_decision)
            const candidateId = String(source.candidate_id || "").trim()
            const consultUrl = articleConsultUrl(source)
            const excerpt = sourceEvidenceExcerpt(source)

            return (
              <article key={sourceIdentity(source) || index} className="rounded-xl border border-border bg-background p-3.5">
                <div className="flex items-start gap-3">
                  <span className="grid size-8 shrink-0 place-items-center rounded-xl bg-brand/[0.06] text-xs font-semibold text-brand">
                    {index + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Badge variant="outline" className="text-[10px]">{researchRoleLabel(source)}</Badge>
                      {source.score != null && <Badge variant="secondary" className="text-[10px]">Score {Math.round(Number(source.score) * (Number(source.score) <= 1 ? 100 : 1))}%</Badge>}
                      {decision === "accepted" && <Badge className="bg-emerald-600 text-[10px]">Gardée</Badge>}
                      {decision === "rejected" && <Badge variant="outline" className="border-rose-200 bg-rose-50 text-[10px] text-rose-700">Écartée</Badge>}
                    </div>

                    <p className="mt-2 text-sm font-semibold leading-5 text-foreground">
                      {source.title || "Source scientifique"}
                    </p>
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      {[
                        Array.isArray(source.authors) ? source.authors.slice(0, 3).join(", ") : "",
                        source.year,
                        source.provider || source.source,
                      ].filter(Boolean).join(" · ")}
                    </p>

                    {(source.reason || excerpt) && (
                      <p className="mt-2 line-clamp-3 text-xs leading-5 text-muted-foreground">
                        {source.reason || excerpt}
                      </p>
                    )}

                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      {consultUrl && (
                        <a
                          href={consultUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2.5 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
                        >
                          <ExternalLink className="size-3.5" />
                          Consulter
                        </a>
                      )}
                      {candidateId && decision !== "accepted" && (
                        <Button size="sm" className="min-h-9 rounded-lg" disabled={busy} onClick={() => onDecision(candidateId, "accepted")}>
                          <Check className="size-3.5" /> Garder
                        </Button>
                      )}
                      {candidateId && decision !== "rejected" && (
                        <Button size="sm" variant="outline" className="min-h-9 rounded-lg" disabled={busy} onClick={() => onDecision(candidateId, "rejected")}>
                          <X className="size-3.5" /> Écarter
                        </Button>
                      )}
                    </div>
                  </div>
                </div>
              </article>
            )
          })}

          <button
            type="button"
            className="inline-flex min-h-9 items-center gap-1.5 rounded-lg px-2 text-xs font-semibold text-brand hover:bg-brand/5"
            onClick={() => setExpanded(false)}
          >
            Réduire les sources
            <ChevronDown className="size-3.5 rotate-180" />
          </button>
        </div>
      )}
    </div>
  )
}

export default function EnnoAmeliorationPage({ onImmersiveModeChange, onCreateProject }: Props) {
  const [projectId, setProjectId] = useState<number | null>(null)
  const [projects, setProjects] = useState<ProjectRead[]>([])
  const [projectContext, setProjectContext] = useState<ImprovementProjectContext | null>(null)
  const [projectChooserOpen, setProjectChooserOpen] = useState(false)
  const [projectQuery, setProjectQuery] = useState("")
  const [sessions, setSessions] = useState<ImprovementSession[]>([])
  const [current, setCurrent] = useState<ImprovementSession | null>(null)
  const [documents, setDocuments] = useState<DocumentRead[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [backgroundJob, setBackgroundJob] = useState<ImprovementBackgroundJob | null>(null)
  const [error, setError] = useState("")
  const [draft, setDraft] = useState("")
  const [pendingMessage, setPendingMessage] = useState("")
  const [selectedText, setSelectedText] = useState("")
  const [selectedSectionId, setSelectedSectionId] = useState<string | null>(null)
  const [sectionsPanelOpen, setSectionsPanelOpen] = useState(true)
  const [expandedSectionIds, setExpandedSectionIds] = useState<Set<string>>(() => new Set())
  const [sectionQuery, setSectionQuery] = useState("")
  const [navigatorOpen, setNavigatorOpen] = useState(false)
  const [navigatorView, setNavigatorView] = useState<"conversations" | "sections">("conversations")
  const [leftOpen, setLeftOpen] = useState(false)
  const [rightOpen, setRightOpen] = useState(false)
  const [proposalFullscreen, setProposalFullscreen] = useState(true)
  useEffect(() => {
    if (rightOpen) setProposalFullscreen(true)
  }, [rightOpen])

  const [sourcePreviewUrl, setSourcePreviewUrl] = useState("")
  const [sourcePreviewLoading, setSourcePreviewLoading] = useState(false)
  const [sourcePreviewError, setSourcePreviewError] = useState("")
  const [creating, setCreating] = useState(false)
  const [newTitle, setNewTitle] = useState("")
  const [newText, setNewText] = useState("")
  const [newDocumentId, setNewDocumentId] = useState("")
  const [newInstruction, setNewInstruction] = useState("")
  const [targetMode, setTargetMode] = useState<ImprovementMode>("section")
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const artifactRef = useRef<HTMLTextAreaElement | null>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)

  const versions = current?.versions || []
  const candidate = useMemo(
    () => [...versions].reverse().find((version) => version.status === "candidate") || null,
    [versions],
  )
  const activeVersion = useMemo(
    () => versions.find((version) => version.version_id === current?.active_version_id) || versions[0] || null,
    [versions, current?.active_version_id],
  )
  const comparisonOriginal = useMemo(
    () => (
      candidate
        ? versions.find((version) => version.version_id === candidate.parent_version_id) || activeVersion
        : activeVersion
    ),
    [candidate, versions, activeVersion],
  )
  const structuredResult = (
    candidate?.generation?.structured_result
    || candidate?.generation?.trace
    || null
  ) as Record<string, any> | null
  const comparisonChanges = (
    (structuredResult?.changes || []).length > 0
      ? structuredResult?.changes
      : candidate?.diff?.changes || []
  ) as Array<Record<string, any>>
  const comparisonSources = uniqueComparisonSources([
    ...((structuredResult?.sources_used || []) as Array<Record<string, any>>),
    ...(((candidate?.evidence?.scholar?.evidence || []) as Array<Record<string, any>>)),
    ...progressiveEvidenceSources(candidate),
  ])
  const documentStructure = (current?.context?.document_structure || {}) as Record<string, any>
  const sourceDocument = documents.find((document) => document.id === current?.source_document_id) || null
  const sourceDocumentIsPdf = Boolean(
    sourceDocument
    && (
      String(sourceDocument.content_type || "").toLowerCase().includes("pdf")
      || String(sourceDocument.filename || "").toLowerCase().endsWith(".pdf")
    )
  )
  const activeProject = projectContext?.project || projects.find((project) => project.id === projectId) || null
  const filteredProjects = useMemo(() => {
    const query = projectQuery.trim().toLocaleLowerCase("fr")
    if (!query) return projects
    return projects.filter((project) => (
      `${project.project_name} ${project.organisme} ${project.year} ${project.domain_label || ""}`
        .toLocaleLowerCase("fr")
        .includes(query)
    ))
  }, [projects, projectQuery])
  const sections = current?.context?.sections || []
  const visibleSections = useMemo(
    () => visibleSectionRows(sections, expandedSectionIds, sectionQuery),
    [sections, expandedSectionIds, sectionQuery],
  )
  const researchSources = (
    current?.context?.research_sources
    || current?.context?.scholar_handoff?.sources
    || []
  ) as Array<Record<string, any>>
  const researchSourcesByMessage = useMemo(() => {
    const messages = (current?.messages || []) as Array<any>
    const canonical = new Map(
      researchSources
        .filter((source) => sourceIdentity(source))
        .map((source) => [sourceIdentity(source), source]),
    )
    const result = new Map<string, Array<Record<string, any>>>()

    messages.forEach((message, index) => {
      const rows = messageResearchSources(message).map((source) => ({
        ...source,
        ...(canonical.get(sourceIdentity(source)) || {}),
      }))
      if (rows.length > 0) result.set(String(message.message_id || index), rows)
    })

    if (result.size === 0 && researchSources.length > 0) {
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        if (messages[index]?.role === "assistant") {
          result.set(String(messages[index].message_id || index), researchSources)
          break
        }
      }
    }

    return result
  }, [current?.messages, researchSources])
  const backgroundActive = isBackgroundJobActive(backgroundJob)
  const backgroundStatus = String(backgroundJob?.status || "").toLowerCase()
  const completedCandidateId = String(backgroundJob?.candidate_version_id || "").trim()
  const currentSessionJob = sessionBackgroundJob(current)
  const terminalResultStale = Boolean(
    ["completed", "failed"].includes(backgroundStatus)
    && (
      pendingMessage
      || String(currentSessionJob?.job_id || "") !== String(backgroundJob?.job_id || "")
      || String(currentSessionJob?.status || "").toLowerCase() !== backgroundStatus
      || (
        completedCandidateId
        && !versions.some((version) => version.version_id === completedCandidateId)
      )
    )
  )

  useEffect(() => {
    let cancelled = false
    let objectUrl = ""
    const documentId = Number(current?.source_document_id || 0)
    if (!projectId || !documentId) {
      setSourcePreviewUrl("")
      setSourcePreviewError("")
      setSourcePreviewLoading(false)
      return () => {
        cancelled = true
      }
    }
    setSourcePreviewLoading(true)
    setSourcePreviewUrl("")
    setSourcePreviewError("")
    getImprovementSourceDocument(projectId, documentId)
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setSourcePreviewUrl(objectUrl)
      })
      .catch((previewError) => {
        if (!cancelled) setSourcePreviewError(getErrorMessage(previewError))
      })
      .finally(() => {
        if (!cancelled) setSourcePreviewLoading(false)
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [projectId, current?.session_id, current?.source_document_id])

  const refreshList = async (id: number) => {
    const response = await listImprovementSessions(id)
    setSessions(response.sessions)
  }

  const openSession = async (sessionId: string, id = projectId) => {
    if (!id) return
    setBusy(true)
    setError("")
    try {
      const response = await getImprovementSession(id, sessionId)
      setCurrent(response.session)
      setBackgroundJob(sessionBackgroundJob(response.session))
      setCreating(false)
      const nextSectionId = response.session.target_section_id || null
      setSelectedSectionId(nextSectionId)
      setExpandedSectionIds(expandedPathForSection(response.session.context?.sections || [], nextSectionId))
      setSectionQuery("")
      setSelectedText("")
      const scope = response.session.target_scope
      setTargetMode(scope === "full_document" ? "full_document" : "section")
    } catch (requestError) {
      setError(getErrorMessage(requestError))
    } finally {
      setBusy(false)
    }
  }

  const loadProject = async (id: number) => {
    setLoading(true)
    setError("")
    setProjectId(id)
    setCurrentProjectId(id)
    setCurrent(null)
    setBackgroundJob(null)
    setSessions([])
    setDocuments([])
    setSelectedText("")
    setSelectedSectionId(null)
    setProjectChooserOpen(false)
    try {
      const [sessionResponse, documentResponse, contextResponse] = await Promise.all([
        listImprovementSessions(id),
        getDocuments(id),
        getImprovementProjectContext(id),
      ])
      setSessions(sessionResponse.sessions)
      setDocuments(documentResponse)
      setProjectContext(contextResponse.context)
      if (sessionResponse.sessions[0]) {
        setCreating(false)
        // Le shell, les documents et la liste sont déjà utilisables. Le détail
        // potentiellement volumineux de la dernière conversation arrive ensuite.
        setLoading(false)
        void openSession(sessionResponse.sessions[0].session_id, id)
      } else {
        setCreating(true)
      }
    } catch (requestError) {
      setProjectContext(null)
      setError(getErrorMessage(requestError))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    const sessionId = current?.session_id
    // Réconcilier aussi un job déjà terminé. Le worker local peut finir entre
    // la réponse POST et le premier rendu : dans cette course, l'ancien code
    // voyait ``completed`` (donc non actif) et ne rechargeait jamais la version
    // candidate pourtant bien créée en base.
    if (!projectId || !sessionId || (!backgroundActive && !terminalResultStale)) return

    let cancelled = false
    let polling = false
    const poll = async () => {
      if (polling) return
      polling = true
      try {
        const response = await getImprovementBackgroundJob(projectId, sessionId)
        if (cancelled) return
        const nextJob = response.background_job || null
        setBackgroundJob(nextJob)
        const status = String(nextJob?.status || "").toLowerCase()
        if (status === "completed" || status === "failed") {
          const detail = await getImprovementSession(projectId, sessionId)
          if (cancelled) return
          setCurrent(detail.session)
          setPendingMessage("")
          if (status === "completed") {
            setError("")
          }
          await refreshList(projectId)
          if (status === "failed") {
            setError(nextJob?.error || "Le traitement du CIR a échoué. Vous pouvez relancer la demande.")
          }
        }
      } catch (pollError) {
        if (!cancelled) setError(getErrorMessage(pollError))
      } finally {
        polling = false
      }
    }

    void poll()
    const intervalId = window.setInterval(poll, 2500)
    return () => {
      cancelled = true
      window.clearInterval(intervalId)
    }
  }, [
    projectId,
    current?.session_id,
    backgroundJob?.job_id,
    backgroundActive,
    terminalResultStale,
  ])

  useEffect(() => {
    onImmersiveModeChange?.(true)
    getProjects()
      .then((availableProjects) => {
        setProjects(availableProjects)
        const savedId = getCurrentProjectId()
        const selected = availableProjects.find((project) => project.id === savedId) || availableProjects[0]
        if (selected) return loadProject(selected.id)
        setLoading(false)
        return undefined
      })
      .catch((requestError) => {
        setError(getErrorMessage(requestError))
        setLoading(false)
      })

    return () => onImmersiveModeChange?.(false)
  }, [onImmersiveModeChange])

  useEffect(() => {
    const viewport = messagesEndRef.current?.closest<HTMLElement>("[data-slot='scroll-area-viewport']")
    viewport?.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" })
  }, [current?.messages?.length, pendingMessage, busy])

  useEffect(() => {
    const compactViewport = window.matchMedia("(max-width: 1439px)")
    const adaptPanels = (event: MediaQueryListEvent | MediaQueryList) => {
      if (event.matches) setLeftOpen(false)
    }
    adaptPanels(compactViewport)
    compactViewport.addEventListener("change", adaptPanels)
    return () => compactViewport.removeEventListener("change", adaptPanels)
  }, [])

  const createSession = async () => {
    if (!projectId) return
    const selectedDocumentId = Number(newDocumentId) || undefined
    if (targetMode === "full_document" && !selectedDocumentId) {
      setError("Choisissez un document CIR du projet ou importez-le depuis votre PC.")
      return
    }
    if (!selectedDocumentId && !newText.trim()) {
      setError("Collez une section, choisissez un document ou importez un fichier.")
      return
    }
    if (!newInstruction.trim()) {
      setError("Décrivez l'amélioration attendue avant de lancer l'analyse.")
      return
    }
    const instruction = newInstruction.trim()
    let sessionCreated = false
    let backgroundQueued = false
    setBusy(true)
    setError("")
    try {
      const response = await createImprovementSession(projectId, {
        title: newTitle.trim() || undefined,
        source_text: targetMode === "section" ? newText.trim() || undefined : undefined,
        source_document_id: selectedDocumentId,
        target_scope: targetMode,
      })
      sessionCreated = true
      setCurrent(response.session)
      setCreating(false)
      setPendingMessage(instruction)
      const improved = await sendImprovementMessage(projectId, response.session.session_id, {
        message: instruction,
        target_scope: targetMode,
      })
      setCurrent(improved.session)
      backgroundQueued = Boolean(improved.background && isBackgroundJobActive(improved.background_job))
      setBackgroundJob(improved.background_job || sessionBackgroundJob(improved.session))
      setNewTitle("")
      setNewText("")
      setNewDocumentId("")
      setNewInstruction("")
      setSelectedSectionId(null)
      setExpandedSectionIds(new Set())
      setSectionQuery("")
      const contextResponse = await getImprovementProjectContext(projectId)
      setProjectContext(contextResponse.context)
      await refreshList(projectId)
    } catch (requestError) {
      setError(getErrorMessage(requestError))
      if (sessionCreated) setDraft(instruction)
    } finally {
      if (!backgroundQueued) setPendingMessage("")
      setBusy(false)
    }
  }

  const handleLocalFile = async (file?: File) => {
    if (!projectId || !file) return
    setBusy(true)
    setError("")
    try {
      const document = await uploadDocument(
        projectId,
        file,
        "Texte à améliorer",
        "improvement",
      )
      setDocuments((rows) => [document, ...rows.filter((row) => row.id !== document.id)])
      setNewDocumentId(String(document.id))
      setNewTitle((title) => title.trim() || file.name)
      setNewText("")
    } catch (requestError) {
      setError(getErrorMessage(requestError))
    } finally {
      setBusy(false)
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }

  const sendMessage = async () => {
    if (!projectId || !current || !draft.trim() || busy || backgroundActive) return
    if (selectedText.length > MAX_INTERACTIVE_SELECTION_CHARS) {
      setError(
        `Le passage sélectionné contient ${selectedText.length.toLocaleString("fr-FR")} caractères. `
        + "Choisissez la section par son titre dans l'arborescence afin de cibler aussi ses sous-sections.",
      )
      return
    }
    const outgoingMessage = draft.trim()
    let backgroundQueued = false
    setBusy(true)
    setError("")
    setPendingMessage(outgoingMessage)
    setDraft("")
    try {
      const section = sections.find((row) => row.section_id === selectedSectionId)
      const response = await sendImprovementMessage(projectId, current.session_id, {
        message: outgoingMessage,
        selected_text: selectedText || undefined,
        target_scope: selectedText ? "selection" : selectedSectionId ? "section" : targetMode,
        target_section_id: selectedText ? undefined : selectedSectionId || undefined,
        target_section_title: selectedText ? undefined : section?.title,
      })
      setCurrent(response.session)
      backgroundQueued = Boolean(response.background && isBackgroundJobActive(response.background_job))
      setBackgroundJob(response.background_job || sessionBackgroundJob(response.session))
      setSelectedText("")
      await refreshList(projectId)
    } catch (requestError) {
      setError(getErrorMessage(requestError))
      setDraft((currentDraft) => currentDraft || outgoingMessage)
    } finally {
      if (!backgroundQueued) setPendingMessage("")
      setBusy(false)
    }
  }

  const decide = async (decision: "accepted" | "rejected") => {
    if (!projectId || !current || !candidate || busy || backgroundActive) return
    setBusy(true)
    setError("")
    try {
      const response = await decideImprovementVersion(
        projectId,
        current.session_id,
        candidate.version_id,
        decision,
      )
      setCurrent(response.session)
      await refreshList(projectId)
    } catch (requestError) {
      setError(getErrorMessage(requestError))
    } finally {
      setBusy(false)
    }
  }

  const restore = async (version: ImprovementVersion) => {
    if (!projectId || !current || busy || backgroundActive) return
    setBusy(true)
    setError("")
    try {
      const response = await restoreImprovementVersion(
        projectId,
        current.session_id,
        version.version_id,
      )
      setCurrent(response.session)
      await refreshList(projectId)
    } catch (requestError) {
      setError(getErrorMessage(requestError))
    } finally {
      setBusy(false)
    }
  }

  const decideSource = async (candidateId: string, decision: "accepted" | "rejected") => {
    if (!projectId || !current || busy || backgroundActive) return
    setBusy(true)
    setError("")
    try {
      const response = await decideImprovementSources(
        projectId,
        current.session_id,
        [candidateId],
        decision,
      )
      setCurrent(response.session)
      await refreshList(projectId)
    } catch (requestError) {
      setError(getErrorMessage(requestError))
    } finally {
      setBusy(false)
    }
  }

  const removeSession = async (session: ImprovementSession) => {
    if (!projectId || !window.confirm(`Supprimer la conversation « ${session.title} » ?`)) return
    setBusy(true)
    try {
      await deleteImprovementSession(projectId, session.session_id)
      const remaining = sessions.filter((row) => row.session_id !== session.session_id)
      setSessions(remaining)
      if (current?.session_id === session.session_id) {
        setCurrent(null)
        if (remaining[0]) await openSession(remaining[0].session_id, projectId)
        else setCreating(true)
      }
    } catch (requestError) {
      setError(getErrorMessage(requestError))
    } finally {
      setBusy(false)
    }
  }

  const captureSelection = () => {
    const element = artifactRef.current
    if (!element) return
    const selected = element.value.slice(element.selectionStart, element.selectionEnd).trim()
    if (selected.length > MAX_INTERACTIVE_SELECTION_CHARS) {
      setSelectedText("")
      setError(
        `Cette sélection contient ${selected.length.toLocaleString("fr-FR")} caractères. `
        + "Utilisez l'arborescence des sections pour éviter d'envoyer accidentellement le CIR complet.",
      )
      return
    }
    setError("")
    setSelectedText(selected)
    if (selected) setTargetMode("section")
  }

  const toggleSection = (sectionId: string) => {
    setExpandedSectionIds((currentIds) => {
      const nextIds = new Set(currentIds)
      if (nextIds.has(sectionId)) nextIds.delete(sectionId)
      else nextIds.add(sectionId)
      return nextIds
    })
  }

  const selectSection = (section: ImprovementSection) => {
    setSelectedSectionId(section.section_id)
    setExpandedSectionIds((currentIds) => new Set([
      ...currentIds,
      ...expandedPathForSection(sections, section.section_id),
    ]))
    setSelectedText("")
    setTargetMode("section")
    setError("")
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const element = artifactRef.current
        if (!element) return
        const position = Math.min(section.start, element.value.length)
        element.focus({ preventScroll: true })
        element.setSelectionRange(position, position)
      })
    })
  }

  if (loading) {
    return <LoadingState label="Chargement de l'espace d'amélioration…" />
  }

  if (!projectId) {
    return (
      <div className="grid h-full place-items-center p-8">
        <div className="max-w-md rounded-2xl border bg-card p-8 text-center shadow-sm">
          <Sparkles className="mx-auto size-9 text-primary" />
          <h1 className="mt-4 text-xl font-semibold">Sélectionnez d'abord un projet</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            EnnoAmelioration conserve ses conversations et ses versions séparément pour chaque projet.
          </p>
          <Button className="mt-5 gap-2" onClick={onCreateProject}>
            <Plus className="size-4" /> Nouveau projet
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="improvement-workspace module-improvement relative flex h-full max-h-full min-h-0 overflow-hidden bg-background">
      <input
        ref={fileInputRef}
        type="file"
        className="hidden"
        accept=".pdf,.doc,.docx,.txt,.md"
        onChange={(event) => handleLocalFile(event.target.files?.[0])}
      />

      <section className="flex h-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <header className="shrink-0 border-b border-border bg-card/95 backdrop-blur-xl">
          <div className="flex min-h-14 items-center gap-2 px-3 sm:px-4">
            <Button
              variant="outline"
              size="sm"
              className="min-h-9 rounded-xl"
              onClick={() => {
                setNavigatorView("conversations")
                setNavigatorOpen(true)
              }}
            >
              <MessageSquareText className="size-4" />
              <span className="hidden sm:inline">Conversations</span>
              <Badge variant="outline" className="ml-1 h-5 px-1.5 text-[10px]">{sessions.length}</Badge>
            </Button>

            {current && sections.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                className="min-h-9 rounded-xl text-muted-foreground"
                onClick={() => {
                  setNavigatorView("sections")
                  setNavigatorOpen(true)
                }}
              >
                <FileText className="size-4" />
                <span className="hidden md:inline">Sections</span>
                <Badge variant="outline" className="ml-1 h-5 px-1.5 text-[10px]">{sections.length}</Badge>
              </Button>
            )}

            <div className="mx-1 h-6 w-px bg-border" aria-hidden="true" />

            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-foreground">
                {creating ? "Nouvelle amélioration" : current?.title || "EnnoAmelioration"}
              </p>
              <p className="truncate text-[11px] text-muted-foreground">
                {activeProject
                  ? `${activeProject.project_name} · ${activeProject.organisme} · ${activeProject.year}`
                  : "Révision CIR contrôlée"}
                {current && (
                  <>
                    <span className="mx-1.5">·</span>
                    {selectedText
                      ? `${selectedText.length} caractères sélectionnés`
                      : sections.find((row) => row.section_id === selectedSectionId)?.title
                        || (targetMode === "full_document" ? "CIR complet" : "Section")}
                  </>
                )}
              </p>
            </div>

            {projects.length > 1 && (
              <select
                value={projectId || ""}
                onChange={(event) => void loadProject(Number(event.target.value))}
                className="hidden h-9 max-w-56 rounded-xl border border-border bg-background px-3 text-xs text-foreground xl:block"
                aria-label="Changer de projet"
              >
                {projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.organisme} — {project.project_name} — {project.year}
                  </option>
                ))}
              </select>
            )}

            {current && (
              <div className="hidden items-center gap-1 rounded-xl border bg-muted/30 p-1 md:flex">
                {([[
                  "section",
                  "Section",
                ], [
                  "full_document",
                  "CIR complet",
                ]] as Array<[ImprovementMode, string]>).map(([mode, label]) => (
                  <button
                    key={mode}
                    type="button"
                    className={cn(
                      "rounded-lg px-2.5 py-1 text-[11px] font-medium transition",
                      targetMode === mode
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                    onClick={() => {
                      setTargetMode(mode)
                      if (mode === "full_document") {
                        setSelectedText("")
                        setSelectedSectionId(null)
                      } else {
                        setSelectedText("")
                      }
                    }}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}

            {current && (
              <Button
                variant={candidate ? "default" : "outline"}
                size="sm"
                className="min-h-9 rounded-xl"
                onClick={() => setRightOpen((value) => !value)}
              >
                {rightOpen ? <PanelRightClose className="size-4" /> : <PanelRightOpen className="size-4" />}
                <span className="hidden lg:inline">Proposition</span>
                {candidate && <Badge variant="secondary" className="ml-1 h-5 px-1.5 text-[10px]">V{candidate.version_number}</Badge>}
              </Button>
            )}
          </div>

          {current && (
            <div className="flex min-h-9 items-center gap-2 overflow-x-auto border-t border-border/70 px-3 py-1.5 sm:px-4" aria-label="Étapes de l'amélioration">
              {[
                { label: "Source", state: "done" },
                { label: "Demande", state: busy || backgroundActive || candidate || activeVersion ? "done" : "current" },
                { label: "Proposition", state: candidate ? "current" : activeVersion ? "done" : "upcoming" },
                { label: "Validation", state: candidate ? "attention" : activeVersion ? "done" : "upcoming" },
              ].map((step, index, values) => (
                <div key={step.label} className="flex shrink-0 items-center gap-2">
                  <span className={cn(
                    "grid size-5 place-items-center rounded-full border text-[10px] font-semibold",
                    step.state === "done" && "border-emerald-600 bg-emerald-600 text-white",
                    step.state === "current" && "border-brand bg-brand/10 text-brand",
                    step.state === "attention" && "border-amber-400 bg-amber-50 text-amber-700",
                    step.state === "upcoming" && "border-border bg-background text-muted-foreground",
                  )}>
                    {step.state === "done" ? <Check className="size-3" /> : index + 1}
                  </span>
                  <span className={cn(
                    "text-[11px] font-medium",
                    step.state === "upcoming" ? "text-muted-foreground" : "text-foreground",
                  )}>{step.label}</span>
                  {index < values.length - 1 && <span className="mx-1 h-px w-5 bg-border sm:w-8" />}
                </div>
              ))}
            </div>
          )}
        </header>

        {error && (
          <div className="mx-4 mt-3 flex items-start gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            <X className="mt-0.5 size-4 shrink-0" />
            <span className="flex-1">{error}</span>
            <button onClick={() => setError("")} aria-label="Fermer l'erreur"><X className="size-3.5" /></button>
          </div>
        )}

        {creating ? (
          <div className="improvement-create-scroll min-h-0 flex-1 overflow-y-scroll overscroll-contain">
            <div className="relative min-h-full overflow-hidden bg-background">
              {/* Décor très léger de la maquette 3 */}
              <div
                className="pointer-events-none absolute inset-0"
                aria-hidden="true"
              >
                <div className="absolute -bottom-28 -left-24 size-[360px] rounded-full bg-primary/[0.035] blur-3xl" />
                <div className="absolute -bottom-32 -right-24 size-[420px] rounded-full bg-violet-500/[0.025] blur-3xl" />
                <div className="absolute left-[12%] top-[31%] size-1 rounded-full bg-primary/10" />
                <div className="absolute left-[15%] top-[35%] size-1 rounded-full bg-primary/10" />
                <div className="absolute left-[18%] top-[28%] size-1 rounded-full bg-primary/10" />
              </div>

              <div className="relative mx-auto flex min-h-full w-full max-w-[1260px] flex-col px-4 py-4 sm:px-5 lg:px-6 xl:px-8 xl:py-5 2xl:px-10">
                {/* Titre central */}
                <div className="mx-auto max-w-xl text-center">
                  <div className="mx-auto grid size-9 place-items-center rounded-xl border border-primary/15 bg-primary/[0.065] text-primary shadow-sm">
                    <Sparkles className="size-[18px]" />
                  </div>

                  <h1 className="mt-2.5 text-[20px] font-semibold tracking-[-0.03em] text-foreground 2xl:text-[22px]">
                    Nouvelle amélioration
                  </h1>

                  <p className="mx-auto mt-1.5 max-w-md text-[12px] leading-5 text-muted-foreground">
                    Importez un CIR complet ou collez une section, puis décrivez l&apos;amélioration attendue.
                    <br className="hidden sm:block" />
                    L&apos;original reste conservé.
                  </p>
                </div>

                {/* Composition de la maquette 3 :
                    aide visuelle à gauche + formulaire central + espace respirant à droite */}
                <div className="mt-3 grid flex-1 items-start justify-center gap-5 xl:grid-cols-[180px_minmax(0,760px)_30px] xl:gap-5 2xl:grid-cols-[190px_minmax(0,790px)_35px] 2xl:gap-5">
                  {/* Colonne d'accompagnement gauche */}
                  <aside className="hidden pt-3 xl:block">
                    <style>{`
                      @keyframes ennoFloatDocument {
                        0%, 100% { transform: translate3d(0, 0, 0) rotate(-4deg); }
                        50% { transform: translate3d(0, -10px, 0) rotate(-2.5deg); }
                      }
                      @keyframes ennoFloatSearch {
                        0%, 100% { transform: translate3d(0, 0, 0) scale(1); }
                        50% { transform: translate3d(7px, -8px, 0) scale(1.035); }
                      }
                      @keyframes ennoPulseCheck {
                        0%, 100% { transform: scale(1); box-shadow: 0 8px 22px rgba(91, 33, 182, .18); }
                        50% { transform: scale(1.08); box-shadow: 0 12px 30px rgba(91, 33, 182, .28); }
                      }
                      @keyframes ennoOrbitSpark {
                        0%, 100% { transform: translate3d(0, 0, 0) rotate(0deg); }
                        33% { transform: translate3d(8px, -6px, 0) rotate(8deg); }
                        66% { transform: translate3d(-4px, -10px, 0) rotate(-7deg); }
                      }
                      @keyframes ennoAura {
                        0%, 100% { opacity: .35; transform: scale(.96); }
                        50% { opacity: .75; transform: scale(1.05); }
                      }
                      .enno-float-document { animation: ennoFloatDocument 6.5s ease-in-out infinite; }
                      .enno-float-search { animation: ennoFloatSearch 5.2s ease-in-out infinite; }
                      .enno-pulse-check { animation: ennoPulseCheck 3.8s ease-in-out infinite; }
                      .enno-orbit-spark { animation: ennoOrbitSpark 4.8s ease-in-out infinite; }
                      .enno-aura { animation: ennoAura 5.5s ease-in-out infinite; }

                      @media (prefers-reduced-motion: reduce) {
                        .enno-float-document,
                        .enno-float-search,
                        .enno-pulse-check,
                        .enno-orbit-spark,
                        .enno-aura {
                          animation: none !important;
                        }
                      }

                      @media (min-width: 1280px) and (max-height: 820px) {
                        .enno-create-visual {
                          transform: scale(.82);
                          transform-origin: top center;
                        }
                      }

                      @media (min-width: 1280px) and (max-height: 720px) {
                        .enno-create-visual {
                          transform: scale(.72);
                        }
                      }
                    `}</style>

                    <div className="enno-create-visual relative mx-auto h-[180px] w-[180px]" aria-hidden="true">
                      <div className="enno-aura absolute left-1/2 top-1/2 size-[145px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary/[0.05] blur-2xl" />
                      {/* Document */}
                      <div className="enno-float-document absolute left-5 top-4 h-[118px] w-[126px] rotate-[-4deg] rounded-[18px] border border-primary/15 bg-card shadow-[0_18px_45px_rgba(61,35,96,0.08)]">
                        <div className="flex h-8 items-center gap-1.5 border-b border-primary/10 px-3">
                          <span className="size-2 rounded-full bg-primary/15" />
                          <span className="size-2 rounded-full bg-primary/10" />
                          <span className="size-2 rounded-full bg-primary/10" />
                        </div>

                        <div className="space-y-2.5 p-3.5">
                          <div className="h-2 w-20 rounded-full bg-primary/12" />
                          <div className="h-2 w-28 rounded-full bg-muted" />
                          <div className="h-2 w-24 rounded-full bg-muted" />

                          <div className="mt-4 flex items-end gap-2">
                            <span className="h-6 w-3 rounded-t bg-primary/10" />
                            <span className="h-10 w-3 rounded-t bg-primary/20" />
                            <span className="h-8 w-3 rounded-t bg-primary/14" />
                          </div>
                        </div>
                      </div>

                      {/* Loupe */}
                      <div className="enno-float-search absolute bottom-4 right-0 grid size-[56px] place-items-center rounded-full border border-primary/15 bg-card shadow-[0_15px_36px_rgba(61,35,96,0.10)]">
                        <Search className="size-6 text-primary" />
                      </div>

                      {/* Badge validation */}
                      <div className="enno-pulse-check absolute right-5 top-10 grid size-7 place-items-center rounded-xl bg-primary text-primary-foreground shadow-md">
                        <Check className="size-4" />
                      </div>

                      {/* Petite étincelle */}
                      <div className="enno-orbit-spark absolute bottom-6 left-[78px] grid size-7 place-items-center rounded-full border border-primary/10 bg-background text-primary shadow-sm">
                        <Sparkles className="size-4" />
                      </div>
                    </div>

                    <div className="mx-auto mt-1 max-w-[180px]">
                      <p className="text-[12px] font-semibold text-foreground">
                        IA au service de vos CIR
                      </p>

                      <div className="mt-2.5 space-y-2.5">
                        <div className="flex items-start gap-3">
                          <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-lg bg-primary/[0.07] text-primary">
                            <Search className="size-3.5" />
                          </span>
                          <p className="text-[11px] leading-4.5 text-muted-foreground">
                            Analyse basée uniquement sur vos preuves.
                          </p>
                        </div>

                        <div className="flex items-start gap-3">
                          <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-lg bg-primary/[0.07] text-primary">
                            <ShieldCheck className="size-3.5" />
                          </span>
                          <p className="text-[11px] leading-4.5 text-muted-foreground">
                            Améliorations claires, traçables et justifiées.
                          </p>
                        </div>

                        <div className="flex items-start gap-3">
                          <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-lg bg-primary/[0.07] text-primary">
                            <History className="size-3.5" />
                          </span>
                          <p className="text-[11px] leading-4.5 text-muted-foreground">
                            L&apos;original reste toujours conservé.
                          </p>
                        </div>
                      </div>
                    </div>
                  </aside>

                  {/* Carte centrale */}
                  <main className="w-full min-w-0 xl:col-start-2">
                    <div className="mx-auto w-full max-w-[790px] overflow-hidden rounded-[18px] border border-border/80 bg-card shadow-[0_18px_48px_rgba(37,20,70,0.07)]">
                      {/* Mini parcours Source / Instruction / Analyse */}
                      <div className="border-b border-border/70 bg-muted/[0.10] px-4 py-2 sm:px-5 lg:px-5 lg:py-2.5">
                        <div className="grid gap-3 sm:grid-cols-3">
                          {[
                            {
                              number: "1",
                              title: "Source",
                              detail: "D’où vient le contenu ?",
                              active: true,
                            },
                            {
                              number: "2",
                              title: "Instruction",
                              detail: "Que souhaitez-vous améliorer ?",
                              active: false,
                            },
                            {
                              number: "3",
                              title: "Analyse",
                              detail: "L’IA analyse et propose",
                              active: false,
                            },
                          ].map((step, index, steps) => (
                            <div key={step.number} className="relative flex min-w-0 items-start gap-3">
                              <span
                                className={cn(
                                  "grid size-6 shrink-0 place-items-center rounded-full border text-[10px] font-semibold transition",
                                  step.active
                                    ? "border-primary bg-primary text-primary-foreground shadow-sm"
                                    : "border-border bg-background text-muted-foreground",
                                )}
                              >
                                {step.number}
                              </span>

                              <div className="min-w-0">
                                <p
                                  className={cn(
                                    "truncate text-[11px] font-semibold",
                                    step.active ? "text-foreground" : "text-muted-foreground",
                                  )}
                                >
                                  {step.title}
                                </p>
                                <p className="mt-0.5 line-clamp-1 text-[9px] leading-4 text-muted-foreground">
                                  {step.detail}
                                </p>
                              </div>

                              {index < steps.length - 1 && (
                                <span className="absolute -right-2 top-3 hidden h-px w-4 bg-border lg:block" />
                              )}
                            </div>
                          ))}
                        </div>
                      </div>

                      <div className="space-y-3 p-4 sm:p-4 lg:p-4 2xl:p-4">
                        {/* Portée */}
                        <section>
                          <p className="mb-2 text-xs font-semibold text-foreground">
                            Portée du texte
                          </p>

                          <div className="grid grid-cols-2 gap-1.5 rounded-xl bg-muted/[0.28] p-1">
                            {([
                              ["section", "Section", FileText],
                              ["full_document", "CIR complet", FileUp],
                            ] as Array<[ImprovementMode, string, typeof FileText]>).map(
                              ([mode, label, Icon]) => (
                                <button
                                  key={mode}
                                  type="button"
                                  className={cn(
                                    "flex min-h-8 cursor-pointer items-center justify-center gap-2 rounded-lg border px-3 text-[13px] font-medium transition-all",
                                    targetMode === mode
                                      ? "border-primary bg-primary text-primary-foreground shadow-sm"
                                      : "border-transparent bg-transparent text-muted-foreground hover:bg-background hover:text-foreground",
                                  )}
                                  onClick={() => {
                                    setTargetMode(mode)
                                    if (mode === "full_document") setNewText("")
                                  }}
                                >
                                  <Icon className="size-4" />
                                  {label}
                                </button>
                              ),
                            )}
                          </div>
                        </section>

                        {/* Source texte ou document */}
                        {targetMode === "section" ? (
                          <section>
                            <label
                              className="mb-2 block text-xs font-semibold text-foreground"
                              htmlFor="improvement-source-text"
                            >
                              Texte de la section
                            </label>

                            <Textarea
                              id="improvement-source-text"
                              className="improvement-text-scroll field-sizing-fixed h-[clamp(118px,17dvh,155px)] min-h-[112px] max-h-[22dvh] resize-y overflow-y-auto rounded-xl border-border/80 bg-background px-4 py-3 text-[13px] leading-5.5 shadow-none focus-visible:ring-2 focus-visible:ring-primary/15 2xl:text-sm"
                              placeholder="Collez ici la section à améliorer…"
                              wrap="soft"
                              value={newText}
                              onChange={(event) => {
                                setNewText(event.target.value)
                                if (event.target.value.trim()) setNewDocumentId("")
                              }}
                            />

                            <div className="my-2.5 flex items-center gap-3">
                              <div className="h-px flex-1 bg-border" />
                              <span className="shrink-0 text-[11px] text-muted-foreground">
                                ou partir d&apos;un document
                              </span>
                              <div className="h-px flex-1 bg-border" />
                            </div>
                          </section>
                        ) : (
                          <section className="rounded-xl border border-primary/10 bg-primary/[0.035] px-4 py-3">
                            <div className="flex items-start gap-3">
                              <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-background text-primary shadow-sm ring-1 ring-primary/10">
                                <FileText className="size-4" />
                              </span>
                              <div>
                                <p className="text-xs font-semibold text-foreground">
                                  CIR complet
                                </p>
                                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                                  Le document est chargé avec sa structure afin de préserver les sections,
                                  sous-sections et éléments du fichier original.
                                </p>
                              </div>
                            </div>
                          </section>
                        )}

                        {/* Choix document */}
                        <section className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
                          <select
                            className="h-10 min-w-0 rounded-xl border border-border/80 bg-background px-3 text-sm text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/10"
                            value={newDocumentId}
                            onChange={(event) => {
                              setNewDocumentId(event.target.value)
                              if (event.target.value) setNewText("")
                            }}
                          >
                            <option value="">
                              {targetMode === "full_document"
                                ? "Choisir le document CIR"
                                : "Choisir un document du projet"}
                            </option>
                            {documents.map((document) => (
                              <option key={document.id} value={document.id}>
                                {document.filename}
                              </option>
                            ))}
                          </select>

                          <Button
                            type="button"
                            variant="outline"
                            className="h-10 cursor-pointer gap-2 rounded-xl px-4"
                            onClick={() => fileInputRef.current?.click()}
                          >
                            <FileUp className="size-4" />
                            Importer depuis le PC
                          </Button>
                        </section>

                        {/* Instruction */}
                        <section>
                          <label
                            className="mb-2 block text-xs font-semibold text-foreground"
                            htmlFor="improvement-instruction"
                          >
                            Que souhaitez-vous améliorer ?
                          </label>

                          <Textarea
                            id="improvement-instruction"
                            className="improvement-text-scroll field-sizing-fixed h-[82px] min-h-[78px] max-h-[14dvh] resize-y overflow-y-auto rounded-xl border-border/80 bg-background px-4 py-3 text-[13px] leading-5.5 shadow-none focus-visible:ring-2 focus-visible:ring-primary/15 2xl:text-sm"
                            placeholder="Ex. : renforce l'argumentation R&D/CIR uniquement à partir des preuves disponibles dans le projet, sans inventer de faits."
                            value={newInstruction}
                            onChange={(event) => setNewInstruction(event.target.value)}
                          />
                        </section>

                        {/* Suggestions */}
                        <section>
                          <div className="mb-1 flex items-center gap-2">
                            <Sparkles className="size-3.5 text-primary" />
                            <p className="text-xs font-semibold text-primary">
                              Suggestions
                            </p>
                          </div>

                          <div className="flex gap-2 overflow-x-auto pb-1">
                            {quickPrompts.slice(0, 3).map((prompt) => (
                              <button
                                key={prompt}
                                type="button"
                                className="shrink-0 cursor-pointer rounded-full border border-primary/10 bg-primary/[0.025] px-3 py-1.5 text-[11px] text-muted-foreground transition hover:border-primary/25 hover:bg-primary/[0.055] hover:text-foreground"
                                onClick={() => setNewInstruction(prompt)}
                              >
                                {prompt}
                              </button>
                            ))}
                          </div>
                        </section>

                        {/* CTA */}
                        <Button
                          className="h-10 w-full cursor-pointer gap-2 rounded-xl bg-primary text-primary-foreground shadow-[0_10px_26px_rgba(91,33,182,0.18)] transition-all hover:-translate-y-0.5 hover:bg-primary/90 hover:shadow-[0_14px_30px_rgba(91,33,182,0.22)] disabled:cursor-not-allowed"
                          disabled={
                            busy
                            || (!newText.trim() && !newDocumentId)
                            || !newInstruction.trim()
                          }
                          onClick={() => createSession()}
                        >
                          {busy ? (
                            <Loader2 className="size-4 animate-spin" />
                          ) : (
                            <Sparkles className="size-4" />
                          )}
                          Analyser et améliorer
                        </Button>
                      </div>
                    </div>
                  </main>

                  {/* Colonne volontairement vide pour conserver l'équilibre visuel */}
                  <div className="hidden xl:block" aria-hidden="true" />
                </div>
              </div>
            </div>
          </div>
        ) : current ? (
          <>
            <ScrollArea className="min-h-0 flex-1">
              <div className="mx-auto max-w-4xl space-y-7 px-4 py-7 sm:px-6 lg:py-9">
                {(current.messages || []).map((message: any, index: number) => {
                  const consultant = message.role === "consultant"
                  const messageKey = String(message.message_id || index)
                  const attachedSources = researchSourcesByMessage.get(messageKey) || []

                  return (
                    <div key={messageKey} className={cn("flex gap-3", consultant ? "justify-end" : "justify-start")}>
                      {!consultant && (
                        <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-full bg-brand/10 text-brand">
                          <Bot className="size-4" />
                        </span>
                      )}

                      <div className={cn("min-w-0", consultant ? "max-w-[82%]" : "w-full max-w-3xl")}>
                        <div className={cn(
                          "text-sm leading-7",
                          consultant
                            ? "rounded-2xl rounded-br-md bg-brand px-4 py-2.5 text-white shadow-sm"
                            : "py-1 text-foreground",
                        )}>
                          <p className="whitespace-pre-wrap">{message.content}</p>
                        </div>

                        {!consultant && attachedSources.length > 0 && (
                          <ResearchAttachment
                            sources={attachedSources}
                            busy={busy || backgroundActive}
                            onDecision={(candidateId, decision) => void decideSource(candidateId, decision)}
                          />
                        )}
                      </div>
                    </div>
                  )
                })}

                {pendingMessage && (
                  <div className="flex justify-end">
                    <div className="max-w-[82%] rounded-2xl rounded-br-md bg-brand px-4 py-2.5 text-sm leading-7 text-white shadow-sm">
                      <p className="whitespace-pre-wrap">{pendingMessage}</p>
                      <p className="mt-1 text-[10px] text-white/65">Message envoyé</p>
                    </div>
                  </div>
                )}

                {(busy || backgroundActive) && (
                  <div className="flex items-start gap-3">
                    <span className="grid size-8 shrink-0 place-items-center rounded-full bg-brand/10 text-brand">
                      <Bot className="size-4" />
                    </span>
                    <div className="flex items-center gap-2 py-1 text-sm text-muted-foreground">
                      <Loader2 className="size-4 animate-spin" />
                      {backgroundActive ? backgroundProgressLabel(backgroundJob) : "Demande reçue — analyse en cours…"}
                    </div>
                  </div>
                )}

                {!busy && !backgroundActive && backgroundStatus === "completed" && !terminalResultStale && candidate && (
                  <div className="flex items-start gap-3">
                    <span className="grid size-8 shrink-0 place-items-center rounded-full bg-brand/10 text-brand">
                      <Sparkles className="size-4" />
                    </span>
                    <div className="w-full max-w-3xl rounded-2xl border border-brand/15 bg-brand/[0.025] p-4">
                      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                        <div>
                          <p className="text-sm font-semibold text-foreground">Proposition V{candidate.version_number} prête</p>
                          <p className="mt-1 text-xs leading-5 text-muted-foreground">
                            La proposition est disponible pour comparaison et validation. L'original reste conservé.
                          </p>
                        </div>
                        <Button size="sm" className="shrink-0 rounded-xl" onClick={() => setRightOpen(true)}>
                          Voir la proposition
                          <PanelRightOpen className="size-4" />
                        </Button>
                      </div>
                    </div>
                  </div>
                )}

                {!busy && !backgroundActive && backgroundStatus === "completed" && !terminalResultStale && !candidate && (
                  <div className="flex items-start gap-3">
                    <span className="grid size-8 shrink-0 place-items-center rounded-full bg-emerald-50 text-emerald-700">
                      <Check className="size-4" />
                    </span>
                    <div className="py-1 text-sm leading-6 text-emerald-700">
                      Traitement terminé — aucune modification sûre n'a été produite ; le compte rendu reste disponible dans la conversation.
                    </div>
                  </div>
                )}

                <div ref={messagesEndRef} />
              </div>
            </ScrollArea>

            <div className="shrink-0 border-t border-border bg-background/95 px-3 pb-3 pt-2 backdrop-blur-xl sm:px-5 sm:pb-4">
              <div className="mx-auto max-w-4xl">
                {selectedText && (
                  <div className="mb-2 flex items-center gap-2 rounded-xl border border-brand/15 bg-brand/5 px-3 py-2 text-xs text-brand">
                    <FileText className="size-3.5" /> Passage sélectionné ({selectedText.length} caractères)
                    <button className="ml-auto" onClick={() => setSelectedText("")} aria-label="Annuler la sélection"><X className="size-3.5" /></button>
                  </div>
                )}

                <div className="mb-2 flex gap-2 overflow-x-auto pb-1">
                  {quickPrompts.slice(0, 3).map((prompt) => (
                    <button
                      key={prompt}
                      className="shrink-0 rounded-full border border-border bg-card px-3 py-1.5 text-[11px] text-muted-foreground transition hover:border-brand/20 hover:bg-brand/5 hover:text-foreground"
                      onClick={() => setDraft(prompt)}
                    >
                      {prompt}
                    </button>
                  ))}
                </div>

                <div className="rounded-2xl border border-input bg-card p-2 shadow-sm transition focus-within:border-brand focus-within:ring-3 focus-within:ring-brand/10">
                  <Textarea
                    className="max-h-36 min-h-[62px] resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
                    placeholder="Demandez une amélioration, une reformulation ou une recherche scientifique…"
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" && !event.shiftKey) {
                        event.preventDefault()
                        sendMessage()
                      }
                    }}
                  />
                  <div className="flex items-center justify-between px-1 pb-1">
                    <div className="flex items-center gap-1">
                      {sections.length > 0 && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="rounded-xl text-xs text-muted-foreground"
                          onClick={() => {
                            setNavigatorView("sections")
                            setNavigatorOpen(true)
                          }}
                        >
                          <FileText className="size-4" /> Section
                        </Button>
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        className="rounded-xl text-xs text-muted-foreground"
                        onClick={() => setRightOpen(true)}
                      >
                        <PanelRightOpen className="size-4" /> Proposition
                      </Button>
                    </div>
                    <Button
                      size="icon"
                      className="rounded-full"
                      disabled={!draft.trim() || busy || backgroundActive}
                      onClick={sendMessage}
                      aria-label="Envoyer la demande"
                    >
                      {(busy || backgroundActive) ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
                    </Button>
                  </div>
                </div>
                <p className="mt-1.5 text-center text-[10px] text-muted-foreground">Entrée pour envoyer · Maj + Entrée pour une nouvelle ligne</p>
              </div>
            </div>
          </>
        ) : null}
      </section>

      <Sheet open={navigatorOpen} onOpenChange={setNavigatorOpen}>
        <SheetContent side="left" className="flex w-[92vw] max-w-[390px] flex-col gap-0 p-0">
          <SheetHeader className="border-b px-4 py-4 pr-14 text-left">
            <SheetTitle>EnnoAmelioration</SheetTitle>
            <SheetDescription>
              Conversations et sections du projet actif, disponibles sans réduire l'espace du chat.
            </SheetDescription>
          </SheetHeader>

          <div className="border-b p-3">
            <div className="grid grid-cols-2 gap-1 rounded-xl bg-muted/60 p-1">
              <button
                type="button"
                className={cn(
                  "rounded-lg px-3 py-2 text-xs font-medium transition",
                  navigatorView === "conversations" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground",
                )}
                onClick={() => setNavigatorView("conversations")}
              >
                Conversations
              </button>
              <button
                type="button"
                className={cn(
                  "rounded-lg px-3 py-2 text-xs font-medium transition",
                  navigatorView === "sections" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground",
                )}
                onClick={() => setNavigatorView("sections")}
                disabled={!current || sections.length === 0}
              >
                Sections {sections.length > 0 ? `(${sections.length})` : ""}
              </button>
            </div>
          </div>

          {navigatorView === "conversations" ? (
            <>
              <div className="border-b p-3">
                <Button
                  className="w-full justify-start gap-2 rounded-xl"
                  onClick={() => {
                    setNavigatorOpen(false)
                    setCreating(true)
                    setCurrent(null)
                    setBackgroundJob(null)
                    setSelectedText("")
                    setRightOpen(false)
                  }}
                >
                  <Plus className="size-4" /> Nouvelle amélioration
                </Button>
              </div>

              <ScrollArea className="min-h-0 flex-1 px-2 py-2">
                <div className="space-y-1 pb-4">
                  {sessions.map((session) => (
                    <div
                      key={session.session_id}
                      className={cn(
                        "group flex items-start rounded-xl border border-transparent p-2.5 transition hover:bg-muted/60",
                        current?.session_id === session.session_id && "border-brand/15 bg-brand/5",
                      )}
                    >
                      <button
                        className="min-w-0 flex-1 text-left"
                        onClick={() => {
                          setNavigatorOpen(false)
                          void openSession(session.session_id)
                        }}
                      >
                        <p className="truncate text-sm font-medium">{session.title}</p>
                        <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-muted-foreground">{session.preview || "Conversation vide"}</p>
                        <div className="mt-2 flex items-center gap-1.5">
                          <Badge variant="outline" className="h-5 px-1.5 text-[10px]">{session.message_count} messages</Badge>
                          {session.candidate_count > 0 && <Badge className="h-5 px-1.5 text-[10px]">À valider</Badge>}
                        </div>
                      </button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="size-7 shrink-0 p-0 opacity-0 group-hover:opacity-100"
                        onClick={() => removeSession(session)}
                        title="Supprimer la conversation"
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  ))}
                  {sessions.length === 0 && <p className="px-3 py-8 text-center text-xs text-muted-foreground">Aucune conversation pour ce projet.</p>}
                </div>
              </ScrollArea>
            </>
          ) : (
            <>
              <div className="border-b p-3">
                <Input
                  className="h-9 rounded-xl text-xs"
                  placeholder="Rechercher une section…"
                  value={sectionQuery}
                  onChange={(event) => setSectionQuery(event.target.value)}
                />
              </div>
              <ScrollArea className="min-h-0 flex-1 px-2 py-2">
                <div className="space-y-0.5 pb-4">
                  {visibleSections.map(({ section, hasChildren }) => (
                    <div
                      key={section.section_id}
                      className={cn(
                        "flex min-w-0 items-center rounded-lg hover:bg-muted",
                        selectedSectionId === section.section_id && "bg-brand/10 text-brand",
                      )}
                      style={{ paddingLeft: `${4 + Math.max(0, section.level - 1) * 10}px` }}
                    >
                      {hasChildren ? (
                        <button
                          type="button"
                          className="grid size-7 shrink-0 place-items-center rounded-md hover:bg-background/80"
                          onClick={() => toggleSection(section.section_id)}
                          title={expandedSectionIds.has(section.section_id) ? "Replier" : "Déplier"}
                        >
                          {expandedSectionIds.has(section.section_id)
                            ? <ChevronDown className="size-3" />
                            : <ChevronRight className="size-3" />}
                        </button>
                      ) : <span className="block size-7 shrink-0" />}
                      <button
                        type="button"
                        className="min-w-0 flex-1 truncate py-2 pr-2 text-left text-xs"
                        onClick={() => {
                          selectSection(section)
                          setNavigatorOpen(false)
                        }}
                        title={section.title}
                      >
                        {section.title}
                      </button>
                    </div>
                  ))}
                  {visibleSections.length === 0 && <p className="px-3 py-8 text-center text-xs text-muted-foreground">Aucune section trouvée.</p>}
                </div>
              </ScrollArea>
            </>
          )}

          <div className="border-t p-3">
            <button
              type="button"
              className="w-full rounded-xl border bg-background p-3 text-left"
              onClick={() => setProjectChooserOpen((open) => !open)}
            >
              <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">Projet actif</p>
              <div className="mt-1 flex items-center gap-2">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-semibold">{activeProject?.project_name}</span>
                  <span className="block truncate text-[11px] text-muted-foreground">{[activeProject?.organisme, activeProject?.year].filter(Boolean).join(" · ")}</span>
                </span>
                <ChevronDown className={cn("size-4 transition-transform", projectChooserOpen && "rotate-180")} />
              </div>
            </button>

            {projectChooserOpen && (
              <div className="mt-2 space-y-2 rounded-xl border bg-background p-2">
                <Input
                  className="h-8 text-xs"
                  placeholder="Rechercher un projet…"
                  value={projectQuery}
                  onChange={(event) => setProjectQuery(event.target.value)}
                />
                <div className="max-h-44 space-y-1 overflow-y-auto">
                  {filteredProjects.map((project) => (
                    <button
                      key={project.id}
                      type="button"
                      className={cn(
                        "w-full rounded-lg px-2 py-2 text-left hover:bg-muted",
                        project.id === projectId && "bg-brand/10 text-brand",
                      )}
                      onClick={() => {
                        setNavigatorOpen(false)
                        void loadProject(project.id)
                      }}
                    >
                      <span className="block truncate text-xs font-medium">{project.project_name}</span>
                      <span className="block truncate text-[10px] text-muted-foreground">{project.organisme} · {project.year}</span>
                    </button>
                  ))}
                </div>
                <Button variant="outline" size="sm" className="w-full justify-start gap-2" onClick={onCreateProject}>
                  <Plus className="size-3.5" /> Nouveau projet
                </Button>
              </div>
            )}

            {projectContext && !projectChooserOpen && (
              <div className="mt-2 flex flex-wrap gap-1">
                <Badge variant="outline" className="h-5 px-1.5 text-[9px]">{projectContext.documents.count} doc.</Badge>
                <Badge variant={projectContext.diagnostic.available ? "default" : "outline"} className="h-5 px-1.5 text-[9px]">Preuves projet</Badge>
                <Badge variant={projectContext.scholar.available ? "default" : "outline"} className="h-5 px-1.5 text-[9px]">Références validées</Badge>
                <Badge variant={projectContext.cir_memory.available ? "default" : "outline"} className="h-5 px-1.5 text-[9px]">Style CIR</Badge>
              </div>
            )}
          </div>
        </SheetContent>
      </Sheet>

      {rightOpen && current && (
        <>
          <button
            type="button"
            className={cn(
              proposalFullscreen
                ? "fixed inset-0 z-40 bg-foreground/10 backdrop-blur-[2px]"
                : "absolute inset-0 z-20 bg-foreground/5 backdrop-blur-[1px]",
            )}
            aria-label="Fermer le panneau d'amélioration"
            onClick={() => setRightOpen(false)}
          />
          <aside
            className={cn(
              "flex min-h-0 flex-col overflow-hidden bg-card transition-[inset,width,height,border-radius] duration-200",
              proposalFullscreen
                ? "fixed inset-2 z-50 h-[calc(100vh-1rem)] w-[calc(100vw-1rem)] rounded-2xl border shadow-2xl sm:inset-3 sm:h-[calc(100vh-1.5rem)] sm:w-[calc(100vw-1.5rem)]"
                : "absolute inset-y-0 right-0 z-30 h-full w-[min(96vw,1120px)] max-w-[96vw] resize-x border-l shadow-[-22px_0_55px_rgba(45,20,80,0.14)] sm:min-w-[560px] sm:w-[min(92vw,1120px)] xl:w-[min(76vw,1120px)]",
            )}
          >
          <div className="flex h-14 items-center gap-2 border-b px-4">
            <Sparkles className="size-4 text-primary" />
            <p className="flex-1 text-sm font-semibold">Proposition d'amélioration</p>
            {candidate ? <Badge>Proposition V{candidate.version_number}</Badge> : <Badge variant="outline">Version active</Badge>}
            {candidate && (
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  className="size-8 rounded-lg border-rose-200 text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                  disabled={busy}
                  onClick={() => decide("rejected")}
                  aria-label="Rejeter la proposition"
                  title="Rejeter la proposition"
                >
                  <X className="size-4" />
                </Button>
                <Button
                  type="button"
                  size="icon"
                  className="size-8 rounded-lg"
                  disabled={busy}
                  onClick={() => decide("accepted")}
                  aria-label="Accepter la proposition"
                  title="Accepter la proposition"
                >
                  <Check className="size-4" />
                </Button>
              </div>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-8 shrink-0 rounded-lg"
              onClick={() => setProposalFullscreen((value) => !value)}
              aria-label={proposalFullscreen ? "Restaurer la fenêtre" : "Agrandir la fenêtre"}
              title={proposalFullscreen ? "Restaurer la fenêtre" : "Plein écran"}
            >
              {proposalFullscreen ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-8 shrink-0 rounded-lg"
              onClick={() => setRightOpen(false)}
              aria-label="Fermer la proposition"
              title="Fermer"
            >
              <X className="size-4" />
            </Button>
          </div>
          <Tabs defaultValue="diff" className="min-h-0 flex-1 gap-0">
            <div className="border-b px-3 py-2">
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="diff">Comparatif</TabsTrigger>
                <TabsTrigger value="sources">Sources</TabsTrigger>
              </TabsList>
            </div>
            <TabsContent value="improved" className="min-h-0 overflow-hidden p-0">
              <div className="flex h-full flex-col">
                <div className="border-b bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
                  {current.source_document_id && documentStructure?.preservation?.source_binary_immutable ? (
                    <span>
                      Gabarit original conservé : seuls les blocs textuels sont proposés à la révision. {Number(documentStructure.figure_count || 0)} figure(s) et {Number(documentStructure.table_count || 0)} tableau(x) restent attachés à leur emplacement dans le document source.
                    </span>
                  ) : (
                    <span>Sélectionnez un passage dans le texte pour cibler la prochaine demande.</span>
                  )}
                </div>
                <Textarea
                  ref={artifactRef}
                  readOnly
                  value={(candidate || activeVersion)?.content || ""}
                  wrap="soft"
                  onSelect={captureSelection}
                  className="min-h-0 flex-1 resize-none rounded-none border-0 p-5 font-sans text-sm leading-6 shadow-none focus-visible:ring-0"
                />
              </div>
            </TabsContent>
            <TabsContent value="diff" className="min-h-0 overflow-hidden p-0">
              {current.source_document_id ? (
                <ImprovementPdfComparator
                  projectId={projectId}
                  sessionId={current.session_id}
                  versionId={candidate?.version_id || ""}
                  activeVersionId={activeVersion?.version_id || ""}
                  changes={comparisonChanges}
                  sourceFilename={sourceDocument?.filename || "Document source"}
                />
              ) : (
                <ImprovementTextComparator
                  originalVersion={comparisonOriginal || activeVersion}
                  proposedVersion={candidate}
                  changes={comparisonChanges}
                  sourceLabel={
                    sections.find((row) => row.section_id === selectedSectionId)?.title
                    || current.title
                    || "Section"
                  }
                />
              )}
            </TabsContent>
            <TabsContent value="audit" className="min-h-0 overflow-hidden p-0">
              <ScrollArea className="h-full">
                <div className="space-y-3 p-4">
                  {(candidate?.audit?.findings || []).length ? candidate?.audit?.findings?.map((finding, index) => (
                    <div key={`${finding.code}-${index}`} className="rounded-xl border p-3">
                      <div className="flex items-center gap-2"><ShieldCheck className="size-4 text-primary" /><p className="text-sm font-semibold">{finding.label}</p><Badge variant="outline" className="ml-auto text-[10px]">{finding.severity}</Badge></div>
                      <p className="mt-2 text-xs leading-5 text-muted-foreground">{finding.explanation}</p>
                      <p className="mt-2 text-xs leading-5">{finding.recommendation}</p>
                    </div>
                  )) : <p className="text-sm text-muted-foreground">L'audit détaillé apparaîtra avec la prochaine proposition.</p>}
                  {((structuredResult?.unsupported_claims || []) as Array<Record<string, any>>).map((claim, index) => (
                    <div key={`unsupported-${index}`} className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-amber-950">
                      <p className="text-sm font-semibold">Preuve à confirmer</p>
                      <p className="mt-2 text-xs leading-5">{claim.claim || claim.reason}</p>
                      <p className="mt-1 text-xs text-amber-800">{claim.reason}</p>
                    </div>
                  ))}
                  {((structuredResult?.questions_for_consultant || []) as string[]).map((question, index) => (
                    <div key={`question-${index}`} className="rounded-xl border border-blue-200 bg-blue-50 p-3 text-xs leading-5 text-blue-950">
                      <span className="font-semibold">À valider : </span>{question}
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </TabsContent>
            <TabsContent value="sources" className="min-h-0 overflow-hidden p-0">
              <ScrollArea className="h-full">
                <div className="space-y-4 p-4">
                  <div className="rounded-xl border border-emerald-200 bg-emerald-50/70 p-3 text-emerald-950">
                    <div className="flex items-center gap-2">
                      <ShieldCheck className="size-4" />
                      <p className="text-sm font-semibold">Corpus privé de cette conversation</p>
                    </div>
                    <p className="mt-2 text-xs leading-5 text-emerald-800">
                      Les sources et références validées de cette conversation ne sont utilisées par aucune autre conversation.
                      Pour travailler sur un autre dossier, ouvrez simplement une nouvelle conversation ; aucun changement
                      de projet dans le tableau de bord n'est nécessaire.
                    </p>
                  </div>
                  {current.context?.scholar_handoff && (
                    <div className="rounded-xl border border-primary/30 bg-primary/5 p-3">
                      <div className="flex items-center gap-2"><Library className="size-4 text-primary" /><p className="text-sm font-semibold">Recherche scientifique liée</p></div>
                      <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-muted-foreground">
                        {current.context.scholar_handoff.assistant_message
                          || current.context.scholar_handoff.error
                          || "La recherche ciblée est rattachée à cette amélioration."}
                      </p>
                    </div>
                  )}
                  {((structuredResult?.sources_used || []) as Array<Record<string, any>>).length > 0 && (
                    <div className="rounded-xl border border-primary/30 bg-primary/5 p-3">
                      <div className="flex items-center gap-2"><Library className="size-4 text-primary" /><p className="text-sm font-semibold">Sources effectivement reliées aux modifications</p></div>
                      <div className="mt-3 space-y-2">
                        {((structuredResult?.sources_used || []) as Array<Record<string, any>>).map((source, index) => {
                          const consultUrl = articleConsultUrl(source)
                          const excerpt = sourceEvidenceExcerpt(source)
                          return <div key={source.evidence_id || index} className="rounded-lg bg-background p-2.5 text-xs">
                            <p className="font-medium">{source.evidence_id} {source.title ? `· ${source.title}` : ""}</p>
                            <p className="mt-1 text-muted-foreground">
                              {[
                                Array.isArray(source.authors) ? source.authors.slice(0, 4).join(", ") : "",
                                source.year,
                              ].filter(Boolean).join(" · ")}
                            </p>
                            {excerpt && <p className="mt-2 whitespace-pre-wrap rounded-md bg-muted/60 p-2 leading-5 text-muted-foreground">{excerpt}</p>}
                            {consultUrl && (
                              <a
                                href={consultUrl}
                                target="_blank"
                                rel="noreferrer"
                                className="mt-2 inline-flex h-8 items-center rounded-lg border bg-background px-3 font-medium text-primary hover:bg-muted"
                              >
                                Consulter l’article
                              </a>
                            )}
                          </div>
                        })}
                      </div>
                    </div>
                  )}
                  {researchSources.length > 0 && (
                    <div className="space-y-3">
                      {researchSources.map((source) => {
                        const decision = normalizeSourceDecision(source.consultant_decision)
                        const siteUrl = publicationSiteUrl(source)
                        const pdfUrl = String(source.pdf_url || (isDirectPdfUrl(source.url) ? source.url : "") || "").trim()
                        return (
                          <div key={source.candidate_id} className="rounded-xl border p-3">
                            <div className="flex items-start gap-2">
                              <div className="min-w-0 flex-1">
                                <p className="text-sm font-semibold leading-5">{source.title}</p>
                                <p className="mt-1 text-[11px] text-muted-foreground">
                                  {[Array.isArray(source.authors) ? source.authors.slice(0, 3).join(", ") : "", source.year, source.provider].filter(Boolean).join(" · ")}
                                </p>
                              </div>
                              <Badge variant={decision === "accepted" ? "default" : "outline"} className="shrink-0 text-[10px]">
                                {decision === "accepted" ? "Gardée" : decision === "rejected" ? "Écartée" : "À valider"}
                              </Badge>
                            </div>
                            {source.reason && <p className="mt-2 text-xs leading-5">{source.reason}</p>}
                            {source.abstract && <p className="mt-2 line-clamp-5 text-xs leading-5 text-muted-foreground">{source.abstract}</p>}
                            <div className="mt-3 flex flex-wrap items-center gap-2">
                              {siteUrl && (
                                <a
                                  href={siteUrl}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="inline-flex h-7 items-center rounded-lg border bg-background px-2.5 text-[11px] font-medium hover:bg-muted"
                                >
                                  Consulter la publication
                                </a>
                              )}
                              {!siteUrl && pdfUrl && (
                                <a
                                  href={pdfUrl}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="inline-flex h-7 items-center rounded-lg border bg-background px-2.5 text-[11px] font-medium hover:bg-muted"
                                >
                                  Ouvrir le PDF
                                </a>
                              )}
                              {decision !== "rejected" && (
                                  <Button variant="outline" size="sm" className="h-7 text-[11px]" disabled={busy} onClick={() => decideSource(source.candidate_id, "rejected")}>
                                    <X className="size-3" /> Écarter
                                  </Button>
                              )}
                              {decision !== "accepted" && (
                                  <Button size="sm" className="h-7 text-[11px]" disabled={busy} onClick={() => decideSource(source.candidate_id, "accepted")}>
                                    <Check className="size-3" /> Garder
                                  </Button>
                              )}
                              {source.article_card_ready && <span className="text-[10px] text-emerald-700">Preuve préparée pour la rédaction</span>}
                              {decision === "accepted" && !source.article_card_ready && (
                                <span className="text-[10px] text-amber-700">Gardée, mais preuve exploitable pour la rédaction encore indisponible</span>
                              )}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}
                  <div className="rounded-xl border p-3">
                    <div className="flex items-center gap-2"><Library className="size-4 text-primary" /><p className="text-sm font-semibold">Corpus scientifique validé</p></div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {candidate?.evidence?.scholar?.selected_article_count != null
                        ? `${candidate.evidence.scholar.selected_article_count} article(s) sélectionné(s), ${candidate.evidence.scholar.writing_ready_card_count || 0} preuve(s) exploitable(s) pour la rédaction.`
                        : "Aucun renfort scientifique n'a été demandé pour cette version."}
                    </p>
                    <div className="mt-3 space-y-2">
                      {(candidate?.evidence?.scholar?.evidence || []).map((source: any, index: number) => {
                        const consultUrl = articleConsultUrl(source)
                        const excerpt = sourceEvidenceExcerpt(source)
                        return <div key={`${source.article_id}-${index}`} className="rounded-lg bg-muted/50 p-2.5 text-xs">
                          <p className="font-medium">{source.citation_id ? `[${source.citation_id}] ` : ""}{source.title}</p>
                          <p className="mt-1 text-muted-foreground">
                            {[
                              Array.isArray(source.authors) ? source.authors.slice(0, 4).join(", ") : "",
                              source.year,
                            ].filter(Boolean).join(" · ")}
                          </p>
                          {excerpt && <p className="mt-2 whitespace-pre-wrap rounded-md bg-background p-2 leading-5 text-muted-foreground">{excerpt}</p>}
                          {consultUrl && (
                            <a href={consultUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex h-8 items-center rounded-lg border bg-background px-3 font-medium text-primary hover:bg-muted">
                              Consulter l’article
                            </a>
                          )}
                        </div>
                      })}
                    </div>
                  </div>
                  <div className="rounded-xl border p-3">
                    <div className="flex items-center gap-2"><ShieldCheck className="size-4 text-primary" /><p className="text-sm font-semibold">Analyse CIR du dossier</p></div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {candidate?.evidence?.diagnostic?.available
                        ? `${candidate.evidence.diagnostic.verrous?.length || 0} verrou(s) transmis comme contexte consultatif.`
                        : "Aucun contrôle d'éligibilité n'a été demandé pour cette version."}
                    </p>
                  </div>
                </div>
              </ScrollArea>
            </TabsContent>
            {current.source_document_id && (
              <TabsContent value="document" className="min-h-0 overflow-hidden p-0">
                <div className="flex h-full min-h-0 flex-col">
                  <div className="flex items-center gap-2 border-b bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
                    <FileText className="size-3.5" />
                    <span className="min-w-0 flex-1 truncate">{sourceDocument?.filename || "Document source original"}</span>
                    {sourcePreviewUrl && (
                      <a href={sourcePreviewUrl} target="_blank" rel="noreferrer" className="font-medium text-primary hover:underline">
                        Ouvrir
                      </a>
                    )}
                  </div>
                  {sourcePreviewLoading ? (
                    <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
                      <Loader2 className="size-4 animate-spin" /> Chargement du document original…
                    </div>
                  ) : sourcePreviewError ? (
                    <div className="m-4 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
                      {sourcePreviewError}
                    </div>
                  ) : sourcePreviewUrl && sourceDocumentIsPdf ? (
                    <iframe
                      title={sourceDocument?.filename || "Document source"}
                      src={`${sourcePreviewUrl}#zoom=page-fit&view=Fit`}
                      className="min-h-0 w-full flex-1 border-0 bg-muted/20"
                    />
                  ) : sourcePreviewUrl ? (
                    <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">
                      <FileText className="size-10 text-muted-foreground" />
                      <p className="max-w-md text-sm text-muted-foreground">
                        Le document original est conservé. Ce format s'ouvre dans son application native afin de préserver sa mise en page.
                      </p>
                      <a href={sourcePreviewUrl} download={sourceDocument?.filename || true} className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground">
                        Télécharger le document original
                      </a>
                    </div>
                  ) : null}
                </div>
              </TabsContent>
            )}
          </Tabs>
          <div className="border-t p-3">
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold"><History className="size-3.5" /> Historique des versions</div>
            <div className="flex gap-2 overflow-x-auto pb-1">
              {versions.map((version) => (
                <Button
                  key={version.version_id}
                  variant={version.is_active ? "default" : "outline"}
                  size="sm"
                  className="h-7 shrink-0 gap-1.5 text-[11px]"
                  disabled={version.is_active || version.status === "candidate" || version.status === "rejected" || busy}
                  onClick={() => restore(version)}
                  title={version.is_active ? "Version active" : "Restaurer cette version"}
                >
                  {!version.is_active && version.status !== "candidate" && <RotateCcw className="size-3" />}
                  {versionLabel(version)}
                </Button>
              ))}
            </div>
          </div>
          </aside>
        </>
      )}
    </div>
  )
}
