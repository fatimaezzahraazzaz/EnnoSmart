"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import {
  AlertTriangle,
  BookOpenText,
  Bot,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronUp,
  Copy,
  Download,
  ExternalLink,
  FilePlus2,
  FileText,
  Globe2,
  Library,
  Loader2,
  MessageSquarePlus,
  MessageSquareText,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  Search,
  Send,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Textarea } from "@/components/ui/textarea"
import {
  createGuidedResearchSession,
  deleteGuidedResearchSession,
  decideGuidedResearchSources,
  exportStateOfArtDocx,
  getGuidedResearchCorpus,
  getGuidedResearchSession,
  getStateOfArtVisualBlob,
  getUploadedScholarArticlePdf,
  listGuidedResearchSessions,
  removeGuidedResearchCorpusArticle,
  sendGuidedResearchMessage,
  uploadAndExtractArticlePdf,
  uploadNewScholarSource,
  type ArticleRead,
  type GuidedResearchConversationTurn,
  type GuidedResearchSession,
} from "@/lib/api"

type ResearchCandidate = {
  candidate_id: string
  title?: string
  authors?: string[]
  year?: number | null
  url?: string | null
  pdf_url?: string | null
  doi?: string | null
  abstract?: string | null
  source_providers?: string[]
  candidate_kind?: string
  relevance_score?: number
  selection_priority_score?: number
  open_access?: boolean
  consultant_decision?: string
  fulltext_verified?: boolean
  scientific_evidence_eligible?: boolean
  fulltext_preparation?: {
    article_id?: number | null
    status?: string | null
    usable_as_scientific_evidence?: boolean
    fulltext_ready?: boolean
    ready_for_writing?: boolean
  }
  relevance_role?:
    | "direct_evidence"
    | "connected_evidence"
    | "official_documentation"
    | "implementation"
  role_reason?: string
  current_research_batch?: boolean

  // Statuts d'accès / extraction renvoyés par les différentes versions du backend.
  // Ils restent optionnels pour conserver la compatibilité avec les anciennes sessions.
  access_status?: string | null
  evidence_status?: string | null
  evidence_reason_code?: string | null
  evidence_reason_detail?: string | null
  evidence_recommended_action?: string | null
  manual_upload_required?: boolean
  browser_download_url?: string | null
  source_json?: Record<string, any>
}

type Props = {
  projectId: number
  projectLabel?: string
  selectedArticles?: ArticleRead[]
  onCorpusChanged?: () => Promise<void> | void
  onGenerate: (guidedSessionId?: string | null) => Promise<any> | any
  onRefreshDraft?: () => Promise<void> | void
  draftMarkdown?: string
  generating?: boolean
  generationError?: string | null
  immersive?: boolean
  projectOptions?: Array<{ id: number; label: string }>
  onProjectChange?: (projectId: number) => Promise<void> | void
  onBackToArticles?: () => void
}

const storageKey = (projectId: number) =>
  `ennoscholar_guided_session_${projectId}`

function formatSessionDate(value?: string) {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ""
  return new Intl.DateTimeFormat("fr-FR", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date)
}

function sessionLabel(session: GuidedResearchSession) {
  const title = String(session.title || "").trim()
  if (title) return title
  return session.message_count ? "Conversation scientifique" : "Nouvelle conversation"
}

function citationText(text: string) {
  return text.split(/(\[A\d+(?:\s*,\s*A\d+)*\])/g).map((part, index) =>
    /^\[A\d+(?:\s*,\s*A\d+)*\]$/.test(part) ? (
      <span
        key={index}
        className="mx-0.5 rounded-md bg-brand/8 px-1.5 py-0.5 text-xs font-semibold text-brand"
      >
        {part}
      </span>
    ) : (
      <span key={index}>{part}</span>
    ),
  )
}

function StateOfArtFigure({
  projectId,
  visualId,
  alt,
}: {
  projectId: number
  visualId: string
  alt: string
}) {
  const [source, setSource] = useState("")
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    let objectUrl = ""
    setFailed(false)
    void getStateOfArtVisualBlob(projectId, visualId)
      .then((blob) => {
        if (!active) return
        objectUrl = URL.createObjectURL(blob)
        setSource(objectUrl)
      })
      .catch(() => {
        if (active) setFailed(true)
      })
    return () => {
      active = false
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [projectId, visualId])

  if (failed) {
    return (
      <div className="my-5 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
        La figure originale n’a pas pu être chargée. Sa provenance reste
        disponible dans la légende.
      </div>
    )
  }
  return (
    <figure className="my-6 overflow-hidden rounded-xl border border-border bg-card shadow-xs">
      {source ? (
        <img
          src={source}
          alt={alt}
          className="mx-auto max-h-[560px] w-auto max-w-full object-contain"
        />
      ) : (
        <div className="flex h-44 items-center justify-center text-muted-foreground">
          <Loader2 className="size-5 animate-spin" />
        </div>
      )}
    </figure>
  )
}

function DraftPreview({
  markdown,
  projectId,
}: {
  markdown: string
  projectId: number
}) {
  if (!markdown.trim()) {
    return (
      <div className="flex h-full min-h-[430px] flex-col items-center justify-center px-8 text-center">
        <div className="rounded-xl bg-muted p-4 text-muted-foreground">
          <FileText className="size-7" />
        </div>
        <p className="mt-4 font-semibold text-foreground">
          Aucun état de l’art rédigé
        </p>
        <p className="mt-2 max-w-sm text-sm leading-6 text-muted-foreground">
          Discutez du plan et des sources dans le chat. L’artefact s’ouvrira
          automatiquement dès qu’une rédaction sera disponible.
        </p>
      </div>
    )
  }

  return (
    <article className="mx-auto max-w-3xl px-6 py-8">
      <div className="space-y-2 text-[14px] leading-7 text-foreground/85">
        {markdown.split(/\n/).map((raw, index) => {
          const line = raw.trim()
          if (!line) return <div key={index} className="h-2" />
          const visualMatch = line.match(
            /^!\[(.*)\]\(ennoscholar-visual:\/\/([A-Za-z0-9_-]+)\)$/,
          )
          if (visualMatch) {
            return (
              <StateOfArtFigure
                key={`${visualMatch[2]}-${index}`}
                projectId={projectId}
                visualId={visualMatch[2]}
                alt={visualMatch[1] || "Figure scientifique sourcée"}
              />
            )
          }
          if (line.startsWith("# ")) {
            return (
              <h1
                key={index}
                className="mb-6 text-2xl font-bold tracking-tight text-foreground"
              >
                {line.slice(2)}
              </h1>
            )
          }
          if (line.startsWith("## ")) {
            return (
              <h2
                key={index}
                className="mb-3 mt-9 border-b border-border pb-2 text-xl font-semibold text-foreground"
              >
                {line.slice(3)}
              </h2>
            )
          }
          if (line.startsWith("### ")) {
            return (
              <h3
                key={index}
                className="mb-2 mt-7 text-base font-semibold text-foreground"
              >
                {line.slice(4)}
              </h3>
            )
          }
          if (line.startsWith("- ")) {
            return (
              <p key={index} className="ml-4 border-l-2 border-brand/25 pl-3">
                {citationText(line.slice(2))}
              </p>
            )
          }
          if (line.startsWith("*") && line.endsWith("*")) {
            return (
              <p key={index} className="mb-5 text-center text-xs italic leading-5 text-muted-foreground">
                {citationText(line.slice(1, -1))}
              </p>
            )
          }
          return (
            <p key={index} className="text-justify">
              {citationText(line)}
            </p>
          )
        })}
      </div>
    </article>
  )
}

function getCandidateAccessInfo(candidate: ResearchCandidate) {
  const raw: any = candidate || {}
  const preparation: any = raw.fulltext_preparation || {}
  const sourceJson: any = raw.source_json || {}
  const preflight: any = sourceJson.evidence_preflight || {}
  const accessProbe: any = sourceJson.access_probe_result || {}

  const accessStatus = String(
    raw.access_status ||
      preparation.access_status ||
      preflight.access_status ||
      sourceJson.access_status ||
      "",
  )
    .trim()
    .toUpperCase()

  const evidenceStatus = String(
    raw.evidence_status ||
      preparation.evidence_status ||
      preflight.evidence_status ||
      preparation.status ||
      "",
  )
    .trim()
    .toUpperCase()

  const reasonCode = String(
    raw.evidence_reason_code ||
      preparation.evidence_reason_code ||
      preparation.reason_code ||
      preflight.reason_code ||
      "",
  )
    .trim()
    .toUpperCase()

  const reasonDetail = String(
    raw.evidence_reason_detail ||
      preparation.evidence_reason_detail ||
      preparation.reason_detail ||
      preparation.reason ||
      preparation.error ||
      preflight.reason_detail ||
      accessProbe.reason_detail ||
      "",
  ).trim()

  const recommendedAction = String(
    raw.evidence_recommended_action ||
      preparation.evidence_recommended_action ||
      preparation.recommended_action ||
      preflight.recommended_action ||
      "",
  ).trim()

  // Certaines versions du backend exposent la cause uniquement sous forme de texte.
  // On agrège donc les champs connus afin de ne pas perdre l'information dans les
  // conversations créées avant l'ajout des statuts structurés.
  const signal = [
    accessStatus,
    evidenceStatus,
    reasonCode,
    reasonDetail,
    recommendedAction,
    preparation.status,
    preparation.access_kind,
    preflight.access_kind,
  ]
    .filter(Boolean)
    .join(" ")
    .toUpperCase()

  const paywalled =
    accessStatus === "PAYWALLED" ||
    reasonCode === "PAYWALL_BLOCKED" ||
    /\bPAYWALL(?:ED)?\b|\bPAID\b|\bPAYANT\b|\bSUBSCRIPTION\b|\bABONNEMENT\b/.test(
      signal,
    )

  const automationBlocked =
    accessStatus === "AUTOMATION_BLOCKED" ||
    [
      "PUBLIC_PDF_BROWSER_ONLY",
      "ANTIBOT_BLOCKED",
      "AUTOMATED_ACCESS_BLOCKED",
      "BROWSER_DOWNLOAD_REQUIRED",
    ].includes(reasonCode) ||
    [
      "BROWSER_DOWNLOAD_REQUIRED",
      "AUTOMATION_BLOCKED",
      "ANTIBOT_BLOCKED",
    ].includes(evidenceStatus) ||
    /ANTI[-_ ]?BOT|AUTOMATION[_ ]BLOCKED|AUTOMATED[_ ]ACCESS[_ ]BLOCKED|PUBLIC[_ ]PDF[_ ]BROWSER[_ ]ONLY|BROWSER[_ ]DOWNLOAD[_ ]REQUIRED|CLOUDFLARE|ROBOTS?\b|HTTP\s*403|STATUS\s*403|\bFORBIDDEN\b/.test(
      signal,
    )

  const manualUploadRequired = Boolean(
    raw.manual_upload_required ||
      preparation.manual_upload_required ||
      preflight.manual_upload_required ||
      paywalled ||
      automationBlocked ||
      [
        "ACCESS_UNAVAILABLE",
        "BROWSER_DOWNLOAD_REQUIRED",
        "ABSTRACT_READY",
        "METADATA_ONLY",
        "EXTRACTION_FAILED",
      ].includes(evidenceStatus),
  )

  const browserDownloadUrl = String(
    raw.browser_download_url ||
      preparation.browser_download_url ||
      preflight.browser_download_url ||
      accessProbe.browser_download_url ||
      raw.pdf_url ||
      raw.url ||
      "",
  ).trim()

  const publicationUrl = String(raw.url || raw.pdf_url || "").trim()

  return {
    accessStatus,
    evidenceStatus,
    reasonCode,
    reasonDetail,
    recommendedAction,
    paywalled,
    automationBlocked,
    manualUploadRequired,
    browserDownloadUrl,
    publicationUrl,
  }
}

function getCandidateDisplayState(candidate: ResearchCandidate) {
  const decided = String(candidate.consultant_decision || "")
  const documentation = [
    "official_documentation",
    "documentation",
    "software_repository",
    "research_output",
  ].includes(String(candidate.candidate_kind))
  const preparation = candidate.fulltext_preparation || {}
  const fulltextReady = Boolean(
    candidate.fulltext_verified ||
      candidate.scientific_evidence_eligible ||
      preparation.usable_as_scientific_evidence ||
      preparation.fulltext_ready ||
      preparation.ready_for_writing,
  )
  const pendingExtraction =
    decided === "accepted" && !documentation && !fulltextReady
  const articleId = Number(preparation.article_id || 0)
  return { decided, documentation, pendingExtraction, articleId }
}

function CandidateCard({
  candidate,
  busy,
  onDecision,
  onUploadPdf,
}: {
  candidate: ResearchCandidate
  busy: boolean
  onDecision: (
    candidate: ResearchCandidate,
    decision: "accepted" | "rejected",
  ) => void
  onUploadPdf: (candidate: ResearchCandidate, file: File) => void
}) {
  const uploadInputRef = useRef<HTMLInputElement | null>(null)
  const { decided, documentation, pendingExtraction, articleId } = getCandidateDisplayState(candidate)
  const roleLabel =
    {
      direct_evidence: "Preuve directe",
      connected_evidence: "Source connexe",
      official_documentation: "Documentation officielle",
      implementation: "Implémentation",
    }[String(candidate.relevance_role)] || ""
  const accessInfo = getCandidateAccessInfo(candidate)

  // Pour une source non encore gardée, on conserve le lien de consultation habituel.
  // Une fois gardée mais non extraite, le lien privilégié devient celui que le
  // navigateur peut réellement ouvrir/télécharger (éditeur, PDF public, etc.).
  const consultationUrl = pendingExtraction
    ? accessInfo.browserDownloadUrl || accessInfo.publicationUrl
    : candidate.pdf_url || candidate.url

  return (
    <div className="rounded-xl border border-border bg-card p-4 shadow-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge
          variant="outline"
          className={
            documentation
              ? "border-violet-200 bg-violet-50 text-violet-700"
              : "border-brand/25 bg-brand/8 text-brand"
          }
        >
          {documentation ? (
            <Globe2 className="mr-1 size-3" />
          ) : (
            <BookOpenText className="mr-1 size-3" />
          )}
          {documentation ? "Documentation" : "Article scientifique"}
        </Badge>
        {roleLabel && <Badge variant="outline">{roleLabel}</Badge>}
        {candidate.open_access && <Badge variant="outline">Accès public</Badge>}
      </div>

      <p className="mt-3 text-sm font-semibold leading-5 text-foreground">
        {candidate.title || "Source sans titre"}
      </p>
      <p className="mt-1 text-xs text-muted-foreground">
        {[
          candidate.authors?.slice(0, 2).join(", "),
          candidate.year,
          candidate.source_providers?.join(" · "),
        ]
          .filter(Boolean)
          .join(" — ")}
      </p>
      {candidate.abstract && (
        <p className="mt-2 line-clamp-3 text-xs leading-5 text-muted-foreground">
          {candidate.abstract}
        </p>
      )}
      {candidate.role_reason && (
        <p className="mt-2 rounded-lg bg-muted/60 p-2.5 text-xs leading-5 text-muted-foreground">
          {candidate.role_reason}
        </p>
      )}

      {/* A retained source is not a validated full text until extraction succeeds.
          Display the extraction state without inferring an access restriction. */}
      {pendingExtraction && (
        <div
          className="mt-3 rounded-xl border border-amber-200 bg-amber-50/70 p-3 dark:border-amber-800 dark:bg-amber-950/30"
          aria-busy={busy}
        >
          <div className="flex items-start gap-2.5">
            <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-background text-amber-700 dark:text-amber-300">
              {busy ? (
                <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              ) : (
                <AlertTriangle className="size-4" aria-hidden="true" />
              )}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold text-amber-900 dark:text-amber-200" role="status">
                {busy ? "Extraction en cours…" : "Extraction interrompue"}
              </p>
              <p className="mt-1 text-xs leading-5 text-amber-800 dark:text-amber-200">
                {busy
                  ? "Le PDF est en cours de préparation. La carte sera mise à jour à la fin de l’extraction."
                  : "L’extraction automatique a été interrompue. Cet article est gardé, mais n’est pas encore dans le corpus. Téléchargez le PDF, puis importez-le ici pour terminer l’extraction."}
              </p>

              <div className="mt-3 flex flex-wrap items-center gap-2">
                {consultationUrl ? (
                  <a
                    href={consultationUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    title="Ouvrir la source dans un nouvel onglet pour télécharger le PDF"
                    className="inline-flex min-h-10 items-center rounded-lg border border-amber-300 bg-background px-3 text-xs font-medium text-amber-900 transition-colors hover:bg-amber-100 focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/25 dark:border-amber-700 dark:text-amber-200 dark:hover:bg-amber-900/40"
                  >
                    <Download className="mr-1.5 size-3.5" aria-hidden="true" />
                    Télécharger le PDF
                  </a>
                ) : (
                  <Button type="button" size="sm" variant="outline" className="min-h-10" disabled title="Aucun lien de téléchargement disponible pour cet article">
                    <Download className="mr-1.5 size-3.5" aria-hidden="true" />
                    Télécharger le PDF
                  </Button>
                )}
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className="min-h-10 rounded-lg border-amber-300 bg-background text-amber-900 hover:bg-amber-100 dark:border-amber-700 dark:text-amber-200 dark:hover:bg-amber-900/40"
                  disabled={busy || articleId <= 0}
                  onClick={() => uploadInputRef.current?.click()}
                >
                  {busy ? (
                    <Loader2 className="mr-1.5 size-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
                  ) : (
                    <UploadCloud className="mr-1.5 size-3.5" aria-hidden="true" />
                  )}
                  {busy ? "Import et extraction…" : "Importer le PDF"}
                </Button>
                <input
                  ref={uploadInputRef}
                  type="file"
                  accept="application/pdf,.pdf"
                  aria-label={`Importer le PDF de ${candidate.title || "cet article"}`}
                  className="hidden"
                  disabled={busy || articleId <= 0}
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    event.currentTarget.value = ""
                    if (file) onUploadPdf(candidate, file)
                  }}
                />
              </div>
              {articleId <= 0 && (
                <p className="mt-2 text-xs leading-5 text-amber-800 dark:text-amber-200">
                  L’import nécessite que la source soit associée à un article.
                </p>
              )}

              <p className="mt-2 text-[11px] leading-4 text-muted-foreground">
                Après import, EnnoScholar vérifie le PDF, extrait le texte
                intégral et n’ajoute l’article au corpus qu’après une extraction
                réussie.
              </p>
            </div>
          </div>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {!pendingExtraction && consultationUrl && (
          <a
            href={consultationUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex min-h-10 items-center rounded-lg px-3 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/25"
          >
            <ExternalLink className="mr-1 size-3" />
            Consulter
          </a>
        )}

        {!["accepted", "rejected"].includes(decided) ? (
          <>
            <Button
              size="sm"
              className="min-h-10 rounded-lg"
              disabled={busy}
              onClick={() => onDecision(candidate, "accepted")}
            >
              {busy ? (
                <Loader2 className="mr-1 size-3 animate-spin" />
              ) : (
                <Check className="mr-1 size-3" />
              )}
              Garder et extraire
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="min-h-10 rounded-lg"
              disabled={busy}
              onClick={() => onDecision(candidate, "rejected")}
            >
              <X className="mr-1 size-3" />
              Écarter
            </Button>
          </>
        ) : decided === "accepted" && !pendingExtraction ? (
          <span role="status">
            <Button
              type="button"
              size="sm"
              disabled
              className="min-h-10 rounded-lg bg-success text-success-foreground disabled:opacity-100"
            >
              <Check className="mr-1 size-3.5" aria-hidden="true" />
              Gardé · validé
            </Button>
          </span>
        ) : (
          <Badge
            role="status"
            className={
              decided === "rejected"
                ? "bg-slate-500"
                : "border border-amber-200 bg-amber-50 text-amber-800 hover:bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200"
            }
          >
            {decided === "rejected"
              ? "Écarté"
              : "Gardé · à compléter"}
          </Badge>
        )}
      </div>
    </div>
  )
}

function getMessageCandidates(
  message: GuidedResearchConversationTurn | any,
): ResearchCandidate[] {
  const metadata: any = message?.metadata || {}
  const direct = metadata?.candidates
  const nested = metadata?.research?.candidates
  const rows = Array.isArray(direct)
    ? direct
    : Array.isArray(nested)
      ? nested
      : []

  return rows.filter(
    (candidate: ResearchCandidate) =>
      candidate && String(candidate.candidate_id || "").trim(),
  )
}

function withMessageCandidates(
  message: GuidedResearchConversationTurn | any,
  candidates: ResearchCandidate[],
) {
  const metadata: any = { ...(message?.metadata || {}) }

  // Update the same attachment that getMessageCandidates reads first.
  if (Array.isArray(metadata.candidates)) {
    metadata.candidates = candidates
  } else if (metadata?.research && Array.isArray(metadata.research?.candidates)) {
    metadata.research = {
      ...metadata.research,
      candidates,
    }
  } else {
    metadata.candidates = candidates
  }

  return {
    ...message,
    metadata,
  }
}

function updateCandidateInMessages(
  messages: GuidedResearchConversationTurn[],
  candidateId: string,
  updater: (candidate: ResearchCandidate) => ResearchCandidate,
) {
  return messages.map((message) => {
    const candidates = getMessageCandidates(message)
    if (!candidates.some((candidate) => candidate.candidate_id === candidateId)) {
      return message
    }

    return withMessageCandidates(
      message,
      candidates.map((candidate) =>
        candidate.candidate_id === candidateId
          ? updater(candidate)
          : candidate,
      ),
    )
  })
}

function hydrateResearchAttachments(
  messages: GuidedResearchConversationTurn[],
  current: any,
  legacyCandidates: ResearchCandidate[],
) {
  const selected = Array.isArray(current?.artifacts?.selected_sources)
    ? current.artifacts.selected_sources
    : []

  const selectedById = new Map(
    selected
      .filter((candidate: ResearchCandidate) => candidate?.candidate_id)
      .map((candidate: ResearchCandidate) => [
        candidate.candidate_id,
        candidate,
      ]),
  )

  let hydrated = messages.map((message) => {
    const candidates = getMessageCandidates(message)
    if (!candidates.length) return message

    return withMessageCandidates(
      message,
      candidates.map((candidate) => ({
        ...candidate,
        ...(selectedById.get(candidate.candidate_id) || {}),
      })),
    )
  })

  // Compatibilité avec les anciennes conversations :
  // si le backend ne stockait pas encore les candidats dans metadata,
  // le dernier batch de recherche est rattaché au dernier message assistant.
  if (
    legacyCandidates.length > 0 &&
    !hydrated.some((message) => getMessageCandidates(message).length > 0)
  ) {
    let targetIndex = -1
    for (let index = hydrated.length - 1; index >= 0; index -= 1) {
      if (hydrated[index]?.role === "assistant") {
        targetIndex = index
        break
      }
    }

    if (targetIndex >= 0) {
      hydrated = hydrated.map((message, index) =>
        index === targetIndex
          ? withMessageCandidates(message, legacyCandidates)
          : message,
      )
    }
  }

  return hydrated
}

function ResearchAttachment({
  candidates,
  busyCandidateId,
  onDecision,
  onUploadPdf,
}: {
  candidates: ResearchCandidate[]
  busyCandidateId: string
  onDecision: (
    candidate: ResearchCandidate,
    decision: "accepted" | "rejected",
  ) => void
  onUploadPdf: (candidate: ResearchCandidate, file: File) => void
}) {
  const [expanded, setExpanded] = useState(false)

  const directCount = candidates.filter(
    (candidate) => candidate.relevance_role === "direct_evidence",
  ).length
  const connectedCount = candidates.filter(
    (candidate) => candidate.relevance_role === "connected_evidence",
  ).length
  const documentationCount = candidates.filter((candidate) =>
    ["official_documentation", "documentation"].includes(
      String(candidate.relevance_role || candidate.candidate_kind || ""),
    ),
  ).length
  const keptCount = candidates.filter(
    (candidate) => candidate.consultant_decision === "accepted",
  ).length

  const preview = candidates.slice(0, 3)

  return (
    <section className="mt-3 overflow-hidden rounded-2xl border border-brand/15 bg-card shadow-[0_4px_18px_rgba(59,34,109,0.055)]">
      <div className="flex flex-col gap-3 border-b border-border/70 bg-brand/[0.025] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-brand/[0.08] text-brand">
              <Search className="size-3.5" aria-hidden="true" />
            </span>
            <p className="text-xs font-semibold text-foreground">
              Recherche scientifique
            </p>
            <Badge
              variant="outline"
              className="rounded-full border-brand/15 bg-background text-[10px]"
            >
              {candidates.length} source{candidates.length > 1 ? "s" : ""}
            </Badge>
          </div>

          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            {directCount > 0 && <span>{directCount} directe(s)</span>}
            {connectedCount > 0 && <span>{connectedCount} connexe(s)</span>}
            {documentationCount > 0 && (
              <span>{documentationCount} documentation(s)</span>
            )}
            {keptCount > 0 && (
              <span className="font-medium text-success">
                {keptCount} gardée(s)
              </span>
            )}
          </div>
        </div>

        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="min-h-9 shrink-0 rounded-xl px-3 text-xs"
          onClick={() => setExpanded((current) => !current)}
          aria-expanded={expanded}
        >
          {expanded ? (
            <>
              Réduire
              <ChevronUp className="size-3.5" data-icon="inline-end" />
            </>
          ) : (
            <>
              Examiner les sources
              <ChevronDown className="size-3.5" data-icon="inline-end" />
            </>
          )}
        </Button>
      </div>

      {!expanded ? (
        <div className="divide-y divide-border/60">
          {preview.map((candidate) => {
            const { decided: decision, pendingExtraction } = getCandidateDisplayState(candidate)
            return (
              <button
                key={candidate.candidate_id}
                type="button"
                className="flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/25"
                onClick={() => setExpanded(true)}
              >
                <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg border border-border bg-background text-brand">
                  <BookOpenText className="size-3.5" aria-hidden="true" />
                </span>

                <span className="min-w-0 flex-1">
                  <span className="line-clamp-1 block text-xs font-semibold text-foreground">
                    {candidate.title || "Source scientifique"}
                  </span>
                  <span className="mt-0.5 line-clamp-1 block text-[11px] text-muted-foreground">
                    {[
                      candidate.year,
                      candidate.source_providers?.join(" · "),
                      candidate.relevance_role === "direct_evidence"
                        ? "Preuve directe"
                        : candidate.relevance_role === "connected_evidence"
                          ? "Source connexe"
                          : "",
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </span>

                {decision === "accepted" ? (
                  <Badge className={pendingExtraction
                    ? "shrink-0 border-amber-200 bg-amber-50 text-[10px] text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200"
                    : "shrink-0 bg-success text-[10px] text-success-foreground"}>
                    {pendingExtraction ? "Gardé · à compléter" : "Gardé · validé"}
                  </Badge>
                ) : decision === "rejected" ? (
                  <Badge
                    variant="secondary"
                    className="shrink-0 text-[9px]"
                  >
                    Écartée
                  </Badge>
                ) : null}
              </button>
            )
          })}

          {candidates.length > preview.length && (
            <button
              type="button"
              className="flex w-full items-center justify-center gap-1.5 px-4 py-2.5 text-xs font-medium text-brand transition-colors hover:bg-brand/[0.035]"
              onClick={() => setExpanded(true)}
            >
              Voir les {candidates.length} sources
              <ChevronDown className="size-3.5" aria-hidden="true" />
            </button>
          )}
        </div>
      ) : (
        <div className="space-y-3 bg-muted/[0.08] p-3 sm:p-4">
          {candidates.map((candidate) => (
            <CandidateCard
              key={candidate.candidate_id}
              candidate={candidate}
              busy={busyCandidateId === candidate.candidate_id}
              onDecision={onDecision}
              onUploadPdf={onUploadPdf}
            />
          ))}

          <div className="flex justify-center pt-1">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="rounded-xl text-xs text-muted-foreground"
              onClick={() => setExpanded(false)}
            >
              Réduire la recherche
              <ChevronUp className="size-3.5" data-icon="inline-end" />
            </Button>
          </div>
        </div>
      )}
    </section>
  )
}

function CorpusPanel({
  open,
  onOpenChange,
  articles,
  loading,
  error,
  onRefresh,
  busyArticleId,
  preparing,
  onRemove,
  onSearch,
  onUpload,
  onUploadMissing,
  onConsult,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  articles: ArticleRead[]
  loading: boolean
  error: string | null
  onRefresh: () => void
  busyArticleId: number | null
  preparing: boolean
  onRemove: (article: ArticleRead) => void
  onSearch: (query: string) => void
  onUpload: (file: File) => void
  onUploadMissing: (article: ArticleRead, file: File) => void
  onConsult: (article: ArticleRead) => void
}) {
  const [query, setQuery] = useState("")
  const uploadInputRef = useRef<HTMLInputElement | null>(null)

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-[96vw] max-w-none flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
        <SheetHeader className="border-b px-5 py-4 pr-14 text-left sm:px-6">
          <SheetTitle className="flex flex-wrap items-center gap-2">
            <Library className="size-5 text-brand" />
            Corpus de rédaction
            <Badge variant="outline" aria-live="polite">
              {loading ? "Chargement…" : error ? "Indisponible" : `${articles.length} article(s)`}
            </Badge>
          </SheetTitle>
          <SheetDescription>
            Chaque article présent ici est sélectionné pour la rédaction.
            L’ajout prépare le texte intégral puis construit automatiquement son
            Article Card.
          </SheetDescription>
        </SheetHeader>

        <div className="border-b bg-muted/40 px-6 py-4">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Ajouter une nouvelle source
          </p>
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="flex min-w-0 flex-1 gap-2">
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Titre, DOI, URL ou besoin scientifique…"
                onKeyDown={(event) => {
                  if (event.key === "Enter" && query.trim()) {
                    event.preventDefault()
                    onSearch(query.trim())
                    setQuery("")
                  }
                }}
              />
              <Button
                disabled={!query.trim() || preparing}
                onClick={() => {
                  onSearch(query.trim())
                  setQuery("")
                }}
              >
                <Search className="mr-2 size-4" />
                Rechercher
              </Button>
            </div>
            <input
              ref={uploadInputRef}
              type="file"
              accept="application/pdf,.pdf"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0]
                event.target.value = ""
                if (file) onUpload(file)
              }}
            />
            <Button
              type="button"
              variant="outline"
              disabled={preparing}
              onClick={() => uploadInputRef.current?.click()}
            >
              {preparing ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : (
                <UploadCloud className="mr-2 size-4" />
              )}
              Importer un PDF
            </Button>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            Les candidats apparaîtront dans le chat. Cliquez ensuite sur
            « Garder et extraire » pour lancer les phases 1 et 2. Seuls les
            articles réellement extraits entrent dans le corpus. Un PDF importé
            depuis votre PC est directement extrait et transformé en Article Card.
          </p>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4" aria-busy={loading}>
          {loading ? (
            <p role="status" className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
              Chargement des articles gardés…
            </p>
          ) : error ? (
            <div role="alert" className="space-y-3 rounded-xl border border-destructive/30 p-4 text-sm">
              <p className="text-destructive">{error}</p>
              <Button variant="outline" onClick={onRefresh}>Réessayer le chargement</Button>
            </div>
          ) : articles.length === 0 ? (
            <div className="rounded-2xl border border-dashed p-10 text-center">
              <Library className="mx-auto size-7 text-muted-foreground" />
              <p className="mt-3 font-medium">Le corpus est vide</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Recherchez puis validez une première source scientifique.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {articles.map((article, index) => {
                const missingPdf = Boolean(
                  article.manual_upload_required ||
                    (!article.fulltext_ready &&
                      [
                        "ACCESS_UNAVAILABLE",
                        "BROWSER_DOWNLOAD_REQUIRED",
                        "ABSTRACT_READY",
                        "METADATA_ONLY",
                        "EXTRACTION_FAILED",
                        "NOT_CHECKED",
                      ].includes(String(article.evidence_status || "").toUpperCase())),
                )
                return (
                <div
                  key={article.id}
                  className="flex items-start gap-3 rounded-xl border border-border p-3"
                >
                  <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-brand/8 text-xs font-semibold text-brand">
                    {index + 1}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold leading-5 text-foreground">
                      {article.title}
                    </p>
                    <div className="mt-1 flex flex-wrap gap-1.5 text-xs text-muted-foreground">
                      {article.year && <span>{article.year}</span>}
                      {article.tag_article && (
                        <Badge variant="outline" className="h-5 text-[10px]">
                          {article.tag_article}
                        </Badge>
                      )}
                      {article.doi && <span className="truncate">DOI {article.doi}</span>}
                    </div>
                    {missingPdf && (
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <Badge className="border border-amber-200 bg-amber-50 text-amber-800 hover:bg-amber-50">
                          PDF de l’article non récupéré
                        </Badge>
                        <label
                          className={`inline-flex h-8 cursor-pointer items-center rounded-lg border border-slate-200 bg-white px-2.5 text-xs font-medium text-slate-700 hover:bg-slate-50 ${
                            preparing || busyArticleId === article.id
                              ? "pointer-events-none opacity-50"
                              : ""
                          }`}
                        >
                          {busyArticleId === article.id ? (
                            <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                          ) : (
                            <UploadCloud className="mr-1.5 size-3.5" />
                          )}
                          Importer le PDF manuellement
                          <input
                            type="file"
                            accept="application/pdf,.pdf"
                            className="hidden"
                            onChange={(event) => {
                              const file = event.target.files?.[0]
                              event.target.value = ""
                              if (file) onUploadMissing(article, file)
                            }}
                          />
                        </label>
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {(article.url ||
                      article.source_json?.uploaded_pdf_available) && (
                      <button
                        type="button"
                        aria-label={`Consulter ${article.title}`}
                        className="inline-flex size-9 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-900"
                        onClick={() => onConsult(article)}
                      >
                        <ExternalLink className="size-4" />
                      </button>
                    )}
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                      disabled={busyArticleId === article.id || preparing}
                      onClick={() => onRemove(article)}
                      aria-label={`Retirer ${article.title} du corpus`}
                    >
                      {busyArticleId === article.id ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <Trash2 className="size-4" />
                      )}
                    </Button>
                  </div>
                </div>
              )})}
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}

export function EnnoScholarPlanChat({
  projectId,
  projectLabel = "Projet actif",
  onCorpusChanged,
  onGenerate,
  onRefreshDraft,
  generating = false,
  generationError = null,
  immersive = false,
  projectOptions = [],
  onProjectChange,
  onBackToArticles,
}: Props) {
  const [sessions, setSessions] = useState<GuidedResearchSession[]>([])
  const [sessionId, setSessionId] = useState("")
  const [messages, setMessages] = useState<GuidedResearchConversationTurn[]>([])
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const [initializing, setInitializing] = useState(true)
  const [decidingId, setDecidingId] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [artifactOpen, setArtifactOpen] = useState(false)
  const [corpusOpen, setCorpusOpen] = useState(false)
  const [conversationsOpen, setConversationsOpen] = useState(false)
  const [preparingCorpus, setPreparingCorpus] = useState(false)
  const [busyArticleId, setBusyArticleId] = useState<number | null>(null)
  const [conversationArticles, setConversationArticles] = useState<ArticleRead[]>([])
  const [corpusLoading, setCorpusLoading] = useState(false)
  const [corpusError, setCorpusError] = useState<string | null>(null)
  const corpusRequestRef = useRef(0)
  const [deletingSessionId, setDeletingSessionId] = useState("")
  const [operatingMode, setOperatingMode] = useState("")
  const [sessionDraftMarkdown, setSessionDraftMarkdown] = useState("")
  const [exportingDocx, setExportingDocx] = useState(false)
  const previousHasDraft = useRef(false)
  const messagesViewportRef = useRef<HTMLDivElement | null>(null)

  const chatOnly = operatingMode === "standalone_chat"
  // L'artefact affiché appartient strictement à la conversation ouverte.
  // Le dernier document global du projet ne doit jamais apparaître comme le
  // brouillon d'une nouvelle conversation.
  const effectiveDraftMarkdown = sessionDraftMarkdown
  const hasDraft = Boolean(effectiveDraftMarkdown.trim())
  const canSend = Boolean(sessionId && input.trim() && !loading && !generating)
  const wordCount = useMemo(
    () => (effectiveDraftMarkdown.match(/\b[\p{L}\p{N}'’-]+\b/gu) || []).length,
    [effectiveDraftMarkdown],
  )

  async function downloadDraftDocx() {
    if (!effectiveDraftMarkdown.trim() || exportingDocx) return
    setExportingDocx(true)
    setError(null)
    setNotice(null)
    try {
      const title = `État de l’art — ${projectLabel}`
      const blob = await exportStateOfArtDocx(
        projectId,
        effectiveDraftMarkdown,
        title,
      )
      const objectUrl = URL.createObjectURL(blob)
      const safeProject = projectLabel
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .replace(/[^A-Za-z0-9_-]+/g, "_")
        .replace(/^_+|_+$/g, "")
        .slice(0, 80) || "projet"
      const anchor = document.createElement("a")
      anchor.href = objectUrl
      anchor.download = `etat_de_l_art_${safeProject}.docx`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 30_000)
      setNotice("Document Word téléchargé avec les figures de l’aperçu.")
    } catch (err: any) {
      setError(err?.message || "Impossible de générer le document Word.")
    } finally {
      setExportingDocx(false)
    }
  }

  useEffect(() => {
    if (
      hasDraft &&
      !previousHasDraft.current &&
      window.matchMedia("(min-width: 1536px)").matches
    ) {
      setArtifactOpen(true)
    }
    if (!hasDraft) setArtifactOpen(false)
    previousHasDraft.current = hasDraft
  }, [hasDraft])

  useEffect(() => {
    const viewport = messagesViewportRef.current
    if (viewport) viewport.scrollTop = viewport.scrollHeight
  }, [messages, loading, generating])

  async function refreshSessions() {
    const result = await listGuidedResearchSessions(projectId, "ennoscholar")
    const rows = (Array.isArray(result?.sessions) ? result.sessions : []).filter(
      (session) =>
        Number(session.project_id) === Number(projectId) &&
        String(session.entry_module || "").trim().toLowerCase() === "ennoscholar",
    )
    setSessions(rows)
    return rows
  }

  async function refreshConversationCorpus(targetSessionId = sessionId) {
    const requestId = ++corpusRequestRef.current
    if (!targetSessionId) {
      setConversationArticles([])
      setCorpusLoading(false)
      return []
    }
    setCorpusLoading(true)
    setCorpusError(null)
    try {
      const result = await getGuidedResearchCorpus(projectId, targetSessionId)
      if (result?.ok === false || !Array.isArray(result?.articles)) {
        throw new Error("La réponse du corpus est indisponible. Réessayez le chargement.")
      }
      const rows = result.articles
      if (requestId === corpusRequestRef.current) setConversationArticles(rows)
      return rows
    } catch (err: any) {
      if (requestId === corpusRequestRef.current) {
        setCorpusError(err?.message || "Impossible de charger les articles gardés.")
      }
      throw err
    } finally {
      if (requestId === corpusRequestRef.current) setCorpusLoading(false)
    }
  }

  function candidatesFromSession(current: any): ResearchCandidate[] {
    const currentBatch = current?.artifacts?.research_plan?.candidates
    const selected = current?.artifacts?.selected_sources

    if (Array.isArray(currentBatch)) {
      const selectedById = new Map(
        (Array.isArray(selected) ? selected : [])
          .filter((row: ResearchCandidate) => row?.candidate_id)
          .map((row: ResearchCandidate) => [row.candidate_id, row]),
      )

      // Le plan conserve l'ordre exact C1, C2, ... de la recherche courante.
      // Les sources sélectionnées portent les décisions consultant à jour.
      return currentBatch.map((row: ResearchCandidate) => ({
        ...row,
        ...(selectedById.get(row.candidate_id) || {}),
      }))
    }

    return Array.isArray(selected)
      ? selected.filter((row: ResearchCandidate) => row.current_research_batch)
      : []
  }

  async function openSession(id: string) {
    setInitializing(true)
    setError(null)
    try {
      const current = await getGuidedResearchSession(projectId, id)
      setSessionId(id)
      const legacyCandidates = candidatesFromSession(current)
      setMessages(
        hydrateResearchAttachments(
          current?.session?.messages || [],
          current,
          legacyCandidates,
        ),
      )
      setOperatingMode(String(current?.session?.context?.operating_mode || ""))
      setSessionDraftMarkdown(String(current?.artifacts?.draft?.markdown || ""))
      await refreshConversationCorpus(id).catch(() => undefined)
      localStorage.setItem(storageKey(projectId), id)
    } catch (err: any) {
      setError(err?.message || "Impossible d’ouvrir cette conversation.")
      throw err
    } finally {
      setInitializing(false)
    }
  }

  async function createConversation() {
    setInitializing(true)
    setError(null)
    setNotice(null)
    try {
      const created = await createGuidedResearchSession(projectId)
      const id = String(created?.session?.session_id || "")
      if (!id) throw new Error("La conversation n’a pas été créée.")
      if (
        Number(created?.session?.project_id) !== Number(projectId) ||
        String(created?.session?.entry_module || "").trim().toLowerCase() !==
          "ennoscholar"
      ) {
        throw new Error(
          "La conversation créée n’est pas rattachée à l’espace EnnoScholar du projet actif.",
        )
      }
      setSessionId(id)
      setMessages(created?.session?.messages || [])
      setOperatingMode(String(created?.session?.context?.operating_mode || ""))
      setSessionDraftMarkdown("")
      setConversationArticles([])
      localStorage.setItem(storageKey(projectId), id)
      // A new document is empty; the diagnostic-backed project corpus is not.
      // The endpoint preserves the private scope of standalone conversations.
      await refreshConversationCorpus(id).catch(() => undefined)
      await refreshSessions()
    } catch (err: any) {
      setError(err?.message || "Impossible de créer la conversation.")
    } finally {
      setInitializing(false)
    }
  }

  async function removeConversation(targetSession: GuidedResearchSession) {
    const confirmed = window.confirm(
      `Supprimer définitivement la conversation « ${sessionLabel(targetSession)} » ?`,
    )
    if (!confirmed) return

    const deletingActive = targetSession.session_id === sessionId
    setDeletingSessionId(targetSession.session_id)
    setError(null)
    setNotice(null)
    try {
      await deleteGuidedResearchSession(projectId, targetSession.session_id)
      const remaining = await refreshSessions()
      if (deletingActive) {
        localStorage.removeItem(storageKey(projectId))
        if (remaining[0]?.session_id) {
          await openSession(remaining[0].session_id)
        } else {
          await createConversation()
        }
      }
      setNotice("Conversation supprimée.")
    } catch (err: any) {
      setError(err?.message || "Impossible de supprimer cette conversation.")
    } finally {
      setDeletingSessionId("")
    }
  }

  async function initialize() {
    setInitializing(true)
    setError(null)
    try {
      const rows = await refreshSessions()
      const stored =
        typeof window !== "undefined"
          ? localStorage.getItem(storageKey(projectId))
          : null
      const preferred =
        rows.find((row) => row.session_id === stored)?.session_id ||
        rows[0]?.session_id
      if (preferred) {
        await openSession(preferred)
      } else {
        await createConversation()
      }
    } catch (err: any) {
      setError(err?.message || "Impossible d’ouvrir les conversations.")
      setInitializing(false)
    }
  }

  useEffect(() => {
    corpusRequestRef.current += 1
    setSessionId("")
    setMessages([])
    setConversationArticles([])
    setCorpusError(null)
    void initialize()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  useEffect(() => {
    if (!corpusOpen || !sessionId || initializing) return
    void refreshConversationCorpus().catch(() => undefined)
    // Refresh decisions made in the Articles page when opening the corpus.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [corpusOpen, sessionId, projectId, initializing])

  async function submitMessageText(text: string) {
    const clean = text.trim()
    if (!sessionId || !clean || loading || generating) return
    setLoading(true)
    setError(null)
    setNotice(null)
    setMessages((current) => [
      ...current,
      {
        message_id: `local-${Date.now()}`,
        role: "consultant",
        content: clean,
        created_at: new Date().toISOString(),
      },
    ])
    try {
      const result = await sendGuidedResearchMessage(projectId, sessionId, clean)
      const response = result?.response
      setMessages((current) => [
        ...current,
        {
          message_id: `assistant-${Date.now()}`,
          role: "assistant",
          content: String(
            response?.assistant_message || "Demande prise en compte.",
          ),
          created_at: new Date().toISOString(),
          metadata: response?.metadata || {},
        },
      ])
      if (response?.metadata?.operating_mode) {
        setOperatingMode(String(response.metadata.operating_mode))
      }
      if (response?.metadata?.standalone_draft_markdown) {
        setSessionDraftMarkdown(
          String(response.metadata.standalone_draft_markdown),
        )
      }
      if (response?.metadata?.trigger_state_of_art_generation === true) {
        const generationResult = await onGenerate(sessionId)
        if (generationResult?.ok === false) {
          setMessages((current) => [
            ...current,
            {
              message_id: `assistant-generation-${Date.now()}`,
              role: "assistant",
              content: String(
                generationResult?.assistant_message ||
                  "La rédaction n'est pas encore publiée. Votre corpus, votre plan et vos choix sont conservés ; vous pouvez poursuivre dans ce chat.",
              ),
              created_at: new Date().toISOString(),
              metadata: {
                generation_status: generationResult?.status || "pending",
                previous_draft_preserved: Boolean(
                  generationResult?.previous_draft_preserved,
                ),
              },
            },
          ])
        } else {
          // En mode autonome, le panneau doit rester isolé par conversation :
          // `effectiveDraftMarkdown` ne reprend donc pas le dernier document
          // global du projet. Après le pipeline commun, synchroniser
          // explicitement le document publié avec la conversation qui vient de
          // le demander. Sans cela, Phase 5 termine correctement côté backend
          // mais l'artefact reste visuellement fermé jusqu'à un rechargement de
          // session.
          let publishedMarkdown = String(
            generationResult?.markdown ||
              generationResult?.state_of_art_view?.markdown ||
              generationResult?.report?.markdown ||
              "",
          )

          if (!publishedMarkdown.trim()) {
            const refreshedSession = await getGuidedResearchSession(
              projectId,
              sessionId,
            )
            publishedMarkdown = String(
              refreshedSession?.artifacts?.draft?.markdown || "",
            )
          }
          if (publishedMarkdown.trim()) {
            setSessionDraftMarkdown(publishedMarkdown)
            setArtifactOpen(true)
            setNotice(
              "La nouvelle rédaction est terminée et affichée dans l’artefact État de l’art.",
            )
          }
        }
      }
      await refreshSessions()
      await refreshConversationCorpus().catch(() => undefined)
    } catch (err: any) {
      setError(err?.message || "Impossible d’envoyer le message.")
    } finally {
      setLoading(false)
    }
  }

  async function submitMessage() {
    const text = input.trim()
    if (!text || !canSend) return
    setInput("")
    await submitMessageText(text)
  }

  async function decide(
    candidate: ResearchCandidate,
    decision: "accepted" | "rejected",
  ) {
    setDecidingId(candidate.candidate_id)
    setError(null)
    setNotice(null)
    if (decision === "accepted") setPreparingCorpus(true)
    try {
      const result = await decideGuidedResearchSources(
        projectId,
        sessionId,
        [candidate.candidate_id],
        decision,
      )
      setMessages((current) =>
        updateCandidateInMessages(
          current,
          candidate.candidate_id,
          (row) => ({ ...row, consultant_decision: decision }),
        ),
      )
      const response = result?.response
      if (response?.assistant_message) {
        setMessages((current) => [
          ...current,
          {
            message_id: `assistant-source-${Date.now()}`,
            role: "assistant",
            content: String(response.assistant_message),
            created_at: new Date().toISOString(),
            metadata: response?.metadata || {},
          },
        ])
      }
      if (decision === "accepted") {
        const refreshedSession = await getGuidedResearchSession(
          projectId,
          sessionId,
        )
        setMessages((current) =>
          hydrateResearchAttachments(current, refreshedSession, candidatesFromSession(refreshedSession)),
        )
        setNotice(
          "Source acceptée : elle est ajoutée au corpus uniquement si son texte a été extrait.",
        )
        await onCorpusChanged?.()
        await refreshConversationCorpus()
      }
      await refreshSessions()
    } catch (err: any) {
      setError(err?.message || "Impossible d’enregistrer cette décision.")
    } finally {
      setDecidingId("")
      setPreparingCorpus(false)
    }
  }

  async function removeArticle(article: ArticleRead) {
    const confirmed = window.confirm(
      `Retirer « ${article.title} » du corpus de rédaction ?`,
    )
    if (!confirmed) return
    setBusyArticleId(article.id)
    setPreparingCorpus(true)
    setError(null)
      setNotice(null)
    try {
      await removeGuidedResearchCorpusArticle(
        projectId,
        sessionId,
        article.id,
      )
      await refreshConversationCorpus()
      await onCorpusChanged?.()
      setNotice("Article retiré du corpus et artefacts de sélection actualisés.")
    } catch (err: any) {
      setError(err?.message || "Impossible de retirer cet article.")
    } finally {
      setBusyArticleId(null)
      setPreparingCorpus(false)
    }
  }

  async function searchNewArticle(query: string) {
    if (!query.trim()) return
    setCorpusOpen(false)
    await submitMessageText(
      `Recherche une nouvelle source scientifique pour compléter le corpus sur : ${query.trim()}. ` +
        "Présente les candidats pertinents sans les sélectionner automatiquement.",
    )
  }

  async function uploadArticleFromComputer(file: File) {
    setPreparingCorpus(true)
    setError(null)
    setNotice(null)
    try {
      const result = await uploadNewScholarSource(projectId, file, sessionId)
      await refreshConversationCorpus()
      await onCorpusChanged?.()
      const cardsCount = Number(result?.phase_2?.cards_count || 0)
      setNotice(
        `« ${result?.article?.title || file.name} » a été importé, extrait et ajouté au corpus. ` +
          `${cardsCount} Article Card(s) sont maintenant prêtes.`,
      )
    } catch (err: any) {
      setError(err?.message || "Impossible d’importer et préparer ce PDF.")
    } finally {
      setPreparingCorpus(false)
    }
  }


  async function uploadCandidatePdf(candidate: ResearchCandidate, file: File) {
    const articleId = Number(candidate.fulltext_preparation?.article_id || 0)
    if (!articleId) {
      setError("Cette source n'a pas encore d'article associé pour recevoir le PDF.")
      return
    }
    setDecidingId(candidate.candidate_id)
    setPreparingCorpus(true)
    setError(null)
    setNotice(null)
    try {
      const result = await uploadAndExtractArticlePdf(
        projectId,
        articleId,
        file,
        candidate.url || null,
        sessionId,
      )
      const refreshedSession = await getGuidedResearchSession(projectId, sessionId)
      setMessages((current) =>
        hydrateResearchAttachments(current, refreshedSession, candidatesFromSession(refreshedSession)),
      )
      await refreshConversationCorpus()
      await onCorpusChanged?.()
      const cardsCount = Number(result?.phase_2?.cards_count || 0)
      setNotice(
        `Le PDF de « ${candidate.title || file.name} » a été extrait et l'article est maintenant dans le corpus. ` +
          `${cardsCount} Article Card(s) sont prêtes.`,
      )
    } catch (err: any) {
      setError(err?.message || "Impossible d'importer et d'extraire ce PDF.")
    } finally {
      setDecidingId("")
      setPreparingCorpus(false)
    }
  }


  async function uploadMissingArticlePdf(article: ArticleRead, file: File) {
    setBusyArticleId(article.id)
    setPreparingCorpus(true)
    setError(null)
    setNotice(null)
    try {
      const result = await uploadAndExtractArticlePdf(
        projectId,
        article.id,
        file,
        article.url,
        sessionId,
      )
      await refreshConversationCorpus()
      await onCorpusChanged?.()
      const cardsCount = Number(result?.phase_2?.cards_count || 0)
      setNotice(
        `Le PDF de « ${article.title} » a été extrait. ` +
          `${cardsCount} Article Card(s) du corpus de cette conversation sont prêtes.`,
      )
    } catch (err: any) {
      setError(
        err?.message ||
          "Impossible d’importer ce PDF pour l’article sélectionné.",
      )
    } finally {
      setBusyArticleId(null)
      setPreparingCorpus(false)
    }
  }

  async function consultArticle(article: ArticleRead) {
    if (article.url) {
      window.open(article.url, "_blank", "noopener,noreferrer")
      return
    }
    if (!article.source_json?.uploaded_pdf_available) return

    const viewer = window.open("about:blank", "_blank")
    try {
      const blob = await getUploadedScholarArticlePdf(projectId, article.id)
      const objectUrl = URL.createObjectURL(blob)
      if (viewer) {
        viewer.location.href = objectUrl
      } else {
        window.open(objectUrl, "_blank", "noopener,noreferrer")
      }
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 300_000)
    } catch (err: any) {
      viewer?.close()
      setError(err?.message || "Impossible d’ouvrir le PDF importé.")
    }
  }

  return (
    <>
      <div
        className={
          immersive
            ? "relative flex h-full min-h-0 overflow-hidden bg-background"
            : "relative flex h-[calc(100dvh-205px)] min-h-[520px] max-h-[900px] overflow-hidden rounded-xl border border-border bg-background shadow-xs"
        }
      >
        <main className="flex min-w-0 flex-1 flex-col bg-card/75">
          <header className="flex min-h-16 shrink-0 flex-wrap items-center justify-between gap-2 border-b border-border bg-card/95 px-3 py-2 backdrop-blur-sm sm:px-5">
            <div className="flex min-w-0 items-center gap-3">
              <Button
                variant="outline"
                size="sm"
                className="min-h-10 shrink-0"
                onClick={() => setConversationsOpen(true)}
                aria-label={`Ouvrir les conversations, ${sessions.length} au total`}
              >
                <MessageSquareText className="size-4" aria-hidden="true" />
                <span className="hidden sm:inline">Conversations</span>
                <Badge variant="outline" className="ml-1">{sessions.length}</Badge>
              </Button>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-foreground">
                  {sessionLabel(
                    sessions.find((session) => session.session_id === sessionId) ||
                      ({
                        session_id: sessionId,
                        project_id: projectId,
                        entry_module: "ennoscholar",
                        state: "",
                        ready_to_write: false,
                        messages: [],
                      } as GuidedResearchSession),
                  )}
                </p>
                <p className="hidden text-xs text-muted-foreground sm:block">
                  Conversation scientifique · mémoire du projet active
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              {onBackToArticles && !chatOnly && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="hidden rounded-xl text-muted-foreground sm:inline-flex"
                  onClick={onBackToArticles}
                >
                  <ChevronLeft className="mr-1 size-4" />
                  Articles
                </Button>
              )}
              {projectOptions.length > 1 && onProjectChange && (
                <select
                  value={projectId}
                  onChange={(event) =>
                    void onProjectChange(Number(event.target.value))
                  }
                  className="hidden h-9 max-w-56 rounded-lg border border-border bg-card px-3 text-xs text-foreground xl:block"
                  aria-label="Changer de projet"
                >
                  {projectOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))}
                </select>
              )}
              <button
                type="button"
                onClick={() => setCorpusOpen(true)}
                className="flex min-h-10 items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 text-sm font-medium text-foreground shadow-xs transition-colors hover:border-brand/25 hover:bg-brand/5 focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/25"
              >
                <Library className="size-4 text-brand" aria-hidden="true" />
                <span className="hidden md:inline">Corpus</span>
                <Badge variant="outline" aria-live="polite">
                  {initializing || corpusLoading ? "…" : corpusError ? "!" : conversationArticles.length}
                </Badge>
                {preparingCorpus && <Loader2 className="size-3.5 animate-spin" />}
              </button>
              <Button
                variant="outline"
                size="sm"
                className="min-h-10"
                onClick={() => hasDraft && setArtifactOpen((current) => !current)}
                disabled={!hasDraft}
                title={hasDraft ? "Afficher l’état de l’art" : "Aucun état de l’art rédigé"}
              >
                {artifactOpen ? (
                  <PanelRightClose className="size-4" />
                ) : (
                  <PanelRightOpen className="size-4" />
                )}
                <span className="hidden lg:inline">Aperçu</span>
              </Button>
            </div>
          </header>

          <div
            ref={messagesViewportRef}
            className="min-h-0 flex-1 overflow-y-auto"
          >
            <div className="mx-auto w-full max-w-5xl space-y-7 px-4 py-7 sm:px-6 lg:px-8">
              {messages.length === 0 && !initializing ? (
                <div className="flex min-h-[360px] flex-col items-center justify-center text-center">
                  <div className="rounded-xl border border-brand/15 bg-brand/8 p-3 text-brand">
                    <MessageSquareText className="size-6" />
                  </div>
                  <h2 className="mt-4 text-xl font-semibold tracking-tight text-foreground">
                    Comment voulez-vous construire l’état de l’art ?
                  </h2>
                  <p className="mt-2 max-w-xl text-sm leading-6 text-muted-foreground">
                    Parlez naturellement du plan, des articles à rechercher, des
                    verrous ou du niveau d’argumentation. La rédaction reste
                    reste disponible comme livrable dans le panneau latéral.
                  </p>
                  <div className="mt-5 flex flex-wrap justify-center gap-2">
                    {[
                      "Donne-moi le plan actuel",
                      "Vérifie la couverture des deux verrous",
                      "Recherche une preuve directe manquante",
                    ].map((suggestion) => (
                      <button
                        key={suggestion}
                        type="button"
                        onClick={() => setInput(suggestion)}
                        className="rounded-lg border border-border bg-card px-3 py-2 text-xs text-muted-foreground hover:border-brand/25 hover:bg-brand/5 hover:text-foreground"
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                messages.map((message, index) => {
                  const consultant = message.role === "consultant"
                  const messageCandidates = consultant
                    ? []
                    : getMessageCandidates(message)

                  return (
                    <div
                      key={message.message_id || `${message.role}-${index}`}
                      className={`flex gap-3 ${
                        consultant ? "justify-end" : "justify-start"
                      }`}
                    >
                      {!consultant && (
                        <div className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-full border border-brand/10 bg-brand/[0.055] text-brand">
                          <Bot className="size-3.5" />
                        </div>
                      )}

                      <div
                        className={
                          consultant
                            ? "max-w-[82%]"
                            : "min-w-0 w-full max-w-[900px]"
                        }
                      >
                        <div
                          className={`whitespace-pre-wrap text-sm leading-7 ${
                            consultant
                              ? "rounded-2xl rounded-br-md border border-brand/10 bg-brand/8 px-4 py-2.5 text-foreground"
                              : "py-1 text-foreground"
                          }`}
                        >
                          {citationText(message.content)}
                        </div>

                        {!consultant && messageCandidates.length > 0 && (
                          <ResearchAttachment
                            candidates={messageCandidates}
                            busyCandidateId={decidingId}
                            onDecision={(row, value) => void decide(row, value)}
                            onUploadPdf={(row, file) =>
                              void uploadCandidatePdf(row, file)
                            }
                          />
                        )}
                      </div>
                    </div>
                  )
                })
              )}

              {(initializing || loading || generating) && (
                <div className="flex items-center gap-2 py-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  {generating
                    ? "Rédaction argumentée en cours…"
                    : "Analyse de votre demande…"}
                </div>
              )}

              {notice && (
                <div
                  className="rounded-xl border border-success/25 bg-success/8 px-4 py-3 text-sm text-success"
                  role="status"
                  aria-live="polite"
                >
                  {notice}
                </div>
              )}
              {error && (
                <div
                  className="rounded-xl border border-destructive/25 bg-destructive/8 px-4 py-3 text-sm text-destructive"
                  role="alert"
                >
                  {error}
                </div>
              )}
              {generationError && (
                <div className="rounded-xl border border-warning/25 bg-warning/8 px-4 py-3 text-sm text-warning-foreground">
                  {generationError}
                </div>
              )}
            </div>
          </div>

          <div className="shrink-0 border-t border-border bg-card/95 px-4 pb-4 pt-3 backdrop-blur-sm sm:px-6">
            <div className="mx-auto max-w-3xl">
              <div className="rounded-xl border border-input bg-card p-2 shadow-sm focus-within:border-brand focus-within:ring-3 focus-within:ring-brand/10">
                <Textarea
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  disabled={initializing || loading || generating}
                  placeholder="Écrivez votre demande sur le plan, la recherche ou la rédaction…"
                  rows={3}
                  className="min-h-[70px] resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault()
                      void submitMessage()
                    }
                  }}
                />
                <div className="flex items-center justify-between px-1 pb-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="rounded-xl text-xs text-muted-foreground"
                    onClick={() => setCorpusOpen(true)}
                  >
                    <FilePlus2 className="mr-1.5 size-4" />
                    Ajouter une source
                  </Button>
                  <Button
                    type="button"
                    size="icon"
                    className="rounded-full"
                    disabled={!canSend}
                    onClick={() => void submitMessage()}
                    aria-label="Envoyer le message"
                  >
                    {loading ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Send className="size-4" />
                    )}
                  </Button>
                </div>
              </div>
              <p className="mt-2 text-center text-[11px] text-muted-foreground">
                Entrée pour envoyer · Maj + Entrée pour une nouvelle ligne
              </p>
            </div>
          </div>
        </main>

        {artifactOpen && (
          <aside className="absolute inset-y-0 right-0 z-30 flex w-[min(94vw,620px)] shrink-0 flex-col border-l border-border bg-muted/30 shadow-2xl lg:w-[min(70%,620px)] 2xl:static 2xl:z-auto 2xl:w-[min(40vw,620px)] 2xl:shadow-none">
            <header className="flex h-16 shrink-0 items-center justify-between border-b border-border bg-card px-4">
              <div>
                <p className="flex items-center gap-2 text-sm font-semibold text-foreground">
                  <FileText className="size-4 text-brand" />
                  État de l’art
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Livrable rédigé · {wordCount.toLocaleString("fr-FR")} mots
                </p>
              </div>
              <div className="flex items-center gap-1">
                <Button
                  variant="outline"
                  size="sm"
                  className="min-h-10 rounded-lg px-3"
                  disabled={!hasDraft || exportingDocx}
                  onClick={() => void downloadDraftDocx()}
                  aria-label="Télécharger l’état de l’art au format Word"
                  aria-busy={exportingDocx}
                  title="Télécharger en Word (.docx)"
                >
                  {exportingDocx ? (
                    <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Download className="size-4" aria-hidden="true" />
                  )}
                  <span className="hidden sm:inline">
                    {exportingDocx ? "Préparation…" : "Word"}
                  </span>
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => navigator.clipboard?.writeText(effectiveDraftMarkdown)}
                  title="Copier le document"
                >
                  <Copy className="size-4" />
                </Button>
                {onRefreshDraft && (
                  <Button
                    variant="ghost"
                    size="icon"
                    disabled={generating}
                    onClick={() => void onRefreshDraft()}
                    title="Actualiser le document"
                  >
                    <RefreshCw
                      className={`size-4 ${generating ? "animate-spin" : ""}`}
                    />
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setArtifactOpen(false)}
                  title="Fermer le livrable"
                >
                  <PanelRightClose className="size-4" />
                </Button>
              </div>
            </header>
            <div className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto [overflow-wrap:anywhere]">
              <DraftPreview
                markdown={effectiveDraftMarkdown}
                projectId={projectId}
              />
            </div>
          </aside>
        )}

      </div>

      <CorpusPanel
        open={corpusOpen}
        onOpenChange={setCorpusOpen}
        articles={conversationArticles}
        loading={initializing || corpusLoading}
        error={corpusError}
        onRefresh={() => void refreshConversationCorpus().catch(() => undefined)}
        busyArticleId={busyArticleId}
        preparing={preparingCorpus}
        onRemove={(article) => void removeArticle(article)}
        onSearch={(query) => void searchNewArticle(query)}
        onUpload={(file) => void uploadArticleFromComputer(file)}
        onUploadMissing={(article, file) =>
          void uploadMissingArticlePdf(article, file)
        }
        onConsult={(article) => void consultArticle(article)}
      />

      <Sheet open={conversationsOpen} onOpenChange={setConversationsOpen}>
        <SheetContent side="left" className="flex w-[92vw] max-w-sm flex-col gap-0 p-0">
          <SheetHeader className="border-b px-4 py-4 pr-14 text-left">
            <SheetTitle>Conversations du projet</SheetTitle>
            <SheetDescription>
              Chaque conversation conserve son propre historique scientifique.
            </SheetDescription>
          </SheetHeader>
          <div className="border-b p-3">
            <Button
              className="w-full justify-start"
              onClick={() => {
                setConversationsOpen(false)
                void createConversation()
              }}
              disabled={initializing}
            >
              <MessageSquarePlus className="mr-2 size-4" />
              Nouvelle conversation
            </Button>
            <Button
              variant="outline"
              className="mt-2 w-full justify-between rounded-xl"
              onClick={() => {
                setConversationsOpen(false)
                setCorpusOpen(true)
              }}
            >
              <span className="flex items-center gap-2">
                <Library className="size-4 text-brand" />
                Corpus de rédaction
              </span>
              <Badge variant="outline">
                {initializing || corpusLoading ? "…" : corpusError ? "!" : conversationArticles.length}
              </Badge>
            </Button>
            {onBackToArticles && !chatOnly && (
              <Button
                variant="ghost"
                className="mt-2 w-full justify-start rounded-xl"
                onClick={() => {
                  setConversationsOpen(false)
                  onBackToArticles()
                }}
              >
                <ChevronLeft className="mr-2 size-4" />
                Revenir aux articles
              </Button>
            )}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {sessions.length === 0 && !initializing && (
              <div className="m-2 rounded-xl border border-dashed border-border p-5 text-center">
                <MessageSquareText className="mx-auto size-5 text-muted-foreground" aria-hidden="true" />
                <p className="mt-2 text-sm font-medium text-foreground">Aucune conversation</p>
                <p className="mt-1 text-xs text-muted-foreground">Créez une conversation pour commencer la rédaction guidée.</p>
              </div>
            )}
            {sessions.map((session) => (
              <div
                key={session.session_id}
                className={`mb-1 flex items-start rounded-xl ${
                  session.session_id === sessionId
                    ? "bg-brand/8 ring-1 ring-brand/15"
                    : "hover:bg-muted/60"
                }`}
              >
                <button
                  type="button"
                  onClick={() => {
                    setConversationsOpen(false)
                    void openSession(session.session_id)
                  }}
                  className="min-w-0 flex-1 rounded-xl px-3 py-2.5 text-left focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/25"
                >
                  <p className="line-clamp-2 text-sm font-medium text-foreground">
                    {sessionLabel(session)}
                  </p>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {formatSessionDate(session.updated_at)} ·{" "}
                    {session.message_count || 0} message(s)
                  </p>
                </button>
                <button
                  type="button"
                  disabled={deletingSessionId === session.session_id}
                  onClick={() => void removeConversation(session)}
                  className="mr-1 mt-1.5 inline-flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/25"
                  aria-label={`Supprimer la conversation ${sessionLabel(session)}`}
                >
                  {deletingSessionId === session.session_id ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Trash2 className="size-4" />
                  )}
                </button>
              </div>
            ))}
          </div>
          <div className="border-t border-border bg-muted/30 px-4 py-3">
            <p className="truncate text-xs font-medium text-foreground">{projectLabel}</p>
            <p className="mt-0.5 text-[11px] text-muted-foreground">Historique et corpus attachés à ce projet.</p>
          </div>
        </SheetContent>
      </Sheet>
    </>
  )
}
